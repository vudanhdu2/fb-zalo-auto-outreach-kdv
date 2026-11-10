# -*- coding: utf-8 -*-
"""
core/ai_responder.py — Bộ não AI Đối thoại Đa Kênh & Sinh Tin Nhắn Tùy Biến Mục Đích
Đọc trực tiếp kịch bản, mục tiêu, Persona và FAQ từ `campaign_config.json`, kết hợp:
  1. Khóa đại từ xưng hô xuyên kênh (Cross-Channel Sticky Pronoun Lock: anh-em, em-anh, em-chị, em-bác, mình-bạn).
  2. Đa dạng hóa câu chào mở đầu theo biến động `{name}`, `{context}`, `{note_1}`, `{note_2}`.
  3. Chống lặp câu trả lời khi khách thả Sticker hoặc nhắn bồi câu ngắn.
  4. Tự động nhường luồng khi phát hiện chủ tài khoản đang trực tiếp chat tay (`Human Takeover Guard`).
"""

import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

import config as app_cfg
from core import db as db_mod


PRONOUN_MAP: Dict[str, Tuple[str, str]] = {
    "minh_ban": ("mình", "bạn"),
    "anh_em": ("anh", "em"),
    "chi_em": ("chị", "em"),
    "em_anh": ("em", "anh"),
    "em_chi": ("em", "chị"),
    "em_bac": ("em", "bác"),
    "em_anh_chi": ("em", "anh/chị"),
}

SYSTEM_BANNER_PATTERNS = (
    "bạn vừa kết bạn",
    "đã đồng ý kết bạn",
    "hãy bắt đầu cùng nhau chia sẻ",
    "[sticker]",
    "[hình ảnh]",
)


def detect_and_lock_pronouns(
    contact: Dict[str, Any],
    history: List[Dict[str, str]],
    latest_user_text: str = "",
) -> Tuple[str, str, str]:
    """
    Suy luận và khóa bền vững cặp đại từ `(self_pronoun, user_pronoun, pair_code)` xuyên suốt cả FB & Zalo.
    """
    locked_code = (contact.get("pronoun_pair") or "").strip()
    user_texts = [
        m.get("content", "")
        for m in history
        if m.get("role") == "user"
    ]
    if latest_user_text:
        user_texts.append(latest_user_text)

    clean_lines = []
    for ut in user_texts:
        low = ut.lower().strip()
        if any(bp in low for bp in SYSTEM_BANNER_PATTERNS):
            continue
        # Loại bỏ cụm danh từ tập thể "anh em mình", "ae" để tránh nhận diện nhầm đại từ
        low = re.sub(r"\b(anh\s+em\s+mình|anh\s+em|a\s+e|ae)\b", " ", low)
        clean_lines.append(low)

    combined = " | ".join(clean_lines)

    detected_code = ""
    if re.search(r"\b(bác|chú|cô)\b", combined) and re.search(r"\b(em|cháu)\b", combined):
        detected_code = "em_bac"
    elif re.search(r"\b(em|e)\b", combined) and re.search(r"\b(anh|a)\b", combined):
        # Nếu khách xưng "em" và gọi mình là "anh" -> mình là "anh", khách là "em"
        if re.search(r"\b(chào\s+anh|chào\s+a|dạ\s+anh|dạ\s+a|vâng\s+anh|ok\s+anh|ok\s+a|cảm\s+ơn\s+anh|em\s+cảm\s+ơn\s+a|e\s+thấy\s+a|cho\s+em\s+hỏi\s+anh)\b", combined):
            detected_code = "anh_em"
        elif re.search(r"\b(anh\s+ơi|a\s+ơi)\b", combined):
            detected_code = "anh_em"
        elif re.search(r"\b(anh\s+cần|anh\s+muốn|anh\s+đang|gửi\s+anh\s+xem|tư\s+vấn\s+cho\s+anh)\b", combined):
            detected_code = "em_anh"
        else:
            detected_code = "anh_em"
    elif re.search(r"\b(chị\s+cần|chị\s+muốn|gửi\s+chị\s+xem|tư\s+vấn\s+cho\s+chị)\b", combined):
        detected_code = "em_chi"

    final_code = detected_code or locked_code
    if not final_code:
        cfg = db_mod.load_campaign_config()
        final_code = cfg.get("sender_persona", {}).get("default_pronoun_pair", "minh_ban")

    if final_code not in PRONOUN_MAP:
        final_code = "minh_ban"

    if final_code != locked_code and contact.get("contact_id"):
        with db_mod.db_lock:
            conn = db_mod.get_conn()
            conn.execute(
                "UPDATE contacts SET pronoun_pair=?, updated_at=? WHERE contact_id=?",
                (final_code, time.strftime("%Y-%m-%d %H:%M:%S"), contact["contact_id"]),
            )
            conn.commit()
            conn.close()
        contact["pronoun_pair"] = final_code

    self_p, user_p = PRONOUN_MAP[final_code]
    return self_p, user_p, final_code


