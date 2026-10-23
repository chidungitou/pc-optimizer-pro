# -*- coding: utf-8 -*-
"""
PC Optimizer Pro - Dọn rác Windows & Tối ưu mạng (CustomTkinter)
Cài đặt:  pip install customtkinter psutil send2trash pystray pillow
Chạy:     python pc_optimizer.py   (nên chạy bằng quyền Administrator)
Tham số:  --tray (mở thu nhỏ vào khay)   --auto (dọn không giao diện, dùng cho lịch tự động)
"""
import os, sys, re, stat, glob, json, time, queue, random, shutil, socket, struct, ctypes, threading, subprocess, statistics
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

try:
    import winreg
except ImportError:  # không phải Windows (chỉ xảy ra khi chạy test)
    winreg = None
import customtkinter as ctk
import hardware_info as hw
import tkinter as tk
import webbrowser
from tkinter import messagebox, filedialog

try:
    import psutil
except ImportError:
    psutil = None
try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

APP_NAME = "PC Optimizer Pro"
APP_VERSION = "2.6"
try:
    from build_info import BUILD_TIME   # file này do build_exe.bat tạo mỗi lần build
except Exception:
    BUILD_TIME = "chạy từ mã nguồn"
try:
    from build_info import UPDATE_REPO   # "chủ-sở-hữu/tên-kho" trên GitHub; GitHub Actions tự điền, build tại máy đọc từ update_repo.txt
except Exception:
    UPDATE_REPO = ""
UPDATE_API = "https://api.github.com"
NO_WIN = 0x08000000
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "PCOptimizerPro"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "log.txt"
STARTUP_BACKUP = DATA_DIR / "startup_backup.json"

# ----------------------------------------------------------------- helpers
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def relaunch_admin():
    argv = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(f'"{a}"' for a in argv), None, 1)

def sh(cmd, timeout=600):
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="oem", errors="replace", timeout=timeout, creationflags=NO_WIN)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return 1, str(e)

def ps(script, timeout=600):
    return sh(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout)

def fmt(b):
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or u == "TB":
            return f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}"
        b /= 1024

def path_size(p):
    total = 0
    try:
        if os.path.isfile(p):
            return os.path.getsize(p)
        stack = [p]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
            except OSError:
                pass
    except OSError:
        pass
    return total

def expand(patterns):
    """Mở %BIẾN% và wildcard; loại đường dẫn trùng nhau (vd %TEMP% == %LOCALAPPDATA%\\Temp)."""
    out, seen = [], set()
    for pat in patterns:
        pat = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), pat).replace("\\", os.sep)
        for p in glob.glob(pat):
            n = os.path.normcase(os.path.abspath(p))
            if n not in seen: seen.add(n); out.append(p)
    return out

def _onerr(func, path, _exc):
    try:
        os.chmod(path, stat.S_IWRITE); func(path)
    except Exception:
        pass

def downloads_path():
    p = os.path.join(os.environ.get("USERPROFILE", ""), "Downloads")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            v, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            p = os.path.expandvars(v)
    except (OSError, AttributeError):
        pass
    return p

# ----------------------------------------------------------------- data
SYSDRIVE = os.environ.get("SystemDrive", "C:")
BR = r"%LOCALAPPDATA%"
TARGETS = [  # key, nhãn, patterns, mặc định, ghi chú
    ("sys_temp", "Temp Windows", [r"%WINDIR%\Temp"], True, "Cần Admin để xóa hết"),
    ("usr_temp", "Temp người dùng", [r"%TEMP%", BR + r"\Temp"], True, ""),
    ("prefetch", "Prefetch", [r"%WINDIR%\Prefetch"], False, "Windows tạo lại, lần mở app đầu hơi chậm"),
    ("wu", "Cache Windows Update", [r"%WINDIR%\SoftwareDistribution\Download"], True, "Tự dừng/bật lại dịch vụ"),
    ("dumps", "Crash dump & báo lỗi", [BR + r"\CrashDumps", r"%WINDIR%\Minidump", r"%WINDIR%\MEMORY.DMP",
                                      r"%PROGRAMDATA%\Microsoft\Windows\WER\ReportQueue",
                                      r"%PROGRAMDATA%\Microsoft\Windows\WER\ReportArchive"], True, ""),
    ("thumb", "Thumbnail & icon cache", [BR + r"\Microsoft\Windows\Explorer\thumbcache_*.db",
                                         BR + r"\Microsoft\Windows\Explorer\iconcache_*.db"], True, ""),
    ("chrome", "Cache Chrome", [BR + r"\Google\Chrome\User Data\*\Cache", BR + r"\Google\Chrome\User Data\*\Code Cache",
                                BR + r"\Google\Chrome\User Data\*\GPUCache"], True, "Nên đóng Chrome trước"),
    ("edge", "Cache Edge", [BR + r"\Microsoft\Edge\User Data\*\Cache", BR + r"\Microsoft\Edge\User Data\*\Code Cache",
                            BR + r"\Microsoft\Edge\User Data\*\GPUCache"], True, "Nên đóng Edge trước"),
    ("firefox", "Cache Firefox", [BR + r"\Mozilla\Firefox\Profiles\*\cache2"], True, "Nên đóng Firefox trước"),
    ("brave", "Cache Brave", [BR + r"\BraveSoftware\Brave-Browser\User Data\*\Cache",
                              BR + r"\BraveSoftware\Brave-Browser\User Data\*\Code Cache"], True, ""),
    ("shader", "Shader cache (DirectX/NVIDIA/AMD)", [BR + r"\D3DSCache", BR + r"\NVIDIA\DXCache", BR + r"\NVIDIA\GLCache",
                                                    BR + r"\AMD\DxCache"], False, "Game có thể biên dịch lại shader"),
    ("logs", "Log hệ thống (CBS/DISM)", [r"%WINDIR%\Logs\CBS\*.log", r"%WINDIR%\Logs\DISM\*.log"], False, "Cần Admin"),
    ("recycle", "Thùng rác", [], True, ""),
    ("downloads", "Thư mục Downloads", [], False, "⚠ XÓA SẠCH toàn bộ, có hỏi xác nhận"),
]

DNS_LIST = [  # tên, ip1, ip2, ghi chú
    ("Cloudflare", "1.1.1.1", "1.0.0.1", "Nhanh + riêng tư"),
    ("Cloudflare Malware", "1.1.1.2", "1.0.0.2", "Chặn malware"),
    ("Cloudflare Family", "1.1.1.3", "1.0.0.3", "Chặn malware + nội dung người lớn"),
    ("Google", "8.8.8.8", "8.8.4.4", "Ổn định, phổ biến"),
    ("Quad9", "9.9.9.9", "149.112.112.112", "Bảo mật cao, chặn malware"),
    ("OpenDNS", "208.67.222.222", "208.67.220.220", "Lọc nội dung"),
    ("AdGuard DNS", "94.140.14.14", "94.140.15.15", "Chặn quảng cáo"),
    ("ControlD", "76.76.2.0", "76.76.10.0", "Linh hoạt, chặn QC"),
    ("AliDNS", "223.5.5.5", "223.6.6.6", "Máy chủ Trung Quốc"),
    ("DNSPod", "119.29.29.29", "182.254.116.116", "Máy chủ Trung Quốc"),
    ("VNPT", "203.113.131.1", "203.113.131.2", "Nhà mạng VNPT"),
    ("FPT", "210.245.24.1", "210.245.24.20", "Nhà mạng FPT"),
    ("Verisign", "64.6.64.6", "64.6.65.6", "Ổn định, không lọc"),
]

DEFAULT_KILL = """chrome msedge firefox opera brave telegram discord teams ms-teams zoom slack whatsapp skype
binance metatrader ollama docker androidstudio code winword excel powerpnt outlook acrobat soffice onedrive dropbox
googledrivefs winrar 7zfm idman utorrent qbittorrent vlc potplayermini64 spotify pixpin ccleaner mstsc""".split()

PROTECTED = {n.lower() for n in """system registry smss csrss wininit winlogon services lsass svchost explorer dwm
fontdrvhost sihost taskhostw ctfmon searchhost startmenuexperiencehost shellexperiencehost runtimebroker python pythonw
conhost audiodg securityhealthsystray textinputhost applicationframehost""".split()}

def dns_query_ms(server, domain, timeout=1.5, port=53):
    qid = random.randint(0, 65535)
    q = b"".join(bytes([len(x)]) + x.encode() for x in domain.split(".")) + b"\x00"
    pkt = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    try:
        t = time.perf_counter(); s.sendto(pkt, (server, port)); data, _ = s.recvfrom(512)
        return (time.perf_counter() - t) * 1000 if data[:2] == pkt[:2] else None
    except OSError:
        return None
    finally:
        s.close()

def list_startup():
    out = []
    if winreg is None: return out
    for hive, tag in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        sub = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(hive, sub) as k:
                i = 0
                while True:
                    try:
                        n, v, _ = winreg.EnumValue(k, i); out.append((tag, n, v)); i += 1
                    except OSError:
                        break
        except OSError:
            pass
    return out

HIVES = {"HKCU": getattr(winreg, "HKEY_CURRENT_USER", 1), "HKLM": getattr(winreg, "HKEY_LOCAL_MACHINE", 2)}
RUN_SUB = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ----------------------------------------------------------------- giao diện: bảng màu & thành phần dùng chung
FONT_UI, FONT_MONO = "Segoe UI", "Consolas"

class C:
    """Bảng màu dùng chung. Dạng (sáng, tối) sẽ tự đổi theo chế độ giao diện."""
    ACCENT, ACCENT_H = "#2f81f7", "#1f6feb"
    ACCENT_TXT = ("#0969da", "#58a6ff")
    OK, WARN, BAD = "#3fb950", "#d29922", "#f85149"
    BG = ("#eef1f4", "#0d1117")
    SIDEBAR = ("#e2e6ea", "#010409")
    CARD = ("#ffffff", "#161b22")
    CARD2 = ("#f3f5f7", "#1f252d")
    BORDER = ("#d0d7de", "#30363d")
    TEXT = ("#1f2328", "#e6edf3")
    MUTED = ("#59636e", "#8b949e")
    NAV_ON = ("#d0d7de", "#1c2433")
    NAV_HOVER = ("#d8dde2", "#161b22")
    DANGER_BG = ("#ffebe9", "#3d1418")

PILL = {  # mức -> (màu nền, màu chữ)
    "ok": (("#dafbe1", "#12361f"), ("#1a7f37", "#3fb950")),
    "warn": (("#fff8c5", "#3b2e0a"), ("#9a6700", "#d29922")),
    "bad": (("#ffebe9", "#4c1a1a"), ("#cf222e", "#f85149")),
    "info": (("#ddf4ff", "#0c2a52"), ("#0969da", "#58a6ff")),
    "unknown": (("#eaeef2", "#30363d"), ("#59636e", "#8b949e")),
}
BTN_STYLES = {
    "primary": dict(fg_color=C.ACCENT, hover_color=C.ACCENT_H, text_color="white"),
    "secondary": dict(fg_color=("#e1e4e8", "#2d333b"), hover_color=("#d0d7de", "#3b434d"), text_color=C.TEXT),
    "danger": dict(fg_color="#da3633", hover_color="#b62324", text_color="white"),
    "success": dict(fg_color="#238636", hover_color="#1a6b2b", text_color="white"),
    "warn": dict(fg_color="#bf6a02", hover_color="#9a5502", text_color="white"),
    "ghost": dict(fg_color="transparent", hover_color=C.NAV_HOVER, text_color=C.MUTED),
}
LEGACY_BTN = {"gray40": "secondary", "#c0392b": "danger", "#7f2a22": "danger", "#2e8b57": "success", "#d9822b": "warn"}
PAGE_INFO = {
    "dash": ("Tổng quan", "Tình trạng máy và cấu hình phần cứng"),
    "clean": ("Dọn rác", "Quét và xóa file tạm, cache, log không cần thiết"),
    "apps": ("Ứng dụng nền", "Đóng bớt chương trình đang chiếm RAM"),
    "net": ("Mạng & DNS", "Làm mới kết nối và chọn DNS nhanh nhất"),
    "tools": ("Công cụ", "Sửa lỗi Windows, tìm file lớn, quản lý chương trình khởi động"),
    "settings": ("Cài đặt & Lịch", "Dọn tự động, danh sách bảo vệ và lịch sử"),
}
LOG_TAGS = (("💡", "info"), ("✔", "ok"), ("💾", "ok"), ("📋", "ok"), ("🏆", "ok"), ("✖", "bad"), ("❌", "bad"), ("⚠", "warn"), ("[thử]", "warn"),
            ("▶", "info"), ("🔍", "info"), ("🧹", "info"), ("⏱", "info"), ("ℹ", "info"))

def log_tag(msg):
    """Chọn màu cho dòng nhật ký theo ký hiệu đầu dòng (✔ xanh, ✖ đỏ, ⚠ vàng, ▶ xanh dương)."""
    m = str(msg).lstrip()
    return next((t for s, t in LOG_TAGS if m.startswith(s)), None)

def lvl_color(pct):
    """Màu theo mức sử dụng: dưới 70% xanh, dưới 90% vàng, còn lại đỏ."""
    return C.OK if pct < 70 else C.WARN if pct < 90 else C.BAD

