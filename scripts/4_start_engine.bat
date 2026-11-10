@echo off
chcp 65001 >nul
title Khởi chạy Hệ thống Tự động Nhắn tin FB + Zalo & Xưởng Kịch Bản
setlocal

cd /d "%~dp0.."

if not exist ".env" (
    echo [THÔNG BÁO] Chưa có file .env, hệ thống tự động tạo từ .env.example...
    copy /Y ".env.example" ".env" >nul
    echo 👉 Hãy điền AI_API_KEY của bạn trong file .env để bật trả lời thông minh bằng LLM!
)

echo [1/3] Khởi tạo Cơ sở dữ liệu SQLite & Nạp danh bạ mẫu...
python scripts\3_import_sample_candidates.py

echo [2/3] Khởi chạy Zalo Real-time Listener (nếu đã quét QR)...
if exist "zalo_bridge\accounts\acc_default\credentials.json" (
    start "Zalo Bridge Listener" cmd /k "node zalo_bridge\zalo_listener.mjs"
) else (
    echo [GHI CHÚ] Chưa có credentials.json của Zalo. Chạy `node scripts\2_login_zalo_qr.mjs` khi muốn kết nối Zalo.
)

echo [3/3] Khởi chạy Engine Đa Kênh (FB + Zalo) & Mở Xưởng Thiết Kế Chiến Dịch tại cổng 8768...
start "FB + Zalo Auto-Outreach Engine" cmd /k "python main_engine.py --daemon --port 8768"

timeout /t 2 >nul
start "" "http://localhost:8768/dashboard/index.html"

echo.
echo ✅ Hệ thống đã sẵn sàng tại: http://localhost:8768/dashboard/index.html
pause
