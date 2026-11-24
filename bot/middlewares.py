"""Middlewares: تسجيل المستخدم، الحظر، والحد من الإغراق."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .config import settings
from .database import get_session
from .models import User
from .services import settings_service, users as users_service

log = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """يمنع إغراق البوت بالرسائل.

    حدّان: حد لحظي (ثانية) وحد دقيق (دقيقة). المشرفون والأدمن مستثنون.
    """

    # سقف عدد المستخدمين المتتبَّعين في وقت واحد. بدونه كان القاموس ينمو
    # **بلا حدّ** بعدد كل من راسل البوت يومًا ما: بوت يعمل أسابيع على لوحة
    # بذاكرة محدودة (Wispbyte) يصل للحد ويُقتل بـ OOM. عند تجاوز السقف
    # تُفرَّغ أقدم النوافذ — وهي أصلًا خاملة (لا رسائل داخل الدقيقة).
    MAX_TRACKED_USERS = 5000

    def __init__(self, per_second: int = 3, per_minute: int = 60):
        self.per_second = per_second
        self.per_minute = per_minute
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._warned: dict[int, float] = {}

    def _evict_if_needed(self, now: float) -> None:
        """يُفرّغ النوافذ التي انتهت صلاحيتها، ويُقبل السقف عند الضرورة.

        التنظيف الدوري رخيص (لا يحدث إلا عند بلوغ السقف) ويُبقي الذاكرة ثابتة
        مهما طال عمر العملية.
        """
        if len(self._hits) < self.MAX_TRACKED_USERS:
            return
        # ① كل نافذة لم تُسجّل رسالة خلال آخر دقيقة صارت بلا فائدة.
        stale = [uid for uid, w in self._hits.items() if not w or now - w[-1] > 60]
        for uid in stale:
            self._hits.pop(uid, None)
            self._warned.pop(uid, None)
        # ② لا يزال فوق السقف؟ نُسقط الأقدم تنبيهًا (FIFO) حتى ينخفض.
        if len(self._hits) >= self.MAX_TRACKED_USERS:
            overflow = len(self._hits) - self.MAX_TRACKED_USERS + 1
            oldest = sorted(self._warned, key=self._warned.get)[:overflow]
            for uid in oldest:
                self._warned.pop(uid, None)
                self._hits.pop(uid, None)
            if not oldest:                       # احتياط: لا مفاتيح للترتيب
                for uid in list(self._hits)[:overflow]:
                    self._hits.pop(uid, None)

    def is_limited(self, user_id: int, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        self._evict_if_needed(now)
        window = self._hits[user_id]
        while window and now - window[0] > 60:
            window.popleft()

        last_second = sum(1 for t in window if now - t <= 1.0)
        if last_second >= self.per_second or len(window) >= self.per_minute:
            return True

        window.append(now)
        return False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        db_user: User | None = data.get("db_user")
        if db_user is not None and db_user.level >= 1:
            return await handler(event, data)  # المشرفون مستثنون

        if self.is_limited(tg_user.id):
            now = time.monotonic()
            # نُنبّه مرة كل 30 ثانية حتى لا نملأ المحادثة
            if now - self._warned.get(tg_user.id, 0) > 30:
                self._warned[tg_user.id] = now
                if isinstance(event, Message):
                    await event.answer("⏳ أرسلت رسائل كثيرة بسرعة. انتظر لحظة.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⏳ تمهّل قليلًا", show_alert=False)
            return None

        return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """يفتح جلسة قاعدة بيانات، يسجّل/يجلب المستخدم، ويضعه في data['db_user']."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        async with get_session() as session:
            user, created = await users_service.get_or_register(
                session,
                tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                owner_id=settings.owner_id,
            )
            await session.commit()

            if user.is_banned:
                if isinstance(event, Message):
                    await event.answer("⛔ حسابك محظور في هذا البوت. تواصل مع الإدارة.")
                return None

            data["db_user"] = user
            data["db_session"] = session

            if created and isinstance(event, Message):
                log.info("مستخدم جديد: %s (%s)", user.display_name, user.telegram_id)

            # وضع الصيانة: المشرفون والأدمن فقط
            if user.level < 1 and await settings_service.get_bool(session, "maintenance", False):
                if isinstance(event, Message):
                    await event.answer("🔧 البوت في وضع الصيانة حاليًا. عُد بعد قليل.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🔧 وضع الصيانة", show_alert=False)
                return None

            return await handler(event, data)
