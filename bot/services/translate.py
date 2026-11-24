"""ترجمة نصية.

الافتراضي: نقطة Google translate العامة (بلا مفتاح، قد تتغير).
يمكن استبدالها بأي مزوّد آخر بتعديل translate_text فقط.
"""
from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MAX_CHUNK = 4500

LANG_NAMES = {
    "ar": "العربية",
    "en": "الإنجليزية",
    "fr": "الفرنسية",
    "de": "الألمانية",
    "es": "الإسبانية",
    "ru": "الروسية",
    "tr": "التركية",
    "it": "الإيطالية",
    "zh": "الصينية",
    "ja": "اليابانية",
    "auto": "تلقائي",
}


class TranslationError(Exception):
    pass


def chunk_text(text: str, size: int = MAX_CHUNK) -> list[str]:
    """تقطيع النص على حدود الجمل حتى لا تنكسر جملة بين طلبين."""
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    buf = ""
    for sentence in re.split(r"(?<=[.!?؟\n])\s+", text):
        if len(buf) + len(sentence) + 1 > size and buf:
            parts.append(buf.strip())
            buf = ""
        buf += sentence + " "
    if buf.strip():
        parts.append(buf.strip())
    return parts


async def translate_text(
    text: str,
    target: str = "ar",
    source: str = "auto",
    timeout: float = 12.0,
) -> str:
    if not text or not text.strip():
        raise TranslationError("لا يوجد نص للترجمة")

    translated: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for piece in chunk_text(text):
            params = {
                "client": "gtx",
                "sl": source,
                "tl": target,
                "dt": "t",
                "q": piece,
            }
            try:
                resp = await client.get(ENDPOINT, params=params)
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise TranslationError(f"تعذّرت الترجمة: {exc}") from exc

            pieces = [seg[0] for seg in (payload[0] or []) if seg and seg[0]]
            if not pieces:
                raise TranslationError("لم يرجع المزوّد نتيجة")
            translated.append("".join(pieces))

    return "\n".join(t.strip() for t in translated).strip()


def looks_arabic(text: str) -> bool:
    letters = re.findall(r"[\w\u0600-\u06FF]", text or "")
    if not letters:
        return False
    arabic = sum(1 for c in letters if "\u0600" <= c <= "\u06FF")
    return arabic / len(letters) > 0.5
