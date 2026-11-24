"""نقطة الإقلاع: تهيئة البوت وقاعدة البيانات والتخزين ثم بدء الاستقبال."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

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


def _quarantine_broken_sqlite(reason: str) -> bool:
    """ينقل ملف SQLite التالف جانبًا ليبني البوت قاعدة جديدة.

    ⚠️ العِلّة التي أُصلحت: ملف قاعدة تالف (انقطاع كهرباء، أو قتل بـSIGKILL في
    منتصف كتابة WAL — شائع على لوحات تعيد التشغيل كثيرًا) كان يُسقط البوت
    بـ`DatabaseError: file is not a database` إنجليزي غامض، **بلا أي محاولة
    إنقاذ**. البوت يبقى ميتًا في كل إعادة تشغيل حتى يتدخّل إنسان.
    الآن: يُنقل الملف إلى `data/broken-<time>.db` (لا يُحذف — قد يكون قابلاً
    للاسترجاع بأدوات SQLite)، ويُبنى ملف جديد نظيف، والبوت يقلع.
    """
    if not settings.is_sqlite or settings.sqlite_path is None:
        return False
    path = Path(settings.sqlite_path)
    if not path.exists():
        return False
    import time as _time

    stamp = _time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}-broken-{stamp}{path.suffix}")
    try:
        path.replace(target)
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                side.replace(side.with_name(side.name + f".broken-{stamp}"))
    except OSError as exc:
        log.error("تعذّر نقل القاعدة التالفة (%s) — راجع المساحة والصلاحيات: %s",
                  type(exc).__name__, exc)
        return False
    log.error(
        "✖ قاعدة البيانات كانت تالفة (%s).\n"
        "   نُقلت إلى: %s (لم تُحذف — قد تُستعاد بأدوات SQLite)\n"
        "   وأُنشئت قاعدة جديدة فارغة. البيانات السابقة لم تُفقد من الملف المحفوظ.",
        reason, target.name)
    return True


async def startup(bot: Bot) -> None:
    init_engine(settings.database_url)
    try:
        await ensure_schema()
    except Exception as exc:                     # noqa: BLE001
        text = str(exc).lower()
        broken = any(sig in text for sig in (
            "file is not a database", "database disk image is malformed",
            "not a database", "malformed"))
        if not broken or not _quarantine_broken_sqlite(type(exc).__name__):
            raise
        # إعادة التهيئة من الصفر بعد نقل التالف
        await dispose_engine()
        init_engine(settings.database_url)
        await ensure_schema()
    log.info("قاعدة البيانات جاهزة: %s", settings.database_url)

    store.init_telegram_backend(bot, settings.storage_channel)
    ok, message = await store.get_backend().healthcheck()
    log.info("فحص التخزين: %s — %s", "نجح" if ok else "فشل", message)
    if not ok:
        log.warning("⚠️ تخزين الملفات لن يعمل حتى تُحل مشكلة قناة التخزين.")


def _panel_memory_mb() -> int | None:
    """حدّ الذاكرة الذي تفرضه اللوحة (Wispbyte/Pterodactyl) بالميجابايت."""
    for name in ("SERVER_MEMORY", "MEMORY_LIMIT", "ZAKR_MEMORY_MB"):
        raw = os.environ.get(name, "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    return None


def _current_rss_mb() -> float | None:
    """الذاكرة المقيمة الحالية — من /proc (لينكس) بلا أي حزمة إضافية."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _verify_writable_dir(path: str = "data") -> tuple[bool, str]:
    """يتأكّد أن مجلد البيانات قابل للكتابة **قبل** أي عمل آخر.

    أشهر عطل على لوحات الاستضافة (Wispbyte وحاويات Docker المقفلة): الجذر
    للقراءة فقط. بلا فحص مبكر يظهر العطل لاحقًا كـ«sqlite3.OperationalError:
    unable to open database file» في منتصف الإقلاع، أو كفشل صامت في حفظ
    الملفات — وكلاهما لا يقول للمستخدم السبب الحقيقي: مسار غير قابل للكتابة.
    """
    base = Path(path)
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, "قابل للكتابة"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


HEARTBEAT_FILE = "data/.heartbeat"


