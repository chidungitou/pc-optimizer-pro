# -*- coding: utf-8 -*-
"""Thu thập & mô tả cấu hình phần cứng (Windows: PowerShell/CIM + psutil). Không phụ thuộc giao diện."""
import os, json, base64, socket, subprocess

NO_WIN = 0x08000000
LEVELS = {"ok": 0, "unknown": 0, "warn": 1, "bad": 2}
LABELS = {"ok": "✔ Tốt", "warn": "⚠ Cần chú ý", "bad": "✖ Nguy hiểm", "unknown": "? Không đọc được"}
RAM_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}

PS_SCRIPT = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
function Fd($x,$f){ if($x){ $x.ToString($f) } else { $null } }
$r=@{}
$r.cpu=@(Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize,SocketDesignation,VirtualizationFirmwareEnabled)
$r.system=@(Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory)
$r.board=@(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product)
$r.bios=@(Get-CimInstance Win32_BIOS | ForEach-Object { [pscustomobject]@{Manufacturer=$_.Manufacturer;Version=$_.SMBIOSBIOSVersion;Date=(Fd $_.ReleaseDate 'yyyy-MM-dd')} })
$r.os=@(Get-CimInstance Win32_OperatingSystem | ForEach-Object { [pscustomobject]@{Caption=$_.Caption;Build=$_.BuildNumber;Arch=$_.OSArchitecture;Install=(Fd $_.InstallDate 'yyyy-MM-dd')} })
$r.gpu=@(Get-CimInstance Win32_VideoController | ForEach-Object { [pscustomobject]@{Name=$_.Name;Maker=$_.AdapterCompatibility;Driver=$_.DriverVersion;DriverDate=(Fd $_.DriverDate 'yyyy-MM-dd');AdapterRAM=$_.AdapterRAM;W=$_.CurrentHorizontalResolution;H=$_.CurrentVerticalResolution;Hz=$_.CurrentRefreshRate} })
$vr=@(); foreach($k in (Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11cd-bfc1-08002be10318}')){ $p=Get-ItemProperty $k.PSPath; if($p.DriverDesc){ $m=$p.'HardwareInformation.qwMemorySize'; if($m -is [byte[]]){ $m=[BitConverter]::ToUInt64($m,0) }; $vr+=[pscustomobject]@{Name=$p.DriverDesc;Mem=$m} } }
$r.vram=$vr
$r.ram=@(Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator,Capacity,Speed,ConfiguredClockSpeed,Manufacturer,PartNumber,SMBIOSMemoryType,FormFactor)
$r.ramarr=@(Get-CimInstance Win32_PhysicalMemoryArray | Select-Object MaxCapacity,MaxCapacityEx,MemoryDevices)
$parts=@(Get-Partition | Where-Object { $_.DriveLetter -match '[A-Za-z]' } | ForEach-Object { [pscustomobject]@{N=[int]$_.DiskNumber;L=[string]$_.DriveLetter} })
$r.vols=@(Get-Volume | Where-Object { $_.DriveLetter -match '[A-Za-z]' } | ForEach-Object { [pscustomobject]@{L=[string]$_.DriveLetter;Label=$_.FileSystemLabel;FS=$_.FileSystem;Size=[int64]$_.Size;Free=[int64]$_.SizeRemaining} })
$disks=@()
foreach($d in (Get-PhysicalDisk)){
  $c=$d | Get-StorageReliabilityCounter
  $n=[int]$d.DeviceId
  $disks+=[pscustomobject]@{Number=$n;Name=$d.FriendlyName;Media=[string]$d.MediaType;Bus=[string]$d.BusType;Size=[int64]$d.Size;Health=[string]$d.HealthStatus;Op=(($d.OperationalStatus | ForEach-Object {[string]$_}) -join ',');Firmware=$d.FirmwareVersion;Temp=$c.Temperature;Wear=$c.Wear;Hours=$c.PowerOnHours;ReadErr=$c.ReadErrorsTotal;WriteErr=$c.WriteErrorsTotal;Letters=@($parts | Where-Object { $_.N -eq $n } | ForEach-Object { $_.L })}
}
$r.disks=$disks
$r.smart=@(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus | Select-Object PredictFailure)
$r.net=@(Get-CimInstance Win32_NetworkAdapter | Where-Object { $_.PhysicalAdapter -and $_.NetEnabled } | Select-Object Name,Speed)
$r.mon=@(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID | ForEach-Object {
  $nm=if($_.UserFriendlyName){ ([Text.Encoding]::ASCII.GetString(@($_.UserFriendlyName | Where-Object { $_ -ne 0 }))).Trim() } else { '' }
  $mk=if($_.ManufacturerName){ ([Text.Encoding]::ASCII.GetString(@($_.ManufacturerName | Where-Object { $_ -ne 0 }))).Trim() } else { '' }
  [pscustomobject]@{Name=$nm;Maker=$mk;Year=$_.YearOfManufacture} })
$fc=(Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1).FullChargedCapacity
$dc=(Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData | Select-Object -First 1).DesignedCapacity
$r.battery=@{Full=$fc;Design=$dc}
$sb=$null; try { $sb=Confirm-SecureBootUEFI } catch {}
$tpm=Get-Tpm
$r.sec=@{SecureBoot=$sb;TpmPresent=$tpm.TpmPresent;TpmReady=$tpm.TpmReady}
$r.temp=@(Get-CimInstance -Namespace root\wmi -ClassName MSAcpi_ThermalZoneTemperature | ForEach-Object { [math]::Round($_.CurrentTemperature/10-273.15,1) })
ConvertTo-Json -InputObject $r -Depth 6 -Compress
"""

# ----------------------------------------------------------------- thu thập
def _run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                           creationflags=NO_WIN if os.name == "nt" else 0)
        return p.returncode, p.stdout
    except Exception as e:
        return 1, str(e).encode("utf-8", "replace")

def ps_json(script, timeout=120):
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    rc, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc], timeout)
    text = out.decode("utf-8-sig", "replace") if isinstance(out, bytes) else str(out)
    i = text.find("{")
    if i < 0:
        return {"error": f"PowerShell không trả dữ liệu (mã {rc}): {text.strip()[:200]}"}
    try:
        return json.loads(text[i:])
    except ValueError as e:
        return {"error": f"Không đọc được JSON: {e}"}

def nvidia():
    rc, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu,driver_version",
                    "--format=csv,noheader,nounits"], 15)
    res = []
    if rc == 0 and isinstance(out, bytes):
        for line in out.decode("utf-8", "replace").splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 6: res.append({"name": p[0], "mem_total": p[1], "mem_used": p[2], "temp": p[3], "util": p[4], "driver": p[5]})
    return res

def extras():
    ex = {}
    try:
        import psutil
        f = psutil.cpu_freq(); ex["cpu_freq"] = f.current if f else None
        vm = psutil.virtual_memory(); ex["ram_total"], ex["ram_used"] = vm.total, vm.total - vm.available
        sw = psutil.swap_memory(); ex["swap_total"], ex["swap_used"] = sw.total, sw.used
        ex["boot"] = psutil.boot_time(); stats = psutil.net_if_stats(); ifs = []
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            ip = next((a.address for a in addrs if a.family == socket.AF_INET), None)
            if st and st.isup and ip and not ip.startswith(("127.", "169.254.")): ifs.append({"name": name, "ip": ip, "speed": st.speed})
        ex["ifaces"] = ifs
        b = psutil.sensors_battery()
        if b: ex["battery"] = {"percent": b.percent, "plugged": b.power_plugged}
    except Exception:
        pass
    ex["nvidia"] = nvidia()
    try:
        import ctypes; ex["admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        ex["admin"] = False
    return ex

def collect():
    raw = ps_json(PS_SCRIPT)
    raw["extra"] = extras()
    return raw

# ----------------------------------------------------------------- tiện ích
def as_list(x):
    if x is None: return []
    return x if isinstance(x, list) else [x]

def first(x):
    l = as_list(x); return l[0] if l and isinstance(l[0], dict) else {}

def num(x):
    try: return float(x) if x not in (None, "") else None
    except (TypeError, ValueError): return None

def fmt_bytes(b):
    b = num(b)
    if b is None: return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or u == "TB":
            return f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}".replace(".0 ", " ")
        b /= 1024

def nominal(size):
    """Dung lượng ghi trên nhãn (hệ thập phân): 931.5 GiB -> '1 TB'."""
    g = (num(size) or 0) / 1e9
    return (f"{g / 1000:.2f}".rstrip("0").rstrip(".") + " TB") if g >= 1000 else f"{g:.0f} GB"

def kb(v):
    v = num(v)
    if not v: return "—"
    return f"{v / 1024:.0f} MB" if v >= 1024 else f"{v:.0f} KB"

# ----------------------------------------------------------------- sức khỏe ổ đĩa
def disk_kind(d):
    media, bus = str(d.get("Media") or "").upper(), str(d.get("Bus") or "")
    if bus.upper() == "NVME": return "SSD NVMe"
    if bus.upper() == "USB": return "Ổ gắn ngoài (USB)"
    if media in ("SSD", "SCM"): return f"SSD {bus}".strip()
    if media == "HDD": return f"HDD {bus}".strip()
    return f"{media.title() or 'Không rõ'} {bus}".strip()

def disk_verdict(d, smart_fail=False):
    """Trả về (level, nhãn, [lý do]). level: ok | warn | bad | unknown."""
    level, reasons = "ok", []
    def bump(l, msg):
        nonlocal level
        if LEVELS[l] > LEVELS[level]: level = l
        reasons.append(msg)
    health, op = str(d.get("Health") or "").lower(), str(d.get("Op") or "").lower()
    temp, wear, hours = num(d.get("Temp")) or None, num(d.get("Wear")), num(d.get("Hours"))
    errs = (num(d.get("ReadErr")) or 0) + (num(d.get("WriteErr")) or 0)
    ssd = disk_kind(d).startswith("SSD")
    if health == "unhealthy": bump("bad", "Windows báo ổ đĩa KHÔNG khỏe (Unhealthy) — nên sao lưu dữ liệu ngay")
    elif health == "warning": bump("warn", "Windows báo cảnh báo (Warning)")
    for w, msg in (("predictive", "Ổ đĩa báo sắp hỏng (Predictive Failure)"), ("lost communication", "Mất kết nối với ổ đĩa"),
                   ("io error", "Lỗi đọc/ghi (IO Error)"), ("failed", "Ổ đĩa ở trạng thái lỗi")):
        if w in op: bump("bad", msg)
    if smart_fail: bump("bad", "SMART dự đoán ổ đĩa sắp hỏng — sao lưu dữ liệu ngay")
    if wear is not None:
        if wear >= 90: bump("bad", f"SSD đã dùng {wear:.0f}% tuổi thọ")
        elif wear >= 70: bump("warn", f"SSD đã dùng {wear:.0f}% tuổi thọ")
    if temp:
        warn, bad = (65, 75) if ssd else (50, 60)
        if temp >= bad: bump("bad", f"Nhiệt độ rất cao: {temp:.0f}°C")
        elif temp >= warn: bump("warn", f"Nhiệt độ cao: {temp:.0f}°C")
    if errs >= 10: bump("bad", f"Có {errs:.0f} lỗi đọc/ghi không sửa được")
    elif errs > 0: bump("warn", f"Có {errs:.0f} lỗi đọc/ghi")
    if not ssd and hours and hours >= 35000: bump("warn", f"HDD đã chạy {hours:.0f} giờ (~{hours / 8760:.1f} năm), nên có kế hoạch sao lưu/thay thế")
    if level == "ok" and not health and temp is None and wear is None: level = "unknown"
    return level, LABELS[level], reasons

# ----------------------------------------------------------------- báo cáo
def build_report(raw):
    ex = raw.get("extra") or {}
    S, issues = [], []
    def sec(title, icon, rows=None, badge=None, bars=None, notes=None):
        s = {"title": title, "icon": icon, "badge": badge, "bars": bars or [], "notes": notes or [],
             "rows": [(k, v) for k, v in (rows or []) if v not in (None, "", "None", "None / None")]}
        S.append(s); return s
    if raw.get("error"):
        sec("Không đọc được một phần dữ liệu", "⚠", [("Chi tiết", str(raw["error"])[:300])], ("warn", "Lỗi"),
            notes=["Hãy chạy ứng dụng bằng quyền Administrator rồi bấm 'Quét lại'."])
    # CPU
    for i, c in enumerate(as_list(raw.get("cpu"))):
        if not isinstance(c, dict): continue
        cur = ex.get("cpu_freq")
        sec("CPU" + (f" {i + 1}" if len(as_list(raw.get("cpu"))) > 1 else ""), "🧠", [
            ("Tên", " ".join(str(c.get("Name") or "").split())), ("Hãng", c.get("Manufacturer")),
            ("Nhân / luồng", f"{c.get('NumberOfCores')} nhân / {c.get('NumberOfLogicalProcessors')} luồng"),
            ("Xung nhịp tối đa", f"{num(c.get('MaxClockSpeed')) / 1000:.2f} GHz" if num(c.get("MaxClockSpeed")) else None),
            ("Xung nhịp hiện tại", f"{cur / 1000:.2f} GHz" if cur else None),
            ("Cache L2 / L3", f"{kb(c.get('L2CacheSize'))} / {kb(c.get('L3CacheSize'))}"),
            ("Socket", c.get("SocketDesignation")),
            ("Ảo hóa (VT-x/AMD-V)", {True: "Đã bật", False: "Đang tắt (bật trong BIOS nếu cần)"}.get(c.get("VirtualizationFirmwareEnabled")))])
    # GPU
    vr = {str(v.get("Name") or "").lower(): num(v.get("Mem")) for v in as_list(raw.get("vram")) if isinstance(v, dict)}
    gpus = [g for g in as_list(raw.get("gpu")) if isinstance(g, dict)]
    real = [g for g in gpus if "basic display" not in str(g.get("Name")).lower()] or gpus
    for g in real:
        name = str(g.get("Name") or ""); mem = vr.get(name.lower()); ar = num(g.get("AdapterRAM"))
        vram = fmt_bytes(mem) if mem else (("≥ 4 GB (Windows không báo chính xác)" if ar and ar >= 4293918720 else fmt_bytes(ar)) if ar else None)
        rows = [("Tên", name), ("Hãng", g.get("Maker")), ("Bộ nhớ video (VRAM)", vram),
                ("Driver", f"{g.get('Driver')}" + (f" ({g.get('DriverDate')})" if g.get("DriverDate") else "")),
                ("Độ phân giải hiện tại", f"{g.get('W')} × {g.get('H')} @ {g.get('Hz')} Hz" if num(g.get("W")) else None)]
        for n in ex.get("nvidia") or []:
            if n["name"].lower() in name.lower() or name.lower() in n["name"].lower():
                rows += [("Nhiệt độ GPU", f"{n['temp']} °C"), ("Tải GPU", f"{n['util']} %"),
                         ("VRAM đang dùng", f"{n['mem_used']} / {n['mem_total']} MiB")]
                if num(n["temp"]) and num(n["temp"]) >= 85: issues.append(("warn", f"GPU nóng: {n['temp']}°C"))
        sec("Card đồ họa (VGA)", "🎮", rows)
    # RAM
    sticks = [m for m in as_list(raw.get("ram")) if isinstance(m, dict)]; arr = first(raw.get("ramarr"))
    total = sum(num(m.get("Capacity")) or 0 for m in sticks) or num(first(raw.get("system")).get("TotalPhysicalMemory")) or ex.get("ram_total")
    maxcap = (num(arr.get("MaxCapacityEx")) or num(arr.get("MaxCapacity")) or 0) * 1024
    rows = [("Tổng dung lượng", fmt_bytes(total)),
            ("Khe cắm đã dùng / tổng", f"{len(sticks)} / {int(num(arr.get('MemoryDevices')))}" if sticks and num(arr.get("MemoryDevices")) else None),
            ("Tối đa hỗ trợ", fmt_bytes(maxcap) if maxcap else None)]
    for j, m in enumerate(sticks, 1):
        typ = RAM_TYPES.get(int(num(m.get("SMBIOSMemoryType")) or 0), "")
        spd = num(m.get("ConfiguredClockSpeed")) or num(m.get("Speed"))
        rows.append((f"Thanh {j} ({m.get('DeviceLocator') or '?'})", " • ".join(x for x in (
            fmt_bytes(m.get("Capacity")), f"{typ} {spd:.0f} MHz".strip() if spd else typ, "SODIMM" if num(m.get("FormFactor")) == 12 else "DIMM",
            f"{str(m.get('Manufacturer') or '').strip()} {str(m.get('PartNumber') or '').strip()}".strip()) if x)))
    bars, notes, badge = [], [], None
    if ex.get("ram_total"):
        fr = ex["ram_used"] / ex["ram_total"]; lv = "bad" if fr > .92 else "warn" if fr > .8 else "ok"
        bars.append(("Đang sử dụng", fr, f"{fmt_bytes(ex['ram_used'])} / {fmt_bytes(ex['ram_total'])} ({fr * 100:.0f}%)", lv))
        if lv != "ok": issues.append((lv, f"RAM đang dùng {fr * 100:.0f}%")); badge = (lv, LABELS[lv])
    if ex.get("swap_total"):
        bars.append(("Bộ nhớ ảo (pagefile)", ex["swap_used"] / ex["swap_total"], f"{fmt_bytes(ex['swap_used'])} / {fmt_bytes(ex['swap_total'])}", "ok"))
    sec("RAM", "🧮", rows, badge, bars, notes)
    # Ổ đĩa
    vols = [v for v in as_list(raw.get("vols")) if isinstance(v, dict)]
    smart_fail = any(isinstance(s, dict) and s.get("PredictFailure") is True for s in as_list(raw.get("smart")))
    used_letters = set()
    for d in sorted((x for x in as_list(raw.get("disks")) if isinstance(x, dict)), key=lambda x: num(x.get("Number")) or 0):
        level, label, reasons = disk_verdict(d, smart_fail)
        if level in ("warn", "bad"): issues.append((level, f"Ổ đĩa {d.get('Name')}: {reasons[0] if reasons else label}"))
        letters = [str(x).upper() for x in as_list(d.get("Letters"))]; used_letters |= set(letters)
        temp, wear, hours = num(d.get("Temp")) or None, num(d.get("Wear")), num(d.get("Hours"))
        errs = (num(d.get("ReadErr")) or 0) + (num(d.get("WriteErr")) or 0)
        bars = []
        for v in vols:
            if str(v.get("L")).upper() in letters and num(v.get("Size")):
                size, free = num(v["Size"]), num(v.get("Free")) or 0; fp = free / size
                lv = "bad" if fp < .05 else "warn" if fp < .10 else "ok"
                if lv != "ok": issues.append((lv, f"Ổ {v['L']}: chỉ còn {fp * 100:.0f}% trống"))
                bars.append((f"{v['L']}: {v.get('Label') or ''} [{v.get('FS') or '?'}]".replace("  ", " "), 1 - fp,
                             f"{fmt_bytes(size - free)} / {fmt_bytes(size)} • trống {fmt_bytes(free)} ({fp * 100:.0f}%)", lv))
        sec(f"Ổ đĩa {int(num(d.get('Number')) or 0)}: {d.get('Name') or ''}", "💾", [
            ("Loại", disk_kind(d)), ("Dung lượng", f"{fmt_bytes(d.get('Size'))}  (nhãn: {nominal(d.get('Size'))})"),
            ("Firmware", d.get("Firmware")), ("Tình trạng (Windows)", f"{d.get('Health') or '—'} / {d.get('Op') or '—'}"),
            ("Nhiệt độ", f"{temp:.0f} °C" if temp else None),
            ("Mức hao mòn SSD", f"{wear:.0f}% tuổi thọ đã dùng" if wear is not None else None),
            ("Thời gian hoạt động", f"{hours:.0f} giờ (~{hours / 24:.0f} ngày)" if hours else None),
            ("Lỗi đọc/ghi", f"{errs:.0f}" if (d.get("ReadErr") is not None or d.get("WriteErr") is not None) else None),
            ("Ổ logic", ", ".join(f"{x}:" for x in letters) or None)],
            (level, label), bars, [f"• {r}" for r in reasons] + (["• Chưa đọc được SMART/độ hao mòn — thử chạy bằng Administrator."] if level == "unknown" else []))
    other = [v for v in vols if str(v.get("L")).upper() not in used_letters and num(v.get("Size"))]
    if other:
        bars = []
        for v in other:
            size, free = num(v["Size"]), num(v.get("Free")) or 0
            bars.append((f"{v['L']}: {v.get('Label') or ''}".strip(), 1 - free / size, f"{fmt_bytes(size - free)} / {fmt_bytes(size)} • trống {fmt_bytes(free)}", "ok"))
        sec("Phân vùng khác (USB/ảo)", "🗂", bars=bars)
    # Bo mạch / hệ thống
    sy, bd, bi, os_ = first(raw.get("system")), first(raw.get("board")), first(raw.get("bios")), first(raw.get("os"))
    sc, sb = raw.get("sec") or {}, (raw.get("sec") or {}).get("SecureBoot")
    temps = [t for t in as_list(raw.get("temp")) if num(t)]
    up = ""
    if ex.get("boot"):
        import time as _t; s = int(_t.time() - ex["boot"]); up = f"{s // 86400} ngày {s % 86400 // 3600} giờ"
    sec("Bo mạch chủ / Hệ thống", "🖥", [
        ("Máy", f"{sy.get('Manufacturer') or ''} {sy.get('Model') or ''}".strip()),
        ("Bo mạch chủ", f"{bd.get('Manufacturer') or ''} {bd.get('Product') or ''}".strip()),
        ("BIOS/UEFI", f"{bi.get('Manufacturer') or ''} {bi.get('Version') or ''} ({bi.get('Date')})" if bi else None),
        ("Secure Boot", {True: "Bật", False: "Tắt"}.get(sb, "Không rõ")),
        ("TPM", ("Có" + (", sẵn sàng" if sc.get("TpmReady") else "")) if sc.get("TpmPresent") else ("Không phát hiện" if sc.get("TpmPresent") is False else None)),
        ("Hệ điều hành", f"{os_.get('Caption') or ''} (build {os_.get('Build')}, {os_.get('Arch')})".strip() if os_ else None),
        ("Cài đặt ngày", os_.get("Install")), ("Đã chạy liên tục", up or None),
        ("Nhiệt độ ACPI (tham khảo)", f"{max(map(float, temps)):.0f} °C" if temps else None)])
    # Màn hình
    mons = [m for m in as_list(raw.get("mon")) if isinstance(m, dict) and (m.get("Name") or m.get("Maker"))]
    if mons: sec("Màn hình", "🖵", [(f"Màn hình {i}", f"{m.get('Name') or 'Không rõ'} ({m.get('Maker') or '?'}, sản xuất {m.get('Year') or '?'})") for i, m in enumerate(mons, 1)])
    # Mạng
    nets = [n for n in as_list(raw.get("net")) if isinstance(n, dict)]
    if nets or ex.get("ifaces"):
        rows = [(f"Card mạng {i}", f"{n.get('Name')}" + (f" • {num(n.get('Speed')) / 1e6:.0f} Mbps" if num(n.get("Speed")) and num(n.get("Speed")) < 1e12 else "")) for i, n in enumerate(nets, 1)]
        rows += [(f"Kết nối: {i['name']}", f"{i['ip']}" + (f" • {i['speed']} Mbps" if i.get("speed") else "")) for i in ex.get("ifaces") or []]
        sec("Mạng", "🌐", rows)
    # Pin
    if ex.get("battery"):
        b, bt = ex["battery"], raw.get("battery") or {}; full, des = num(bt.get("Full")), num(bt.get("Design")); rows, badge, notes = [], None, []
        rows.append(("Mức pin", f"{b['percent']:.0f}% ({'đang cắm sạc' if b.get('plugged') else 'đang dùng pin'})"))
        if full and des:
            h = full / des * 100; lv = "bad" if h < 60 else "warn" if h < 80 else "ok"; badge = (lv, f"Độ chai {100 - h:.0f}%")
            rows.append(("Sức khỏe pin", f"{h:.0f}% dung lượng thiết kế ({full:.0f} / {des:.0f} mWh)"))
            if lv != "ok": issues.append((lv, f"Pin chỉ còn {h:.0f}% dung lượng thiết kế"))
        sec("Pin", "🔋", rows, badge, [], notes)
    # Tổng hợp (đặt đầu danh sách)
    worst = max((LEVELS[l] for l, _ in issues), default=0); lv = {0: "ok", 1: "warn", 2: "bad"}[worst]
    ndisk = sum(1 for s in S if s["icon"] == "💾")
    notes = [f"{'✖' if l == 'bad' else '⚠'} {m}" for l, m in sorted(issues, key=lambda x: -LEVELS[x[0]])] or ["✔ Không phát hiện vấn đề nào."]
    if not ex.get("admin"): notes.append("• Chưa chạy bằng Administrator: SMART, TPM, Secure Boot có thể không đọc được.")
    S.insert(0, {"title": "Tổng hợp tình trạng", "icon": "🩺", "badge": (lv, LABELS[lv]), "bars": [], "notes": notes,
                 "rows": [("Ổ cứng được kiểm tra", str(ndisk)), ("Vấn đề phát hiện", str(len(issues)))]})
    return S

def report_text(sections):
    out = []
    for s in sections:
        out.append(f"== {s['title']}" + (f"  [{LABELS.get(s['badge'][0], s['badge'][1]) if s['badge'] else ''}]" if s.get("badge") else ""))
        out += [f"  {k}: {v}" for k, v in s["rows"]]
        out += [f"  {lbl}: {txt}" for lbl, _, txt, _ in s["bars"]]
        out += [f"  {n}" for n in s["notes"]]; out.append("")
    return "\n".join(out)
