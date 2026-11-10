# -*- coding: utf-8 -*-
"""
Cấu hình trung tâm cho Hệ thống Tự động Nhắn tin & Tuyển Kiểm Duyệt Viên (Facebook Messenger + Zalo)
Tự động đọc biến môi trường từ file `.env` và hỗ trợ cả 2 chế độ chạy Zalo Bridge:
  - `local`: Chạy Node.js `zca-js` và SQLite `zalo_messages.db` trực tiếp ngay trên máy tính hiện tại.
  - `ssh`  : Chạy Node.js `zca-js` trên máy chủ Linux VPS từ xa thông qua kết nối SSH/SFTP.
"""

import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
ARTIFACT_DIR_PATH = BASE_DIR / "artifacts"
ZALO_LOCAL_DIR = BASE_DIR / "zalo_bridge"

for _d in (DATA_DIR, LOGS_DIR, ARTIFACT_DIR_PATH, ZALO_LOCAL_DIR, ZALO_LOCAL_DIR / "accounts"):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv_file():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
        return
    except ImportError:
        pass
    for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv_file()

# 1. Thông tin Admin / Chủ nhóm
ADMIN_FULL_NAME = os.getenv("ADMIN_FULL_NAME", "Admin Nhóm").strip()
ADMIN_SHORT_NAME = os.getenv("ADMIN_SHORT_NAME", ADMIN_FULL_NAME.split()[-1] if ADMIN_FULL_NAME else "Admin").strip()
ADMIN_ZALO_PHONE = os.getenv("ADMIN_ZALO_PHONE", "0912345678").strip()
ADMIN_ZALO_PHONE_DOT = os.getenv(
    "ADMIN_ZALO_PHONE_DOT",
    f"{ADMIN_ZALO_PHONE[:4]}.{ADMIN_ZALO_PHONE[4:6]}.{ADMIN_ZALO_PHONE[6:]}" if len(ADMIN_ZALO_PHONE) == 10 else ADMIN_ZALO_PHONE,
).strip()
MAIN_FB_ACTOR_UID = os.getenv("MAIN_FB_ACTOR_UID", "100000000000001").strip()
OWN_ADMIN_FB_UIDS = {
    u.strip()
    for u in os.getenv("OWN_ADMIN_FB_UIDS", MAIN_FB_ACTOR_UID).split(",")
    if u.strip()
} | {MAIN_FB_ACTOR_UID}

# 2. Cấu hình AI LLM
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1/chat/completions").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "sk-replace-with-your-own-api-key").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
AI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("AI_FALLBACK_MODELS", f"{AI_MODEL},gpt-4o").split(",")
    if m.strip()
]

# 3. Cấu hình Zalo Bridge (local hoặc ssh)
ZALO_BRIDGE_MODE = os.getenv("ZALO_BRIDGE_MODE", "local").strip().lower()
ZALO_ACCOUNT_ID = os.getenv("ZALO_ACCOUNT_ID", "acc_default").strip()
ZALO_SENDER_ID = os.getenv("ZALO_SENDER_ID", "100000000000000000").strip()
VPS_HOST = os.getenv("VPS_HOST", "127.0.0.1").strip()
VPS_PORT = int(os.getenv("VPS_PORT", "22"))
VPS_USER = os.getenv("VPS_USER", "root").strip()
VPS_PASS = os.getenv("VPS_PASS", "").strip()
REMOTE_ZALO_BOT_DIR = os.getenv("REMOTE_ZALO_BOT_DIR", "/opt/zalo_bot").strip()

# 4. Cấu hình cổng Chrome CDP
CDP_MAIN_PORT = int(os.getenv("CDP_MAIN_PORT", "9232"))
CDP_SCOUT1_PORT = int(os.getenv("CDP_SCOUT1_PORT", "9234"))
CDP_SCOUT2_PORT = int(os.getenv("CDP_SCOUT2_PORT", "9235"))

# 5. Đường dẫn cơ sở dữ liệu & nhật ký
DB_PATH = str(DATA_DIR / "mod_recruitment_queue.db")
DAEMON_LOG_PATH = str(LOGS_DIR / "auto_responder_daemon.log")
DAEMON_LIVE_LOG_PATH = str(LOGS_DIR / "mod_daemon_live.log")
DAEMON_LOCK_PATH = str(DATA_DIR / "auto_responder_daemon.lock")
ARTIFACT_DIR = str(ARTIFACT_DIR_PATH)
LIVE_STATUS_JSON = str(BASE_DIR / "fast_engine_live_status.json")
LEGACY_LOG_JSON = str(DATA_DIR / "auto_responder_log.json")
MOD_PROOFS_DIR = str(ARTIFACT_DIR_PATH / "mod_proofs")
os.makedirs(MOD_PROOFS_DIR, exist_ok=True)


