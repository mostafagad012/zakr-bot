"""إعدادات الذكاء الاصطناعي لكل مستخدم: اختيار المزوّد والموديل والمفاتيح.

**مبدأ الخصوصية الحاكم**: مفتاح المستخدم سرّه. لذلك:
* لا يُسجَّل في أي سجل، ولا يظهر في رسالة خطأ.
* لا يُعرض كاملًا أبدًا — تُعرض أول 4 وآخر 4 أحرف فقط (`mask_key`).
* يُخزَّن مشفّرًا بمفتاح مشتق من `BOT_TOKEN` (`_encrypt`) حتى لا يكفي
  تسريب ملف قاعدة البيانات وحده لاستخدام المفاتيح.
* يُحذف نهائيًا عند طلب المستخدم (`forget_key`).

**التشفير هنا ليس بديلًا عن أمان الخادم** — هو طبقة إضافية: من يقرأ
الملف مباشرة لا يجد المفاتيح صالحة. من يملك الخادم والتوكن معًا يستطيع
فكّها، وهذا حدّ مقبول ومعلن في التوثيق.

وظيفة هذا الموديول الثانية: **تطبيق قرار المستخدم على الطبقة العامة**
(`ai.settings`)، فتسري الميزات كلها بمزوّده المختار بلا تعديل في كل
موضع نداء.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import UserAiSetting
from . import ai

log = logging.getLogger(__name__)

MIN_KEY_LEN = 8
MAX_KEY_LEN = 400
KEY_LINE = "•" * 5


# ------------------------------------------------------------- التشفير
def _cipher_key() -> bytes:
    """مفتاح تشفير مشتق من توكن البوت — ثابت داخل نفس النشر."""
    seed = (settings.bot_token or "zakr-local-dev").encode()
    return hashlib.sha256(b"zakr-ai-keys::" + seed).digest()


def _encrypt(plain: str) -> str:
    """تشفير متناظر مبسّط (XOR + base64) — كافٍ لإخفاء المفتاح في الملف.

    ملاحظة صريحة: هذا **إخفاء قوي لا تشفير مؤسسي**. الغرض منع قراءة
    المفاتيح من نسخة قاعدة بيانات مسروقة، لا مقاومة من يملك الخادم.
    """
    if not plain:
        return ""
    key = _cipher_key()
    raw = plain.encode("utf-8")
    masked = bytes(byte ^ key[i % len(key)] for i, byte in enumerate(raw))
    # HMAC للتحقق من السلامة عند الفكّ
    tag = hmac.new(key, raw, hashlib.sha256).digest()[:8]
    return base64.urlsafe_b64encode(tag + masked).decode("ascii")


def _decrypt(token: str) -> str:
    """فكّ التشفير مع التحقق من السلامة. يُرجع "" عند أي تلف."""
    if not token:
        return ""
    try:
        data = base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return ""
    if len(data) <= 8:
        return ""
    tag, masked = data[:8], data[8:]
    key = _cipher_key()
    raw = bytes(byte ^ key[i % len(key)] for i, byte in enumerate(masked))
    expected = hmac.new(key, raw, hashlib.sha256).digest()[:8]
    if not hmac.compare_digest(tag, expected):
        return ""      # بيانات تالفة أو مشفّرة بتوكن آخر
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def mask_key(plain: str) -> str:
    """عرض آمن للمفتاح: أول 4 وآخر 4 أحرف فقط."""
    key = (plain or "").strip()
    if not key:
        return ""
    if len(key) <= 10:
        return KEY_LINE
    return f"{key[:4]}{KEY_LINE}{key[-4:]}"


# ------------------------------------------------------------- التخزين
async def get_row(session: AsyncSession, user_id: int) -> UserAiSetting | None:
    return await session.scalar(
        select(UserAiSetting).where(UserAiSetting.user_id == user_id)
    )


async def get_or_create(session: AsyncSession, user_id: int) -> UserAiSetting:
    row = await get_row(session, user_id)
    if row is not None:
        return row
    row = UserAiSetting(user_id=user_id)
    session.add(row)
    await session.flush()
    return row


def _load_keys(row: UserAiSetting) -> dict[str, str]:
    """يحوّل حقل JSON إلى قاموس مزوّد ← مفتاح **مفكوك**."""
    try:
        stored = json.loads(row.keys_json or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {
        str(name): _decrypt(str(token))
        for name, token in stored.items()
    }


def _save_keys(row: UserAiSetting, keys: dict[str, str]) -> None:
    row.keys_json = json.dumps(
        {name: _encrypt(key) for name, key in keys.items() if key},
        ensure_ascii=False,
    )


def plain_keys(row: UserAiSetting | None) -> dict[str, str]:
    """المفاتيح المحفوظة مفكوكة — **للتطبيق فقط، لا للعرض**.

    لماذا موجودة؟ لأن المفتاح المحفوظ في القاعدة كان يُشفَّر ويُخفى ويُحذف
    … ثم **لا يُستخدم أبدًا**: `ai.key_for` يقرأ من `.env` وحده. فكان الأدمن
    يحفظ مفتاحًا، تراه الشاشة وتطمئن، ثم يفشل الطلب بـ 401 بلا سبب مفهوم.
    هذه الدالة تُغلق تلك الفجوة، ولا تُنادى إلا من مسار واحد صريح
    (`promote_to_env`) أو عند فحص «هل عندنا مفتاح؟».
    """
    if row is None:
        return {}
    return {name: key for name, key in _load_keys(row).items() if key}


def has_usable_key(provider: str) -> bool:
    """هل سيرسل البوت مفتاحًا فعلًا لهذا المزوّد؟

    **لا** يكفي وجود مفتاح مشفّر في القاعدة: طبقة الطلب تقرأ `.env` والبيئة
    فقط، فالمفتاح المخزَّن يُصبح فعّالًا بعد نقله صريحًا (`ai:promote`).
    وخلط المعنيين كان يجعل الشاشة تقول «عندك مفتاح» ثم يفشل الطلب بـ 401.
    """
    if provider not in ai.PROVIDERS:
        return False
    return bool(ai.key_for(provider))


async def set_key(session: AsyncSession, user_id: int, provider: str, key: str) -> bool:
    """يحفظ مفتاح مزوّد. يُرجع False إن كان المفتاح غير صالح الشكل."""
    provider = (provider or "").strip().lower()
    if provider not in ai.PROVIDERS:
        raise ValueError("مزوّد غير معروف")
    key = (key or "").strip()
    if not MIN_KEY_LEN <= len(key) <= MAX_KEY_LEN:
        return False
    if any(ch.isspace() for ch in key):
        return False

    row = await get_or_create(session, user_id)
    keys = _load_keys(row)
    keys[provider] = key
    _save_keys(row, keys)
    await session.flush()
    return True


async def saved_providers(session: AsyncSession, user_id: int) -> list[str]:
    """المزوّدون الذين حفظ المستخدم مفاتيحهم."""
    row = await get_row(session, user_id)
    if row is None:
        return []
    return [name for name, key in _load_keys(row).items() if key]


async def get_masked_keys(session: AsyncSession, user_id: int) -> dict[str, str]:
    """عرض آمن لكل مفتاح محفوظ (لا يُكشف الأصل أبدًا)."""
    row = await get_row(session, user_id)
    if row is None:
        return {}
    return {name: mask_key(key) for name, key in _load_keys(row).items() if key}


async def forget_key(session: AsyncSession, user_id: int, provider: str) -> bool:
    row = await get_row(session, user_id)
    if row is None:
        return False
    keys = _load_keys(row)
    if provider not in keys:
        return False
    keys.pop(provider)
    _save_keys(row, keys)
    if row.provider == provider:
        row.provider = ""
    await session.flush()
    return True


async def clear_all(session: AsyncSession, user_id: int) -> None:
    """يحذف كل المفاتيح والتفضيلات — لطلب «امسح بياناتي»."""
    row = await get_row(session, user_id)
    if row is None:
        return
    row.keys_json = "{}"
    row.provider = ""
    row.model = ""
    row.notes = ""
    await session.flush()


# ------------------------------------------------------- التفضيلات
async def set_provider(session: AsyncSession, user_id: int,
                       provider: str, model: str = "") -> None:
    if provider not in ai.PROVIDERS:
        raise ValueError("مزوّد غير معروف")
    row = await get_or_create(session, user_id)
    row.provider = provider
    row.model = (model or "").strip()[:120]
    row.auto_provider = False
    await session.flush()


async def set_notes(session: AsyncSession, user_id: int, notes: str) -> None:
    """ملاحظات المستخدم لتخصيص الشرح (نص حر، بحدّ طول)."""
    row = await get_or_create(session, user_id)
    row.notes = (notes or "").strip()[:800]
    await session.flush()


async def use_auto(session: AsyncSession, user_id: int) -> None:
    """يرجع للاختيار التلقائي (من `.env`)."""
    row = await get_or_create(session, user_id)
    row.provider = ""
    row.model = ""
    row.auto_provider = True
    await session.flush()


# --------------------------------------------------- التطبيق على الطبقة
def env_provider() -> str:
    """المزوّد الفعّال من `.env` (أو auto). هذه هي الحقيقة الوحيدة الآن."""
    from ..config import settings as live

    return (live.ai_provider or "auto").strip().lower() or "auto"


def has_env_key(provider: str) -> bool:
    """هل يوجد مفتاح لهذا المزوّد في `.env` أو البيئة؟"""
    from . import ai as ai_layer

    return bool(ai_layer.key_for(provider))


async def status(db_session: AsyncSession, user_id: int | None = None) -> dict:
    """حالة الإعداد كما يراها الأدمن — بلا أي تبديل للطبقة العامة.

    **لماذا لا نبدّل `ai.settings` بعد الآن؟** لأن الإعداد صار واحدًا للجميع
    في `.env`، وتبديل كائن عالمي لكل مستخدم كان يجعل اختيار أدمن واحد
    يظهر لطالب آخر في نفس العملية. الآن نقرأ ولا نكتب.
    """
    from ..config import settings as live

    provider = env_provider()
    row = await get_row(db_session, user_id) if user_id else None
    return {
        "provider": provider,
        "label": (ai.PROVIDERS[provider].label if provider in ai.PROVIDERS
                  else provider),
        "model": (live.ai_model or "").strip(),
        "configured": bool(live.ai_api_key or has_env_key(provider)),
        "saved_keys": list(row.keys_json and _load_keys(row) or {}) if row else [],
    }


def active_label(_legacy_row: UserAiSetting | None = None) -> str:
    """وصف مختصر للإعداد الفعّال — من `.env` لا من صف المستخدم."""
    from ..config import settings as live

    provider = env_provider()
    label = ai.PROVIDERS[provider].label if provider in ai.PROVIDERS else provider
    model = (live.ai_model or "").strip()
    if model:
        label += f" · {model}"
    return label
