"""تحليل المادة (PDF/نص) ثم شرحها حسب اختيارات المستخدم وبرومبته الحرفي.

**ما يفعله هذا الملف ومتى:** عندما يضغط المستخدم «اشرح» ويختار مادة (ملفًا
من مكتبته أو PDF يرفعه للتوّ)، لا نُلخّص الملف فقط — بل:

1. نحلّل بنيته: عنوان، أقسام، تعريفات، معادلات، أرقام مهمة، أسئلة.
2. نُقسّمه إلى أجزاء بحسب **نوع الشرح المطلوب** (شرح كامل / نقاط / تعريفات
   / أمثلة / امتحان / شرح لكل قسم على حدة).
3. نبني طلبًا يجمع: اختيارات المستخدم + **برومبته الحرفي (محميًا)** + المادة.
4. نطلب من النموذج إعادة علامات الحماية كما هي، فنفكّها بعد التهريب فتعود
   رموز المستخدم حرفية في العرض.

**لماذا تحليل بنيوي محلي قبل النموذج؟** لأن النموذج يعمل على ١٢٠٠٠ حرف
كحد أقصى، وملف من ٣٠ صفحة أضعاف ذلك. التحليل المحلي يختار **ما يستحق**
الإرسال (التعريفات والمعادلات والعناوين) بدل أن يُقتطع النص من منتصفه
فيضيع المعنى — وهذا يفسّر لماذا تخرج شروح النماذج سطحيّة على الملفات
الكبيرة دون هذه الطبقة.

⚠️ **حدود صريحة:** لا نُفسّر الأشكال ولا الرسوم. القراءة بالنص فقط —
سواء من طبقة نص PDF أو عبر وكيل الرؤية الذي يختاره المستخدم. إن كان
الملف صورًا ولم يوجد مفتاح وكيل، نقول ذلك للمستخدم بدل إرجاع شرح عام
يبدو كأنه قرأ الملف.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import ai, tutor, verbatim
from . import learner as learner_service

log = logging.getLogger(__name__)

# ── اختيارات المستخدم (مفاتيح ثابتة تُخزَّن في FSM وتُستخدم في الأزرار) ──
MODE_EXPLAIN = "explain"        # شرح كامل
MODE_BULLETS = "bullets"        # نقاط سريعة
MODE_DEFINITIONS = "defs"       # التعريفات والمصطلحات
MODE_EXAMPLES = "examples"      # أمثلة محلولة
MODE_QUIZ = "quiz"              # أسئلة للمراجعة
MODE_SECTIONS = "sections"      # شرح كل قسم على حدة

DEPTH_SHORT = "short"
DEPTH_NORMAL = "normal"
DEPTH_DEEP = "deep"

MODES: dict[str, str] = {
    MODE_EXPLAIN: "شرح كامل ومتدرّج",
    MODE_BULLETS: "نقاط سريعة للمراجعة",
    MODE_DEFINITIONS: "التعريفات والمصطلحات فقط",
    MODE_EXAMPLES: "أمثلة محلولة خطوة بخطوة",
    MODE_QUIZ: "أسئلة مراجعة مع إجابات",
    MODE_SECTIONS: "شرح كل قسم على حدة",
}

DEPTHS: dict[str, str] = {
    DEPTH_SHORT: "مختصر (٥ أسطر)",
    DEPTH_NORMAL: "متوسّط",
    DEPTH_DEEP: "مفصّل جدًا",
}

LANG_AR = "ar"
LANG_EN = "en"
LANG_MIXED = "mixed"

LANGUAGES: dict[str, str] = {
    LANG_AR: "عربي",
    LANG_EN: "إنجليزي",
    LANG_MIXED: "عربي مع المصطلح الإنجليزي",
}

#: أقصى طول للبرومبت الحر — يمنع استنزاف الحدّ الكامل للنموذج بالتعليمات
MAX_PROMPT_CHARS = 1500
#: أقصى حجم للمادة المرسلة في طلب واحد
MAX_MATERIAL_CHARS = 9000


# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ExplainOptions:
    """اختيارات المستخدم — كلها لها قيم افتراضية معقولة."""

    mode: str = MODE_EXPLAIN
    depth: str = DEPTH_NORMAL
    language: str = LANG_MIXED
    prompt: str = ""                       # برومبت المستخدم الحرفي
    focus: str = ""                        # تركيز على قسم/صفحة محددة
    include_numbers: bool = True           # إلزام حفظ الأرقام والمعادلات

    def normalized(self) -> "ExplainOptions":
        """يضمن أن كل قيمة معروفة — التخزين في FSM قد يحمل قيمة قديمة."""
        return ExplainOptions(
            mode=self.mode if self.mode in MODES else MODE_EXPLAIN,
            depth=self.depth if self.depth in DEPTHS else DEPTH_NORMAL,
            language=self.language if self.language in LANGUAGES else LANG_MIXED,
            prompt=(self.prompt or "").strip()[:MAX_PROMPT_CHARS],
            focus=(self.focus or "").strip()[:200],
            include_numbers=bool(self.include_numbers),
        )

    def labels(self) -> tuple[str, str, str]:
        opts = self.normalized()
        return (MODES[opts.mode], DEPTHS[opts.depth], LANGUAGES[opts.language])


# ───────────────────────────────────────────────────── تحليل المادة
@dataclass
class MaterialAnalysis:
    """نتيجة تحليل المادة محليًا — تُعرَض للمستخدم ويُبنى عليها الطلب."""

    title: str = ""
    sections: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    char_count: int = 0
    word_count: int = 0
    truncated: bool = False

    @property
    def is_usable(self) -> bool:
        """هل فيه نص يكفي للشرح؟ (تحت ٤٠ حرفًا لا يصلح)"""
        return self.char_count >= tutor.MIN_TEXT_CHARS

#: عناوين أقسام شائعة في المواد الجامعية العربية والإنجليزية
_SECTION_RE = re.compile(
    r"^\s*(?:"
    r"(?:ال)?فصل\s+\S+|(?:ال)?باب\s+\S+|(?:ال)?مبحث\s+\S+|(?:ال)?مطلب\s+\S+|"
    r"(?:ال)?وحدة\s+\S+|(?:ال)?جزء\s+\S+|(?:ال)?قسم\s+\S+|"
    r"chapter\s+\d+|section\s+\d+|unit\s+\d+|part\s+\d+|"
    r"\d+(?:\.\d+){0,3}[.)]\s+\S"
    r")\s*.{0,90}$",
    re.MULTILINE | re.IGNORECASE,
)

_DEFINITION_RE = re.compile(
    r"^[^\n]{2,80}?\s*(?::|－|–|—|=)\s*\S[^\n]{5,300}$"
    r"|^(?:تعريف|التعريف|definition|يُعرَّف|يعرف)\b[^\n]{5,300}$",
    re.MULTILINE | re.IGNORECASE,
)

_EQUATION_RE = re.compile(
    r"[^\n]{0,60}(?:=|\+|\-|\*|\^|∑|∫|√|≈|≤|≥)[^\n]{0,60}",
    re.MULTILINE,
)

_QUESTION_RE = re.compile(
    r"^[^\n]{5,200}(?:\?|؟)\s*$"
    r"|^(?:سؤال|تمرين|اثبت|احسب|اشرح|علّل|قارن|ما هو|ما هي|عرّف)\b[^\n]{3,200}$",
    re.MULTILINE | re.IGNORECASE,
)

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|٪)?\b")


def analyze(text: str, *, title: str = "", limit: int = MAX_MATERIAL_CHARS) -> MaterialAnalysis:
    """يحلّل المادة محليًا — بلا شبكة وبلا تكلفة.

    الحدّ (`limit`) يُطبَّق على النص **المُرسَل** لا على الإحصاء: نحسب
    الأرقام على كامل النص كي تكون صادقة، ثم نقتطع للإرسال.
    """
    body = (text or "").strip()
    analysis = MaterialAnalysis(
        title=(title or "").strip()[:200],
        char_count=len(body),
        word_count=len(body.split()),
        truncated=len(body) > limit,
    )
    if not body:
        return analysis

    # الإحصاء على كامل النص — الأرقام التي نعرضها للمستخدم يجب أن تكون حقيقية
    analysis.sections = _dedupe(_SECTION_RE.findall(body))[:40]
    analysis.definitions = _dedupe(_DEFINITION_RE.findall(body))[:30]
    analysis.equations = _dedupe(m.strip() for m in _EQUATION_RE.findall(body))[:25]
    analysis.questions = _dedupe(_QUESTION_RE.findall(body))[:20]
    analysis.numbers = _dedupe(_NUMBER_RE.findall(body))[:40]
    return analysis


def _dedupe(items) -> list[str]:
    """يزيل التكرار مع الحفاظ على الترتيب، ويُنقّي الفراغات."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = " ".join(str(raw).split())
        if not value or len(value) < 2 or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