async def heartbeat_loop(interval: int = 60) -> None:
    """يكتب طابعًا زمنيًا كل دقيقة — نبضة حياة يقرأها فحص الصحة.

    **لماذا؟** «العملية شغّالة» ليست دليلًا على أن البوت يعمل: قد تكون
    عالقة في انتظار شبكة أو في حلقة لا تنتهي. النبضة تُكتب من مهمة بدأت
    **بعد** نجاح الإقلاع، وفحص الصحة في الصورة (Docker HEALTHCHECK) يقرأ
    عمرها ويُعيد التشغيل إن تجمّدت — بدل خدمة «حيّة» لا ترد على أحد.
    """
    path = Path(HEARTBEAT_FILE)
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(asyncio.get_running_loop().time()), encoding="utf-8")
            path.touch()
        except OSError as exc:            # قرص ممتلئ مثلًا — لا نُسقط البوت
            log.warning("تعذّر كتابة نبضة الحياة: %s", type(exc).__name__)
        await asyncio.sleep(interval)


# ───────────────────────────── الإيقاف اللطيف (Wispbyte)
#  ⚠️ عِلّة حقيقية على لوحات الاستضافة: Wispbyte (وغيرها) تُرسل **SIGTERM**
#  عند كل إعادة تشغيل أو نشر، وتنتظر ~١٠ ثوانٍ ثم SIGKILL. البوت لا يعالج
#  الإشارة إطلاقًا لغياب أي handler، فيُقتل فورًا حتى لو كان:
#      • يكتب في قاعدة البيانات (SQLite قد تُترك في WAL غير مكتمل)
#      • يرفع ملفًا لقناة التخزين (الملف يُرفع ناقصًا)
#      • يُنهي معاملة (تُلغى بلا تراجع)
#  النتيجة المقيسة عند المستخدم: ملفات تالفة و«البوت وقف فجأة» مع كل نشر.
#  هذا المعالج يعطي الإغلاق **٥ ثوانٍ كحد أقصى** ليكمل ما في يده.
# ⚠️ لا تُنشئ `asyncio.Event()` على مستوى الوحدة: بايثون ٣.٩ وما قبلها
# يربطه بالحلقة الحالية وقت الاستيراد، فيتعلّق بأول حلقة تُنشأ ويصبح عديم
# الأثر في الحلقة التي يعمل فيها البوت فعلًا (ومنه تنشأ حالات «الإيقاف لا يعمل»
# و«الاختبارات تتخطّى») — الإنشاء الكسول داخل الحلقة يزيل الاحتمال كله.
_STOP: "asyncio.Event | None" = None
_STOP_SIGNAL: str = ""


def _stop_event() -> asyncio.Event:
    """يُرجع حدث الإيقاف المرتبط بالحلقة الجارية (يُنشأ عند أول طلب)."""
    global _STOP
    if _STOP is None:
        _STOP = asyncio.Event()
    return _STOP


def _install_signal_handlers() -> None:
    """يحوّل SIGTERM/SIGINT إلى إيقاف لطيف بدل القتل المفاجئ.

    لا يفشل الإقلاع إن تعذّر: `add_signal_handler` غير مدعوم على ويندوز،
    وبعض اللوحات تشغّل البوت في بيئة لا تسمح بتسجيل الإشارات.
    """
    global _STOP_SIGNAL

    def _request(signame: str) -> None:
        global _STOP_SIGNAL
        if _STOP_SIGNAL:
            return                       # إشارة ثانية = الصبر نفد، تُترك فورًا
        _STOP_SIGNAL = signame
        log.warning("🛑 استُلمت %s — إيقاف لطيف (حتى ٥ ثوانٍ) …", signame)
        _stop_event().set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT", "SIGQUIT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request, signame)
        except (NotImplementedError, RuntimeError, ValueError) as exc:
            log.debug("تعذّر تسجيل %s: %s", signame, type(exc).__name__)


