"""OCR للملفات المصوّرة (سكانر) — tesseract + poppler.

يعمل تلقائيًا عند توفر `tesseract` و `pdftoppm` على النظام،
ويتراجع بهدوء (يرجع نصًا فارغًا) عند غيابهما حتى لا يتعطّل الرفع.

التثبيت (لكل منصّة أسماء حزمها — لا تستخدم أوامر ديبيان على أندرويد):

    ديبيان/أوبنتو:
        sudo apt-get install -y tesseract-ocr tesseract-ocr-ara poppler-utils fonts-hosny-amiri
    أندرويد/Termux:
        pkg install tesseract poppler python-pillow python-numpy
        # ثم بيانات العربية يدويًا:
        curl -L -o $PREFIX/share/tessdata/ara.traineddata \
          https://github.com/tesseract-ocr/tessdata_fast/raw/main/ara.traineddata
    ماك:
        brew install tesseract tesseract-lang poppler

⚠️ على Termux **لا وجود** لـ`tesseract-ocr` ولا `tesseract-ocr-ara` ولا
   `poppler-utils`؛ الأسماء الصحيحة `tesseract` و`poppler` فقط، وحزمة
   tesseract تأتي ببيانات `eng` وحدها — فيجب تنزيل `ara.traineddata`.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# ⚠️ Pillow **اختياري import-time** — عِلّة حقيقية أُثبتت بالاختبار:
#    `from PIL import Image` هنا كان يقتل البوت كله عند الإقلاع لو غابت
#    Pillow (أو فشل تثبيتها على منصّة بلا عجلة جاهزة). وملف `bot/main.py`
#    يستورد `upload.py` ← `ocr.py`، فكان غياب Pillow = بوت لا يبدأ إطلاقًا
#    بدل بوت يرفض الصور فقط. الآن الغياب يُعطّل مسار الصور وحده.
try:                                        # pragma: no cover - يعتمد النظام
    from PIL import Image, ImageFilter, ImageOps
    PIL_AVAILABLE = True
except ImportError:                          # pragma: no cover
    Image = ImageFilter = ImageOps = None    # type: ignore[assignment]
    PIL_AVAILABLE = False

log = logging.getLogger(__name__)

# ⚠️ اللغة الافتراضية `ara` وليست `ara+eng` — وهذا مقصود ومقاس.
# على ٩ صفحات من مستندين عربيين مصوّرين حقيقيين من Wikimedia Commons:
#     ara       → ٩٣٣٦ حرف عربي،   ٠ حرف لاتيني، نقاء ١٠٠٪
#     ara+eng   → ٦٦٠٩ حرف عربي، ١٧٢٥ حرف لاتيني، نقاء ٧٩٫٣٪
# إضافة `eng` تجعل tesseract يقرأ مقطوعًا عربيًا كلاتيني فينتج هراءً
# مثل `Jal` و `So SNARE` و `gor`، ويخسر ٢٩٪ من النص العربي نفسه.
# المستندات المختلطة تحتاج `OCR_LANGS=ara+eng` في `.env` (مقاس: تحافظ
# على `Python`/`calculus` لكنها تُنقص العربي).
DEFAULT_LANGS = "ara"
DEFAULT_DPI = 300          # يُستبدل تلقائيًا بدقة الصورة المضمّنة إن أمكن
MIN_RENDER_DPI = 150
MAX_RENDER_DPI = 600
SCRIPT_SAMPLE_PAGES = 8        # عدد الصفحات المُصوّت عليها في كشف اللغة

# محارف التحكّم الخفيّة التي يبثّها tesseract حول المقاطع المزدوجة الاتجاه
# (LRM/RLM/علامات التضمين/مبدّل الاتجاه/‏BOM). لا تُرى بالعين لكنها تكسر
# المطابقة في الفهرسة والبحث، وتُظهر النص مقلوبًا في بعض العارضات.
_BIDI_CONTROLS = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f"      # ZWSP ZWNJ ZWJ LRM RLM
    "\u202a\u202b\u202c\u202d\u202e"        # LRE RLE PDF LRO RLO
    "\u2066\u2067\u2068\u2069"              # LRI RLI FSI PDI
    "\ufeff]"                               # BOM
)


def clean_ocr_text(text: str) -> str:
    """ينظّف مخرجات OCR دون المساس بترتيب الكلمات.

    ملاحظة مهمة: مخرجات `tesseract` بالعربية تخرج **بالترتيب المنطقي الصحيح**
    (وليس البصري)، لذلك لا نُمرّرها على `bidi.get_display` إطلاقًا — تجريبها
    على مخرجات سليمة قلبها إلى `ةلادلا ريغت لدعم سيقت ةقتشملا`.
    التنظيف هنا يقتصر على إزالة محارف التحكّم الخفيّة وتجميع الأسطر الفارغة.
    """
    if not text:
        return ""
    text = _BIDI_CONTROLS.sub("", text)
    # تجميع أكثر من سطر فارغ متتالٍ في سطر واحد، وقصّ المسافات الزائدة
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln.strip() or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- #
# معالجة ما قبل OCR: تسوية الإضاءة + تقويم الميل
#
# قياسات على مستند عربي (١٧ كلمة مستهدفة × ١٤ سيناريو = ٢٣٨) عبر
# نفس مسار البوت، PDF مصوّر حقيقي:
#     السيناريو            الحالي     بعد المعالجة
#      ميل 0°               14/17        14/17
#      ميل 5°               10/17        16/17
#      ميل 8°                5/17        14/17
#     ميل 12°                0/17        15/17
#      ميل -6°              12/17        14/17
#      مظلم + ميل 5°         5/17        14/17
#      ضوضاء sigma=20       14/17        14/17
#      ضبابية               13/17        13/17
#      صفحة فارغة            0/17         0/17   (لا إنذار كاذب)
#      المجموع             125/238      184/238   (+47%)
# ولا سيناريو واحد تدهور.
#
# الخلاصة العملية:
#  • **الميل هو أسوأ عامل مُفسد** للمسح الضوئي، وتقويمه أكبر مصدر للمكسب.
#  • الضوضاء والضبابية وضغط JPEG يتعامل معها tesseract جيدًا أصلًا.
#  • قسمة الصورة على خلفية مُنعّمة (illumination flattening) **بلا شرط
#    مضرّة**: قِيست فأفسدت صفحات سليمة (١٥→٠). تُستخدم فقط مشروطةً
#    بقياس تدرّج الإضاءة — انظر قسم `GRADIENT_THRESHOLD` أسفله.
#  • تحذير قياس: نموذج «تظليل» يُركّب أسود فوق النص (composite) يمسح
#    المحتوى فعلًا (نسبة حبر ١٫٠٠) فلا يصلح لقياس OCR. النموذج الصحيح
#    ضربٌ في التدرّج (multiply) الذي يحفظ الحبر ويخفض التباين.
# --------------------------------------------------------------------------- #

SKEW_MAX_ANGLE = 12.0        # أقصى زاوية يُبحث عنها
SKEW_STEP = 0.25             # دقّة البحث بالدرجات
SKEW_DEADZONE = 0.7          # أقل من هذا لا نُدير الصفحة
SKEW_MIN_CONFIDENCE = 1.35   # نسبة تباين أدنى لقبول الزاوية
DARK_PAGE_MEAN = 110.0       # متوسط سطوع تُعتبر دونه الصفحة معتمة
LOCAL_THRESHOLD_BLUR = 25    # نصف قطر تنعيم الخلفية للعتبة المحلية


def _deskew_binary_mask(image):
    """قناع ثنائي للحبر بعتبة **محلية** (تطرح خلفية مُنعّمة).

    العتبة المحلية ضرورية: العتبة العامة تجعل تدرّج الإضاءة يبدو
    كأنه ميل، فتُقاس زاوية خاطئة (قِيس: صفحة مضاءة وغير مائلة
    قُدّرت بـ -12°).
    """
    import numpy as np

    gray = image.convert("L")
    background = gray.filter(ImageFilter.GaussianBlur(LOCAL_THRESHOLD_BLUR))
    mask = np.asarray(gray, dtype=np.float32) < np.asarray(background, dtype=np.float32) * 0.92
    return mask.astype(np.float32)


def estimate_skew(
    image,
    max_angle: float = SKEW_MAX_ANGLE,
    step: float = SKEW_STEP,
    min_confidence: float = SKEW_MIN_CONFIDENCE,
) -> float:
    """يقدّر زاوية ميل الصفحة بتحليل projection profile.

    في الصفحة المستقيمة تكون أسطر النص حادّة، فيبلغ تباين مجاميع الصفوف
    أقصى قيمة عند الزاوية الصحيحة. يُرجع الزاوية التي تُمرَّر إلى
    `Image.rotate()` لتقويم الصفحة، أو **صفرًا** إذا لم تتجاوز الزاوية
    حدّ الثقة — وهو ما يمنع «اكتشاف» ميل وهمي سببه تدرّج إضاءة.
    """
    try:
        import numpy as np
    except ImportError:
        return 0.0

    try:
        binary = _deskew_binary_mask(image)
        if binary.sum() < 500:          # صفحة شبه فارغة — لا يوجد ما يُقاس
            return 0.0
        # تصغير للسرعة فقط؛ الزاوية لا تتأثر
        h, w = binary.shape
        scale = min(1.0, 900.0 / max(h, w))
        if scale < 1.0:
            small = Image.fromarray((binary * 255).astype(np.uint8)).resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
            binary = np.asarray(small, dtype=np.float32) / 255.0
        pil = Image.fromarray((binary * 255).astype(np.uint8))

        def variance_at(angle: float) -> float:
            rotated = np.asarray(
                pil.rotate(angle, expand=False, fillcolor=0, resample=Image.BILINEAR),
                dtype=np.float32,
            ) / 255.0
            return float(rotated.sum(axis=1).var())

        zero_variance = variance_at(0.0)
        best_angle, best_variance = 0.0, zero_variance
        angle = -max_angle
        while angle <= max_angle + step / 2:
            variance = variance_at(angle)
            if variance > best_variance:
                best_angle, best_variance = angle, variance
            angle += step

        confidence = best_variance / zero_variance if zero_variance > 0 else 0.0
        if confidence < min_confidence:
            log.debug("تجاهل ميل %.2f° — الثقة %.2f تحت الحد %.2f",
                      best_angle, confidence, min_confidence)
            return 0.0
        return best_angle
    # أضيق من Exception المتعمّد: أخطاء البرمجة (NameError/AttributeError) لازم
    # تظهر في الاختبارات بدل ما تُبتلع وتُعطّل التقويم في صمت.
    except (OSError, ValueError) as exc:
        log.debug("تقدير الميل فشل: %s", exc)
        return 0.0


def deskew(image, deadzone: float = SKEW_DEADZONE):
    """يقوّم ميل الصفحة. يُرجع `(الصورة, الزاوية المطبَّقة)`."""
    angle = estimate_skew(image)
    if abs(angle) < deadzone:
        return image, 0.0
    return image.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC), angle


def normalize_exposure(image):
    """يفتح الصفحة المعتمة فقط، ويترك الصفحات الطبيعية كما هي.

    الصفحة شديدة العتمة تجعل tesseract يفشل في تقسيم الصفحة بالكامل
    (قِيس عمليًا: نص فارغ تمامًا). القياس هنا رخيص ولا يمسّ الصفحات
    السليمة، فلا يُطبَّق إلا عند تجاوز العتمة.
    """
    try:
        import numpy as np

        gray = image.convert("L")
        if float(np.asarray(gray, dtype=np.float32).mean()) >= DARK_PAGE_MEAN:
            return image
        return ImageOps.autocontrast(image, cutoff=1)
    except ImportError:
        return image
    except (OSError, ValueError) as exc:
        log.debug("تسوية الإضاءة فشلت: %s", exc)
        return image


# --------------------------------------------------------------------------- #
# تسوية تدرّج الإضاءة (ظل اليد/الهاتف في تصوير الموبايل)
#
# تدرّج الإضاءة يُربك tesseract فيهلوس نصًا متكرّرًا — قِيس على صفحة
# امتحان ثانوية عامة حقيقية:
#     الحالة        قياس التدرّج    tesseract مباشرة    بعد التسوية
#      نظيف              13٫1          100٪ / 100٪        25٪ / 36٪  ← تضرّ
#      ظل خفيف           36٫7           14٪ /   9٪        32٪ / 39٪  ← تنفع
#      ظل متوسط          48٫6           12٪ /   7٪        31٪ / 36٪  ← تنفع
#      ظل قوي            90٫1           12٪ /  15٪        22٪ / 34٪  ← تنفع
#
# لذلك التسوية **مشروطة** بقياس التدرّج: تُطبَّق فقط عندما يتجاوز الحد،
# فتبقى الصفحة النظيفة على ١٠٠٪ ويتحسّن المظلوم ~٢٫٥ ضعفًا.
#
# تحذير: التحويل الثنائي (binarization) ليس بديلًا — قِيس على نفس الصفحة
# بخمسة أنصاف أقطار × ثلاثة هوامش، فأفضل إعداد أعطى ٢٠٪ مقابل ١٠٠٪
# لـ tesseract مباشرة. لذلك لا يُستخدم إلا لإعادة المحاولة عند النص الفارغ.
# --------------------------------------------------------------------------- #

GRADIENT_THRESHOLD = 25.0    # فوق هذا تُعتبر الإضاءة متدرّجة


def illumination_gradient(image) -> float:
    """أقصى فرق سطوع بين أرباع الصفحة — مقياس رخيص لتدرّج الإضاءة."""
    try:
        import numpy as np

        a = np.asarray(image.convert("L"), dtype=np.float32)
        h, w = a.shape
        if h < 4 or w < 4:
            return 0.0
        halves_h, halves_w = h // 2, w // 2
        quadrants = (
            a[:halves_h, :halves_w].mean(), a[:halves_h, halves_w:].mean(),
            a[halves_h:, :halves_w].mean(), a[halves_h:, halves_w:].mean(),
        )
        return float(max(quadrants) - min(quadrants))
    except ImportError:
        return 0.0
    except (OSError, ValueError) as exc:
        log.debug("قياس التدرّج فشل: %s", exc)
        return 0.0


def flatten_illumination(image):
    """يقسم الصورة على خلفية مُنعّمة فيُلغى تدرّج الإضاءة.

    نصف قطر التنعيم يتناسب مع عرض الصفحة (لا قيمة ثابتة) — قيمة صغيرة
    على صفحة ٣٠٠dpi تُنتج عتبة متعرّجة تفتّت الحروف.
    """
    try:
        import numpy as np

        gray = image.convert("L")
        radius = max(40, gray.size[0] // 12)
        background = gray.filter(ImageFilter.GaussianBlur(radius))
        a = np.asarray(gray, dtype=np.float32) + 1.0
        b = np.asarray(background, dtype=np.float32) + 1.0
        out = np.clip(a / b * 235.0, 0, 255).astype(np.uint8)
        return Image.fromarray(out)
    except ImportError:
        return image
    except (OSError, ValueError) as exc:
        log.debug("تسوية التدرّج فشلت: %s", exc)
        return image


# --------------------------------------------------------------------------- #
# عتبة محلية (binarization) — شبكة الأمان الأخيرة
#
# قِيس على صفحة عربية بإضاءة متدرّجة (خلفية رمادية لا بيضاء):
#     tesseract مباشرة       → نص فارغ تمامًا (٠/١٧)
#     بعد العتبة المحلية     → ١٣/١٧
# السبب أن متوسط سطوع الصفحة ١٨٣ (أي «طبيعي»)، لكن تباين الخلفية
# يُربك تقسيم tesseract الداخلي. لذلك لا تُطبَّق هذه الخطوة إلا
# **بعد** فشل المحاولة الأولى، فلا تكلّف شيئًا في الحالة الطبيعية.
# --------------------------------------------------------------------------- #

BINARIZE_BLOCK = 40      # نصف قطر تنعيم الخلفية
BINARIZE_C = 18          # هامش السواد المطلوب تحت الخلفية
BLANK_PAGE_STD = 15.0    # أقل من هذا تُعتبر الصفحة فارغة (لا نُعيد المحاولة)

# تعميق مشروط بالفشل: كتب حقيقية تبدأ بغلاف وفراغات وفهارس، فلا يظهر
# المتن إلا بعد الصفحة ١٥ أحيانًا (قِيس: كتاب ٦٤٥ صفحة متنُه من ~٢٥،
# فكانت نتيجة أول ١٥ صفحة صفرًا رغم سلامة الـ OCR). عندها فقط نجرّب
# دفعات أعمق — بحد أقصى دفعتين — بنفس لغة الدفعة الأولى. المستندات
# العادية لا تدفع شيئًا لأن الدفعة الأولى تكفيها.
DEEP_SCAN_MIN_CHARS = 100   # أقل من هذا من دفعة نُعمّق للدفعة التالية
DEEP_SCAN_MAX_BATCHES = 2   # دفعات إضافية كحد أقصى (حتى ٣×max_pages صفحة)


def adaptive_binarize(image, block: int = BINARIZE_BLOCK, c: int = BINARIZE_C):
    """يحوّل الصورة إلى أبيض/أسود بعتبة محلية (تطرح خلفية مُنعّمة)."""
    import numpy as np

    gray = image.convert("L")
    background = gray.filter(ImageFilter.GaussianBlur(block))
    a = np.asarray(gray, dtype=np.int16)
    b = np.asarray(background, dtype=np.int16)
    return Image.fromarray(np.where(a < b - c, 0, 255).astype(np.uint8))


def has_ink(image) -> bool:
    """هل في الصفحة محتوى أصلًا؟ يمنع إعادة المحاولة على صفحة بيضاء."""
    try:
        import numpy as np

        return float(np.asarray(image.convert("L"), dtype=np.float32).std()) > BLANK_PAGE_STD
    except (ImportError, OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# فكّ التواء أخفّ (افتراضي، OCR_DEWARP=0 للإيقاف): نموذج UVDoc عبر ONNX
# Runtime (٣٢MB) بدل PaddleOCR كاملًا (~٦٠٠MB)، يغذّي tesseract.
#
# مقاس على كتاب «الامتحان» نفسه (لقطات موبايل، صفحتان في اللقطة)،
# أحرف عربية، بمسار البوت الكامل (prepare + tesseract):
#
#     صفحة PDF     الأساس     عتبة فقط     UVDoc+عتبة     Paddle+UVDoc
#        ٦         ١٣٢٧        ١٨٧٩         ١٩٠٣           ١٧٣٣
#        ٢٠        ١٩١٠        ١٩٧٦         ٢٢٩٥           ١٥٩٩
#        ١٠        ١٩٩٩        ٢٠٥٤         ٢٣٤٧             —
#        ٣٠        ١٣٤٣        ١٤٦٦         ١٤٠٩             —
#
# دروس القياس (لا تُكسَر):
#  * فكّ الالتواء وحده **أسوأ** من الأساس (٨٨٤ مقابل ١٣٢٧ على صفحة ٦):
#    إعادة التشكيل ترقّق الحبر. العتبة المحلية بعده جزء من الوصفة.
#  * العتبة وحدها تكسب جزءًا لكنها **تضرّ صفحة مسطّحة** (−٣٦٪) —
#    لذلك لا تخرج أبدًا خارج مسار فكّ الالتواء هذا.
#  * تصغير الصورة قبل فكّ الالتواء انهار (٦٢٩ بدل ٢٣٤٧) — حجم كامل فقط.
#  * صفحات مسطّحة طولية لا يمسّها المسار أصلًا (بوابة العرض أدناه)،
#    وحتى عند تجربته عليها لم يضرّ (٦١٣/١٠٢٩/٥٦٤ مقابل ٤٦٩/١٠٧٢/٥٦٥).
#
# التكلفة: عامل فرعي ذروته ~٠٫٨GB على لقطات ٤٠٠٠px (البوت لا يتأثر —
# عملية منفصلة تُغلق بنهاية الاستخراج)، +١٫٣ث فكّ التواء للصفحة.
# التشغيل: يعمل افتراضيًا مع tesseract وقت التشغيل (`OCR_DEWARP=0`
# للإيقاف) متى توفّر onnxruntime (ضمن requirements) — النموذج
# يُنزَّل تلقائيًا أول مرة (~٣٢MB) إلى `models/uvdoc.onnx` أو مسار
# `UVDOC_MODEL_PATH`. أي فشل ⇒ تراجع صامت لمسار tesseract العادي.
# --------------------------------------------------------------------------- #

UVDoc_MIN_ASPECT = 1.15      # عرض/ارتفاع فوقه تُعتبر الصفحة «لقطة صفحتين»
UVDoc_STARTUP_TIMEOUT = 120  # ثانية لإقلاع العامل وتحميل النموذج
UVDoc_PAGE_TIMEOUT = 180     # ثانية كحد أقصى لفكّ التواء صفحة واحدة
UVDoc_MODEL_URL = (
    "https://huggingface.co/PaddlePaddle/UVDoc_onnx/resolve/main/inference.onnx"
)
# بصمة النموذج الرسمي أعلاه — يُرفض أي ملف لا يطابقها (سلامة توريد)
UVDoc_MODEL_SHA256 = "54cab30dc2cf347b4f3d6971c833b1c3d84f5ef17280f1f6d15796bff20e63d6"


def _uvdoc_model_path():
    """مسار نموذج UVDoc: `UVDOC_MODEL_PATH` أو `models/uvdoc.onnx`."""
    import os
    from pathlib import Path

    override = os.environ.get("UVDOC_MODEL_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "models" / "uvdoc.onnx"


def _uvdoc_available() -> bool:
    """هل onnxruntime مثبّت؟ (النموذج يُنزَّل عند الحاجة — انظر الأسفل)."""
    import importlib.util

    return importlib.util.find_spec("onnxruntime") is not None


def _file_sha256(path) -> str:
    """بصمة ملف تُقرأ على دفعات (النموذج ٣٢MB)."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_uvdoc_model() -> bool:
    """يضمن وجود النموذج محليًا وسليمًا، وينزّله أول مرة. False عند التعذّر.

    سلامة التوريد: الملف المنزّل يُتحقّق منه ببصمة SHA256 مثبّتة
    بالكود (UVDoc_MODEL_SHA256) قبل أي استخدام — ملف تالف/مبدَّل
    يُرفض ويُعطَّل فكّ الالتواء بهدوء بدل تشغيل نموذج مجهول.
    مسار `UVDOC_MODEL_PATH` الصريح ملفٌ يوفّره المشغّل بنفسه فيُوثق به.
    """
    import os

    path = _uvdoc_model_path()
    trusted_local = bool(os.environ.get("UVDOC_MODEL_PATH", "").strip())
    try:
        if path.exists() and path.stat().st_size > 1_000_000:
            if trusted_local or _file_sha256(path) == UVDoc_MODEL_SHA256:
                return True
            log.warning("OCR: بصمة نموذج UVDoc غير مطابقة — إعادة تنزيل")
        import urllib.request

        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("OCR: تنزيل نموذج UVDoc (~٣٢MB) إلى %s", path)
        tmp = path.with_suffix(".onnx.tmp")
        req = urllib.request.Request(UVDoc_MODEL_URL, headers={"User-Agent": "zakr-bot"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(tmp, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        if tmp.stat().st_size < 1_000_000 or _file_sha256(tmp) != UVDoc_MODEL_SHA256:
            log.warning("OCR: نموذج UVDoc المنزّل غير سليم (حجم/بصمة) — يُرفض")
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True
    except (OSError, ValueError) as exc:
        log.warning("OCR: تعذّر توفير نموذج UVDoc (%s) — بلا فكّ التواء", exc)
        return False


class _DewarpSession:
    """جلسة عامل فكّ التواء فرعي واحد — تُفتح كسلًا وتُغلق بنهاية الاستخراج.

    ⚠️ لماذا عملية فرعية؟ درس مقاس من تجارب الاستدلال الثقيل: داخل
    عملية البوت تكدّست الذاكرة حتى الـ OOM ولم تُحرَّر. العامل هنا
    يعيش لاستخراج واحد ثم يموت، وموته أو فشله لا يقتل البوت —
    الصفحة تتراجع للمسار العادي.
    """

    def __init__(self, model_path):
        import queue
        import subprocess
        import sys
        import tempfile
        import threading

        self._queue: queue.Queue = queue.Queue()
        self._dead = False
        self._counter = 0
        self._tmpdir = tempfile.mkdtemp(prefix="uvdoc_ocr_")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "bot.services.ocr_uvdoc_worker", str(model_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        try:
            ready = self._queue.get(timeout=UVDoc_STARTUP_TIMEOUT)
        except queue.Empty:
            self.close()
            raise RuntimeError("عامل UVDoc لم يُقلع في الوقت")
        if ready != "READY":
            self.close()
            raise RuntimeError(f"عامل UVDoc ردّ عند الإقلاع: {ready!r}")

    def _pump(self) -> None:
        try:
            for line in self._proc.stdout:
                self._queue.put(line.strip())
        except (OSError, ValueError):
            pass

    def dewarp(self, image):
        """يفكّ التواء صورة واحدة. None عند أي فشل (الصفحة تتراجع)."""
        import os
        import queue

        if self._dead or self._proc.poll() is not None:
            return None
        self._counter += 1
        in_path = os.path.join(self._tmpdir, f"{self._counter:04d}.png")
        try:
            image.save(in_path)
            self._proc.stdin.write(in_path + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            log.warning("OCR: تعذّر إرسال صفحة لعامل UVDoc (%s)", exc)
            self._dead = True
            return None
        try:
            reply = self._queue.get(timeout=UVDoc_PAGE_TIMEOUT)
        except queue.Empty:
            log.warning("OCR: عامل UVDoc تجاوز %dث — يُقتل ويُعطَّل", UVDoc_PAGE_TIMEOUT)
            self._dead = True
            self.close()
            return None
        if not reply.startswith("OK "):
            log.warning("OCR: فكّ التواء صفحة فشل (%s) — تراجع", reply[:120])
            return None
        try:
            with Image.open(reply[3:]) as flat:
                return flat.copy()
        except (OSError, ValueError) as exc:
            log.warning("OCR: تعذّرت قراءة صفحة مفكوكة (%s)", exc)
            return None

    def close(self) -> None:
        """يُغلق العامل ويمسح المؤقتات. آمن للاستدعاء المتكرر."""
        import shutil

        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
        except (OSError, AttributeError):
            pass
        shutil.rmtree(getattr(self, "_tmpdir", ""), ignore_errors=True)


def _start_dewarp_session() -> _DewarpSession | None:
    """يفتح جلسة فكّ التواء. None = غير متاح (المسار العادي يكفي)."""
    if not _uvdoc_available():
        return None
    if not _ensure_uvdoc_model():
        return None
    try:
        return _DewarpSession(_uvdoc_model_path())
    except (OSError, RuntimeError) as exc:
        log.warning("OCR: تعذّر بدء عامل UVDoc (%s) — بلا فكّ التواء", exc)
        return None


@dataclass(frozen=True)
class OcrResult:
    text: str
    pages: int
    pages_ocr: int
    available: bool  # هل الـ OCR متاح أصلًا على هذا النظام؟


@lru_cache(maxsize=1)
# ─────────────────────────────────────────────────────────────────────
#  دعم OCR المنزَّل محليًا (لوحات الاستضافة بلا root وبلا apt)
# ─────────────────────────────────────────────────────────────────────
#  على Wispbyte/Pterodactyl لا يمكن `apt install tesseract`. لكن
#  `ocr_fetch.py` ينزّله في `.ocr/` داخل المشروع ويشغّله بـ LD_LIBRARY_PATH.
#  هنا نقرأ مساراته تلقائيًا عند الاستيراد، فتصبح `is_available()` صحيحة
#  بلا أي إعداد يدوي من المستخدم.
# مجلد OCR المنزَّل — يُشارك ocr_fetch.py نفس المتغيّر (ZAKR_OCR_DIR)
def ocr_dir_from_env(root: Path) -> Path:
    import os

    return Path(os.environ.get("ZAKR_OCR_DIR") or (root / ".ocr")).resolve()


OCR_DIR = Path(__file__).resolve().parents[2] / ".ocr"


def _load_local_ocr_env() -> None:
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    ocr_dir = ocr_dir_from_env(root)
    if not ocr_dir.is_dir():
        return

    bindir = ocr_dir / "usr/bin"
    if bindir.is_dir():
        path = os.environ.get("PATH", "")
        if str(bindir) not in path.split(os.pathsep):
            os.environ["PATH"] = f"{bindir}{os.pathsep}{path}"

    libdirs = [str(d) for d in (ocr_dir / "usr/lib").glob("*/") if d.is_dir()]
    libdirs.append(str(ocr_dir / "usr/lib"))
    existing = [d for d in libdirs if Path(d).is_dir()]
    if existing:
        joined = os.pathsep.join(existing)
        current = os.environ.get("LD_LIBRARY_PATH", "")
        if joined not in current:
            os.environ["LD_LIBRARY_PATH"] = f"{joined}{os.pathsep}{current}" if current else joined

    if not os.environ.get("TESSDATA_PREFIX"):
        for cand in sorted((ocr_dir / "usr/share").glob("tesseract-ocr/*/tessdata")):
            os.environ["TESSDATA_PREFIX"] = str(cand)
            break


_load_local_ocr_env()


@lru_cache(maxsize=1)
def _binary_works(name: str, flag: str = "--version") -> bool:
    """هل الأمر موجود **ويعمل فعلًا**؟ لا يكفي `which`.

    عِلّة حقيقية: `which` ترجع مسارًا حتى لو كان الملف معطوبًا (مكتبة مشتركة
    ناقصة على أندرويد مثلًا)، فيظنّ البوت أن OCR متاح ثم يفشل على كل ملف.
    نُشغّل الأمر ونتحقق من كود الخروج.
    """
    path = shutil.which(name)
    if path is None:
        return False
    try:
        proc = subprocess.run(
            [path, flag], capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("الأمر %s موجود لكنه لا ينفّذ: %s", name, exc)
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        log.warning("الأمر %s فشل (كود %s): %s", name, proc.returncode, detail[:300])
        return False
    return True


@lru_cache(maxsize=1)
def is_available() -> bool:
    """يتحقق أن tesseract و pdftoppm موجودان **ويعملان فعلًا**.

    وPillow شرط مستقل: بدونها لا تهيئة للصور ولا لفّ صفحات PDF — والبوت
    يبدأ سليمًا لكن رفع الملفات المصوّرة يُرفض برسالة مفهومة.
    """
    if not PIL_AVAILABLE:
        return False
    return _binary_works("tesseract") and _binary_works("pdftoppm", "-v")


def refresh_local_ocr_env() -> None:
    """يعيد قراءة مسارات OCR المحلي ويُبطل المخابئ.

    **عِلّة حقيقية أُثبتت بالاختبار:** `_load_local_ocr_env()` تُنفَّذ عند
    الاستيراد. لو نُزِّل tesseract **بعد** الاستيراد (وهو ما يحدث في
    `self_install.py`: نستورد البوت للفحص ← ثم ننزّل)، تبقى المسارات غير
    مطبَّقة والمخابئ تقول «غير متاح» — فيُنزَّل ١٦٠ ميجا ثم يُقال للمستخدم
    إن OCR معطّل. هذه الدالة تُغلق تلك الفجوة.
    """
    _load_local_ocr_env()
    _binary_works.cache_clear()
    is_available.cache_clear()
    installed_languages.cache_clear()


def missing_reason() -> str:
    """سبب تعطّل OCR بالعربية الواضحة — يُعرض للمستخدم بدل «غير متاح»."""
    if not PIL_AVAILABLE:
        return "حزمة Pillow غير مثبّتة (pip install Pillow)"
    return "tesseract أو pdftoppm غير مثبّت على النظام"


def arabic_available() -> bool:
    """هل بيانات اللغة العربية (ara) مثبّتة؟ بدونها OCR العربي يرجع فراغًا."""
    return any("ara" == lang.strip() for lang in installed_languages())


def tessdata_dir():
    """مجلد tessdata المستخدم فعلًا — ليضع فيه `self_install.py` بيانات العربية.

    الترتيب: `TESSDATA_PREFIX` الصريح ← مجلد OCR المحلي `.ocr/` ← مسارات
    التوزيعات الشائعة (ديبيان/أوبنتو ٤و٥، أندرويد/Termux، Alpine، macOS).
    يرجع `None` لو لم يوجد أي منها — عندها لا نكتب في مكان عشوائي.
    """
    import os
    from pathlib import Path

    candidates = []
    explicit = os.environ.get("TESSDATA_PREFIX", "").strip()
    if explicit:
        candidates.append(Path(explicit))

    root = Path(__file__).resolve().parents[2]
    ocr_share = root / ".ocr" / "usr" / "share"
    candidates += sorted(ocr_share.glob("tesseract-ocr/*/tessdata"))
    candidates += [
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/usr/share/tesseract-ocr/tessdata"),
        Path("/usr/share/tessdata"),
        Path(os.environ.get("PREFIX", "/usr")) / "share" / "tessdata",
        Path("/opt/homebrew/share/tessdata"),
        Path("/usr/local/share/tessdata"),
    ]
    for cand in candidates:
        if cand.is_dir() and cand.name != "":
            return cand
    return None


@lru_cache(maxsize=1)
def installed_languages() -> list[str]:
    try:
        import pytesseract

        return list(pytesseract.get_languages(config=""))
    except Exception as exc:
        log.warning("تعذّر قراءة لغات tesseract: %s", exc)
        return []


def _script_of(image) -> str:
    """يقرأ اسم خط الصفحة عبر OSD. يرجع سلسلة فاضية عند الفشل."""
    try:
        import pytesseract

        info = pytesseract.image_to_osd(image)
    except Exception as exc:
        log.debug("كشف الخط فشل: %s", exc)
        return ""
    for line in info.splitlines():
        if line.startswith("Script:"):
            return line.split(":", 1)[1].strip()
    return ""


def detect_languages(images, default: str = DEFAULT_LANGS, sample: int = SCRIPT_SAMPLE_PAGES) -> str:
    """يختار لغة OCR من **اسم خط** الصفحة، بتصويت أغلبية على أول صفحات.

    لماذا اسم الخط وليس «ثقة» الكشف:
    قِيس على امتحانات ثانوية عامة مصوّرة حقيقية (١٢ صفحة لكل ملف):
        ملف عربي      → Script: Arabic ×١١ من ١١ | ثقة ١٢–٦٧
        ملف إنجليزي   → Script: Latin  ×١١ من ١٢ | ثقة ٠٫٣–٣٫٧ ← !
    اسم الخط صحيح عمليًا دائمًا، بينما ثقة الملف الإنجليزي **أقل من ٤**،
    فأي عتبة ثقة معقولة كانت ترفضه وتُسقطه على `ara+eng` — وهو تحديدًا
    الإعداد الذي يبثّ هراءً لاتينيًا في النص العربي.

    التصويت على أكثر من صفحة يمتص الخطأ الفردي (رُصد `Cyrillic` مرة
    واحدة من ١٢ في ملف إنجليزي).

    ثلاث قواعد إضافية من قياس ١٧ مستندًا حقيقيًا (كتب ومجلات وامتحانات):
      • **العيّنة ٨ صفحات لا ٣**: كتب كثيرة تبدأ بغلاف وصفحة حقوق فيها
        رابط لاتيني وفراغات، والمتن يبدأ صفحة ٥+. بعينة ٣ رُصد ديوان
        عربي صوّت `eng` فأخرج صفر حرف عربي، وبعينة ٨ صارت أصوات
        `Arabic` أغلبية واضحة.
      • **نُصوّت على الخطوط المعروفة فقط** (`Arabic`/`Latin`): ضوضاء
        OSD على الصفحات العربية متكررة وموثقة (`Japanese` على غلاف،
        `Katakana`، و`Cyrillic` ٣ مرات في مجلة عربية) — ولو نافست
        الخطوط المعروفة لسرقت القرار أو تركته لترتيب `set` العشوائي.
        بلا أي صوت معروف نرجع للافتراضي.
      • **التعادل يُحسم للافتراضي** حتميًا، لا بترتيب مجموعة.

    المستند المختلط عربي+إنجليزي يُقرأ `ara` (يخسر المصطلحات اللاتينية
    لكن يحافظ على ٢٩٪ من النص العربي). لضبطه يدويًا: `OCR_LANGS=ara+eng`.
    """
    if isinstance(images, Image.Image):
        images = [images]

    mapping = {"Arabic": "ara", "Latin": "eng"}
    counts: dict[str, int] = {}
    raw_votes: list[str] = []
    for image in list(images)[:sample]:
        script = _script_of(image)
        if script:
            raw_votes.append(script)
        lang = mapping.get(script)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return default

    best = max(counts.values())
    winners = [lang for lang, count in counts.items() if count == best]
    if len(winners) == 1:
        chosen = winners[0]
    elif default in winners:
        chosen = default
    else:
        chosen = "ara" if "ara" in winners else sorted(winners)[0]
    log.debug("كشف الخط: %s (معروف %s) ⇒ %s", raw_votes, counts, chosen)
    return chosen


def pick_languages(preferred: str = DEFAULT_LANGS) -> str:
    """يختار اللغات المثبتة فعلًا من القائمة المطلوبة."""
    installed = installed_languages()
    if not installed:
        return preferred
    wanted = [lang.strip() for lang in preferred.split("+") if lang.strip()]
    chosen = [lang for lang in wanted if lang in installed]
    return "+".join(chosen) if chosen else "+".join(installed[:2])


def detect_render_dpi(data: bytes, fallback: int = DEFAULT_DPI) -> int:
    """يحسب دقة الرسم من دقة الصورة المضمّنة في الصفحة.

    `pdf2image` يرسم حسب مقاس صفحة PDF بالنقاط، لا حسب دقة المسح.
    لذلك ملف مسحوب بـ ٩٠٠×١٤٢٤ بكسل على صفحة ١٫٥ بوصة يُرسم بـ
    `dpi=300` إلى ٤٥٠×٧١١ فقط — **نصف دقته الأصلية**، وقِيس أن هذا
    يخسر ١١٪ من النص العربي. والعكس صحيح: كتاب بدقة أصلية ١٦٩dpi
    يُكبَّر ٣٫٥ مرة عند ٦٠٠ فيصير ٥١٠٠×٦٦٠٠ بلا فائدة تُذكر (٩٧٪→١٠٠٪).

    القاعدة: نرسم بالدقة الأصلية، محصورة بين ١٥٠ و٦٠٠.
    """
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if not reader.pages:
            return fallback
        page = reader.pages[0]
        page_width_pt = float(page.mediabox.width)
        if page_width_pt <= 0:
            return fallback

        widest = 0
        xobjects = page.get("/Resources", {}).get("/XObject")
        for item in (xobjects.get_object().values() if xobjects else []):
            try:
                obj = item.get_object()
                if obj.get("/Subtype") == "/Image":
                    widest = max(widest, int(obj.get("/Width") or 0))
            except Exception:  # كائن تالف — نتجاوزه
                continue
        if widest <= 0:
            return fallback

        native = 72.0 * widest / page_width_pt
        return int(max(MIN_RENDER_DPI, min(MAX_RENDER_DPI, round(native))))
    except Exception as exc:  # لا نكسر الرفع بسبب فحص اختياري
        log.debug("تعذّر حساب دقة الرسم: %s", exc)
        return fallback


def _report_progress(on_progress, done: int) -> None:
    """يُعلم مستدعيًا اختياريًا بعدد الصفحات المنجزة — فشله لا يكسر OCR."""
    if on_progress is None:
        return
    try:
        on_progress(done)
    except Exception as exc:  # واجهة التقدم تجميلية — لا تُسقط قراءة
        log.debug("خطأ في مُبلّغ التقدم: %s", exc)


def _extract_text_from_pdf(
    data: bytes,
    max_pages: int = 15,
    dpi: int | None = None,
    langs: str = "",
    fix_skew: bool = True,
    dewarp: bool = False,
    on_progress=None,
) -> OcrResult:
    """يحوّل صفحات PDF إلى صور ثم يستخرج النص منها.

    `dpi=None` (الافتراضي) يحسب الدقة من الصورة المضمّنة — انظر
    `detect_render_dpi`. و`langs=""` (الافتراضي) يكشف لغة الصفحات
    الأولى تلقائيًا — انظر `detect_languages`. الرسم والمعالجة
    صفحةً-صفحة (حماية من OOM على الصفحات العملاقة)، ولو أعطت أول
    دفعة أقل من `DEEP_SCAN_MIN_CHARS` حرفًا يعمّق لدفعات أبعد —
    انظر `DEEP_SCAN_*`. لا يرمي استثناءً: عند أي فشل يرجع نصًا فارغًا.

    `dewarp=True` يمرّر الصفحات العريضة (لقطات صفحتين من موبايل) على
    فكّ التواء UVDoc قبل القراءة — اختياري وخفيف، انظر قسم UVDoc.
    """
    if not is_available():
        return OcrResult("", 0, 0, False)
    if not data:
        return OcrResult("", 0, 0, True)

    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
    except ImportError as exc:
        log.warning("مكتبات OCR غير مثبّتة: %s", exc)
        return OcrResult("", 0, 0, False)

    used_dpi = detect_render_dpi(data) if dpi is None else dpi

    # ⚠️ الرسم صفحةً-صفحة، لا دفعة واحدة — وهذا مقصود ومقاس:
    # صفحات مصوّرة عملاقة (مخطوطة ٣٨×٢١ بوصة ≈ ٧٧٠٠ بكسل عرضًا)
    # رسمُ ١٥ منها دفعة واحدة قتل العملية بـ OOM (137) على ٢GB RAM
    # لأن كل صورة ~١٣٠MB. صفحة في الذاكرة كل مرة آمن، وثمنه استدعاء
    # poppler إضافي لكل صفحة (أرخص بكثير من زمن OCR نفسه).
    def render_page(page_no: int, render_dpi: int):
        """يرسم صفحة واحدة. None = لا توجد صفحة بهذا الرقم/فشل الرسم."""
        try:
            pages = convert_from_bytes(
                data, dpi=render_dpi, first_page=page_no, last_page=page_no)
        except (PDFPageCountError, PDFInfoNotInstalledError, ValueError, OSError) as exc:
            log.debug("تعذّر رسم صفحة %d: %s", page_no, exc)
            return None
        return pages[0] if pages else None

    if render_page(1, used_dpi) is None:
        log.warning("تعذّر تحويل PDF إلى صور")
        return OcrResult("", 0, 0, True)

    # langs فارغة = كشف تلقائي من خط أول الصفحات (المستند عادة موحّد اللغة).
    # عيّنات الكشف تُرسم بدقة أخف (٢٠٠) — OSD لا يحتاج أكثر، وقد تحقّقنا
    # من ١٦ مستندًا حقيقيًا أن قراره عند ٢٠٠ يطابق ٣٠٠.
    if langs:
        language = pick_languages(langs)
    else:
        sample_images = []
        for page_no in range(1, min(SCRIPT_SAMPLE_PAGES, max_pages) + 1):
            sample = render_page(page_no, min(used_dpi, 200))
            if sample is None:
                break
            sample_images.append(sample)
        language = pick_languages(detect_languages(sample_images))
        del sample_images
    log.info("OCR: لغة %s بدقة %ddpi (حتى %d صفحة)", language, used_dpi, max_pages)

    chunks: list[str] = []
    counters = {"skewed": 0, "rescued": 0, "flattened": 0, "dewarped": 0}
    progress = {"done": 0}  # صفحات منجزة — تُبثّ لـ on_progress (عداد الواجهة)
    dewarp_session: _DewarpSession | None = None  # يُفتح عند أول صفحة عريضة

    def prepare(image):
        """المعالجة القبلية قبل القراءة (تسوية/تدرّج/تقويم)."""
        if not fix_skew:
            return image
        image = normalize_exposure(image)
        if illumination_gradient(image) > GRADIENT_THRESHOLD:
            image = flatten_illumination(image)
            counters["flattened"] += 1
        image, used = deskew(image)
        if used:
            counters["skewed"] += 1
        return image

    def dewarped_chunk(image) -> str | None:
        """مسار الصفحات العريضة: فكّ التواء UVDoc ثم تحضير وعتبة محلية.

        None = المسار غير متاح أو فشل (المتصل يتراجع للكلاسيكي).
        العتبة المحلية هنا **جزء من الوصفة لا إنقاذ**: فكّ الالتواء
        وحده قِيس أسوأ من الأساس لأنه يرقّق الحبر — انظر قسم UVDoc.
        """
        nonlocal dewarp_session
        if dewarp_session is None:
            dewarp_session = _start_dewarp_session()
            if dewarp_session is None:
                return None
        flat = dewarp_session.dewarp(image)
        if flat is None:
            return None
        counters["dewarped"] += 1
        return pytesseract.image_to_string(
            adaptive_binarize(prepare(flat)), lang=language) or ""

    def process(image) -> str:
        """يقرأ صفحة واحدة بـ tesseract (مع إنقاذ العتبة المحلية)."""
        try:
            # لقطة صفحتين عريضة + فكّ التواء مفعّل ⇒ المسار المفكوك
            # أولًا؛ فارغ/فشل ⇒ الكلاسيكي تحت، فلا نخسر الصفحة أبدًا.
            if dewarp and image.width >= image.height * UVDoc_MIN_ASPECT:
                chunk = dewarped_chunk(image)
                if chunk and chunk.strip():
                    return chunk
            image = prepare(image)
            chunk = pytesseract.image_to_string(image, lang=language) or ""
            # شبكة الأمان: صفحة بتباين خلفية مربك تُخرج نصًا فارغًا رغم
            # وجود محتوى. نُعيد المحاولة بعتبة محلية — فقط عند الفشل.
            if not chunk.strip() and has_ink(image):
                retry = pytesseract.image_to_string(
                    adaptive_binarize(image), lang=language) or ""
                if retry.strip():
                    chunk = retry
                    counters["rescued"] += 1
            return chunk
        except Exception as exc:  # صفحة فاسدة أو فشل tesseract
            log.debug("OCR فشل في صفحة: %s", exc)
            return ""

    def ocr_range(first: int, last: int) -> tuple[int, bool]:
        """يعالج صفحات [first..last] واحدةً واحدة. يرجع (عددها، نهاية المستند؟)."""
        done = 0
        for page_no in range(first, last + 1):
            image = render_page(page_no, used_dpi)
            if image is None:
                return done, True
            chunks.append(process(image))
            del image
            done += 1
            progress["done"] += 1
            _report_progress(on_progress, progress["done"])
        return done, False

    def collected_text() -> str:
        return "\n\n".join(clean_ocr_text(c) for c in chunks if c.strip())

    try:
        total_pages, reached_end = ocr_range(1, max_pages)

        # تعميق مشروط: أول دفعة شبه فارغة + ما زال في المستند صفحات ⇒
        # جرّب دفعات أعمق بنفس اللغة، بحد أقصى DEEP_SCAN_MAX_BATCHES.
        batch_no = 0
        while (not reached_end
               and len(collected_text()) < DEEP_SCAN_MIN_CHARS
               and batch_no < DEEP_SCAN_MAX_BATCHES):
            first = total_pages + 1
            batch_no += 1
            log.info("OCR: تعميق — دفعة من صفحة %d بعد مردود ضعيف", first)
            done, reached_end = ocr_range(first, first + max_pages - 1)
            total_pages += done
            if done == 0:
                break

        text = collected_text()
        if counters["skewed"] or counters["rescued"] or counters["flattened"] or counters["dewarped"]:
            log.info("OCR: قُوّم ميل %d صفحة، وسُوّي تدرّج %d، وأُنقذت %d بعتبة محلية، وفُكّ التواء %d",
                     counters["skewed"], counters["flattened"], counters["rescued"], counters["dewarped"])
        return OcrResult(text=text.strip(), pages=total_pages, pages_ocr=len(chunks), available=True)
    finally:
        # الجلسة تُغلق دائمًا — لا عمّال UVDoc معلّقون عند أي فشل.
        if dewarp_session is not None:
            dewarp_session.close()


def extract_text_from_pdf(
    data: bytes,
    max_pages: int = 15,
    dpi: int | None = None,
    langs: str = "",
    fix_skew: bool = True,
    dewarp: bool = False,
    on_progress=None,
) -> OcrResult:
    """الواجهة العامة لاستخراج النص — لا ترمي استثناءً أبدًا.

    أي فشل غير متوقع (بايتات تالفة، مدخل غير متوقّع، انهيار poppler)
    يُسجَّل ويُرجَع نص فارغ بدل أن يصل للمستخدم كخطأ في الرفع.
    """
    try:
        return _extract_text_from_pdf(
            data, max_pages=max_pages, dpi=dpi, langs=langs,
            fix_skew=fix_skew, dewarp=dewarp, on_progress=on_progress)
    except Exception as exc:  # شبكة أمان أخيرة — مقصودة
        log.warning("OCR: فشل غير متوقع أثناء استخراج النص: %s", exc)
        return OcrResult("", 0, 0, True)


def status_text() -> str:
    """وصف مختصر لحالة OCR — يُعرض في لوحة الإدارة."""
    if not is_available():
        return (
            "❌ OCR غير متاح\n"
            "التثبيت: sudo apt-get install -y tesseract-ocr tesseract-ocr-ara poppler-utils"
        )
    langs = ", ".join(installed_languages()) or "—"
    text = f"✅ OCR متاح — اللغات: {langs}"
    if _uvdoc_available():
        ready = _uvdoc_model_path().exists()
        text += f"\n🧭 فكّ التواء UVDoc: onnxruntime مثبّت، النموذج {'جاهز' if ready else 'يُنزَّل عند أول استخدام'}"
    return text
