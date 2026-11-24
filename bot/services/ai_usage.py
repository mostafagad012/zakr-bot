"""استهلاك الذكاء الاصطناعي: سقف يومي لكل مستخدم + إحصاءات للأدمن.

**المشكلة العملية**: كل ضغطة على «🧑‍🏫 اشرح لي» تنفق من رصيد مزوّد حقيقي.
بلا سقف، طالب واحد متحمّس (أو بوت يضغط الأزرار) يستنفد طبقة مجانية كاملة
في دقائق، فتصبح الميزة معطّلة للجميع بلا سبب مفهوم.

**ثلاث قواعد تصميمية**:

1. **السقف يُفحص قبل الطلب لا بعده** — وإلا فقد أنفقنا ما نريد منعه.
2. **الفشل لا يُحسَب استهلاكًا كاملًا** لكنه يُسجَّل — لأن حمى الفشل تستهلك
   المحاولات والرصيد أيضًا، ولأن الأدمن يحتاج معرفة أن المزوّد يتعطّل.
3. **لا نحفظ نص الطلب ولا الرد** — صف واحد بلا محتوى: من، أي مزوّد، أي
   نوع، نجح أم لا. تكفي للعدّ ولا تخزّن بيانات الطلاب.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiUsage, User, utcnow

log = logging.getLogger(__name__)

# السقف اليومي الافتراضي لكل مستخدم. قابل للتغيير من لوحة الأدمن
# (`ai_daily_limit` في `bot_settings`) — لأن مزوّدًا مجانيًا وآخر مدفوعًا
# لا يجوز أن يكون لهما نفس الرقم.
DEFAULT_DAILY_LIMIT = 30

# أنواع الاستخدام — للعرض والتصفية
KIND_LABELS = {
    "explain": "شرح",
    "summary": "تلخيص",
    "exam": "امتحان",
    "exam_full": "امتحان شامل",
    "test": "اختبار إعداد",
    "models": "قائمة موديلات",
}


def today_start():
    """بداية اليوم بتوقيت UTC — يُحسب السقف يوميًا لا كل 24 ساعة متحركة."""
    now = utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def daily_limit(session: AsyncSession, user_id: int | None = None,
                      fallback: int = DEFAULT_DAILY_LIMIT) -> int:
    """السقف الفعّال: للمشرفين بلا سقف تقريبًا، وللطلاب حسب الإعداد.

    لماذا استثناء للمشرفين؟ لأنهم يجرّبون الميزة عند الإعداد ويحتاجون
    اختبارها أكثر من مرة، وحصرهم بنفس سقف الطالب يعطّلهم. لكن ليس بلا حد
    مطلقًا — سقف عالٍ يحمي من خطأ برمجي في حلقة.
    """
    from . import settings_service

    if user_id is not None:
        user = await session.get(User, user_id)
        if user is not None and getattr(user, "level", 0) >= 1:
            return fallback * 10

    value = await settings_service.get_int(session, "ai_daily_limit", fallback)
    return max(1, min(value, 1000))      # حدود معقولة تمنع الأرقام العبثية


# الامتحان الشامل يستهلك حتى 5 نداءات (دفعات 10 أسئلة) — فيُحسب بخمسة.
# بدونه يستطيع الطالب حرق سقفه كله بامتحان واحد ثم يفقد الشرح بقية اليوم.
KIND_COST = {"explain": 1, "summary": 1, "exam": 1, "test": 1, "exam_full": 5}


def cost_of(kind: str) -> int:
    """تكلفة الطلب بعدد «الوحدات» — لا بعدد النداءات الخام."""
    return KIND_COST.get(kind, 1)


async def weighted_used_today(session: AsyncSession, user_id: int | None) -> int:
    """الاستهلاك المحسوب بالتكلفة لا بعدد الصفوف."""
    if user_id is None:
        return 0
    rows = await session.execute(
        select(AiUsage.kind, func.count(AiUsage.id)).where(
            AiUsage.user_id == user_id,
            AiUsage.created_at >= today_start(),
        ).group_by(AiUsage.kind)
    )
    return sum(cost_of(str(kind or "")) * int(count) for kind, count in rows.all())


async def check(session: AsyncSession, user_id: int | None,
                kind: str = "explain") -> tuple[bool, int, int]:
    """هل يسمح لهذا الطلب الآن؟ يُرجع (مسموح، المستهلك، السقف)."""
    limit = await daily_limit(session, user_id)
    used = await weighted_used_today(session, user_id)
    room = limit - used
    return room >= cost_of(kind), used, limit


async def remaining(session: AsyncSession, user_id: int | None) -> tuple[int, int]:
    """(المتبقي، السقف) — للعرض في شاشة الحالة."""
    limit = await daily_limit(session, user_id)
    used = await weighted_used_today(session, user_id)
    return max(0, limit - used), limit


async def limit_message(session: AsyncSession, user_id: int | None) -> str:
    """رسالة مفهومة عند بلوغ السقف — لا «خطأ» مبهم."""
    limit = await daily_limit(session, user_id)
    used = await weighted_used_today(session, user_id)
    return (
        "⏳ <b>وصلت الحدّ اليومي للذكاء الاصطناعي</b>\n\n"
        f"استخدمت <b>{used}</b> من <b>{limit}</b> طلبًا اليوم.\n\n"
        "الميزات الأخرى تعمل عادي: المكتبة، البحث، الكويزات الجاهزة، "
        "وتلخيص PDF المحلي.\n"
        "الحدّ يتجدّد بعد منتصف الليل (UTC)."
    )


async def record(session: AsyncSession, user_id: int | None, kind: str,
                 ok: bool = True, provider: str = "") -> None:
    """تسجيل طلب واحد. لا يرمي استثناءً أبدًا — الإحصاء لا يجوز أن يُسقط ميزة."""
    try:
        session.add(AiUsage(
            user_id=user_id, provider=provider[:30], kind=kind[:20], ok=bool(ok),
        ))
        await session.flush()
    except Exception as exc:      # pragma: no cover - دفاع أخير
        log.warning("تعذّر تسجيل استهلاك AI: %s", type(exc).__name__)


async def last_error_provider(session: AsyncSession, hours: int = 24) -> str:
    """أكثر مزوّد فشلًا في آخر (hours) ساعة — مؤشر أعطال مبكر."""
    since = utcnow() - timedelta(hours=hours)
    row = await session.execute(
        select(AiUsage.provider, func.count(AiUsage.id))
        .where(AiUsage.ok.is_(False), AiUsage.created_at >= since)
        .group_by(AiUsage.provider)
        .order_by(func.count(AiUsage.id).desc())
        .limit(1)
    )
    first = row.first()
    return first[0] if first else ""


async def stats(session: AsyncSession, hours: int = 24) -> dict:
    """أرقام لوحة الأدمن: الحجم، نسبة الفشل، حسب النوع، وأكثر المستخدمين."""
    since = utcnow() - timedelta(hours=hours)
    window = AiUsage.created_at >= since

    total = int(await session.scalar(
        select(func.count(AiUsage.id)).where(window)) or 0)
    failed = int(await session.scalar(
        select(func.count(AiUsage.id)).where(window, AiUsage.ok.is_(False))) or 0)

    by_kind: list[tuple[str, int]] = []
    rows = await session.execute(
        select(AiUsage.kind, func.count(AiUsage.id))
        .where(window).group_by(AiUsage.kind)
        .order_by(func.count(AiUsage.id).desc()).limit(6)
    )
    by_kind = [(str(kind or "—"), int(count)) for kind, count in rows.all()]

    by_provider: list[tuple[str, int]] = []
    rows = await session.execute(
        select(AiUsage.provider, func.count(AiUsage.id))
        .where(window).group_by(AiUsage.provider)
        .order_by(func.count(AiUsage.id).desc()).limit(6)
    )
    by_provider = [(str(name or "—"), int(count)) for name, count in rows.all()]

    top_users: list[tuple[str, int]] = []
    rows = await session.execute(
        select(User.first_name, User.username, func.count(AiUsage.id))
        .join(User, User.id == AiUsage.user_id)
        .where(window).group_by(AiUsage.user_id)
        .order_by(func.count(AiUsage.id).desc()).limit(5)
    )
    for first_name, username, count in rows.all():
        top_users.append((str(first_name or username or "—"), int(count)))

    active_users = int(await session.scalar(
        select(func.count(func.distinct(AiUsage.user_id))).where(window)) or 0)

    return {
        "hours": hours,
        "total": total,
        "failed": failed,
        "ok": total - failed,
        "success_rate": (round((total - failed) * 100 / total) if total else 100),
        "active_users": active_users,
        "by_kind": by_kind,
        "by_provider": by_provider,
        "top_users": top_users,
        "worst_provider": await last_error_provider(session, hours),
    }


async def prune(session: AsyncSession, keep_days: int = 60) -> int:
    """يحذف الصفوف القديمة — الجدول ينمو بلا حد وإلا أثقل القاعدة."""
    cutoff = utcnow() - timedelta(days=max(1, keep_days))
    result = await session.execute(delete(AiUsage).where(AiUsage.created_at < cutoff))
    return int(result.rowcount or 0)
