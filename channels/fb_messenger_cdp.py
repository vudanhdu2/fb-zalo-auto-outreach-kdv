# -*- coding: utf-8 -*-
"""
channels/fb_messenger_cdp.py — Kênh Tự Động Nhắn Tin & Phản Hồi Facebook Messenger (Playwright Chrome CDP)
Kết nối trực tiếp vào trình duyệt Chrome đang đăng nhập sẵn (mặc định cổng 9232):
  1. Gửi tin nhắn mở đầu (Outreach) tới bất kỳ `fb_uid` nào với nhịp gõ tự nhiên như người thật.
  2. Tự động phát hiện tài khoản chặn tin nhắn người lạ (`UNREACHABLE`) để chuyển sang kênh Zalo.
  3. Quét hộp thư chưa đọc (`Unread Scanner`) mỗi 5 giây và phản hồi tự động theo `campaign_config.json`.
"""

import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional
from playwright.sync_api import sync_playwright

import config as app_cfg
from core import db as db_mod
from core import ai_responder


def _human_type_delay_ms() -> int:
    return random.randint(18, 42)


def send_fb_message(
    fb_uid: str,
    message_text: str,
    image_path: Optional[str] = None,
    cdp_port: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Gửi tin nhắn Facebook Messenger tới `fb_uid` qua cổng Chrome CDP.
    Trả về: `{"ok": bool, "status": "SENT" | "UNREACHABLE" | "ERROR", "detail": str}`
    """
    port = cdp_port or app_cfg.CDP_MAIN_PORT
    t0 = time.time()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10000)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                url = f"https://www.facebook.com/messages/t/{fb_uid}"
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)

                body_txt = (page.locator("body").inner_text(timeout=5000) or "").lower()
                unreachable_markers = [
                    "không thể gửi tin nhắn cho tài khoản này",
                    "you can't message this account",
                    "hiện không nhận tin nhắn",
                    "unable to message",
                ]
                if any(m in body_txt for m in unreachable_markers):
                    return {"ok": False, "status": "UNREACHABLE", "detail": "Khách chặn tin nhắn từ người lạ trên FB"}

                box = page.locator('div[role="textbox"][contenteditable="true"]').last
                if box.count() == 0:
                    return {"ok": False, "status": "UNREACHABLE", "detail": "Không tìm thấy ô nhập tin nhắn Messenger"}

                box.click(timeout=5000)
                page.wait_for_timeout(400)

                if image_path and os.path.exists(image_path):
                    file_inputs = page.locator('input[type="file"]')
                    if file_inputs.count() > 0:
                        file_inputs.first.set_input_files(image_path)
                        page.wait_for_timeout(1500)

                box.fill(message_text)
                page.wait_for_timeout(500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)
                latency = int((time.time() - t0) * 1000)
                return {"ok": True, "status": "SENT", "latency_ms": latency, "detail": "Đã gửi thành công qua FB Messenger"}
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    except Exception as e:
        return {"ok": False, "status": "ERROR", "detail": str(e)}


class FBMessengerScannerWorker(threading.Thread):
    """
    Luồng quét hộp thư chưa đọc trên Facebook Messenger (`Port 9232`) và tự động trả lời theo `campaign_config.json`.
    """

    def __init__(self, poll_interval_sec: float = 5.0):
        super().__init__(daemon=True, name="FBMessengerScannerWorker")
        self.poll_interval_sec = poll_interval_sec

    def run(self):
        print(f"🔵 [FBMessengerScanner] Đang lắng nghe hộp thư Facebook Messenger trên cổng CDP {app_cfg.CDP_MAIN_PORT}...")
        db_mod.set_config("fb_scanner_status", "RUNNING")
        while True:
            try:
                if db_mod.is_kill_switch_on() or db_mod.is_quiet_sleeping_hours():
                    time.sleep(10)
                    continue
                t0 = time.time()
                self._scan_once()
                scan_ms = int((time.time() - t0) * 1000)
                db_mod.set_config("last_fb_scan_ms", str(scan_ms))
                db_mod.set_config("fb_scanner_status", "RUNNING")
            except Exception as e:
                db_mod.set_config("fb_scanner_status", f"WAIT_CHROME_CDP ({app_cfg.CDP_MAIN_PORT})")
            time.sleep(self.poll_interval_sec)

    def _scan_once(self) -> int:
        conn = db_mod.get_conn()
        contacts = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contacts WHERE fb_uid != '' AND stage IN ('CONTACTED', 'REPLIED_ASKING', 'INTERESTED_AGREED', 'CONVERTED')"
            ).fetchall()
        ]
        conn.close()
        if not contacts:
            return 0

        by_fb_uid = {str(c["fb_uid"]).strip(): c for c in contacts if c.get("fb_uid")}
        replied = 0

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{app_cfg.CDP_MAIN_PORT}", timeout=6000)
            ctx = browser.contexts[0] if browser.contexts else None
            if not ctx:
                return 0
            # Tìm tab Messenger đang mở sẵn
            msg_page = None
            for pg in ctx.pages:
                if "facebook.com/messages" in (pg.url or ""):
                    msg_page = pg
                    break
            if not msg_page:
                return 0

            # Kiểm tra các đường link hội thoại có dấu chấm chưa đọc
            unread_links = msg_page.locator('a[href*="/messages/t/"]').all()
            for link in unread_links[:15]:
                try:
                    href = link.get_attribute("href") or ""
                    m = re.search(r"/messages/t/(\d+)", href)
                    if not m:
                        continue
                    uid = m.group(1)
                    if uid not in by_fb_uid:
                        continue
                    aria = (link.get_attribute("aria-label") or "").lower()
                    if not any(u in aria for u in ("chưa đọc", "unread")):
                        continue

                    cand = by_fb_uid[uid]
                    link.click(timeout=3000)
                    msg_page.wait_for_timeout(1500)

                    # Đọc các bong bóng tin nhắn gần nhất trong khung chat
                    rows = msg_page.locator('div[role="row"]').all()
                    history: List[Dict[str, str]] = []
                    for r in rows[-12:]:
                        txt = (r.inner_text() or "").strip()
                        if not txt:
                            continue
                        is_self = "bạn đã gửi" in txt.lower() or "you sent" in txt.lower()
                        clean_t = re.sub(r"^(Bạn đã gửi|You sent)\s*", "", txt, flags=re.I).strip()
                        if clean_t:
                            history.append({"role": "assistant" if is_self else "user", "content": clean_t})

                    if not history or history[-1]["role"] != "user":
                        continue

                    user_concat = " | ".join(m["content"] for m in history if m["role"] == "user")
                    if (cand.get("last_user_msg") or "") == user_concat:
                        continue

                    latest_user = history[-1]["content"]
                    t_gen = time.time()
                    res = ai_responder.classify_and_generate_reply(
                        "Facebook Messenger",
                        cand,
                        history,
                        latest_unanswered_text=latest_user,
                    )
                    ai_reply = (res.get("reply") or "").strip()
                    intent = res.get("intent") or "ASKING_INFO"

                    if intent == "NO_REPLY_NEEDED" or not ai_reply:
                        continue

                    box = msg_page.locator('div[role="textbox"][contenteditable="true"]').last
                    if box.count() > 0:
                        box.click(timeout=3000)
                        box.fill(ai_reply)
                        msg_page.wait_for_timeout(450)
                        msg_page.keyboard.press("Enter")
                        msg_page.wait_for_timeout(1200)

                        new_stage = cand.get("stage") or "REPLIED_ASKING"
                        if intent == "INTERESTED_AGREED":
                            new_stage = "INTERESTED_AGREED"
                        elif intent == "DECLINED":
                            new_stage = "DECLINED"
                        elif new_stage == "CONTACTED":
                            new_stage = "REPLIED_ASKING"

                        with db_mod.db_lock:
                            c_db = db_mod.get_conn()
                            c_db.execute(
                                """
                                UPDATE contacts
                                SET stage=?, last_user_msg=?, last_ai_reply=?, last_channel='Facebook Messenger', updated_at=?
                                WHERE contact_id=?
                                """,
                                (new_stage, user_concat, ai_reply, time.strftime("%Y-%m-%d %H:%M:%S"), cand["contact_id"]),
                            )
                            c_db.commit()
                            c_db.close()

                        latency_ms = int((time.time() - t_gen) * 1000)
                        db_mod.log_interaction(
                            channel="Facebook Messenger",
                            contact=cand,
                            user_msg=latest_user,
                            ai_reply=ai_reply,
                            intent=intent,
                            latency_ms=latency_ms,
                            status="sent_verified",
                        )
                        print(f"⚡ [FB FAST-SCANNER {latency_ms}ms] Đã phản hồi {cand['name']} (intent={intent})")
                        replied += 1
                except Exception:
                    continue
        return replied
