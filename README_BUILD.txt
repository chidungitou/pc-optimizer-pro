PC OPTIMIZER PRO - DONG GOI THANH FILE EXE
==========================================
Cach 1 (khuyen dung, tren may Windows cua ban)
  1. Cai Python 3.10+ tu python.org (tick "Add python.exe to PATH").
  2. Dat chung 1 thu muc: pc_optimizer.py, test_pc_optimizer.py, requirements.txt, build_exe.bat
  3. Bam doi build_exe.bat  ->  ket qua: dist\PCOptimizerPro.exe

Cach 2 (khong can cai gi tren may): GitHub Actions
  Day ca thu muc len mot repo GitHub (file workflow phai nam o .github/workflows/build.yml)
  -> tab Actions -> "Build exe & Release" -> Run workflow -> tai file PCOptimizerPro.exe o muc Artifacts.
  Muon phat hanh ban moi cho nguoi dung (app tu bao "Co ban moi"): xem HUONG_DAN_GITHUB.txt
  (tao Release v2.x -> Actions tu build va dinh file .exe vao Release).
  Build tai may (Cach 1) cung kiem tra ban moi duoc neu tao file update_repo.txt (1 dong: ten-tai-khoan/ten-kho).

Ghi chu
  - File exe tu yeu cau quyen Administrator (can cho netsh, Prefetch, Task Scheduler).
  - Ban "onefile" tu giai nen vao %TEMP% moi lan mo (khoi dong cham 2-5 giay); app tu bao ve
    thu muc giai nen nay nen se khong xoa nham chinh no.
  - Windows SmartScreen / antivirus co the canh bao vi file chua ky so (app co taskkill, xoa file hang loat,
    sua DNS). Day la canh bao nham thuong gap voi PyInstaller. Cach xu ly: ky so (code signing),
    hoac Nhieu thong tin > Van chay. Ban cung co the dung --onedir thay cho --onefile de it bi canh bao hon
    va mo nhanh hon (sua tham so trong build_exe.bat).
  - Du lieu/log/cau hinh: %LOCALAPPDATA%\PCOptimizerPro