# ─────────────────────────────────────────── بناء طلب الشرح
_MODE_INSTRUCTIONS: dict[str, str] = {
    MODE_EXPLAIN: (
        "اشرح المادة شرحًا متدرّجًا: خلاصة في سطرين، ثم الفكرة الأساسية، "
        "ثم التفصيل، ثم خلاصة سريعة. اربط كل جزء بالمادة نفسها."
    ),
    MODE_BULLETS: (
        "لخّص المادة في نقاط مرقّمة قصيرة — كل نقطة سطر واحد. "
        "لا فقرات طويلة، ولا مقدمات."
    ),
    MODE_DEFINITIONS: (
        "استخرج كل تعريف ومصطلح في المادة واشرحه في سطرين: "
        "التعريف كما ورد، ثم معناه بكلمات أبسط. لا تُضِف مصطلحات غير موجودة."
    ),
    MODE_EXAMPLES: (
        "اشرح المادة عبر أمثلة محلولة خطوة بخطوة. لكل مثال: المسألة، "
        "ثم الحل خطوة بخطوة، ثم لماذا هذه الخطوة. استخدم معادلات المادة نفسها."
    ),
    MODE_QUIZ: (
        "ولّد من المادة أسئلة مراجعة (٦–١٠) متدرّجة الصعوبة، وأجب عن كل سؤال "
        "في سطرين مع الإشارة إلى موضعه في المادة."
    ),
    MODE_SECTIONS: (
        "المادة مقسّمة أقسامًا. اشرح كل قسم في مقطع مستقل بعنوانه، "
        "بنفس ترتيب ورودها، وبلا دمج بينها."
    ),
}

