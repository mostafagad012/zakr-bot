"""دليل المزوّدين: مميزات وعيوب كل مزوّد ذكاء اصطناعي ومساعدات الاختيار.

لماذا ملف منفصل عن `ai.py`؟ لأن هذا **محتوى تربوي** لا منطق تنفيذ:
نصوص عربية تُعرض للمستخدم قبل أن يختار، ومقيّمات تُبنى عليها التوصية.
فصله يجعل تحديث النصوص لا يمسّ كود الاتصال، وأسماء المزوّدين هنا
مربوطة بالكتالوج في `ai.PROVIDERS` (يُتحقَّق من ذلك في الاختبارات).

**مبدأ الصدق**: لا نُجمّل صورة مزوّد على حساب آخر. العيوب مذكورة صراحة
(التكلفة، الخصوصية، حدود المجاني، جودة العربية) لأن المستخدم يختار
قرارًا يترتّب عليه مال وخصوصية، لا زرًا تجميليًا.

المقيّمات (1–5):
* `free`     — سخاء الطبقة المجانية
* `quality`  — جودة الإجابات عامةً
* `arabic`   — جودة العربية تحديدًا
* `speed`    — السرعة/زمن أول توكن
* `ease`     — سهولة الحصول على مفتاح وتشغيله
* `privacy`  — الخصوصية (هل البيانات تخرج من جهازك؟ 5 = لا تخرج)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Verdict:
    """ملف حكم على مزوّد — كما يُعرض للمستخدم."""

    provider: str
    tagline: str                     # سطر واحد: لمن هذا المزوّد؟
    best_for: tuple[str, ...]        # الحالات التي يتفوّق فيها
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    free: int
    quality: int
    arabic: int
    speed: int
    ease: int
    privacy: int
    cost_hint: str = ""              # ملاحظة عن التكلفة
    needs_card: bool = False         # هل يحتاج بطاقة عند التسجيل؟
    offline_capable: bool = False    # يعمل بلا إنترنت؟
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        """متوسط عام — للأرشفة والمقارنة لا للترتيب المطلق."""
        values = [self.free, self.quality, self.arabic, self.speed, self.ease, self.privacy]
        return round(sum(values) / len(values), 1)


# --------------------------------------------------------------- الدليل
VERDICTS: dict[str, Verdict] = {
    "gemini": Verdict(
        provider="gemini",
        tagline="أفضل بداية: مجاني وسهل وقوي في العربية",
        best_for=("البداية بلا تكلفة", "الشرح والتلخيص الطويل", "العربية"),
        pros=(
            "طبقة مجانية سخية — تكفي طالبًا أو مكتبة صغيرة",
            "لا تحتاج بطاقة بنكية للتسجيل",
            "نافذة سياق ضخمة (نصوص PDF طويلة في طلب واحد)",
            "جودة عربية جيدة جدًا",
            "المفتاح جاهز في دقيقة من Google AI Studio",
        ),
        cons=(
            "الطبقة المجانية لها حدود طلبات يومية قد توقفك وقت الذروة",
            "قد تكون أبطأ في أول توكن من غيرها",
            "البيانات تذهب لخوادم Google",
        ),
        free=5, quality=4, arabic=5, speed=3, ease=5, privacy=2,
        cost_hint="مجاني بحدود يومية، ثم مدفوع بالاستخدام",
        needs_card=False,
    ),
    "groq": Verdict(
        provider="groq",
        tagline="الأسرع: رد فوري، وطبقة مجانية بحدود",
        best_for=("السرعة", "الامتحانات القصيرة", "التجربة اليومية"),
        pros=(
            "أسرع استجابة بين الخيارات المتاحة (رد شبه فوري)",
            "طبقة مجانية بحدود يومية معقولة",
            "لا تحتاج بطاقة بنكية",
            "مفتاح سهل جدًا",
        ),
        cons=(
            "الموديلات المتاحة أقل تنوّعًا من غيرها",
            "جودة العربية جيدة لكن أقل من Gemini/Claude",
            "حدود المجاني اليومية محدودة على الموديلات الكبيرة",
        ),
        free=4, quality=3, arabic=3, speed=5, ease=5, privacy=2,
        cost_hint="مجاني بحدود، ثم مدفوع رخيص جدًا",
        needs_card=False,
    ),
    "openai": Verdict(
        provider="openai",
        tagline="القياسي: جودة عالية جدًا لكنه مدفوع",
        best_for=("أعلى جودة إجابة", "الشرح الدقيق", "الاعتمادية"),
        pros=(
            "أعلى جودة عامة في الشرح والاستدلال",
            "مستقر جدًا وموثّق على نطاق واسع",
            "جودة عربية ممتازة",
            "أدوات وإمكانات واسعة",
        ),
        cons=(
            "لا توجد طبقة مجانية حقيقية — تحتاج رصيدًا مدفوعًا",
            "يحتاج بطاقة بنكية للتسجيل",
            "قد يكون الأغلى حسب الموديل المختار",
        ),
        free=1, quality=5, arabic=5, speed=4, ease=4, privacy=2,
        cost_hint="مدفوع بالاستخدام (يتفاوت كثيرًا حسب الموديل)",
        needs_card=True,
    ),
    "anthropic": Verdict(
        provider="anthropic",
        tagline="الأفضل للشرح الطويل والمتماسك (Claude)",
        best_for=("الشرح العميق", "التلخيص الطويل", "الأسلوب الطبيعي"),
        pros=(
            "أسلوب شرح طبيعي ومتماسك، ممتاز للنصوص الطويلة",
            "يلتزم بالتعليمات المخصّصة بدقة عالية",
            "جودة عربية عالية",
        ),
        cons=(
            "لا توجد طبقة مجانية عبر الـAPI — رصيد مدفوع",
            "يحتاج بطاقة بنكية",
            "قد يكون مرتفع التكلفة مقارنة بالبدائل",
        ),
        free=1, quality=5, arabic=5, speed=4, ease=3, privacy=2,
        cost_hint="مدفوع بالاستخدام",
        needs_card=True,
    ),
    "openrouter": Verdict(
        provider="openrouter",
        tagline="مفتاح واحد لكل الموديلات — مرونة قصوى",
        best_for=("تجربة موديلات كثيرة", "المقارنة", "موديلات مجانية"),
        pros=(
            "مفتاح واحد يفتح مئات الموديلات من مزوّدين مختلفين",
            "فيه موديلات مجانية (بسرعة/حدود أقل)",
            "تستطيع تغيير الموديل بسطر في الإعدادات بلا تغيير مزوّد",
            "مراقبة الاستهلاك لكل موديل",
        ),
        cons=(
            "الجودة تعتمد كليًا على الموديل الذي تختاره",
            "الموديلات المجانية قد تكون بطيئة أو محدودة",
            "طبقة وسيطة إضافية = نقطة فشل إضافية",
        ),
        free=3, quality=4, arabic=4, speed=3, ease=4, privacy=2,
        cost_hint="حسب الموديل: مجاني محدود أو مدفوع",
        needs_card=False,
    ),
    "deepseek": Verdict(
        provider="deepseek",
        tagline="الأرخص: تكلفة منخفضة جدًا مقابل جودة جيدة",
        best_for=("التكلفة المنخفضة", "الاستخدام الكثيف"),
        pros=(
            "تكلفة منخفضة جدًا مقارنة بالجودة",
            "جودة جيدة في الشرح والاستدلال",
            "مناسب للاستخدام الكثيف على ميزانية صغيرة",
        ),
        cons=(
            "جودة العربية أقل من Gemini/Claude في النصوص الطويلة",
            "قد تكون السرعة متفاوتة وقت الذروة",
            "البيانات تذهب لخوادم في الصين — راجع سياسة الخصوصية",
        ),
        free=1, quality=3, arabic=3, speed=3, ease=4, privacy=1,
        cost_hint="مدفوع لكنه من الأرخص",
        needs_card=True,
    ),
    "mistral": Verdict(
        provider="mistral",
        tagline="أوروبي: خصوصية أفضل نسبيًا وجودة جيدة",
        best_for=("الخصوصية", "الامتثال الأوروبي"),
        pros=(
            "مزوّد أوروبي بسياسات خصوصية أوضح",
            "جودة جيدة وسرعة معقولة",
            "فيه طبقة تجريبية مجانية محدودة",
        ),
        cons=(
            "جودة العربية أقل من الثلاثة الكبار",
            "الموديلات المتاحة أصغر عمومًا",
            "الطبقة المجانية محدودة",
        ),
        free=2, quality=3, arabic=3, speed=4, ease=3, privacy=4,
        cost_hint="مجاني محدود ثم مدفوع",
        needs_card=False,
    ),
    "xai": Verdict(
        provider="xai",
        tagline="Grok — سريع وجيد، لكن مدفوع",
        best_for=("السرعة", "الأسئلة العامة"),
        pros=("سريع", "جودة جيدة", "نافذة سياق كبيرة"),
        cons=(
            "مدفوع بلا طبقة مجانية حقيقية",
            "جودة العربية متفاوتة حسب الموديل",
            "يحتاج بطاقة",
        ),
        free=1, quality=4, arabic=3, speed=5, ease=3, privacy=2,
        cost_hint="مدفوع بالاستخدام",
        needs_card=True,
    ),
    "together": Verdict(
        provider="together",
        tagline="تشكيلة موديلات مفتوحة بأسعار معقولة",
        best_for=("الموديلات المفتوحة", "التحكم في التكلفة"),
        pros=("موديلات مفتوحة كثيرة", "أسعار معقولة", "مناسب للتجارب"),
        cons=("الجودة تعتمد على الموديل", "جودة العربية أقل من الكبار", "يحتاج بطاقة"),
        free=1, quality=3, arabic=3, speed=4, ease=3, privacy=2,
        cost_hint="مدفوع بالاستخدام",
        needs_card=True,
    ),
    "cerebras": Verdict(
        provider="cerebras",
        tagline="سرعة استثنائية للموديلات المفتوحة",
        best_for=("أقصى سرعة", "المهام القصيرة"),
        pros=("من الأسرع في العالم", "طبقة مجانية للتجربة"),
        cons=("موديلات محدودة", "جودة العربية متوسطة", "الحدود قد تكون ضيقة"),
        free=3, quality=3, arabic=3, speed=5, ease=3, privacy=2,
        cost_hint="مجاني محدود ثم مدفوع",
    ),
    "deepinfra": Verdict(
        provider="deepinfra",
        tagline="استضافة موديلات مفتوحة بتكلفة منخفضة",
        best_for=("التكلفة", "الموديلات المفتوحة"),
        pros=("رخيص", "موديلات كثيرة"),
        cons=("الجودة حسب الموديل", "جودة العربية متوسطة", "يحتاج بطاقة"),
        free=1, quality=3, arabic=2, speed=4, ease=3, privacy=2,
        cost_hint="مدفوع رخيص",
        needs_card=True,
    ),
    "fireworks": Verdict(
        provider="fireworks",
        tagline="منصة للموديلات المفتوحة بسرعة عالية",
        best_for=("الموديلات المفتوحة", "الإنتاج"),
        pros=("سرعة عالية", "موديلات مفتوحة كثيرة"),
        cons=("جودة العربية متوسطة", "يحتاج بطاقة", "إعداد أطول"),
        free=1, quality=3, arabic=2, speed=5, ease=2, privacy=2,
        cost_hint="مدفوع بالاستخدام",
        needs_card=True,
    ),
    "perplexity": Verdict(
        provider="perplexity",
        tagline="بحث مدعوم بمصادر — ليس بديلًا عامًا للشرح",
        best_for=("الأسئلة البحثية", "المصادر"),
        pros=("يربط الإجابة بمصادر حديثة", "ممتاز للأسئلة المعلوماتية"),
        cons=(
            "ليس مناسبًا للشرح التدريسي المتدرّج",
            "لا يعرض قائمة موديلات عبر الـAPI",
            "مدفوع",
        ),
        free=1, quality=4, arabic=3, speed=3, ease=3, privacy=2,
        cost_hint="مدفوع بالاستخدام",
        needs_card=True,
    ),
    "cohere": Verdict(
        provider="cohere",
        tagline="مزوّد مؤسّسي — مناسب للشركات أكثر من الطلاب",
        best_for=("الاستخدام المؤسّسي", "البحث الدلالي"),
        pros=("قوي في التضمين والبحث", "طبقة تجريبية محدودة"),
        cons=("جودة العربية متوسطة", "إعداد أطول", "موديلات أقل"),
        free=2, quality=3, arabic=2, speed=4, ease=2, privacy=3,
        cost_hint="مجاني محدود ثم مدفوع",
    ),
    "nvidia": Verdict(
        provider="nvidia",
        tagline="NVIDIA NIM — تجربة مجانية للموديلات المفتوحة",
        best_for=("التجربة المجانية", "الموديلات المفتوحة"),
        pros=("طبقة تجريبية مجانية", "موديلات مفتوحة كثيرة"),
        cons=("جودة العربية متفاوتة", "الحدود قد تتغيّر", "إعداد أطول"),
        free=3, quality=3, arabic=2, speed=4, ease=2, privacy=2,
        cost_hint="مجاني بحدود",
    ),
    "moonshot": Verdict(
        provider="moonshot",
        tagline="Kimi — نافذة سياق ضخمة بأسعار منخفضة",
        best_for=("النصوص الطويلة جدًا", "التكلفة"),
        pros=("سياق ضخم جدًا", "رخيص نسبيًا"),
        cons=("جودة العربية أقل من الكبار", "سرعة متفاوتة", "يحتاج بطاقة"),
        free=1, quality=3, arabic=2, speed=3, ease=2, privacy=1,
        cost_hint="مدفوع منخفض",
        needs_card=True,
    ),
    "qwen": Verdict(
        provider="qwen",
        tagline="Qwen — من الأفضل في العربية بين الموديلات المفتوحة",
        best_for=("العربية", "التكلفة المنخفضة"),
        pros=(
            "من أقوى الموديلات في دعم العربية",
            "تكلفة منخفضة",
            "متنوّع الأحجام",
        ),
        cons=(
            "الإعداد والتعامل مع النطاق قد يكون أصعب",
            "التوثيق بالعربية محدود",
            "يحتاج بطاقة في الغالب",
        ),
        free=2, quality=4, arabic=4, speed=3, ease=2, privacy=1,
        cost_hint="مجاني محدود ثم مدفوع رخيص",
    ),
    "azure_openai": Verdict(
        provider="azure_openai",
        tagline="OpenAI عبر Azure — للمؤسسات والامتثال",
        best_for=("المؤسسات", "الامتثال والخصوصية التعاقدية"),
        pros=("اتفاقيات مؤسسية", "خصوصية تعاقدية", "نفس جودة OpenAI"),
        cons=(
            "إعداد معقّد (حساب Azure ونشر موديل)",
            "أغلى عادةً",
            "يحتاج بيانات مؤسسة",
        ),
        free=1, quality=5, arabic=5, speed=4, ease=1, privacy=4,
        cost_hint="مدفوع مؤسسي",
        needs_card=True,
    ),
    "ollama": Verdict(
        provider="ollama",
        tagline="موديل على جهازك: خصوصية كاملة وصفر تكلفة",
        best_for=("الخصوصية المطلقة", "بلا إنترنت", "بلا تكلفة"),
        pros=(
            "لا يخرج أي نص من جهازك — خصوصية مطلقة",
            "صفر تكلفة تمامًا",
            "يعمل بلا إنترنت بعد التنزيل الأول",
            "لا يحتاج مفتاحًا ولا تسجيلًا",
        ),
        cons=(
            "يحتاج جهازًا قويًا (ذاكرة كافية) وسيرفرًا يعمل دائمًا",
            "جودة العربية أعلى الموديلات المحلية محدودة",
            "أبطأ من الخدمات السحابية",
            "تنزيل الموديل قد يستهلك جيجابايتات",
        ),
        free=5, quality=2, arabic=2, speed=2, ease=3, privacy=5,
        cost_hint="مجاني تمامًا (تحمّل تكلفة جهازك)",
        offline_capable=True,
    ),
    "lmstudio": Verdict(
        provider="lmstudio",
        tagline="موديل محلي بواجهة سهلة — خصوصية كاملة",
        best_for=("الخصوصية", "التجربة المحلية"),
        pros=("خصوصية كاملة", "واجهة سهلة لتحميل الموديلات", "مجاني"),
        cons=("يحتاج جهازًا قويًا", "جودة العربية محدودة", "أبطأ"),
        free=5, quality=2, arabic=2, speed=2, ease=3, privacy=5,
        cost_hint="مجاني تمامًا",
        offline_capable=True,
    ),
    "vllm": Verdict(
        provider="vllm",
        tagline="سيرفر محلي للتحكم الكامل — للمهندسين",
        best_for=("التحكم الكامل", "الخصوصية", "التحجيم"),
        pros=("تحكم كامل", "خصوصية", "مفتوح المصدر"),
        cons=("يحتاج خادمًا ومعرفة تقنية", "إعداد طويل", "جودة حسب موديلك"),
        free=5, quality=3, arabic=2, speed=3, ease=1, privacy=5,
        cost_hint="مجاني (تكلفة الخادم عليك)",
        offline_capable=True,
    ),
    "custom": Verdict(
        provider="custom",
        tagline="أي خدمة متوافقة مع OpenAI — مرونة كاملة",
        best_for=("الخدمات الخاصة", "التجريب"),
        pros=("يعمل مع أي خدمة متوافقة", "تحكم كامل في العنوان والموديل"),
        cons=("تحتاج معرفة بالعنوان الصحيح", "أنت مسؤول عن صحة الإعداد"),
        free=3, quality=3, arabic=3, speed=3, ease=1, privacy=3,
        cost_hint="حسب الخدمة",
    ),
}


# ------------------------------------------------------- مساعدات الاختيار
# خيارات «إيه أهم حاجة عندك؟» — كل اهتمام يُرجّح مقيّمًا
PRIORITIES: tuple[tuple[str, str, str], ...] = (
    ("free", "💰 مجاني", "أقل تكلفة ممكنة، ولو على حساب الجودة"),
    ("arabic", "🇪🇬 عربي قوي", "الأهم أن يفهم ويكتب عربيًا بشكل طبيعي"),
    ("privacy", "🔒 خصوصية", "لا أريد أن تخرج بياناتي من جهازي"),
    ("quality", "🎯 أعلى جودة", "مستعدّ أدفع لأفضل شرح"),
    ("speed", "⚡ سرعة", "رد فوري وقليل الانتظار"),
    ("ease", "🛠️ أسهل إعداد", "أريد مفتاحًا في دقيقة بلا تعقيد"),
)


def verdict(provider_key: str) -> Verdict | None:
    return VERDICTS.get(provider_key)


def priority_weight(priority: str, item: Verdict) -> int:
    """وزن مقيّم واحد حسب اهتمام المستخدم — كل اهتمام يقابل مقيّمًا."""
    mapping = {
        "free": item.free,
        "arabic": item.arabic,
        "privacy": item.privacy,
        "quality": item.quality,
        "speed": item.speed,
        "ease": item.ease,
    }
    return mapping.get(priority, 0)


def recommend(priority: str, limit: int = 3,
              allowed: tuple[str, ...] | None = None) -> list[tuple[str, Verdict]]:
    """أفضل (limit) مزوّدين حسب اهتمام المستخدم.

    الترتيب: المقيّم المطلوب أولًا، ثم المتوسط العام كفاصل — حتى لا
    يفوز مزوّد متخصّص ضعيف في كل شيء آخر.
    """
    pool = {
        key: item for key, item in VERDICTS.items()
        if item.provider != "custom" and (allowed is None or key in allowed)
    }
    ranked = sorted(
        pool.items(),
        key=lambda kv: (priority_weight(priority, kv[1]), kv[1].overall),
        reverse=True,
    )
    # لا نوصي بمزوّد بلا بيانات كافية عن اهتمام المستخدم
    return [item for item in ranked if priority_weight(priority, item[1]) > 0][:limit]


def compare_keys() -> list[str]:
    """المزوّدون المعروضون في شاشة المقارنة — أشهر الخيارات العملية."""
    return [
        "gemini", "openai", "anthropic", "groq", "openrouter",
        "deepseek", "mistral", "qwen", "ollama",
    ]


def score_bar(value: int, width: int = 5) -> str:
    """تمثيل بصري للمقيّم: ▰▰▰▱▱"""
    value = max(0, min(width, int(value)))
    return "▰" * value + "▱" * (width - value)


def stars(value: int) -> str:
    return "⭐" * max(0, min(5, int(value)))


def tradeoff_summary(provider_key: str) -> str:
    """سطر صادق للمقايضة الأساسية — أهم ما يحتاج المستخدم معرفته."""
    summaries = {
        "gemini": "مجاني وقوي في العربية — لكن له حدود يومية وقد يكون أبطأ.",
        "groq": "أسرع الكل — لكن جودة العربية أقل من Gemini وClaude.",
        "openai": "أعلى جودة — لكن لا مجاني فعلي وتحتاج بطاقة ورصيدًا.",
        "anthropic": "أفضل شرح طويل — لكن مدفوع فقط وبلا طبقة مجانية.",
        "openrouter": "مرونة قصوى بمفتاح واحد — لكن الجودة تعتمد على الموديل.",
        "deepseek": "أرخص الكل — لكن الخصوصية أضعف والعربية أقل.",
        "mistral": "خصوصية أوروبية — لكن العربية أضعف.",
        "qwen": "قوي في العربية ورخيص — لكن الإعداد أصعب.",
        "ollama": "خصوصية مطلقة وصفر تكلفة — لكن أبطأ وأضعف في العربية.",
        "custom": "أي خدمة متوافقة — لكن الإعداد عليك.",
    }
    return summaries.get(provider_key, "")
