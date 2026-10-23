# -*- coding: utf-8 -*-
"""Kiểm thử PC Optimizer Pro. Chạy:  python test_pc_optimizer.py
- Phần LOGIC: chạy được trên mọi hệ điều hành (dùng thư mục tạm, không đụng dữ liệu thật).
- Phần GIAO DIỆN: cần màn hình (Windows có sẵn; Linux dùng: xvfb-run -a python test_pc_optimizer.py).
Các lệnh Windows thật (netsh, taskkill, schtasks, khay hệ thống, toast) KHÔNG được kiểm thử ở đây.
"""
import os, sys, json, time, socket, shutil, tempfile, threading, unittest, stat, types
from unittest import mock

BASE = tempfile.mkdtemp(prefix="pco_test_")
P = lambda *a: os.path.join(BASE, *a)
for d in ("lad/Temp", "up/Downloads", "win/Temp", "win/Prefetch", "pd"):
    os.makedirs(P(*d.split("/")), exist_ok=True)
os.environ.update(LOCALAPPDATA=P("lad"), USERPROFILE=P("up"), WINDIR=P("win"), TEMP=P("lad", "Temp"),
                  SystemDrive=P("sysdrive"), PROGRAMDATA=P("pd"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pc_optimizer as pco

pco.CONFIG_FILE = pco.DATA_DIR / "config.json"
pco.LOG_FILE = pco.DATA_DIR / "log.txt"
TEMP = P("lad", "Temp")

def mk(path, size=100, readonly=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(b"x" * size)
    if readonly: os.chmod(path, stat.S_IREAD)
    return path

def reset():
    for d in (TEMP, P("up", "Downloads"), P("win", "Prefetch")):
        shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    for f in (pco.CONFIG_FILE, pco.LOG_FILE):
        if f.exists(): f.unlink()


class TestCore(unittest.TestCase):
    def setUp(self): reset()

    def test_fmt(self):
        self.assertEqual(pco.fmt(0), "0 B"); self.assertEqual(pco.fmt(1536), "1.5 KB"); self.assertEqual(pco.fmt(5 * 1024 ** 3), "5.0 GB")

    def test_parse_version_and_is_newer(self):
        self.assertEqual(pco.parse_version("v2.6"), (2, 6, 0, 0)); self.assertEqual(pco.parse_version("2.10.1-beta"), (2, 10, 1, 0))
        self.assertEqual(pco.parse_version("linh tinh"), (0, 0, 0, 0))
        self.assertTrue(pco.is_newer("2.10", "2.9"))            # so số, không so chuỗi
        self.assertTrue(pco.is_newer("v2.7", "2.6")); self.assertFalse(pco.is_newer("2.6.0", "2.6")); self.assertFalse(pco.is_newer("2.5", "2.6"))
        self.assertFalse(pco.is_newer("", "2.6"))

    def test_pick_release_prefers_app_exe(self):
        data = {"tag_name": "v2.7", "html_url": "https://github.com/o/r/releases/tag/v2.7", "body": " ghi chú ", "published_at": "2026-10-23T08:00:00Z",
                "assets": [{"name": "PCOptimizerPro.exe.sha256", "browser_download_url": "https://x/sha"},
                           {"name": "setup_helper.exe", "browser_download_url": "https://x/helper"},
                           {"name": "PCOptimizerPro.exe", "browser_download_url": "https://github.com/o/r/releases/download/v2.7/PCOptimizerPro.exe", "size": 12345}]}
        i = pco.pick_release(data, "o/r")
        self.assertEqual((i["tag"], i["version"], i["notes"], i["published"], i["size"]), ("v2.7", "2.7", "ghi chú", "2026-10-23", 12345))
        self.assertTrue(i["asset_url"].endswith("/PCOptimizerPro.exe"))
        i2 = pco.pick_release({"tag_name": "v1.0"}, "o/r")           # chưa có file đính kèm -> rơi về trang phát hành
        self.assertEqual((i2["asset_url"], i2["page"]), ("", "https://github.com/o/r/releases"))

    def test_fetch_latest_release_against_local_server(self):
        import http.server
        payload = {"tag_name": "v9.9", "html_url": "https://github.com/o/r/releases/tag/v9.9",
                   "assets": [{"name": "PCOptimizerPro.exe", "browser_download_url": "https://github.com/o/r/releases/download/v9.9/PCOptimizerPro.exe", "size": 1}]}
        seen = {}
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["ua"] = self.headers.get("User-Agent")
                if self.path == "/repos/o/r/releases/latest":
                    body = json.dumps(payload).encode(); self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        api = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            info, err = pco.fetch_latest_release("o/r", api=api); self.assertIsNone(err); self.assertEqual(info["tag"], "v9.9")
            self.assertTrue(pco.is_newer(info["version"])); self.assertIn("PCOptimizerPro/", seen["ua"])      # GitHub bắt buộc có User-Agent
            info, err = pco.fetch_latest_release("o/chua-co-release", api=api); self.assertIsNone(info); self.assertIn("Release", err)
        finally:
            srv.shutdown(); srv.server_close()
        self.assertIn("kết nối", pco.fetch_latest_release("o/r", api=api, timeout=1)[1])   # server đã đóng
        for bad in ("", "khong-co-gach-cheo", "a/b/c", "a b/c"): self.assertIn("cấu hình", pco.fetch_latest_release(bad)[1])

    def test_tooltip_texts_cover_everything(self):
        self.assertEqual({k for k, *_ in pco.TARGETS} - set(pco.TARGET_HELP), set())     # mọi mục dọn đều có giải thích
        self.assertEqual(set(pco.PAGE_INFO) - set(pco.NAV_HINT), set())                  # mọi trang đều có giải thích ở thanh bên
        self.assertTrue(all(len(v) > 15 for v in pco.HINTS.values()))
        self.assertTrue(all(isinstance(v, str) and v for v in list(pco.SWITCH_HELP.values()) + list(pco.TARGET_HELP.values())))

    def test_ui_helpers(self):
        self.assertEqual([pco.log_tag(x) for x in ("✔ a", "  ✖ b", "⚠ c", "[thử] d", "▶ e", "xyz")], ["ok", "bad", "warn", "warn", "info", None])
        self.assertEqual((pco.lvl_color(10), pco.lvl_color(75), pco.lvl_color(95)), (pco.C.OK, pco.C.WARN, pco.C.BAD))
        s = pco.short_path("C:\\" + "a" * 200, 40); self.assertLessEqual(len(s), 42); self.assertIn("…", s)
        self.assertEqual(pco.short_path("C:\\ngắn", 40), "C:\\ngắn")

    def test_expand_dedupes_same_folder(self):
        self.assertEqual(len(pco.expand([r"%TEMP%", r"%LOCALAPPDATA%\Temp"])), 1)

    def test_expand_wildcard(self):
        mk(P("lad", "Cache1", "a")); mk(P("lad", "Cache2", "a"))
        self.assertEqual(len(pco.expand([r"%LOCALAPPDATA%\Cache*"])), 2)

    def test_collect_counts_once_with_sizes(self):
        mk(P("lad", "Temp", "a.tmp"), 1000); mk(P("lad", "Temp", "sub", "b.tmp"), 2000)
        items = pco.collect_items(["usr_temp"])  # usr_temp liệt kê %TEMP% và %LOCALAPPDATA%\Temp (cùng thư mục)
        self.assertEqual(len(items), 2); self.assertEqual(sum(i["size"] for i in items), 3000)

    def test_whitelist_protects_item_and_its_parent(self):
        mk(P("lad", "Temp", "keep", "important.txt")); mk(P("lad", "Temp", "junk.tmp"))
        mk(P("lad", "Temp", "other", "x.tmp"))
        wl = [P("lad", "Temp", "keep", "important.txt")]
        paths = [os.path.basename(i["path"]) for i in pco.collect_items(["usr_temp"], wl)]
        self.assertEqual(sorted(paths), ["junk.tmp", "other"])

    def test_protects_pyinstaller_dir_and_data_dir(self):
        mine = mk(P("lad", "Temp", "_MEI123", "lib.dll")); mk(P("lad", "Temp", "junk.tmp"))
        with mock.patch.object(sys, "_MEIPASS", P("lad", "Temp", "_MEI123"), create=True):
            names = [os.path.basename(i["path"]) for i in pco.collect_items(["usr_temp"])]
        self.assertEqual(names, ["junk.tmp"]); self.assertTrue(os.path.exists(mine))
        self.assertTrue(pco.is_protected(str(pco.DATA_DIR / "config.json")))

    def test_delete_entry_dry_then_real_incl_readonly(self):
        d = P("lad", "Temp", "dir"); mk(d + "/ro.bin", 500, readonly=True); mk(d + "/n/x", 500)
        self.assertEqual(pco.delete_entry(d, dry=True), (1000, 0)); self.assertTrue(os.path.exists(d))
        self.assertEqual(pco.delete_entry(d), (1000, 0)); self.assertFalse(os.path.exists(d))

    def test_execute_items_and_cancel(self):
        for i in range(5): mk(P("lad", "Temp", f"f{i}"), 10)
        items = pco.collect_items(["usr_temp"]); ev = threading.Event(); seen = []
        def prog(f):
            seen.append(f)
            if len(seen) == 2: ev.set()
        total, fails, per = pco.execute_items(items, False, prog, ev)
        self.assertEqual((total, fails), (20, 0)); self.assertEqual(len(os.listdir(TEMP)), 3)

    def test_run_clean_writes_history_but_dry_does_not(self):
        mk(P("lad", "Temp", "a"), 300); cfg = pco.load_config(); out = []
        pco.run_clean(["usr_temp"], cfg, dry=True, log=out.append)
        self.assertEqual(os.listdir(TEMP), ["a"]); self.assertEqual(cfg["history"], [])
        total, _ = pco.run_clean(["usr_temp"], cfg, dry=False, log=out.append)
        self.assertEqual(total, 300); self.assertEqual(os.listdir(TEMP), [])
        self.assertEqual(pco.load_config()["history"][0]["freed"], 300)

    def test_config_defaults_corrupt_and_merge(self):
        self.assertEqual(pco.load_config()["schedule"]["mode"], "off")
        pco.CONFIG_FILE.write_text("{hỏng json", "utf-8"); self.assertTrue(pco.load_config()["preview"])
        pco.CONFIG_FILE.write_text(json.dumps({"schedule": {"mode": "daily"}, "whitelist": ["x"]}), "utf-8")
        c = pco.load_config(); self.assertEqual((c["schedule"]["mode"], c["schedule"]["time"], c["whitelist"]), ("daily", "21:00", ["x"]))

    def test_auto_run_never_touches_downloads(self):
        mk(P("lad", "Temp", "t.tmp"), 50); dl = mk(P("up", "Downloads", "precious.zip"), 50)
        cfg = pco.load_config(); cfg["auto_keys"] = ["usr_temp", "downloads"]; pco.save_config(cfg)
        with mock.patch.object(pco, "toast") as t:
            self.assertEqual(pco.auto_run(), 0); t.assert_called_once()
        self.assertTrue(os.path.exists(dl)); self.assertEqual(os.listdir(TEMP), [])
        self.assertIn("[auto]", pco.LOG_FILE.read_text("utf-8"))

    def test_build_info_script_and_fallback(self):
        d = tempfile.mkdtemp(); here = os.path.dirname(os.path.abspath(__file__))
        pco.subprocess.run([sys.executable, os.path.join(here, "make_build_info.py")], cwd=d, check=True, capture_output=True)
        ns = {}
        with open(os.path.join(d, "build_info.py"), encoding="utf-8") as fh: exec(fh.read(), ns)
        self.assertRegex(ns["BUILD_TIME"], r"^\d{4}-\d\d-\d\d \d\d:\d\d$"); self.assertTrue(pco.BUILD_TIME)

    def test_valid_time(self):
        for ok in ("00:00", "09:05", "23:59"): self.assertTrue(pco.valid_time(ok), ok)
        for bad in ("24:00", "9:05", "12:60", "", "ab:cd", None): self.assertFalse(pco.valid_time(bad), bad)

    def test_schtasks_commands(self):
        self.assertIsNone(pco.build_schtasks({"mode": "off"}))
        d = pco.build_schtasks({"mode": "daily", "time": "21:30", "day": "MON"})
        self.assertEqual(d[d.index("/SC") + 1], "DAILY"); self.assertEqual(d[d.index("/ST") + 1], "21:30")
        self.assertIn("--auto", d[d.index("/TR") + 1]); self.assertIn("HIGHEST", d)
        w = pco.build_schtasks({"mode": "weekly", "time": "08:00", "day": "FRI"}); self.assertEqual(w[w.index("/D") + 1], "FRI")
        l = pco.build_schtasks({"mode": "logon", "time": "", "day": ""}); self.assertEqual(l[l.index("/SC") + 1], "ONLOGON"); self.assertNotIn("/ST", l)

    def test_self_cmd(self):
        self.assertTrue(pco.self_cmd("--tray").endswith("--tray")); self.assertIn(".py", pco.self_cmd())
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", "C:\\app\\pco.exe"):
            self.assertEqual(pco.self_cmd("--auto"), '"C:\\app\\pco.exe" --auto')

    def test_dns_query_against_local_udp_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(("127.0.0.1", 0)); port = srv.getsockname()[1]
        got = {}
        def serve():
            data, addr = srv.recvfrom(512); got["q"] = data; srv.sendto(data[:2] + b"\x81\x80" + data[4:], addr)
        threading.Thread(target=serve, daemon=True).start()
        ms = pco.dns_query_ms("127.0.0.1", "example.com", port=port)
        self.assertIsNotNone(ms); self.assertLess(ms, 1000); self.assertIn(b"\x07example\x03com\x00", got["q"]); srv.close()
        self.assertIsNone(pco.dns_query_ms("127.0.0.1", "example.com", timeout=0.2, port=port))  # server đã đóng

    def test_startup_task_helpers(self):
        calls = []
        with mock.patch.object(pco, "sh", side_effect=lambda c, *a, **k: (calls.append(c), (0, "OK"))[1]):
            self.assertTrue(pco.startup_enabled()); pco.set_startup(True)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("/Create" in f and "ONLOGON" in f and "HIGHEST" in f and "--tray" in f for f in flat))
        with mock.patch.object(pco, "sh", return_value=(1, "Access denied")):
            self.assertFalse(pco.startup_enabled())
            with self.assertRaises(RuntimeError): pco.set_startup(True)

    def test_sh_does_not_inherit_stdin(self):  # bắt buộc cho exe --noconsole
        with mock.patch.object(pco.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as r:
            pco.sh("echo"); pco.ps("1")
        for c in r.call_args_list: self.assertEqual(c.kwargs["stdin"], pco.subprocess.DEVNULL)

    def test_log_rotation(self):
        pco.LOG_FILE.write_bytes((b"dong cu xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n") * 25000)  # ~1.5 MB
        pco.append_log("dong moi\n")
        self.assertLess(pco.LOG_FILE.stat().st_size, 300_000); self.assertTrue(pco.LOG_FILE.read_text("utf-8").endswith("dong moi\n"))

    def test_relaunch_admin_frozen_passes_only_args(self):
        with mock.patch.object(pco.ctypes, "windll", create=True) as w, mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "argv", ["C:\\app\\pco.exe", "--tray"]), mock.patch.object(sys, "executable", "C:\\app\\pco.exe"):
            pco.relaunch_admin()
        args = w.shell32.ShellExecuteW.call_args.args
        self.assertEqual((args[1], args[2], args[3]), ("runas", "C:\\app\\pco.exe", '"--tray"'))


import hardware_info as hw

GB = 1024 ** 3
RAW = {
    "cpu": [{"Name": "AMD Ryzen 5 5600X   6-Core Processor", "Manufacturer": "AuthenticAMD", "NumberOfCores": 6, "NumberOfLogicalProcessors": 12,
             "MaxClockSpeed": 3701, "L2CacheSize": 3072, "L3CacheSize": 32768, "SocketDesignation": "AM4", "VirtualizationFirmwareEnabled": True}],
    "system": {"Manufacturer": "Micro-Star", "Model": "MS-7C56", "TotalPhysicalMemory": 16 * GB},   # PowerShell: 1 phần tử -> object, không phải list
    "board": [{"Manufacturer": "Micro-Star International", "Product": "B550-A PRO"}],
    "bios": [{"Manufacturer": "American Megatrends", "Version": "H.90", "Date": "2022-03-10"}],
    "os": [{"Caption": "Microsoft Windows 11 Pro", "Build": "26100", "Arch": "64-bit", "Install": "2024-01-02"}],
    "gpu": [{"Name": "NVIDIA GeForce RTX 3060", "Maker": "NVIDIA", "Driver": "32.0.15.6094", "DriverDate": "2024-10-01",
             "AdapterRAM": 4293918720, "W": 1920, "H": 1080, "Hz": 144}],
    "vram": [{"Name": "NVIDIA GeForce RTX 3060", "Mem": 12 * GB}],
    "ram": [{"DeviceLocator": "DIMM_A1", "Capacity": 8 * GB, "Speed": 3200, "ConfiguredClockSpeed": 3200, "Manufacturer": "Kingston", "PartNumber": "KF3200C16D4/8GX ", "SMBIOSMemoryType": 26, "FormFactor": 8},
            {"DeviceLocator": "DIMM_B1", "Capacity": 8 * GB, "Speed": 3200, "ConfiguredClockSpeed": 3200, "Manufacturer": "Kingston", "PartNumber": "KF3200C16D4/8GX ", "SMBIOSMemoryType": 26, "FormFactor": 8}],
    "ramarr": [{"MaxCapacity": 134217728, "MaxCapacityEx": 134217728, "MemoryDevices": 4}],
    "disks": [
        {"Number": 0, "Name": "Samsung SSD 980 PRO 1TB", "Media": "SSD", "Bus": "NVMe", "Size": 1000204886016, "Health": "Healthy", "Op": "OK", "Firmware": "5B2QGXA7", "Temp": 41, "Wear": 3, "Hours": 2400, "ReadErr": 0, "WriteErr": 0, "Letters": "C"},
        {"Number": 1, "Name": "WDC WD10EZEX-08WN4A0", "Media": "HDD", "Bus": "SATA", "Size": 1000204886016, "Health": "Warning", "Op": "OK", "Firmware": "01.01A01", "Temp": 52, "Wear": None, "Hours": 41000, "ReadErr": 3, "WriteErr": 0, "Letters": ["D", "E"]}],
    "vols": [{"L": "C", "Label": "Windows", "FS": "NTFS", "Size": 930 * GB, "Free": 40 * GB},
             {"L": "D", "Label": "Data", "FS": "NTFS", "Size": 500 * GB, "Free": 200 * GB},
             {"L": "E", "Label": "", "FS": "NTFS", "Size": 431 * GB, "Free": 10 * GB},
             {"L": "F", "Label": "USB", "FS": "FAT32", "Size": 29 * GB, "Free": 20 * GB}],
    "smart": [{"PredictFailure": False}],
    "net": [{"Name": "Realtek PCIe GbE Family Controller", "Speed": 1000000000}],
    "mon": [{"Name": "DELL U2723QE", "Maker": "DEL", "Year": 2023}],
    "battery": {"Full": None, "Design": None}, "sec": {"SecureBoot": True, "TpmPresent": True, "TpmReady": True}, "temp": [],
    "extra": {"cpu_freq": 3700.0, "ram_total": 16 * GB, "ram_used": 12 * GB, "swap_total": 4 * GB, "swap_used": GB, "boot": time.time() - 90000,
              "ifaces": [{"name": "Ethernet", "ip": "192.168.1.10", "speed": 1000}], "nvidia": [{"name": "NVIDIA GeForce RTX 3060", "mem_total": "12288", "mem_used": "1500", "temp": "47", "util": "5", "driver": "560.94"}], "admin": True}}

def find(sections, text):
    return [s for s in sections if text in s["title"]]


class TestHardware(unittest.TestCase):
    def test_powershell_quirks_and_formatting(self):
        self.assertEqual(hw.as_list(None), []); self.assertEqual(hw.as_list({"a": 1}), [{"a": 1}]); self.assertEqual(hw.as_list("C"), ["C"])
        self.assertEqual(hw.first([]), {}); self.assertEqual(hw.first({"a": 1}), {"a": 1})
        self.assertEqual(hw.fmt_bytes(16 * GB), "16 GB"); self.assertEqual(hw.fmt_bytes(1536), "1.5 KB"); self.assertEqual(hw.fmt_bytes(None), "—")
        self.assertEqual(hw.nominal(1000204886016), "1 TB"); self.assertEqual(hw.nominal(500107862016), "500 GB"); self.assertEqual(hw.nominal(2000398934016), "2 TB")

    def test_disk_verdict_thresholds(self):
        ssd = {"Media": "SSD", "Bus": "NVMe", "Health": "Healthy", "Op": "OK", "Temp": 40, "Wear": 5, "ReadErr": 0, "WriteErr": 0}
        hdd = {"Media": "HDD", "Bus": "SATA", "Health": "Healthy", "Op": "OK", "Temp": 40, "Hours": 1000}
        v = lambda d, **kw: hw.disk_verdict({**d, **kw})[0]
        self.assertEqual(v(ssd), "ok"); self.assertEqual(v(ssd, Health="Warning"), "warn"); self.assertEqual(v(ssd, Health="Unhealthy"), "bad")
        self.assertEqual(v(ssd, Op="Predictive Failure"), "bad")
        self.assertEqual(v(ssd, Temp=66), "warn"); self.assertEqual(v(ssd, Temp=76), "bad")
        self.assertEqual(v(hdd, Temp=51), "warn"); self.assertEqual(v(hdd, Temp=61), "bad")      # HDD nhạy nhiệt hơn SSD
        self.assertEqual(v(ssd, Wear=70), "warn"); self.assertEqual(v(ssd, Wear=90), "bad"); self.assertEqual(v(ssd, Wear=0), "ok")
        self.assertEqual(v(ssd, ReadErr=1), "warn"); self.assertEqual(v(ssd, ReadErr=6, WriteErr=4), "bad")
        self.assertEqual(v(hdd, Hours=40000), "warn"); self.assertEqual(v(ssd, Hours=90000), "ok")  # giờ chạy chỉ cảnh báo với HDD
        self.assertEqual(hw.disk_verdict(ssd, smart_fail=True)[0], "bad")
        self.assertEqual(hw.disk_verdict({"Media": "Unspecified", "Bus": "USB"})[0], "unknown")
        self.assertEqual(hw.disk_verdict({"Health": "Healthy", "Temp": 0})[0], "ok")               # Temp=0 = không có dữ liệu

    def test_full_report(self):
        S = hw.build_report(RAW); self.assertEqual(S[0]["title"], "Tổng hợp tình trạng")
        gpu = find(S, "VGA")[0]; rows = dict(gpu["rows"])
        self.assertEqual(rows["Bộ nhớ video (VRAM)"], "12 GB")                    # lấy từ registry, không bị trần 4 GB của Win32
        self.assertIn("47 °C", rows["Nhiệt độ GPU"])
        ram = dict(find(S, "RAM")[0]["rows"]); self.assertEqual(ram["Tổng dung lượng"], "16 GB"); self.assertEqual(ram["Khe cắm đã dùng / tổng"], "2 / 4")
        self.assertIn("DDR4 3200 MHz", ram["Thanh 1 (DIMM_A1)"]); self.assertEqual(ram["Tối đa hỗ trợ"], "128 GB")
        d0, d1 = find(S, "Ổ đĩa 0")[0], find(S, "Ổ đĩa 1")[0]
        self.assertEqual(d0["badge"][0], "ok"); self.assertEqual(d1["badge"][0], "warn")
        self.assertEqual([b[0][0] for b in d0["bars"]], ["C"]); self.assertEqual([b[0][0] for b in d1["bars"]], ["D", "E"])   # Letters dạng chuỗi "C" vẫn map đúng
        self.assertEqual(find(S, "Phân vùng khác")[0]["bars"][0][0][0], "F")
        e = [b for b in d1["bars"] if b[0].startswith("E")][0]; self.assertEqual(e[3], "bad")                          # trống 2,3% (<5%) -> nguy hiểm
        notes = "\n".join(S[0]["notes"]); self.assertIn("WDC", notes); self.assertIn("HDD đã chạy", "\n".join(d1["notes"]))
        self.assertEqual(S[0]["badge"][0], "bad")   # ổ E còn 2% trống -> tổng hợp ở mức nguy hiểm
        board = dict(find(S, "Bo mạch")[0]["rows"]); self.assertEqual(board["Secure Boot"], "Bật"); self.assertIn("B550-A PRO", board["Bo mạch chủ"])
        self.assertIn("1 ngày", board["Đã chạy liên tục"]); self.assertTrue(find(S, "Màn hình")); self.assertTrue(find(S, "Mạng"))
        self.assertFalse(find(S, "Pin"))

    def test_battery_health(self):
        raw = {**RAW, "battery": {"Full": 30000, "Design": 50000}, "extra": {**RAW["extra"], "battery": {"percent": 80, "plugged": False}}}
        pin = find(hw.build_report(raw), "Pin")[0]; self.assertEqual(pin["badge"][0], "warn")
        self.assertEqual(pin["badge"][1], "Độ chai 40%"); self.assertIn("60%", dict(pin["rows"])["Sức khỏe pin"])

    def test_robust_against_bad_data(self):
        for raw in ({}, {"error": "PowerShell lỗi"}, {"cpu": "rác", "disks": [None, 5, {"Number": "x"}], "vols": "?", "gpu": [None], "ram": [{}], "extra": None},
                    {"disks": [{"Number": 0, "Letters": None, "Size": None}], "sec": None, "battery": None}):
            S = hw.build_report(raw); self.assertEqual(S[0]["title"], "Tổng hợp tình trạng"); hw.report_text(S)
        S = hw.build_report({"error": "x"}); self.assertEqual(S[1]["title"], "Không đọc được một phần dữ liệu")
        self.assertIn("Administrator", "\n".join(S[0]["notes"]))

    def test_report_text(self):
        t = hw.report_text(hw.build_report(RAW))
        for s in ("Samsung SSD 980 PRO", "WDC WD10EZEX", "RTX 3060", "DDR4", "Cần chú ý"): self.assertIn(s, t)

    def test_ps_json_parsing_and_errors(self):
        good = b'\xef\xbb\xbfWARNING: profile noise\n{"cpu":[{"Name":"X"}],"disks":[]}'
        with mock.patch.object(hw, "_run", return_value=(0, good)): self.assertEqual(hw.ps_json("x")["cpu"][0]["Name"], "X")
        with mock.patch.object(hw, "_run", return_value=(1, b"Access denied")): self.assertIn("không trả dữ liệu", hw.ps_json("x")["error"])
        with mock.patch.object(hw, "_run", return_value=(0, "{hỏng".encode("utf-8"))): self.assertIn("JSON", hw.ps_json("x")["error"])

    def test_ps_script_is_safe_to_embed(self):
        import base64
        self.assertNotIn('"', hw.PS_SCRIPT); self.assertTrue(hw.PS_SCRIPT.strip().endswith("ConvertTo-Json -InputObject $r -Depth 6 -Compress"))
        self.assertEqual(hw.PS_SCRIPT.count("{"), hw.PS_SCRIPT.count("}")); self.assertEqual(hw.PS_SCRIPT.count("("), hw.PS_SCRIPT.count(")"))
        with mock.patch.object(hw, "_run", return_value=(0, b"{}")) as r: hw.ps_json(hw.PS_SCRIPT)
        enc = r.call_args.args[0][-1]; self.assertEqual(base64.b64decode(enc).decode("utf-16-le"), hw.PS_SCRIPT)

    def test_collect_never_raises_off_windows(self):
        with mock.patch.object(hw, "_run", return_value=(1, b"")):
            raw = hw.collect()
        self.assertIn("error", raw); self.assertIn("extra", raw); hw.build_report(raw)


@unittest.skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "cần màn hình (Linux: xvfb-run -a)")
class TestGUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset(); c = pco.load_config(); c["tray"] = False; pco.save_config(c)
        for n in ("showinfo", "showerror", "showwarning"): mock.patch.object(pco.messagebox, n).start()
        mock.patch.object(pco.messagebox, "askyesno", return_value=True).start()
        mock.patch.object(pco.hw, "collect", return_value=RAW).start()   # test không phụ thuộc phần cứng thật (quét thật mất 10+ giây)
        cls.app = pco.App(); cls.app.update()
    @classmethod
    def tearDownClass(cls):
        mock.patch.stopall()
        try: cls.app.destroy()
        except Exception: pass
    def setUp(self):
        reset(); self.app.cfg = pco.load_config(); self.app.cfg["tray"] = False; self.app.cfg["whitelist"] = []; self.app.dry.set(False)
    def pump(self, cond, timeout=10):
        t = time.time()
        while time.time() - t < timeout:
            self.app.update()
            if cond(): return True
            time.sleep(0.03)
        return False
    def idle(self): return self.pump(lambda: not self.app.busy)
    def dialog(self):
        ds = [w for w in self.app.winfo_children() if isinstance(w, pco.PreviewDialog)]; return ds[0] if ds else None

    def test_title_shows_version(self):
        self.assertIn(pco.APP_VERSION, self.app.title()); self.assertIn("PC Optimizer Pro", self.app.title())

    def test_update_flow_banner_and_state(self):
        app = self.app; new = {"tag": "v99.0", "version": "99.0", "page": "https://github.com/o/r/releases/tag/v99.0", "notes": "",
                               "asset_url": "https://github.com/o/r/releases/download/v99.0/PCOptimizerPro.exe", "asset_name": "PCOptimizerPro.exe",
                               "size": 5 * 1024 ** 2, "published": "2026-10-23"}
        with mock.patch.object(pco, "save_config"):
            app._upd_busy = True; app._update_done(new, None, True)
            self.assertFalse(app._upd_busy); self.assertEqual(app.update_info["tag"], "v99.0"); self.assertIn("v99.0", app.upd_lbl.cget("text"))
            self.assertIn("5.0 MB", app.upd_sub.cget("text")); self.assertGreater(app.cfg["update"]["last_check"], 0)
            app.update_skip(); self.assertEqual(app.cfg["update"]["skipped"], "v99.0")
            app._update_done(dict(new, tag="v0.1", version="0.1"), None, True); self.assertIsNone(app.update_info)      # bản cũ hơn -> không báo
            self.assertIn("mới nhất", app.upd_status.cget("text"))
            app._update_done(None, "Lỗi giả lập", True); self.assertIn("Lỗi giả lập", app.upd_status.cget("text"))
            app.cfg["update"]["skipped"] = ""
        with mock.patch.object(pco, "UPDATE_REPO", ""), mock.patch.object(pco.threading, "Thread") as th:      # chưa cấu hình kho -> không gọi mạng
            app.check_update(True); th.assert_not_called(); self.assertIn("cấu hình", app.upd_status.cget("text"))

    def test_clean_summary_text(self):
        app = self.app; app.clean_bytes.clear(); app._setall(False); app.clean_vars["usr_temp"].set(True); app.update_clean_sum()
        self.assertIn("Đã chọn 1 mục", app.clean_total.cget("text")); self.assertIn("Quét", app.clean_total.cget("text"))   # chưa quét -> gợi ý bấm Quét
        app._set_clean_size("usr_temp", 5 * 1024 ** 2); self.assertIn("5.0 MB", app.clean_total.cget("text"))
        app.clean_bytes.clear()
        for k, _, _, d, _ in pco.TARGETS: app.clean_vars[k].set(d)

    def test_status_chip_follows_busy_state(self):
        self.assertTrue(self.idle()); self.assertIn("Sẵn sàng", self.app.status_chip.cget("text"))
        self.app.run_bg(lambda: None); self.assertIn("Đang xử lý", self.app.status_chip.cget("text"))
        self.assertTrue(self.idle()); self.assertIn("Sẵn sàng", self.app.status_chip.cget("text"))

    def test_all_pages_render(self):
        for name in self.app.pages: self.app.show(name); self.app.update()

    def test_no_tk_access_from_worker_threads(self):
        # mọi giá trị đọc từ widget phải được lấy ở luồng chính -> các job nền không được gọi .get() của Tk
        mk(P("lad", "Temp", "a"), 10); self.app.dry.set(True); self.app.start_clean(["usr_temp"], preview=False)
        self.assertTrue(self.idle()); self.assertTrue(os.path.exists(P("lad", "Temp", "a")))  # chế độ thử không xóa

    def test_preview_then_confirm_deletes_only_ticked(self):
        mk(P("lad", "Temp", "delete_me"), 100); mk(P("lad", "Temp", "keep_me"), 5000)
        self.app.start_clean(["usr_temp"], preview=True)
        self.assertTrue(self.pump(lambda: self.dialog() is not None)); dlg = self.dialog()
        self.assertEqual(len(dlg.rows), 2)
        for v, its in dlg.rows:
            if its[0]["path"].endswith("keep_me"): v.set(False)
        dlg.update_summary(); self.assertIn("1 mục", dlg.go.cget("text")); dlg.confirm()
        self.assertTrue(self.idle())
        self.assertEqual(os.listdir(TEMP), ["keep_me"]); self.assertEqual(self.app.cfg["history"][-1]["freed"], 100)

    def test_preview_cancel_deletes_nothing(self):
        mk(P("lad", "Temp", "a"), 10); self.app.start_clean(["usr_temp"], preview=True)
        self.assertTrue(self.pump(lambda: self.dialog() is not None)); self.dialog().destroy(); self.idle()
        self.assertEqual(os.listdir(TEMP), ["a"])

    def test_empty_preview_shows_no_dialog(self):
        self.app.start_clean(["usr_temp"], preview=True); self.assertTrue(self.idle()); self.assertIsNone(self.dialog())

    def test_whitelist_ui_roundtrip_and_effect(self):
        mk(P("lad", "Temp", "keep", "f"), 10); mk(P("lad", "Temp", "junk"), 10)
        self.app._add_wl(P("lad", "Temp", "keep")); self.assertEqual(len(self.app.cfg["whitelist"]), 1)
        self.app.start_clean(["usr_temp"], preview=False); self.assertTrue(self.idle())
        self.assertEqual(os.listdir(TEMP), ["keep"])
        self.app.remove_whitelist(self.app.cfg["whitelist"][0]); self.assertEqual(self.app.cfg["whitelist"], [])

    def test_apply_schedule_validates_and_builds_commands(self):
        calls = []
        with mock.patch.object(pco, "sh", side_effect=lambda c, *a, **k: (calls.append(c), (0, "OK"))[1]), mock.patch.object(pco, "is_admin", return_value=True):
            self.app.sch_mode.set("Hằng ngày"); self.app.sch_time.delete(0, "end"); self.app.sch_time.insert(0, "25:99")
            self.app.apply_schedule(); self.idle(); self.assertEqual(calls, [])
            self.app.sch_time.delete(0, "end"); self.app.sch_time.insert(0, "21:30")
            self.app.apply_schedule(); self.assertTrue(self.idle())
        flat = [" ".join(c) for c in calls if isinstance(c, list)]
        self.assertTrue(any("/Delete" in f for f in flat)); self.assertTrue(any("/Create" in f and "21:30" in f for f in flat))
        self.assertEqual(pco.load_config()["schedule"]["time"], "21:30")

    def test_auto_keys_and_lowdisk_persist(self):
        self.app.auto_vars["chrome"].set(False); self.app.save_auto_keys(); self.assertNotIn("chrome", pco.load_config()["auto_keys"])
        self.app.v_low.set(True); self.app.low_pct.delete(0, "end"); self.app.low_pct.insert(0, "15"); self.app.save_lowdisk()
        self.assertEqual(pco.load_config()["low_disk"], {"enabled": True, "percent": 15})

    def test_low_disk_watcher_triggers_auto_clean(self):
        mk(P("lad", "Temp", "a"), 10); self.app.cfg["low_disk"] = {"enabled": True, "percent": 99}; self.app.cfg["auto_keys"] = ["usr_temp"]
        self.app._last_low = 0
        low = types.SimpleNamespace(total=100, used=99, free=1)  # ổ đĩa còn 1% trống
        with mock.patch.object(self.app, "after"), mock.patch.object(pco.shutil, "disk_usage", return_value=low):
            self.app.watch_disk()
        self.assertGreater(self.app._last_low, 0)
        with mock.patch.object(self.app, "after"), mock.patch.object(pco.shutil, "disk_usage", return_value=low):
            self.app.watch_disk()  # trong thời gian chờ 6 giờ: không dọn lại
        self.assertTrue(self.idle()); self.assertEqual(os.listdir(TEMP), [])

    def test_close_to_tray_and_restore(self):
        fake = mock.Mock(); self.app.tray, self.app.tray_ok = fake, True; self.app.cfg["tray"] = True
        self.app.on_close(); self.app.update(); self.assertEqual(self.app.state(), "withdrawn"); fake.notify.assert_called()
        self.app.restore_window(); self.app.update(); self.assertEqual(self.app.state(), "normal")
        self.app.tray_ok = False

    def test_hw_cards_render_and_copy_export(self):
        S = hw.build_report(RAW); self.app.render_hardware(S); self.app.update()
        self.assertEqual(len(self.app.hw_frame.winfo_children()), len(S))
        self.app.hw_sections = S; self.app.hw_copy(); self.assertIn("Samsung SSD 980", self.app.clipboard_get())
        out = P("export.txt")
        with mock.patch.object(pco.filedialog, "asksaveasfilename", return_value=out): self.app.hw_export()
        with open(out, encoding="utf-8") as fh: self.assertIn("WDC WD10EZEX", fh.read())

    def test_hw_scan_async_and_error_isolated(self):
        self.assertTrue(self.pump(lambda: not self.app.hw_busy))      # chờ lần quét lúc khởi động xong
        with mock.patch.object(pco.hw, "collect", return_value=RAW):
            self.app.hw_scan(); self.assertTrue(self.app.hw_busy)
            self.app.hw_scan()                                          # bấm lại khi đang quét: bị bỏ qua, không chạy chồng
            self.assertTrue(self.pump(lambda: not self.app.hw_busy))
        self.assertGreater(len(self.app.hw_sections), 5)
        with mock.patch.object(pco.hw, "collect", side_effect=RuntimeError("boom")):
            self.app.hw_scan(); self.assertTrue(self.pump(lambda: not self.app.hw_busy))
        self.assertEqual(self.app.hw_sections[0]["title"], "Lỗi quét phần cứng")
        self.assertTrue(self.idle())                                    # lỗi quét phần cứng không làm kẹt các tác vụ khác

    def test_zz_close_without_tray_quits(self):
        self.app.cfg["tray"] = False; self.app.tray_ok = False; self.app.on_close()
        with self.assertRaises(Exception): self.app.winfo_exists() and self.app.update() or (_ for _ in ()).throw(Exception())


if __name__ == "__main__":
    try: unittest.main(verbosity=2)
    finally: shutil.rmtree(BASE, ignore_errors=True)
