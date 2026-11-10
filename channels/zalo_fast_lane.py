# -*- coding: utf-8 -*-
"""
channels/zalo_fast_lane.py — Kênh Tự Động Nhắn Tin, Tìm SĐT, Kết Bạn & Phản Hồi Zalo (`zca-js` + SQLite)
Tương thích 100% với cả 2 chế độ:
  - `ZALO_BRIDGE_MODE="local"` (chạy Node.js `zca-js` trực tiếp trên máy tính cá nhân).
  - `ZALO_BRIDGE_MODE="ssh"`   (chạy Node.js `zca-js` trên máy chủ Linux VPS từ xa qua SSH).
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import config as app_cfg
from core import db as db_mod
from core import ai_responder


def outreach_zalo_by_phone_or_uid(
    contact: Dict[str, Any],
    message_text: str,
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Tìm tài khoản Zalo qua SĐT (nếu chưa có `zalo_uid`), gửi lời mời kết bạn kèm lời chào tùy biến
    và gửi tin nhắn mở đầu (kèm ảnh nếu có) qua `zca-js`.
    """
    t0 = time.time()
    phone = str(contact.get("phone") or "").strip()
    zalo_uid = str(contact.get("zalo_uid") or "").strip()
    if not phone and not zalo_uid:
        return {"ok": False, "status": "MISSING_PHONE_AND_UID", "detail": "Không có SĐT hoặc Zalo UID"}

    campaign_cfg = db_mod.load_campaign_config()
    auto_fr = bool(campaign_cfg.get("workflow_rules", {}).get("auto_friend_request_zalo", True))
    fr_greeting = ai_responder.generate_zalo_friend_greeting(contact, campaign_cfg)

    ssh = app_cfg.create_zalo_ssh_client()
    ssh.connect(app_cfg.VPS_HOST, port=app_cfg.VPS_PORT, username=app_cfg.VPS_USER, password=app_cfg.VPS_PASS, timeout=10)
    sftp = ssh.open_sftp()

    remote_img = ""
    if image_path and os.path.exists(image_path):
        remote_img = f"/opt/zalo_bot/outreach_{contact.get('contact_id', 'tmp')}.png"
        sftp.put(image_path, remote_img)

    payload = json.dumps(
        {
            "phone": phone,
            "uid": zalo_uid,
            "name": contact.get("name") or "Khách hàng",
            "msg": message_text,
            "img": remote_img,
            "autoFriendRequest": auto_fr,
            "friendGreeting": fr_greeting,
            "accountId": app_cfg.ZALO_ACCOUNT_ID,
            "senderId": app_cfg.ZALO_SENDER_ID,
            "senderName": campaign_cfg.get("sender_persona", {}).get("full_name") or app_cfg.ADMIN_FULL_NAME,
        },
        ensure_ascii=False,
    )

    mjs_code = f"""
import {{ Zalo, ThreadType }} from "zca-js";
import {{ DatabaseSync }} from "node:sqlite";
import fs from "node:fs";

const item = {payload};
const credPath = `/opt/zalo_bot/accounts/${{item.accountId}}/credentials.json`;
const cred = JSON.parse(fs.readFileSync(credPath, "utf-8"));
const zalo = new Zalo();
const api = await zalo.login(cred);
const db = new DatabaseSync("/opt/zalo_bot/zalo_messages.db");
db.exec("PRAGMA busy_timeout=30000");

let targetUid = String(item.uid || "").trim();
let zaloName = item.name;

if (!targetUid && item.phone) {{
  const found = await api.findUser(String(item.phone));
  targetUid = String(found?.uid || found?.userId || found?.id || "").trim();
  zaloName = String(found?.display_name || found?.zalo_name || found?.name || item.name);
}}

if (!targetUid) {{
  console.log("__ZALO_ERR__USER_NOT_FOUND");
  process.exit(0);
}}

if (item.autoFriendRequest) {{
  try {{
    await api.sendFriendRequest(item.friendGreeting, targetUid);
    await new Promise(r => setTimeout(r, 800));
  }} catch (e) {{}}
}}

let res = null;
if (item.img) {{
  res = await api.sendMessage({{ msg: item.msg, attachments: [item.img] }}, targetUid, ThreadType.User);
}} else {{
  res = await api.sendMessage({{ msg: item.msg }}, targetUid, ThreadType.User);
}}

const msgId = String(res?.message?.msgId || res?.msgId || "sent_ok");
const convId = `zalo_${{item.accountId}}_user_${{targetUid}}`;
const nowIso = new Date().toISOString();

db.prepare(`
  INSERT INTO conversations (id, account_id, thread_id, customer_id, customer_name, user_name, last_snippet, status, thread_type, updated_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, 'bot', 'user', ?)
  ON CONFLICT(id) DO UPDATE SET last_snippet=excluded.last_snippet, updated_at=excluded.updated_at
`).run(convId, item.accountId, targetUid, targetUid, zaloName, zaloName, item.msg, nowIso);

db.prepare(`
  INSERT OR IGNORE INTO messages (conversation_id, sender_id, sender_name, role, content, timestamp, zalo_msg_id, source)
  VALUES (?, ?, ?, 'assistant', ?, ?, ?, 'outreach')
`).run(convId, item.senderId, item.senderName, item.msg, nowIso, msgId);

console.log("__ZALO_OK__" + JSON.stringify({{ uid: targetUid, zaloName, msgId }}));
process.exit(0);
"""
    script_name = f"outreach_{contact.get('contact_id', 'tmp')}.mjs"
    with sftp.open(f"/opt/zalo_bot/{script_name}", "w") as rf:
        rf.write(mjs_code)
    sftp.close()

    _, stdout, stderr = ssh.exec_command(
        f"cd /opt/zalo_bot && node {script_name} && rm -f {script_name}",
        timeout=35,
    )
    out_txt = stdout.read().decode("utf-8", errors="replace")
    err_txt = stderr.read().decode("utf-8", errors="replace")
    ssh.close()

    if "__ZALO_OK__" in out_txt:
        info = json.loads(out_txt.split("__ZALO_OK__")[1].strip().splitlines()[0])
        return {
            "ok": True,
            "status": "SENT",
            "zalo_uid": info.get("uid", zalo_uid),
            "zalo_name": info.get("zaloName", ""),
            "msg_id": info.get("msgId", ""),
            "latency_ms": int((time.time() - t0) * 1000),
        }
    return {"ok": False, "status": "ERROR", "detail": out_txt or err_txt}