async def _graceful_stop(dp: Dispatcher, background: list) -> None:
    """يوقف الاستقبال وينتظر المهام الجارية — بحدّ زمني لا يتجاوز مهلة اللوحة."""
    await _stop_event().wait()
    try:
        await dp.stop_polling()
    except Exception as exc:            # noqa: BLE001 — لا نُسقط الإغلاق
        log.debug("تعذّر إيقاف الاستقبال: %s", type(exc).__name__)

    if not background:
        return

    # ⚠️ المهام الخلفية كلها **حلقات دائمة** (نسخ احتياطي/إحصاءات/نبضة) —
    # لا تنتهي وحدها أبدًا. انتظارها «لطبيعيًا» كان يعني استهلاك مهلة اللوحة
    # كاملة (٥ ثوانٍ) في كل إيقاف بلا فائدة. الأصحّ: إلغاؤها ثم منحها مهلة
    # قصيرة لتُنفّذ كتل `finally` (إغلاق ملف/إنهاء معاملة) — إغلاق أسرع
    # بنفس الأمان، ولا يقترب من حدّ SIGKILL في Wispbyte.
    grace = 3.0
    for task in background:
        task.cancel()
    log.info("⏳ إيقاف %d مهمة خلفية (مهلة %.0f ثوانٍ للتنظيف) …", len(background), grace)
    done, pending = await asyncio.wait(background, timeout=grace)
    if pending:
        # `cancel()` لا تُنهي المهمة فورًا، بل ترمي CancelledError داخلها
        # لتُنفّذ كتل `finally` ثم تنتهي. العودة قبل انتظارها تعني أن العملية
        # قد تُغلق والقاعدة ما زالت تُنهي معاملة.
        await asyncio.gather(*pending, return_exceptions=True)
        log.warning("لم تنتهِ %d مهمة في المهلة — أُلغيت وأنهت تنظيفها", len(pending))
    else:
        log.info("✔ كل المهام الخلفية أنهت تنظيفها (%d)", len(done))


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


def _missing_settings_text(missing: list[str]) -> str:
    """رسالة واحدة واضحة لكل متغيّر ناقص — بمكان الإصلاح الصحيح للمنصّة.

    ⚠️ العِلّة التي أُصلحت: الرسائل كانت تقول «انسخ .env.example إلى .env»
    وهي **مستحيلة** على Wispbyte وكل اللوحات: لا طرفية ولا محرّر ملفات،
    والمتغيّرات تُضاف في تبويب Variables. إرسال المستخدم إلى الحلّ الخطأ
    يعني بوتًا لا يقلع أبدًا وهو يظنّ أنه فعل المطلوب.
    """
    lines = ["✖ إعداد ناقص — البوت لن يقلع بهذه الحالة:", ""]
    hints = {
        "BOT_TOKEN": "التوكن من @BotFather",
        "OWNER_ID": "آيدي حسابك من @userinfobot",
        "STORAGE_CHANNEL": "آيدي قناتك ويبدأ بـ -100",
    }
    for name in missing:
        lines.append(f"   • {name} — {hints.get(name, '')}")
    if settings.runs_on_panel:
        lines += [
            "",
            "📍 على Wispbyte (وكل لوحة استضافة):",
            "   لوحة التحكم ← تبويب Startup ← Variables ← أضف المتغيّر واحفظ،",
            "   ثم Restart. لا يوجد ملف .env على اللوحة.",
        ]
    else:
        lines += [
            "",
            "📍 على سيرفر/VPS/جهازك:",
            "   cp .env.example .env   ثم املأ القيم داخل .env",
        ]
    return "\n".join(lines)


