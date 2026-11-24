"""لوحة الإدارة: إحصائيات، مراجعة الملفات، إدارة المواد، إدارة الصلاحيات، النسخ الاحتياطي."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from .. import texts
from ..config import settings
from ..models import (
    AcademicYear,
    Department,
    FileItem,
    FileStatus,
    Level,
    Semester,
    Subject,
    User,
    UserRole,
)
from ..services import backup, catalog, files as files_service
from ..services import settings_service, store
from ..services import users as users_service
from ..services.files import human_size
from ..services.users import PermissionDenied
from ..states import AdminFlow
from .helpers import ProgressReporter, back_button, escape, role_label

log = logging.getLogger(__name__)
router = Router(name="admin")



# ------------------------------------------------------------- حراس الصلاحيات
def _staff(user: User) -> bool:
    return user.level >= 1


def _admin(user: User) -> bool:
    return user.level >= 2


def _owner(user: User) -> bool:
    """المالك = المستوى الأعلى (3).

    **خطأ حقيقي:** كانت تقارن `role == "owner"` بينما بقية المشروع كله
    يستخدم `level >= 3`. فلو كان المالك مسجّلًا بدور `admin` ومستوى 3 (وهو
    ما يفعله الترقية بالمستوى) لاختلف حكم الشاشتين على الشخص نفسه: يفتح
    لوحة الإدارة ثم يُرفض من عملية يملكها. الآن مصدر واحد للحكم.
    """
    return (getattr(user, "level", 0) or 0) >= 3


async def _deny(message_like, text: str) -> None:
    if isinstance(message_like, CallbackQuery):
        await message_like.answer(text, show_alert=True)
    else:
        await message_like.answer(text)


def admin_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 الإحصائيات", callback_data="adm:stats"),
        InlineKeyboardButton(text="⏳ المراجعة", callback_data="adm:pending:0"),
    )
    builder.row(
        InlineKeyboardButton(text="📚 إدارة المواد", callback_data="adm:subjects"),
        InlineKeyboardButton(text="👥 المستخدمون", callback_data="adm:users:0"),
    )
    builder.row(
        InlineKeyboardButton(text="🧠 كويز جديد", callback_data="adm:quiz:new"),
        InlineKeyboardButton(text="🪄 توليد تلقائي", callback_data="adm:quiz:auto"),
    )
    builder.row(
        InlineKeyboardButton(text="💾 نسخة احتياطية", callback_data="adm:backup"),
        InlineKeyboardButton(text="📦 تصدير Excel", callback_data="adm:export:xlsx"),
    )
    builder.row(InlineKeyboardButton(text="📄 تصدير JSON", callback_data="adm:export:json"))
    builder.row(
        InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="adm:settings"),
        InlineKeyboardButton(text="📢 رسالة جماعية", callback_data="adm:broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text="🩺 تقرير الصحة", callback_data="adm:health:full"),
        InlineKeyboardButton(text="🔌 فحص التخزين", callback_data="adm:health"),
    )
    builder.row(back_button(callback_data="menu:main"))
    return builder


async def cmd_admin(message: Message, db_user: User) -> None:
    if not _staff(db_user):
        await _deny(message, "🔒 هذه الأوامر للمشرفين والأدمن فقط.")
        return
    await message.answer(
        f"🛠️ <b>لوحة الإدارة</b>\nصلاحيّتك: {role_label(db_user.role)}",
        reply_markup=admin_menu().as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(callback: CallbackQuery, db_user: User) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return
    await callback.message.edit_text("🛠️ لوحة الإدارة", reply_markup=admin_menu().as_markup())
    await callback.answer()


# ------------------------------------------------------------------ الإحصائيات
@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return

    stats = await files_service.library_stats(db_session)
    active = await users_service.active_last_days(db_session, 7)
    top_subjects = await files_service.top_subjects(db_session, limit=5)

    lines = [
        texts.header("admin", "إحصائيات المكتبة", subtitle="📊 نظرة عامة"),
        texts.info_row("👥", "المستخدمون", f"{stats['users']} (نشطون آخر 7 أيام: {active})"),
        texts.info_row("📘", "المواد", stats['subjects']),
        texts.info_row("📄", "الملفات",
                       f"{stats['files_total']} (منشورة {stats['files_approved']} / قيد المراجعة {stats['files_pending']})"),
        texts.info_row("📥", "التحميلات", stats['downloads']),
        texts.info_row("📦", "حجم التخزين", human_size(stats['storage_bytes'])),
    ]
    if top_subjects:
        lines.append("\n🏆 <b>أكثر المواد تحميلًا</b>")
        for subject, downloads in top_subjects:
            lines.append(f"• {escape(subject.name)} — {downloads}")

    builder = InlineKeyboardBuilder()
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))
    await callback.message.answer("\n".join(lines), reply_markup=builder.as_markup(),
                                  parse_mode="HTML")
    await callback.answer()


# ------------------------------------------------------------- مراجعة الملفات
@router.callback_query(F.data.startswith("adm:pending:"))
async def cb_pending(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return

    page = int(callback.data.split(":")[-1])
    items = await files_service.pending_files(db_session, limit=50)
    if not items:
        builder = InlineKeyboardBuilder()
        builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))
        await callback.message.edit_text(
            "✅ لا توجد ملفات بانتظار المراجعة.", reply_markup=builder.as_markup(), parse_mode="HTML"
        )
        await callback.answer()
        return

    current = items[min(page, len(items) - 1)]
    subject_name = escape(current.subject.name if current.subject else "—")
    uploader = escape(current.uploader.display_name if current.uploader else "—")

    text = (
        f"⏳ <b>مراجعة ملف</b> ({min(page, len(items) - 1) + 1}/{len(items)})\n\n"
        f"📄 {escape(current.title)}\n"
        f"📘 المادة: {subject_name}\n"
        f"📁 الفئة: {escape(current.category.name if current.category else '—')}\n"
        f"👤 الرافع: {uploader}\n"
        f"📦 الحجم: {human_size(current.file_size)}\n"
        f"📝 الوصف: {escape(current.description or '—')}\n"
        f"🏷️ الوسوم: {escape(current.tags or '—')}"
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ قبول", callback_data=f"adm:ok:{current.id}"),
        InlineKeyboardButton(text="❌ رفض", callback_data=f"adm:no:{current.id}"),
    )
    builder.row(
        InlineKeyboardButton(text="👁️ معاينة", callback_data=f"file:view:{current.id}"),
        InlineKeyboardButton(text="🗑️ حذف نهائي", callback_data=f"adm:del:{current.id}"),
    )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:pending:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}", callback_data="noop:page"))
    if page + 1 < len(items):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:pending:{page + 1}"))
    builder.row(*nav)
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


async def _load_file(db_session, file_id: int):
    item = await files_service.get_file(db_session, file_id)
    if item is None or item.status == FileStatus.DELETED.value:
        return None
    return item


@router.callback_query(F.data.startswith("adm:ok:"))
async def cb_approve(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return
    item = await _load_file(db_session, int(callback.data.split(":")[-1]))
    if item is None:
        await callback.answer("الملف غير متاح", show_alert=True)
        return

    await files_service.set_status(db_session, item, FileStatus.APPROVED, db_user.id, "قبول")

    await db_session.commit()
    await callback.answer("✅ تم قبول الملف")
    await callback.message.edit_text(f"✅ تم قبول: {escape(item.title)}")


@router.callback_query(F.data.startswith("adm:no:"))
async def cb_reject(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return
    item = await _load_file(db_session, int(callback.data.split(":")[-1]))
    if item is None:
        await callback.answer("الملف غير متاح", show_alert=True)
        return

    await files_service.set_status(db_session, item, FileStatus.REJECTED, db_user.id, "رفض")
    await db_session.commit()
    await callback.answer("❌ تم رفض الملف")
    await callback.message.edit_text(f"❌ تم رفض: {escape(item.title)}")


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_delete_file(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 الحذف النهائي للأدمن والمالك فقط")
        return
    item = await _load_file(db_session, int(callback.data.split(":")[-1]))
    if item is None:
        await callback.answer("الملف غير متاح", show_alert=True)
        return

    await files_service.soft_delete(db_session, item, db_user.id, "حذف من لوحة الإدارة")
    removed = await store.get_backend().delete(item.storage_message_id)
    await db_session.commit()
    await callback.answer(f"🗑️ تم الحذف{'' if removed else ' (بقي المنشور في قناة التخزين)'}")
    await callback.message.edit_text(f"🗑️ تم حذف: {escape(item.title)}")


# ------------------------------------------------------------ إدارة المواد
@router.callback_query(F.data == "adm:subjects")
async def cb_subjects_admin(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return

    departments = await catalog.list_departments(db_session)
    subjects = await catalog.list_subjects(db_session, include_inactive=True)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ قسم جديد", callback_data="adm:new:dept"))
    for dept in departments[:10]:
        builder.row(InlineKeyboardButton(
            text=f"➕ مادة في {escape(dept.name[:20])}", callback_data=f"adm:new:subj:{dept.id}"
        ))
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))

    text = "📚 <b>إدارة المواد</b>\n\n"
    text += f"الأقسام: {len(departments)} | المواد: {len(subjects)}\n\n"
    if subjects:
        text += "<b>المواد الحالية</b>\n"
        for subject in subjects[:25]:
            flag = "" if subject.is_active else " (موقوفة)"
            text += f"• {escape(subject.name)}{flag}\n"
        text += "\nلإيقاف/حذف مادة: /subject_off رقم_المادة"

    await callback.message.edit_text(text[:4000], reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "adm:new:dept")
async def cb_new_dept(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return
    await state.set_state(AdminFlow.waiting_department_name)
    await callback.message.answer("✏️ اكتب اسم القسم الجديد:")
    await callback.answer()


@router.message(F.text, AdminFlow.waiting_department_name)
async def on_dept_name(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    await state.clear()
    name = (message.text or "").strip()
    dept = await catalog.get_or_create(db_session, Department, name[:120])
    await db_session.commit()
    await message.answer(f"✅ تم إضافة القسم: {escape(dept.name)}", parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:new:subj:"))
async def cb_new_subject(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return
    dept_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminFlow.waiting_subject_name)
    await state.update_data(department_id=dept_id)
    await callback.message.answer(
        "✏️ اكتب بيانات المادة بهذا الترتيب (كل سطر بيان):\n"
        "<code>الاسم\nالمستوى\nالسنة\nالفصل</code>\n"
        "مثال:\n<code>رياضيات 1\nالفرقة الأولى\n2025-2026\nالترم الأول</code>\n\n"
        "يمكن كتابة الاسم فقط في سطر واحد.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(F.text, AdminFlow.waiting_subject_name)
async def on_subject_name(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    dept_id = data.get("department_id")
    lines = [line.strip() for line in (message.text or "").splitlines() if line.strip()]
    if not lines:
        await message.answer("لم تكتب شيئًا.")
        return

    name = lines[0][:180]
    level = await catalog.get_or_create(db_session, Level, lines[1][:120]) if len(lines) > 1 else None
    year = await catalog.get_or_create(db_session, AcademicYear, lines[2][:40]) if len(lines) > 2 else None
    semester = await catalog.get_or_create(db_session, Semester, lines[3][:60]) if len(lines) > 3 else None

    subject = await catalog.create_subject(
        db_session, name, dept_id,
        level_id=level.id if level else None,
        year_id=year.id if year else None,
        semester_id=semester.id if semester else None,
    )
    await db_session.commit()
    await message.answer(f"✅ تم إضافة المادة: {escape(subject.name)} (رقمها {subject.id})",
                         parse_mode="HTML")


async def cmd_subject_off(message: Message, command: CommandObject, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(message, "🔒 للمشرفين فقط.")
        return
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("الاستخدام: /subject_off 12")
        return
    subject = await db_session.get(Subject, int(raw))
    if subject is None:
        await message.answer("المادة غير موجودة.")
        return
    subject.is_active = not subject.is_active
    await db_session.commit()
    await message.answer(
        f"{'✅ تم تفعيل' if subject.is_active else '⏸️ تم إيقاف'} المادة: {escape(subject.name)}",
        parse_mode="HTML",
    )


# --------------------------------------------------------------- المستخدمون
@router.callback_query(F.data.startswith("adm:users:"))
async def cb_users(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    users = await users_service.list_users(db_session, limit=100)
    builder = InlineKeyboardBuilder()
    if not users:
        builder.row(InlineKeyboardButton(text="لا يوجد مستخدمون", callback_data="noop:page"))
    for user in users[:30]:
        builder.row(InlineKeyboardButton(
            text=f"{escape(user.display_name[:20])} — {role_label(user.role)}",
            callback_data=f"adm:user:{user.telegram_id}",
        ))
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))
    await callback.message.edit_text(
        f"👥 <b>المستخدمون ({len(users)})</b>", reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def cb_user_detail(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    telegram_id = int(callback.data.split(":")[-1])
    target = await users_service.get_by_telegram(db_session, telegram_id)
    if target is None:
        await callback.answer("المستخدم غير موجود", show_alert=True)
        return

    text = (
        f"👤 <b>{escape(target.display_name)}</b>\n"
        f"المعرّف: <code>{target.telegram_id}</code>\n"
        f"اليوزر: {('@' + target.username) if target.username else '—'}\n"
        f"الصلاحية: {role_label(target.role)}\n"
        f"الرفع: {target.uploads_count} | التحميل: {target.downloads_count}\n"
        f"الحالة: {'⛔ محظور' if target.is_banned else '✅ نشط'}"
    )
    builder = InlineKeyboardBuilder()
    if target.role != UserRole.OWNER.value:
        builder.row(
            InlineKeyboardButton(text="👮 ترقية لمشرف", callback_data=f"adm:role:{telegram_id}:moderator"),
            InlineKeyboardButton(text="🛡️ ترقية لأدمن", callback_data=f"adm:role:{telegram_id}:admin"),
        )
        builder.row(
            InlineKeyboardButton(text="🎓 تنزيل لطالب", callback_data=f"adm:role:{telegram_id}:student"),
            InlineKeyboardButton(
                text="⛔ حظر" if not target.is_banned else "✅ فك حظر",
                callback_data=f"adm:ban:{telegram_id}:{0 if target.is_banned else 1}",
            ),
        )
    builder.row(back_button("⬅️ المستخدمون", "adm:users:0"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm:role:"))
async def cb_set_role(callback: CallbackQuery, db_user: User, db_session) -> None:
    # بيانات الأزرار قابلة للتزوير من عملاء معدّلين — تحقّق قبل أي فعل
    try:
        _, _, telegram_id, role = callback.data.split(":")
        target_id, new_role = int(telegram_id), UserRole(role)
    except ValueError:
        await callback.answer("⚠️ بيانات زر غير صالحة", show_alert=True)
        return
    target = await users_service.get_by_telegram(db_session, target_id)
    if target is None:
        await callback.answer("المستخدم غير موجود", show_alert=True)
        return
    try:
        await users_service.set_role(db_session, db_user, target, new_role)
        await db_session.commit()
    except PermissionDenied as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"✅ أصبحت صلاحية {target.display_name}: {role_label(role)}")
    await callback.message.edit_text(f"✅ تم تحديث صلاحية {escape(target.display_name)} إلى {role_label(role)}",
                                     parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:ban:"))
async def cb_ban(callback: CallbackQuery, db_user: User, db_session) -> None:
    try:
        _, _, telegram_id, flag = callback.data.split(":")
        target_id = int(telegram_id)
    except ValueError:
        await callback.answer("⚠️ بيانات زر غير صالحة", show_alert=True)
        return
    target = await users_service.get_by_telegram(db_session, target_id)
    if target is None:
        await callback.answer("المستخدم غير موجود", show_alert=True)
        return
    try:
        await users_service.set_banned(db_session, db_user, target, flag == "1")
        await db_session.commit()
    except PermissionDenied as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("✅ تم التحديث")
    await callback.message.edit_text("✅ تم تحديث حالة المستخدم.")


async def cmd_promote(message: Message, command: CommandObject, db_user: User, db_session) -> None:
    """/promote @username moderator|admin|student — للمالك والأدمن."""
    if not _admin(db_user):
        await _deny(message, "🔒 للأدمن والمالك فقط.")
        return
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer("الاستخدام: <code>/promote @user moderator</code>", parse_mode="HTML")
        return
    identifier, role_name = parts[0], parts[1].lower()
    if role_name not in {r.value for r in UserRole}:
        await message.answer("الصلاحية يجب أن تكون: student / moderator / admin / owner")
        return
    target = await users_service.resolve_target(db_session, identifier)
    if target is None:
        await message.answer("لم أجد هذا المستخدم — يجب أن يكون قد فتح البوت مرة واحدة.")
        return
    try:
        await users_service.set_role(db_session, db_user, target, UserRole(role_name))
        await db_session.commit()
    except PermissionDenied as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await message.answer(f"✅ {escape(target.display_name)} أصبح {role_label(role_name)}", parse_mode="HTML")


async def cmd_demote(message: Message, command: CommandObject, db_user: User, db_session) -> None:
    """/demote @username — ينزل المستخدم إلى طالب."""
    if not _admin(db_user):
        await _deny(message, "🔒 للأدمن والمالك فقط.")
        return
    identifier = (command.args or "").strip()
    target = await users_service.resolve_target(db_session, identifier) if identifier else None
    if target is None:
        await message.answer("الاستخدام: <code>/demote @user</code>", parse_mode="HTML")
        return
    try:
        await users_service.set_role(db_session, db_user, target, UserRole.STUDENT)
        await db_session.commit()
    except PermissionDenied as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await message.answer(f"✅ {escape(target.display_name)} أصبح طالبًا", parse_mode="HTML")


# ------------------------------------------------------------ النسخ الاحتياطي
@router.callback_query(F.data == "adm:backup")
async def cb_backup(callback: CallbackQuery, db_user: User) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    if not settings.is_sqlite or settings.sqlite_path is None:
        await callback.answer("قاعدة البيانات ليست SQLite — استخدم pg_dump", show_alert=True)
        return
    try:
        target = backup.sqlite_backup(settings.sqlite_path, settings.backup_dir, settings.backup_keep)
    except FileNotFoundError as exc:
        await callback.answer(f"⚠️ {exc}", show_alert=True)
        return

    await callback.answer("💾 تم إنشاء النسخة")
    await callback.message.answer("💾 تم إنشاء نسخة احتياطية.")
    try:
        await callback.message.answer_document(
            FSInputFile(str(target)), caption=f"💾 {target.name}"
        )
    except Exception as exc:
        log.warning("تعذّر إرسال ملف النسخة: %s", exc)
        await callback.message.answer(f"⚠️ تعذّر إرسال الملف، لكنه محفوظ في: {target}")


@router.callback_query(F.data == "adm:health")
async def cb_health(callback: CallbackQuery, db_user: User) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return
    ok, message_text = await store.get_backend().healthcheck()
    await callback.answer(("✅ " if ok else "❌ ") + message_text, show_alert=True)


# ------------------------------------------------------------- إنشاء كويز
@router.callback_query(F.data == "adm:quiz:new")
async def cb_new_quiz(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return
    await state.set_state(AdminFlow.waiting_quiz_question)
    await state.update_data(questions=[])
    await callback.message.answer(
        "🧠 <b>كويز جديد</b>\n"
        "اكتب عنوان الكويز أولًا، ثم سنضيف الأسئلة.\n"
        "للإنهاء في أي وقت اضغط «إلغاء»"
    )
    await callback.answer()


@router.message(F.text, AdminFlow.waiting_quiz_question)
async def on_quiz_title(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    data = await state.get_data()
    if "title" not in data:
        await state.update_data(title=(message.text or "").strip()[:180])
        await message.answer(
            "✏️ اكتب نص السؤال الأول:\n(أو أرسل «توليد» لتوليد أسئلة تلقائيًا من نص تلصقه)"
        )
        return

    if (message.text or "").strip().lower() == "تم":
        await _finish_quiz(message, db_user, db_session, state)
        return

    await state.update_data(current_question=(message.text or "").strip()[:1000])
    await state.set_state(AdminFlow.waiting_quiz_options)
    await message.answer("✏️ اكتب الخيارات، كل خيار في سطر (2 إلى 6 خيارات):")


@router.message(F.text, AdminFlow.waiting_quiz_options)
async def on_quiz_options(message: Message, state: FSMContext) -> None:
    options = [line.strip() for line in (message.text or "").splitlines() if line.strip()]
    if len(options) < 2:
        await message.answer("⚠️ اكتب خيارين على الأقل، كل خيار في سطر.")
        return
    await state.update_data(current_options=options[:6])
    await state.set_state(AdminFlow.waiting_quiz_answer)
    await message.answer(f"✏️ اكتب رقم الإجابة الصحيحة (من 1 إلى {len(options[:6])}):")


@router.message(F.text, AdminFlow.waiting_quiz_answer)
async def on_quiz_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    options = data.get("current_options") or []
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(options):
        await message.answer(f"⚠️ اكتب رقمًا بين 1 و {len(options)}.")
        return

    questions = list(data.get("questions") or [])
    questions.append({
        "text": data.get("current_question"),
        "options": options,
        "correct_index": int(raw) - 1,
    })
    await state.update_data(questions=questions, current_question=None, current_options=None)
    await state.set_state(AdminFlow.waiting_quiz_question)
    await message.answer(
        f"✅ تمت إضافة السؤال ({len(questions)}).\n"
        "اكتب السؤال التالي، أو أرسل «تم» لحفظ الكويز."
    )


async def _finish_quiz(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    from ..services import quiz as quiz_service

    data = await state.get_data()
    await state.clear()
    questions = data.get("questions") or []
    if not questions:
        await message.answer("⚠️ لم تُضف أي سؤال.")
        return

    quiz = await quiz_service.create_quiz(
        db_session,
        title=data.get("title") or "كويز",
        created_by=db_user.id,
        questions=[(q["text"], q["options"], q["correct_index"], None) for q in questions],
    )
    await db_session.commit()
    await message.answer(
        f"✅ تم إنشاء الكويز: {escape(quiz.title)}\nعدد الأسئلة: {len(questions)}\nرقمه: {quiz.id}",
        parse_mode="HTML",
    )



# ------------------------------------------------------- تقرير الصحة والمراقبة
@router.callback_query(F.data == "adm:health:full")
async def cb_health_full(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return

    from ..services.monitor import health_report, human_uptime

    report = await health_report(db_session)
    header = "🩺 <b>تقرير الصحة</b> — " + ("✅ كل شيء يعمل" if report.ok else "⚠️ توجد ملاحظات")
    # أسطر الفحص تُبنى من أسماء ملفات/قيم قد تحتوي `<` أو `&` — تهريبها
    # يمنع فشل الإرسال بسبب وسم HTML غير صالح (خطأ يظهر كرسالة لا تُرسل).
    body = "\n".join(escape(line) for line in report.lines)
    if report.issues:
        body += "\n\n<b>ملاحظات تحتاج انتباهًا</b>\n" + "\n".join(f"• {escape(i)}" for i in report.issues)

    builder = InlineKeyboardBuilder()
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))
    await callback.message.edit_text(
        f"{header}\n\n{body}\n\n🕓 التشغيل: {human_uptime()}"[:4000],
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


# ------------------------------------------------------------------- البث
@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast_start(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 البث للأدمن والمالك فقط")
        return
    await state.set_state(AdminFlow.waiting_setting_value)
    await state.update_data(purpose="broadcast")
    await callback.message.answer(
        "📢 <b>رسالة جماعية</b>\nاكتب نص الرسالة الآن.\n"
        "بعدها سيُطلب منك اختيار الجمهور.\nللإلغاء: اضغط «إلغاء»"
    )
    await callback.answer()


@router.message(F.text, AdminFlow.waiting_setting_value)
async def on_setting_value(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    data = await state.get_data()
    purpose = data.get("purpose")

    if purpose == "broadcast":
        await state.update_data(broadcast_text=(message.text or "")[:4000])
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="👥 الجميع", callback_data="adm:bc:all"),
            InlineKeyboardButton(text="🛡️ المشرفون فقط", callback_data="adm:bc:staff"),
        )
        builder.row(InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:bc:cancel"))
        await message.answer("📢 اختر الجمهور:", reply_markup=builder.as_markup())
        return

    if purpose == "setting":
        key = data.get("setting_key")
        await state.clear()
        new_value = (message.text or "").strip()

        # تحقق من القيم الرقمية والمنطقية قبل الحفظ
        if key in settings_service.INT_KEYS:
            if not new_value.lstrip("-").isdigit():
                await message.answer("⚠️ القيمة يجب أن تكون رقمًا.")
                return
        elif key in settings_service.BOOL_KEYS:
            if new_value.lower() not in {"true", "false", "1", "0", "نعم", "لا"}:
                await message.answer("⚠️ القيمة يجب أن تكون true أو false.")
                return

        await settings_service.set(db_session, key, new_value)
        await db_session.commit()
        await message.answer(
            f"✅ تم حفظ <code>{escape(key)}</code> = {escape(new_value)} — التغيير سارٍ فورًا",
            parse_mode="HTML",
        )
        return

    await state.clear()


@router.callback_query(F.data.startswith("adm:bc:"))
async def cb_broadcast_send(callback: CallbackQuery, db_user: User, db_session, state: FSMContext) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    target = callback.data.split(":")[-1]
    data = await state.get_data()
    await state.clear()

    if target == "cancel":
        await callback.message.edit_text("❌ تم إلغاء الرسالة الجماعية.")
        await callback.answer()
        return

    text = data.get("broadcast_text")
    if not text:
        await callback.answer("لا توجد رسالة", show_alert=True)
        return

    stmt = select(User.telegram_id).where(User.is_banned.is_(False))
    if target == "staff":
        stmt = stmt.where(User.role.in_([
            UserRole.MODERATOR.value, UserRole.ADMIN.value, UserRole.OWNER.value,
        ]))

    recipients = list((await db_session.scalars(stmt)).all())
    sent = failed = 0
    for telegram_id in recipients:
        try:
            # نص البث يكتبه الأدمن: نُهرّبه حتى لا يكسر وسوم HTML فيفشل
            # الإرسال لبعض المستخدمين بصمت (نراه كـ«فشل: N» بلا سبب).
            await callback.message.bot.send_message(
                telegram_id, f"📢 <b>إعلان</b>\n\n{escape(text)}",
                parse_mode="HTML")
            sent += 1
        except Exception as exc:
            failed += 1
            log.debug("فشل إرسال الإعلان إلى %s: %s", telegram_id, exc)

    await callback.message.edit_text(f"📢 أُرسلت إلى {sent} مستخدم (فشل: {failed}).")
    await callback.answer(f"✅ أُرسلت إلى {sent}")


# ----------------------------------------------------------------- الإعدادات
SETTINGS_LABELS = {
    "bot_name": "اسم البوت",
    "welcome_message": "رسالة الترحيب",
    "require_review": "المراجعة الإلزامية (true/false)",
    "maintenance": "وضع الصيانة (true/false)",
}


@router.callback_query(F.data == "adm:settings")
async def cb_settings(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    values = await settings_service.get_effective(db_session)

    builder = InlineKeyboardBuilder()
    for key, label in SETTINGS_LABELS.items():
        current = values.get(key)
        current = "—" if current in (None, "") else current
        builder.row(InlineKeyboardButton(
            text=f"✏️ {label}: {str(current)[:24]}", callback_data=f"adm:set:{key}"
        ))
    builder.row(
        InlineKeyboardButton(text="🔄 إعادة فهرسة البحث", callback_data="adm:reindex"),
        InlineKeyboardButton(text="🔎 إعادة OCR", callback_data="adm:reocr"),
    )
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))

    await callback.message.edit_text(
        "⚙️ <b>الإعدادات</b>\nاضغط على أي بند لتعديله:",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:set:"))
async def cb_edit_setting(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return
    key = callback.data.split(":")[-1]
    if key not in SETTINGS_LABELS:
        await callback.answer("مفتاح غير معروف", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_setting_value)
    await state.update_data(purpose="setting", setting_key=key)
    await callback.message.answer(f"✏️ اكتب القيمة الجديدة لـ«{SETTINGS_LABELS[key]}»:")
    await callback.answer()


# ------------------------------------------------------- إعادة الفهرسة والـ OCR
@router.callback_query(F.data == "adm:reindex")
async def cb_reindex(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يعيد بناء فهرس البحث النصي والدلالي لكل الملفات."""
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    from ..services import files as files_service

    notice = await callback.message.answer("🔄 جارٍ إعادة الفهرسة…")
    files = (await db_session.scalars(select(FileItem))).all()
    count = 0
    for item in files:
        existing = await files_service.get_indexed_text(db_session, item.id)
        await files_service.index_file(db_session, item, extracted_text=existing)
        count += 1
    await db_session.commit()
    await notice.edit_text(f"✅ أُعيدت فهرسة {count} ملف (نصيًا ودلاليًا).")
    await callback.answer(f"✅ {count} ملف")


