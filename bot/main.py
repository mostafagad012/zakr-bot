"""نقطة الإقلاع: تهيئة البوت وقاعدة البيانات والتخزين ثم بدء الاستقبال."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import settings
from .database import dispose_engine, ensure_schema, init_engine
from .handlers import (
    about, admin, ai_settings, exam, explain_material, learner, library,
    materials, quiz, search_handler, start, translate, tutor, upload,
)
from .middlewares import RateLimitMiddleware, UserMiddleware
from .services import monitor, store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("bot")

# ⚠️ الترتيب حاسم: `search_handler` فيه معالج نص حر (بديل `/search`)،
# فيجب أن يأتي **بعد** كل تدفّقات FSM حتى لا يسرق نصًا يخصّها. والتدفّقات
# نفسها محمية بشرط الحالة داخلها، وهذا حزام أمان ثانٍ.
ROUTER_ORDER = [
    start.router,     # /start والقائمة
    about.router,
    admin.router,
    upload.router,
    quiz.router,
    exam.router,      # الامتحانات المولّدة بأزرار
    learner.router,   # حسابي/تحليلي/تخصيص الأسلوب
    tutor.router,     # الشرح والتلخيص الذكي
    explain_material.router,  # الشرح من مادة (PDF/مكتبة) + برومبت حر
    materials.router,  # إدارة مواد المستخدم (تعديل/حذف)
    ai_settings.router,  # اختيار مزوّد الذكاء الاصطناعي
    translate.router,
    library.router,   # معالجات التنقّل العامة
    search_handler.router,  # أخيرًا: النص الحر = بحث تلقائي
]


def build_bot() -> Bot:
    """ينشئ كائن البوت، مع دعم سيرفر Bot API محلي للملفات الكبيرة."""
    kwargs: dict = {}
    if settings.bot_api_base:
        from aiogram.client.session.aiohttp import AiohttpSession

        kwargs["session"] = AiohttpSession(base_url=settings.bot_api_base)
        log.info("استخدام سيرفر Bot API محلي: %s", settings.bot_api_base)

    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        **kwargs,
    )


def build_dispatcher(bot: Bot) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UserMiddleware())
    dp.update.middleware(RateLimitMiddleware(per_second=3, per_minute=60))

    @dp.errors()
    async def _on_unhandled_error(event) -> bool:
        """شبكة أمان أخيرة: خطأ في أي معالج يُسجَّل ولا يُسقط الاستقبال،
        ونُطلق سراح زر المستخدم العالق بدل دوّار تحميل أبدي."""
        log.exception("خطأ غير معالج في تحديث: %s", event.exception)
        if event.update.callback_query:
            try:
                await event.update.callback_query.answer(
                    "⚠️ حدث خطأ — حاول مجددًا", show_alert=False)
            except TelegramBadRequest as exc:
                # الزر صار قديمًا/محذوفًا — لا مجال لإبلاغ المستخدم،
                # والخطأ الأصلي مسجَّل بالفعل في السطر أعلاه.
                log.debug("تعذّر إطلاق الزر العالق: %s", exc)
        return True

    for router in ROUTER_ORDER:
        dp.include_router(router)
    return dp


async def startup(bot: Bot) -> None:
    init_engine(settings.database_url)
    await ensure_schema()
    log.info("قاعدة البيانات جاهزة: %s", settings.database_url)

    store.init_telegram_backend(bot, settings.storage_channel)
    ok, message = await store.get_backend().healthcheck()
    log.info("فحص التخزين: %s — %s", "نجح" if ok else "فشل", message)
    if not ok:
        log.warning("⚠️ تخزين الملفات لن يعمل حتى تُحل مشكلة قناة التخزين.")


HEARTBEAT_FILE = "data/.heartbeat"


async def heartbeat_loop(interval: int = 60) -> None:
    """يكتب طابعًا زمنيًا كل دقيقة — نبضة حياة يقرأها فحص الصحة.

    **لماذا؟** «العملية شغّالة» ليست دليلًا على أن البوت يعمل: قد تكون
    عالقة في انتظار شبكة أو في حلقة لا تنتهي. النبضة تُكتب من مهمة بدأت
    **بعد** نجاح الإقلاع، وفحص الصحة في الصورة (Docker HEALTHCHECK) يقرأ
    عمرها ويُعيد التشغيل إن تجمّدت — بدل خدمة «حيّة» لا ترد على أحد.
    """
    from pathlib import Path

    path = Path(HEARTBEAT_FILE)
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(asyncio.get_running_loop().time()), encoding="utf-8")
            path.touch()
        except OSError as exc:            # قرص ممتلئ مثلًا — لا نُسقط البوت
            log.warning("تعذّر كتابة نبضة الحياة: %s", type(exc).__name__)
        await asyncio.sleep(interval)


async def backup_loop() -> None:
    """نسخة احتياطية دورية (كل 12 ساعة) مع تدوير النسخ."""
    from .services import backup

    if not settings.is_sqlite or settings.sqlite_path is None:
        log.info("النسخ الاحتياطي التلقائي متاح لـ SQLite فقط — تم تجاوزه")
        return

    while True:
        await asyncio.sleep(12 * 3600)
        try:
            path = backup.sqlite_backup(
                settings.sqlite_path, settings.backup_dir, settings.backup_keep
            )
            log.info("نسخة احتياطية دورية: %s", path)
        except Exception as exc:
            log.error("فشل النسخ الاحتياطي الدوري: %s", exc)


async def main() -> int:
    if not settings.bot_token or settings.bot_token.startswith("123456789:AAExample"):
        log.error("BOT_TOKEN غير مُعدّ. انسخ .env.example إلى .env واملأ BOT_TOKEN")
        return 1
    if not settings.owner_id:
        log.error("OWNER_ID غير مُعدّ — لن يكون هناك مالك للبوت")
        return 1
    if not settings.storage_channel:
        log.error("STORAGE_CHANNEL غير مُعدّ — تخزين الملفات لن يعمل")
        return 1

    # شكل التوكن قبل بناء الكائن: `Bot()` يرمي TokenValidationError برسالة
    # إنجليزية وتتبّع طويل — على لوحة استضافة بلا طرفية هذا يُقرأ كعطل غامض
    # بينما السبب سطر واحد: نسخة ناقصة من @BotFather.
    import re

    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", settings.bot_token):
        log.error("✖️ BOT_TOKEN لا يشبه توكن تيليجرام (الصيغة: أرقام:٤٠ حرفًا تقريبًا).\n"
                  "   انسخه كاملًا من @BotFather بلا مسافات ولا أسطر مقطوعة.")
        return 1

    try:
        bot = build_bot()
    except Exception as exc:                      # noqa: BLE001
        log.error("✖️ تعذّر بناء البوت (%s): %s", type(exc).__name__, exc)
        return 1
    dp = build_dispatcher(bot)

    try:
        await startup(bot)
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            # أشهر خطأ في أول تشغيل: توكن منسوخ ناقصًا. بلا هذا الالتقاط
            # يرى المستخدم تتبّع بايثون طويلًا بدل سبب واحد واضح.
            log.error("✖️ تيليجرام رفض BOT_TOKEN (Unauthorized).\n"
                      "   راجع التوكن من @BotFather — لا مسافات، ولا سطر ناقص.")
            return 1
        except Exception as exc:      # شبكة/مهلة عند الإقلاع
            log.error("✖️ تعذّر الاتصال بتيليجرام (%s).\n"
                      "   راجع اتصال الإنترنت — البوت سيحاول عند التشغيل التالي.",
                      type(exc).__name__)
            return 1
        log.info("البوت @%s يعمل (المالك: %s)", me.username, settings.owner_id)

        background = [
            asyncio.create_task(backup_loop()),
            asyncio.create_task(monitor.stats_loop(interval_minutes=60)),
            asyncio.create_task(heartbeat_loop()),
        ]
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        finally:
            for task in background:
                task.cancel()
    finally:
        # الإغلاق بترتيب معاكس للفتح: العمليات الخلفية ⇒ الاتصالات ⇒ البوت
        await dispose_engine()
        await bot.session.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit):
        log.info("تم إيقاف البوت")
