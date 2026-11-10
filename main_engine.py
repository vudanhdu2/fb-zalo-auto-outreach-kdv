# -*- coding: utf-8 -*-
"""
main_engine.py — Bộ Điều Phối Trung Tâm Tự Động Nhắn Tin & Đối Thoại AI Đa Kênh (Facebook Messenger + Zalo)
Hỗ trợ tùy chỉnh 100% mục đích chiến dịch qua `campaign_config.json` hoặc trực tiếp trên Giao diện Web (Cổng 8768):
  - Luồng 1 (`FBMessengerScannerWorker`): Quét tin nhắn chưa đọc trên Facebook Messenger & phản hồi tự động.
  - Luồng 2 (`ZaloFastLaneWorker`): Quét tin nhắn Zalo thời gian thực (`zalo_messages.db`) & phản hồi siêu tốc.
  - Luồng 3 (`OutreachDripWorker`): Gửi tin nhắn mở đầu (Outreach) theo nhịp an toàn (FB trước -> tự động cứu hộ qua Zalo nếu khách chặn tin nhắn người lạ).
  - Luồng 4 (`DashboardAPIServer`): Máy chủ Web & REST API tại `http://localhost:8768/dashboard/index.html` cho phép chỉnh sửa kịch bản, nạp danh sách khách hàng và bật/tắt chiến dịch trực tiếp.
"""

import argparse
import glob
import json
import os
import random
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config as app_cfg
from core import db as db_mod
from core import ai_responder
from channels.fb_messenger_cdp import FBMessengerScannerWorker, send_fb_message
from channels.zalo_fast_lane import ZaloFastLaneWorker, outreach_zalo_by_phone_or_uid