def short_path(p, n=90):
    """Rút gọn đường dẫn quá dài bằng dấu … ở giữa (giữ nguyên phần đầu và phần cuối)."""
    p = str(p)
    return p if len(p) <= n else p[: n // 2 - 2] + " … " + p[-(n // 2 - 2):]

class Tooltip:
    """Chú thích nổi: rê chuột ~0,4 giây thì hiện khung giải thích, rời chuột/bấm/đóng cửa sổ thì ẩn."""
    DELAY = 400

    def __init__(self, widget, text):
        self.w, self.text, self.tw, self._show_job, self._hide_job = widget, text, None, None, None
        for ev, fn in (("<Enter>", self._enter), ("<Leave>", self._leave), ("<ButtonPress>", self._hide), ("<Destroy>", self._hide)):
            try: widget.bind(ev, fn, add="+")
            except Exception: pass

    def _enter(self, _e=None):
        if self._hide_job:                       # chuột chỉ chuyển giữa các phần bên trong cùng một nút -> giữ nguyên, không nhấp nháy
            try: self.w.after_cancel(self._hide_job)
            except Exception: pass
            self._hide_job = None
        if self.tw is None and self._show_job is None:
            try: self._show_job = self.w.after(self.DELAY, self._show)
            except Exception: pass

    def _leave(self, _e=None):
        if self._show_job:
            try: self.w.after_cancel(self._show_job)
            except Exception: pass
            self._show_job = None
        if self.tw is not None and self._hide_job is None:
            try: self._hide_job = self.w.after(90, self._hide)
            except Exception: self._hide()

    def _show(self):
        self._show_job = None
        if self.tw is not None or not self.text: return
        try:
            px, py = self.w.winfo_pointerxy()
            tw = tk.Toplevel(self.w); tw.wm_overrideredirect(True)
            try: tw.wm_attributes("-topmost", True)
            except Exception: pass
            tk.Label(tw, text=self.text, justify="left", wraplength=360, bg="#1f2937", fg="#f3f4f6", bd=0, padx=11, pady=8,
                     highlightthickness=1, highlightbackground="#4b5563", font=(FONT_UI, 10)).pack()
            tw.update_idletasks()
            w, h, sw, sh = tw.winfo_reqwidth(), tw.winfo_reqheight(), tw.winfo_screenwidth(), tw.winfo_screenheight()
            x = max(0, min(px + 14, sw - w - 8)); y = py + 22
            if y + h > sh - 8: y = max(0, py - h - 14)
            tw.wm_geometry(f"+{x}+{y}"); self.tw = tw
        except Exception:
            self.tw = None

    def _hide(self, _e=None):
        for j in (self._show_job, self._hide_job):
            if j:
                try: self.w.after_cancel(j)
                except Exception: pass
        self._show_job = self._hide_job = None
        if self.tw is not None:
            try: self.tw.destroy()
            except Exception: pass
            self.tw = None

def tip(widget, text):
    """Gắn chú thích cho widget rồi trả lại chính widget (dùng được trong chuỗi .pack()/.grid())."""
    if text:
        try: Tooltip(widget, text)
        except Exception: pass
    return widget

# Giải thích cho các nút, tra theo chữ trên nút (nút nào cần giải thích riêng thì truyền hint=...)
HINTS = {
    # --- Tổng quan
    "🚀 Dọn nhanh 1 chạm": "Dọn ngay các mục mặc định an toàn (file tạm, cache Windows Update, cache trình duyệt, crash dump, thumbnail...). "
                           "Không bao giờ đụng tới thư mục Downloads.\nNếu bật “Luôn xem trước” trong Cài đặt, app sẽ hiện danh sách để bạn duyệt trước khi xóa.",
    "🌐 Làm mới mạng": "Xóa bộ nhớ đệm DNS rồi đăng ký lại DNS (ipconfig /flushdns + /registerdns). Dùng khi vào web chập chờn. An toàn, không ngắt mạng.",
    "🛡 Tạo điểm khôi phục": "Tạo một điểm khôi phục hệ thống (System Restore) để có thể quay lại nếu có sự cố.\n"
                            "Cần quyền Admin và đã bật “Bảo vệ hệ thống” cho ổ C. Mặc định Windows chỉ cho tạo 1 điểm mỗi 24 giờ.",
    "📂 Mở thư mục log": "Mở thư mục chứa nhật ký hoạt động, cấu hình và lịch sử dọn của app.",
    "⬇ Tải về": "Mở trình duyệt để tải file .exe của bản mới từ GitHub. Tải xong thoát app rồi chép file mới đè lên file cũ.",
    "📄 Xem thay đổi": "Mở trang phát hành trên GitHub để đọc ghi chú: bản mới có gì thay đổi.",
    "Bỏ qua bản này": "Ẩn thông báo của bản này. Khi có bản mới hơn, app vẫn báo lại.",
    "🔄 Kiểm tra ngay": "Hỏi GitHub xem có bản mới không (cần Internet). Kết quả hiện ngay bên cạnh.",
    "Mở thư mục log": "Mở thư mục chứa nhật ký hoạt động (log.txt), cấu hình và lịch sử dọn của app.",
    "Chạy lại với quyền Admin": "Khởi động lại app với quyền Administrator để dọn được Temp hệ thống, Prefetch, dùng lệnh mạng và SFC/DISM đầy đủ. Windows sẽ hỏi xác nhận (UAC).",
    "↻ Quét lại": "Quét lại cấu hình phần cứng (CPU, RAM, ổ đĩa, GPU, pin, mạng...) và cập nhật các thẻ bên dưới.",
    "💾 Xuất .txt": "Lưu toàn bộ báo cáo phần cứng ra file .txt để gửi cho thợ sửa máy hoặc lưu lại.",
    "📋 Sao chép": "Sao chép báo cáo phần cứng vào bộ nhớ tạm để dán (Ctrl+V) vào tin nhắn, email hoặc diễn đàn.",
    # --- khung chính
    "⏹ Hủy tác vụ": "Yêu cầu dừng tác vụ đang chạy. Dọn rác sẽ dừng sau mục hiện tại; lệnh hệ thống như SFC/DISM không dừng ngay được.",
    "Xóa": "Xóa nội dung khung nhật ký trên màn hình (file log.txt đã lưu không bị xóa).",
    "Ẩn": "Ẩn/hiện khung nhật ký để có thêm chỗ cho nội dung chính.",
    # --- Dọn rác
    "🔍 Quét dung lượng": "Chỉ tính xem mỗi mục đang chiếm bao nhiêu dung lượng. KHÔNG xóa bất cứ thứ gì.",
    "👁 Xem trước & dọn": "Liệt kê từng file/thư mục sẽ bị xóa để bạn bỏ tick mục muốn giữ, rồi mới xóa. Cách an toàn nhất.",
    "🧹 Dọn ngay": "Xóa ngay các mục đã tick, bỏ qua bước xem trước. Riêng Downloads vẫn luôn hỏi xác nhận.",
    "Chọn tất cả": "Tick tất cả các mục trong danh sách.",
    "Bỏ chọn": "Bỏ tick tất cả các mục trong danh sách.",
    "Bỏ chọn tất cả": "Bỏ tick toàn bộ để chọn lại từng mục.",
    "Hủy": "Đóng hộp thoại, không xóa gì cả.",
    # --- Ứng dụng nền
    "↻ Làm mới": "Tải lại danh sách ứng dụng đang chạy và dung lượng RAM từng cái đang dùng.",
    "DS mặc định": "Tick sẵn các ứng dụng thường chiếm RAM (trình duyệt, chat, họp, Office, trình tải...). Bạn vẫn xem lại và bỏ tick trước khi đóng.",
    "Đóng nhẹ": "Gửi yêu cầu đóng cửa sổ như khi bấm ✕: ứng dụng có thể hỏi lưu file rồi tự thoát. An toàn.",
    "Ép đóng": "Buộc tắt ngay (taskkill /f). Dữ liệu chưa lưu sẽ MẤT. Chỉ dùng cho ứng dụng bị treo.",
    # --- Mạng
    "Reset Winsock & IP": "Đặt lại Winsock và ngăn xếp TCP/IP (netsh winsock reset, netsh int ip reset). Sửa lỗi “có kết nối nhưng không vào được mạng” do cấu hình hỏng.\n"
                          "Phải khởi động lại máy sau đó; cấu hình IP có thể về mặc định. Có hỏi xác nhận.",
    "Flush DNS": "Xóa bộ nhớ đệm DNS của Windows. Dùng khi một trang web không vào được hoặc vào sai sau khi đổi DNS. An toàn.",
    "Tối ưu TCP": "Đặt các thông số TCP phổ biến: autotuning=normal, RSS=bật, ECN=tắt. Giúp kết nối ổn định; hiệu quả thường nhỏ, không làm tăng tốc độ gói cước.",
    "Release/Renew IP": "Trả địa chỉ IP hiện tại rồi xin IP mới từ router. Mạng sẽ ngắt vài giây. Dùng khi báo xung đột IP hoặc không nhận được IP.",
    "Xem DNS hiện tại": "Hiện DNS đang dùng của từng card mạng trong khung nhật ký. Chỉ đọc, không thay đổi gì.",
    "⏱ Đo tất cả DNS": "Gửi truy vấn DNS thật tới từng nhà cung cấp rồi xếp hạng theo tốc độ trung vị (mất khoảng 20–40 giây). Chỉ đo, chưa đổi gì; "
                      "nhà cung cấp nhanh nhất sẽ được chọn sẵn ở ô bên cạnh.",
    "Gán DNS tĩnh": "Đặt DNS của nhà cung cấp đã chọn cho card mạng đã chọn. Cần quyền Admin. Muốn quay lại bấm “Về DNS tự động”.",
    "Về DNS tự động (DHCP)": "Trả DNS của card mạng về tự động theo router/nhà mạng.",
    # --- Công cụ
    "SFC /scannow": "Quét và tự sửa file hệ thống Windows bị hỏng (sfc /scannow). Mất khoảng 5–15 phút, cần Admin. Xem kết quả ở khung nhật ký.",
    "DISM RestoreHealth": "Sửa kho ảnh hệ thống Windows bằng file gốc tải từ Windows Update (cần Internet, 10–30 phút). Nên chạy trước SFC nếu SFC báo không sửa được.",
    "Dọn WinSxS": "Dọn các bản cập nhật cũ trong WinSxS (DISM StartComponentCleanup). Giải phóng vài trăm MB đến vài GB; sau đó không gỡ lùi được các bản cập nhật cũ đó.",
    "Disk Cleanup": "Mở công cụ Dọn dẹp ổ đĩa có sẵn của Windows (cleanmgr) để bạn tự chọn thêm các mục khác.",
    "High Performance": "Chuyển gói nguồn sang Hiệu suất cao: máy chạy mạnh hơn nhưng tốn điện, nóng hơn; laptop hao pin nhanh. Đổi lại trong Power Options.",
    "Tắt Hibernate (giải phóng hiberfil)": "Tắt chế độ ngủ đông và xóa file hiberfil.sys (thường vài GB). Sẽ mất tính năng ngủ đông và khởi động nhanh (Fast Startup).",
    "Chọn...": "Chọn thư mục cần tìm file lớn.",
    "Quét": "Liệt kê các file lớn nhất trong thư mục đã chọn (kể cả thư mục con). Chỉ đọc, không xóa gì.",
    "Xóa mục chọn": "Xóa các file đã tick: chuyển vào Thùng rác nếu đã cài send2trash, nếu không sẽ xóa vĩnh viễn. Có hỏi xác nhận.",
    "Mở vị trí": "Mở File Explorer tại file đầu tiên bạn đã tick.",
    "Gỡ mục chọn": "Gỡ chương trình khỏi danh sách tự chạy khi bật máy (Registry Run). Chương trình vẫn còn, chỉ không tự mở nữa. Có sao lưu để khôi phục.",
    "Khôi phục đã gỡ": "Đưa lại các mục khởi động đã gỡ trước đó từ bản sao lưu.",
    # --- Cài đặt
    "+ Thêm thư mục bảo vệ...": "Chọn một thư mục/file mà app không bao giờ được xóa, kể cả khi nó nằm trong khu vực dọn rác.",
    "✕": "Bỏ mục này khỏi danh sách bảo vệ (sau đó app có thể dọn nó).",
    "Áp dụng lịch": "Tạo/cập nhật lịch dọn tự động trong Windows Task Scheduler theo lựa chọn phía trên. Cần chạy app bằng Admin.",
    "Gỡ lịch": "Xóa lịch dọn tự động khỏi Windows Task Scheduler.",
    "▶ Chạy dọn tự động ngay bây giờ": "Dọn ngay bộ mục được tick phía trên mà không cần chờ đến giờ hẹn, không hỏi xem trước. Downloads luôn bị loại trừ.",
}
NAV_HINT = {
    "dash": "Xem CPU/RAM/ổ đĩa theo thời gian thực, cấu hình phần cứng và các thao tác nhanh.",
    "clean": "Quét và xóa file tạm, cache, log... để lấy lại dung lượng ổ đĩa.",
    "apps": "Xem ứng dụng nào đang chiếm RAM và đóng bớt những cái không cần.",
    "net": "Sửa lỗi mạng, làm mới DNS và tìm DNS nhanh nhất cho máy bạn.",
    "tools": "Sửa file hệ thống, tìm file dung lượng lớn, quản lý chương trình khởi động cùng Windows.",
    "settings": "Dọn tự động theo lịch, danh sách bảo vệ, tùy chọn app và lịch sử dọn.",
}
TARGET_HELP = {
    "sys_temp": "File tạm do Windows và các dịch vụ hệ thống tạo ra (thư mục Temp của Windows). An toàn để xóa; file đang dùng tự được bỏ qua. Cần Admin để xóa hết.",
    "usr_temp": "File tạm của bạn và các chương trình (thư mục %TEMP%). An toàn để xóa; file đang mở sẽ được bỏ qua.",
    "prefetch": "Dữ liệu giúp Windows mở ứng dụng nhanh hơn. Xóa được, Windows tự tạo lại, nhưng lần mở ứng dụng đầu tiên sau đó hơi chậm. Mặc định tắt.",
    "wu": "File cập nhật Windows đã tải về (SoftwareDistribution/Download). Xóa an toàn; app tự dừng và bật lại dịch vụ Windows Update, bản cập nhật dang dở sẽ tải lại.",
    "dumps": "File ghi lại khi ứng dụng/Windows bị lỗi (crash dump, báo cáo lỗi). Chỉ để chẩn đoán, có thể chiếm nhiều GB. Xóa an toàn nếu bạn không đang điều tra lỗi.",
    "thumb": "Bộ nhớ ảnh thu nhỏ và biểu tượng của File Explorer. Windows tự tạo lại; lần mở thư mục ảnh đầu tiên có thể hơi chậm.",
    "chrome": "Cache trang web của Chrome (Cache, Code Cache, GPUCache). KHÔNG xóa mật khẩu, cookie hay lịch sử. Nên đóng Chrome trước; trang web mở lần sau có thể tải chậm hơn chút.",
    "edge": "Cache trang web của Edge. KHÔNG xóa mật khẩu, cookie hay lịch sử. Nên đóng Edge trước.",
    "firefox": "Cache trang web của Firefox. KHÔNG xóa mật khẩu, cookie hay lịch sử. Nên đóng Firefox trước.",
    "brave": "Cache trang web của Brave. KHÔNG xóa mật khẩu, cookie hay lịch sử.",
    "shader": "Cache shader của DirectX/NVIDIA/AMD. Game có thể bị giật nhẹ ở lần chạy đầu vì phải biên dịch lại shader. Mặc định tắt.",
    "logs": "Các file log của Windows (CBS/DISM...). Chỉ dùng để chẩn đoán lỗi cập nhật. Cần Admin.",
    "recycle": "Làm trống Thùng rác. File trong đó sẽ mất vĩnh viễn.",
    "downloads": "⚠ XÓA SẠCH toàn bộ thư mục Downloads, KHÔNG khôi phục được. Luôn hỏi xác nhận và không bao giờ nằm trong dọn tự động.",
}
SWITCH_HELP = {
    "Tự kiểm tra bản mới (tối đa mỗi 24 giờ)": "Khi mở app (và mỗi 24 giờ nếu app chạy nền trong khay), tự hỏi GitHub xem có bản mới không. Chỉ đọc thông tin, không tự tải hay cài gì.",
    "Luôn xem trước danh sách trước khi dọn": "Bật: “Dọn nhanh 1 chạm” sẽ hiện danh sách xem trước để bạn duyệt rồi mới xóa. Tắt: xóa ngay không hỏi.",
    "Bấm ✕ thì thu nhỏ vào khay hệ thống (chạy nền)": "Bật: bấm ✕ chỉ thu nhỏ app vào khay cạnh đồng hồ và vẫn chạy nền (dọn theo lịch, theo dõi ổ đĩa). Thoát hẳn: chuột phải biểu tượng khay > Thoát.",
    "Hiện thông báo khi dọn xong": "Hiện bong bóng thông báo ở khay hệ thống kèm dung lượng đã giải phóng, kể cả khi dọn tự động.",
    "Khởi động cùng Windows (chạy nền trong khay)": "Tạo tác vụ trong Task Scheduler để app tự chạy ẩn trong khay mỗi khi bạn đăng nhập Windows. Cần chạy bằng Admin.",
}

def fnt(size=13, weight="normal", family=None, **kw):
    return ctk.CTkFont(family=family or FONT_UI, size=size, weight=weight, **kw)

def btn(parent, text, cmd, kind=None, hint=None, **kw):
    """Nút bấm có kiểu: primary / secondary / danger / success / warn / ghost (mã màu cũ được tự quy đổi).
    Tự gắn chú thích: lấy theo `hint` nếu có, không thì tra HINTS theo chữ trên nút."""
    if kind is None:
        fg = kw.get("fg_color")
        kind = "primary" if fg is None else LEGACY_BTN.get(fg)
    if kind:
        kw.update(BTN_STYLES[kind])
    kw.setdefault("corner_radius", 8); kw.setdefault("height", 34); kw.setdefault("font", fnt(13))
    b = ctk.CTkButton(parent, text=text, command=cmd, **kw)
    tip(b, hint or HINTS.get(text))
    return b

def card(parent, **kw):
    """Khung thẻ bo góc có viền mảnh."""
    kw.setdefault("fg_color", C.CARD); kw.setdefault("corner_radius", 12)
    kw.setdefault("border_width", 1); kw.setdefault("border_color", C.BORDER)
    return ctk.CTkFrame(parent, **kw)

def pill(parent, text, level="ok", **kw):
    """Nhãn trạng thái dạng viên thuốc (xanh/vàng/đỏ/xanh dương/xám)."""
    bg, fg = PILL.get(level, PILL["unknown"])
    return ctk.CTkLabel(parent, text=f"  {text}  ", fg_color=bg, text_color=fg, corner_radius=10, height=22, font=fnt(11, "bold"), **kw)

def set_pill(w, text, level):
    bg, fg = PILL.get(level, PILL["unknown"]); w.configure(text=f"  {text}  ", fg_color=bg, text_color=fg)

def apply_theme():
    """Đồng bộ màu mặc định của customtkinter (ô tick, công tắc, thanh tiến trình...) với bảng màu C."""
    try:
        T = ctk.ThemeManager.theme
        acc, acc_h = [C.ACCENT, C.ACCENT], [C.ACCENT_H, C.ACCENT_H]
        upd = {"CTkCheckBox": dict(fg_color=acc, hover_color=acc_h, corner_radius=6, border_width=2, border_color=["#8c959f", "#6e7681"]),
               "CTkSwitch": dict(progress_color=acc),
               "CTkProgressBar": dict(progress_color=acc, fg_color=["#d0d7de", "#30363d"]),
               "CTkOptionMenu": dict(fg_color=["#e1e4e8", "#2d333b"], button_color=["#d0d7de", "#3b434d"],
                                     button_hover_color=["#c0c7cf", "#4a535f"], text_color=list(C.TEXT), corner_radius=8),
               "CTkComboBox": dict(fg_color=["#ffffff", "#0d1117"], border_color=list(C.BORDER), button_color=["#d0d7de", "#3b434d"],
                                   button_hover_color=["#c0c7cf", "#4a535f"], corner_radius=8),
               "CTkEntry": dict(fg_color=["#ffffff", "#0d1117"], border_color=list(C.BORDER), corner_radius=8),
               "CTkScrollbar": dict(button_color=["#c0c7cf", "#3b434d"], button_hover_color=["#a8b0b9", "#4a535f"])}
        for name, kv in upd.items():
            if name in T: T[name].update(kv)
    except Exception:
        pass

def set_window_icon(win):
    """Đặt biểu tượng cửa sổ. customtkinter tự ghi đè icon sau ~200ms nên phải đặt trễ hơn."""
    def apply():
        try:
            ico = DATA_DIR / "app.ico"
            if not ico.exists():
                make_icon_image(256).save(str(ico), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
            win.iconbitmap(str(ico))
        except Exception:
            pass
    try: win.after(300, apply)
    except Exception: pass

# ----------------------------------------------------------------- v2: cấu hình / lõi dọn dẹp / lịch / khay
CONFIG_FILE = DATA_DIR / "config.json"
TASK_NAME = "PCOptimizerPro_AutoClean"
STARTUP_TASK = "PCOptimizerPro_Tray"
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MODES = {"Tắt": "off", "Hằng ngày": "daily", "Hằng tuần": "weekly", "Khi đăng nhập": "logon"}
DEFAULT_CFG = {
    "preview": True, "tray": True, "notify": True, "whitelist": [],
    "auto_keys": ["sys_temp", "usr_temp", "wu", "dumps", "thumb", "chrome", "edge", "firefox", "brave", "recycle"],
    "schedule": {"mode": "off", "time": "21:00", "day": "SUN"},
    "low_disk": {"enabled": False, "percent": 10},
    "history": [],
    "update": {"auto": True, "last_check": 0, "skipped": "", "notified": ""},
}
_MUTEX = None

def parse_version(s):
    """'v2.6' -> (2, 6, 0, 0); '2.10.1-beta' -> (2, 10, 1, 0). Chuỗi lạ -> (0, 0, 0, 0). Để so sánh số học (2.10 > 2.9)."""
    nums = [int(n) for n in re.findall(r"\d+", re.split(r"[-+]", str(s).strip().lstrip("vV"), 1)[0])][:4]
    return tuple(nums + [0] * (4 - len(nums)))

def is_newer(remote, local=None):
    return parse_version(remote) > parse_version(APP_VERSION if local is None else local)

def pick_release(data, repo=""):
    """Rút thông tin cần dùng từ JSON 'latest release' của GitHub (ưu tiên file .exe có tên PCOptimizer...)."""
    tag = str(data.get("tag_name") or "").strip()
    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
    exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
    exe = next((a for a in exes if "pcoptimizer" in str(a.get("name", "")).lower()), exes[0] if exes else None) or {}
    return {"tag": tag, "version": tag.lstrip("vV"),
            "page": data.get("html_url") or f"https://github.com/{repo}/releases",
            "notes": str(data.get("body") or "").strip(), "asset_url": exe.get("browser_download_url") or "",
            "asset_name": exe.get("name") or "", "size": int(exe.get("size") or 0), "published": str(data.get("published_at") or "")[:10]}

def fetch_latest_release(repo, api=UPDATE_API, timeout=8):
    """Hỏi GitHub bản phát hành mới nhất (không cần token). Trả (thông tin, None) hoặc (None, thông báo lỗi tiếng Việt)."""
    import urllib.request, urllib.error
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        return None, "Chưa cấu hình kho GitHub (dạng chủ-sở-hữu/tên-kho). Xem HUONG_DAN_GITHUB.txt."
    req = urllib.request.Request(f"{api}/repos/{repo}/releases/latest",
                                 headers={"User-Agent": f"PCOptimizerPro/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if "127.0.0.1" in api else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404: return None, "Không thấy bản phát hành nào: kho chưa có Release nào, hoặc kho đang để Private (phải để Public)."
        if e.code in (403, 429): return None, "GitHub tạm giới hạn số lần kiểm tra. Thử lại sau ít phút."
        return None, f"GitHub trả lỗi {e.code}."
    except Exception:
        return None, "Không kết nối được tới GitHub. Kiểm tra mạng rồi thử lại."
    info = pick_release(data if isinstance(data, dict) else {}, repo)
    return (info, None) if info["tag"] else (None, "Phản hồi từ GitHub không hợp lệ.")

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    try:
        data = json.loads(CONFIG_FILE.read_text("utf-8"))
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict): cfg[k].update(v)
            else: cfg[k] = v
    except Exception:
        pass
    return cfg

def save_config(cfg):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, CONFIG_FILE)

def add_history(cfg, freed, source, fails=0):
    h = cfg.get("history", []) + [{"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "freed": int(freed), "src": source, "fails": fails}]
    cfg["history"] = h[-50:]; save_config(cfg)

def append_log(line, limit=1_000_000, keep=200_000):
    """Ghi log; khi file vượt ~1 MB thì chỉ giữ lại ~200 KB cuối."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > limit:
            data = LOG_FILE.read_bytes()[-keep:]; LOG_FILE.write_bytes(data[data.find(b"\n") + 1:])
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(line)
    except OSError:
        pass

def valid_time(t):
    return re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t or "") is not None

def _norm(p):
    return os.path.normcase(os.path.abspath(p))

def is_protected(path, whitelist=()):
    """True nếu mục nằm trong/chứa mục được bảo vệ (whitelist, dữ liệu của app, thư mục giải nén PyInstaller)."""
    n = _norm(path)
    guards = [str(DATA_DIR)] + ([sys._MEIPASS] if hasattr(sys, "_MEIPASS") else []) + list(whitelist)
    for g in guards:
        g = _norm(g)
        if n == g or n.startswith(g + os.sep) or g.startswith(n + os.sep):
            return True
    return False

def target_paths(key):
    if key == "downloads": return [downloads_path()]
    if key == "recycle": return [SYSDRIVE + "\\$Recycle.Bin"]
    for k, _, pats, _, _ in TARGETS:
        if k == key: return expand(pats)
    return []

def collect_items(keys, whitelist=(), cancel=None):
    """Lập danh sách mục sẽ xóa: mỗi mục = 1 file/thư mục con trực tiếp của thư mục đích (đã khử trùng lặp)."""
    items, seen = [], set()
    for key in keys:
        if key == "recycle":
            items.append({"key": key, "path": None, "size": path_size(SYSDRIVE + "\\$Recycle.Bin")}); continue
        for base in target_paths(key):
            if cancel is not None and cancel.is_set(): return items
            if os.path.isfile(base): entries = [base]
            else:
                try: entries = [e.path for e in os.scandir(base)]
                except OSError: continue
            for p in entries:
                n = _norm(p)
                if n in seen: continue
                seen.add(n)
                if is_protected(p, whitelist): continue
                items.append({"key": key, "path": p, "size": path_size(p)})
    return items

def delete_entry(path, dry=False):
    """Xóa 1 file/thư mục (thử lại 3 lần). Trả về (bytes_giải_phóng, số_lỗi)."""
    s = path_size(path)
    if dry: return s, 0
    isdir = os.path.isdir(path) and not os.path.islink(path)
    for _ in range(3):
        try:
            if isdir: shutil.rmtree(path, onerror=_onerr)
            else: os.chmod(path, stat.S_IWRITE); os.remove(path)
        except OSError:
            pass
        if not os.path.lexists(path): break
        time.sleep(0.2)
    left = path_size(path) if os.path.lexists(path) else 0
    return max(0, s - left), (1 if left else 0)

def execute_items(items, dry=False, progress=None, cancel=None, log=lambda m: None):
    per, fails_total, total = {}, 0, 0
    svc = any(i["key"] == "wu" for i in items) and not dry and is_admin()
    if svc: sh("net stop wuauserv"); sh("net stop bits")
    try:
        for n, it in enumerate(items, 1):
            if cancel is not None and cancel.is_set(): log("⏹ Đã hủy theo yêu cầu."); break
            if it["key"] == "recycle":
                freed, fails = it["size"], 0
                if not dry: ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
            else:
                freed, fails = delete_entry(it["path"], dry)
            per[it["key"]] = per.get(it["key"], 0) + freed; total += freed; fails_total += fails
            if progress: progress(n / len(items))
    finally:
        if svc: sh("net start wuauserv"); sh("net start bits")
    return total, fails_total, per

def run_clean(keys, cfg, dry=False, source="thủ công", progress=None, cancel=None, log=print, items=None):
    names = {k: l for k, l, *_ in TARGETS}
    if items is None: items = collect_items(keys, cfg.get("whitelist", []), cancel)
    total, fails, per = execute_items(items, dry, progress, cancel, log)
    for k, v in per.items(): log(f"  • {names.get(k, k)}: {fmt(v)}")
    log(f"✅ HOÀN TẤT{' (CHẾ ĐỘ THỬ)' if dry else ''}: giải phóng {fmt(total)}; {fails} mục đang được dùng nên bỏ qua.")
    if not dry: add_history(cfg, total, source, fails)
    return total, fails

def toast(title, msg):
    if os.name != "nt": return
    t, m = title.replace("'", ""), msg.replace("'", "")
    ps("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
       "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('ToastText02');"
       f"$n=$x.GetElementsByTagName('text'); $n.Item(0).AppendChild($x.CreateTextNode('{t}')) | Out-Null;"
       f"$n.Item(1).AppendChild($x.CreateTextNode('{m}')) | Out-Null;"
       "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
       "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($x))", 30)

def auto_run():
    """Chế độ không giao diện (--auto) do Task Scheduler gọi. Không bao giờ xóa Downloads."""
    cfg = load_config()
    keys = [k for k in cfg["auto_keys"] if k != "downloads"]
    def log(m):
        append_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [auto] {m}\n")
    log("Bắt đầu dọn tự động")
    total, _ = run_clean(keys, cfg, False, "lịch tự động", log=log)
    if cfg.get("notify"): toast(APP_NAME, f"Đã dọn tự động {fmt(total)}.")
    return 0

def self_cmd(args=""):
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {args}'.strip()
    exe = sys.executable; pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pw): exe = pw
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}" {args}'.strip()

def build_schtasks(sched):
    mode = sched.get("mode", "off")
    if mode == "off": return None
    cmd = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", self_cmd("--auto"), "/RL", "HIGHEST"]
    if mode == "daily": cmd += ["/SC", "DAILY", "/ST", sched["time"]]
    elif mode == "weekly": cmd += ["/SC", "WEEKLY", "/D", sched["day"], "/ST", sched["time"]]
    elif mode == "logon": cmd += ["/SC", "ONLOGON"]
    else: return None
    return cmd

def startup_enabled():
    return sh(["schtasks", "/Query", "/TN", STARTUP_TASK])[0] == 0

def set_startup(on):
    """Windows không tự chạy app yêu cầu Admin từ khóa Run, nên dùng Task Scheduler (đăng nhập + quyền cao nhất)."""
    sh(["schtasks", "/Delete", "/TN", STARTUP_TASK, "/F"])
    if on:
        rc, out = sh(["schtasks", "/Create", "/F", "/TN", STARTUP_TASK, "/TR", self_cmd("--tray"), "/SC", "ONLOGON", "/RL", "HIGHEST"])
        if rc != 0: raise RuntimeError(out or "schtasks lỗi (cần chạy bằng Administrator)")

def single_instance():
    global _MUTEX
    try:
        _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\PCOptimizerPro_GUI")
        return ctypes.windll.kernel32.GetLastError() != 183
    except Exception:
        return True

def make_icon_image(size=64):
    """Biểu tượng ứng dụng/khay: nền xanh bo góc + tia sét trắng (vẽ ở 4x rồi thu nhỏ cho mượt)."""
    from PIL import Image, ImageDraw
    k = 4; S = size * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((S * 0.03, S * 0.03, S * 0.97, S * 0.97), radius=S * 0.24, fill=(37, 99, 235, 255))
    u = S / 64
    d.polygon([(36 * u, 8 * u), (18 * u, 36 * u), (30 * u, 36 * u), (26 * u, 56 * u), (46 * u, 26 * u), (33 * u, 26 * u)], fill=(255, 255, 255, 255))
    return im.resize((size, size), Image.LANCZOS)

class Tray:
    """Biểu tượng khay hệ thống (pystray). Callback được chuyển về luồng Tk qua app.ui()."""
    def __init__(self, app): self.app, self.icon = app, None
    def start(self):
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Mở PC Optimizer", lambda i, m: self.app.ui(self.app.restore_window), default=True),
                pystray.MenuItem("Dọn nhanh", lambda i, m: self.app.ui(self.app.quick_clean)),
                pystray.MenuItem("Chạy dọn tự động ngay", lambda i, m: self.app.ui(lambda: self.app.run_auto("khay"))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Thoát", lambda i, m: self.app.ui(self.app.quit_app)))
            self.icon = pystray.Icon("pco", make_icon_image(), APP_NAME, menu)
            self.icon.run_detached(); return True
        except Exception:
            self.icon = None; return False
    def notify(self, msg, title=APP_NAME):
        try:
            if self.icon: self.icon.notify(msg, title)
        except Exception:
            pass
    def stop(self):
        try:
            if self.icon: self.icon.stop()
        except Exception:
            pass

class PreviewDialog(ctk.CTkToplevel):
    """Hộp thoại xem trước: liệt kê từng mục sẽ xóa, cho bỏ tick mục muốn giữ."""
    MAX_ROWS = 200
    def __init__(self, app, items, dry):
        super().__init__(app)
        self.app, self.dry, self.rows, self._ri = app, dry, [], 0
        self.title("Xem trước trước khi dọn"); self.geometry("940x680"); self.minsize(700, 460)
        self.configure(fg_color=C.BG); self.transient(app); self.after(200, self._grab); set_window_icon(self)
        names = {k: l for k, l, *_ in TARGETS}
        items = sorted(items, key=lambda i: -i["size"])
        total = sum(i["size"] for i in items)
        # đầu hộp thoại: tổng quan
        head = card(self); head.pack(fill="x", padx=14, pady=(14, 8))
        top = ctk.CTkFrame(head, fg_color="transparent"); top.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(top, font=fnt(19, "bold"), text=f"{len(items)} mục  •  {fmt(total)} sẽ được dọn").pack(side="left")
        if dry: pill(top, "CHẾ ĐỘ THỬ • không xóa thật", "info").pack(side="left", padx=12)
        ctk.CTkLabel(head, text="Bỏ tick những mục bạn muốn giữ lại, rồi bấm nút đỏ ở góc dưới bên phải để dọn.",
                     text_color=C.MUTED, anchor="w").pack(fill="x", padx=16, pady=(2, 12))
        if any(i["key"] == "downloads" for i in items):
            wb = ctk.CTkFrame(self, fg_color=C.DANGER_BG, border_width=1, border_color=C.BAD, corner_radius=10)
            wb.pack(fill="x", padx=14, pady=(0, 8))
            ctk.CTkLabel(wb, text="⚠  Có file trong Downloads — xóa xong không thể khôi phục!", text_color=C.BAD,
                         font=fnt(13, "bold")).pack(anchor="w", padx=14, pady=8)
        # chân hộp thoại (pack trước để luôn hiện, kể cả khi cửa sổ thấp)
        foot = ctk.CTkFrame(self, fg_color="transparent"); foot.pack(side="bottom", fill="x", padx=14, pady=(4, 14))
        btn(foot, "Hủy", self.destroy, kind="secondary", width=90, height=40).pack(side="right", padx=4)
        self.go = btn(foot, "", self.confirm, kind="danger", height=40, font=fnt(14, "bold"), hint="Xóa các mục đang được tick trong danh sách. Mục đã bỏ tick sẽ được giữ nguyên."); self.go.pack(side="right", padx=4)
        bar = ctk.CTkFrame(self, fg_color="transparent"); bar.pack(fill="x", padx=12, pady=(0, 2))
        btn(bar, "Chọn tất cả", lambda: self.set_all(True), kind="secondary", width=110, height=30).pack(side="left", padx=4)
        btn(bar, "Bỏ chọn tất cả", lambda: self.set_all(False), kind="secondary", width=120, height=30).pack(side="left", padx=4)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C.CARD, border_width=1, border_color=C.BORDER, corner_radius=12)
        self.scroll.pack(fill="both", expand=True, padx=14, pady=(6, 8)); self.scroll.grid_columnconfigure(0, weight=1)
        for it in items[:self.MAX_ROWS]:
            self._row([it], it["size"], names.get(it["key"], it["key"]), it["path"] or "Thùng rác (toàn bộ)")
        rest = {}
        for it in items[self.MAX_ROWS:]: rest.setdefault(it["key"], []).append(it)
        for k, lst in rest.items():
            self._row(lst, sum(i["size"] for i in lst), names.get(k, k), f"(+{len(lst)} mục nhỏ còn lại)")
        self.update_summary()
    def _grab(self):
        try: self.grab_set()
        except Exception: pass
    def _row(self, its, size, cat, path):
        v = ctk.BooleanVar(value=True); self.rows.append((v, its))
        tip(ctk.CTkCheckBox(self.scroll, text=f"{cat}   ›   {short_path(path, 95)}", variable=v, command=self.update_summary,
                            font=fnt(12)), f"{cat}\n{path}\nTick = sẽ xóa, bỏ tick = giữ lại.").grid(row=self._ri, column=0, sticky="w", padx=8, pady=3)
        ctk.CTkLabel(self.scroll, text=fmt(size), anchor="e", font=fnt(12, "bold"),
                     text_color=C.WARN if size >= 500 * 2 ** 20 else C.TEXT).grid(row=self._ri, column=1, sticky="e", padx=(8, 12))
        self._ri += 1
    def selected(self):
        return [it for v, its in self.rows if v.get() for it in its]
    def update_summary(self):
        sel = self.selected()
        self.go.configure(text=f"🧹 Dọn {len(sel)} mục ({fmt(sum(i['size'] for i in sel))})", state="normal" if sel else "disabled")
    def set_all(self, val):
        for v, _ in self.rows: v.set(val)
        self.update_summary()
    def confirm(self):
        sel = self.selected(); self.destroy()
        if sel: self.app.run_bg(self.app.clean_job, [], self.dry, [], sel, "thủ công")

# ----------------------------------------------------------------- app
class App(ctk.CTk):
    def __init__(self, start_hidden=False):
        super().__init__()
        ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue"); apply_theme()
        self.title(f"{APP_NAME} v{APP_VERSION}"); self.geometry("1200x800"); self.minsize(1020, 680)
        self.configure(fg_color=C.BG); set_window_icon(self)
        self.q = queue.Queue(); self.busy = False; self.cancel = threading.Event(); self._last_low = 0
        self.cfg = load_config(); self.tray = Tray(self)
        self.dry = ctk.BooleanVar(value=False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.clean_vars, self.clean_sizes, self.clean_bytes, self.clean_bars = {}, {}, {}, {}
        self._app_job, self._bar_col, self._log_open = None, {}, True
        self.update_info, self._upd_busy = None, False
        self.app_vars, self.big_vars, self.start_vars = {}, [], []
        self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)
        self._build_sidebar(); self._build_main()
        self.tray_ok = self.tray.start() if self.cfg.get("tray") else False
        self.show("dash"); self._poll(); self.refresh_dash(); self.refresh_apps(); self.refresh_startup()
        self.after(60000, self.watch_disk); self.hw_scan(); self.after(6000, self._update_tick)
        if start_hidden and self.tray_ok: self.withdraw()
        self.log(f"Sẵn sàng. Quyền Admin: {'CÓ' if is_admin() else 'KHÔNG (một số chức năng sẽ bị hạn chế)'}")
        self.log("💡 Mẹo: rê chuột vào bất kỳ nút, công tắc hay ô tick nào để xem giải thích chức năng.")

    # ---- infrastructure
    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=232, corner_radius=0, fg_color=C.SIDEBAR); sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        brand = ctk.CTkFrame(sb, fg_color="transparent"); brand.pack(fill="x", padx=16, pady=(22, 20))
        ctk.CTkLabel(brand, text="⚡", width=42, height=42, corner_radius=12, fg_color=C.ACCENT, text_color="white",
                     font=fnt(22, "bold")).pack(side="left")
        tb = ctk.CTkFrame(brand, fg_color="transparent"); tb.pack(side="left", padx=10)
        ctk.CTkLabel(tb, text="PC Optimizer", font=fnt(18, "bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(tb, text="Dọn rác & tối ưu Windows", font=fnt(11), text_color=C.MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(sb, text="MENU", font=fnt(11, "bold"), text_color=C.MUTED, anchor="w").pack(fill="x", padx=24, pady=(0, 4))
        self.nav, self.nav_bar = {}, {}
        for key, text in (("dash", "📊  Tổng quan"), ("clean", "🧹  Dọn rác"), ("apps", "🧩  Ứng dụng nền"),
                          ("net", "🌐  Mạng & DNS"), ("tools", "🛠  Công cụ"),
                          ("settings", "⚙  Cài đặt & Lịch")):
            row = ctk.CTkFrame(sb, fg_color="transparent"); row.pack(fill="x", padx=10, pady=2)
            bar = ctk.CTkFrame(row, width=4, height=24, corner_radius=2, fg_color="transparent"); bar.pack(side="left", padx=(0, 4))
            b = btn(row, text, lambda k=key: self.show(k), anchor="w", fg_color="transparent", text_color=C.TEXT,
                    hover_color=C.NAV_HOVER, height=40, font=fnt(14), hint=NAV_HINT[key])
            b.pack(side="left", fill="x", expand=True); self.nav[key] = b; self.nav_bar[key] = bar
        # phần dưới cùng (pack từ dưới lên): phiên bản -> giao diện -> chế độ thử -> trạng thái quyền
        ctk.CTkLabel(sb, text=f"v{APP_VERSION}   Build: {BUILD_TIME}", text_color=C.MUTED, font=fnt(11),
                     wraplength=205).pack(side="bottom", pady=(4, 12))
        theme_map = {"Tối": "dark", "Sáng": "light", "Theo hệ thống": "system"}
        om = ctk.CTkOptionMenu(sb, values=list(theme_map), command=lambda v: ctk.set_appearance_mode(theme_map[v]), height=30)
        om.set("Tối"); om.pack(side="bottom", fill="x", padx=16, pady=(0, 6)); tip(om, "Đổi giao diện Tối / Sáng / theo cài đặt Windows.")
        ctk.CTkLabel(sb, text="Giao diện", font=fnt(11), text_color=C.MUTED, anchor="w").pack(side="bottom", fill="x", padx=18)
        dc = card(sb, corner_radius=10); dc.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        tip(ctk.CTkSwitch(dc, text="Chế độ thử", variable=self.dry, font=fnt(13, "bold")), "Bật: các thao tác xóa chỉ được MÔ PHỎNG và ghi vào nhật ký, không xóa thật. "
            "Áp dụng cho: dọn rác, đóng ứng dụng, gán DNS, xóa file lớn, gỡ khởi động.\nCác lệnh mạng và công cụ hệ thống (Flush DNS, SFC, DISM...) vẫn chạy thật."
            ).pack(anchor="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(dc, text="Mô phỏng, không xóa thật", font=fnt(11), text_color=C.MUTED, anchor="w").pack(anchor="w", padx=14, pady=(0, 10))
        adm = is_admin()
        self.admin_pill = pill(sb, "● Quyền Administrator" if adm else "● Chưa có quyền Admin", "ok" if adm else "warn")
        self.admin_pill.pack(side="bottom", anchor="w", padx=16, pady=(0, 10))
        tip(self.admin_pill, "Có quyền Administrator: dọn được Temp hệ thống, Prefetch, dùng lệnh mạng, SFC/DISM đầy đủ." if adm else
            "Chưa có quyền Admin: một số mục dọn và lệnh hệ thống sẽ không chạy đầy đủ. Vào Tổng quan > “Chạy lại với quyền Admin”.")

    def _build_main(self):
        self.main = ctk.CTkFrame(self, fg_color="transparent"); self.main.grid(row=0, column=1, sticky="nsew", padx=16, pady=(14, 12))
        self.main.grid_columnconfigure(0, weight=1); self.main.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(self.main, fg_color="transparent"); top.grid(row=0, column=0, sticky="ew", pady=(0, 8)); top.grid_columnconfigure(0, weight=1)
        tt = ctk.CTkFrame(top, fg_color="transparent"); tt.grid(row=0, column=0, sticky="w")
        self.page_title = ctk.CTkLabel(tt, text="", font=fnt(26, "bold"), anchor="w"); self.page_title.pack(anchor="w")
        self.page_sub = ctk.CTkLabel(tt, text="", font=fnt(13), text_color=C.MUTED, anchor="w"); self.page_sub.pack(anchor="w")
        self.status_chip = pill(top, "● Sẵn sàng", "ok"); self.status_chip.grid(row=0, column=1, sticky="e")
        self.pages = {k: ctk.CTkFrame(self.main, fg_color="transparent") for k in self.nav}
        for p in self.pages.values():
            p.grid(row=1, column=0, sticky="nsew")
        pf = ctk.CTkFrame(self.main, fg_color="transparent"); pf.grid(row=2, column=0, sticky="ew", pady=(6, 2)); pf.grid_columnconfigure(0, weight=1)
        self.prog = ctk.CTkProgressBar(pf, height=6); self.prog.grid(row=0, column=0, sticky="ew"); self.prog.set(0)
        self.cancel_btn = btn(pf, "⏹ Hủy tác vụ", self.cancel.set, kind="danger", width=100, height=26, font=fnt(12))
        self.cancel_btn.grid(row=0, column=1, padx=(10, 0)); self.cancel_btn.grid_remove()
        lh = ctk.CTkFrame(self.main, fg_color="transparent"); lh.grid(row=3, column=0, sticky="ew", pady=(6, 2)); lh.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(lh, text="NHẬT KÝ HOẠT ĐỘNG", font=fnt(11, "bold"), text_color=C.MUTED).grid(row=0, column=0, sticky="w", padx=2)
        btn(lh, "Mở thư mục log", lambda: os.startfile(DATA_DIR), kind="ghost", width=110, height=22, font=fnt(11)).grid(row=0, column=1)
        btn(lh, "Xóa", self.clear_log, kind="ghost", width=50, height=22, font=fnt(11)).grid(row=0, column=2)
        self.log_toggle = btn(lh, "Ẩn", self.toggle_log, kind="ghost", width=50, height=22, font=fnt(11)); self.log_toggle.grid(row=0, column=3)
        self.logbox = ctk.CTkTextbox(self.main, height=140, font=fnt(12, family=FONT_MONO), fg_color=C.CARD, border_width=1,
                                     border_color=C.BORDER, corner_radius=10)
        self.logbox.grid(row=4, column=0, sticky="ew"); self.logbox.configure(state="disabled")
        for tag, col in (("ok", "#2ea043"), ("bad", "#e5534b"), ("warn", "#c69026"), ("info", "#4493f8"), ("ts", "#8b949e")):
            try: self.logbox.tag_config(tag, foreground=col)
            except Exception: pass
        self.build_dash(); self.build_clean(); self.build_apps(); self.build_net(); self.build_tools(); self.build_settings()

    def clear_log(self):
        self.logbox.configure(state="normal"); self.logbox.delete("1.0", "end"); self.logbox.configure(state="disabled")

    def toggle_log(self):
        self._log_open = not self._log_open
        if self._log_open: self.logbox.grid()
        else: self.logbox.grid_remove()
        self.log_toggle.configure(text="Ẩn" if self._log_open else "Hiện")

    def _busy_ui(self, busy):
        try:
            if busy: self.cancel_btn.grid(); set_pill(self.status_chip, "● Đang xử lý…", "warn")
            else: self.cancel_btn.grid_remove(); set_pill(self.status_chip, "● Sẵn sàng", "ok")
        except Exception:
            pass

    def show(self, name):
        self.pages[name].tkraise()
        for k, b in self.nav.items():
            on = k == name
            b.configure(fg_color=C.NAV_ON if on else "transparent", text_color=C.ACCENT_TXT if on else C.TEXT)
            self.nav_bar[k].configure(fg_color=C.ACCENT if on else "transparent")
        t, s = PAGE_INFO.get(name, (name, "")); self.page_title.configure(text=t); self.page_sub.configure(text=s)

    def log(self, msg):
        self.q.put(("log", msg))

    def ui(self, fn):
        self.q.put(("call", fn))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    ts = f"[{datetime.now():%H:%M:%S}] "
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", ts, "ts"); self.logbox.insert("end", f"{payload}\n", log_tag(payload))
                    try:
                        if int(self.logbox.index("end-1c").split(".")[0]) > 1500: self.logbox.delete("1.0", "500.0")
                    except Exception: pass
                    self.logbox.see("end"); self.logbox.configure(state="disabled")
                    append_log(f"{ts}{payload}\n")
                else:
                    payload()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def run_bg(self, fn, *args):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Đang có tác vụ chạy, vui lòng đợi."); return
        self.busy = True; self.cancel.clear(); self.prog.configure(mode="indeterminate"); self.prog.start(); self._busy_ui(True)
        def work():
            try:
                fn(*args)
            except Exception as e:
                self.log(f"❌ Lỗi: {e}")
            finally:
                def fin():
                    self.busy = False; self.prog.stop(); self.prog.configure(mode="determinate"); self.prog.set(0); self._busy_ui(False)
                self.ui(fin)
        threading.Thread(target=work, daemon=True).start()

    def run_cmd(self, label, cmd, use_ps=False, timeout=1800):
        def job():
            self.log(f"▶ {label} ...")
            rc, out = ps(cmd, timeout) if use_ps else sh(cmd, timeout)
            tail = out[-900:] if out else ""
            self.log(f"{'✔' if rc == 0 else '✖'} {label} (mã {rc})" + (f"\n{tail}" if tail else ""))
        self.run_bg(job)

    # ---- dashboard
    def build_dash(self):
        page = self.pages["dash"]; page.grid_columnconfigure(0, weight=1); page.grid_rowconfigure(0, weight=1)
        p = ctk.CTkScrollableFrame(page, fg_color="transparent"); p.grid(row=0, column=0, sticky="nsew"); p.grid_columnconfigure((0, 1), weight=1)
        # 3 thẻ số liệu: CPU / RAM / ổ đĩa
        stats = ctk.CTkFrame(p, fg_color="transparent"); stats.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform="stat")
        self.bars, self.bar_big = {}, {}
        for i, (k, t, ic) in enumerate((("cpu", "CPU", "⚙"), ("ram", "Bộ nhớ RAM", "🧩"), ("disk", f"Ổ đĩa {SYSDRIVE}", "💽"))):
            f = card(stats); f.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            h = ctk.CTkFrame(f, fg_color="transparent"); h.pack(fill="x", padx=16, pady=(14, 0))
            ctk.CTkLabel(h, text=ic, font=fnt(15)).pack(side="left")
            ctk.CTkLabel(h, text=t, font=fnt(13, "bold"), text_color=C.MUTED).pack(side="left", padx=6)
            big = ctk.CTkLabel(f, text="—", font=fnt(32, "bold"), anchor="w"); big.pack(fill="x", padx=16, pady=(2, 0))
            b = ctk.CTkProgressBar(f, height=8); b.pack(fill="x", padx=16, pady=(6, 6)); b.set(0)
            l = ctk.CTkLabel(f, text="—", font=fnt(12), text_color=C.MUTED, anchor="w"); l.pack(fill="x", padx=16, pady=(0, 14))
            self.bars[k] = (b, l); self.bar_big[k] = big
        # thông báo có bản mới (ẩn cho tới khi phát hiện bản mới)
        self.upd_banner = card(p, border_color=C.ACCENT, fg_color=("#ddf4ff", "#0c2a52"))
        self.upd_banner.grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
        ub = ctk.CTkFrame(self.upd_banner, fg_color="transparent"); ub.pack(side="left", fill="x", expand=True, padx=16, pady=10)
        self.upd_lbl = ctk.CTkLabel(ub, text="", font=fnt(14, "bold"), anchor="w", text_color=C.ACCENT_TXT); self.upd_lbl.pack(fill="x")
        self.upd_sub = ctk.CTkLabel(ub, text="", font=fnt(12), anchor="w", text_color=C.MUTED); self.upd_sub.pack(fill="x")
        btn(self.upd_banner, "Bỏ qua bản này", self.update_skip, kind="ghost", width=110).pack(side="right", padx=(0, 12))
        btn(self.upd_banner, "📄 Xem thay đổi", self.update_notes, kind="secondary", width=130).pack(side="right", padx=4)
        btn(self.upd_banner, "⬇ Tải về", self.update_download, kind="primary", width=110).pack(side="right", padx=4)
        self.upd_banner.grid_remove()
        # trạng thái quyền Admin
        ab = card(p); ab.grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        self.admin_lbl = ctk.CTkLabel(ab, text="", anchor="w", font=fnt(13)); self.admin_lbl.pack(side="left", padx=16, pady=12)
        if not is_admin():
            self.admin_lbl.configure(text="⚠ Chưa chạy Admin: Prefetch, Temp hệ thống, netsh, DISM/SFC sẽ không đầy đủ.", text_color=C.WARN)
            btn(ab, "Chạy lại với quyền Admin", lambda: (relaunch_admin(), self.destroy()), kind="warn").pack(side="right", padx=12, pady=8)
        else:
            self.admin_lbl.configure(text="✔ Đang chạy với quyền Administrator", text_color=C.OK)
        # thao tác nhanh
        qa = card(p); qa.grid(row=5, column=0, columnspan=2, sticky="ew", pady=6)
        ctk.CTkLabel(qa, text="Thao tác nhanh", font=fnt(16, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(qa, text="Dọn nhanh chỉ xử lý các mục mặc định an toàn — không bao giờ đụng tới thư mục Downloads.",
                     font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w", padx=16, pady=(0, 8))
        row = ctk.CTkFrame(qa, fg_color="transparent"); row.pack(fill="x", padx=12, pady=(0, 14)); row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="qa")
        for i, (t, c, kd) in enumerate((("🚀 Dọn nhanh 1 chạm", self.quick_clean, "primary"), ("🌐 Làm mới mạng", self.net_refresh, "secondary"),
                                        ("🛡 Tạo điểm khôi phục", self.restore_point, "secondary"),
                                        ("📂 Mở thư mục log", lambda: os.startfile(DATA_DIR), "secondary"))):
            btn(row, t, c, kind=kd, height=42).grid(row=0, column=i, sticky="ew", padx=4)
        if psutil is None:
            ctk.CTkLabel(p, text="Thiếu thư viện psutil → chạy: pip install psutil", text_color=C.WARN).grid(row=6, column=0, columnspan=2, sticky="w")

        self.build_hw_section(p)

    # ---- cấu hình phần cứng
    HW_COLORS = {"ok": "#3fb950", "warn": "#d29922", "bad": "#f85149", "unknown": "gray60"}

    def build_hw_section(self, p):
        h = ctk.CTkFrame(p, fg_color="transparent"); h.grid(row=7, column=0, columnspan=2, sticky="ew", padx=2, pady=(16, 2))
        t = ctk.CTkFrame(h, fg_color="transparent"); t.pack(side="left")
        ctk.CTkLabel(t, text="Cấu hình phần cứng", font=fnt(18, "bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(t, text="Quét tự động khi mở app. Vàng/đỏ = mục cần chú ý.", font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w")
        btn(h, "↻ Quét lại", self.hw_scan, kind="secondary", width=90, height=30).pack(side="right", padx=4)
        btn(h, "💾 Xuất .txt", self.hw_export, kind="secondary", width=100, height=30).pack(side="right", padx=4)
        btn(h, "📋 Sao chép", self.hw_copy, kind="secondary", width=100, height=30).pack(side="right", padx=4)
        self.hw_frame = ctk.CTkFrame(p, fg_color="transparent"); self.hw_frame.grid(row=8, column=0, columnspan=2, sticky="ew")
        self.hw_sections, self.hw_busy = [], False

    def _hw_status(self, text):
        for w in self.hw_frame.winfo_children(): w.destroy()
        c = card(self.hw_frame); c.pack(fill="x", pady=5)
        ctk.CTkLabel(c, text=text, text_color=C.MUTED).pack(anchor="w", padx=16, pady=14)

    def hw_scan(self):
        if self.hw_busy: return
        self.hw_busy = True; self._hw_status("⏳ Đang quét phần cứng (5–20 giây)...")
        def job():
            try: sections = hw.build_report(hw.collect())
            except Exception as e:
                sections = [{"title": "Lỗi quét phần cứng", "icon": "⚠", "badge": ("bad", "Lỗi"), "rows": [("Chi tiết", str(e))], "bars": [], "notes": []}]
            def done():
                self.hw_busy = False; self.hw_sections = sections; self.render_hardware(sections)
            self.ui(done)
        threading.Thread(target=job, daemon=True).start()

    @staticmethod
    def _fit_wrap(width, items, last):
        """Cho các nhãn nhiều dòng tự xuống dòng theo bề rộng thẻ và tự tính chiều cao (tránh bị cắt chữ)."""
        wrap = max(220, int(width))
        if last[0] is not None and abs(last[0] - wrap) < 8: return
        last[0] = wrap
        for lb, txt in items:
            lines = sum(max(1, -(-int(len(part) * 7.2) // wrap)) for part in str(txt).split("\n"))
            try: lb.configure(wraplength=wrap, height=max(22, 20 * lines + 4))
            except Exception: pass

    def render_hardware(self, sections):
        for w in self.hw_frame.winfo_children(): w.destroy()
        for s in sections:
            lvl = s["badge"][0] if s.get("badge") else "ok"
            strip = self.HW_COLORS.get(lvl, C.ACCENT) if s.get("badge") else C.ACCENT
            cd = card(self.hw_frame); cd.pack(fill="x", pady=5, padx=2); cd.grid_columnconfigure(1, weight=1)
            ctk.CTkFrame(cd, width=4, corner_radius=2, fg_color=strip).grid(row=0, column=0, sticky="ns", padx=(8, 0), pady=12)
            body = ctk.CTkFrame(cd, fg_color="transparent"); body.grid(row=0, column=1, sticky="ew", padx=(6, 8))
            head = ctk.CTkFrame(body, fg_color="transparent"); head.pack(fill="x", padx=8, pady=(10, 4))
            ctk.CTkLabel(head, text=s["icon"], width=34, height=34, corner_radius=10, fg_color=C.CARD2, font=fnt(16)).pack(side="left")
            ctk.CTkLabel(head, text=s["title"], font=fnt(15, "bold")).pack(side="left", padx=10)
            if s.get("badge"): pill(head, s["badge"][1], lvl).pack(side="right")
            if s["rows"]:
                g = ctk.CTkFrame(body, fg_color="transparent"); g.pack(fill="x", padx=8, pady=(0, 2)); g.grid_columnconfigure(1, weight=1)
                vals = []
                for i, (k, v) in enumerate(s["rows"]):
                    ctk.CTkLabel(g, text=k, text_color=C.MUTED, anchor="nw", justify="left", width=170, height=22).grid(row=i, column=0, sticky="nw", pady=2)
                    lb = ctk.CTkLabel(g, text=str(v), anchor="nw", justify="left", wraplength=520, height=22)
                    lb.grid(row=i, column=1, sticky="nw", padx=8, pady=2); vals.append((lb, str(v)))
                last = [None]; self._fit_wrap(560, vals, last)
                g.bind("<Configure>", lambda e, vs=vals, ls=last: self._fit_wrap(e.width - 200, vs, ls))
            for lbl, frac, txt, lv in s["bars"]:
                r = ctk.CTkFrame(body, fg_color="transparent"); r.pack(fill="x", padx=8, pady=3); r.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(r, text=lbl, anchor="w", width=170, text_color=C.MUTED).grid(row=0, column=0, sticky="w")
                b = ctk.CTkProgressBar(r, height=8, progress_color=self.HW_COLORS.get(lv, C.ACCENT)); b.grid(row=0, column=1, sticky="ew", padx=8)
                b.set(max(0, min(1, frac)))
                ctk.CTkLabel(r, text=txt, anchor="e", width=240).grid(row=0, column=2, sticky="e")
            notes = []
            for n in s["notes"]:
                col = self.HW_COLORS["bad"] if n.startswith("✖") else self.HW_COLORS["warn"] if n.startswith("⚠") else \
                      (self.HW_COLORS[lvl] if lvl in ("warn", "bad") else C.MUTED)
                txt = ("• " + n) if n[:1].isalnum() else n
                lb = ctk.CTkLabel(body, text=txt, anchor="w", justify="left", wraplength=700, text_color=col, height=22)
                lb.pack(fill="x", padx=10, pady=1); notes.append((lb, txt))
            if notes:
                last2 = [None]; self._fit_wrap(740, notes, last2)
                body.bind("<Configure>", lambda e, ns=notes, ls=last2: self._fit_wrap(e.width - 40, ns, ls))
            ctk.CTkFrame(body, height=8, fg_color="transparent").pack()

    def hw_copy(self):
        if not self.hw_sections: self.log("Chưa có dữ liệu phần cứng, hãy đợi quét xong."); return
        self.clipboard_clear(); self.clipboard_append(hw.report_text(self.hw_sections)); self.log("📋 Đã sao chép báo cáo phần cứng")

    def hw_export(self):
        if not self.hw_sections: self.log("Chưa có dữ liệu phần cứng, hãy đợi quét xong."); return
        f = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="bao_cao_phan_cung.txt", filetypes=[("Text", "*.txt")])
        if f:
            with open(f, "w", encoding="utf-8") as fh: fh.write(hw.report_text(self.hw_sections))
            self.log(f"💾 Đã xuất báo cáo: {f}")

    def _set_stat(self, k, pct, big, detail):
        bar, lab = self.bars[k]
        bar.set(max(0, min(1, pct / 100))); lab.configure(text=detail)
        self.bar_big[k].configure(text=big)
        col = lvl_color(pct)
        if self._bar_col.get(k) != col:          # chỉ đổi màu khi đổi mức, tránh vẽ lại mỗi 2 giây
            self._bar_col[k] = col; bar.configure(progress_color=col)
            self.bar_big[k].configure(text_color=col if col != C.OK else C.TEXT)

    def refresh_dash(self):
        try:
            if psutil:
                cpu = psutil.cpu_percent(); vm = psutil.virtual_memory()
                self._set_stat("cpu", cpu, f"{cpu:.0f}%", f"{psutil.cpu_count(logical=True) or '?'} luồng xử lý")
                self._set_stat("ram", vm.percent, f"{vm.percent:.0f}%", f"{fmt(vm.used)} / {fmt(vm.total)}  •  trống {fmt(vm.available)}")
            du = shutil.disk_usage(SYSDRIVE + "\\")
            pct = du.used / du.total * 100
            self._set_stat("disk", pct, f"{pct:.0f}%", f"{fmt(du.used)} / {fmt(du.total)}  •  trống {fmt(du.free)}")
        except Exception:
            pass
        self.after(2000, self.refresh_dash)

    def restore_point(self):
        self.run_cmd("Tạo điểm khôi phục", "Checkpoint-Computer -Description 'PCOptimizerPro' -RestorePointType MODIFY_SETTINGS", True)

    # ---- clean
    CLEAN_GROUPS = (("HỆ THỐNG WINDOWS", ("sys_temp", "usr_temp", "prefetch", "wu", "dumps", "thumb", "logs", "shader", "recycle")),
                    ("TRÌNH DUYỆT", ("chrome", "edge", "firefox", "brave")),
                    ("VÙNG NGUY HIỂM", ("downloads",)))

    def build_clean(self):
        p = self.pages["clean"]; p.grid_columnconfigure(0, weight=1); p.grid_rowconfigure(1, weight=1)
        bar = card(p); bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        r1 = ctk.CTkFrame(bar, fg_color="transparent"); r1.pack(fill="x", padx=10, pady=(10, 2))
        btn(r1, "🔍 Quét dung lượng", lambda: self.run_bg(self.scan, [k for k, *_ in TARGETS], list(self.cfg["whitelist"])), kind="secondary").pack(side="left", padx=4)
        btn(r1, "👁 Xem trước & dọn", self.on_preview_clean, kind="primary").pack(side="left", padx=4)
        btn(r1, "🧹 Dọn ngay", self.on_clean_now, kind="danger").pack(side="left", padx=4)
        btn(r1, "Bỏ chọn", lambda: self._setall(False), kind="ghost", width=80).pack(side="right", padx=4)
        btn(r1, "Chọn tất cả", lambda: self._setall(True), kind="ghost", width=100).pack(side="right", padx=4)
        self.clean_total = ctk.CTkLabel(bar, text="", anchor="w", font=fnt(13), text_color=C.MUTED); self.clean_total.pack(fill="x", padx=16, pady=(2, 10))
        sc = ctk.CTkScrollableFrame(p, fg_color="transparent"); sc.grid(row=1, column=0, sticky="nsew")
        meta = {t[0]: t for t in TARGETS}
        groups = [(g, [k for k in keys if k in meta]) for g, keys in self.CLEAN_GROUPS]
        groups[0][1].extend(k for k in meta if not any(k in ks for _, ks in self.CLEAN_GROUPS))
        for gname, keys in groups:
            if not keys: continue
            ctk.CTkLabel(sc, text=gname, font=fnt(11, "bold"), anchor="w",
                         text_color=C.BAD if "NGUY" in gname else C.MUTED).pack(fill="x", padx=6, pady=(10, 2))
            for key in keys:
                _, label, _, default, note = meta[key]; danger = key == "downloads"
                row = card(sc, corner_radius=10, fg_color=C.DANGER_BG if danger else C.CARD, border_color=C.BAD if danger else C.BORDER)
                row.pack(fill="x", pady=3); row.grid_columnconfigure(1, weight=1)
                v = ctk.BooleanVar(value=default); self.clean_vars[key] = v
                ck = dict(fg_color=C.BAD, hover_color="#b62324") if danger else {}
                th = TARGET_HELP.get(key, "")
                tip(ctk.CTkCheckBox(row, text=label, variable=v, width=250, font=fnt(13, "bold"), command=self.update_clean_sum,
                                    **ck), th).grid(row=0, column=0, padx=(14, 6), pady=11, sticky="w")
                tip(ctk.CTkLabel(row, text=note, text_color=C.WARN if danger else C.MUTED, font=fnt(12), anchor="w"), th).grid(row=0, column=1, sticky="w", padx=6)
                pb = ctk.CTkProgressBar(row, width=90, height=6); pb.set(0); pb.grid(row=0, column=2, padx=8); self.clean_bars[key] = pb
                lab = ctk.CTkLabel(row, text="—", width=84, anchor="e", font=fnt(13, "bold"), text_color=C.MUTED)
                lab.grid(row=0, column=3, padx=(4, 14)); self.clean_sizes[key] = lab
                tip(lab, "Dung lượng mục này đang chiếm. Bấm “Quét dung lượng” để cập nhật. Vàng ≥ 500 MB, đỏ ≥ 1 GB.")
        self.update_clean_sum()

    def _setall(self, val):
        for v in self.clean_vars.values(): v.set(val)
        self.update_clean_sum()

    def update_clean_sum(self):
        """Dòng tóm tắt: số mục đang chọn và (nếu đã quét) dung lượng ước tính sẽ giải phóng."""
        sel = self.selected_keys()
        if self.clean_bytes:
            txt = f"Đã chọn {len(sel)} mục   •   có thể giải phóng ≈ {fmt(sum(self.clean_bytes.get(k, 0) for k in sel))}"
        else:
            txt = f"Đã chọn {len(sel)} mục   •   bấm “Quét dung lượng” để xem từng mục đang chiếm bao nhiêu"
        self.clean_total.configure(text=txt)

    def _set_clean_size(self, k, s):
        self.clean_bytes[k] = s
        self.clean_sizes[k].configure(text=fmt(s) if s else "0 B",
                                      text_color=C.MUTED if s == 0 else C.BAD if s >= 2 ** 30 else C.WARN if s >= 500 * 2 ** 20 else C.TEXT)
        self.update_clean_sum()

    def _scan_done(self, sizes):
        mx = max(sizes.values(), default=0) or 1
        for k, b in self.clean_bars.items(): b.set(sizes.get(k, 0) / mx)
        self.update_clean_sum()

    def selected_keys(self):
        return [k for k, v in self.clean_vars.items() if v.get()]

    def scan(self, all_keys, wl):
        self.log("🔍 Đang quét dung lượng..."); total = 0; sizes = {}
        for key in all_keys:
            s = sum(i["size"] for i in collect_items([key], wl, self.cancel)); total += s; sizes[key] = s
            self.ui(lambda k=key, s=s: self._set_clean_size(k, s))
        self.ui(lambda: self._scan_done(dict(sizes)))
        self.log(f"✔ Quét xong: {fmt(total)}")

    def quick_clean(self):
        self.start_clean([k for k, _, _, d, _ in TARGETS if d])

    def on_preview_clean(self):
        self.start_clean(self.selected_keys(), preview=True)

    def on_clean_now(self):
        self.start_clean(self.selected_keys(), preview=False)

    def start_clean(self, keys, preview=None):
        """Mọi thao tác đọc widget (dry/cfg) đều làm ở luồng chính rồi truyền giá trị vào luồng nền."""
        if not keys:
            messagebox.showinfo(APP_NAME, "Chưa chọn mục nào."); return
        dry = self.dry.get(); wl = list(self.cfg["whitelist"])
        preview = self.cfg["preview"] if preview is None else preview
        if preview:
            def job():
                self.log("🔍 Đang lập danh sách xem trước...")
                items = collect_items(keys, wl, self.cancel)
                if not items: self.log("Không có mục nào để dọn."); return
                self.ui(lambda: self.open_preview(items, dry))
            self.run_bg(job); return
        if "downloads" in keys and not dry:
            if not messagebox.askyesno(APP_NAME, f"XÓA SẠCH toàn bộ file trong:\n{downloads_path()}\n\nKhông thể hoàn tác. Tiếp tục?", icon="warning"):
                keys = [k for k in keys if k != "downloads"]
                if not keys: return
        self.run_bg(self.clean_job, keys, dry, wl, None, "thủ công")

    def open_preview(self, items, dry):
        self.restore_window(); PreviewDialog(self, items, dry)

    def run_auto(self, source):
        keys = [k for k in self.cfg["auto_keys"] if k != "downloads"]
        if keys: self.run_bg(self.clean_job, keys, self.dry.get(), list(self.cfg["whitelist"]), None, source)

    def clean_job(self, keys, dry, wl, items=None, source="thủ công"):
        self.log(f"🧹 Bắt đầu dọn {'(CHẾ ĐỘ THỬ)' if dry else ''}...")
        if items is None: items = collect_items(keys, wl, self.cancel)
        if not items: self.log("Không có mục nào để dọn."); return
        self.ui(lambda: (self.prog.stop(), self.prog.configure(mode="determinate"), self.prog.set(0)))
        last = [-1]
        def progress(f):
            if int(f * 100) != last[0]:
                last[0] = int(f * 100); self.ui(lambda: self.prog.set(f))
        total, _ = run_clean(keys, self.cfg, dry, source, progress, self.cancel, self.log, items)
        self.ui(lambda: self.after_clean(items, total, dry))

    def after_clean(self, items, total, dry):
        if not dry:
            for k in {i["key"] for i in items}:
                self._set_clean_size(k, 0); self.clean_sizes[k].configure(text="—"); self.clean_bars[k].set(0)
            if self.cfg.get("notify"): self.tray.notify(f"Đã dọn {fmt(total)}")
        self.refresh_history()

    # ---- cửa sổ / khay / theo dõi ổ đĩa
    def on_close(self):
        if self.cfg.get("tray") and self.tray_ok:
            self.withdraw(); self.tray.notify("Vẫn đang chạy nền. Bấm biểu tượng ở khay để mở lại.")
        else:
            self.quit_app()

    def restore_window(self):
        self.deiconify(); self.lift(); self.focus_force()

    def quit_app(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "Đang có tác vụ chạy. Vẫn thoát?"): return
        self.tray.stop(); self.destroy()

    def watch_disk(self):
        ld = self.cfg["low_disk"]
        if ld["enabled"] and not self.busy:
            try:
                du = shutil.disk_usage(SYSDRIVE + "\\"); free_pct = du.free / du.total * 100
                if free_pct < ld["percent"] and time.time() - self._last_low > 6 * 3600:
                    self._last_low = time.time()
                    self.tray.notify(f"Ổ {SYSDRIVE} chỉ còn {free_pct:.0f}% trống. Đang dọn tự động...")
                    self.run_auto("ổ đĩa gần đầy")
            except Exception:
                pass
        self.after(60000, self.watch_disk)

    # ---- cài đặt & lịch
    def build_settings(self):
        p = self.pages["settings"]; p.grid_columnconfigure(0, weight=1); p.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(p, fg_color="transparent"); sc.grid(row=0, column=0, sticky="nsew")
        def section(title, desc=""):
            f = card(sc); f.pack(fill="x", pady=6, padx=1)
            ctk.CTkLabel(f, text=title, font=fnt(16, "bold"), anchor="w").pack(fill="x", padx=16, pady=(12, 0 if desc else 6))
            if desc: ctk.CTkLabel(f, text=desc, text_color=C.MUTED, font=fnt(12), justify="left", wraplength=760, anchor="w").pack(fill="x", padx=16, pady=(0, 8))
            return f
        c = self.cfg
        g = section("Chung")
        self.v_preview = ctk.BooleanVar(value=c["preview"]); self.v_tray = ctk.BooleanVar(value=c["tray"])
        self.v_notify = ctk.BooleanVar(value=c["notify"]); self.v_startup = ctk.BooleanVar(value=startup_enabled())
        for t, v, cmd in (("Luôn xem trước danh sách trước khi dọn", self.v_preview, self.save_general),
                          ("Bấm ✕ thì thu nhỏ vào khay hệ thống (chạy nền)", self.v_tray, self.save_general),
                          ("Hiện thông báo khi dọn xong", self.v_notify, self.save_general),
                          ("Khởi động cùng Windows (chạy nền trong khay)", self.v_startup, self.toggle_startup)):
            tip(ctk.CTkSwitch(g, text=t, variable=v, command=cmd), SWITCH_HELP.get(t, "")).pack(anchor="w", padx=18, pady=5)
        ctk.CTkFrame(g, height=6, fg_color="transparent").pack()
        u = section("Cập nhật", f"Phiên bản hiện tại v{APP_VERSION}. Kiểm tra bản mới trên GitHub Releases"
                                f" ({UPDATE_REPO or 'chưa cấu hình kho — xem HUONG_DAN_GITHUB.txt'}).")
        self.v_upd_auto = ctk.BooleanVar(value=c["update"]["auto"])
        tip(ctk.CTkSwitch(u, text="Tự kiểm tra bản mới (tối đa mỗi 24 giờ)", variable=self.v_upd_auto, command=self.save_update_opts),
            SWITCH_HELP["Tự kiểm tra bản mới (tối đa mỗi 24 giờ)"]).pack(anchor="w", padx=18, pady=(4, 6))
        ur = ctk.CTkFrame(u, fg_color="transparent"); ur.pack(fill="x", padx=14, pady=(0, 14))
        btn(ur, "🔄 Kiểm tra ngay", lambda: self.check_update(True), width=150).pack(side="left", padx=4)
        self.upd_status = ctk.CTkLabel(ur, text=self._upd_idle_text(), text_color=C.MUTED, anchor="w"); self.upd_status.pack(side="left", padx=10)
        w = section("Danh sách bảo vệ", "Các mục này không bao giờ bị xóa (thư mục chứa chúng cũng được giữ nguyên).")
        self.wl_frame = ctk.CTkFrame(w, fg_color="transparent"); self.wl_frame.pack(fill="x", padx=14)
        btn(w, "+ Thêm thư mục bảo vệ...", self.add_whitelist, kind="secondary", width=210).pack(anchor="w", padx=16, pady=(8, 14))
        self.render_whitelist()
        s = section("Lịch dọn tự động", "Dùng Windows Task Scheduler — vẫn chạy khi app đã tắt.")
        sch = c["schedule"]; inv = {v: k for k, v in MODES.items()}
        r = ctk.CTkFrame(s, fg_color="transparent"); r.pack(fill="x", padx=14, pady=(2, 4))
        for col, t in enumerate(("Chế độ", "Ngày (chỉ 'Hằng tuần')", "Giờ (HH:MM)")):
            ctk.CTkLabel(r, text=t, text_color=C.MUTED, font=fnt(11), anchor="w").grid(row=0, column=col, sticky="w", padx=4)
        self.sch_mode = ctk.CTkOptionMenu(r, values=list(MODES), width=160); self.sch_mode.set(inv.get(sch["mode"], "Tắt")); self.sch_mode.grid(row=1, column=0, padx=4, pady=(0, 4))
        self.sch_day = ctk.CTkOptionMenu(r, values=DAYS, width=110); self.sch_day.set(sch["day"]); self.sch_day.grid(row=1, column=1, padx=4, pady=(0, 4), sticky="w")
        self.sch_time = ctk.CTkEntry(r, width=80); self.sch_time.insert(0, sch["time"]); self.sch_time.grid(row=1, column=2, padx=4, pady=(0, 4), sticky="w")
        tip(self.sch_mode, "Tần suất dọn tự động: Tắt / Hằng ngày / Hằng tuần / Khi đăng nhập Windows.")
        tip(self.sch_day, "Thứ trong tuần sẽ chạy. Chỉ có tác dụng với chế độ Hằng tuần.")
        tip(self.sch_time, "Giờ chạy theo dạng HH:MM (24 giờ), ví dụ 21:30. Dùng cho chế độ Hằng ngày/Hằng tuần.")
        r2 = ctk.CTkFrame(s, fg_color="transparent"); r2.pack(fill="x", padx=14, pady=6)
        btn(r2, "Áp dụng lịch", self.apply_schedule, width=130).pack(side="left", padx=4)
        btn(r2, "Gỡ lịch", self.remove_schedule, kind="secondary", width=90).pack(side="left", padx=4)
        self.sch_status = ctk.CTkLabel(r2, text="", text_color=C.MUTED); self.sch_status.pack(side="left", padx=10)
        ctk.CTkLabel(s, text="Mục được dọn tự động (Downloads luôn bị loại trừ):", font=fnt(13, "bold"), anchor="w").pack(fill="x", padx=16, pady=(6, 2))
        grid = ctk.CTkFrame(s, fg_color="transparent"); grid.pack(fill="x", padx=14); grid.grid_columnconfigure((0, 1, 2), weight=1)
        self.auto_vars = {}
        for i, t in enumerate(t for t in TARGETS if t[0] != "downloads"):
            v = ctk.BooleanVar(value=t[0] in c["auto_keys"]); self.auto_vars[t[0]] = v
            tip(ctk.CTkCheckBox(grid, text=t[1], variable=v, command=self.save_auto_keys), "Có dọn mục này khi dọn tự động (theo lịch, khi ổ đĩa đầy, hoặc nút chạy ngay) hay không.\n\n" + TARGET_HELP.get(t[0], "")).grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=3)
        btn(s, "▶ Chạy dọn tự động ngay bây giờ", lambda: self.run_auto("chạy tay"), kind="secondary", width=250).pack(anchor="w", padx=16, pady=(10, 14))
        d = section("Dọn khi ổ đĩa gần đầy", "Chỉ hoạt động khi app (hoặc biểu tượng khay) đang chạy.")
        ld = c["low_disk"]; r3 = ctk.CTkFrame(d, fg_color="transparent"); r3.pack(fill="x", padx=14, pady=(0, 14))
        self.v_low = ctk.BooleanVar(value=ld["enabled"])
        tip(ctk.CTkSwitch(r3, text="Bật — dọn khi dung lượng trống dưới", variable=self.v_low, command=self.save_lowdisk),
            "Khi dung lượng trống của ổ hệ thống thấp hơn ngưỡng bên cạnh, app tự dọn bằng các mục đã chọn ở phần “Lịch dọn tự động”. Tối đa 1 lần mỗi 6 giờ.").pack(side="left", padx=4)
        self.low_pct = ctk.CTkEntry(r3, width=56); self.low_pct.insert(0, str(ld["percent"])); self.low_pct.pack(side="left", padx=6)
        self.low_pct.bind("<FocusOut>", self.save_lowdisk); ctk.CTkLabel(r3, text="%").pack(side="left")
        tip(self.low_pct, "Ngưỡng phần trăm dung lượng trống (ví dụ 10). Nhập xong bấm ra ngoài ô để lưu.")
        h = section("Lịch sử dọn")
        self.hist_box = ctk.CTkTextbox(h, height=160, font=fnt(12, family=FONT_MONO), fg_color=C.CARD2, border_width=0)
        self.hist_box.pack(fill="x", padx=14, pady=(0, 14))
        self.refresh_history(); self.refresh_sched_status()

    # ---- cập nhật từ GitHub Releases
    def _upd_idle_text(self):
        t = self.cfg["update"].get("last_check") or 0
        return f"Lần kiểm tra cuối: {datetime.fromtimestamp(t):%H:%M %d/%m/%Y}" if t else "Chưa kiểm tra lần nào."

    def _set_upd_status(self, text, level="info"):
        try: self.upd_status.configure(text=text, text_color={"ok": C.OK, "warn": C.WARN}.get(level, C.MUTED))
        except Exception: pass

    def save_update_opts(self):
        self.cfg["update"]["auto"] = self.v_upd_auto.get(); save_config(self.cfg)

    def _update_tick(self):
        """Mỗi giờ xem có đến hạn kiểm tra tự động (24 giờ/lần) chưa; chỉ chạy khi đã cấu hình kho GitHub."""
        u = self.cfg["update"]
        if UPDATE_REPO and u.get("auto") and time.time() - (u.get("last_check") or 0) > 24 * 3600:
            self.check_update(False)
        self.after(3600 * 1000, self._update_tick)

    def check_update(self, manual=False):
        """Hỏi GitHub ở luồng nền (không đụng widget); kết quả được chuyển về luồng chính qua self.ui."""
        if self._upd_busy: return
        if not UPDATE_REPO:
            if manual: self._set_upd_status("Chưa cấu hình kho GitHub — xem HUONG_DAN_GITHUB.txt.", "warn")
            return
        self._upd_busy = True
        if manual: self._set_upd_status("Đang kiểm tra...")
        repo = UPDATE_REPO
        def work():
            info, err = fetch_latest_release(repo)
            self.ui(lambda: self._update_done(info, err, manual))
        threading.Thread(target=work, daemon=True).start()

    def _update_done(self, info, err, manual):
        self._upd_busy = False; u = self.cfg["update"]
        if err:                                   # kiểm tra tự động thất bại thì im lặng, 1 giờ sau thử lại
            if manual: self._set_upd_status(err, "warn"); self.log(f"⚠ Kiểm tra cập nhật: {err}")
            return
        u["last_check"] = time.time()
        if not is_newer(info["version"]):
            self.update_info = None; self.upd_banner.grid_remove(); save_config(self.cfg)
            self._set_upd_status(f"✔ Bạn đang dùng bản mới nhất (v{APP_VERSION}). {self._upd_idle_text()}", "ok")
            if manual: self.log(f"✔ Đang dùng bản mới nhất (v{APP_VERSION})")
            return
        self.update_info = info
        self._set_upd_status(f"Có bản mới {info['tag']} (đang dùng v{APP_VERSION}).", "warn")
        if manual or u.get("skipped") != info["tag"]: self._show_update_banner(info)
        if not manual and u.get("skipped") != info["tag"] and u.get("notified") != info["tag"]:
            u["notified"] = info["tag"]
            self.log(f"⬆ Có bản mới {info['tag']} — xem ở màn hình Tổng quan")
            if self.cfg.get("notify"): self.tray.notify(f"Có bản mới {info['tag']}. Mở app để tải về.")
        save_config(self.cfg)

    def _show_update_banner(self, info):
        self.upd_lbl.configure(text=f"🎉 Có bản mới {info['tag']} (bạn đang dùng v{APP_VERSION})")
        bits = [f"phát hành {info['published']}" if info["published"] else "", fmt(info["size"]) if info["size"] else "",
                info["asset_name"] or "chưa có file .exe đính kèm — bấm Tải về để mở trang phát hành"]
        self.upd_sub.configure(text="  •  ".join(b for b in bits if b))
        self.upd_banner.grid()

    def _open_url(self, url):
        if str(url).startswith("https://github.com/"): webbrowser.open(url); return True   # chỉ mở liên kết GitHub
        self.log("⚠ Liên kết không hợp lệ, không mở.")
        return False

    def update_download(self):
        i = self.update_info
        if not i: return
        if self._open_url(i["asset_url"] or i["page"]):
            self.log(f"⬇ Đã mở trình duyệt để tải {i['asset_name'] or i['tag']}")
            messagebox.showinfo(APP_NAME, "Trình duyệt đang tải bản mới.\n\nSau khi tải xong:\n"
                                "1) Thoát app hoàn toàn (chuột phải biểu tượng ở khay hệ thống > Thoát).\n"
                                "2) Chép file mới ĐÈ lên file cũ (cùng thư mục) rồi mở lại.\n\n"
                                "Cấu hình, danh sách bảo vệ và lịch sử được giữ nguyên. Nếu để file mới ở chỗ khác, vào Cài đặt bấm "
                                "“Áp dụng lịch” và bật lại “Khởi động cùng Windows” để các tác vụ trỏ tới file mới.")

    def update_notes(self):
        if self.update_info: self._open_url(self.update_info["page"])

    def update_skip(self):
        if not self.update_info: return
        self.cfg["update"]["skipped"] = self.update_info["tag"]; save_config(self.cfg)
        self.upd_banner.grid_remove(); self._set_upd_status(f"Đã bỏ qua {self.update_info['tag']}. Bản kế tiếp sẽ vẫn được báo.")

    def save_general(self):
        c = self.cfg
        c["preview"], c["tray"], c["notify"] = self.v_preview.get(), self.v_tray.get(), self.v_notify.get()
        save_config(c)
        if c["tray"] and not self.tray_ok: self.tray_ok = self.tray.start()

    def toggle_startup(self):
        try:
            set_startup(self.v_startup.get()); self.log("✔ Đã cập nhật 'khởi động cùng Windows'")
        except Exception as e:
            self.log(f"✖ {e}"); self.v_startup.set(startup_enabled())

    def render_whitelist(self):
        for w in self.wl_frame.winfo_children(): w.destroy()
        for pth in self.cfg["whitelist"]:
            r = ctk.CTkFrame(self.wl_frame, fg_color=C.CARD2, corner_radius=8); r.pack(fill="x", pady=2)
            ctk.CTkLabel(r, text="🛡  " + short_path(pth, 100), anchor="w").pack(side="left", padx=10, pady=4)
            btn(r, "✕", lambda p=pth: self.remove_whitelist(p), kind="secondary", width=28, height=24).pack(side="right", padx=6)
        if not self.cfg["whitelist"]: ctk.CTkLabel(self.wl_frame, text="(chưa có mục nào)", text_color=C.MUTED).pack(anchor="w", padx=2)

    def add_whitelist(self):
        d = filedialog.askdirectory()
        if d: self._add_wl(d)

    def _add_wl(self, pth):
        pth = os.path.normpath(pth)
        if pth not in self.cfg["whitelist"]: self.cfg["whitelist"].append(pth); save_config(self.cfg)
        self.render_whitelist()

    def remove_whitelist(self, pth):
        self.cfg["whitelist"] = [x for x in self.cfg["whitelist"] if x != pth]; save_config(self.cfg); self.render_whitelist()

    def save_auto_keys(self):
        self.cfg["auto_keys"] = [k for k, v in self.auto_vars.items() if v.get()]; save_config(self.cfg)

    def save_lowdisk(self, *_):
        try: pct = min(90, max(1, int(self.low_pct.get())))
        except ValueError: pct = 10
        self.cfg["low_disk"] = {"enabled": self.v_low.get(), "percent": pct}; save_config(self.cfg)

    def apply_schedule(self):
        mode, t = MODES[self.sch_mode.get()], self.sch_time.get().strip()
        if mode in ("daily", "weekly") and not valid_time(t):
            messagebox.showerror(APP_NAME, "Giờ không hợp lệ. Dùng định dạng HH:MM, ví dụ 21:30."); return
        sched = {"mode": mode, "time": t if valid_time(t) else self.cfg["schedule"]["time"], "day": self.sch_day.get()}
        self.cfg["schedule"] = sched; save_config(self.cfg)
        if mode != "off" and not is_admin():
            messagebox.showwarning(APP_NAME, "Tạo lịch chạy với quyền cao nhất cần chạy app bằng Administrator."); return
        def job():
            sh(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
            cmd = build_schtasks(sched)
            if cmd is None: self.log("✔ Đã tắt lịch tự động")
            else:
                rc, out = sh(cmd); self.log(f"{'✔ Đã tạo lịch tự động' if rc == 0 else '✖ Không tạo được lịch'}: {out[:300]}")
            self.ui(self.refresh_sched_status)
        self.run_bg(job)

    def remove_schedule(self):
        self.sch_mode.set("Tắt"); self.apply_schedule()

    def refresh_sched_status(self):
        def job():
            rc, _ = sh(["schtasks", "/Query", "/TN", TASK_NAME])
            txt = "✔ Lịch đang hoạt động trong Task Scheduler" if rc == 0 else "Chưa có lịch trong Task Scheduler"
            self.ui(lambda: self.sch_status.configure(text=txt))
        threading.Thread(target=job, daemon=True).start()

    def refresh_history(self):
        h = self.cfg.get("history", [])
        lines = [f"Tổng đã giải phóng ({len(h)} lần gần nhất): {fmt(sum(x['freed'] for x in h))}", ""]
        lines += [f"{x['t']}   {fmt(x['freed']):>10}   {x['src']}" for x in reversed(h[-15:])]
        self.hist_box.configure(state="normal"); self.hist_box.delete("1.0", "end")
        self.hist_box.insert("end", "\n".join(lines)); self.hist_box.configure(state="disabled")

    # ---- apps
    def build_apps(self):
        p = self.pages["apps"]; p.grid_columnconfigure(0, weight=1); p.grid_rowconfigure(1, weight=1)
        bar = card(p); bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        r = ctk.CTkFrame(bar, fg_color="transparent"); r.pack(fill="x", padx=10, pady=(10, 2))
        self.app_search = ctk.CTkEntry(r, placeholder_text="🔎 Tìm tiến trình...", width=170, height=34)
        self.app_search.pack(side="left", padx=4); self.app_search.bind("<KeyRelease>", self._app_search_changed)
        tip(self.app_search, "Gõ một phần tên tiến trình (ví dụ chrome) để lọc danh sách.")
        btn(r, "↻ Làm mới", self.refresh_apps, kind="secondary", width=95).pack(side="left", padx=4)
        btn(r, "DS mặc định", self.select_default_apps, kind="secondary", width=110).pack(side="left", padx=4)
        btn(r, "Bỏ chọn", lambda: [v.set(False) for v in self.app_vars.values()], kind="ghost", width=80).pack(side="left", padx=4)
        btn(r, "Đóng nhẹ", lambda: self.kill_apps(False)).pack(side="right", padx=4)
        btn(r, "Ép đóng", lambda: self.kill_apps(True), kind="danger").pack(side="right", padx=4)
        self.app_info = ctk.CTkLabel(bar, text="", anchor="w", font=fnt(12), text_color=C.MUTED); self.app_info.pack(fill="x", padx=16, pady=(2, 10))
        self.app_list = ctk.CTkScrollableFrame(p, fg_color=C.CARD, border_width=1, border_color=C.BORDER, corner_radius=12)
        self.app_list.grid(row=1, column=0, sticky="nsew"); self.app_list.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(p, text="Đóng nhẹ = yêu cầu app tự thoát (an toàn, có thể hỏi lưu file). Ép đóng = taskkill /f, có thể mất dữ liệu chưa lưu.",
                     text_color=C.MUTED, font=fnt(12), wraplength=820, justify="left", anchor="w").grid(row=2, column=0, sticky="w", padx=4, pady=6)

    def _app_search_changed(self, _e=None):
        if self._app_job:
            try: self.after_cancel(self._app_job)
            except Exception: pass
        self._app_job = self.after(250, self.refresh_apps)    # chờ người dùng gõ xong rồi mới lọc lại

    def refresh_apps(self):
        self._app_job = None
        for w in self.app_list.winfo_children(): w.destroy()
        if psutil is None:
            ctk.CTkLabel(self.app_list, text="Cần cài psutil: pip install psutil", text_color=C.WARN).grid(row=0, column=0, padx=10, pady=10); return
        groups = {}; me = os.getpid(); user = None
        try: user = psutil.Process().username()
        except Exception: pass
        for pr in psutil.process_iter(["name", "memory_info", "username", "pid"]):
            try:
                n = (pr.info["name"] or "").lower()
                base = n[:-4] if n.endswith(".exe") else n
                if not n or base in PROTECTED or pr.info["pid"] == me or (user and pr.info["username"] != user): continue
                g = groups.setdefault(n, [0, 0]); g[0] += 1; g[1] += pr.info["memory_info"].rss
            except Exception:
                pass
        flt = self.app_search.get().strip().lower()
        items = sorted(((n, c, m) for n, (c, m) in groups.items() if flt in n), key=lambda x: -x[2])[:120]
        self.app_info.configure(text=f"Hiển thị {len(items)} ứng dụng  •  tổng RAM {fmt(sum(m for _, _, m in items))}  •  sắp xếp theo RAM giảm dần")
        for i, (n, c, m) in enumerate(items):
            v = self.app_vars.setdefault(n, ctk.BooleanVar(value=False))
            tip(ctk.CTkCheckBox(self.app_list, text=f"{n}   ({c} tiến trình)", variable=v),
                f"{n}: {c} tiến trình, đang dùng {fmt(m)} RAM.\nTick rồi bấm “Đóng nhẹ” (an toàn) hoặc “Ép đóng” (buộc tắt) để giải phóng RAM."
                ).grid(row=i, column=0, sticky="w", padx=8, pady=3)
            ctk.CTkLabel(self.app_list, text=fmt(m), anchor="e", font=fnt(12, "bold"),
                         text_color=C.BAD if m >= 1536 * 2 ** 20 else C.WARN if m >= 500 * 2 ** 20 else C.TEXT).grid(row=i, column=1, sticky="e", padx=(8, 14))

    def select_default_apps(self):
        for n, v in self.app_vars.items():
            v.set((n[:-4] if n.endswith(".exe") else n) in DEFAULT_KILL)

    def kill_apps(self, force):
        names = [n for n, v in self.app_vars.items() if v.get()]
        if not names: messagebox.showinfo(APP_NAME, "Chưa chọn ứng dụng nào."); return
        if force and not messagebox.askyesno(APP_NAME, f"Ép đóng {len(names)} ứng dụng?\nDữ liệu chưa lưu sẽ mất.", icon="warning"): return
        dry = self.dry.get()
        def job():
            for n in names:
                if dry: self.log(f"[thử] sẽ đóng {n}"); continue
                rc, _ = sh(["taskkill"] + (["/f"] if force else []) + ["/im", n])
                self.log(f"{'✔' if rc == 0 else '–'} {n}")
            self.ui(self.refresh_apps)
        self.run_bg(job)

    # ---- network
    def build_net(self):
        p = self.pages["net"]; p.grid_columnconfigure(0, weight=1); p.grid_rowconfigure(2, weight=1)
        top = card(p); top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(top, text="Làm mới & tối ưu mạng", font=fnt(16, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(top, text="Dùng khi mạng chập chờn, vào web chậm hoặc sau khi đổi DNS. Một số lệnh cần quyền Admin.",
                     font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w", padx=16, pady=(0, 6))
        r = ctk.CTkFrame(top, fg_color="transparent"); r.pack(fill="x", padx=10, pady=(0, 12))
        for t, c in (("Reset Winsock & IP", self.net_reset), ("Flush DNS", lambda: self.run_bg(self.net_flush_job)),
                     ("Tối ưu TCP", self.net_tcp), ("Release/Renew IP", self.net_renew), ("Xem DNS hiện tại", self.net_show)):
            btn(r, t, c, kind="secondary").pack(side="left", padx=4, pady=2)
        mid = card(p); mid.grid(row=1, column=0, sticky="ew", pady=4)
        ctk.CTkLabel(mid, text="Đo tốc độ DNS", font=fnt(16, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(mid, text="Gửi truy vấn DNS thật tới từng nhà cung cấp rồi lấy trung vị — kết quả phản ánh đúng mạng của bạn.",
                     font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w", padx=16, pady=(0, 6))
        r2 = ctk.CTkFrame(mid, fg_color="transparent"); r2.pack(fill="x", padx=10, pady=(0, 4))
        btn(r2, "⏱ Đo tất cả DNS", lambda: self.run_bg(self.dns_bench)).pack(side="left", padx=4)
        self.dns_pick = ctk.CTkComboBox(r2, width=330, values=[f"{i+1}. {n} ({a} / {b})" for i, (n, a, b, _) in enumerate(DNS_LIST)])
        self.dns_pick.pack(side="left", padx=4); self.dns_pick.set("Chọn nhà cung cấp DNS")
        adapters = self.adapters()
        self.adapter = ctk.CTkComboBox(r2, width=170, values=adapters or ["Ethernet"]); self.adapter.pack(side="left", padx=4)
        tip(self.dns_pick, "Chọn nhà cung cấp DNS muốn dùng. Sau khi “Đo tất cả DNS”, nhà cung cấp nhanh nhất được chọn sẵn.")
        tip(self.adapter, "Card mạng sẽ bị đổi DNS (thường là Ethernet hoặc Wi-Fi đang kết nối). Tên phải đúng như trong Windows.")
        if adapters: self.adapter.set(adapters[0])
        r3 = ctk.CTkFrame(mid, fg_color="transparent"); r3.pack(fill="x", padx=10, pady=(0, 12))
        btn(r3, "Gán DNS tĩnh", self.dns_apply, kind="success").pack(side="left", padx=4)
        btn(r3, "Về DNS tự động (DHCP)", self.dns_dhcp, kind="secondary").pack(side="left", padx=4)
        self.dns_out = ctk.CTkTextbox(p, font=fnt(12, family=FONT_MONO), fg_color=C.CARD, border_width=1, border_color=C.BORDER, corner_radius=12)
        self.dns_out.grid(row=2, column=0, sticky="nsew", pady=6)
        self.dns_out.insert("end", "Bấm 'Đo tất cả DNS' để xếp hạng theo tốc độ thực tế từ máy bạn.\n")

    def adapters(self):
        if not psutil: return []
        return [n for n, s in psutil.net_if_stats().items() if s.isup and "loopback" not in n.lower() and "vethernet" not in n.lower()]

    def net_reset(self):
        if not messagebox.askyesno(APP_NAME, "Reset Winsock & IP stack. Nên khởi động lại máy sau đó. Tiếp tục?"): return
        def job():
            for c in ("netsh winsock reset", "netsh int ip reset", "ipconfig /flushdns"):
                rc, out = sh(c); self.log(f"{'✔' if rc == 0 else '✖'} {c}")
            self.log("ℹ Hãy khởi động lại máy để hoàn tất reset mạng.")
        self.run_bg(job)

    def net_flush_job(self):
        rc, _ = sh("ipconfig /flushdns"); self.log(f"{'✔' if rc == 0 else '✖'} Flush DNS")

    def net_tcp(self):
        def job():
            for c in ("netsh int tcp set global autotuninglevel=normal", "netsh int tcp set global rss=enabled",
                      "netsh int tcp set global ecncapability=disabled"):
                rc, _ = sh(c); self.log(f"{'✔' if rc == 0 else '✖'} {c}")
        self.run_bg(job)

    def net_renew(self):
        def job():
            sh("ipconfig /release"); rc, out = sh("ipconfig /renew"); self.log(f"{'✔' if rc == 0 else '✖'} Release/Renew IP")
        self.run_bg(job)

    def net_refresh(self):
        self.run_bg(lambda: (self.net_flush_job(), sh("ipconfig /registerdns"), self.log("✔ Đã làm mới DNS/mạng")))

    def net_show(self):
        self.run_cmd("DNS hiện tại", "Get-DnsClientServerAddress -AddressFamily IPv4 | Format-Table InterfaceAlias,ServerAddresses -AutoSize | Out-String", True)

    def dns_bench(self):
        self.log("⏱ Đang đo DNS (20-40 giây)...")
        domains = ["google.com", "facebook.com", "youtube.com", "microsoft.com", "wikipedia.org"]
        def test(item):
            n, a, b, note = item; vals = []
            for _ in range(2):
                for d in domains:
                    t = dns_query_ms(a, d)
                    if t is not None: vals.append(t)
            ok = len(vals) / (2 * len(domains))
            return (statistics.median(vals) if vals else None, ok, item)
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(test, DNS_LIST))
        res.sort(key=lambda r: (r[0] is None, r[0] or 0))
        lines = [f"{'#':<3}{'Tên':<22}{'IP chính':<17}{'Trung vị':>10}{'Thành công':>12}", "-" * 66]
        for i, (m, ok, (n, a, b, note)) in enumerate(res, 1):
            lines.append(f"{i:<3}{n:<22}{a:<17}{(f'{m:.0f} ms' if m is not None else 'Timeout'):>10}{ok*100:>11.0f}%")
        good = [r for r in res if r[0] is not None][:3]
        lines += ["", "🏆 NHANH NHẤT:"] + [f"  {i}. {r[2][0]} - {r[2][1]} ({r[0]:.0f} ms)" for i, r in enumerate(good, 1)]
        lines += ["", "🛡 BẢO MẬT: Quad9 (9.9.9.9), Cloudflare 1.1.1.2, AdGuard DNS (94.140.14.14)",
                  "⚖ CÂN BẰNG: Cloudflare 1.1.1.1 / 1.0.0.1", "",
                  "Lưu ý: DNS Trung Quốc (AliDNS/DNSPod) có thể nhanh nhưng bị kiểm duyệt/ghi log theo luật nước đó."]
        txt = "\n".join(lines)
        def put():
            self.dns_out.delete("1.0", "end"); self.dns_out.insert("end", txt)
            if good:
                idx = DNS_LIST.index(good[0][2]); self.dns_pick.set(f"{idx+1}. {DNS_LIST[idx][0]} ({DNS_LIST[idx][1]} / {DNS_LIST[idx][2]})")
        self.ui(put); self.log("✔ Đo DNS xong")

    def dns_apply(self):
        sel = self.dns_pick.get()
        try: idx = int(sel.split(".")[0]) - 1; n, a, b, _ = DNS_LIST[idx]
        except Exception: messagebox.showinfo(APP_NAME, "Hãy chọn nhà cung cấp DNS."); return
        ad = self.adapter.get().strip()
        if self.dry.get(): self.log(f"[thử] gán DNS {n} cho {ad}"); return
        def job():
            r1, o1 = sh(f'netsh interface ip set dns name="{ad}" static {a} primary')
            r2, o2 = sh(f'netsh interface ip add dns name="{ad}" {b} index=2')
            sh("ipconfig /flushdns")
            self.log(f"{'✔' if r1 == 0 else '✖'} Gán {n} ({a}/{b}) cho '{ad}'" + ("" if r1 == 0 else f"\n{o1}\n(cần Admin?)"))
        self.run_bg(job)

    def dns_dhcp(self):
        ad = self.adapter.get().strip()
        def job():
            rc, out = sh(f'netsh interface ip set dns name="{ad}" dhcp'); self.log(f"{'✔' if rc == 0 else '✖'} Đã về DNS tự động cho '{ad}' {out if rc else ''}")
        self.run_bg(job)

    # ---- tools
    def build_tools(self):
        page = self.pages["tools"]; page.grid_columnconfigure(0, weight=1); page.grid_rowconfigure(0, weight=1)
        p = ctk.CTkScrollableFrame(page, fg_color="transparent"); p.grid(row=0, column=0, sticky="nsew"); p.grid_columnconfigure(0, weight=1)
        sysf = card(p); sysf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(sysf, text="Công cụ hệ thống", font=fnt(16, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(sysf, text="Cần quyền Admin. SFC và DISM có thể chạy vài phút — theo dõi tiến độ ở khung nhật ký bên dưới.",
                     font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w", padx=16, pady=(0, 6))
        r = ctk.CTkFrame(sysf, fg_color="transparent"); r.pack(fill="x", padx=10, pady=(0, 12)); r.grid_columnconfigure((0, 1, 2), weight=1, uniform="tl")
        tools = (("SFC /scannow", lambda: self.run_cmd("SFC scannow", "sfc /scannow")),
                 ("DISM RestoreHealth", lambda: self.run_cmd("DISM RestoreHealth", "DISM /Online /Cleanup-Image /RestoreHealth")),
                 ("Dọn WinSxS", lambda: self.run_cmd("DISM StartComponentCleanup", "DISM /Online /Cleanup-Image /StartComponentCleanup")),
                 ("Disk Cleanup", lambda: subprocess.Popen("cleanmgr", creationflags=NO_WIN)),
                 ("High Performance", lambda: self.run_cmd("Power plan High Performance", "powercfg /setactive SCHEME_MIN")),
                 ("Tắt Hibernate (giải phóng hiberfil)", lambda: self.run_cmd("Tắt hibernate", "powercfg /hibernate off")))
        for i, (t, c) in enumerate(tools):
            btn(r, t, c, kind="secondary").grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="ew")
        # file lớn
        bf = card(p); bf.grid(row=1, column=0, sticky="ew", pady=4)
        ctk.CTkLabel(bf, text="Tìm file dung lượng lớn", font=fnt(16, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(bf, text="Chọn thư mục và ngưỡng, bấm Quét. File xóa sẽ được chuyển vào Thùng rác (nếu có send2trash).",
                     font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w", padx=16, pady=(0, 6))
        r2 = ctk.CTkFrame(bf, fg_color="transparent"); r2.pack(fill="x", padx=10, pady=(0, 8))
        self.big_dir = ctk.CTkEntry(r2, width=230); self.big_dir.insert(0, os.environ.get("USERPROFILE", "C:\\")); self.big_dir.pack(side="left", padx=4); tip(self.big_dir, "Thư mục sẽ quét, gồm cả các thư mục con. Mặc định là thư mục tài khoản của bạn.")
        btn(r2, "Chọn...", lambda: (lambda d: d and (self.big_dir.delete(0, "end"), self.big_dir.insert(0, d)))(filedialog.askdirectory()), kind="secondary", width=70).pack(side="left", padx=2)
        self.big_min = ctk.CTkEntry(r2, width=70); self.big_min.insert(0, "200"); self.big_min.pack(side="left", padx=4); tip(self.big_min, "Chỉ liệt kê file lớn hơn số MB này (mặc định 200).")
        ctk.CTkLabel(r2, text="MB", text_color=C.MUTED).pack(side="left")
        btn(r2, "Quét", self.start_find_big).pack(side="left", padx=6)
        btn(r2, "Xóa mục chọn", self.del_big, kind="danger").pack(side="left", padx=4)
        btn(r2, "Mở vị trí", self.open_big, kind="secondary").pack(side="left", padx=4)
        self.big_frame = ctk.CTkScrollableFrame(bf, height=180, fg_color=C.CARD2, corner_radius=10); self.big_frame.pack(fill="x", padx=14, pady=(0, 14))
        self.big_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.big_frame, text="Chưa quét. Bấm “Quét” để liệt kê các file lớn nhất.", text_color=C.MUTED).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        # khởi động
        sf = card(p); sf.grid(row=2, column=0, sticky="ew", pady=4)
        h = ctk.CTkFrame(sf, fg_color="transparent"); h.pack(fill="x", padx=10, pady=(12, 4))
        t = ctk.CTkFrame(h, fg_color="transparent"); t.pack(side="left", padx=6)
        ctk.CTkLabel(t, text="Chương trình khởi động cùng Windows", font=fnt(16, "bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(t, text="Gỡ bớt mục không cần thiết để máy khởi động nhanh hơn. Có sao lưu để khôi phục.", font=fnt(12), text_color=C.MUTED, anchor="w").pack(anchor="w")
        btn(h, "Gỡ mục chọn", self.remove_startup, kind="danger", width=110).pack(side="right", padx=4)
        btn(h, "Khôi phục đã gỡ", self.restore_startup, kind="secondary", width=130).pack(side="right", padx=4)
        self.start_frame = ctk.CTkScrollableFrame(sf, height=140, fg_color=C.CARD2, corner_radius=10); self.start_frame.pack(fill="x", padx=14, pady=(0, 14))

    def start_find_big(self):
        root = self.big_dir.get().strip()
        try: minb = float(self.big_min.get()) * 1048576
        except ValueError: minb = 200 * 1048576
        self.run_bg(self.find_big, root, minb)

    def find_big(self, root, minb):
        self.log(f"🔍 Quét file ≥ {fmt(minb)} trong {root} ...")
        found, stack = [], [root]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False): stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                s = e.stat().st_size
                                if s >= minb: found.append((s, e.path))
                        except OSError: pass
            except OSError: pass
        found.sort(reverse=True); found = found[:60]
        def put():
            for w in self.big_frame.winfo_children(): w.destroy()
            self.big_vars = []
            if not found:
                ctk.CTkLabel(self.big_frame, text="Không có file nào lớn hơn ngưỡng đã đặt.", text_color=C.MUTED).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            for i, (s, pth) in enumerate(found):
                v = ctk.BooleanVar(value=False); self.big_vars.append((v, pth))
                tip(ctk.CTkCheckBox(self.big_frame, text=short_path(pth, 100), variable=v), pth).grid(row=i, column=0, sticky="w", padx=6, pady=3)
                ctk.CTkLabel(self.big_frame, text=fmt(s), anchor="e", font=fnt(12, "bold"),
                             text_color=C.WARN if s >= 2 ** 30 else C.TEXT).grid(row=i, column=1, sticky="e", padx=10)
        self.ui(put); self.log(f"✔ Tìm thấy {len(found)} file lớn")

    def del_big(self):
        sel = [p for v, p in self.big_vars if v.get()]
        if not sel: return
        how = "chuyển vào Thùng rác" if send2trash else "XÓA VĨNH VIỄN (chưa cài send2trash)"
        if not messagebox.askyesno(APP_NAME, f"{len(sel)} file sẽ {how}. Tiếp tục?", icon="warning"): return
        dry = self.dry.get()
        def job():
            for pth in sel:
                if dry: self.log(f"[thử] xóa {pth}"); continue
                try:
                    send2trash(os.path.normpath(pth)) if send2trash else os.remove(pth); self.log(f"✔ Đã xóa {pth}")
                except Exception as e: self.log(f"✖ {pth}: {e}")
        self.run_bg(job)

    def open_big(self):
        for v, pth in self.big_vars:
            if v.get(): subprocess.Popen(["explorer", "/select,", os.path.normpath(pth)]); break

    def refresh_startup(self):
        for w in self.start_frame.winfo_children(): w.destroy()
        self.start_vars = []
        for tag, n, val in list_startup():
            v = ctk.BooleanVar(value=False); self.start_vars.append((v, tag, n, val))
            tip(ctk.CTkCheckBox(self.start_frame, text=f"[{tag}] {n}  →  {short_path(val, 90)}", variable=v),
                f"{n}\n{val}\n\nChương trình này tự chạy khi bật máy. Tick rồi bấm “Gỡ mục chọn” để tắt tự chạy. Mục thuộc HKLM cần quyền Admin để gỡ.").pack(anchor="w", pady=3, padx=6)
        if not self.start_vars: ctk.CTkLabel(self.start_frame, text="(không có mục nào)", text_color=C.MUTED).pack(pady=8)

    def remove_startup(self):
        sel = [x for x in self.start_vars if x[0].get()]
        if not sel: return
        if not messagebox.askyesno(APP_NAME, f"Gỡ {len(sel)} mục khỏi khởi động? (có sao lưu để khôi phục)"): return
        try: bak = json.loads(STARTUP_BACKUP.read_text("utf-8")) if STARTUP_BACKUP.exists() else []
        except Exception: bak = []
        for _, tag, n, val in sel:
            if self.dry.get(): self.log(f"[thử] gỡ {n}"); continue
            try:
                with winreg.OpenKey(HIVES[tag], RUN_SUB, 0, winreg.KEY_SET_VALUE) as k: winreg.DeleteValue(k, n)
                bak.append({"tag": tag, "name": n, "value": val}); self.log(f"✔ Đã gỡ khởi động: {n}")
            except OSError as e: self.log(f"✖ {n}: {e} (HKLM cần Admin)")
        STARTUP_BACKUP.write_text(json.dumps(bak, ensure_ascii=False, indent=1), "utf-8"); self.refresh_startup()

    def restore_startup(self):
        if not STARTUP_BACKUP.exists(): self.log("Không có bản sao lưu."); return
        try: bak = json.loads(STARTUP_BACKUP.read_text("utf-8"))
        except Exception: return
        left = []
        for it in bak:
            try:
                with winreg.OpenKey(HIVES[it["tag"]], RUN_SUB, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, it["name"], 0, winreg.REG_SZ, it["value"])
                self.log(f"✔ Khôi phục {it['name']}")
            except OSError as e: self.log(f"✖ {it['name']}: {e}"); left.append(it)
        STARTUP_BACKUP.write_text(json.dumps(left, ensure_ascii=False), "utf-8"); self.refresh_startup()


def main():
    if os.name != "nt":
        print("Ứng dụng này chỉ chạy trên Windows."); return 1
    if "--auto" in sys.argv: return auto_run()
    if not single_instance():
        ctypes.windll.user32.MessageBoxW(0, f"{APP_NAME} v{APP_VERSION} (build {BUILD_TIME}) đang được mở ở một bản khác — có thể đang thu nhỏ trong khay hệ thống.\n\n"
                                         "Nếu bạn vừa build bản mới mà vẫn thấy giao diện cũ: bấm mũi tên ^ cạnh đồng hồ, chuột phải biểu tượng PC Optimizer, chọn 'Thoát', rồi mở lại file exe.", APP_NAME, 0x40); return 0
    App(start_hidden="--tray" in sys.argv).mainloop(); return 0


if __name__ == "__main__":
    sys.exit(main())
