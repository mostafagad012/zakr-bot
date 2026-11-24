"""تسجيل الملفات، فهرستها للبحث، إحصائيات المكتبة."""
from __future__ import annotations

import hashlib
import logging
import re

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    FileCategory,
    FileItem,
    FileStatus,
    ModerationLog,
    Subject,
    User,
    utcnow,
)

log = logging.getLogger(__name__)

from . import semantic  # noqa: E402 — بعد النماذج لتجنّب استيراد دائري

FTS_TABLE = "search_index"


def detect_kind(mime_type: str | None, filename: str | None) -> str:
    m = (mime_type or "").lower()
    n = (filename or "").lower()
    if m.startswith("application/pdf") or n.endswith(".pdf"):
        return "pdf"
    if m.startswith("video/") or n.endswith((".mp4", ".mkv", ".mov", ".avi")):
        return "video"
    if m.startswith("image/") or n.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "image"
    if m.startswith("audio/") or n.endswith((".mp3", ".m4a", ".wav", ".ogg")):
        return "audio"
    if n.endswith((".doc", ".docx")) or "word" in m:
        return "docx"
    if n.endswith((".ppt", ".pptx")):
        return "pptx"
    if n.endswith((".xls", ".xlsx")):
        return "xlsx"
    if n.endswith((".zip", ".rar", ".7z")):
        return "archive"
    if n.startswith("http://") or n.startswith("https://"):
        return "link"
    return "other"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_tags(raw: str | None) -> str | None:
    if not raw:
        return None
    # الفواصل العربية (، و ؛) والإنجليزية والمسافات كلها فواصل وسوم
    parts = [t.strip().lower() for t in re.split(r"[,\u060c\u061b\u066c\s]+", raw) if t.strip()]
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return ",".join(seen[:20]) or None


async def list_categories(session: AsyncSession) -> list[FileCategory]:
    return list(await session.scalars(select(FileCategory).order_by(FileCategory.sort_order)))


async def register_file(
    session: AsyncSession,
    *,
    subject_id: int,
    category_id: int,
    uploader_id: int,
    title: str,
    description: str | None = None,
    tags: str | None = None,
    original_filename: str | None = None,
    file_size: int | None = None,
    mime_type: str | None = None,
    storage_message_id: int | None = None,
    tg_file_id: str | None = None,
    tg_unique_file_id: str | None = None,
    external_url: str | None = None,
    sha256: str | None = None,
    pages_count: int | None = None,
    status: FileStatus = FileStatus.PENDING,
    extracted_text: str | None = None,
) -> FileItem:
    """يحفظ سجل الملف + يفهرسه للبحث. لا يعمل commit."""
    kind = detect_kind(mime_type, original_filename or external_url)
    file_item = FileItem(
        subject_id=subject_id,
        category_id=category_id,
        uploader_id=uploader_id,
        title=title,
        description=description,
        tags=normalize_tags(tags),
        original_filename=original_filename,
        file_size=file_size,
        mime_type=mime_type,
        file_kind=kind,
        storage_message_id=storage_message_id,
        tg_file_id=tg_file_id,
        tg_unique_file_id=tg_unique_file_id,
        external_url=external_url,
        sha256=sha256,
        pages_count=pages_count,
        text_extracted=bool(extracted_text),
        status=status.value,
    )
    session.add(file_item)
    await session.flush()

    await index_file(session, file_item, extracted_text=extracted_text)
    return file_item


def fts_id_column(dialect_name: str | None) -> str:
    """عمود معرّف الملف في جدول FTS: rowid في SQLite، file_id في PostgreSQL.

    ⚠️ كان `rowid` مكتوبًا صراحة في الفهرسة فتنكسر كل عملية رفع/فهرسة
    على PostgreSQL (لا عمود بهذا الاسم هناك) — لا تُعد لهذه الصراحة.
    """
    return "rowid" if not dialect_name or dialect_name == "sqlite" else "file_id"


def _session_id_column(session: AsyncSession) -> str:
    return fts_id_column(session.bind.dialect.name if session.bind is not None else None)