class ZaloFastLaneWorker(threading.Thread):
    """
    Luồng phản hồi siêu tốc Zalo (`ZaloFastLaneWorker`):
    Quét `zalo_messages.db` mỗi 2.5 giây (< 0.05s/lần nhờ Index SQLite tối ưu),
    tự động đối thoại theo `campaign_config.json` và thả tim ❤️ tự nhiên.
    """

    def __init__(self, poll_interval_sec: float = 2.5):
        super().__init__(daemon=True, name="ZaloFastLaneWorker")
        self.poll_interval_sec = poll_interval_sec
        self.ssh = None
        self.sftp = None

    def _ensure_conn(self):
        if self.ssh is None or self.ssh.get_transport() is None or not self.ssh.get_transport().is_active():
            self.ssh = app_cfg.create_zalo_ssh_client()
            self.ssh.connect(
                app_cfg.VPS_HOST,
                port=app_cfg.VPS_PORT,
                username=app_cfg.VPS_USER,
                password=app_cfg.VPS_PASS,
                timeout=10,
            )
            self.sftp = self.ssh.open_sftp()

    def run(self):
        print(f"🟢 [ZaloFastLane] Đang lắng nghe tin nhắn Zalo thời gian thực (chế độ: {app_cfg.ZALO_BRIDGE_MODE})...")
        db_mod.set_config("zalo_fast_lane_status", "RUNNING")
        while True:
            try:
                if db_mod.is_kill_switch_on() or db_mod.is_quiet_sleeping_hours():
                    time.sleep(5)
                    continue
                self._ensure_conn()
                self._poll_once()
            except Exception as e:
                print(f"[ZaloFastLane Note]: {e}")
                self.ssh = None
                time.sleep(3)
            time.sleep(self.poll_interval_sec)

    def _poll_once(self) -> int:
        t0 = time.time()
        conn = db_mod.get_conn()
        contacts = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contacts WHERE zalo_uid != '' AND stage NOT IN ('DECLINED', 'PAUSED_MANUAL')"
            ).fetchall()
        ]
        conn.close()

        if not contacts:
            db_mod.set_config("last_zalo_scan_ms", str(int((time.time() - t0) * 1000)))
            return 0

        by_zuid = {str(c["zalo_uid"]).strip(): c for c in contacts if c.get("zalo_uid")}
        uids_json = json.dumps(list(by_zuid.keys()))

        query_py = (
            "import sqlite3, json; "
            "con = sqlite3.connect('/opt/zalo_bot/zalo_messages.db', timeout=10); "
            "con.row_factory = sqlite3.Row; "
            f"uids = {uids_json}; "
            f"q = f\"SELECT c.thread_id AS user_id, m.role, m.content, m.timestamp, m.id, m.zalo_msg_id FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE c.account_id='{app_cfg.ZALO_ACCOUNT_ID}' AND (c.thread_type='user' OR c.thread_type IS NULL) AND c.thread_id IN ({{','.join(['?']*len(uids))}}) ORDER BY m.id ASC\"; "
            "rows = [dict(r) for r in con.execute(q, uids).fetchall()]; "
            "print('__JSON_START__' + json.dumps(rows, ensure_ascii=False))"
        )
        _, stdout, _ = self.ssh.exec_command(f"python3 -c {json.dumps(query_py)}", timeout=15)
        out = stdout.read().decode("utf-8", errors="replace")
        scan_ms = int((time.time() - t0) * 1000)
        db_mod.set_config("last_zalo_scan_ms", str(scan_ms))
        db_mod.set_config("zalo_fast_lane_status", "RUNNING")

        if "__JSON_START__" not in out:
            return 0

        rows = json.loads(out.split("__JSON_START__")[1].strip())
        msgs_by_uid: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            msgs_by_uid.setdefault(str(r["user_id"]), []).append(r)

        replied_count = 0
        campaign_cfg = db_mod.load_campaign_config()
        auto_heart = bool(campaign_cfg.get("workflow_rules", {}).get("auto_heart_reaction", True))
        sender_name = campaign_cfg.get("sender_persona", {}).get("full_name") or app_cfg.ADMIN_FULL_NAME

        for zuid, cand in by_zuid.items():
            msgs = msgs_by_uid.get(zuid, [])
            if not msgs or msgs[-1].get("role") != "user":
                continue

            history = [
                {"role": m["role"], "content": (m.get("content") or "").strip()}
                for m in msgs
                if (m.get("content") or "").strip()
            ]
            if not history or history[-1]["role"] != "user":
                continue

            user_concat = " | ".join(m["content"] for m in history if m["role"] == "user")
            if (cand.get("last_user_msg") or "") == user_concat:
                continue

            latest_user = history[-1]["content"]
            last_z_mid = str(msgs[-1].get("zalo_msg_id") or "").strip()

            t_gen = time.time()
            res = ai_responder.classify_and_generate_reply(
                "Zalo",
                cand,
                history,
                latest_unanswered_text=latest_user,
            )
            ai_reply = (res.get("reply") or "").strip()
            intent = res.get("intent") or "ASKING_INFO"

            if intent == "NO_REPLY_NEEDED" or not ai_reply:
                with db_mod.db_lock:
                    c_db = db_mod.get_conn()
                    c_db.execute(
                        "UPDATE contacts SET last_user_msg=?, updated_at=? WHERE contact_id=?",
                        (user_concat, time.strftime("%Y-%m-%d %H:%M:%S"), cand["contact_id"]),
                    )
                    c_db.commit()
                    c_db.close()
                continue

            send_payload = json.dumps(
                {
                    "uid": zuid,
                    "reply": ai_reply,
                    "targetMsgId": last_z_mid if auto_heart else "",
                    "accountId": app_cfg.ZALO_ACCOUNT_ID,
                    "senderId": app_cfg.ZALO_SENDER_ID,
                    "senderName": sender_name,
                },
                ensure_ascii=False,
            )

            send_mjs = f"""
import {{ Zalo, ThreadType, Reactions }} from "zca-js";
import {{ DatabaseSync }} from "node:sqlite";
import fs from "node:fs";

const item = {send_payload};
const cred = JSON.parse(fs.readFileSync(`/opt/zalo_bot/accounts/${{item.accountId}}/credentials.json`, "utf-8"));
const zalo = new Zalo();
const api = await zalo.login(cred);
const db = new DatabaseSync("/opt/zalo_bot/zalo_messages.db");
db.exec("PRAGMA busy_timeout=30000");

if (item.targetMsgId) {{
  try {{
    await api.addReaction(Reactions.HEART, {{
      data: {{ msgId: String(item.targetMsgId), cliMsgId: String(item.targetMsgId) }},
      threadId: item.uid,
      type: ThreadType.User,
    }});
  }} catch (e) {{}}
}}

const res = await api.sendMessage({{ msg: item.reply }}, item.uid, ThreadType.User);
const msgId = String(res?.message?.msgId || res?.msgId || "sent_ok");
const convId = `zalo_${{item.accountId}}_user_${{item.uid}}`;
const nowIso = new Date().toISOString();

db.prepare(`
  INSERT OR IGNORE INTO messages (conversation_id, sender_id, sender_name, role, content, timestamp, zalo_msg_id, source)
  VALUES (?, ?, ?, 'assistant', ?, ?, ?, 'fast_lane')
`).run(convId, item.senderId, item.senderName, item.reply, nowIso, msgId);

console.log("__SENT_OK__" + msgId);
process.exit(0);
"""
            with self.sftp.open("/opt/zalo_bot/fast_reply_single.mjs", "w") as rf:
                rf.write(send_mjs)
            _, s_out, _ = self.ssh.exec_command(
                "cd /opt/zalo_bot && node fast_reply_single.mjs && rm -f fast_reply_single.mjs",
                timeout=25,
            )
            s_txt = s_out.read().decode("utf-8", errors="replace")
            if "__SENT_OK__" in s_txt:
                msg_id = s_txt.split("__SENT_OK__")[1].strip().splitlines()[0]
                new_stage = cand.get("stage") or "REPLIED_ASKING"
                if intent == "INTERESTED_AGREED":
                    new_stage = "INTERESTED_AGREED"
                elif intent == "DECLINED":
                    new_stage = "DECLINED"
                elif new_stage in ("QUEUED", "CONTACTED"):
                    new_stage = "REPLIED_ASKING"

                with db_mod.db_lock:
                    c_db = db_mod.get_conn()
                    c_db.execute(
                        """
                        UPDATE contacts
                        SET stage=?, last_user_msg=?, last_ai_reply=?, last_channel='Zalo', updated_at=?
                        WHERE contact_id=?
                        """,
                        (new_stage, user_concat, ai_reply, time.strftime("%Y-%m-%d %H:%M:%S"), cand["contact_id"]),
                    )
                    c_db.commit()
                    c_db.close()

                latency_ms = int((time.time() - t_gen) * 1000)
                db_mod.log_interaction(
                    channel="Zalo Fast-Lane",
                    contact=cand,
                    user_msg=latest_user,
                    ai_reply=ai_reply,
                    intent=intent,
                    latency_ms=latency_ms,
                    msg_id=msg_id,
                    status="sent_verified",
                )
                print(f"⚡ [ZALO FAST-LANE {latency_ms}ms] Đã phản hồi {cand['name']} (msgId={msg_id}, intent={intent})")
                replied_count += 1

        return replied_count