_DEPTH_INSTRUCTIONS: dict[str, str] = {
    DEPTH_SHORT: "الطول المطلوب: مختصر جدًا — لا تتجاوز ٥ أسطر أو ٦ نقاط.",
    DEPTH_NORMAL: "الطول المطلوب: متوسّط — تفصيل كافٍ للفهم بلا إطالة.",
    DEPTH_DEEP: "الطول المطلوب: مفصّل جدًا — غطِّ كل فكرة في المادة بتفصيلها.",
}

_LANG_INSTRUCTIONS: dict[str, str] = {
    LANG_AR: "اكتب كل الرد بالعربية الفصحى المبسّطة، وبلا أي كلمة إنجليزية "
             "إلا إذا كانت اسمًا لعالم أو نظام لا يُترجم.",
    LANG_EN: "Write the entire answer in English.",
    LANG_MIXED: "اكتب بالعربية المصرية المبسّطة، وأبقِ المصطلح العلمي "
                "بالإنجليزية بين قوسين عند أول ظهور له.",
}


def build_explain_prompt(
    material: str,
    options: ExplainOptions,
    profile,
    *,
    analysis: MaterialAnalysis | None = None,
    title: str = "",
    protected: verbatim.ProtectedText | None = None,
) -> str:
    """يبني طلب الشرح الكامل من المادة + اختيارات المستخدم + البرومبت الحرفي.

    `material` هو النص **بعد** أن تُحفظ رموزه إن كان فيه برومبت مستخدم؛
    أما البرومبت الحر فيُمرَّر محميًا في `protected` كي تُعاد علاماته.
    """
    opts = options.normalized()
    mode_text, depth_text, lang_text = opts.labels()
    parts: list[str] = []

    header = "اشرح المادة التالية" + (f" («{title}»)" if title else "") + "."
    parts += [header, "", "تعليمات التخصيص (التزم بها حرفيًا):",
              learner_service.build_personalization(profile), ""]

    parts += ["نمط الشرح المطلوب:", _MODE_INSTRUCTIONS[opts.mode], ""]
    parts += ["مستوى التفصيل:", _DEPTH_INSTRUCTIONS[opts.depth], ""]
    parts += ["لغة الرد:", _LANG_INSTRUCTIONS[opts.language], ""]

    if opts.include_numbers:
        parts += [
            "قاعدة إلزامية عن الرموز والأرقام:",
            "- انقل كل رقم ومعادلة ورمز كما ورد في المادة **بلا تغيير**.",
            "- لا تُحوّل الرموز إلى كلمات (اكتب `≤` لا «أصغر من أو يساوي»).",
            "- المعادلات تبقى كما هي بين `backticks` إن كانت في سطر مستقل.",
            "",
        ]

    if opts.focus:
        parts += ["ركّز على هذا الجزء تحديدًا أولًا ثم أكمل الباقي:",
                  ai.wrap_untrusted(opts.focus, limit=400), ""]

    # ── البرومبت الحرفي: أهمّ مقطع في الطلب ──────────────────────────
    if opts.prompt:
        parts += [
            "طلب المستخدم الحرفي (التزم به حرفيًا، وهو الأسبق على أي نمط عام):",
        ]
        if protected is not None and protected.saved:
            parts += [
                "⚠️ النص التالي محميّ: العلامات من الشكل U+E000 NUMBER U+E001",
                "هي رموز كتبها المستخدم وطلَب الحفاظ عليها. أعِد كل علامة",
                "كما هي في موضعها بلا تغيير ولا ترجمة ولا حذف — سنُعيدها نحن",
                "إلى رموزها الأصلية عند العرض.",
            ]
        # نُرسل النسخة المحميّة (رموزها خلف علامات) لا النص الخام.
        outgoing = protected.text if protected is not None else opts.prompt
        parts += [ai.wrap_untrusted(outgoing, limit=MAX_PROMPT_CHARS), ""]

    # ── خريطة المادة: تُساعد النموذج على تغطية كل الأقسام ────────────
    if analysis is not None and analysis.is_usable:
        if analysis.sections:
            parts += ["أقسام المادة (غطِّها كلها):",
                      "\n".join(f"- {s}" for s in analysis.sections[:20]), ""]
        if opts.mode in (MODE_DEFINITIONS, MODE_EXPLAIN, MODE_EXAMPLES):
            if analysis.definitions:
                parts += ["تعريفات رصدتُها في المادة:",
                          "\n".join(f"- {d}" for d in analysis.definitions[:12]), ""]
        if opts.mode in (MODE_EXAMPLES, MODE_EXPLAIN) and analysis.equations:
            parts += ["معادلات رصدتُها في المادة (لا تُغيَّر):",
                      "\n".join(f"- {e}" for e in analysis.equations[:12]), ""]

    parts += ["نص المادة:",
              ai.wrap_untrusted(material, limit=MAX_MATERIAL_CHARS)]
    return "\n".join(parts)


