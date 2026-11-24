"""إدارة الهيكل الأكاديمي: قسم ← مستوى ← سنة ← فصل ← مادة."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import AcademicYear, Department, FileItem, FileStatus, Level, Semester, Subject


class NotFound(Exception):
    pass


async def get_or_create(session: AsyncSession, model, name: str, **extra):
    row = await session.scalar(select(model).where(model.name == name))
    if row:
        return row
    row = model(name=name, **extra)
    session.add(row)
    await session.flush()
    return row


async def list_departments(session: AsyncSession) -> list[Department]:
    return list(await session.scalars(select(Department).order_by(Department.sort_order, Department.name)))


async def list_levels(session: AsyncSession) -> list[Level]:
    return list(await session.scalars(select(Level).order_by(Level.sort_order, Level.name)))


async def list_years(session: AsyncSession, only_active: bool = False) -> list[AcademicYear]:
    stmt = select(AcademicYear).order_by(AcademicYear.sort_order.desc(), AcademicYear.name.desc())
    if only_active:
        stmt = stmt.where(AcademicYear.is_active.is_(True))
    return list(await session.scalars(stmt))


async def list_semesters(session: AsyncSession) -> list[Semester]:
    return list(await session.scalars(select(Semester).order_by(Semester.sort_order, Semester.name)))


async def list_subjects(
    session: AsyncSession,
    department_id: int | None = None,
    level_id: int | None = None,
    year_id: int | None = None,
    semester_id: int | None = None,
    include_inactive: bool = False,
) -> list[Subject]:
    stmt = select(Subject).options(
        selectinload(Subject.department),
        selectinload(Subject.level),
        selectinload(Subject.year),
        selectinload(Subject.semester),
    )
    if department_id is not None:
        stmt = stmt.where(Subject.department_id == department_id)
    if level_id is not None:
        stmt = stmt.where(Subject.level_id == level_id)
    if year_id is not None:
        stmt = stmt.where(Subject.year_id == year_id)
    if semester_id is not None:
        stmt = stmt.where(Subject.semester_id == semester_id)
    if not include_inactive:
        stmt = stmt.where(Subject.is_active.is_(True))
    stmt = stmt.order_by(Subject.name)
    return list(await session.scalars(stmt))


async def all_subjects(session: AsyncSession, *, limit: int = 60) -> list[Subject]:
    """كل المواد النشطة بلا تصفية هرمية — لاختيار مادة عند التحرير.

    **لماذا دالة منفصلة عن `list_subjects`؟** لأن `list_subjects` مبنية
    على التنقّل الهرمي (قسم ← مستوى ← سنة ← فصل)، والتحرير يحتاج «كل
    المواد» في قائمة واحدة بلا المرور بالهرم. إعادة استخدام
    `list_subjects` بلا معاملات تُرجع كل شيء أيضًا لكنها تُحمّل علاقات
    التنقّل كلها بلا داعٍ.
    """
    stmt = (
        select(Subject)
        .where(Subject.is_active.is_(True))
        .order_by(Subject.name)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def create_subject(
    session: AsyncSession,
    name: str,
    department_id: int,
    level_id: int | None = None,
    year_id: int | None = None,
    semester_id: int | None = None,
    code: str | None = None,
    description: str | None = None,
) -> Subject:
    subject = Subject(
        name=name,
        code=code,
        department_id=department_id,
        level_id=level_id,
        year_id=year_id,
        semester_id=semester_id,
        description=description,
    )
    session.add(subject)
    await session.flush()
    return subject


async def get_subject(session: AsyncSession, subject_id: int) -> Subject:
    subject = await session.get(Subject, subject_id)
    if subject is None:
        raise NotFound(f"المادة {subject_id} غير موجودة")
    return subject


@dataclass(frozen=True)
class SubjectStats:
    subject_id: int
    files_total: int
    files_approved: int
    files_pending: int
    downloads: int


async def subject_stats(session: AsyncSession, subject_id: int) -> SubjectStats:
    rows = (
        await session.execute(
            select(FileItem.status, func.count(FileItem.id), func.coalesce(func.sum(FileItem.downloads_count), 0))
            .where(FileItem.subject_id == subject_id)
            .group_by(FileItem.status)
        )
    ).all()
    total = sum(int(r[1]) for r in rows)
    approved = sum(int(r[1]) for r in rows if r[0] == FileStatus.APPROVED.value)
    pending = sum(int(r[1]) for r in rows if r[0] == FileStatus.PENDING.value)
    downloads = sum(int(r[2]) for r in rows)
    return SubjectStats(subject_id, total, approved, pending, downloads)


async def set_active(session: AsyncSession, model_obj, active: bool) -> None:
    model_obj.is_active = active
    await session.flush()



