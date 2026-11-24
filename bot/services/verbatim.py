"""الحفاظ الحرفي على نصّ المستخدم ورموزه في مخرجات الشرح.

**المشكلة التي يحلّها هذا الملف:** طلب المستخدم كان صريحًا:

    «الـAI لما يشرح، يجيب نفس اللي كاتبه بالضبط، حتى لو فيه رموز»

والطريق الطبيعي (`ai.to_html`) يُهشّم المخرَج كاملًا — وهذا صحيح أمنيًا لكنه
يجعل الرموز تخرج كما هي في **النص** (ترميز HTML) لا كما كتبها المستخدم في
**العرض**. وفي اتجاه آخر، تمرير المخرَج إلى تيليجرام كـHTML خام يعني أن أي
`<` في إجابة النموذج تكسر الرسالة بـ`can't parse entities`.

الحل ليس «تهريب أكثر» ولا «تهريب أقل»، بل **حماية الرموز قبل أي معالجة**:

1. نستخرج من نص المستخدم كل ما يجب أن يبقى حرفيًا: الرموز والعمليات
   الرياضية، الكود، الروابط، الأرقام، والكلمات التي كتبها بنفسه.
2. نُخفيه خلف علامات بديلة آمنة (Private Use Area) لا تمسّها أدوات
   التهريب ولا تُحسب كوسوم HTML.
3. تُرسل النسخة المحميّة إلى النموذج، ونطلب منه إعادة العلامات كما هي.
4. عند الإخراج نفكّ الحماية **بعد** التهريب، فتعود الرموز حرفية ومعروضة.

**قاعدة الاتجاه الواحد:** كل دالة هنا إمّا `protect` أو `restore`، ولا شيء
غير ذلك. الحماية تُطبَّق مرة واحدة عند بناء الطلب، والفكّ مرة واحدة عند
العرض — فالتطبيق المزدوج لا يمكن أن يحدث.

⚠️ موضع الحماية: يُطبَّق على **نص المستخدم** (البرومبت) لا على مخرَج
النموذج، لأن الرموز المطلوب حفظها هي التي **كتبها** المستخدم. ما يكتبه
النموذج من رموز يبقى نصًّا مُهرَّبًا آمنًا كالعادة.
"""
from __future__ import annotations

import html
import re

# ── علامات بديلة من منطقة الاستخدام الخاص (Private Use Area) ────────────
# لماذا U+E000+؟ لأنها:
#   * ليست رموزًا في Unicode تُفسَّر كترميز (لا < ولا &)
#   * لا تُمسَح بـ`clean_output` (الذي يمسح محارف التحكّم فقط)
#   * لا تُعالَج بأي قاعدة تنسيق عربية/لاتينية
#   * لا تظهر عادةً في نص المستخدم، فيمكن كشفها بثقة
_PROTECT_OPEN = "\ue000"
_PROTECT_CLOSE = "\ue001"


def _token(index: int) -> str:
    """علامة بديلة لرقم — بادئة/لاحقة مع فهرس ثابت العرض."""
    return f"{_PROTECT_OPEN}{index}{_PROTECT_CLOSE}"


# ── ما يجب أن يبقى حرفيًا ────────────────────────────────────────────────
# ترتيب الأنماط مقصود: الأطول أولًا حتى لا يقتطع نمطٌ قصير نمطًا أطول.
# (لو فُحص `=` قبل `==`، لصارت `==` علامتين منفصلتين وتغيّر المعنى.)
_PATTERNS: tuple[tuple[str, str], ...] = (
    # كود داخل علامات اقتباس خلفية — الأولوية الأعلى، قد يحوي أي رمز
    ("code", r"```[\s\S]{0,2000}?```|`[^`\n]{1,500}`"),
    # روابط
    ("url", r"https?://[^\s<>\"')]{3,600}"),
    # معادلات رياضية بين $
    ("math", r"\$\$[\s\S]{0,1200}?\$\$|\$[^$\n]{1,400}\$"),
    # نسبة مئوية مع رقمها — قبل `%` المفردة وإلا انفصلت عن رقمها
    ("pct", r"\b\d+(?:[.,]\d+)?\s*%"),
    # معاملات متعدّدة المحارف. ⚠️ `**` و`//` إلزاميّتان قبل `*` و`/`:
    # لو فُحصت المفردة أولًا لصارت `**` علامتين منفصلتين وتغيّر المعنى.
    ("op", r"<=>|<=|>=|!=|==|:=|\+=|-=|\*=|/=|//|\*\*|\+\+|--|->|=>|<>|\.\.\.|<->"
           r"|≈|≠|≤|≥|∞|∑|∏|√|∫|±|∓|×|÷|·|∗|⊕|⊗|∈|∉|⊂|⊃|∪|∩|∀|∃|∅|∆|∇|∂|°|µ|Ω|π|θ|λ|Δ"
           r"|↔|→|←|↑|↓"),
    # كسور عددية
    ("frac", r"\b\d+\s*/\s*\d+\b"),
    # أس: الأساس متبوع بـ`^` ثم **أس محدود فقط** (رقم/رمز/قوسان).
    # `[\s\S]{1,8}` كانت تبتلع الفراغ وما بعده فتُدمج تعبيرات متجاورة.
    ("pow", r"[\w\u0600-\u06FF]+\s*\^\s*(?:\{[^}]{1,24}\}|\([^)]{1,24}\)|\w{1,6})"),
    # رموز مفردة ذات معنى
    ("sym", r"[<>=&|\\{}~^*%@#$+_]"),
)


