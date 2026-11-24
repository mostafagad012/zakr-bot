"""البيانات الافتراضية عند أول تشغيل."""
from __future__ import annotations

import logging

from sqlalchemy import select

from .database import get_session
from .models import (
    DEFAULT_CATEGORIES,
    DEFAULT_LEVELS,
    DEFAULT_SEMESTERS,
    BotSetting,
    FileCategory,
    Level,
    Semester,
)

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "bot_name": "ذاكر - مساعدك الدراسى",
    "welcome_message": (
        "أهلًا بك في ذاكر - مساعدك الدراسى 📚\n"
        "اضغط 📋 القائمة للتنقّل بين الأقسام والمواد والملفات."
    ),
    "require_review": "true",
    "maintenance": "false",
}


async def seed_defaults() -> None:
    """يضيف الفئات والمستويات والفصول والإعدادات إن لم تكن موجودة."""
    async with get_session() as s:
        for name, icon, order in DEFAULT_CATEGORIES:
            exists = await s.scalar(select(FileCategory.id).where(FileCategory.name == name))
            if not exists:
                s.add(FileCategory(name=name, icon=icon, sort_order=order))

        for i, name in enumerate(DEFAULT_LEVELS, start=1):
            exists = await s.scalar(select(Level.id).where(Level.name == name))
            if not exists:
                s.add(Level(name=name, sort_order=i))

        for i, name in enumerate(DEFAULT_SEMESTERS, start=1):
            exists = await s.scalar(select(Semester.id).where(Semester.name == name))
            if not exists:
                s.add(Semester(name=name, sort_order=i))

        for key, value in DEFAULT_SETTINGS.items():
            exists = await s.scalar(select(BotSetting.key).where(BotSetting.key == key))
            if not exists:
                s.add(BotSetting(key=key, value=value))

        await s.commit()
    log.info("تم التأكد من البيانات الافتراضية")