# ==============================================================================
# CẦU NỐI ĐA CHẾ ĐỘ ZALO BRIDGE (LOCAL PC HOẶC SSH VPS)
# Giúp toàn bộ code tự động hóa Zalo chạy trực tiếp trên máy tính cá nhân
# mà không bắt buộc phải có máy chủ Linux VPS riêng!
# ==============================================================================
class _DummyChannel:
    def shutdown_write(self):
        pass


class _StreamBuffer:
    def __init__(self, data: bytes = b""):
        self._buf = io.BytesIO(data)
        self.channel = _DummyChannel()

    def read(self) -> bytes:
        return self._buf.read()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._buf.write(data)


class _LocalSFTPFile:
    def __init__(self, local_path: Path, mode: str = "w"):
        self.local_path = local_path
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self._fh = open(self.local_path, "wb" if "w" in mode else "rb")

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._fh.write(data)

    def read(self):
        return self._fh.read()

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class LocalSFTPBridge:
    """Giả lập giao diện SFTP của Paramiko ngay trên thư mục `./zalo_bridge` của máy tính."""

    def _map_path(self, remote_path: str) -> Path:
        norm = remote_path.replace("\\", "/")
        for prefix in ("/opt/zalo_bot/", f"{REMOTE_ZALO_BOT_DIR.rstrip('/')}/"):
            if norm.startswith(prefix):
                rel = norm[len(prefix):]
                return ZALO_LOCAL_DIR / rel
        return ZALO_LOCAL_DIR / Path(norm).name

    def open(self, remote_path: str, mode: str = "w"):
        return _LocalSFTPFile(self._map_path(remote_path), mode)

    def put(self, local_path: str, remote_path: str):
        dst = self._map_path(remote_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dst)

    def get(self, remote_path: str, local_path: str):
        src = self._map_path(remote_path)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local_path)

    def close(self):
        pass


class _DummyTransport:
    def is_active(self) -> bool:
        return True


class LocalZaloBridgeClient:
    """
    Thực thi trực tiếp các lệnh Python/Node.js của Zalo Bridge ngay trên máy cục bộ (`./zalo_bridge`),
    tương thích 100% với giao diện `paramiko.SSHClient` trong `auto_responder_mod_recruitment.py`.
    """

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, *args, **kwargs):
        return True

    def get_transport(self):
        return _DummyTransport()

    def open_sftp(self):
        return LocalSFTPBridge()

    def exec_command(self, command: str, timeout: int = 30):
        local_dir_posix = ZALO_LOCAL_DIR.as_posix()
        cmd = command
        cmd = cmd.replace("/opt/zalo_bot", local_dir_posix)
        if REMOTE_ZALO_BOT_DIR:
            cmd = cmd.replace(REMOTE_ZALO_BOT_DIR.rstrip("/"), local_dir_posix)

        # Chuyển câu lệnh dạng `cd ... && node file.mjs && rm -f file.mjs` sang chạy đa nền tảng
        m_node = re.search(r"node\s+([^\s&;]+)", cmd)
        if m_node and ("cd " in cmd or cmd.strip().startswith("node ")):
            script_name = m_node.group(1).strip()
            script_path = ZALO_LOCAL_DIR / script_name
            try:
                proc = subprocess.run(
                    ["node", str(script_path)],
                    cwd=str(ZALO_LOCAL_DIR),
                    capture_output=True,
                    timeout=timeout,
                )
                if "rm -f" in cmd and script_path.exists():
                    try:
                        script_path.unlink()
                    except Exception:
                        pass
                return _StreamBuffer(), _StreamBuffer(proc.stdout), _StreamBuffer(proc.stderr)
            except Exception as e:
                return _StreamBuffer(), _StreamBuffer(b""), _StreamBuffer(str(e).encode("utf-8"))

        # Chuyển câu lệnh `python3 -c ...` sang trình thông dịch Python hiện tại
        if cmd.strip().startswith("python3 -c "):
            py_exe = f'"{sys.executable}"'
            cmd = py_exe + " -c " + cmd.strip()[len("python3 -c "):]

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ZALO_LOCAL_DIR),
                shell=True,
                capture_output=True,
                timeout=timeout,
            )
            return _StreamBuffer(), _StreamBuffer(proc.stdout), _StreamBuffer(proc.stderr)
        except Exception as e:
            return _StreamBuffer(), _StreamBuffer(b""), _StreamBuffer(str(e).encode("utf-8"))

    def close(self):
        pass


def create_zalo_ssh_client():
    """Trả về SSHClient thật nếu `ZALO_BRIDGE_MODE=ssh`, hoặc `LocalZaloBridgeClient` nếu chạy cục bộ."""
    if ZALO_BRIDGE_MODE == "ssh" and VPS_HOST and VPS_HOST not in ("127.0.0.1", "localhost", "local"):
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return ssh
    return LocalZaloBridgeClient()