async def index_file(session: AsyncSession, file_item: FileItem, extracted_text: str | None = None) -> None:
    """يكتب/يحدّث صف الملف في جدول البحث النصي + متجه البحث الدلالي."""
    content = extracted_text or ""
    column = _session_id_column(session)
    await session.execute(
        text(f"DELETE FROM {FTS_TABLE} WHERE {column} = :rid"), {"rid": file_item.id}
    )
    await session.execute(
        text(
            f"INSERT INTO {FTS_TABLE} ({column}, title, description, tags, content) "
            "VALUES (:rid, :title, :description, :tags, :content)"
        ),
        {
            "rid": file_item.id,
            "title": file_item.title or "",
            "description": file_item.description or "",
            "tags": (file_item.tags or "").replace(",", " "),
            "content": content,
        },
    )
    # البحث الدلالي: متجه من العنوان + الوصف + الوسوم + النص
    await semantic.index(
        session,
        file_item.id,
        file_item.title or "",
        file_item.description or "",
        (file_item.tags or "").replace(",", " "),
        content,
    )


async def unindex_file(session: AsyncSession, file_id: int) -> None:
    column = _session_id_column(session)
    await session.execute(text(f"DELETE FROM {FTS_TABLE} WHERE {column} = :rid"), {"rid": file_id})
    await semantic.unindex(session, file_id)


async def get_indexed_text(session: AsyncSession, file_id: int) -> str | None:
    """يرجع النص المفهرس لملف (يُستخدم للتلخيص). يعمل على SQLite و PostgreSQL."""
    column = _session_id_column(session)
    row = await session.execute(
        text(f"SELECT content FROM {FTS_TABLE} WHERE {column} = :rid"), {"rid": file_id}
    )
    value = row.scalar()
    return value if value else None


async def get_file(session: AsyncSession, file_id: int) -> FileItem | None:
    stmt = (
        select(FileItem)
        .options(selectinload(FileItem.subject), selectinload(FileItem.category), selectinload(FileItem.uploader))
        .where(FileItem.id == file_id)
    )
    return await session.scalar(stmt)


async def files_by_category(
    session: AsyncSession,
    subject_id: int,
    category_id: int | None = None,
    only_approved: bool = True,
    limit: int = 50,
) -> list[FileItem]:
    stmt = (
        select(FileItem)
        .where(FileItem.subject_id == subject_id)
        .options(selectinload(FileItem.category), selectinload(FileItem.uploader))
    )
    if category_id is not None:
        stmt = stmt.where(FileItem.category_id == category_id)
    if only_approved:
        stmt = stmt.where(FileItem.status == FileStatus.APPROVED.value)
    else:
        stmt = stmt.where(FileItem.status != FileStatus.DELETED.value)
    stmt = stmt.order_by(
        FileItem.is_pinned.desc(), FileItem.downloads_count.desc(), FileItem.created_at.desc()
    ).limit(limit)
    return list(await session.scalars(stmt))


async def pending_files(session: AsyncSession, limit: int = 30) -> list[FileItem]:
    stmt = (
        select(FileItem)
        .where(FileItem.status == FileStatus.PENDING.value)
        .options(selectinload(FileItem.subject), selectinload(FileItem.category), selectinload(FileItem.uploader))
        .order_by(FileItem.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def pending_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count(FileItem.id)).where(FileItem.status == FileStatus.PENDING.value)
    ) or 0


async def duplicate_of(session: AsyncSession, sha256: str) -> FileItem | None:
    if not sha256:
        return None
    stmt = (
        select(FileItem)
        .where(FileItem.sha256 == sha256, FileItem.status != FileStatus.DELETED.value)
        .limit(1)
    )
    return await session.scalar(stmt)


async def set_status(
    session: AsyncSession,
    file_item: FileItem,
    status: FileStatus,
    actor_id: int | None = None,
    note: str | None = None,
) -> FileItem:
    file_item.status = status.value
    file_item.reviewed_by = actor_id
    file_item.review_note = note
    file_item.reviewed_at = utcnow()
    session.add(
        ModerationLog(
            actor_id=actor_id,
            target_type="file",
            target_id=file_item.id,
            action=status.value,
            note=note,
        )
    )
    await session.flush()
    return file_item


