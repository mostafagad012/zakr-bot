"""البحث: بالأزرار، وبكتابة حرة في الشات (بلا أوامر /).

**قرار تصميمي**: حُذفت الأوامر النصية نهائيًا. بدل `/search كلمة`، صار
أي نص يكتبه المستخدم في الشات يُفهم كبحث تلقائيًا — وهذا هو السلوك
الأكثر بديهية: المستخدم يكتب «تفاضل» فيجد الملفات.

الشرط الوحيد للتفعيل: رسالة نصية **ليست** داخل أي تدفّق آخر (حالة FSM).
لهذا يُسجَّل هذا المعالج **آخرًا** في ترتيب الراوترات، فلا يسرق نصًا
يخصّ تدفّقًا آخر (رفع ملف، شرح، تلخيص، إجابة كويز...).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..services import search
from ..states import SearchFlow
from .helpers import back_button, escape

router = Router(name="search")


def results_markup(hits, back_to: str = "menu:main") -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for hit in hits:
        builder.row(InlineKeyboardButton(
            text=f"📄 {escape(hit.file.title[:38])}",
            callback_data=f"file:view:{hit.file.id}",
        ))
    builder.row(back_button(callback_data=back_to))
    return builder


def format_hits(hits) -> str:
    lines = [texts.header("search", f"نتائج البحث ({len(hits)})")]
    for hit in hits:
        subject_name = escape(hit.file.subject.name) if hit.file.subject else "—"
        lines.append(
            f"\n📄 <b>{escape(hit.file.title)}</b>\n"
            f"  📘 {subject_name} | 📥 {hit.file.downloads_count} تحميل"
        )
        if hit.snippet:
            lines.append(f"  💬 <i>{escape(hit.snippet[:120])}</i>")
    return "\n".join(lines)[:4000]


@router.callback_query(F.data == "search:ask")
async def cb_search_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchFlow.waiting_query)
    await callback.message.answer(
        "🔍 اكتب كلمة البحث الآن.\n"
        "البحث يشمل: أسماء الملفات، الوصف، الوسوم، وداخل نصوص الـ PDF.\n\n"
        "للإلغاء اضغط «رجوع» في الأسفل."
    )
    await callback.answer()


@router.message(F.text, SearchFlow.waiting_query)
async def on_search_query(message: Message, state: FSMContext, db_session) -> None:
    query = (message.text or "").strip()
    await state.clear()
    if not query:
        await message.answer("لم تكتب شيئًا.")
        return
    hits = await search.search_files(db_session, query)
    if not hits:
        await message.answer(
            f"🔍 لا توجد نتائج عن «{escape(query)}».\nجرّب كلمة أقصر أو مختلفة."
        )
        return
    await message.answer(format_hits(hits), reply_markup=results_markup(hits).as_markup(),
                         parse_mode="HTML")


@router.callback_query(F.data.startswith("search:subj:"))
async def cb_search_in_subject(callback: CallbackQuery, state: FSMContext) -> None:
    subject_id = int(callback.data.split(":")[-1])
    await state.set_state(SearchFlow.waiting_subject_query)
    await state.update_data(subject_id=subject_id)
    await callback.message.answer("🔍 اكتب كلمة البحث داخل هذه المادة:")
    await callback.answer()


@router.message(F.text, SearchFlow.waiting_subject_query)
async def on_subject_query(message: Message, state: FSMContext, db_session) -> None:
    data = await state.get_data()
    subject_id = data.get("subject_id")
    await state.clear()
    query = (message.text or "").strip()
    hits = await search.search_files(db_session, query, subject_id=subject_id)
    back = f"subj:view:{subject_id}" if subject_id else "menu:main"
    if not hits:
        await message.answer(f"🔍 لا توجد نتائج عن «{escape(query)}» في هذه المادة.")
        return
    await message.answer(format_hits(hits), reply_markup=results_markup(hits, back).as_markup(),
                         parse_mode="HTML")


# ------------------------------------------------ بحث بالكتابة الحرة
# يُسجَّل أخيرًا في هذا الراوتر، والراوتر نفسه متأخر، فلا يلتقط نصًا
# يخصّ تدفّقًا آخر. شرط الطول يمنع تحويل رسالة قصيرة عابرة إلى بحث.
MIN_FREE_QUERY = 2
MAX_FREE_QUERY = 120


@router.message(F.text)
async def on_free_text(message: Message, state: FSMContext, db_session) -> None:
    """أي نص خارج التدفّقات = بحث تلقائي (بديل `/search`)."""
    # موجود داخل تدفّق آخر؟ اتركه لمن يخصّه.
    if await state.get_state() is not None:
        return
    query = (message.text or "").strip()
    if len(query) < MIN_FREE_QUERY or len(query) > MAX_FREE_QUERY:
        return
    hits = await search.search_files(db_session, query)
    if not hits:
        await message.answer(
            f"🔍 لا توجد نتائج عن «{escape(query)}».\n"
            "جرّب كلمة أقصر، أو تصفّح المكتبة بالأزرار.",
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text="📚 المكتبة", callback_data="nav:dept:0"))
            .row(InlineKeyboardButton(text="📋 القائمة", callback_data="menu:main"))
            .as_markup(),
            parse_mode="HTML",
        )
        return
    await message.answer(
        format_hits(hits),
        reply_markup=results_markup(hits).as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