# ─────────────────────────────────────────── تشغيل الشرح
@dataclass(frozen=True)
class ExplainResult:
    """نتيجة الشرح مع ما يلزم لعرضها بصدق."""

    text: str
    used_ai: bool
    note: str = ""
    saved_symbols: tuple[str, ...] = ()
    truncated_material: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


async def explain_material(
    material: str,
    options: ExplainOptions,
    profile,
    *,
    title: str = "",
) -> ExplainResult:
    """يشرح مادة حسب اختيارات المستخدم، مع حفظ رموز برومبته حرفيًا.

    لا يرمي استثناءً: أي فشل يعود بـ`used_ai=False` وملاحظة عربية مفهومة
    (نفس عقد `tutor.explain` — الواجهة تتوقّعه).
    """
    opts = options.normalized()
    body = (material or "").strip()
    if len(body) < tutor.MIN_TEXT_CHARS:
        return ExplainResult(
            "", False,
            "المادة قصيرة جدًا أو لم يُستخرج منها نص — أرسل ملفًا فيه نص، "
            "أو صوّر الصفحات بوضوح لو كان سكانر.",
        )

    analysis = analyze(body, title=title)

    # حماية رموز البرومبت مرة واحدة هنا — والفكّ عند العرض
    protected = verbatim.protect(opts.prompt) if opts.prompt else None
    prompt = build_explain_prompt(
        body[:MAX_MATERIAL_CHARS], opts, profile,
        analysis=analysis, title=title, protected=protected,
    )

    if not ai.is_configured():
        return ExplainResult(
            "", False,
            "الشرح الذكي غير مُعدّ — يحتاج مفتاح ذكاء اصطناعي. "
            "تقدر تستخدم «تلخيص PDF» المحلي في الوقت الحالي.",
            saved_symbols=protected.saved if protected else (),
            truncated_material=analysis.truncated,
        )

    if tutor.looks_like_injection(opts.prompt) or tutor.looks_like_injection(body[:2000]):
        log.warning("شرح مادة: نص يشبه محاولة حقن تعليمات (%d حرف)", len(opts.prompt))

    try:
        answer = await ai.complete(
            prompt,
            system=tutor._EXPLAIN_SYSTEM,
            max_tokens=2200 if opts.depth == DEPTH_DEEP else 1400,
        )
    except ai.AIError as exc:
        log.info("فشل شرح المادة: %s", type(exc).__name__)
        return ExplainResult(
            "", False, f"تعذّر الشرح الآن: {exc}",
            saved_symbols=protected.saved if protected else (),
            truncated_material=analysis.truncated,
        )

    if not answer.strip():
        return ExplainResult(
            "", False, "لم يرجع الشرح نصًا — جرّب مستوى تفصيل آخر.",
            truncated_material=analysis.truncated,
        )

    return ExplainResult(
        answer, True,
        saved_symbols=protected.saved if protected else (),
        truncated_material=analysis.truncated,
    )


