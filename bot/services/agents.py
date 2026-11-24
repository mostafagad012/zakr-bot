"""الوكلاء (المزوّدون) — قراءة أسمائهم من `.env` وإضافتهم من البوت.

**المطلوب من المالك:** «لما أضيف مفتاح في البوت يبقى اسمه في `.env`
`aiagent_api_key` — مع تغيير `aiagent` باسم النموذج اللي هضيفه».
وقرّر أن التسمية **باسم المزوّد** لا باسم الموديل: مفتاح واحد يخدم كل
موديلات الخدمة نفسها. فصار التسمية:

    gemini   ⇒  gemini_api_key    (+ اختياريًا gemini_base_url / gemini_model)
    groq     ⇒  groq_api_key
    aiagent  ⇒  aiagent_api_key

**لماذا لا جدول في قاعدة البيانات؟** لأن المفتاح يجب أن يعيش في `.env`
كما طلب المالك — فالملف هو مصدر الحقيقة، والقراءة منه مباشرة تعني أن أي
تعديل يدوي في الملف يظهر في البوت بلا مزامنة ولا تعارض بين مصدرين.

**ثلاث قواعد**:

1. **اسم الوكيل يُتحقّق قبل الكتابة** — لأنه يصبح جزءًا من اسم متغيّر بيئة،
   واسم فيه شرطة أو مسافة أو حرف كبير يكسر الملف أو لا يُقرأ لاحقًا.
2. **لا نظلّل مزوّدًا ثابتًا** — لا يُسمح بإنشاء وكيل باسم `openai` أو
   `gemini`؛ تعديلها يتم من شاشة المزوّد نفسه لا كـ«وكيل جديد».
3. **القراءة تتجاوز حالة الإعدادات** — نقرأ `os.environ` مباشرة، فالإضافة
   تعمل فورًا بلا إعادة تشغيل (و`env_file.reload_settings` تُحدّث الباقي).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# اسم صالح: حروف إنجليزية صغيرة وأرقام وشرطة سفلية. نمنع الأكبر والأشرطة
# لأن أسماء متغيّرات البيئة على أنظمة مختلفة حسّاسة لحالة الأحرف وتُفسّر
# الشرطة كخيار سطر أوامر في بعض الأدوات.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,23}$")

# أسماء محجوزة: أسماء المزوّدين الثابتة (تُدار من شاشتها) وأسماء عامة قد
# تصطدم بمتغيّرات النظام أو بإعدادات البوت الأساسية.
RESERVED_EXTRA: tuple[str, ...] = (
    "ai", "bot", "config", "custom", "auto", "default", "env",
    "token", "key", "api", "url", "model", "owner", "admin",
)

# المزوّدون الثابتون في الكود: لا يصحّ إنشاء «وكيل» بأحد أسمائهم لأنه
# سيظلّل مزوّدًا حقيقيًا (ويُفسد مفتاحه القديم). مكرّرة هنا بوعي لتجنّب
# استيراد دائري مع `ai`؛ ويوجد اختبار يتأكد أنها مطابقة لمفاتيح `PROVIDERS`.
RESERVED_PROVIDERS: tuple[str, ...] = (
    "openai", "anthropic", "gemini", "azure_openai", "openrouter", "groq",
    "deepseek", "xai", "mistral", "together", "fireworks", "cerebras",
    "deepinfra", "perplexity", "cohere", "nvidia", "moonshot", "qwen",
    "ollama", "lmstudio", "vllm",
)

# اللاحقات التي تُعرّف الوكيل في `.env`
SUFFIXES: tuple[str, ...] = ("api_key", "base_url", "model")


@dataclass(frozen=True)
class Agent:
    """وكيل أضافه الأدمن — مشتقّ بالكامل من `.env`."""

    name: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    @property
    def key_var(self) -> str:
        return f"{self.name}_api_key"

    @property
    def url_var(self) -> str:
        return f"{self.name}_base_url"

    @property
    def model_var(self) -> str:
        return f"{self.name}_model"

    @property
    def is_complete(self) -> bool:
        """الوكيل المخصّص يحتاج عنوانًا؛ المفتاح اختياري (موديلات محلية)."""
        return bool(self.base_url)

    @property
    def label(self) -> str:
        return f"{self.name} (وكيل مخصّص)"


def normalize_name(raw: str) -> str:
    """يحوّل ما يكتبه الأدمن إلى اسم صالح: `AI Agent!` ⇒ `ai_agent`."""
    text = (raw or "").strip().lower()
    text = re.sub(r"[\s\-.]", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def validate_name(raw: str, static_names: set[str] | None = None) -> tuple[bool, str]:
    """هل يصلح هذا الاسم لمتغيّر بيئة؟ يُرجع (صالح، سبب الرفض أو الاسم المقترح)."""
    name = normalize_name(raw)
    if not name:
        return False, "الاسم فاضي — اكتب حروفًا إنجليزية"
    if not NAME_PATTERN.match(name):
        return False, "الاسم لازم يبدأ بحرف ويكون ٢–٢٤ حرفًا إنجليزيًا صغيرًا أو رقمًا أو _"
    if name in RESERVED_EXTRA or name in RESERVED_PROVIDERS:
        return False, f"«{name}» اسم محجوز — اختر غيره"
    if static_names and name in static_names:
        return False, (
            f"«{name}» مزوّد ثابت في البوت — تُعدّله من شاشته، لا كـ«وكيل جديد»"
        )
    return True, name


def _env(name: str) -> str:
    return (os.environ.get(name, "") or "").strip().strip('"').strip("'")


def from_env(name: str) -> Agent:
    """يبني وكيلًا من متغيّرات البيئة الحالية."""
    return Agent(
        name=name,
        api_key=_env(f"{name}_api_key"),
        base_url=_env(f"{name}_base_url"),
        model=_env(f"{name}_model"),
    )


def discover(static_names: set[str] | None = None) -> dict[str, Agent]:
    """كل الوكلاء المعرَّفين في البيئة/`.env` — بأي لاحقة من اللواحق الثلاث.

    نكتشف الاسم من أي متغيّر معروف (مفتاح أو عنوان أو موديل)، لأن الأدمن قد
    يضيف وكيلًا محليًا بلا مفتاح، أو مفتاحًا ثم يضيف عنوانه لاحقًا.
    """
    static_names = static_names or set()
    found: dict[str, Agent] = {}
    for raw_name in list(os.environ):
        lowered = raw_name.lower()
        for suffix in SUFFIXES:
            if not lowered.endswith(f"_{suffix}"):
                continue
            base = lowered[: -(len(suffix) + 1)]
            if not base or not NAME_PATTERN.match(base):
                break
            if base in static_names or base in RESERVED_EXTRA \
                    or base in RESERVED_PROVIDERS:
                break
            agent = from_env(base)
            if any((agent.api_key, agent.base_url, agent.model)):
                found[base] = agent
            break
    return found


def legacy_key(provider: str) -> str:
    """اسم المفتاح بالصيغة القديمة `AI_API_KEY_<PROVIDER>` — للتوافق فقط."""
    return f"AI_API_KEY_{provider.strip().upper().replace('-', '_')}"


def env_vars_for(name: str) -> tuple[str, str, str]:
    """أسماء المتغيّرات الثلاثة لوكيل — للعرض في الشاشة."""
    agent = Agent(name=name)
    return agent.key_var, agent.url_var, agent.model_var
