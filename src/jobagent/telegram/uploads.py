"""Resume upload via Telegram. The caller has already verified the chat is TELEGRAM_ALLOWED_CHAT_ID."""

from __future__ import annotations

import logging

from jobagent.db.base import Repository
from jobagent.storage import MAX_RESUME_BYTES, ResumeStore, object_name
from jobagent.telegram.client import TelegramClient
from jobagent.utils.dates import utcnow

log = logging.getLogger(__name__)

ALIASES = {"ai": "ai", "ml": "ai", "aiml": "ai", "ai/ml": "ai", "/ai": "ai", "fullstack": "fullstack",
           "full-stack": "fullstack", "full stack": "fullstack", "/fullstack": "fullstack"}


def variant_from_caption(caption: str | None) -> str:
    """'ai' / 'fullstack' (also /ai, /fullstack) select a variant. Any other caption - empty, '/resume', 'my cv' - is the main resume."""
    return ALIASES.get((caption or "").strip().lower(), "default")


def handle_document(msg: dict, repo: Repository, tg: TelegramClient, store: ResumeStore | None) -> str:
    doc = msg.get("document") or {}
    name = (doc.get("file_name") or "").lower()
    if doc.get("mime_type") != "application/pdf" and not name.endswith(".pdf"):
        return "⚠️ That is not a PDF. Please send your resume as a PDF file."
    if store is None:
        return "⚠️ Resume storage is not available right now."
    variant = variant_from_caption(msg.get("caption"))
    size = int(doc.get("file_size") or 0)
    if size > MAX_RESUME_BYTES:
        return f"⚠️ That file is {size // 1024} KB; the limit is {MAX_RESUME_BYTES // 1024 // 1024} MB. Please send a smaller PDF."
    try:
        data = tg.get_file(doc["file_id"])
    except Exception as e:  # noqa: BLE001
        log.warning("resume download failed: %s", type(e).__name__)
        return "⚠️ I could not download that file from Telegram. Please send it again."
    if len(data) > MAX_RESUME_BYTES:
        return f"⚠️ That file is {len(data) // 1024} KB; the limit is {MAX_RESUME_BYTES // 1024 // 1024} MB."
    if not data.startswith(b"%PDF"):
        return "⚠️ That file is not a valid PDF (wrong file header). Please export your resume as a PDF and resend it."
    try:
        store.upload(object_name(variant), data)
    except Exception as e:  # noqa: BLE001
        log.warning("resume upload failed: %s", type(e).__name__)
        return "⚠️ Saving the resume failed. Please try again in a minute."

    row = repo.get_profile_row() or {}
    variants = dict(row.get("resume_variants") or {})
    now = utcnow().isoformat()
    variants[variant] = {"path": object_name(variant), "size": len(data), "updated_at": now}
    fields: dict = {"resume_variants": variants}
    if variant == "default":
        fields.update(resume_path=object_name("default"), resume_updated_at=now)
    repo.update_profile_fields(**fields)
    repo.add_event("info", "resume_uploaded", None, {"variant": variant, "size": len(data)})  # never the content
    kb = max(1, round(len(data) / 1024))
    label = "" if variant == "default" else f" as the '{variant}' variant"
    return f"✅ Resume saved{label} ({kb} KB). Send /setup to update your details."