def render(result: ExplainResult, *, title: str = "", options: ExplainOptions | None = None,
           protected: verbatim.ProtectedText | None = None) -> str:
    """يبني الرسالة النهائية (HTML آمن) مع إعادة الرموز الحرفية.

    **ترتيب العمليات هنا هو جوهر الميزة:**
      1. `ai.to_html` يُهشّم رد النموذج بالكامل (أمان تيليجرام).
      2. `verbatim.restore` يفكّ العلامات **بعد** التهريب، فتعود الرموز
         حرفية في العرض بدل أن تُهشَّم أو تُفسَّر كوسوم.

    لو عكسنا الترتيب (فكّ الحماية ثم تهريب) لصارت `<` رمزًا يُهشَّم إلى
    `&lt;` ويظهر للمستخدم مُرمَّزًا — وهو بالضبط العطل الذي كانت تُشتكي
    منه الشكوى الأصلية.
    """
    opts = (options or ExplainOptions()).normalized()
    escaped = ai.to_html(result.text)
    if protected is not None:
        # ⚠️ بعد التهريب لا قبله: الرموز تُعاد مُهشَّمة لتُعرَض حرفية في
        # تيليجرام (وضع HTML يفسّر `<` الخام كبداية وسم فيُفشل الرسالة).
        escaped = verbatim.restore(escaped, protected, for_html=True)

    mode_text, depth_text, lang_text = opts.labels()
    blocks = [
        f"<b>📄 {tutor._safe(title or 'شرح المادة')}</b>",
        f"<i>{tutor._safe(mode_text)} · {tutor._safe(depth_text)} · "
        f"{tutor._safe(lang_text)}</i>",
        "",
        escaped,
    ]
    if result.saved_symbols:
        # الرمز يُوضع داخل <code> فيُهرَّب مرة واحدة كالمعتاد؛ `_safe`
        # تفعل ذلك. لا نستخدم إخراج `restore` هنا لأنه مُهشَّم سلفًا.
        shown = " · ".join(f"<code>{tutor._safe(s)}</code>"
                           for s in result.saved_symbols[:12])
        more = (f" (+{len(result.saved_symbols) - 12})"
                if len(result.saved_symbols) > 12 else "")
        blocks += ["", f"<i>🔒 محفوظة حرفيًا من برومبتك: {shown}{more}</i>"]
    if result.truncated_material:
        blocks += ["", "<i>ℹ️ الملف طويل — شُرح الجزء الأهم؛ اختر «شرح كل قسم "
                       "على حدة» لتغطية الباقي.</i>"]
    if result.note:
        blocks += ["", f"<i>{tutor._safe(result.note)}</i>"]
    return "\n".join(blocks)
