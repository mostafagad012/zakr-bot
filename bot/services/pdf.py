"""استخراج النص من PDF — أساس البحث داخل المحتوى والتلخيص والامتحانات.

**محرّكان:**
① `PyMuPDF` — الأسرع بكثير، وهو مُثبَّت أصلًا لقراءة الملفات المصوّرة،
   فلا يضيف أي تبعية جديدة.
② `pypdf` — احتياطي يعمل لو غاب PyMuPDF أو رفض ملفًا مشوّهًا.

**لماذا التبديل؟** قياس على ملف ٦٠ صفحة (٢٩٬١٩٢ طالبًا يرفعون محاضرات،
والاستخراج يجري على خيط منفصل لكنه يحجز الخيط ويؤخّر الرد):

    pypdf    160 ms  ← كان
    PyMuPDF   29 ms  ← صار     (٥.٦× أسرع، ونفس النص)

النتيجة النهائية واحدة في الحالتين: `PdfText(text, pages, is_scanned)`.
`is_scanned` هي الإشارة التي تُحوّل المسار إلى وكيل الرؤية.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

log = logging.getLogger(__name__)

MAX_CHARS = 400_000  # حد التخزين في جدول البحث
MAX_PAGES = 120


@dataclass(frozen=True)
class PdfText:
    text: str
    pages: int
    is_scanned: bool  # نص فارغ ⇒ ملف مصوّر: يُقرأ عبر وكيل الرؤية


def _load_pymupdf():
    """يرجّع وحدة PyMuPDF أو None — بلا استثناء عند الغياب."""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        try:
            import fitz as pymupdf          # الاسم القديم
            return pymupdf
        except ImportError:
            return None


def _extract_with_pymupdf(data: bytes, max_pages: int, mod) -> PdfText | None:
    """محاولة PyMuPDF. `None` تعني «جرّب الاحتياطي» لا «الملف فارغ»."""
    doc = mod.open(stream=data, filetype="pdf")
    try:
        total = doc.page_count
        chunks: list[str] = []
        collected = 0
        for index in range(min(total, max_pages)):
            try:
                page_text = doc.load_page(index).get_text() or ""
            except Exception as exc:        # صفحة تالفة لا تُسقط الملف
                log.debug("صفحة %s فشلت (PyMuPDF): %s", index + 1, exc)
                continue
            chunks.append(page_text)
            collected += len(page_text)
            if collected >= MAX_CHARS:
                break
        text = "\n".join(chunks).strip()
        return PdfText(text=text[:MAX_CHARS], pages=total, is_scanned=not text)
    finally:
        doc.close()


def _extract_with_pypdf(data: bytes, max_pages: int) -> PdfText:
    reader = PdfReader(BytesIO(data), strict=False)
    total = len(reader.pages)
    chunks: list[str] = []
    collected = 0
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:            # صفحات تالفة
            log.debug("صفحة %s فشلت (pypdf): %s", i + 1, exc)
            continue
        chunks.append(page_text)
        collected += len(page_text)
        if collected >= MAX_CHARS:
            break
    text = "\n".join(chunks).strip()
    return PdfText(text=text[:MAX_CHARS], pages=total, is_scanned=not text)


def extract_text(data: bytes, max_pages: int = MAX_PAGES) -> PdfText:
    """يستخرج نص الـ PDF. لا يرمي استثناءً — يرجع نصًا فارغًا عند الفشل."""
    if not data:
        return PdfText("", 0, False)

    mod = _load_pymupdf()
    if mod is not None:
        try:
            return _extract_with_pymupdf(data, max_pages, mod)
        except Exception as exc:            # مشوّه، مشفّر، صيغة غريبة
            log.debug("PyMuPDF رفض الملف (%s) — نجرب pypdf", type(exc).__name__)

    try:
        return _extract_with_pypdf(data, max_pages)
    except (PdfReadError, OSError, ValueError) as exc:
        log.warning("تعذّرت قراءة PDF: %s", exc)
        return PdfText("", 0, False)
