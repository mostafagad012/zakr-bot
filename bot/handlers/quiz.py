"""الكويزات: عرض القائمة، بدء كويز، تصحيح الإجابات."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import Quiz
from ..services import quiz as quiz_service
from ..states import QuizFlow
from .helpers import back_button, escape, slice_page

router = Router(name="quiz")

LETTERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]


@router.callback_query(F.data.startswith("quiz:list:"))
async def cb_quiz_list(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    await state.clear()
    page = int(callback.data.split(":")[-1])
    quizzes = await quiz_service.list_quizzes(db_session)

    builder = InlineKeyboardBuilder()
    if not quizzes:
        builder.row(InlineKeyboardButton(text="لا توجد كويزات بعد", callback_data="noop:page"))
        builder.row(back_button(callback_data="menu:main"))
        await callback.message.edit_text(
            "🧠 <b>الكويزات</b>\n\nلا توجد كويزات بعد — يمكن للأدمن إضافتها من لوحة الإدارة.",
            reply_markup=builder.as_markup(), parse_mode="HTML",
        )
        await callback.answer()
        return

    items, total_pages = slice_page(quizzes, page + 1)
    for quiz in items:
        builder.row(InlineKeyboardButton(
            text=f"🧠 {escape(quiz.title[:36])} ({len(quiz.questions)})",
            callback_data=f"quiz:start:{quiz.id}",
        ))
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"quiz:list:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
        if page + 1 < total_pages:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"quiz:list:{page + 1}"))
        builder.row(*row)
    builder.row(back_button(callback_data="menu:main"))

    await callback.message.edit_text(
        texts.header("quiz", subtitle="اختر كويزًا للبدء:"),
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("quiz:start:"))
async def cb_quiz_start(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    quiz_id = int(callback.data.split(":")[-1])
    quiz = await quiz_service.get_quiz(db_session, quiz_id)
    if quiz is None or not quiz.questions:
        await callback.answer("الكويز غير متاح", show_alert=True)
        return

    questions = sorted(quiz.questions, key=lambda q: q.id)
    await state.set_state(QuizFlow.answering)
    await state.update_data(quiz_id=quiz_id, answers=[], index=0, total=len(questions))

    await _ask_question(callback.message, quiz, questions[0], 0, len(questions))
    await callback.answer()


async def _ask_question(message, quiz: Quiz, question, index: int, total: int) -> None:
    builder = InlineKeyboardBuilder()
    for i, option in enumerate(question.option_list):
        label = LETTERS[i] if i < len(LETTERS) else f"{i + 1}."
        builder.row(InlineKeyboardButton(
            text=f"{label} {escape(option[:60])}",
            callback_data=f"quiz:ans:{question.id}:{i}",
        ))
    builder.row(InlineKeyboardButton(text="❌ إلغاء", callback_data="quiz:abort"))

    await message.answer(
        texts.header("quiz", escape(quiz.title)) + "\n"
        f"❓ السؤال {index + 1} من {total}\n"
        f"{texts.progress_bar(index + 1, total)}\n\n"
        f"<b>{escape(question.text)}</b>",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("quiz:ans:"), QuizFlow.answering)
async def cb_quiz_answer(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    # نقرأ الإجابة من الـcallback ولا نثق برقم السؤال المرسل: الترتيب
    # المحفوظ في الحالة هو المصدر، فالسؤال لا يُقفز ولا يُكرَّر ولو ضغط
    # المستخدم زرًّا قديمًا من رسالة سابقة.
    _, _, _stale_question_id, answer = callback.data.split(":")
    data = await state.get_data()
    quiz_id = data.get("quiz_id")
    answers = list(data.get("answers") or [])
    index = data.get("index", 0)

    quiz = await quiz_service.get_quiz(db_session, quiz_id)
    if quiz is None:
        await state.clear()
        await callback.answer("انتهى الكويز", show_alert=True)
        return

    questions = sorted(quiz.questions, key=lambda q: q.id)
    answers.append(int(answer))
    index += 1

    if index < len(questions):
        await state.update_data(answers=answers, index=index)
        await _ask_question(callback.message, quiz, questions[index], index, len(questions))
        await callback.answer()
        return

    # انتهت الأسئلة — نصحّح
    result = await quiz_service.submit_attempt(db_session, quiz, callback.from_user.id, answers)
    await db_session.commit()
    await state.clear()

    verdict = "🎉 نجحت!" if result.passed else "😔 لم تنجح هذه المرة"
    lines = [
        texts.header("quiz", f"نتيجة {escape(quiz.title)}"),
        verdict,
        texts.info_row("✅", "الإجابات الصحيحة", f"<b>{result.correct}/{result.total}</b> ({result.score_percent}%)"),
        texts.info_row("🎯", "نسبة النجاح المطلوبة", f"{quiz.pass_percent}%"),
    ]

    lines.append("\n📚 <b>مراجعة الإجابات</b>")
    for question in questions:
        lines.append(f"• {escape(question.text[:70])} ← {escape(question.option_list[question.correct_index])}")
        if question.explanation:
            lines.append(f"  <i>{escape(question.explanation[:120])}</i>")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 إعادة الكويز", callback_data=f"quiz:start:{quiz_id}"))
    builder.row(back_button("⬅️ الكويزات", "quiz:list:0"))
    await callback.message.answer("\n".join(lines)[:4000], reply_markup=builder.as_markup(),
                                  parse_mode="HTML")
    await callback.answer("✅ انتهت الأسئلة")


@router.callback_query(F.data == "quiz:abort")
async def cb_quiz_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ تم إلغاء الكويز.")
    await callback.answer()


@router.callback_query(F.data.startswith("file:sum:"))
async def cb_file_summary(callback: CallbackQuery, db_session) -> None:
    """ملخص ملف مخزّن (يعتمد على النص المستخرج وقت الرفع)."""
    from ..services import summarizer
    from ..services.files import get_file, get_indexed_text

    file_id = int(callback.data.split(":")[-1])
    item = await get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return

    content = await get_indexed_text(db_session, item.id)
    if not content or not content.strip():
        await callback.answer("لا يوجد نص مستخرج لهذا الملف", show_alert=True)
        return

    summary = summarizer.summarize(content, max_sentences=8)
    await callback.message.answer(
        texts.header("summarize", f"ملخص {escape(item.title)}") + f"\n\n{escape(summary.text)}",
        parse_mode="HTML",
    )
    await callback.answer()
