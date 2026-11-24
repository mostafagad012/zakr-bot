"""سجل تدقيق تغييرات إعداد الذكاء الاصطناعي (من؟ ماذا؟ متى؟ ونجح أم لا؟).

**القاعدة الأمنية الوحيدة** التي يقوم عليها هذا الملف: **لا تُسجَّل قيمة
سرية أبدًا**. أي مفتاح (`AI_API_KEY…`) يُستبدل بعلامة `«مضبوط»` أو `«حُذف»`
أو `«محذوف»`، حتى في قاعدة البيانات — لأن جدول التدقيق يُصدَّر ويُنسخ
ويُقرأ من لوحة الإدارة أحيانًا، فلا يجوز أن يكون طريقًا ثانيًا لتسريب المفتاح.

ما يُسجَّل: المسار (provider/model/base_url) وقيمه غير السرية، والمفتاح
بعلامة فقط، ومن قام بالتغيير، ونتيجة العملية:
`ok` كُتب في `.env` · `readonly` طُبِّق في الذاكرة فقط · `failed` فشل.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ConfigAudit, User, utcnow

log = logging.getLogger(__name__)

SECRET_MARK = "«مفتاح»"  # noqa: S105 - علامة عرض لا كلمة مرور
MAX_KEEP = 500          # سقف الصفوف — التدقيق ليس أرشيفًا أبديًا


def is_secret(key: str) -> bool:
    """هل هذا المتغيّر سرّي (قيمته لا تُسجَّل)؟"""
    return key.startswith("AI_API_KEY")


def mask_value(key: str, value: str) -> str:
    """قيمة آمنة للسجل: المفاتيح تُستبدل بعلامة، وغيرها يُقصّ."""
    if is_secret(key):
        return SECRET_MARK if value else "«حُذف»"
    return (value or "").strip()[:120]


async def log_change(session: AsyncSession, actor: User | None, key: str,
                     old_value: str, new_value: str, outcome: str = "ok") -> None:
    """يقيد تغييرًا واحدًا. **لا يرمي استثناءً** — التدقيق لا يُسقط الإعداد."""
    try:
        session.add(ConfigAudit(
            actor_id=getattr(actor, "id", None),
            actor_name=(getattr(actor, "display_name", "") or "")[:120],
            key=key[:60],
            old_value=mask_value(key, old_value)[:120],
            new_value=mask_value(key, new_value)[:120],
            outcome=outcome[:20],
        ))
        await session.flush()
    except Exception as exc:      # pragma: no cover - دفاع أخير
        log.warning("تعذّر تسجيل تغيير الإعداد: %s", type(exc).__name__)


def _signature(key: str, old_value: str, new_value: str) -> tuple[str, str]:
    """توقيع للمقارنة — يجعل «لم يتغيّر شيء» قرارًا واعيًا لا صدفة."""
    return mask_value(key, old_value), mask_value(key, new_value)


async def record_apply(session: AsyncSession, actor: User | None,
                       changes: list[tuple[str, str, str]] | Sequence[str],
                       outcome: str = "ok") -> int:
    """يقيد مجموعة تغييرات ناتجة عن عمليّة واحدة (اختيار مزوّد مثلًا).

    `changes` = [(المتغيّر، القيمة القديمة، القيمة الجديدة)] — أو مجرد
    أسماء المتغيّرات التي تغيّرت (وهو ما تُرجعه عمليات إدارة المفاتيح، التي
    لا تحتفظ بالقيم القديمة والجديدة في الذاكرة عمدًا).

    في الشكل الثاني نضع «حُذف/غُيّر» بدل القيم، والقناع يمنع أي مفتاح من
    الظهور في السجل. يُرجع عدد ما سُجِّل فعلًا (بدون «لا تغيير»).
    """
    written = 0
    if changes and isinstance(changes[0], str):
        # أسماء فقط: نُسجّل «تغيّر» ولا ندّعي معرفة القيمة
        for key in changes:
            await log_change(session, actor, key, "", "محدَّث", outcome)
            written += 1
        if written:
            try:
                await session.commit()
            except Exception as exc:      # pragma: no cover
                log.warning("تعذّر حفظ سجل التدقيق: %s", type(exc).__name__)
        return written
    for key, old_value, new_value in changes:
        if is_secret(key):
            # المفتاح: ما يهم هو «هل تغيّر؟» لا قيمته. فإن كان في الملف قبل
            # التعديل وبعده فلا شيء يستحق التسجيل.
            if bool(old_value) == bool(new_value):
                continue
        else:
            before, after = _signature(key, old_value, new_value)
            if before == after:
                continue
        await log_change(session, actor, key, old_value, new_value, outcome)
        written += 1
    if written:
        # لماذا commit هنا؟ لأن `flush` لا يجعل الصف الجديد مرئيًا لاستعلام
        # في نفس الجلسة (وضوحًا في الهوية/الحالة)، فكان السجل يبدو فارغًا
        # مباشرة بعد الكتابة. والتدقيق يجب أن يُحفظ حتى لو تابع المعالج
        # عمله ثم حدث خطأ لاحق.
        try:
            await session.commit()
        except Exception as exc:      # pragma: no cover
            log.warning("تعذّر حفظ سجل التدقيق: %s", type(exc).__name__)
    return written


async def recent(session: AsyncSession, limit: int = 10) -> list[ConfigAudit]:
    """أحدث التغييرات — للعرض في اللوحة."""
    rows = await session.execute(
        select(ConfigAudit).order_by(desc(ConfigAudit.created_at)).limit(limit)
    )
    return list(rows.scalars().all())


async def count(session: AsyncSession) -> int:
    from sqlalchemy import func

    return int(await session.scalar(select(func.count(ConfigAudit.id))) or 0)


# سقف قصّ واحد: نمسح على دفعات محدودة بدل تحميل كل الصفوف القديمة في الذاكرة
# (على تثبيت قديم قد تكون ملايين الصفوف؛ `offset` وحده يجلب الباقي كله).
PRUNE_CHUNK = 500


async def prune(session: AsyncSession, keep: int = MAX_KEEP) -> int:
    """يبقي أحدث (keep) صفًا فقط — الجدول لا ينمو بلا حد."""
    from sqlalchemy import delete

    ids = await session.execute(
        select(ConfigAudit.id).order_by(desc(ConfigAudit.created_at))
        .offset(keep).limit(PRUNE_CHUNK)
    )
    stale = [row[0] for row in ids.all()]
    if not stale:
        return 0
    await session.execute(delete(ConfigAudit).where(ConfigAudit.id.in_(stale)))
    return len(stale)


def describe(entry: ConfigAudit) -> str:
    """سطر واحد مقروء للعرض في تيليجرام (نص عادي — يُهرَّب عند الاستخدام)."""
    when = entry.created_at.strftime("%m-%d %H:%M") if entry.created_at else "—"
    who = entry.actor_name or "—"
    outcome = {"ok": "✅", "readonly": "⚠️", "failed": "❌"}.get(entry.outcome, "•")
    old = entry.old_value or "—"
    new = entry.new_value or "—"
    if old == new:
        return f"{outcome} {when} · {who} · {entry.key}"
    return f"{outcome} {when} · {who} · {entry.key}: {old} ← {new}"


def now_stamp() -> str:
    """طابع زمني مقروء — تستخدمه شاشة سجل التدقيق."""
    return utcnow().strftime("%Y-%m-%d %H:%M")
