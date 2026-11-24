"""استخراج النص من PDF (pypdf) — أساس البحث داخل المحتوى والتلخيص."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

log = logging.getLogger(__name__)

MAX_CHARS = 400_000  # حد التخزين في جدول البحث


@dataclass(frozen=True)
class PdfText:
    text: str
    pages: int
    is_scanned: bool  # نص فارغ ⇒ غالبًا ملف مصوّر يحتاج OCR


def extract_text(data: bytes, max_pages: int = 120) -> PdfText:
    """يستخرج نص الـ PDF. لا يرمي استثناءً — يرجع نصًا فارغًا عند الفشل."""
    if not data:
        return PdfText("", 0, False)
    try:
        reader = PdfReader(BytesIO(data), strict=False)
    except (PdfReadError, OSError, ValueError) as exc:
        log.warning("تعذّرت قراءة PDF: %s", exc)
        return PdfText("", 0, False)

    total = len(reader.pages)
    chunks: list[str] = []
    collected = 0
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # صفحات تالفة
            log.debug("صفحة %s فشلت: %s", i + 1, exc)
            continue
        chunks.append(page_text)
        collected += len(page_text)
        if collected >= MAX_CHARS:
            break

    text = "\n".join(chunks).strip()
    return PdfText(text=text[:MAX_CHARS], pages=total, is_scanned=not text)