@router.callback_query(F.data == "adm:reocr")
async def cb_reocr(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يعيد محاولة OCR للملفات التي فشل استخراج النص منها."""
    if not _admin(db_user):
        await _deny(callback, "🔒 للأدمن والمالك فقط")
        return

    from ..services import files as files_service, ocr as ocr_service, store

    if not ocr_service.is_available():
        await callback.answer(ocr_service.status_text(), show_alert=True)
        return

    candidates = (
        await db_session.scalars(
            select(FileItem).where(
                FileItem.file_kind == "pdf",
                FileItem.text_extracted.is_(False),
                FileItem.status != FileStatus.DELETED.value,
            )
        )
    ).all()

    if not candidates:
        await callback.answer("لا توجد ملفات PDF بلا نص", show_alert=True)
        return

    # عدّاد حي بعدد الملفات — إعادة OCR جماعية قد تطول لدقائق،
    # والقراءة في خيط عامل حتى لا يتجمّد البوت أثناءها.
    reporter = ProgressReporter(
        callback.message, "إعادة OCR للملفات بلا نص",
        total=len(candidates), stage="بدء المعالجة")
    await reporter.start()
    storage = store.get_backend()
    done = 0
    try:
        for idx, item in enumerate(candidates, 1):
            reporter.set_stage(f"معالجة ملف {idx} من {len(candidates)}")
            if not item.tg_file_id:
                reporter.report(idx)
                continue
            try:
                data = await storage.fetch_bytes(item.tg_file_id)
            except Exception as exc:
                log.warning("تعذّر تنزيل %s للـ OCR: %s", item.id, exc)
                reporter.report(idx)
                continue
            result = await asyncio.to_thread(
                ocr_service.extract_text_from_pdf,
                data, langs=settings.ocr_langs,
                dewarp=settings.ocr_dewarp)
            reporter.report(idx)
            if result.text.strip():
                await files_service.index_file(db_session, item, extracted_text=result.text)
                item.text_extracted = True
                done += 1
    finally:
        await reporter.finish()
    await db_session.commit()
    await callback.message.answer(
        "✅ <b>اكتملت إعادة OCR</b>\n"
        f"📄 نجح: {done} من {len(candidates)} ملف.",
        parse_mode="HTML",
    )
    await callback.answer(f"✅ {done} ملف")


# ------------------------------------------------- توليد كويز تلقائيًا من ملف
@router.callback_query(F.data == "adm:quiz:auto")
async def cb_auto_quiz(callback: CallbackQuery, db_user: User, db_session, state: FSMContext) -> None:
    """يولّد أسئلة «أكمل الفراغ» من نص ملف PDF مفهرس."""
    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return

    candidates = (
        await db_session.scalars(
            select(FileItem)
            .where(FileItem.text_extracted.is_(True), FileItem.status == FileStatus.APPROVED.value)
            .order_by(FileItem.created_at.desc())
            .limit(30)
        )
    ).all()

    if not candidates:
        await callback.answer("لا توجد ملفات بنص مستخرج", show_alert=True)
        return

    await state.set_state(AdminFlow.waiting_quiz_question)
    await state.update_data(purpose="autoquiz")

    builder = InlineKeyboardBuilder()
    for item in candidates:
        builder.row(InlineKeyboardButton(
            text=f"📄 {escape(item.title[:34])}", callback_data=f"adm:autoq:{item.id}"
        ))
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))
    await callback.message.edit_text(
        "🧠 <b>توليد كويز تلقائيًا</b>\nاختر ملفًا (يُستخدم نصه المستخرج):",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:autoq:"))
async def cb_auto_quiz_build(callback: CallbackQuery, db_user: User, db_session, state: FSMContext) -> None:
    from ..services import files as files_service
    from ..services import quiz as quiz_service

    if not _staff(db_user):
        await _deny(callback, "🔒 للمشرفين فقط")
        return

    file_id = int(callback.data.split(":")[-1])
    await state.clear()

    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return

    content = await files_service.get_indexed_text(db_session, file_id)
    if not content or not content.strip():
        await callback.answer("لا يوجد نص مستخرج لهذا الملف", show_alert=True)
        return

    generated = quiz_service.generate_from_text(content, count=6, seed=file_id)
    if len(generated) < 2:
        await callback.message.edit_text(
            "⚠️ النص لا يكفي لتوليد أسئلة جيدة (يحتاج جملًا أطول ومفردات متكررة)."
        )
        await callback.answer("النص غير كافٍ", show_alert=True)
        return

    quiz = await quiz_service.create_quiz(
        db_session,
        title=f"كويز تلقائي: {item.title[:120]}",
        subject_id=item.subject_id,
        created_by=db_user.id,
        questions=[(q["text"], q["options"], q["correct_index"], q.get("explanation")) for q in generated],
    )
    await db_session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🧠 تجربة الكويز", callback_data=f"quiz:start:{quiz.id}"))
    builder.row(back_button("⬅️ لوحة الإدارة", "adm:menu"))
    await callback.message.edit_text(
        f"✅ <b>تم توليد الكويز</b>\n"
        f"العنوان: {escape(quiz.title)}\n"
        f"عدد الأسئلة: {len(generated)}\n"
        f"المصدر: {escape(item.title)}",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer(f"✅ {len(generated)} سؤال")


# ------------------------------------------------------------------ التصدير
@router.callback_query(F.data.startswith("adm:export:"))
async def cb_export(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يصدر المكتبة إلى Excel أو JSON ويرسل الملف."""
    if not _admin(db_user):
        await _deny(callback, "🔒 التصدير للأدمن والمالك فقط")
        return

    fmt = callback.data.split(":")[-1]
    if fmt not in {"xlsx", "json"}:
        await callback.answer("صيغة غير مدعومة", show_alert=True)
        return

    from ..services import export as export_service

    notice = await callback.message.answer(f"⏳ جارٍ التصدير إلى {fmt.upper()}…")
    try:
        path = await export_service.export_files(db_session, settings.backup_dir, fmt=fmt)
    except ImportError as exc:
        await notice.edit_text(f"⚠️ مكتبة التصدير غير مثبّتة: {exc}\nثبّتها بـ: pip install openpyxl")
        await callback.answer()
        return
    except Exception as exc:
        log.error("فشل التصدير: %s", exc, exc_info=True)
        await notice.edit_text(f"⚠️ فشل التصدير: {exc}")
        await callback.answer()
        return

    summary = await export_service.collect_summary(db_session)
    await notice.edit_text(
        f"✅ تم تصدير {summary['files_total']} ملف إلى {path.name}"
    )
    try:
        await callback.message.answer_document(FSInputFile(str(path)), caption=f"📦 {path.name}")
    except Exception as exc:
        log.warning("تعذّر إرسال ملف التصدير: %s", exc)
        await callback.message.answer(f"⚠️ الملف جاهز في: {path}")
    await callback.answer("✅ تم التصدير")