async def soft_delete(session: AsyncSession, file_item: FileItem, actor_id: int | None = None, note: str | None = None) -> None:
    await set_status(session, file_item, FileStatus.DELETED, actor_id, note)
    await unindex_file(session, file_item.id)


async def record_download(session: AsyncSession, file_item: FileItem, user: User) -> None:
    file_item.downloads_count += 1
    user.downloads_count += 1
    await session.flush()


async def library_stats(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count(FileItem.id))) or 0
    approved = await session.scalar(
        select(func.count(FileItem.id)).where(FileItem.status == FileStatus.APPROVED.value)
    ) or 0
    pending = await session.scalar(
        select(func.count(FileItem.id)).where(FileItem.status == FileStatus.PENDING.value)
    ) or 0
    downloads = await session.scalar(select(func.coalesce(func.sum(FileItem.downloads_count), 0))) or 0
    users = await session.scalar(select(func.count(User.id))) or 0
    subjects = await session.scalar(select(func.count(Subject.id))) or 0
    size = await session.scalar(select(func.coalesce(func.sum(FileItem.file_size), 0))) or 0
    return {
        "files_total": int(total),
        "files_approved": int(approved),
        "files_pending": int(pending),
        "downloads": int(downloads),
        "users": int(users),
        "subjects": int(subjects),
        "storage_bytes": int(size),
    }


async def top_subjects(session: AsyncSession, limit: int = 10) -> list[tuple[Subject, int]]:
    rows = (
        await session.execute(
            select(Subject, func.coalesce(func.sum(FileItem.downloads_count), 0).label("dl"))
            .join(FileItem, FileItem.subject_id == Subject.id, isouter=True)
            .group_by(Subject.id)
            .order_by(text("dl DESC"))
            .limit(limit)
        )
    ).all()
    return [(r[0], int(r[1])) for r in rows]


def human_size(num: int | None) -> str:
    if not num:
        return "—"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


# ═══════════════════════════════════════════════════════════════════
#  تعديل المواد — صلاحية الملكية الكاملة للمستخدم على ما رفعه
# ═══════════════════════════════════════════════════════════════════
#
#  السياسة المتفَق عليها: **صاحب الملف يعدّل ويحذف ما رفعه هو فقط**،
#  والمشرفون والأدمن يعدّلون أي ملف (لأنهم يراجعون المكتبة أصلًا).
#
#  ⚠️ لماذا هذه السياسة وليست «أي مستخدم يعدّل أي ملف»؟ لأن المكتبة
#  مشتركة بين الطلاب: لو عدّل أي طالب أي ملف لصار بإمكانه حذف مادة
#  زميله أو تغيير عنوانها — وهذا تدمير متبادل بلا رقابة. الملكية تجعل
#  كل مسؤولية على صاحبها، والمراجعة تبقى للمشرف.

#: حقول يقدر المستخدم يعدّلها بنفسه (بلا مراجعة إدارية).
#: `status` **_ليس_** منها: تغيير حالة الملف قرار إداري لا تحرير شخصي.
EDITABLE_FIELDS: tuple[str, ...] = (
    "title", "description", "tags", "category_id", "subject_id",
    "external_url", "original_filename",
)

TITLE_MAX = 240
DESCRIPTION_MAX = 2000
TAG_MAX_COUNT = 20
TAG_MAX_LEN = 40
URL_MAX = 500


class EditError(Exception):
    """خطأ تحرير مفهوم للمستخدم — الرسالة عربية وصالحة للعرض."""


def can_edit(file_item: FileItem, user: User | None) -> bool:
    """هل يملك هذا المستخدم حقّ تعديل هذا الملف؟

    صاحب الملف نعم · المشرف/الأدمن/المالك نعم · غير ذلك لا.
    `user=None` تعني «لا أعرف من أنت» ⇒ لا (أضيق صلاحية).
    """
    if file_item is None or user is None:
        return False
    if getattr(user, "is_staff", lambda: False)():
        return True
    return file_item.uploader_id is not None and file_item.uploader_id == user.id


