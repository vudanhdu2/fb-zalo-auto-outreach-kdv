# -*- coding: utf-8 -*-
"""
core/db.py — Quản trị Cơ sở dữ liệu SQLite WAL Đa Kênh (Facebook Messenger + Zalo)
Thiết kế tổng quát (Purpose-Agnostic), dùng được cho mọi mục đích chiến dịch:
Bán hàng (Sales/Lead), Chăm sóc khách hàng (CSKH), Mời đối tác/KOL, hoặc Phát triển cộng đồng.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config as app_cfg

DB_PATH = str(app_cfg.DATA_DIR / "outreach_engine.db")
CAMPAIGN_CONFIG_PATH = str(app_cfg.BASE_DIR / "campaign_config.json")
db_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def load_campaign_config(custom_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(custom_path or CAMPAIGN_CONFIG_PATH)
    if not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Đồng bộ thông tin từ .env nếu người dùng đã cấu hình trong .env
    persona = cfg.setdefault("sender_persona", {})
    if os.getenv("ADMIN_FULL_NAME"):
        persona["full_name"] = app_cfg.ADMIN_FULL_NAME
    if os.getenv("ADMIN_SHORT_NAME"):
        persona["short_name"] = app_cfg.ADMIN_SHORT_NAME
    if os.getenv("ADMIN_ZALO_PHONE"):
        persona["zalo_phone"] = app_cfg.ADMIN_ZALO_PHONE
    return cfg


def save_campaign_config(cfg_data: Dict[str, Any], custom_path: Optional[str] = None) -> None:
    path = Path(custom_path or CAMPAIGN_CONFIG_PATH)
    path.write_text(json.dumps(cfg_data, ensure_ascii=False, indent=2), encoding="utf-8")


def init_db() -> None:
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()

        # 1. Bảng cấu hình vận hành thời gian thực & Kill-Switch
        cur.execute("""
        CREATE TABLE IF NOT EXISTS engine_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
        """)

        # 2. Bảng Danh bạ Khách hàng / Đối tác đa kênh (Contacts)
        # Stages: QUEUED -> CONTACTED -> REPLIED_ASKING -> INTERESTED_AGREED -> CONVERTED | DECLINED | UNREACHABLE | PAUSED_MANUAL
        cur.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            contact_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            fb_uid TEXT DEFAULT '',
            zalo_uid TEXT DEFAULT '',
            zalo_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            context_tag TEXT DEFAULT '',
            custom_note_1 TEXT DEFAULT '',
            custom_note_2 TEXT DEFAULT '',
            pronoun_pair TEXT DEFAULT '',
            stage TEXT NOT NULL DEFAULT 'QUEUED',
            initial_msg TEXT DEFAULT '',
            last_user_msg TEXT DEFAULT '',
            last_ai_reply TEXT DEFAULT '',
            last_channel TEXT DEFAULT '',
            attachment_sent_fb INTEGER DEFAULT 0,
            attachment_sent_zalo INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """)

        # 3. Bảng Nhật ký Tương tác & Độ trễ (Interaction Logs)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS interaction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            channel TEXT NOT NULL,
            contact_id TEXT,
            contact_name TEXT,
            context_tag TEXT,
            user_msg TEXT,
            ai_reply TEXT,
            intent TEXT,
            latency_ms INTEGER DEFAULT 0,
            msg_id TEXT,
            status TEXT
        )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS ix_contacts_stage ON contacts(stage)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_contacts_fb_uid ON contacts(fb_uid)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_contacts_zalo_uid ON contacts(zalo_uid)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_logs_contact ON interaction_logs(contact_id, id DESC)")

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "kill_switch": "OFF",
            "pause_new_outreach": os.getenv("PAUSE_NEW_OUTREACH", "OFF"),
            "zalo_poll_interval_sec": "2.5",
            "fb_unread_scan_interval_sec": "5.0",
            "outreach_drip_interval_sec": "120",
            "fb_scanner_status": "IDLE",
            "zalo_fast_lane_status": "IDLE",
            "outreach_worker_status": "IDLE",
        }
        for k, v in defaults.items():
            cur.execute(
                "INSERT OR IGNORE INTO engine_config (key, value, updated_at) VALUES (?, ?, ?)",
                (k, str(v), now_str),
            )

        conn.commit()
        conn.close()


