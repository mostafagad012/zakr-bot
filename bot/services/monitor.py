"""مراقبة: وقت التشغيل، تقرير صحة دوري، وكشف المشاكل المعروفة."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from . import files as files_service
from . import ocr as ocr_service
from . import store
from . import users as users_service

log = logging.getLogger(__name__)

STARTED_AT = time.time()


def uptime_seconds() -> float:
    return time.time() - STARTED_AT


def human_uptime(seconds: float | None = None) -> str:
    total = int(seconds if seconds is not None else uptime_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if minutes:
        parts.append(f"{minutes} دقيقة")
    if not parts:
        parts.append(f"{secs} ثانية")
    return " و ".join(parts[:3])


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    lines: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


async def health_report(session: AsyncSession) -> HealthReport:
    """يفحص المكونات الحرجة ويرجع تقريرًا مقروءًا."""
    lines: list[str] = []
    issues: list[str] = []

    # 1) التخزين
    try:
        storage_ok, storage_msg = await store.get_backend().healthcheck()
    except Exception as exc:
        storage_ok, storage_msg = False, f"التخزين غير مهيّأ: {exc}"
    lines.append(("✅ " if storage_ok else "❌ ") + f"التخزين: {storage_msg}")
    if not storage_ok:
        issues.append("قناة التخزين غير قابلة للكتابة — رفع الملفات سيتوقف")

    # 2) قاعدة البيانات
    try:
        stats = await files_service.library_stats(session)
        lines.append(
            f"✅ قاعدة البيانات: {stats['files_total']} ملف / {stats['users']} مستخدم / "
            f"{stats['subjects']} مادة"
        )
    except Exception as exc:
        lines.append(f"❌ قاعدة البيانات: {exc}")
        issues.append("قاعدة البيانات لا تستجيب")
        stats = None

    # 3) طابور المراجعة
    if stats is not None and stats["files_pending"] > 0:
        lines.append(f"⏳ بانتظار المراجعة: {stats['files_pending']} ملف")
        issues.append(f"{stats['files_pending']} ملف بانتظار المراجعة")
    elif stats is not None:
        lines.append("✅ لا توجد ملفات معلّقة")

    # 4) مساحة القرص لقاعدة البيانات
    if settings.sqlite_path is not None and settings.sqlite_path.exists():
        size_mb = settings.sqlite_path.stat().st_size / (1024 * 1024)
        lines.append(f"✅ حجم قاعدة البيانات: {size_mb:.1f} MB")
        if size_mb > 500:
            issues.append("قاعدة البيانات كبيرة — فكّر في الانتقال إلى PostgreSQL")

    # 5) OCR
    lines.append(("✅ " if ocr_service.is_available() else "⚠️ ") + f"OCR: {'متاح' if ocr_service.is_available() else 'غير مثبّت (الملفات المصوّرة لن تُفهرَس)'}")

    # 5.5) البحث الدلالي العصبي
    from . import embeddings as embeddings_service
    from . import semantic as semantic_service

    neural, total_vectors = await semantic_service.embedding_coverage(session)
    if embeddings_service.is_available():
        lines.append(f"✅ بحث دلالي عصبي: {neural}/{total_vectors} ملف مُضمَّن")
    else:
        lines.append("ℹ️ بحث دلالي: TF-IDF (فعّل EMBEDDING_MODEL للنموذج العصبي)")

    # 6) النشاط
    active = await users_service.active_last_days(session, 7)
    lines.append(f"✅ نشطون آخر 7 أيام: {active}")

    # 7) الإعدادات
    lines.append(f"✅ وقت التشغيل: {human_uptime()}")
    lines.append(f"✅ المراجعة الإلزامية: {'مفعّلة' if settings.require_review else 'معطّلة'}")

    return HealthReport(ok=not issues, lines=lines, issues=issues)


async def stats_loop(interval_minutes: int = 60) -> None:
    """يسجّل إحصائية دورية في السجل — للمتابعة عبر journalctl."""
    from ..database import get_session

    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            async with get_session() as session:
                stats = await files_service.library_stats(session)
                log.info(
                    "إحصائية دورية | ملفات=%s منشور=%s معلّق=%s تحميلات=%s مستخدمون=%s",
                    stats["files_total"], stats["files_approved"], stats["files_pending"],
                    stats["downloads"], stats["users"],
                )
        except Exception as exc:
            log.error("فشل جمع الإحصائية الدورية: %s", exc)