class OutreachDripWorker(threading.Thread):
    """
    Luồng gửi tin nhắn mở đầu (Outreach Drip) có điều tiết nhịp độ an toàn (Pacer):
    Tự động chọn kênh theo `workflow_rules.channel_routing`:
      - `fb_and_zalo_smart`: Ưu tiên gửi qua FB Messenger (nếu có `fb_uid`), nếu khách chặn tin nhắn lạ hoặc chỉ có SĐT thì tự động tìm Zalo & kết bạn + gửi tin qua Zalo!
      - `zalo_only`: Chỉ gửi qua Zalo.
      - `fb_only`: Chỉ gửi qua Facebook Messenger.
    """

    def __init__(self):
        super().__init__(daemon=True, name="OutreachDripWorker")

    def run(self):
        print("🚀 [OutreachDripWorker] Đã khởi động bộ điều tiết gửi tin mở đầu (FB + Zalo Smart Routing)...")
        db_mod.set_config("outreach_worker_status", "RUNNING")
        while True:
            try:
                if db_mod.is_kill_switch_on():
                    db_mod.set_config("outreach_worker_status", "PAUSED_KILL_SWITCH")
                    time.sleep(10)
                    continue
                if db_mod.is_new_outreach_paused():
                    db_mod.set_config("outreach_worker_status", "PAUSED_NEW_OUTREACH")
                    time.sleep(10)
                    continue
                if db_mod.is_quiet_sleeping_hours():
                    db_mod.set_config("outreach_worker_status", "QUIET_SLEEPING_HOURS")
                    time.sleep(30)
                    continue

                db_mod.set_config("outreach_worker_status", "RUNNING")
                did_send = self._drip_one()
                cfg = db_mod.load_campaign_config()
                rules = cfg.get("workflow_rules", {})
                min_d = int(rules.get("min_delay_between_outreach_sec", 90))
                max_d = int(rules.get("max_delay_between_outreach_sec", 180))
                wait_s = random.randint(min_d, max(min_d + 5, max_d)) if did_send else 15
                time.sleep(wait_s)
            except Exception as e:
                print(f"[OutreachDripWorker Error]: {e}")
                time.sleep(15)

    def _drip_one(self) -> bool:
        conn = db_mod.get_conn()
        row = conn.execute(
            "SELECT * FROM contacts WHERE stage='QUEUED' ORDER BY updated_at ASC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return False

        contact = dict(row)
        cfg = db_mod.load_campaign_config()
        routing = (cfg.get("workflow_rules", {}).get("channel_routing") or "fb_and_zalo_smart").lower()
        msg_text = ai_responder.generate_outreach_message(contact, cfg)

        sent_ok = False
        used_channel = ""

        # 1. Thử gửi qua Facebook Messenger nếu có fb_uid và cấu hình cho phép
        if routing in ("fb_and_zalo_smart", "fb_only") and contact.get("fb_uid"):
            res_fb = send_fb_message(contact["fb_uid"], msg_text)
            if res_fb.get("ok"):
                sent_ok = True
                used_channel = "Facebook Messenger"
                db_mod.log_interaction(
                    channel="FB Outreach",
                    contact=contact,
                    user_msg="[Gửi lời chào mở đầu]",
                    ai_reply=msg_text,
                    intent="OUTREACH_SENT",
                    latency_ms=res_fb.get("latency_ms", 0),
                    status="sent_verified",
                )

        # 2. Nếu FB không gửi được (hoặc chế độ zalo_only) và khách có SĐT / zalo_uid -> Gửi qua Zalo!
        if not sent_ok and routing in ("fb_and_zalo_smart", "zalo_only") and (contact.get("phone") or contact.get("zalo_uid")):
            res_zl = outreach_zalo_by_phone_or_uid(contact, msg_text)
            if res_zl.get("ok"):
                sent_ok = True
                used_channel = "Zalo"
                contact["zalo_uid"] = res_zl.get("zalo_uid") or contact.get("zalo_uid", "")
                contact["zalo_name"] = res_zl.get("zalo_name") or contact.get("zalo_name", "")
                db_mod.log_interaction(
                    channel="Zalo Outreach",
                    contact=contact,
                    user_msg="[Gửi lời chào mở đầu Zalo]",
                    ai_reply=msg_text,
                    intent="OUTREACH_SENT",
                    latency_ms=res_zl.get("latency_ms", 0),
                    msg_id=res_zl.get("msg_id", ""),
                    status="sent_verified",
                )

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        with db_mod.db_lock:
            conn = db_mod.get_conn()
            if sent_ok:
                conn.execute(
                    """
                    UPDATE contacts
                    SET stage='CONTACTED', initial_msg=?, last_ai_reply=?, last_channel=?,
                        zalo_uid=?, zalo_name=?, updated_at=?
                    WHERE contact_id=?
                    """,
                    (
                        msg_text,
                        msg_text,
                        used_channel,
                        contact.get("zalo_uid", ""),
                        contact.get("zalo_name", ""),
                        now_str,
                        contact["contact_id"],
                    ),
                )
            else:
                conn.execute(
                    "UPDATE contacts SET stage='UNREACHABLE', updated_at=? WHERE contact_id=?",
                    (now_str, contact["contact_id"]),
                )
            conn.commit()
            conn.close()
        db_mod.export_live_snapshot()
        return sent_ok


class DashboardAPIHandler(SimpleHTTPRequestHandler):
    """Phục vụ giao diện Web Dashboard (`dashboard/index.html`) và các API cấu hình chiến dịch trực tiếp."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(app_cfg.BASE_DIR), **kwargs)

    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.path = "/dashboard/index.html"
            return super().do_GET()

        if parsed.path == "/api/status":
            snap = db_mod.export_live_snapshot()
            presets = []
            for pf in sorted((app_cfg.BASE_DIR / "campaigns").glob("*.json")):
                try:
                    p_obj = json.loads(pf.read_text(encoding="utf-8"))
                    presets.append({
                        "file": pf.name,
                        "campaign_id": p_obj.get("campaign_id", pf.stem),
                        "campaign_name": p_obj.get("campaign_name", pf.stem),
                        "campaign_goal": p_obj.get("campaign_goal", ""),
                        "config": p_obj,
                    })
                except Exception:
                    pass
            snap["full_campaign_config"] = db_mod.load_campaign_config()
            snap["presets"] = presets
            return self._send_json(snap)

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        if parsed.path == "/api/campaign":
            # Lưu cấu hình chiến dịch tùy chỉnh mới hoặc áp dụng Preset
            if "preset_file" in body:
                p_path = app_cfg.BASE_DIR / "campaigns" / os.path.basename(body["preset_file"])
                if p_path.exists():
                    cfg_data = json.loads(p_path.read_text(encoding="utf-8"))
                    db_mod.save_campaign_config(cfg_data)
                    return self._send_json({"ok": True, "message": f"Đã nạp mẫu chiến dịch: {cfg_data.get('campaign_name')}"})
            elif "config" in body and isinstance(body["config"], dict):
                db_mod.save_campaign_config(body["config"])
                return self._send_json({"ok": True, "message": "Đã lưu cấu hình chiến dịch thành công!"})
            return self._send_json({"ok": False, "error": "Dữ liệu không hợp lệ"}, 400)

        if parsed.path == "/api/toggle":
            key = body.get("key", "")
            val = body.get("value", "")
            if key in ("pause_new_outreach", "kill_switch"):
                db_mod.set_config(key, val)
                return self._send_json({"ok": True, "key": key, "value": val})
            return self._send_json({"ok": False, "error": "Khóa cấu hình không hợp lệ"}, 400)

        if parsed.path == "/api/contacts":
            items = body.get("contacts") or []
            if isinstance(items, dict):
                items = [items]
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            added = 0
            with db_mod.db_lock:
                conn = db_mod.get_conn()
                cur = conn.cursor()
                for c in items:
                    cid = str(c.get("contact_id") or c.get("fb_uid") or c.get("phone") or c.get("zalo_uid") or f"c_{int(time.time()*1000)}_{added}").strip()
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO contacts (
                            contact_id, name, fb_uid, zalo_uid, zalo_name, phone,
                            context_tag, custom_note_1, custom_note_2, stage, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cid,
                            c.get("name") or c.get("fb_name") or "Khách hàng",
                            str(c.get("fb_uid") or "").strip(),
                            str(c.get("zalo_uid") or "").strip(),
                            str(c.get("zalo_name") or "").strip(),
                            str(c.get("phone") or "").strip(),
                            str(c.get("context_tag") or c.get("group_name") or "").strip(),
                            str(c.get("custom_note_1") or "").strip(),
                            str(c.get("custom_note_2") or "").strip(),
                            str(c.get("stage") or "QUEUED").strip(),
                            now_str,
                        ),
                    )
                    added += 1
                conn.commit()
                conn.close()
            return self._send_json({"ok": True, "added": added})

        if parsed.path == "/api/preview_message":
            sample_contact = body.get("contact") or {
                "contact_id": "preview_01",
                "name": "Trần Minh Tuấn",
                "context_tag": "Giải pháp Quản trị & Bán hàng Đa kênh",
                "phone": "0987654321",
            }
            preview_cfg = body.get("config") or db_mod.load_campaign_config()
            outreach_txt = ai_responder.generate_outreach_message(sample_contact, preview_cfg)
            sample_q = body.get("sample_question") or "Chi phí bên mình như thế nào và làm sao để dùng thử vậy bạn?"
            ai_res = ai_responder.classify_and_generate_reply(
                "Zalo",
                sample_contact,
                [{"role": "assistant", "content": outreach_txt}, {"role": "user", "content": sample_q}],
                latest_unanswered_text=sample_q,
            )
            return self._send_json({
                "ok": True,
                "outreach_preview": outreach_txt,
                "sample_question": sample_q,
                "ai_reply_preview": ai_res.get("reply", ""),
                "intent": ai_res.get("intent", ""),
            })

        return self._send_json({"ok": False, "error": "Not found"}, 404)


def run_server_and_workers(port: int = 8768, web_only: bool = False):
    db_mod.init_db()
    db_mod.export_live_snapshot()

    if not web_only:
        fb_worker = FBMessengerScannerWorker(poll_interval_sec=5.0)
        zalo_worker = ZaloFastLaneWorker(poll_interval_sec=2.5)
        drip_worker = OutreachDripWorker()
        fb_worker.start()
        zalo_worker.start()
        drip_worker.start()

        def _snapshot_loop():
            while True:
                try:
                    db_mod.export_live_snapshot()
                except Exception:
                    pass
                time.sleep(3)

        threading.Thread(target=_snapshot_loop, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardAPIHandler)
    print("==========================================================================")
    print(f"✅ HỆ THỐNG TỰ ĐỘNG NHẮN TIN & ĐỐI THOẠI ĐA KÊNH (FB + ZALO) ĐÃ KHỞI ĐỘNG")
    print(f"🌐 Bảng Điều Hành & Tùy Chỉnh Chiến Dịch: http://localhost:{port}/dashboard/index.html")
    print("==========================================================================")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FB + Zalo Customizable Auto-Outreach & AI Responder Engine")
    parser.add_argument("--port", type=int, default=8768, help="Cổng Web Dashboard & API (mặc định: 8768)")
    parser.add_argument("--web-only", action="store_true", help="Chỉ mở giao diện Web cấu hình, chưa bật worker gửi tin")
    parser.add_argument("--daemon", action="store_true", help="Chạy đầy đủ các luồng FB + Zalo + Web Dashboard")
    args = parser.parse_args()
    run_server_and_workers(port=args.port, web_only=args.web_only)
