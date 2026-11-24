"""قراءة الملفات المصوّرة عبر وكيل الذكاء الاصطناعي الذي اختاره المستخدم.

**لماذا هذا الملف؟**

كانت قراءة الصور تعتمد على `tesseract` (OCR محلي). وهي طريقة ممتازة
بالفعل — تعمل بلا إنترنت وبلا مفتاح وبلا تكلفة — لكنها تحتاج تثبيت حزم
نظام، وهو مستحيل على لوحات الاستضافة التي تمنح المستخدم حاويةً بلا `root`.

القرار هنا (بقرار المالك): **إزالة مسار tesseract كليًا**، وتمرير الملفات
على وكيل الذكاء الاصطناعي الذي يختاره المستخدم.

**الثمن — مذكور صراحةً لأن إخفاءه خدعة:**

* ملف مصوّر + لا مفتاح للمستخدم = لا قراءة إطلاقًا. لا بديل.
* تكلفة على المستخدم (أو على مزوّد مجاني بحصّة).
* الملف يخرج من خادمك إلى طرف ثالث. من يستخدم Azure/Gemini/OpenAI
  يبقى داخل مزوّد معروف، لكن البيانات تغادر نطاقك.
* المزودون النصّيون (Groq, DeepSeek, Mistral …) لا يقبلون صورًا أصلًا،
  فنكشف ذلك **قبل** إرسال أي شيء ونقول السبب بدل خطأ 400 غامض.

**ما لم يُحذف من المنطق القديم:** تنظيف محارف الاتجاه (bidi). العربي حين
يمرّ عبر أي مسار — بشري أو آلي — يحمل محارف تحكّم تُفسد الترتيب في
تيليجرام. تلك الدالة عامة وتبقى هنا لأنها لا علاقة لها بـtesseract.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass

from . import ai

log = logging.getLogger("bot.extract")

# ────────────────────────── حدود عملية
#  سبب وجودها: نموذج رؤية يُحاسَب على كل صورة، وصفحة مسحوبة بدقة 300DPI
#  قد تتجاوز حدّ الطلب فيُرفض الطلب كله بعد دفع ثمن التحويل. الحدود هنا
#  تحمي محفظة المستخدم قبل أن يكتشف المشكلة من فاتورة.
MAX_PAGES = 12                  # أبعد من ذلك: نُبلّغ ونتوقف بصدق
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_EDGE_PX = 1600              # أطول ضلع بعد التصغير
MAX_TEXT_CHARS = 400_000
RENDER_DPI = 150                # كافٍ للقراءة، وأقلّ من 300 بـ4 أضعاف حجمًا

PROMPT = (
    "استخرج كل النص الظاهر في هذه الصفحة كما هو حرفيًا. "
    "لا تشرح، لا تلخّص، لا تضف عنوانًا، لا تصف الصورة. "
    "حافظ على ترتيب الأسطر وفواصل الفقرات. "
    "اكتب الأرقام والأرقام العربية والرموز كما تظهر. "
    "إن لم يكن في الصفحة نص قابل للقراءة فاردد فقط: [[فارغة]]"
)

EMPTY_MARKER = "[[فارغة]]"


class ExtractError(RuntimeError):
    """فشل الاستخراج بسبب مفهوم للمستخدم (نُصه جاهز للعرض)."""


@dataclass
class ExtractResult:
    text: str
    pages: int
    pages_done: int
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


# ─────────────────────────────── تنظيف النص
#  محارف تحكّم الاتجاه وسواها — تصل من أي مسار (OCR قديمًا، ردود النموذج
#  الآن) وتُفسد عرض العربي في تيليجرام. الإزالة ليست تجميلًا: النص الذي
#  يحملها يظهر مقلوبًا أو مبتورًا عند الطبع.
_BIDI_CONTROLS = re.compile(
    "[\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069\ufeff\u200b-\u200d]"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """ينظّف نصًا مستخرجًا من أي مصدر: يحذف محارف التحكّم ويوحّد الفواصل."""
    if not text:
        return ""
    out = _BIDI_CONTROLS.sub("", text)
    out = _CONTROL_CHARS.sub("", out)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ─────────────────────── كشف قدرة المزوّد على الصور
#  القائمة ليست «رأيًا»: كل اسم هنا مبني على أن المزوّد يقبل رسائل بصيغة
#  `image_url` عبر واجهة OpenAI المتوافقة، أو كتلة `image` في Anthropic.
#  من ليس في القائمة **قد** يكون قادرًا بموديل معيّن (OpenRouter مثلًا
#  يعتمد على الموديل المختار)، فنقول ذلك بدل أن نُنكر.
VISION_PROVIDERS: frozenset[str] = frozenset({
    "openai",
    "anthropic",
    "gemini",
    "azure_openai",
    "openrouter",
    "xai",
})

VISION_MAYBE: frozenset[str] = frozenset({"ollama", "lmstudio", "vllm", "custom"})


def supports_vision(provider_key: str) -> bool:
    return (provider_key or "").strip().lower() in VISION_PROVIDERS


def vision_reason(provider_key: str) -> str:
    """سبب عدم الدعم بالعربية — جاهز للعرض، لا يُخمّن."""
    key = (provider_key or "").strip().lower()
    provider = ai.PROVIDERS.get(key)
    label = provider.label if provider else (key or "غير محدّد")
    if key in VISION_MAYBE:
        return (
            f"‏{label} خادم محلّي — قد يدعم الصور إن كان الموديل المُحمَّل "
            "موديل رؤية. جرّبه، وإن رفض فاختر OpenAI أو Gemini."
        )
    return (
        f"‏{label} لا يقرأ الصور — نصّي فقط. "
        "اختر مزوّدًا يدعم الرؤية من ⚙️ إعدادات الذكاء الاصطناعي: "
        "OpenAI · Gemini · Anthropic · Azure · xAI."
    )


# ─────────────────────────── تحويل الملفات إلى صور
def _pdf_to_images(data: bytes, limit: int | None = None) -> tuple[list[bytes], int]:
    """يرسم صفحات PDF إلى PNG — بلا poppler ولا أي حزمة نظام.

    `pdf2image` يحتاج `pdftoppm` من poppler، وهو برنامج نظام لا يمكن
    تثبيته على لوحة بلا `root` — وهو أصل المشكلة التي جاء هذا الملف
    ليحلّها. `PyMuPDF` يرسم بنفسه داخل بايثون، فلا حاجة لأي شيء خارجي.
    """
    try:
        import pymupdf
    except ImportError:                  # اسم الحزمة القديم
        try:
            import fitz as pymupdf      # type: ignore[no-redef]
        except ImportError as exc:
            raise ExtractError(
                "‏PyMuPDF غير مثبَّت — لا يمكن تحويل PDF إلى صور. "
                "أصلحه بـ: pip install PyMuPDF"
            ) from exc

    # نقرأ الحدّ **وقت التنفيذ** لا وقت التعريف: قيمة افتراضية `limit=MAX_PAGES`
    # تُجمَّد عند تعريف الدالة، فأي تعديل للإعداد بعدها لا يسري.
    if limit is None:
        limit = MAX_PAGES

    images: list[bytes] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        total = doc.page_count
        for index in range(min(total, limit)):
            page = doc.load_page(index)
            pix = page.get_pixmap(dpi=RENDER_DPI)
            images.append(_shrink(pix.tobytes("png")))
    finally:
        doc.close()
    return images, total


def _shrink(png: bytes) -> bytes:
    """يُصغّر الصورة قبل الإرسال — الحجم هو التكلفة.

    النموذج يقرأ 1500px عن 1500px بنفس جودة 3000px، لكن الثانية تدفع
    أربعة أضعاف. التصغير هنا يقلّل الفاتورة بلا خسارة قراءة ملحوظة.
    """
    try:
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(png))
        image = image.convert("RGB")
        if max(image.size) > MAX_EDGE_PX:
            ratio = MAX_EDGE_PX / max(image.size)
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.LANCZOS,
            )
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
        result = buffer.getvalue()
        return result if len(result) < len(png) else png
    except Exception as exc:            # noqa: BLE001 — التصغير تحسين لا شرط
        log.debug("تعذّر تصغير الصورة: %s", type(exc).__name__)
        return png


# ───────────────────────────── بناء رسالة الرؤية
def _image_block(data: bytes, style: str) -> dict:
    """كتلة صورة بالصيغة التي يفهمها المزوّد."""
    mime = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
    payload = base64.b64encode(data).decode("ascii")
    if style == "anthropic":
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": payload},
        }
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{payload}"}}


def _messages_for(image: bytes, style: str) -> list[dict]:
    block = _image_block(image, style)
    if style == "anthropic":
        # Anthropic: الرؤية كتلة داخل content، والنصّ بعدها
        return [{"role": "user", "content": [block, {"type": "text", "text": PROMPT}]}]
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                block,
            ],
        }
    ]


# ────────────────────────────────── الواجهة
async def extract_async(
    data: bytes,
    *,
    filename: str = "",
    mime: str = "",
    provider_key: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    on_progress=None,
) -> ExtractResult:
    """يستخرج نصًا من ملف (PDF مصوّر أو صورة) عبر وكيل الرؤية.

    `api_key` مفتاح **المستخدم** — يُمرَّر للطلب وحده ولا يُكتب في `.env`
    ولا يُخزَّن في أي مكان. هذا هو الفرق الجوهري عن مسار الإعدادات القديم.
    """
    key = (provider_key or ai.get_provider().key).strip().lower()
    provider = ai.get_provider(key)
    if not supports_vision(key):
        raise ExtractError(vision_reason(key))

    if key in VISION_MAYBE and not api_key:
        raise ExtractError(vision_reason(key))

    # بلا مفتاح لا نُرسل إطلاقًا. لو تركناها لطبقة `ai.chat` لتراجعت إلى
    # المفتاح العام في `.env` — وهذا يعني أن ملف الطالب يُقرأ بمفتاح
    # المالك بلا أن يطلب أحد: تكلفة على غير من طلبها، وبياناته تخرج
    # باسم حساب آخر. الفشل الصريح هنا هو السلوك الصحيح.
    if not api_key and provider.requires_key:
        raise ExtractError(
            f"لا مفتاح محفوظ لمزوّد {provider.label} — أضفه من ⚙️ إعدادات "
            "الذكاء الاصطناعي، أو اختر مزوّدًا آخر تملك مفتاحه."
        )

    notes: list[str] = []

    # ① نحوّل إلى صور
    lowered = (filename or "").lower()
    is_pdf = (mime or "").lower() == "application/pdf" or lowered.endswith(".pdf")
    if is_pdf:
        images, total = await asyncio.to_thread(_pdf_to_images, data)
        if total > MAX_PAGES:
            notes.append(
                f"الملف {total} صفحة — قرأتُ أول {MAX_PAGES} فقط "
                "(حدّ التكلفة). ارفع الباقي في ملف منفصل."
            )
    else:
        if (mime or "").lower().startswith("image/") or lowered.endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        ):
            images, total = [await asyncio.to_thread(_shrink, data)], 1
        else:
            raise ExtractError(
                "هذا النوع لا يمكن قراءته بالرؤية — المقبول: PDF أو صورة "
                "(JPG/PNG/WEBP). ملفات Word/Excel تُقرأ كنصّ مباشرة."
            )

    if not images:
        raise ExtractError("لم أستطع تحويل الملف إلى صفحات — قد يكون تالفًا.")

    # ② صفحة صفحة: القراءة تتراكم، وفشل صفحة لا يُسقط الملف كله
    parts: list[str] = []
    done = 0
    for index, image in enumerate(images, start=1):
        try:
            text = await ai.chat(
                _messages_for(image, provider.api_style),
                max_tokens=min(8000, ai.MAX_OUTPUT_TOKENS),
                provider=key,
                api_key=api_key,
                model=model,
            )
        except ai.AIError as exc:
            if index == 1:
                raise ExtractError(f"تعذّر على الوكيل قراءة الملف: {exc}") from exc
            notes.append(f"توقفتُ عند الصفحة {index} بعد خطأ: {exc}")
            break
        cleaned = clean_text(text)
        if cleaned and EMPTY_MARKER not in cleaned:
            parts.append(cleaned)
        done = index
        if on_progress:
            try:
                on_progress(done, len(images))
            except Exception:              # noqa: BLE001 — العرض لا يكسر العمل
                pass

    if done < len(images):
        notes.append(f"قُرئت {done} صفحة من {len(images)}.")

    text = clean_text("\n\n".join(parts))
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        notes.append("النص طويل جدًا — احتفظت بأوّله فقط للفهرسة.")

    return ExtractResult(
        text=text, pages=total, pages_done=done, notes=tuple(notes)
    )
