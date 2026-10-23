@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Build PCOptimizerPro.exe

:: --- 1. Dong ban dang chay (neu khong, Windows khoa file exe va khong ghi de duoc -> van ra ban cu) ---
echo [*] Dong PCOptimizerPro.exe dang chay (neu co)...
taskkill /f /im PCOptimizerPro.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: --- 2. Tim Python ---
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
%PY% --version >nul 2>&1 || (echo [!] Chua cai Python 3.10+ ^(https://www.python.org^). Nho tick "Add python.exe to PATH". & pause & exit /b 1)

echo [*] Cai thu vien...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install -r requirements.txt pyinstaller || (echo [!] Cai thu vien that bai. & pause & exit /b 1)

:: --- 3. Don ban build cu ---
echo [*] Don thu muc build cu...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
rmdir /s /q __pycache__ 2>nul
del /q PCOptimizerPro.spec 2>nul
if exist dist\PCOptimizerPro.exe (
  echo [!] KHONG xoa duoc dist\PCOptimizerPro.exe - file dang bi khoa.
  echo     Mo Task Manager - tab Details - ket thuc moi tien trinh PCOptimizerPro.exe, roi chay lai.
  pause & exit /b 1
)

echo [*] Chay kiem thu logic (cac cua so nhap nhay luc nay la bai test, KHONG phai file exe)...
%PY% test_pc_optimizer.py >test_result.txt 2>&1
findstr /b "OK" test_result.txt >nul && (echo     Kiem thu: OK) || (echo [!] Co test that bai, xem test_result.txt. Van tiep tuc build.)

echo [*] Tao icon va dau phien ban...
%PY% make_build_info.py
%PY% -c "import pc_optimizer as p; p.make_icon_image(256).save('app.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])" || (echo [!] Tao icon that bai. & pause & exit /b 1)

echo [*] Dong goi (1-3 phut)...
%PY% -m PyInstaller --noconfirm --clean --onefile --noconsole --uac-admin ^
  --name PCOptimizerPro --icon app.ico ^
  --collect-all customtkinter --hidden-import pystray._win32 ^
  pc_optimizer.py || (echo [!] Dong goi that bai. Xem loi o tren. & pause & exit /b 1)

:: --- 4. Kiem tra ket qua ---
if not exist dist\PCOptimizerPro.exe (echo [!] Khong thay dist\PCOptimizerPro.exe & pause & exit /b 1)
echo.
echo ====================================================
echo  XONG. File moi vua tao:
for %%F in (dist\PCOptimizerPro.exe) do echo    %%~fF
for %%F in (dist\PCOptimizerPro.exe) do echo    Sua luc: %%~tF   Dung luong: %%~zF bytes
type build_info.py
echo  Mo file tren: goc duoi ben trai trong app phai hien dong "Build:" trung voi gio o day.
echo  Neu thay Build khac hoac khong co dong Build = dang mo nham ban cu.
echo ====================================================
pause
