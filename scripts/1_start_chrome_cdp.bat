@echo off
chcp 65001 >nul
title Khởi chạy Trình duyệt Chrome CDP (Cổng 9232, 9234, 9235)
setlocal

set "ROOT_DIR=%~dp0.."
set "PROFILES_DIR=%ROOT_DIR%\chrome_profiles"
if not exist "%PROFILES_DIR%" mkdir "%PROFILES_DIR%"

set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME_EXE%" (
    echo [LỖI] Không tìm thấy Google Chrome tại đường dẫn mặc định.
    pause
    exit /b 1
)

echo ============================================================================
echo [1/3] Mở Chrome Cổng 9232 - Nick Facebook Admin Chính (Nhắn tin, Cấp quyền KDV)
echo ============================================================================
start "" "%CHROME_EXE%" --remote-debugging-port=9232 --user-data-dir="%PROFILES_DIR%\fb_main_9232" --no-first-run --no-default-browser-check https://www.facebook.com/messages

echo [2/3] Mở Chrome Cổng 9234 - Nick Facebook Phụ Scout 1 (Quét thành viên/SĐT)
start "" "%CHROME_EXE%" --remote-debugging-port=9234 --user-data-dir="%PROFILES_DIR%\fb_scout_9234" --no-first-run --no-default-browser-check https://www.facebook.com

echo [3/3] Mở Chrome Cổng 9235 - Nick Facebook Phụ Scout 2 (Dự phòng/Xác minh huy hiệu)
start "" "%CHROME_EXE%" --remote-debugging-port=9235 --user-data-dir="%PROFILES_DIR%\fb_scout_9235" --no-first-run --no-default-browser-check https://www.facebook.com

echo.
echo [HOÀN TẤT] Vui lòng đăng nhập tài khoản Facebook tương ứng trên các cửa sổ Chrome vừa mở!
pause
