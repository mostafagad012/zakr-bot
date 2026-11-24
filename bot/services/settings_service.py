"""قراءة إعدادات البوت الحية من قاعدة البيانات (مع تراجع إلى .env).

المشكلة التي يحلّها هذا الموديول: لوحة الإدارة تحفظ في جدول bot_settings،
بينما كان الكود يقرأ من .env فقط — فتغيير الإعداد من اللوحة لا يفعل شيئًا.
القاعدة الآن: **اللوحة تتحكم، و.env هو القيمة الافتراضية**.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings as env_settings
from ..models import BotSetting

BOOL_KEYS = {"require_review", "maintenance"}
INT_KEYS: set[str] = set()
TEXT_KEYS = {"bot_name", "welcome_message"}

DEFAULTS: dict[str, str] = {
    "require_review": "true" if env_settings.require_review else "false",
    "maintenance": "false",
    "bot_name": "ذاكر - مساعدك الدراسى",
    "welcome_message": "",
}


async def get_raw(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(select(BotSetting.value).where(BotSetting.key == key))


async def get(session: AsyncSession, key: str, default: str | None = None) -> str:
    value = await get_raw(session, key)
    if value is None or value == "":
        return default if default is not None else DEFAULTS.get(key, "")
    return value


async def get_int(session: AsyncSession, key: str, default: int = 0) -> int:
    raw = await get(session, key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


async def get_bool(session: AsyncSession, key: str, default: bool = False) -> bool:
    raw = (await get(session, key)).strip().lower()
    if raw in {"1", "true", "yes", "on", "نعم", "مفعل", "مفعّل"}:
        return True
    if raw in {"0", "false", "no", "off", "لا", "معطل", "معطّل"}:
        return False
    return default


async def set(session: AsyncSession, key: str, value: str) -> None:
    """يحفظ إعدادًا (لا يعمل commit)."""
    row = await session.get(BotSetting, key)
    if row is None:
        session.add(BotSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def get_effective(session: AsyncSession) -> dict:
    """كل الإعدادات الفعّالة دفعة واحدة — للعرض في لوحة الإدارة."""
    return {
        "require_review": await get_bool(session, "require_review", env_settings.require_review),
        "maintenance": await get_bool(session, "maintenance", False),
        "bot_name": await get(session, "bot_name", "ذاكر - مساعدك الدراسى"),
        "welcome_message": await get(session, "welcome_message", ""),
    }
