/**
 * ĐĂNG NHẬP ZALO BẰNG MÃ QR & LƯU CREDENTIALS.JSON
 * Chạy lệnh: node scripts/2_login_zalo_qr.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Zalo } from "zca-js";
import { initZaloDb } from "../zalo_bridge/zalo_listener.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");

const ACCOUNT_ID = process.env.ZALO_ACCOUNT_ID || "acc_default";
const ACC_DIR = path.join(ROOT_DIR, "zalo_bridge", "accounts", ACCOUNT_ID);
const QR_PATH = path.join(ROOT_DIR, "zalo_qr.png");

fs.mkdirSync(ACC_DIR, { recursive: true });
initZaloDb(path.join(ROOT_DIR, "zalo_bridge", "zalo_messages.db"));

console.log("=================================================================");
console.log("📱 ĐĂNG NHẬP ZALO QUA MÃ QR (zca-js)");
console.log(`📂 Thư mục lưu phiên: ${ACC_DIR}`);
console.log(`🖼️ Ảnh mã QR sẽ được tạo tại: ${QR_PATH}`);
console.log("👉 Mở ứng dụng Zalo trên điện thoại và quét mã QR khi ảnh hiện ra...");
console.log("=================================================================");

const zalo = new Zalo({ selfListen: true });
const api = await zalo.loginQR({
  qrPath: QR_PATH,
});

const cred = {
  imei: api.getContext().imei,
  cookie: api.getCookie().toJSON ? api.getCookie().toJSON() : api.getCookie(),
  userAgent: api.getContext().userAgent,
  language: api.getContext().language || "vi",
};

const credPath = path.join(ACC_DIR, "credentials.json");
fs.writeFileSync(credPath, JSON.stringify(cred, null, 2), "utf-8");
console.log(`✅ Đã lưu phiên đăng nhập Zalo thành công tại: ${credPath}`);
process.exit(0);
