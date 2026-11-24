"""تصدير المكتبة إلى Excel أو JSON — للتوثيق والتحليل خارج البوت."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import FileItem, Subject, User

log = logging.getLogger(__name__)

COLUMNS = [
    ("المعرّف", 8),
    ("العنوان", 40),
    ("المادة", 24),
    ("القسم", 20),
    ("الفئة", 18),
    ("الرافع", 18),
    ("الحالة", 12),
    ("الحجم (بايت)", 14),
    ("النوع", 10),
    ("التحميلات", 12),
    ("مثبّت", 8),
    ("الوسوم", 24),
    ("الوصف", 40),
    ("تاريخ الرفع", 20),
    ("رسالة التخزين", 16),
]

STATUS_AR = {
    "pending": "قيد المراجعة",
    "approved": "منشور",
    "rejected": "مرفوض",
    "deleted": "محذوف",
}


async def collect_rows(session: AsyncSession, subject_id: int | None = None) -> list[dict]:
    """يجمع صفوف الملفات مع بياناتها المرتبطة."""
    stmt = (
        select(FileItem)
        .options(
            selectinload(FileItem.subject).selectinload(Subject.department),
            selectinload(FileItem.category),
            selectinload(FileItem.uploader),
        )
        .where(FileItem.status != "deleted")
        .order_by(FileItem.subject_id, FileItem.category_id, FileItem.created_at)
    )
    if subject_id is not None:
        stmt = stmt.where(FileItem.subject_id == subject_id)

    rows: list[dict] = []
    for item in await session.scalars(stmt):
        rows.append(
            {
                "id": item.id,
                "title": item.title,
                "subject": item.subject.name if item.subject else "",
                "department": (item.subject.department.name if item.subject and item.subject.department else ""),
                "category": item.category.name if item.category else "",
                "uploader": item.uploader.display_name if item.uploader else "",
                "status": STATUS_AR.get(item.status, item.status),
                "size": item.file_size or 0,
                "kind": item.file_kind or "",
                "downloads": item.downloads_count,
                "pinned": "نعم" if item.is_pinned else "لا",
                "tags": item.tags or "",
                "description": item.description or "",
                "created_at": item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
                "storage_message_id": item.storage_message_id or "",
            }
        )
    return rows


async def collect_summary(session: AsyncSession) -> dict:
    """ملخّص إحصائي يرافق التصدير."""
    total = await session.scalar(select(func.count(FileItem.id))) or 0
    approved = await session.scalar(
        select(func.count(FileItem.id)).where(FileItem.status == "approved")
    ) or 0
    downloads = await session.scalar(select(func.coalesce(func.sum(FileItem.downloads_count), 0))) or 0
    size = await session.scalar(select(func.coalesce(func.sum(FileItem.file_size), 0))) or 0
    subjects = await session.scalar(select(func.count(Subject.id))) or 0
    users = await session.scalar(select(func.count(User.id))) or 0
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "files_total": int(total),
        "files_approved": int(approved),
        "downloads": int(downloads),
        "storage_bytes": int(size),
        "subjects": int(subjects),
        "users": int(users),
    }


def to_json(rows: list[dict], summary: dict) -> str:
    return json.dumps({"summary": summary, "files": rows}, ensure_ascii=False, indent=2)


def to_excel(rows: list[dict], summary: dict, dest: str | Path) -> Path:
    """يكتب ملف Excel. يحتاج openpyxl."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()

    # ورقة الملخّص
    info = workbook.active
    info.title = "ملخص"
    info.append(["ذاكر - مساعدك الدراسى", ""])
    info["A1"].font = Font(bold=True, size=14)
    info.append([])
    labels = {
        "generated_at": "تاريخ التصدير",
        "files_total": "إجمالي الملفات",
        "files_approved": "الملفات المنشورة",
        "downloads": "إجمالي التحميلات",
        "storage_bytes": "حجم التخزين (بايت)",
        "subjects": "عدد المواد",
        "users": "عدد المستخدمين",
    }
    for key, label in labels.items():
        info.append([label, summary.get(key, "")])
    info.column_dimensions["A"].width = 26
    info.column_dimensions["B"].width = 28

    # ورقة الملفات
    sheet = workbook.create_sheet("الملفات")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    for index, (title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        sheet.column_dimensions[get_column_letter(index)].width = width

    keys = [
        "id", "title", "subject", "department", "category", "uploader", "status",
        "size", "kind", "downloads", "pinned", "tags", "description",
        "created_at", "storage_message_id",
    ]
    for row_index, row in enumerate(rows, start=2):
        for column_index, key in enumerate(keys, start=1):
            sheet.cell(row=row_index, column=column_index, value=row.get(key, ""))

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(2, len(rows) + 1)}"

    workbook.save(dest)
    return dest


async def export_files(
    session: AsyncSession,
    dest_dir: str | Path,
    fmt: str = "xlsx",
    subject_id: int | None = None,
) -> Path:
    """يصدر المكتبة ويرجع مسار الملف الناتج."""
    rows = await collect_rows(session, subject_id)
    summary = await collect_summary(session)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_dir = Path(dest_dir)

    if fmt == "json":
        dest = dest_dir / f"library-{stamp}.json"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(to_json(rows, summary), encoding="utf-8")
        return dest

    dest = dest_dir / f"library-{stamp}.xlsx"
    return to_excel(rows, summary, dest)