def set_config(key: str, value: str) -> None:
    with db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO engine_config (key, value, updated_at) VALUES (?, ?, ?)",
            (key, str(value), time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()


def get_config(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM engine_config WHERE key=?", (key,)).fetchone()
    conn.close()
    return str(row["value"]) if row and row["value"] is not None else default


def is_kill_switch_on() -> bool:
    return get_config("kill_switch", "OFF").upper() == "ON"


def is_new_outreach_paused() -> bool:
    if get_config("pause_new_outreach", "OFF").upper() == "ON":
        return True
    cfg = load_campaign_config()
    return bool(cfg.get("workflow_rules", {}).get("pause_new_outreach", False))


def is_quiet_sleeping_hours(now_ts: Optional[float] = None) -> bool:
    cfg = load_campaign_config()
    rules = cfg.get("workflow_rules", {})
    q_start = int(rules.get("quiet_hours_start", os.getenv("QUIET_HOURS_START", "0")))
    q_end = int(rules.get("quiet_hours_end", os.getenv("QUIET_HOURS_END", "6")))
    dt = datetime.fromtimestamp(now_ts or time.time())
    if q_start < q_end:
        return q_start <= dt.hour < q_end
    return dt.hour >= q_start or dt.hour < q_end


def log_interaction(
    channel: str,
    contact: Dict[str, Any],
    user_msg: str,
    ai_reply: str,
    intent: str,
    latency_ms: int = 0,
    msg_id: str = "",
    status: str = "sent_verified",
) -> None:
    with db_lock:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO interaction_logs (
                timestamp, channel, contact_id, contact_name, context_tag,
                user_msg, ai_reply, intent, latency_ms, msg_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                channel,
                contact.get("contact_id") or contact.get("fb_uid") or contact.get("zalo_uid") or "",
                contact.get("name") or "",
                contact.get("context_tag") or "",
                user_msg,
                ai_reply,
                intent,
                int(latency_ms),
                str(msg_id or ""),
                status,
            ),
        )
        conn.commit()
        conn.close()


def export_live_snapshot() -> Dict[str, Any]:
    """Xuất dữ liệu thời gian thực ra `fast_engine_live_status.json` cho Web Dashboard đọc."""
    conn = get_conn()
    cfg = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM engine_config").fetchall()}
    contacts = [dict(r) for r in conn.execute("SELECT * FROM contacts ORDER BY updated_at DESC").fetchall()]
    logs = [dict(r) for r in conn.execute("SELECT * FROM interaction_logs ORDER BY id DESC LIMIT 300").fetchall()]
    conn.close()

    stage_counts: Dict[str, int] = {}
    for c in contacts:
        st = c.get("stage") or "QUEUED"
        stage_counts[st] = stage_counts.get(st, 0) + 1

    campaign_cfg = load_campaign_config()
    snapshot = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "campaign": {
            "campaign_id": campaign_cfg.get("campaign_id", ""),
            "campaign_name": campaign_cfg.get("campaign_name", ""),
            "campaign_goal": campaign_cfg.get("campaign_goal", ""),
            "sender_persona": campaign_cfg.get("sender_persona", {}),
            "workflow_rules": campaign_cfg.get("workflow_rules", {}),
        },
        "engine_config": cfg,
        "stage_counts": stage_counts,
        "total_contacts": len(contacts),
        "contacts": contacts[:500],
        "recent_logs": logs,
    }
    try:
        Path(app_cfg.LIVE_STATUS_JSON).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return snapshot
