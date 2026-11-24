"""تصفّح المكتبة: قسم ← مستوى ← سنة ← فصل ← مادة ← فئة ← ملف."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User
from ..services import catalog, files as files_service, search
from ..services.files import human_size
from .helpers import (
    back_button,
    cb_data,
    escape,
    file_caption,
    slice_page,
)

log = logging.getLogger(__name__)
router = Router(name="library")

# الحارس انتقل إلى `helpers.cb_data` — نسخة واحدة بدل ثلاث.
_cd = cb_data


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """يحرّر الرسالة ويتجاهل خطأ "لم يتغير شيء"."""
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


def _grid(rows: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [rows[i : i + width] for i in range(0, len(rows), width)]


# ------------------------------------------------------------------ الأقسام
@router.callback_query(F.data.startswith("nav:dept:"))
async def cb_departments(callback: CallbackQuery, db_session) -> None:
    page = int(callback.data.split(":")[-1] or 0)
    departments = await catalog.list_departments(db_session)

    if not departments:
        await _edit(
            callback,
            texts.header("library") + "\n\n📭 لا توجد أقسام بعد.\n"
            "يمكن للأدمن إضافة الأقسام والمواد من لوحة الإدارة.",
            InlineKeyboardMarkup(inline_keyboard=[[back_button(callback_data="menu:main")]]),
        )
        await callback.answer()
        return

    items, total_pages = slice_page(departments, page + 1)
    buttons = [
        InlineKeyboardButton(text=f"🏛️ {escape(d.name)}", callback_data=_cd(f"nav:lvl:0:{d.id}"))
        for d in items
    ]
    builder = InlineKeyboardBuilder()
    for row in _grid(buttons, 1):
        builder.row(*row)
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"nav:dept:{page - 1}"))
        nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
        if page + 1 < total_pages:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"nav:dept:{page + 1}"))
        builder.row(*nav_row)
    builder.row(back_button(callback_data="menu:main"))

    await _edit(callback, texts.header("library", subtitle="🏛️ اختر القسم:"), builder.as_markup())
    await callback.answer()


# ----------------------------------------------------------------- المستويات
@router.callback_query(F.data.startswith("nav:lvl:"))
async def cb_levels(callback: CallbackQuery, db_session) -> None:
    _, _, _page_raw, dept_id = callback.data.split(":")
    levels = await catalog.list_levels(db_session)
    subjects_all = await catalog.list_subjects(db_session, department_id=int(dept_id))

    builder = InlineKeyboardBuilder()
    if levels:
        for level in levels:
            count = sum(1 for s in subjects_all if s.level_id == level.id)
            builder.row(InlineKeyboardButton(
                text=f"🎓 {escape(level.name)} ({count})", callback_data=_cd(f"nav:yr:0:{dept_id}:{level.id}")
            ))
        builder.row(InlineKeyboardButton(
            text="📖 كل مواد القسم", callback_data=_cd(f"nav:subj:0:{dept_id}:0:0:0")
        ))
    else:
        builder.row(InlineKeyboardButton(
            text="📖 مواد القسم", callback_data=_cd(f"nav:subj:0:{dept_id}:0:0:0")
        ))
    builder.row(back_button("⬅️ الأقسام", "nav:dept:0"))

    await _edit(callback, texts.header("library", subtitle="🎓 اختر المستوى الدراسي:"), builder.as_markup())
    await callback.answer()


# --------------------------------------------------------------- السنوات
@router.callback_query(F.data.startswith("nav:yr:"))
async def cb_years(callback: CallbackQuery, db_session) -> None:
    _, _, _page, dept_id, level_id = callback.data.split(":")
    years = await catalog.list_years(db_session, only_active=True) or await catalog.list_years(db_session)
    subjects_all = await catalog.list_subjects(
        db_session, department_id=int(dept_id), level_id=int(level_id)
    )

    builder = InlineKeyboardBuilder()
    for year in years:
        count = sum(1 for s in subjects_all if s.year_id == year.id)
        builder.row(InlineKeyboardButton(
            text=f"📅 {escape(year.name)} ({count})",
            callback_data=_cd(f"nav:sem:0:{dept_id}:{level_id}:{year.id}"),
        ))
    builder.row(InlineKeyboardButton(
        text="📖 كل السنوات", callback_data=_cd(f"nav:subj:0:{dept_id}:{level_id}:0:0")
    ))
    builder.row(back_button("⬅️ المستويات", _cd(f"nav:lvl:0:{dept_id}")))

    await _edit(callback, texts.header("library", subtitle="📅 اختر العام الدراسي:"), builder.as_markup())
    await callback.answer()


# --------------------------------------------------------------- الفصول
@router.callback_query(F.data.startswith("nav:sem:"))
async def cb_semesters(callback: CallbackQuery, db_session) -> None:
    _, _, _page, dept_id, level_id, year_id = callback.data.split(":")
    semesters = await catalog.list_semesters(db_session)
    subjects_all = await catalog.list_subjects(
        db_session, department_id=int(dept_id), level_id=int(level_id), year_id=int(year_id)
    )

    builder = InlineKeyboardBuilder()
    for semester in semesters:
        count = sum(1 for s in subjects_all if s.semester_id == semester.id)
        builder.row(InlineKeyboardButton(
            text=f"🗓️ {escape(semester.name)} ({count})",
            callback_data=_cd(f"nav:subj:0:{dept_id}:{level_id}:{year_id}:{semester.id}"),
        ))
    builder.row(InlineKeyboardButton(
        text="📖 كل الفصول", callback_data=_cd(f"nav:subj:0:{dept_id}:{level_id}:{year_id}:0")
    ))
    builder.row(back_button("⬅️ السنوات", _cd(f"nav:yr:0:{dept_id}:{level_id}")))

    await _edit(callback, texts.header("library", subtitle="🗓️ اختر الفصل الدراسي:"), builder.as_markup())
    await callback.answer()


# ---------------------------------------------------------------- المواد
@router.callback_query(F.data.startswith("nav:subj:"))
async def cb_subjects(callback: CallbackQuery, db_session) -> None:
    _, _, page_raw, dept_id, level_id, year_id, semester_id = callback.data.split(":")
    page = int(page_raw)

    subjects = await catalog.list_subjects(
        db_session,
        department_id=int(dept_id) or None,
        level_id=int(level_id) or None,
        year_id=int(year_id) or None,
        semester_id=int(semester_id) or None,
    )
    if not subjects:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="لا توجد مواد في هذا المسار", callback_data="noop:page"))
        builder.row(back_button("⬅️ رجوع", _cd(f"nav:sem:0:{dept_id}:{level_id}:{year_id}")))
        await _edit(callback, "📭 لا توجد مواد بعد في هذا المسار.", builder.as_markup())
        await callback.answer()
        return

    items, total_pages = slice_page(subjects, page + 1)
    builder = InlineKeyboardBuilder()
    for subject in items:
        builder.row(InlineKeyboardButton(
            text=f"📘 {escape(subject.name)}", callback_data=_cd(f"subj:view:{subject.id}")
        ))
    if total_pages > 1:
        nav_row = []
        def _nav_page(p: int) -> str:
            return f"nav:subj:{p}:{dept_id}:{level_id}:{year_id}:{semester_id}"

        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=_cd(_nav_page(page - 1))))
        nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
        if page + 1 < total_pages:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=_cd(_nav_page(page + 1))))
        builder.row(*nav_row)
    builder.row(back_button("⬅️ الفصول", _cd(f"nav:sem:0:{dept_id}:{level_id}:{year_id}")))

    await _edit(callback, texts.header("library", subtitle=f"📘 المواد ({len(subjects)}) — اختر مادة:"), builder.as_markup())
    await callback.answer()


# ------------------------------------------------------------ صفحة المادة
@router.callback_query(F.data.startswith("subj:view:"))
async def cb_subject_view(callback: CallbackQuery, db_session) -> None:
    subject_id = int(callback.data.split(":")[-1])
    subject = await catalog.get_subject(db_session, subject_id)
    stats = await catalog.subject_stats(db_session, subject_id)
    categories = await files_service.list_categories(db_session)

    # استعلام تجميعي واحد بدل استعلام كامل لكل تصنيف — وكان العدّاد
    # مقطوعًا عند ٥٠ بسبب limit في files_by_category (عِلّة عرض حقيقية).
    counts = await files_service.category_counts(db_session, subject_id)

    builder = InlineKeyboardBuilder()
    for category in categories:
        count = counts.get(category.id, 0)
        builder.row(InlineKeyboardButton(
            text=f"{category.icon or '📁'} {escape(category.name)} ({count})",
            callback_data=_cd(f"cat:view:{subject_id}:{category.id}:0"),
        ))
    builder.row(InlineKeyboardButton(
        text="📂 كل الملفات", callback_data=_cd(f"cat:view:{subject_id}:0:0")
    ))
    builder.row(InlineKeyboardButton(
        text="📤 رفع ملف هنا", callback_data=_cd(f"upload:cat:{subject_id}")
    ))
    builder.row(back_button("⬅️ المواد", _cd(
        f"nav:subj:0:{subject.department_id}:{subject.level_id or 0}:"
        f"{subject.year_id or 0}:{subject.semester_id or 0}"
    )))

    text = "\n".join([
        texts.header("subject", escape(subject.name)),
        texts.info_block([
            ("🏛️", "القسم", escape(subject.department.name if subject.department else "—")),
            ("📄", "الملفات المنشورة", f"<b>{stats.files_approved}</b>"),
            ("⏳", "قيد المراجعة", stats.files_pending),
            ("📥", "إجمالي التحميلات", stats.downloads),
        ]),
    ])
    await _edit(callback, text, builder.as_markup())
    await callback.answer()


# -------------------------------------------------------------- الفئة/الملفات
@router.callback_query(F.data.startswith("cat:view:"))
async def cb_category_files(callback: CallbackQuery, db_session) -> None:
    _, _, subject_id, category_id, page_raw = callback.data.split(":")
    subject_id, category_id, page = int(subject_id), int(category_id), int(page_raw)

    items = await files_service.files_by_category(
        db_session, subject_id, category_id or None, only_approved=True, limit=200
    )
    if not items:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="📤 كن أول من يرفع هنا", callback_data=_cd(f"upload:cat:{subject_id}")
        ))
        builder.row(back_button("⬅️ المادة", _cd(f"subj:view:{subject_id}")))
        await _edit(callback, "📭 لا توجد ملفات منشورة في هذه الفئة بعد.", builder.as_markup())
        await callback.answer()
        return

    visible, total_pages = slice_page(items, page + 1)
    builder = InlineKeyboardBuilder()
    for item in visible:
        label = f"{'📌 ' if item.is_pinned else ''}{escape(item.title[:38])}"
        builder.row(InlineKeyboardButton(text=label, callback_data=_cd(f"file:view:{item.id}")))
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text="◀️", callback_data=_cd(f"cat:view:{subject_id}:{category_id}:{page - 1}")
            ))
        nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
        if page + 1 < total_pages:
            nav_row.append(InlineKeyboardButton(
                text="▶️", callback_data=_cd(f"cat:view:{subject_id}:{category_id}:{page + 1}")
            ))
        builder.row(*nav_row)
    builder.row(back_button("⬅️ المادة", _cd(f"subj:view:{subject_id}")))

    await _edit(callback, texts.header("library", subtitle=f"📂 الملفات ({len(items)}) — اختر ملفًا:"), builder.as_markup())
    await callback.answer()


# ------------------------------------------------------------------- الملف
@router.callback_query(F.data.startswith("file:view:"))
async def cb_file_view(callback: CallbackQuery, db_user: User, db_session) -> None:
    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if item.storage_message_id:
        builder.row(InlineKeyboardButton(text="⬇️ تحميل الملف", callback_data=_cd(f"file:dl:{item.id}")))
    if item.external_url:
        builder.row(InlineKeyboardButton(text="🔗 فتح الرابط", url=item.external_url))
    if item.text_extracted:
        builder.row(InlineKeyboardButton(text="📝 ملخص الملف", callback_data=_cd(f"file:sum:{item.id}")))
    if db_user is not None and db_user.is_staff():
        builder.row(InlineKeyboardButton(
            text="📌 إلغاء التثبيت" if item.is_pinned else "📌 تثبيت في الأعلى",
            callback_data=_cd(f"file:pin:{item.id}"),
        ))
    builder.row(back_button("⬅️ رجوع", _cd(f"subj:view:{item.subject_id}")))

    uploader_name = item.uploader.display_name if item.uploader else "—"
    rows = [
        ("📘", "المادة", escape(item.subject.name if item.subject else "—")),
        ("📁", "الفئة", escape(item.category.name if item.category else "—")),
        ("👤", "الرافع", escape(uploader_name)),
        ("📦", "الحجم", human_size(item.file_size)),
        ("📥", "التحميلات", item.downloads_count),
        ("🗓️", "التاريخ", item.created_at.strftime("%Y-%m-%d") if item.created_at else "—"),
    ]
    if item.tag_list:
        rows.append(("🏷️", "الوسوم", ", ".join(escape(t) for t in item.tag_list[:8])))
    text = (
        texts.header("file", escape(item.title))
        + f"\n\n{escape(item.description or 'بدون وصف')}\n\n"
        + texts.info_block(rows)
    )
    await _edit(callback, text, builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("file:dl:"))
async def cb_file_download(callback: CallbackQuery, db_user: User, db_session, bot) -> None:
    from ..services.storage import StorageError

    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None or not item.storage_message_id:
        await callback.answer("الملف غير متاح للتحميل", show_alert=True)
        return

    from ..services import store

    storage = store.get_backend()
    try:
        await storage.forward_to(callback.from_user.id, item.storage_message_id,
                                 caption=file_caption(item, item.uploader))
    except StorageError as exc:
        await db_session.commit()
        await callback.answer(str(exc), show_alert=True)
        return

    await files_service.record_download(db_session, item, db_user)
    await db_session.commit()
    await callback.answer("✅ تم إرسال الملف")


@router.callback_query(F.data.startswith("files:recent:"))
async def cb_recent(callback: CallbackQuery, db_session) -> None:
    items = await search.recent_files(db_session, limit=12)
    builder = InlineKeyboardBuilder()
    if not items:
        builder.row(InlineKeyboardButton(text="لا توجد ملفات بعد", callback_data="noop:page"))
    for item in items:
        builder.row(InlineKeyboardButton(
            text=f"🆕 {escape(item.title[:38])}", callback_data=_cd(f"file:view:{item.id}")
        ))
    builder.row(back_button(callback_data="menu:main"))
    await _edit(callback, texts.header("recent", subtitle="آخر ما أُضيف للمكتبة:"), builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("file:pin:"))
async def cb_pin_file(callback: CallbackQuery, db_user: User, db_session) -> None:
    """تثبيت/إلغاء تثبيت ملف — للمشرفين والأدمن."""
    if db_user is None or not db_user.is_staff():
        await callback.answer("🔒 للمشرفين فقط", show_alert=True)
        return

    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return

    item.is_pinned = not item.is_pinned
    await db_session.commit()
    await callback.answer("📌 تم إلغاء التثبيت" if not item.is_pinned else "📌 تم التثبيت في الأعلى")