def generate_outreach_message(contact: Dict[str, Any], campaign_cfg: Optional[Dict[str, Any]] = None) -> str:
    """
    Sinh tin nhắn mở đầu cá nhân hóa theo `outreach_templates` trong `campaign_config.json`.
    """
    cfg = campaign_cfg or db_mod.load_campaign_config()
    persona = cfg.get("sender_persona", {})
    templates = cfg.get("outreach_templates") or [
        "Chào {name}, {self_p} là {short_name} bên {brand_or_org} (Zalo {zalo_phone}). Thấy {user_p} quan tâm tới {context} nên {self_p} nhắn kết nối để trao đổi nhanh nhé."
    ]

    seed_str = str(contact.get("contact_id") or contact.get("fb_uid") or contact.get("phone") or contact.get("name") or "0")
    idx = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16) % len(templates)
    tpl = templates[idx]

    self_p, user_p, _ = detect_and_lock_pronouns(contact, [])
    values = {
        "name": (contact.get("name") or "bạn").strip(),
        "full_name": persona.get("full_name") or app_cfg.ADMIN_FULL_NAME,
        "short_name": persona.get("short_name") or app_cfg.ADMIN_SHORT_NAME,
        "role_title": persona.get("role_title") or "Phụ trách Tư vấn",
        "brand_or_org": persona.get("brand_or_org") or "Team",
        "zalo_phone": persona.get("zalo_phone") or app_cfg.ADMIN_ZALO_PHONE,
        "context": (contact.get("context_tag") or "lĩnh vực của mình").strip(),
        "note_1": (contact.get("custom_note_1") or "").strip(),
        "note_2": (contact.get("custom_note_2") or "").strip(),
        "self_p": self_p,
        "Self_p": self_p.capitalize(),
        "user_p": user_p,
        "User_p": user_p.capitalize(),
    }

    msg = tpl
    for k, v in values.items():
        msg = msg.replace("{" + k + "}", str(v))
    return re.sub(r"\s{2,}", " ", msg).strip()