async def main() -> int:
    # نجمع كل الناقص ونعرضه مرة واحدة — بدل رسالة ثم إعادة تشغيل ثم أخرى
    missing = []
    if not settings.bot_token or settings.bot_token.startswith("123456789:AAExample"):
        missing.append("BOT_TOKEN")
    if not settings.owner_id:
        missing.append("OWNER_ID")
    if not settings.storage_channel:
        missing.append("STORAGE_CHANNEL")
    if missing:
        log.error("%s", _missing_settings_text(missing))
        return 1

    # شكل التوكن قبل بناء الكائن: `Bot()` يرمي TokenValidationError برسالة
    # إنجليزية وتتبّع طويل — على لوحة استضافة بلا طرفية هذا يُقرأ كعطل غامض
    # بينما السبب سطر واحد: نسخة ناقصة من @BotFather.
    import re

    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", settings.bot_token):
        log.error("✖️ BOT_TOKEN لا يشبه توكن تيليجرام (الصيغة: أرقام:٤٠ حرفًا تقريبًا).\n"
                  "   انسخه كاملًا من @BotFather بلا مسافات ولا أسطر مقطوعة.")
        return 1

    try:
        bot = build_bot()
    except Exception as exc:                      # noqa: BLE001
        log.error("✖️ تعذّر بناء البوت (%s): %s", type(exc).__name__, exc)
        return 1
    # فحص مبكر لمسار البيانات: فشله الآن رسالة عربية واضحة، وفشله لاحقًا
    # يظهر كخطأ SQLite غامض في منتصف التشغيل.
    ok, why = _verify_writable_dir("data")
    if not ok:
        log.error("✖️ مجلد data غير قابل للكتابة (%s).\n"
                  "   على Wispbyte/Docker: اجعل مجلد العمل قابلًا للكتابة،\n"
                  "   أو اضبط DATABASE_URL على مسار داخل وحدة تخزين دائمة.", why)
        return 1
    log.debug("مجلد data: %s", why)

    # ── تحذير القرص المؤقت (أخطر عِلّة على Wispbyte)
    #  Wispbyte وكل حاويات Pterodactyl تمسح نظام الملفات عند كل إعادة نشر.
    #  البوت يعمل بـSQLite افتراضيًا ⇒ كل إعادة نشر تمحو المستخدمين والملفات
    #  والإعدادات، ويظهر للمستخدم «المكتبة فَرَغَت» بلا سبب. التحذير لا يمنع
    #  التشغيل (كثيرون يجرّبون بلا قاعدة خارجية) لكنه يقول الحقيقة صراحةً.
    # ── حدّ الذاكرة على اللوحة: تحذير مبكر بدل قتل صامت بـOOM
    #  Wispbyte تضع `SERVER_MEMORY` (بالميجابايت). البوت يستهلك ~١٩٦MB في
    #  السكون (٢٢٣MB ذروة). كان OCR يزيدها على صور كبيرة، وقد أُزيل —
    #  فالرقم اليوم أدقّ. لو الحدّ أقل من ٥١٢MB نخبر المستخدم بالسبب
    #  **قبل** أن تقتله اللوحة بلا أي رسالة.
    mem_mb = _panel_memory_mb()
    if mem_mb is not None:
        used = _current_rss_mb()
        if used is not None:
            log.info("الذاكرة: %.0f MB مستخدمة من %d MB مسموحة", used, mem_mb)
        if mem_mb < 512:
            log.warning(
                "⚠️ حدّ الذاكرة على اللوحة %d MB — أقلّ من المطلوب لتشغيل مريح.\n"
                "   البوت يستهلك ~٢٠٠ MB وحده.\n"
                "   ارفع الحدّ إلى ١ GB — أو أضف ZAKR_MINIMAL=1 لتقليل الحِمل.", mem_mb)

    if settings.sqlite_is_ephemeral:
        log.warning(
            "⚠️ قاعدة البيانات SQLite على قرص **يُمسح** عند كل إعادة نشر.\n"
            "   Wispbyte (وكل حاويات Pterodactyl) لا تحفظ نظام الملفات.\n"
            "   النتيجة: كل إعادة نشر تمحو المستخدمين والملفات والإعدادات.\n"
            "   الحلّ: أضف DATABASE_URL لقاعدة خارجية، مثل:\n"
            "     postgresql+asyncpg://USER:PASS@HOST:5432/DBNAME\n"
            "   (Supabase/Neon مجانًا). أو اربط وحدة تخزين دائمة."
        )

    dp = build_dispatcher(bot)
    _install_signal_handlers()

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

        # نُعلن الجاهزية للمنفذ الصحي (PORT) — كانت اللوحة ترى "ok" كاذبًا.
        try:
            from main import mark_ready

            mark_ready()
        except Exception:            # noqa: BLE001 — نقطة الدخول قد لا تكون main
            pass

        background = [
            asyncio.create_task(backup_loop()),
            asyncio.create_task(monitor.stats_loop(interval_minutes=60)),
            asyncio.create_task(heartbeat_loop()),
        ]
        # المراقب يعمل بالتوازي مع الاستقبال: أيهما ينتهي أولًا يُنهي الآخر.
        # `start_polling` تُعيد التحكّم عند stop_polling أو عند خطأ.
        # ── `allowed_updates` مشتقّة لا مكتوبة بيد
        #  قائمة يدوية تعني: أي معالج جديد على حدث آخر (edited_message،
        #  my_chat_member، inline_query …) يبقى **ميتًا بلا أي خطأ** — تيليجرام
        #  لا ترسله أصلًا. الاشتقاق من الشجرة يجعل الإضافة تعمل تلقائيًا.
        allowed = dp.resolve_used_update_types()
        log.info("الأحداث المطلوبة من تيليجرام: %s", ", ".join(allowed))
        polling = asyncio.create_task(
            dp.start_polling(bot, allowed_updates=allowed)
        )
        stopper = asyncio.create_task(_graceful_stop(dp, background))
        try:
            await asyncio.wait({polling, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (polling, stopper, *background):
                if not task.done():
                    task.cancel()
            await asyncio.gather(polling, stopper, *background, return_exceptions=True)
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
