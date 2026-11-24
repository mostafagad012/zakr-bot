"""إدارة المستخدمين والصلاحيات.

الدخول مفتوح: أي مستخدم تيليجرام يُسجَّل تلقائيًا كطالب عند أول تفاعل.
المالك (OWNER_ID) هو الوحيد الذي يستطيع منح/سحب صلاحيات المشرف والأدمن.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ModerationLog, User, UserRole, utcnow


class PermissionDenied(Exception):
    pass


async def get_or_register(
    session: AsyncSession,
    telegram_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    owner_id: int = 0,
) -> tuple[User, bool]:
    """يرجّع (المستخدم، هل هو جديد). يرقّي المالك تلقائيًا."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    created = user is None

    if created:
        role = UserRole.OWNER.value if owner_id and telegram_id == owner_id else UserRole.STUDENT.value
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            role=role,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            # سباق: تحديثان متزامنان لنفس مستخدم جديد — أعد جلب الفائز
            await session.rollback()
            existing = await session.scalar(
                select(User).where(User.telegram_id == telegram_id))
            if existing is None:
                raise
            user, created = existing, False
    if not created:
        user.username = username or user.username
        user.first_name = first_name or user.first_name
        user.last_name = last_name or user.last_name
        user.last_seen_at = utcnow()
        # حماية: المالك لا يفقد صلاحيته أبدًا
        if owner_id and telegram_id == owner_id and user.role != UserRole.OWNER.value:
            user.role = UserRole.OWNER.value
        await session.flush()

    return user, created


async def get_by_telegram(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def resolve_target(session: AsyncSession, identifier: str) -> User | None:
    """يبحث عن مستخدم برقم تيليجرام أو @username."""
    identifier = identifier.strip()
    if identifier.startswith("@"):
        return await session.scalar(select(User).where(User.username == identifier[1:]))
    if identifier.lstrip("-").isdigit():
        return await session.scalar(select(User).where(User.telegram_id == int(identifier)))
    return None


async def set_role(
    session: AsyncSession,
    actor: User,
    target: User,
    role: UserRole,
    note: str | None = None,
) -> User:
    """تغيير صلاحية مستخدم. المالك فقط يرفع/يخفض؛ الأدمن يرفع لمشرف فقط."""
    if actor.id == target.id and role.value != actor.role:
        raise PermissionDenied("لا يمكنك تغيير صلاحيتك بنفسك")

    if target.role == UserRole.OWNER.value and actor.role != UserRole.OWNER.value:
        raise PermissionDenied("لا يمكن تعديل صلاحية المالك")

    if actor.role == UserRole.ADMIN.value and role in (UserRole.ADMIN, UserRole.OWNER):
        raise PermissionDenied("الأدمن يستطيع الترقية إلى مشرف فقط — ترقية الأدمن للمالك فقط")

    if actor.role not in (UserRole.OWNER.value, UserRole.ADMIN.value):
        raise PermissionDenied("هذه العملية للمالك والأدمن فقط")

    target.role = role.value
    session.add(
        ModerationLog(
            actor_id=actor.id, target_type="user", target_id=target.id,
            action=f"role:{role.value}", note=note,
        )
    )
    await session.flush()
    return target


async def set_banned(
    session: AsyncSession, actor: User, target: User, banned: bool, note: str | None = None
) -> User:
    if target.role == UserRole.OWNER.value:
        raise PermissionDenied("لا يمكن حظر المالك")
    if actor.level <= target.level:
        raise PermissionDenied("لا يمكنك حظر مستخدم بصلاحيات مساوية أو أعلى")
    target.is_banned = banned
    session.add(
        ModerationLog(
            actor_id=actor.id, target_type="user", target_id=target.id,
            action="ban" if banned else "unban", note=note,
        )
    )
    await session.flush()
    return target


async def list_users(
    session: AsyncSession, role: str | None = None, limit: int = 50, offset: int = 0
) -> list[User]:
    stmt = select(User).order_by(User.uploads_count.desc(), User.created_at.desc()).limit(limit).offset(offset)
    if role:
        stmt = select(User).where(User.role == role).order_by(User.created_at.desc()).limit(limit).offset(offset)
    return list(await session.scalars(stmt))


async def active_last_days(session: AsyncSession, days: int = 7) -> int:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return await session.scalar(
        select(func.count(User.id)).where(User.last_seen_at >= cutoff)
    ) or 0


async def top_uploaders(session: AsyncSession, limit: int = 10) -> list[User]:
    """أكثر المستخدمين رفعًا للملفات — للعرض في «حول البوت»."""
    rows = await session.scalars(
        select(User)
        .where(User.is_banned.is_(False))
        .order_by(User.uploads_count.desc())
        .limit(limit)
    )
    return list(rows)
