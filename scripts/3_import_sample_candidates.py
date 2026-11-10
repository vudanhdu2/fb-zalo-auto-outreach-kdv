# -*- coding: utf-8 -*-
"""
Khởi tạo Cơ sở dữ liệu SQLite (`data/outreach_engine.db` + `zalo_bridge/zalo_messages.db`)
và nạp danh sách khách hàng mẫu từ `data/contacts_sample.json`.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import config as app_cfg
from core import db as db_mod


def ensure_local_zalo_db():
    zdb = app_cfg.ZALO_LOCAL_DIR / "zalo_messages.db"
    conn = sqlite3.connect(str(zdb), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY,
      account_id TEXT NOT NULL,
      thread_id TEXT NOT NULL,
      customer_id TEXT,
      customer_name TEXT,
      user_name TEXT,
      last_snippet TEXT,
      status TEXT DEFAULT 'bot',
      thread_type TEXT DEFAULT 'user',
      updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conversation_id TEXT NOT NULL,
      sender_id TEXT,
      sender_name TEXT,
      role TEXT NOT NULL,
      content TEXT,
      timestamp TEXT NOT NULL,
      zalo_msg_id TEXT,
      attach_json TEXT,
      source TEXT
    );
    CREATE TABLE IF NOT EXISTS zalo_reaction_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      target_msg_id TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      reaction_icon TEXT,
      created_at TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_msg_conv_zmsg
      ON messages(conversation_id, zalo_msg_id)
      WHERE zalo_msg_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS ix_msg_conv_ts
      ON messages(conversation_id, timestamp);
    CREATE INDEX IF NOT EXISTS ix_msg_assistant_ts
      ON messages(conversation_id, role, timestamp)
      WHERE role = 'assistant';
    CREATE INDEX IF NOT EXISTS ix_conv_acc_type_last
      ON conversations(account_id, thread_type, updated_at);
    """)
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Khởi tạo DB và nạp danh bạ khách hàng mẫu")
    parser.add_argument(
        "--input",
        default=str(app_cfg.DATA_DIR / "contacts_sample.json"),
        help="Đường dẫn tới file JSON chứa danh sách khách hàng",
    )
    args = parser.parse_args()

    db_mod.init_db()
    ensure_local_zalo_db()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[CẢNH BÁO] Không tìm thấy file danh bạ: {input_path}")
        return

    items = json.loads(input_path.read_text(encoding="utf-8"))
    conn = db_mod.get_conn()
    cur = conn.cursor()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    for idx, c in enumerate(items):
        cid = str(c.get("contact_id") or c.get("fb_uid") or c.get("phone") or f"c_{idx}").strip()
        cur.execute(
            """
            INSERT OR IGNORE INTO contacts (
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
        inserted += cur.rowcount
    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    conn.close()
    print(f"✅ Đã khởi tạo DB tại: {db_mod.DB_PATH}")
    print(f"✅ Đã thêm mới {inserted} khách hàng (Tổng số trong danh bạ: {total})")


if __name__ == "__main__":
    main()
