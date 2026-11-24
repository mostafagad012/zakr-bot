"""طبقة الذكاء الاصطناعي — تدعم كل المزوّدين والموديلات.

الفكرة: الغالبية العظمى من الخدمات (OpenAI، Gemini، Groq، DeepSeek، Mistral،
xAI، Together، Fireworks، Cerebras، DeepInfra، Perplexity، Cohere، NVIDIA،
Moonshot، Qwen، Ollama، LM Studio، vLLM…) تعرض **واجهة متوافقة مع OpenAI**:
نفس المسار `/chat/completions` ونفس شكل الطلب والرد. فعميل HTTP واحد
يغطيها كلها، ويتبدّل المزوّد بسطر في `.env`.

الاستثناء الوحيد الجوهري: **Anthropic (Claude)** — لها واجهة مختلفة
(`/messages`، `x-api-key`، ورد بشكل `content[0].text`)، فلها محوّل خاص
داخل نفس الطبقة. كل ما دونه انحراف بسيط في العنوان فقط.

قواعد التصميم (مقصودة، لا تُبسّط):

* **لا ترمي استثناءً خامًا أبدًا** — كل فشل يُغلَّف في `AIError` برسالة
  عربية مفهومة تصلح للعرض للمستخدم.
* **المفتاح لا يُسجَّل ولا يظهر في أي رسالة خطأ.** الطلبات تُبنى في مكان
  واحد، وأي رسالة تُنقّى من المفتاح قبل التسجيل أو العرض.
* **المحتوى من المستخدم/الملفات بيانات لا أوامر** (`wrap_untrusted`)،
  وأهم ضمانة أن النموذج لا يملك أدوات: مخرجه نص فقط.
* **حدود صلبة**: طول مدخل، طول مخرج، مهلة، عدد محاولات.
* **توافق المزوّدين**: بعض الموديلات ترفض `max_completion_tokens` أو
  `temperature`؛ عند 400 نعيد المحاولة بعد إزالة البارامتر المرفوض.
* **الموديلات المحلية بلا مفتاح** (Ollama/LM Studio/vLLM): تعمل بمفتاح
  وهمي، فلا يشترط التكوين وجود مفتاح.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from dataclasses import dataclass

import httpx

from ..config import settings
from . import agents as agents_service

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- الحدود
MAX_INPUT_CHARS = 12000         # أقصى ما يُرسل في رسالة واحدة
MAX_OUTPUT_TOKENS = 1200        # أقصى ما نطلبه من النموذج
REQUEST_TIMEOUT = 60.0          # ثانية
MAX_RETRIES = 2                 # إعادات المحاولة عند 429/5xx/مهلة
MAX_RESPONSE_BYTES = 2_000_000  # حماية من رد ضخم غير متوقع

_KEY_IN_URL = re.compile(r"(key=)[A-Za-z0-9_\-]+")


class AIError(Exception):
    """فشل في طبقة الذكاء الاصطناعي — رسالة عربية صالحة للعرض."""


class AINotConfigured(AIError):
    """لا يوجد مفتاح/موديل مُعدّ — الميزة معطّلة بهدوء."""


# ---------------------------------------------------------------- المزوّدون
@dataclass(frozen=True)
class Provider:
    """وصف مزوّد. `api_style` هي واجهة البرمجة التي يتكلّمها."""

    key: str
    label: str
    base_url: str
    default_model: str
    key_url: str = ""
    api_style: str = "openai"     # openai | anthropic
    requires_key: bool = True     # الموديلات المحلية لا تحتاج مفتاحًا
    key_prefixes: tuple[str, ...] = ()  # للكشف التلقائي من شكل المفتاح
    supports_model_list: bool = True  # بعض المزوّدين بلا نقطة /models
    notes: str = ""


# كل العناوين تحقّقت فعليًا: ردّت 401/400 (موجودة وتطلب مفتاحًا) لا 404.
PROVIDERS: dict[str, Provider] = {
    # ------------------------------------------------ العالمية
    "openai": Provider(
        "openai", "OpenAI",
        "https://api.openai.com/v1", "gpt-5-mini",
        "https://platform.openai.com/api-keys",
        key_prefixes=("sk-proj-", "sk-"),
    ),
    "anthropic": Provider(
        "anthropic", "Anthropic (Claude)",
        "https://api.anthropic.com/v1", "claude-sonnet-4-5",
        "https://console.anthropic.com/settings/keys",
        api_style="anthropic", key_prefixes=("sk-ant-",),
    ),
    "gemini": Provider(
        "gemini", "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.5-flash",
        "https://aistudio.google.com/apikey",
        key_prefixes=("AIza",),
        notes="طبقة مجانية سخية",
    ),
    "azure_openai": Provider(
        "azure_openai", "Azure OpenAI",
        "", "",  # لا عنوان افتراضي — يختلف لكل عميل
        "https://portal.azure.com",
        notes="يتطلب AI_BASE_URL و AI_MODEL وأحيانًا AI_API_KEY",
    ),
    # ------------------------------------------------ سريعة/رخيصة
    "groq": Provider(
        "groq", "Groq",
        "https://api.groq.com/openai/v1", "openai/gpt-oss-120b",
        "https://console.groq.com/keys",
        key_prefixes=("gsk_",),
        notes="الأسرع، مجاني بحدود",
    ),
    "cerebras": Provider(
        "cerebras", "Cerebras",
        "https://api.cerebras.ai/v1", "llama-3.3-70b",
        "https://cloud.cerebras.ai",
        key_prefixes=("csk-",),
    ),
    "deepinfra": Provider(
        "deepinfra", "DeepInfra",
        "https://api.deepinfra.com/v1/openai", "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "https://deepinfra.com/dash/api_keys",
    ),
    "fireworks": Provider(
        "fireworks", "Fireworks AI",
        "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "https://fireworks.ai/account/api-keys",
    ),
    # ------------------------------------------------ بوابات متعددة الموديلات
    "openrouter": Provider(
        "openrouter", "OpenRouter",
        "https://openrouter.ai/api/v1", "openai/gpt-4o-mini",
        "https://openrouter.ai/keys",
        key_prefixes=("sk-or-",),
        notes="كل الموديلات بمفتاح واحد (٥٠٠+ موديل)",
    ),
    "together": Provider(
        "together", "Together AI",
        "https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "https://api.together.ai/settings/api-keys",
    ),
    "perplexity": Provider(
        "perplexity", "Perplexity",
        "https://api.perplexity.ai", "sonar",
        "https://www.perplexity.ai/settings/api",
        key_prefixes=("pplx-",),
        supports_model_list=False,  # لا توجد نقطة /models
    ),
    # ------------------------------------------------ نماذج أخرى
    "deepseek": Provider(
        "deepseek", "DeepSeek",
        "https://api.deepseek.com/v1", "deepseek-chat",
        "https://platform.deepseek.com/api_keys",
        key_prefixes=("sk-",),
        notes="رخيص جدًا",
    ),
    "mistral": Provider(
        "mistral", "Mistral AI",
        "https://api.mistral.ai/v1", "mistral-large-latest",
        "https://console.mistral.ai/api-keys",
    ),
    "xai": Provider(
        "xai", "xAI (Grok)",
        "https://api.x.ai/v1", "grok-4",
        "https://console.x.ai",
        key_prefixes=("xai-",),
    ),
    "moonshot": Provider(
        "moonshot", "Moonshot (Kimi)",
        "https://api.moonshot.cn/v1", "moonshot-v1-8k",
        "https://platform.moonshot.cn/console/api-keys",
        key_prefixes=("sk-",),
    ),
    "qwen": Provider(
        "qwen", "Alibaba Qwen (DashScope)",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen-plus",
        "https://dashscope-intl.console.aliyun.com",
        key_prefixes=("sk-",),
        notes="قوي في العربية",
    ),
    "nvidia": Provider(
        "nvidia", "NVIDIA NIM",
        "https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct",
        "https://build.nvidia.com",
        key_prefixes=("nvapi-",),
    ),
    "cohere": Provider(
        "cohere", "Cohere",
        "https://api.cohere.ai/compatibility/v1", "command-r-plus-08-2024",
        "https://dashboard.cohere.com/api-keys",
    ),
    # ------------------------------------------------ محلية (بلا مفتاح)
    "ollama": Provider(
        "ollama", "Ollama (موديل محلي)",
        "http://localhost:11434/v1", "llama3.2",
        "https://ollama.com/download",
        requires_key=False,
        notes="يعمل على جهازك بلا تكلفة ولا إنترنت",
    ),
    "lmstudio": Provider(
        "lmstudio", "LM Studio (موديل محلي)",
        "http://localhost:1234/v1", "local-model",
        "https://lmstudio.ai",
        requires_key=False,
    ),
    "vllm": Provider(
        "vllm", "vLLM / سيرفر محلي",
        "http://localhost:8000/v1", "default",
        "",
        requires_key=False,
        notes="مخدوم ذاتيًا",
    ),
    # ------------------------------------------------ أي خدمة أخرى
    "custom": Provider(
        "custom", "خدمة أخرى متوافقة",
        "", "",
        "",
        notes="يتطلب AI_BASE_URL (+ AI_MODEL)",
    ),
}

DEFAULT_PROVIDER = "openai"

# ترتيب العرض في لوحة الإدارة والقوائم
# ------------------------------------------- الوكلاء المخصّصون من `.env`
# الوكلاء الذين سجّلناهم نحن (لتفريقهم عن المزوّدين الثابتين عند التنظيف)
_registered_agents: set[str] = set()


def register_agents() -> list[str]:
    """يفعّل كل وكيل معرَّف في `.env` كمزوّد كامل داخل `PROVIDERS`.

    **لماذا؟** كل الميزات (الشرح، التلخيص، الامتحانات، القائمة الحيّة،
    بطاقات الشرح) تتعامل مع `PROVIDERS`. فبدل أن نضيف مسارًا موازيًا
    للوكلاء — وهو ما يُنتج ميزتين تتباعدان مع الوقت — نحوّل الوكيل إلى
    `Provider` عادي: فيعمل معه كل شيء بلا كود إضافي.

    يُستدعى عند الاستيراد وبعد كل تعديل في `.env` (‏`env_file.reload_settings`).
    """
    from . import agents as agents_service

    # الوكلاء الذين أضفناهم سابقًا ليسوا «ثابتين» — يجب أن يُعاد اكتشافهم
    # بعد كل تحديث، ولو مررناهم ضمن المحجوزين لاختفوا من القائمة.
    static = set(PROVIDERS) - _registered_agents
    found = agents_service.discover(static)

    added: list[str] = []
    for name, agent in found.items():
        if not agent.is_complete:
            # وكيل بلا عنوان API لا يمكن نداؤه — لا نعرضه كخيار صالح
            continue
        existing = PROVIDERS.get(name)
        provider = Provider(
            key=name,
            label=agent.label,
            base_url=agent.base_url,
            default_model=agent.model or (existing.default_model if existing else ""),
            key_url="",
            api_style="openai",
            requires_key=bool(agent.api_key),
            key_prefixes=(),
            supports_model_list=True,
        )
        PROVIDERS[name] = provider
        if name not in PROVIDER_ORDER:
            PROVIDER_ORDER.append(name)      # type: ignore[arg-type]
        _registered_agents.add(name)
        added.append(name)

    # وكيل حُذفت متغيّراته من `.env` (يدويًا أو من الشاشة) يجب أن يختفي،
    # وإلا بقي خيارًا في القوائم بلا مفتاح ولا عنوان.
    for stale in _registered_agents - set(found):
        _registered_agents.discard(stale)
        PROVIDERS.pop(stale, None)
        if stale in PROVIDER_ORDER:
            PROVIDER_ORDER.remove(stale)
    return added


PROVIDER_ORDER: list[str] = [
    "openai", "anthropic", "gemini", "azure_openai",
    "openrouter", "groq", "deepseek", "xai", "mistral",
    "together", "fireworks", "cerebras", "deepinfra",
    "perplexity", "cohere", "nvidia", "moonshot", "qwen",
    "ollama", "lmstudio", "vllm", "custom",
]


def _specific_key(provider_key: str) -> str:
    """مفتاح مزوّد بعينه من البيئة — تسمية جديدة أولًا ثم القديمة."""
    name = (provider_key or "").strip().lower()
    # الشكل الصغير القياسي، ثم الشكل الكبير الذي تكتبه واجهات المزوّدين
    # (`GEMINI_API_KEY`)، ثم الصيغة القديمة. بلا الشكل الكبير كان مفتاح
    # مكتوب بأحرف كبيرة **غير مرئي** للطبقة كلها.
    for variable in (f"{name}_api_key", f"{name.upper()}_API_KEY",
                     agents_service.legacy_key(name)):
        value = os.environ.get(variable, "").strip()
        if value:
            return value
    specific = (getattr(settings, "ai_provider_keys", None) or {}).get(name, "")
    return specific.strip() if isinstance(specific, str) else ""


def provider_model(provider_key: str) -> str:
    """الموديل الفعلي لمزوّد: `<name>_model` ثم `AI_MODEL` ثم الافتراضي."""
    name = (provider_key or "").strip().lower()
    specific = os.environ.get(f"{name}_model", "").strip()
    if specific:
        return specific
    if name == (settings.ai_provider or "").strip().lower():
        return _model()
    provider = PROVIDERS.get(name)
    return provider.default_model if provider else ""


def auto_choice() -> tuple[str, str]:
    """وضع `auto`: (المزوّد، المفتاح) بلا تخمين أعمى.

    الترتيب: المفتاح العام `AI_API_KEY` (ويُستدلّ على مزوّده من بادئته)،
    وإلا **أول مزوّد له مفتاح خاص في `.env`** — وهذا ما يجعل إضافة وكيل
    جديد بمفتاحه تعمل مباشرة ولو تُرك `AI_PROVIDER=auto`.
    """
    generic = (settings.ai_api_key or "").strip()
    if generic:
        return detect_provider_from_key(generic) or "", generic
    for name in PROVIDER_ORDER:
        key = _specific_key(name)
        if key:
            return name, key
    return "", ""


def get_provider(key: str | None = None) -> Provider:
    """المزوّد المطلوب، مع تراجع آمن إلى الافتراضي عند اسم غير معروف.

    `AI_PROVIDER=auto` يكشف المزوّد من شكل المفتاح تلقائيًا (‏`sk-ant-` →
    Anthropic، `gsk_` → Groq، `AIza` → Gemini…)، فإن فشل الكشف رجع OpenAI.
    """
    name = (key or settings.ai_provider or DEFAULT_PROVIDER).strip().lower()
    if name == "auto":
        # لا نستعمل `_api_key()` هنا لأنها تنادي `get_provider()` نفسها
        # في وضع auto فيصير استدعاءً ذاتيًا لا ينتهي.
        chosen, _value = auto_choice()
        return PROVIDERS.get(chosen or DEFAULT_PROVIDER, PROVIDERS[DEFAULT_PROVIDER])
    return PROVIDERS.get(name, PROVIDERS[DEFAULT_PROVIDER])


def detect_provider_from_key(api_key: str) -> str | None:
    """يخمّن المزوّد من شكل المفتاح — مساعدة للمستخدم، لا تُلزم أحدًا.

    يُستعمل فقط عند `AI_PROVIDER=auto` أو لعرض اقتراح في اللوحة.
    الأطول أولًا حتى لا يبتلع `sk-` مفاتيح `sk-ant-`/`sk-or-`.
    """
    key = (api_key or "").strip()
    if not key:
        return None
    matches = [
        (prefix, provider.key)
        for provider in PROVIDERS.values()
        for prefix in provider.key_prefixes
        if key.startswith(prefix)
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: len(item[0]), reverse=True)
    return matches[0][1]


def key_for(provider_key: str) -> str:
    """مفتاح مزوّد محدّد — يسمح للمستخدم بحفظ مفاتيح عدّة معًا.

    الأولوية:
    1) `<PROVIDER>_api_key` (مثل `gemini_api_key`) — التسمية المطلوبة.
    2) `AI_API_KEY_<PROVIDER>` — الصيغة القديمة، تبقى مدعومة حتى لا تتعطّل
       إعدادات قائمة عند التحديث.
    3) `AI_API_KEY` — المفتاح العام (وهو الوحيد المطلوب في أبسط إعداد).

    لماذا؟ المستخدم يختار مزوّده من البوت، فإن حفظ مفتاحًا لكل مزوّد
    استطاع التبديل من الإعدادات بلا لمس `.env` — وهذا جوهر «AI على مزاجه».
    """
    name = (provider_key or "").strip().lower()
    specific = _specific_key(name)
    if specific:
        return specific          # التسمية المعتمدة: <name>_api_key (أو القديمة)

    if (settings.ai_provider or "").strip().lower() == "auto":
        # في وضع auto: المفتاح العام **إن كان يخصّ هذا المزوّد**. وإلا نرجع
        # فارغًا — لا نُعيد مفتاح مزوّد آخر.
        #
        # **هذا خطأ حقيقي:** `key_for("gemini")` كانت تُرجع مفتاح Groq في
        # وضع auto، فتقول الشاشة «عندك مفتاح» بينما الطلب سيُرسَل بمفتاح
        # مزوّد آخر ويُرفض 401 — وهو أسوأ من «لا مفتاح» لأنه بلا مخرج ظاهر.
        chosen, chosen_key = auto_choice()
        if not chosen_key:
            return ""
        if chosen and name and chosen != name:
            return ""
        return chosen_key

    # المفتاح العام يخدم المزوّد النشط المعلن في `AI_PROVIDER`
    generic = (settings.ai_api_key or "").strip()
    if name and name != (settings.ai_provider or "").strip().lower():
        return ""
    return generic


def _api_key() -> str:
    """مفتاح المزوّد النشط حاليًا."""
    return key_for(get_provider().key)


def _model() -> str:
    return (settings.ai_model or "").strip() or get_provider().default_model


def _base_url() -> str:
    """عنوان الـ API. المزوّد المخصّص/المحلي قد يحتاج `AI_BASE_URL` صريحًا."""
    custom = (settings.ai_base_url or "").strip().rstrip("/")
    if custom:
        return custom
    return get_provider().base_url.rstrip("/")


def reset_settings() -> None:
    """يرجع الطبقة إلى كائن الإعدادات الرسمي في `config`.

    نقطة رجوع واحدة يستخدمها `env_file.reload_settings` والاختبارات: كل من
    يستبدل `ai.settings` مؤقتًا (لاختبار سلوك معيّن) يُعيده من هنا، فيبقى
    الإعداد واحدًا في الذاكرة ولا يتسرّب تعديل اختبار إلى آخر.
    """
    global settings
    from ..config import settings as env_settings

    settings = env_settings


def is_configured(*, provider: str | None = None, api_key: str | None = None) -> bool:
    """هل الطبقة جاهزة؟ (عنوان + موديل، ومفتاح إلا للمحلي)

    `provider`/`api_key` يقيسان **مزوّدًا بعينه بمفتاحه** لا المزوّد العام:
    شاشة حفظ المفتاح تعرض حالة المفتاح الذي أدخله المستخدم الآن، فقياس
    المزوّد العام هناك يجعل الرسالة تصف حالة أخرى غير التي أمامه.
    """
    if provider or api_key:
        chosen = get_provider(provider)
        if chosen.requires_key and not (api_key or key_for(chosen.key)):
            return False
        return bool((settings.ai_base_url or "").strip() or chosen.base_url)
    current = get_provider()
    if current.requires_key and not _api_key():
        return False
    return bool(_base_url() and _model())


def status_text(provider: str | None = None) -> str:
    """وصف مختصر للحالة — يُعرض في لوحة الإدارة."""
    chosen = get_provider(provider)
    if provider:
        if chosen.requires_key and not key_for(chosen.key):
            return (
                "⚠️ لا مفتاح محفوظ لهذا المزوّد\n"
                f"المزوّد: {chosen.label}"
                + (f"\nالمفتاح من: {chosen.key_url}" if chosen.key_url else "")
            )
        return f"✅ جاهز — {chosen.label} · الموديل: {_model()}"
    if chosen.requires_key and not _api_key():
        return (
            "⚠️ الذكاء الاصطناعي غير مُعدّ\n"
            f"المزوّد: {chosen.label}\n"
            "أضف AI_API_KEY في .env"
            + (f"\nالمفتاح من: {chosen.key_url}" if chosen.key_url else "")
        )
    if not _base_url():
        return f"⚠️ AI_BASE_URL مطلوب لمزوّد «{chosen.label}»"
    if not _model():
        return f"⚠️ AI_MODEL مطلوب لمزوّد «{chosen.label}»"
    return f"✅ جاهز — {chosen.label} · الموديل: {_model()}"


# ------------------------------------------------------- حماية المحتوى
_UNTRUSTED_OPEN = "<<<بداية المحتوى>>>"
_UNTRUSTED_CLOSE = "<<<نهاية المحتوى>>>"


def wrap_untrusted(text: str, limit: int = MAX_INPUT_CHARS) -> str:
    """يغلّف محتوى غير موثوق (نص PDF أو مدخل مستخدم) ليتعامل معه النموذج كبيانات.

    لا يمنع الحقن منعًا تامًا (لا شيء يمنعه) لكنه يقلّله، وأهم ضمانة أن
    النموذج لا يملك أدوات: مخرجه نص يُهرب قبل الإرسال، فلا ينفّذ شيئًا.
    """
    body = (text or "").strip()
    if len(body) > limit:
        body = body[:limit] + "\n… (اقتُطع الباقي لطول النص)"
    body = body.replace(_UNTRUSTED_OPEN, "[محجوب]").replace(_UNTRUSTED_CLOSE, "[محجوب]")
    return f"{_UNTRUSTED_OPEN}\n{body}\n{_UNTRUSTED_CLOSE}"


def _scrub(message: str) -> str:
    """ينزع المفتاح من أي نص قبل التسجيل أو العرض."""
    key = _api_key()
    if key and key in message:
        message = message.replace(key, "***")
    return _KEY_IN_URL.sub(r"\1***", message)


def clean_output(text: str) -> str:
    """تنظيف مخرجات النموذج قبل العرض: بلا رموز تحكّم وبلا فراغات زائدة."""
    if not text:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def to_html(text: str) -> str:
    """يحوّل مخرجات النموذج إلى HTML آمن لتيليجرام.

    مخرجات النموذج نص غير موثوق: قد تحوي `<` أو `&` فتنكسر رسالة تيليجرام
    (`can't parse entities`) أو تُفسَّر كوسم. التهريب مركزي هنا حتى لا
    تنساه أي ميزة تستخدم الذكاء الاصطناعي.
    """
    # `html.escape` في تمريرة واحدة: التهريب المتتابع بأيدينا كان يولّد
    # `&amp;lt;` مزدوجًا فتظهر الأقواس حرفيًا بدل التعامل معها كنص آمن.
    return html.escape(clean_output(text), quote=False)


# ------------------------------------------------------------- الاستدعاء
def _build_request(
    messages: list[dict],
    max_tokens: int,
    *,
    provider_key: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[str, dict, dict]:
    """يبني (المسار، الجسم، الترويسات) حسب واجهة المزوّد.

    `provider_key` / `api_key` / `model` تجاوزٌ صريح للطلب الواحد — أُضيف
    لمسار قراءة الملفات، حيث كل مستخدم يستخدم **مفتاحه هو** ومزوّده هو،
    لا المفتاح العام في `.env`. بلا هذا التجاوز كان أي طالب في مجموعة
    واحدة يقرأ ملفًا بمفتاح المالك.
    """
    provider = get_provider(provider_key) if provider_key else get_provider()
    if model is None:
        # ⚠️ فرق جوهري: `_model()` تقرأ الموديل **العام** من `.env`، وهو
        # يخصّ المزوّد العام لا المزوّد المطلوب. تمريره لمزوّد آخر يُنتج
        # 404 «الموديل غير موجود» ويُقرأ عند المستخدم كأن مفتاحه فاسد.
        # (مثال حقيقي: `AI_MODEL=gpt-4o` مع اختيار Groq.)
        # لذلك: مزوّد صريح ⇒ موديله الافتراضي، إلا لو مرّر المستخدم موديله.
        if provider_key and provider_key.strip().lower() != (
            settings.ai_provider or ""
        ).strip().lower():
            model = provider.default_model
        else:
            model = _model()
    key_value = api_key if api_key is not None else _api_key()

    if provider.api_style == "anthropic":
        # Anthropic: النظام بارامتر منفصل، والمخرجات في max_tokens إلزامي
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        convo = [m for m in messages if m.get("role") != "system"]
        payload: dict = {"model": model, "max_tokens": max_tokens, "messages": convo}
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        headers = {
            "x-api-key": key_value,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        return f"{_base_url()}/messages", payload, headers

    payload = {"model": model, "messages": messages}
    payload["max_completion_tokens"] = max_tokens
    payload["temperature"] = settings.ai_temperature
    headers = {
        "Authorization": f"Bearer {key_value}",
        "Content-Type": "application/json",
    }
    return f"{_base_url()}/chat/completions", payload, headers


def _extract_content(payload: dict) -> str:
    """يستخرج النص من رد المزوّد بأشكاله المختلفة."""
    try:
        # --- شكل Anthropic ---
        if isinstance(payload.get("content"), list):
            parts = [
                str(block.get("text", ""))
                for block in payload["content"]
                if isinstance(block, dict) and block.get("type") in (None, "text")
            ]
            joined = "".join(parts).strip()
            if joined:
                return clean_output(joined)

        # --- شكل OpenAI المتوافق ---
        choices = payload.get("choices") or []
        if not choices:
            raise AIError("لم يرجع المزوّد أي نتيجة")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(chunk["text"])
                for chunk in content
                if isinstance(chunk, dict) and chunk.get("text")
            )
        if not content or not str(content).strip():
            # بعض موديلات الاستدلال تضع النص في حقل منفصل
            content = message.get("reasoning_content") or ""
        if not str(content).strip():
            raise AIError("المزوّد رجع ردًا فارغًا")
        return clean_output(str(content))
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise AIError("رد المزوّد بصيغة غير متوقّعة") from exc


async def chat(
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    timeout: float | None = None,
    retries: int = MAX_RETRIES,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """يرسل محادثة ويعيد نص الرد. يرمي `AIError` فقط.

    إعادات المحاولة محصورة في 429/5xx/المهلة — الأخطاء المنطقية
    (400/401/404) لا تنجح بالتكرار فلا نضيّع وقتًا فيها.

    `provider` / `api_key` / `model`: تجاوز للطلب الواحد. مطلوب لمسار
    قراءة الملفات حيث لكل مستخدم مفتاحه، ولا يصحّ إلزامه بضبط `.env`
    العام: **الفحص `is_configured()` يُتخطّى حين يُمرَّر مفتاح صريح**،
    وإلا صار المستخدم يملك مفتاحًا صالحًا ويُرفض لأن المالك لم يضبط مفتاحًا.
    """
    explicit = bool(api_key)
    if not explicit and not is_configured():
        raise AINotConfigured(
            "الذكاء الاصطناعي غير مُعدّ — أضف AI_API_KEY في .env (انظر .env.example)"
        )

    url, payload, headers = _build_request(
        messages, max_tokens or MAX_OUTPUT_TOKENS,
        provider_key=provider, api_key=api_key, model=model,
    )
    deadline = timeout or settings.ai_timeout or REQUEST_TIMEOUT
    last_error = "فشل غير معروف"

    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=deadline) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            last_error = "انتهت مهلة الاتصال بالذكاء الاصطناعي"
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise AIError(last_error) from None
        except httpx.HTTPError as exc:
            raise AIError(f"تعذّر الاتصال بالذكاء الاصطناعي: {_scrub(str(exc))}") from None

        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise AIError("رد ضخم غير متوقّع من المزوّد")

        if resp.status_code == 400:
            detail = _scrub(_error_detail(resp))
            # بعض الموديلات ترفض أحد البارامترات — نزيله ونعيد
            if "max_completion_tokens" in detail and "max_completion_tokens" in payload:
                payload.pop("max_completion_tokens", None)
                payload["max_tokens"] = max_tokens or MAX_OUTPUT_TOKENS
                continue
            if "max_tokens" in detail and "max_tokens" in payload:
                payload.pop("max_tokens", None)
                continue
            if "temperature" in detail and "temperature" in payload:
                payload.pop("temperature", None)
                continue
            raise AIError(f"طلب مرفوض من المزوّد: {detail}")

        if resp.status_code in (401, 403):
            active = get_provider(provider) if provider else get_provider()
            hint = f" (المفتاح من {active.key_url})" if active.key_url else ""
            raise AIError(
                f"المفتاح مرفوض — راجع المفتاح ومزوّد {active.label}{hint}"
            )
        if resp.status_code == 404:
            shown = model or _model()
            raise AIError(
                f"الموديل «{shown}» غير موجود عند المزوّد — "
                "افتح لوحة الإدارة → 🤖 الذكاء الاصطناعي → قائمة الموديلات"
            )
        if resp.status_code == 429:
            last_error = "تجاوزت حدّ الاستخدام (429) — انتظر قليلًا"
            if attempt < retries:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            raise AIError(last_error)
        if resp.status_code >= 500:
            last_error = "خدمة الذكاء الاصطناعي تعطّلت مؤقتًا"
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise AIError(last_error)

        try:
            return _extract_content(resp.json())
        except ValueError:
            raise AIError("رد المزوّد ليس JSON صالحًا") from None

    raise AIError(last_error)


def _error_detail(resp: httpx.Response) -> str:
    """يستخرج رسالة الخطأ من رد المزوّد بحدّ طول آمن."""
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "")[:300]
    # بعض المزوّدين يُرجعون قائمة أو نصًا بدل كائن JSON — لا نفترض الشكل.
    if not isinstance(data, dict):
        return str(data)[:300]
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    if err:
        return str(err)[:300]
    return str(data)[:300]


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """نداء بسيط: تعليمات + مدخل ← نص.

    الوسائط الثلاثة الأخيرة تُمرَّر إلى `chat` كما هي — بدونها كان أي نداء
    «فحص» يقيس المزوّد العام في `.env` ولو كان المقصود مزوّد المستخدم.
    """
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return await chat(
        messages, max_tokens=max_tokens, timeout=timeout,
        provider=provider, api_key=api_key, model=model,
    )


async def list_models(provider_key: str | None = None) -> list[str]:
    """أسماء الموديلات المتاحة فعلًا عند المزوّد — للتشخيص من اللوحة.

    أسماء الموديلات تتغيّر بسرعة، فبدل تخمين اسم صحيح نقرأه من المزوّد.
    `provider_key` يسمح بجلب قائمة مزوّد بعينه بلا تغيير المزوّد النشط —
    فيبقى إعداد `.env` كما هو أثناء العرض.
    """
    provider = get_provider(provider_key)
    key_value = key_for(provider.key)
    base = (settings.ai_base_url or "").strip().rstrip("/") if provider_key is None else ""
    base = base or provider.base_url
    if not base:
        raise AINotConfigured("لا يوجد عنوان API — أضف AI_BASE_URL")
    if not provider.supports_model_list:
        raise AIError(
            f"مزوّد «{provider.label}» لا يعرض قائمة موديلات — "
            "اكتب اسم الموديل يدويًا في AI_MODEL"
        )
    headers = {"x-api-key": key_value} if provider.api_style == "anthropic" else {
        "Authorization": f"Bearer {key_value}"
    }
    if provider.api_style == "anthropic":
        headers["anthropic-version"] = "2023-06-01"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise AIError(f"تعذّر جلب الموديلات: {_scrub(str(exc))}") from None

    if resp.status_code != 200:
        raise AIError(f"المزوّد رفض طلب الموديلات ({resp.status_code}): {_error_detail(resp)}")
    try:
        data = resp.json()
    except ValueError:
        raise AIError("رد الموديلات ليس JSON صالحًا") from None

    items = data.get("data") if isinstance(data, dict) else data
    names: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name")
            if name:
                names.append(str(name))
        elif isinstance(item, str):
            names.append(item)
    return sorted(names)


async def healthcheck(
    *, provider: str | None = None, api_key: str | None = None, model: str | None = None
) -> tuple[bool, str]:
    """اختبار حقيقي صغير: يكشف خطأ المفتاح أو الموديل قبل أن يراه المستخدم.

    كان النداء بلا وسائط، فيقيس **المزوّد العام** في `.env` ويُبلّغ عنه —
    بينما القيمة المعروضة للمستخدم هي مفتاحه هو. فمستخدم يحفظ مفتاحًا
    سليمًا قد يقرأ «❌ مفتاح غير صالح» لأن المزوّد العام معطوب، والعكس:
    يقرأ «✅ يعمل» ومفتاحه لا يعمل. الآن يُقاس المزوّد والمفتاح والموديل
    الذين سيُستخدمون فعلًا.
    """
    if not is_configured(provider=provider, api_key=api_key):
        return False, status_text(provider)
    try:
        reply = await complete(
            "قل كلمة: جاهز", max_tokens=16, timeout=25.0,
            provider=provider, api_key=api_key, model=model,
        )
        provider_obj = get_provider(provider)
        shown_model = model or _model()
        return True, f"✅ يعمل — {provider_obj.label} · {shown_model}\nردّ النموذج: {reply[:60]}"
    except AIError as exc:
        return False, f"❌ {exc}"


# تسجيل الوكلاء المعرَّفين في `.env` بمجرّد استيراد الطبقة — فيعملون في كل
# الميزات (الشرح، التلخيص، الامتحانات) بلا أي كود خاص بهم.
try:      # pragma: no cover - يحدث عند الاستيراد فقط
    register_agents()
except Exception as exc:      # لا يجوز أن يمنع وكيلٌ معطوب إقلاع البوت
    log.warning("تعذّر تسجيل الوكلاء: %s", type(exc).__name__)