class ProtectedText:
    """نصّ المستخدم بعد حماية رموزه — يعرف كيف يُعيدها كما هي."""

    __slots__ = ("_text", "_saved")

    def __init__(self, text: str, saved: list[str]) -> None:
        self._text = text
        self._saved = saved

    @property
    def text(self) -> str:
        """النسخة المُرسَلة إلى النموذج (رموزها مخفيّة خلف علامات)."""
        return self._text

    @property
    def saved(self) -> tuple[str, ...]:
        """الرموز الأصلية المحفوظة، بترتيب الظهور."""
        return tuple(self._saved)

    def __bool__(self) -> bool:
        return bool(self._text.strip())

    def __len__(self) -> int:
        return len(self._text)

    def __repr__(self) -> str:                 # pragma: no cover - تشخيص
        return f"ProtectedText(len={len(self._text)}, saved={len(self._saved)})"

    def restore(self, output: str, *, for_html: bool = True) -> str:
        """يُعيد الرموز الحرفية **بعد** أن يكون المخرَج قد هُرِّب.

        **نقطة حرجة:** تيليجرام في وضع HTML يُفسّر `<` كبداية وسم، فإن
        أرسلنا `<` خامًا **تفشل الرسالة** بـ`can't parse entities`. فالرمز
        الحرفي يجب أن يُرسَل **مُهشَّمًا** ليُعرَض حرفيًا: `<` تُرسَل
        `&lt;` فتُعرَض `<`.

        لذلك `for_html=True` (الافتراضي) يُهشّم الرمز عند إعادته، فيكون
        ما يراه المستخدم **مطابقًا تمامًا** لما كتبه. و`for_html=False`
        للنص العادي (تصدير/سجلّ) حيث لا معنى للتهريب.

        النموذج قد يعيد العلامة كما هي، أو بمسافات حولها، أو (نادرًا)
        برقم مختلف. نتعامل مع الحالات الثلاث:
          * علامة سليمة ⇒ تُستبدل بالرمز الأصلي (مُهشَّمًا لـHTML)
          * علامة مشوّهة/مفقودة ⇒ تبقى العلامة كما هي (لا نخسر النص)
          * رقم غير موجود ⇒ تُترك كما هي بلا استبدال خاطئ
        """
        if not output or not self._saved:
            return output

        saved = self._saved

        def replace(match: re.Match[str]) -> str:
            raw = match.group(1).strip()
            if not raw.isdigit():
                return match.group(0)
            index = int(raw)
            if 0 <= index < len(saved):
                symbol = saved[index]
                # التهريب مرة واحدة هنا: المخرَج مُهرَّب سلفًا، والرمز
                # نفسه قد يحوي `<`/`&`/`>` فيحتاج تهريبًا مستقلًا.
                return html.escape(symbol, quote=False) if for_html else symbol
            return match.group(0)

        pattern = re.compile(
            re.escape(_PROTECT_OPEN) + r"\s*(\d{1,6})\s*?" + re.escape(_PROTECT_CLOSE)
        )
        return pattern.sub(replace, output)


def protect(text: str, *, limit: int = 4000) -> ProtectedText:
    """يحمي رموز نص المستخدم قبل إرساله للنموذج.

    المدخلات الطويلة جدًا تُقتطع هنا لا في النموذج، حتى يبقى فهرس كل
    علامة صحيحًا بعد الاقتطاع.
    """
    body = (text or "")
    if len(body) > limit:
        body = body[:limit] + "\n… (اقتُطع الباقي لطول النص)"

    saved: list[str] = []

    def capture(match: re.Match[str]) -> str:
        token = _token(len(saved))
        saved.append(match.group(0))
        return token

    # نُطبّق الأنماط بالتتابع على النص، وكل نمط يرى نصوص العلامات السابقة
    # كأنها محارف عادية — لا خطر لأن العلامات ليست في أي نمط.
    result = body
    for _kind, pattern in _PATTERNS:
        result = re.sub(pattern, capture, result)

    return ProtectedText(result, saved)


def restore(text: str, protected: ProtectedText | None, *,
            for_html: bool = True) -> str:
    """فكّ الحماية بأمان حتى لو كان `protected` غائبًا."""
    if protected is None:
        return text
    return protected.restore(text, for_html=for_html)


def symbols_in(text: str) -> list[str]:
    """الرموز الموجودة في نص المستخدم — للتشخيص والعرض للمستخدم.

    تُستخدم في سطر «حفظت هذه الرموز» كي يتأكّد المستخدم أن ما كتبه
    بالضبط قد وصل، بدل أن يثق بلا دليل.
    """
    found: list[str] = []
    seen: set[str] = set()
    for _kind, pattern in _PATTERNS:
        for match in re.finditer(pattern, text or ""):
            value = match.group(0)
            if value not in seen:
                seen.add(value)
                found.append(value)
    return found


def verbatim_report(text: str) -> str:
    """سطر عربي يصف ما سيُحفظ حرفيًا — يُعرَض قبل التشغيل."""
    items = symbols_in(text)
    if not items:
        return "لا رموز خاصة — النص يُنقل كما هو."
    shown = " · ".join(items[:12])
    extra = f" (و{len(items) - 12} غيرها)" if len(items) > 12 else ""
    return f"🔒 سيُحفظ حرفيًا: {shown}{extra}"