def generate_zalo_friend_greeting(contact: Dict[str, Any], campaign_cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = campaign_cfg or db_mod.load_campaign_config()
    persona = cfg.get("sender_persona", {})
    tpl = cfg.get("zalo_friend_request_greeting") or (
        "Chào {name}, mình là {full_name} bên {brand_or_org}, kết bạn Zalo với mình để tiện trao đổi nhé"
    )
    msg = (
        tpl.replace("{name}", (contact.get("name") or "bạn").strip())
        .replace("{full_name}", persona.get("full_name") or app_cfg.ADMIN_FULL_NAME)
        .replace("{short_name}", persona.get("short_name") or app_cfg.ADMIN_SHORT_NAME)
        .replace("{brand_or_org}", persona.get("brand_or_org") or "Team")
    )
    return msg[:145]


def is_human_owner_active(contact: Dict[str, Any], history: List[Dict[str, str]]) -> bool:
    """
    Kiểm tra nếu chủ tài khoản đang trực tiếp chat tay trong luồng hội thoại
    (tin nhắn `assistant` cuối cùng không khớp với `initial_msg` hay `last_ai_reply` của bot).
    """
    cfg = db_mod.load_campaign_config()
    if not cfg.get("workflow_rules", {}).get("human_takeover_guard", True):
        return False

    assistant_msgs = [m.get("content", "").strip() for m in history if m.get("role") == "assistant" and m.get("content", "").strip()]
    if not assistant_msgs:
        return False

    last_asst = assistant_msgs[-1]
    known_init = (contact.get("initial_msg") or "").strip()
    known_reply = (contact.get("last_ai_reply") or "").strip()

    if not known_init and not known_reply:
        return False

    if known_reply and (last_asst in known_reply or known_reply in last_asst):
        return False
    if known_init and (last_asst in known_init or known_init in last_asst):
        return False

    return True


def classify_and_generate_reply(
    channel: str,
    contact: Dict[str, Any],
    history: List[Dict[str, str]],
    latest_unanswered_text: str = "",
) -> Dict[str, Any]:
    """
    Sinh câu trả lời thông minh theo đúng mục tiêu, Persona và FAQ trong `campaign_config.json`.
    """
    if is_human_owner_active(contact, history):
        return {
            "intent": "NO_REPLY_NEEDED",
            "reply": "",
            "is_personal_or_human_active": True,
        }

    cfg = db_mod.load_campaign_config()
    persona = cfg.get("sender_persona", {})
    ai_brain = cfg.get("ai_brain", {})
    self_p, user_p, _ = detect_and_lock_pronouns(contact, history, latest_unanswered_text)

    user_text = (latest_unanswered_text or (history[-1]["content"] if history else "")).strip()
    if not user_text:
        return {"intent": "NO_REPLY_NEEDED", "reply": ""}

    # Trích xuất các mục FAQ khớp từ khóa hoặc toàn bộ FAQ (nếu <= 8 mục)
    faqs = ai_brain.get("knowledge_base_faq") or []
    faq_lines = []
    for item in faqs:
        topic = item.get("topic", "Thông tin")
        ans = item.get("answer_fact", "")
        faq_lines.append(f"- [{topic}]: {ans}")
    faq_block = "\n".join(faq_lines) if faq_lines else "- Giải đáp ngắn gọn, trung thực theo mục tiêu chiến dịch."

    guardrails = ai_brain.get("hard_guardrails") or []
    guardrail_block = "\n".join(f"- {g}" for g in guardrails)

    raw_sys = ai_brain.get("system_instructions") or "Bạn là {full_name} ({role_title} tại {brand_or_org}). Mục tiêu: {campaign_goal}"
    sys_filled = (
        raw_sys.replace("{name}", contact.get("name") or "bạn")
        .replace("{full_name}", persona.get("full_name") or app_cfg.ADMIN_FULL_NAME)
        .replace("{short_name}", persona.get("short_name") or app_cfg.ADMIN_SHORT_NAME)
        .replace("{role_title}", persona.get("role_title") or "Phụ trách Tư vấn")
        .replace("{brand_or_org}", persona.get("brand_or_org") or "Team")
        .replace("{context}", contact.get("context_tag") or "")
        .replace("{campaign_goal}", cfg.get("campaign_goal") or "")
    )

    system_prompt = f"""{sys_filled}

[THÔNG TIN KHÁCH HÀNG & KÊNH GIAO TIẾP]
- Kênh đang chat: {channel}
- Tên khách hàng: {contact.get('name', 'bạn')}
- Ngữ cảnh / Phân khúc: {contact.get('context_tag', '')}
- Ghi chú riêng: {contact.get('custom_note_1', '')} {contact.get('custom_note_2', '')}
- CẶP ĐẠI TỪ XƯNG HÔ BẮT BUỘC: Bạn xưng là "{self_p}", gọi khách hàng là "{user_p}".
- Phong cách giao tiếp: {persona.get('tone_style', 'Tự nhiên như người thật, ngắn gọn 1-3 câu.')}

[KIẾN THỨC TRẢ LỜI KHÁCH HÀNG (FAQ)]
{faq_block}

[QUY TẮC BẮT BUỘC]
{guardrail_block}
- Không bao giờ lặp lại y hệt câu trả lời trước đó của chính mình (`{contact.get('last_ai_reply', '')}`).

Hãy trả về kết quả dưới dạng JSON hợp lệ (không bọc markdown) với 2 trường:
{{
  "intent": "ASKING_INFO" | "INTERESTED_AGREED" | "DECLINED" | "NO_REPLY_NEEDED",
  "reply": "<câu trả lời tự nhiên bằng tiếng Việt, xưng '{self_p}' gọi '{user_p}'>"
}}"""

    messages_payload = [{"role": "system", "content": system_prompt}]
    for m in history[-10:]:
        messages_payload.append({"role": m["role"], "content": m["content"]})

    # Gọi LLM API (với fallback tự động nếu chưa điền API Key hoặc mất mạng)
    if app_cfg.AI_API_KEY and not app_cfg.AI_API_KEY.startswith("sk-replace"):
        for model_name in app_cfg.AI_FALLBACK_MODELS:
            try:
                resp = requests.post(
                    app_cfg.AI_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {app_cfg.AI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "messages": messages_payload,
                        "temperature": 0.4,
                        "max_tokens": 350,
                    },
                    timeout=18,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_content = (data["choices"][0]["message"]["content"] or "").strip()
                    raw_content = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content, flags=re.I).strip()
                    parsed = json.loads(raw_content)
                    reply_txt = (parsed.get("reply") or "").strip()
                    intent_val = (parsed.get("intent") or "ASKING_INFO").strip().upper()
                    if reply_txt and reply_txt == (contact.get("last_ai_reply") or "").strip():
                        reply_txt = f"Dạ {user_p} cần thêm thông tin phần nào cứ nhắn {self_p} hỗ trợ ngay nhé."
                    return {"intent": intent_val, "reply": reply_txt}
            except Exception:
                continue

    # Fallback thông minh theo từ khóa FAQ nếu người dùng chưa cấu hình AI_API_KEY
    low_u = user_text.lower()
    if any(k in low_u for k in ["bận", "không có nhu cầu", "thôi", "không làm", "không cần"]):
        return {
            "intent": "DECLINED",
            "reply": f"Ok {user_p} nhé, cảm ơn {user_p} đã phản hồi. Chúc {user_p} nhiều sức khỏe và công việc thuận lợi nha.",
        }
    if any(k in low_u for k in ["ok", "đồng ý", "nhất trí", "được nhé", "tư vấn giúp", "gửi mình"]):
        return {
            "intent": "INTERESTED_AGREED",
            "reply": f"Ok {user_p} nhé, để {self_p} gửi thông tin chi tiết và hướng dẫn bước tiếp theo cho {user_p} luôn nha.",
        }
    for item in faqs:
        kws = item.get("question_keywords") or []
        if any(kw.lower() in low_u for kw in kws):
            fact = item.get("answer_fact", "")
            return {
                "intent": "ASKING_INFO",
                "reply": f"{fact} {user_p.capitalize()} xem nếu phù hợp thì nhắn {self_p} hỗ trợ tiếp nhé.",
            }
    return {
        "intent": "ASKING_INFO",
        "reply": f"Chào {user_p}, {self_p} nhận được tin nhắn của {user_p} rồi. {user_p.capitalize()} cho {self_p} biết thêm nhu cầu cụ thể để {self_p} tư vấn chi tiết nhất nhé.",
    }