def edit_denied_reason(file_item: FileItem, user: User | None) -> str:
    """سبب واضح بالعربية بدل «لا تملك صلاحية» المبهمة."""
    if user is None:
        return "تعذّر التعرّف على حسابك. جرّب /start ثم أعد المحاولة."
    if file_item is None:
        return "الملف لم يعد موجودًا — ربما حُذف."
    if getattr(user, "is_banned", False):
        return "حسابك موقوف — لا يمكنك تعديل المواد."
    return ("الملف مش من رفعك — تقدر تعدّل ملفاتك اللي رفعتها أنت بس. "
            "لو الملف فيه خطأ، كلّم المشرف.")


def normalize_title(raw: str | None) -> str:
    """يوحّد العنوان: بلا فراغات زائدة، وبحدّ أقصى معروف."""
    value = " ".join((raw or "").split())
    if not value:
        raise EditError("العنوان لا يكون فاضيًا.")
    if len(value) > TITLE_MAX:
        raise EditError(f"العنوان طويل جدًا — الحدّ {TITLE_MAX} حرفًا.")
    return value


def normalize_description(raw: str | None) -> str:
    """الوصف: نحافظ على أسطره (لا نُسطّحه) لكن بلا فراغات زائدة."""
    value = "\n".join(line.rstrip() for line in (raw or "").splitlines()).strip()
    if len(value) > DESCRIPTION_MAX:
        raise EditError(f"الوصف طويل جدًا — الحدّ {DESCRIPTION_MAX} حرفًا.")
    return value


def normalize_tags_or_raise(raw: str | None) -> str:
    """يُنقّي الوسوم ويرفع خطأً مفهومًا بدل قطع صامت.

    `normalize_tags` الموجودة تقتطع بصمت؛ في التحرير اليدوي يحتاج
    المستخدم أن يعرف أن وسمًا أُسقط، لا أن يفاجأ لاحقًا.
    """
    cleaned = normalize_tags(raw)
    if cleaned is None:
        return ""
    parts = [t.strip() for t in cleaned.split(",") if t.strip()]
    too_long = [t for t in parts if len(t) > TAG_MAX_LEN]
    if too_long:
        raise EditError(
            f"وسم طويل جدًا (الحدّ {TAG_MAX_LEN} حرفًا): {too_long[0][:50]}"
        )
    if len(parts) > TAG_MAX_COUNT:
        raise EditError(f"عدد الوسوم كبير — الحدّ {TAG_MAX_COUNT} وسمًا.")
    return ",".join(parts)


