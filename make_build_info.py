# Tạo build_info.py (do build_exe.bat và GitHub Actions gọi). Hiện trong app để biết đang chạy bản nào,
# và cho app biết kho GitHub nào cần hỏi khi kiểm tra bản mới.
import datetime, os, re
from pathlib import Path

stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
# Thứ tự ưu tiên: biến môi trường của GitHub Actions -> file update_repo.txt (build tại máy) -> để trống
repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
if not repo and Path("update_repo.txt").exists():
    lines = [l.strip() for l in Path("update_repo.txt").read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    repo = lines[0] if lines else ""
if repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
    print("[!] update_repo.txt phai co dang  ten-tai-khoan/ten-kho  (vi du: nguyenvana/pc-optimizer-pro). Bo qua gia tri:", repo)
    repo = ""

with open("build_info.py", "w", encoding="utf-8") as f:
    f.write('BUILD_TIME = "%s"\nUPDATE_REPO = "%s"\n' % (stamp, repo))
print("Build stamp:", stamp)
print("Update repo:", repo or "(chua cau hinh - app se khong kiem tra ban moi; xem HUONG_DAN_GITHUB.txt)")
