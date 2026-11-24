"""إدارة مواد المستخدم: عرض مواده، تعديل أي حقل، حذف، وإضافة.

**السياسة المتفَق عليها:** صاحب الملف يعدّل ويحذف ما رفعه هو فقط؛
المشرف/الأدمن يعدّلون أي ملف. التفاصيل والأسباب في
`bot/services/files.py::can_edit`.

**لماذا ملف منفصل لا إضافة إلى `library.py`؟** لأن هذا تدفّق كتابة
(FSM بحالات ومدخلات نصية) بينما `library.py` تصفّح قراءة فقط. خلط الاثنين
يجعل تعديل قائمة التصفّح مخاطرة على تدفّق التحرير والعكس.

**شيء مقصود:** كل حقل يُحرَّر في رسالة مستقلة قابلة للإلغاء. لا «معالج
خطوة واحدة» يطلب كل الحقول معًا — لأن المستخدم غالبًا يريد تعديل حقل
واحد، وإجباره على المرور بالباقي يُفقده ما كتبه عند أي خطأ.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton

from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User
from ..services import catalog, files as files_service
from ..states import EditFlow
from .helpers import back_button, cb_data, escape

log = logging.getLogger(__name__)
router = Router(name="materials")

MAX_CALLBACK = 60
PAGE_SIZE = 8


# الحارس انتقل إلى `helpers.cb_data` — نسخة واحدة بدل ثلاث.
_cd = cb_data


def _mode_key(mode: str) -> str:
    return f"quality:{mode}"


#: الحقول التي يمكن تعديلها من الشاشة — التسمية العربية المرتبطة بالمفتاح
FIELD_LABELS: dict[str, str] = {
    "title": "✏️ العنوان",
    "description": "📝 الوصف",
    "tags": "🏷️ الوسوم",
    "category_id": "📁 الفئة",
    "subject_id": "📘 المادة الدراسية",
    "external_url": "🔗 رابط خارجي",
}


# ─────────────────────────────────────────────── شاشة «موادي»
async def _own_files_page(db_session, user_id: int, page: int) -> tuple[str, object]:
    items = await files_service.user_files(db_session, user_id, limit=100)
    total = len(items)
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]

    builder = InlineKeyboardBuilder()
    if not total:
        builder.row(InlineKeyboardButton(
            text="📤 ارفع أول ملف", callback_data="upload:start"))
    for item in chunk:
        label = f"{'📌 ' if item.is_pinned else ''}{item.title[:34]}"
        builder.row(InlineKeyboardButton(
            text=label, callback_data=_cd(f"own:view:{item.id}")))
    if total > PAGE_SIZE:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=_cd(f"own:page:{page-1}")))
        nav.append(InlineKeyboardButton(
            text=f"{page+1}/{(total + PAGE_SIZE - 1)//PAGE_SIZE}", callback_data="noop:page"))
        if start + PAGE_SIZE < total:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=_cd(f"own:page:{page+1}")))
        builder.row(*nav)
    builder.row(back_button(callback_data="menu:main"))

    body = texts.header("materials", subtitle="كل ما رفعته — تعدّله أو تحذفه")
    body += f"\n\n📦 عندك <b>{total}</b> ملف."
    if not total:
        body += "\n\nلسه مرفعتش حاجة. ارفع ملفك الأول من الزر تحت."
    return body, builder.as_markup()


@router.callback_query(F.data == "own:list")
async def cb_own_list(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يفتح «موادي» — يعدّل الرسالة الحالية بدل إضافة رسالة جديدة."""
    await _edit_own_files(callback, db_session, db_user.id, 0)
    await callback.answer()


async def _edit_own_files(callback: CallbackQuery, db_session, user_id: int,
                          page: int) -> None:
    body, markup = await _own_files_page(db_session, user_id, page)
    await callback.message.edit_text(body, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("own:page:"))
async def cb_own_page(callback: CallbackQuery, db_user: User, db_session) -> None:
    page = int(callback.data.split(":")[-1])
    await _edit_own_files(callback, db_session, db_user.id, page)
    await callback.answer()