def normalize_url(raw: str | None) -> str:
    """رابط خارجي اختياري — تحقّق من المخطّط فقط.

    لا نُجري طلب شبكة للتحقّق (بطيء وقد يكون خطيرًا)، لكن نرفض أي مخطّط
    غير http/https حتى لا يُخزَّن `javascript:` ويُعرض كزرّ في تيليجرام.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if len(value) > URL_MAX:
        raise EditError(f"الرابط طويل جدًا — الحدّ {URL_MAX} حرفًا.")
    low = value.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise EditError("الرابط لازم يبدأ بـ http:// أو https://")
    return value


async def update_file(
    session: AsyncSession,
    file_item: FileItem,
    user: User | None,
    *,
    changes: dict[str, object],
    actor_id: int | None = None,
) -> FileItem:
    """يطبّق تعديلات المستخدم على ملف — مع التحقّق وإعادة الفهرسة.

    **لماذا إعادة الفهرسة ضرورية هنا؟** لأن `search_index` (FTS) يحمل
    نسخة من العنوان والوصف والوسوم. تعديل الحقول بلا إعادة فهرسة يعني أن
    البحث يظل يُرجع **العنوان القديم** — وهو عطل حقيقي لا يلاحظه أحد
    إلا بعد شكوى «بحثت بالاسم الجديد فلم أجد الملف».
    """
    if not can_edit(file_item, user):
        raise EditError(edit_denied_reason(file_item, user))

    applied: list[str] = []

    if "title" in changes:
        file_item.title = normalize_title(changes["title"])       # type: ignore[arg-type]
        applied.append("title")
    if "description" in changes:
        file_item.description = normalize_description(changes["description"]) or None  # type: ignore[arg-type]
        applied.append("description")
    if "tags" in changes:
        file_item.tags = normalize_tags_or_raise(changes["tags"]) or None  # type: ignore[arg-type]
        applied.append("tags")
    if "external_url" in changes:
        file_item.external_url = normalize_url(changes["external_url"]) or None  # type: ignore[arg-type]
        applied.append("external_url")
    if "original_filename" in changes:
        name = " ".join(str(changes["original_filename"] or "").split())[:240]
        file_item.original_filename = name or None
        applied.append("original_filename")

    if "category_id" in changes:
        new_cat = changes["category_id"]
        if new_cat is not None:
            from ..models import FileCategory      # استيراد محلي: تفادي دورة
            exists = await session.get(FileCategory, int(new_cat))  # type: ignore[arg-type]
            if exists is None:
                raise EditError("الفئة المختارة غير موجودة.")
            file_item.category_id = int(new_cat)  # type: ignore[arg-type]
            applied.append("category_id")

    if "subject_id" in changes:
        new_subj = changes["subject_id"]
        if new_subj is not None:
            from ..models import Subject
            exists = await session.get(Subject, int(new_subj))  # type: ignore[arg-type]
            if exists is None:
                raise EditError("المادة المختارة غير موجودة.")
            file_item.subject_id = int(new_subj)  # type: ignore[arg-type]
            applied.append("subject_id")

    if not applied:
        raise EditError("لم تُرسل أي تغييرات.")

    # تعديل المحتوى الوصفي يُعيد الملف للمراجعة إن كان منشورًا؟
    # لا — لأن التعديل من صاحبه على وصفه لا يغيّر الملف نفسه (الوسيط
    # المخزَّن في تيليجرام لم يُلمَس). لكن نُسجّل التعديل للشفافية.
    session.add(
        ModerationLog(
            actor_id=actor_id if actor_id is not None else
            (user.id if user is not None else None),
            target_type="file",
            target_id=file_item.id,
            action="edited",
            note="تعديل ذاتي: " + ", ".join(applied)[:200],
        )
    )
    await session.flush()

    # إعادة الفهرسة بالعنوان/الوصف/الوسوم الجديدة (ونص المادة كما هو)
    try:
        existing = await get_indexed_text(session, file_item.id)
        await index_file(session, file_item, existing)
    except Exception as exc:            # noqa: BLE001
        # فشل الفهرسة لا يُلغي التعديل — لكن يجب ألّا يمرّ صامتًا
        log.warning("تعذّر إعادة فهرسة الملف %s بعد التعديل: %s", file_item.id, exc)

    return file_item


async def user_files(
    session: AsyncSession, user_id: int, *, limit: int = 30, only_active: bool = True,
) -> list[FileItem]:
    """ملفات رفعها هذا المستخدم — لشاشة «موادي».

    `only_active=True` تستثني المحذوف، لأن شاشة «موادي» لإدارة ما يملكه
    المستخدم فعلًا؛ إظهار المحذوف يربك ويوحي بأنه ما زال قائمًا.
    """
    stmt = select(FileItem).where(FileItem.uploader_id == user_id)
    if only_active:
        stmt = stmt.where(FileItem.status != FileStatus.DELETED.value)
    stmt = stmt.order_by(FileItem.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def user_files_count(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(FileItem.id)).where(
        FileItem.uploader_id == user_id,
        FileItem.status != FileStatus.DELETED.value,
    )
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def delete_file(
    session: AsyncSession, file_item: FileItem, user: User | None, *, note: str = "",
) -> None:
    """حذف الملف بصلاحية المالك/المشرف — حذف ناعم مع تفريغ فهارسه.

    حذف ناعم (`status=deleted`) لا حذف صفّي: الملف ما زال في قناة
    تيليجرام، وحذف الصفّ يفقد أثر من رفعه ومتى — والمراجعة تحتاج الأثر.
    """
    if not can_edit(file_item, user):
        raise EditError(edit_denied_reason(file_item, user))
    await soft_delete(
        session, file_item,
        actor_id=user.id if user is not None else None,
        note=note or "حذف بواسطة صاحبه" if user is not None else note,
    )
    await session.flush()
