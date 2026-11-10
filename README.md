# 🚀 Hệ Thống Tự Động Nhắn Tin & Đối Thoại AI Đa Kênh (Facebook Messenger + Zalo) — Tùy Biến Mọi Mục Đích

Bộ khung (Framework) tự động tiếp cận (Outreach) và trả lời tin nhắn 2 chiều thời gian thực trên **Facebook Messenger (Playwright Chrome CDP)** và **Zalo (`zca-js` + SQLite)**.

Thiết kế **độc lập hoàn toàn với nghiệp vụ cụ thể (Purpose-Agnostic)**: Bạn có thể tùy chỉnh 100% mục tiêu chiến dịch, chân dung người gửi, mẫu câu chào mở đầu và kịch bản trả lời AI ngay trên **Giao diện Web (`http://localhost:8768/dashboard/index.html`)** hoặc qua file `campaign_config.json` cho bất kỳ mục đích nào:
- 💼 **Tư vấn Bán hàng & Chăm sóc Lead (Sales B2B / B2C)**
- 🤝 **Mời Hợp tác Đối tác / Đại lý / Nhà phân phối / KOL / KOC**
- 💖 **Chăm sóc Khách hàng Cũ & Đặt lịch hẹn Dịch vụ (CSKH)**
- 🌐 **Phát triển Cộng đồng / Mời Thành viên VIP / Ban quản trị**

---

## ✨ Cơ chế kỹ thuật cốt lõi

1. **Điều phối Đa Kênh Thông Minh (`fb_and_zalo_smart`)**:
   - **Facebook Messenger (`channels/fb_messenger_cdp.py`)**: Kết nối vào trình duyệt Chrome đang mở sẵn (`Port 9232`), gửi tin nhắn mở đầu và quét hộp thư chưa đọc mỗi 5 giây để phản hồi tự nhiên.
   - **Zalo (`channels/zalo_fast_lane.py` + `zalo_bridge/zalo_listener.mjs`)**: Tự động tìm tài khoản Zalo qua Số điện thoại (`api.findUser`), gửi lời mời kết bạn kèm lời chào tùy biến, gửi tin nhắn mở đầu và phản hồi siêu tốc qua SQLite (`< 0.05s/lần quét`).
   - **Tự động cứu hộ chéo kênh**: Nếu khách chặn tin nhắn từ người lạ trên Facebook Messenger (`UNREACHABLE`), hệ thống tự động chuyển sang tìm SĐT và nhắn qua Zalo!
   - **Chạy trực tiếp trên PC (`ZALO_BRIDGE_MODE="local"`) hoặc qua VPS (`ZALO_BRIDGE_MODE="ssh"`)**: Không bắt buộc phải thuê máy chủ Linux VPS.

2. **Bộ Não AI Đối Thoại Tùy Biến (`core/ai_responder.py`)**:
   - **Khóa đại từ xưng hô xuyên kênh (`Sticky Pronoun Lock`)**: Tự động nhận diện cách khách xưng hô (`anh - em`, `em - anh`, `em - chị`, `em - bác`, `mình - bạn`) và giữ nhất quán trên cả Facebook lẫn Zalo.
   - **Nhường luồng cho người thật (`Human Takeover Guard`)**: Khi phát hiện chủ tài khoản đang trực tiếp nhảy vào chat tay với khách, bot tự động nhường luồng để không nhắn chồng.
   - **4 Mẫu Chiến dịch (Presets) tích hợp sẵn** trong thư mục `campaigns/` — bấm chọn 1 chạm ngay trên giao diện Web.

---

## 📁 Cấu trúc thư mục

```text
fb-zalo-auto-outreach-kdv/
├── main_engine.py                     # Bộ điều phối trung tâm (FB Worker + Zalo Worker + Drip Worker + Web API)
├── config.py                          # Quản lý cấu hình .env & Cầu nối Zalo Local/SSH
├── campaign_config.json               # Kịch bản chiến dịch đang chạy (chỉnh sửa trực tiếp trên Web hoặc JSON)
├── campaigns/                         # 4 bộ mẫu kịch bản dựng sẵn
│   ├── preset_1_sales_b2b.json        # Mẫu 1: Tư vấn Bán hàng B2B/B2C
│   ├── preset_2_partner_kol.json      # Mẫu 2: Mời Đối tác / Đại lý / KOL / KOC
│   ├── preset_3_customer_care.json    # Mẫu 3: Chăm sóc Khách hàng & Đặt lịch hẹn
│   └── preset_4_community_invite.json # Mẫu 4: Mời Quản trị Cộng đồng / Nhóm VIP
├── core/
│   ├── db.py                          # Quản trị SQLite WAL (contacts, interaction_logs, engine_config)
│   └── ai_responder.py                # Động cơ sinh tin nhắn mở đầu, khóa đại từ & trả lời AI theo FAQ
├── channels/
│   ├── fb_messenger_cdp.py            # Kênh tự động gửi & quét tin nhắn chưa đọc Facebook Messenger (CDP)
│   └── zalo_fast_lane.py              # Kênh tự động tìm SĐT, kết bạn, gửi & trả lời Zalo (zca-js)
├── zalo_bridge/
│   └── zalo_listener.mjs              # Trình lắng nghe tin nhắn Zalo thời gian thực (zca-js + node:sqlite)
├── dashboard/
│   └── index.html                     # Giao diện Web "Xưởng Thiết Kế Kịch Bản & Quản Lý Hội Thoại"
└── scripts/
    ├── 1_start_chrome_cdp.bat         # Mở Chrome CDP (cổng 9232, 9234, 9235)
    ├── 2_login_zalo_qr.mjs            # Quét mã QR đăng nhập Zalo & lưu credentials.json
    ├── 3_import_sample_candidates.py  # Khởi tạo DB & nạp danh bạ mẫu từ JSON
    └── 4_start_engine.bat             # Khởi chạy toàn bộ hệ thống & mở Web Studio
```

---

## ⚡ Hướng dẫn Cài đặt & Sử dụng trong 5 phút

```powershell
# 1. Clone repo & cài đặt thư viện (Yêu cầu Python 3.10+ và Node.js >= 22.5)
git clone https://github.com/vudanhdu2/fb-zalo-auto-outreach-kdv.git
cd fb-zalo-auto-outreach-kdv
pip install -r requirements.txt
playwright install chromium
npm install

# 2. Tạo file .env và điền AI_API_KEY của bạn
copy .env.example .env

# 3. Mở Chrome CDP (đăng nhập FB) & Quét QR đăng nhập Zalo
scripts\1_start_chrome_cdp.bat
node scripts/2_login_zalo_qr.mjs

# 4. Bật hệ thống & Mở giao diện Web tùy chỉnh kịch bản tại http://localhost:8768
scripts\4_start_engine.bat
```