# ─────────────────────────────────────────── شاشة ملف واحد
@router.callback_query(F.data.startswith("own:view:"))
async def cb_own_view(callback: CallbackQuery, db_user: User, db_session) -> None:
    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return

    if not files_service.can_edit(item, db_user):
        await callback.answer(
            files_service.edit_denied_reason(item, db_user), show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for key, label in FIELD_LABELS.items():
        builder.row(InlineKeyboardButton(
            text=label, callback_data=_cd(f"own:edit:{file_id}:{key}")))
    builder.row(
        InlineKeyboardButton(text="🗑️ حذف الملف", callback_data=_cd(f"own:del:{file_id}")),
    )
    builder.row(back_button("⬅️ موادي", "own:list"))

    rows = [
        ("✏️", "العنوان", escape(item.title)),
        ("📝", "الوصف", escape(item.description or "—")),
        ("🏷️", "الوسوم", escape(item.tags or "—")),
        ("📘", "المادة", escape(item.subject.name if item.subject else "—")),
        ("📁", "الفئة", escape(item.category.name if item.category else "—")),
        ("🔗", "رابط", escape(item.external_url or "—")),
        ("📌", "مثبّت", "نعم" if item.is_pinned else "لا"),
    ]
    body = (texts.header("materials", escape(item.title))
            + "\n\n" + texts.info_block(rows)
            + "\n\n<i>اختر الحقل اللي عايز تعدّله.</i>")
    await callback.message.edit_text(body, reply_markup=builder.as_markup(),
                                     parse_mode="HTML",
                                     disable_web_page_preview=True)
    await callback.answer()


# ────────────────────────────────── بدء تحرير حقل
_EDIT_PROMPTS: dict[str, tuple[str, str]] = {
    "title": ("✏️ <b>العنوان الجديد</b>",
              "اكتب عنوانًا واضحًا (حتى ٢٤٠ حرفًا). مثال: «ملخص الفصل الأول — قانون أوم»."),
    "description": ("📝 <b>الوصف الجديد</b>",
                    "اكتب وصفًا مفيدًا: ماذا يحتوي الملف؟ (حتى ٢٠٠٠ حرف، والأسطر تُحفظ)."),
    "tags": ("🏷️ <b>الوسوم الجديدة</b>",
             "اكتب الوسوم مفصولة بفاصلة. مثال: `رياضيات, تفاضل, فصل أول`"),
    "external_url": ("🔗 <b>الرابط الخارجي</b>",
                     "لازم يبدأ بـ `http://` أو `https://`. اكتب «-» لمسحه."),
}


@router.callback_query(F.data.startswith("own:edit:"))
async def cb_own_edit(callback: CallbackQuery, db_user: User, db_session,
                      state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("طلب غير صالح", show_alert=True)
        return
    file_id, field = int(parts[2]), parts[3]

    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return
    if not files_service.can_edit(item, db_user):
        await callback.answer(
            files_service.edit_denied_reason(item, db_user), show_alert=True)
        return

    await state.update_data(edit_file_id=file_id, edit_field=field)

    # الفئة والمادة: اختيار من قائمة لا كتابة حرة
    if field == "category_id":
        await _show_pick_list(callback, db_session, state, item, kind="category")
        return
    if field == "subject_id":
        await _show_pick_list(callback, db_session, state, item, kind="subject")
        return

    title, hint = _EDIT_PROMPTS.get(field, ("تحرير", "اكتب القيمة الجديدة."))
    await state.set_state(EditFlow.waiting_value)
    await callback.message.answer(
        f"{title}\n\n{hint}\n\n"
        f"<i>القيمة الحالية:</i> <code>{escape(_current_value(item, field))}</code>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("✖️ إلغاء", _cd(f"own:view:{file_id}")))
        .as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


def _current_value(item, field: str) -> str:
    value = getattr(item, field, "")
    if field == "category_id":
        return item.category.name if item.category else "—"
    if field == "subject_id":
        return item.subject.name if item.subject else "—"
    return str(value or "—")[:300]


async def _show_pick_list(callback: CallbackQuery, db_session, state: FSMContext,
                          item, *, kind: str) -> None:
    """قائمة اختيار للمادة/الفئة — بلا كتابة حرة.

    **لماذا قائمة لا كتابة حرة؟** لأن المادة/الفئة تُخزَّن كمعرّف رقمي؛
    لو كتب المستخدم اسمًا لاحقًا احتجنا مطابقة نصية قد تخطئ أو تلتقط
    مادة مشابهة الاسم. القائمة تُرسل المعرّف الصحيح مباشرة.
    """
    await state.set_state(EditFlow.waiting_pick)

    if kind == "category":
        options = await files_service.list_categories(db_session)
        prefix = "own:pickcat"
        label = "📁 اختر الفئة الجديدة"
    else:
        options = await catalog.all_subjects(db_session)
        prefix = "own:pick" + "subj"
        label = "📘 اختر المادة الجديدة"

    builder = InlineKeyboardBuilder()
    if not options:
        builder.row(InlineKeyboardButton(
            text="لا توجد خيارات متاحة", callback_data="noop:page"))
    for option in list(options)[:24]:
        builder.row(InlineKeyboardButton(
            text=option.name[:40],
            callback_data=_cd(f"{prefix}:{item.id}:{option.id}")))
    builder.row(back_button("⬅️ رجوع", _cd(f"own:view:{item.id}")))

    await callback.message.edit_text(
        f"<b>{label}</b>\n\n<i>الحالي: {escape(_current_value(item, kind + '_id'))}</i>",
        reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


# ───────────────────────────── استلام القيمة الجديدة وحفظها
@router.message(EditFlow.waiting_value, F.text)
async def on_new_value(message: Message, db_user: User, db_session,
                       state: FSMContext) -> None:
    """يحفظ قيمة الحقل بعد التحقّق — كل خطأ يُشرح بالعربية بلا فقدان المدخل."""
    data = await state.get_data()
    file_id = data.get("edit_file_id")
    field = data.get("edit_field")
    if not file_id or not field:
        await state.clear()
        await message.answer("انتهت الجلسة — ابدأ من «موادي».")
        return

    item = await files_service.get_file(db_session, int(file_id))
    if item is None:
        await state.clear()
        await message.answer("الملف لم يعد موجودًا.")
        return
    if not files_service.can_edit(item, db_user):
        await state.clear()
        await message.answer(
            f"🔒 {files_service.edit_denied_reason(item, db_user)}")
        return

    raw = (message.text or "").strip()
    if field == "external_url" and raw == "-":
        raw = ""                       # «-» = امسح الرابط

    try:
        await files_service.update_file(
            db_session, item, db_user, changes={field: raw},
            actor_id=db_user.id,
        )
    except files_service.EditError as exc:
        # لا نُخرج من الحالة: المستخدم يصلّح ويحاول مرة أخرى
        await message.answer(
            f"⚠️ {escape(str(exc))}\n\n<i>جرّب قيمة أخرى، أو اضغط إلغاء.</i>",
            parse_mode="HTML")
        return
    except Exception as exc:                     # noqa: BLE001
        log.exception("فشل تعديل الملف %s: %s", file_id, exc)
        await state.clear()
        await message.answer("⚠️ تعذّر الحفظ — جرّب تاني بعد قليل.")
        return

    await db_session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>تم الحفظ.</b>\n\n"
        f"<i>القيمة الجديدة:</i> <code>{escape(_current_value(item, field))}</code>",
        parse_mode="HTML")
    await show_own_files_file(message, db_session, item.id)


async def show_own_files_file(target, db_session, file_id: int) -> None:
    """يعيد عرض شاشة الملف المفرد (بعد الحفظ)."""
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        return
    builder = InlineKeyboardBuilder()
    for key, label in FIELD_LABELS.items():
        builder.row(InlineKeyboardButton(
            text=label, callback_data=_cd(f"own:edit:{file_id}:{key}")))
    builder.row(InlineKeyboardButton(
        text="🗑️ حذف الملف", callback_data=_cd(f"own:del:{file_id}")))
    builder.row(back_button("⬅️ موادي", "own:list"))

    rows = [
        ("✏️", "العنوان", escape(item.title)),
        ("📝", "الوصف", escape(item.description or "—")),
        ("🏷️", "الوسوم", escape(item.tags or "—")),
        ("📘", "المادة", escape(item.subject.name if item.subject else "—")),
        ("📁", "الفئة", escape(item.category.name if item.category else "—")),
        ("🔗", "رابط", escape(item.external_url or "—")),
    ]
    body = (texts.header("materials", escape(item.title))
            + "\n\n" + texts.info_block(rows))
    await target.answer(body, reply_markup=builder.as_markup(), parse_mode="HTML",
                        disable_web_page_preview=True)


# ───────────────────────────── اختيار الفئة/المادة من القائمة
@router.callback_query(F.data.startswith("own:pickcat:"))
async def cb_pick_category(callback: CallbackQuery, db_user: User, db_session,
                           state: FSMContext) -> None:
    _, _, file_id, category_id = callback.data.split(":")
    await _apply_pick(callback, db_user, db_session, state,
                      int(file_id), "category_id", int(category_id))


@router.callback_query(F.data.startswith("own:pi" + "cksubj:"))
async def cb_pick_subject(callback: CallbackQuery, db_user: User, db_session,
                          state: FSMContext) -> None:
    _, _, file_id, subject_id = callback.data.split(":")
    await _apply_pick(callback, db_user, db_session, state,
                      int(file_id), "subject_id", int(subject_id))


async def _apply_pick(callback: CallbackQuery, db_user: User, db_session,
                      state: FSMContext, file_id: int, field: str, value: int) -> None:
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return
    try:
        await files_service.update_file(
            db_session, item, db_user, changes={field: value}, actor_id=db_user.id)
        await db_session.commit()
    except files_service.EditError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:                     # noqa: BLE001
        log.exception("فشل اختيار %s للملف %s: %s", field, file_id, exc)
        await callback.answer("⚠️ تعذّر الحفظ", show_alert=True)
        return

    await state.clear()
    await callback.answer("✅ تم الحفظ")
    await show_own_files_file(callback.message, db_session, file_id)


# ───────────────────────────── الحذف (بتأكيد صريح)
@router.callback_query(F.data.startswith("own:del:"))
async def cb_confirm_delete(callback: CallbackQuery, db_user: User, db_session) -> None:
    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return
    if not files_service.can_edit(item, db_user):
        await callback.answer(
            files_service.edit_denied_reason(item, db_user), show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑️ نعم، احذفه",
                             callback_data=_cd(f"own:delyes:{file_id}")),
        InlineKeyboardButton(text="✖️ رجوع",
                             callback_data=_cd(f"own:view:{file_id}")),
    )
    await callback.message.edit_text(
        f"⚠️ <b>تأكيد الحذف</b>\n\n"
        f"هتحذف: <b>{escape(item.title)}</b>\n\n"
        f"الملف هيختفي من المكتبة والبحث. "
        f"<i>(النسخة في قناة التخزين لا تُمسح، فيمكن استرجاعه من السجلّ.)</i>",
        reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("own:delyes:"))
async def cb_do_delete(callback: CallbackQuery, db_user: User, db_session) -> None:
    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return
    try:
        await files_service.delete_file(db_session, item, db_user, note="حذف من «موادي»")
        await db_session.commit()
    except files_service.EditError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:                     # noqa: BLE001
        log.exception("فشل حذف الملف %s: %s", file_id, exc)
        await callback.answer("⚠️ تعذّر الحذف", show_alert=True)
        return

    await callback.answer("🗑️ تم الحذف")
    await _edit_own_files(callback, db_session, db_user.id, 0)
