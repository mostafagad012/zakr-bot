"""واجهة الامتحانات: توليد بالذكاء الاصطناعي، إجابة بأزرار، نتيجة نهائية.

التصميم: **سؤال واحد لكل رسالة** (لا قائمة طويلة). كل إجابة تُسجَّل في
`FSM` فورًا، ثم تُعدَّل نفس الرسالة إلى السؤال التالي — فالمستخدم يرى
تقدّمًا واضحًا (سؤال ٣ من ٥) ولا يضيع في قائمة.

الأمان: الإجابة تأتي من `callback_data` وهو مدخل قابل للتزوير، لذا:
* تُتحقَّق الأرقام مقابل عدد الخيارات الفعلي قبل التسجيل.
* تُسجَّل إجابة واحدة فقط لكل سؤال (تعديل مزدوج لا يفسد النتيجة).
* زر الإجابة المستخدمة يُستبدل فورًا فلا يُضغط مرتين.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User
from ..config import settings
from ..services import ai
from ..services import ai_usage
from ..services import exam as exam_service
from ..services import learner
from ..states import ExamFlow
from .helpers import back_button

log = logging.getLogger(__name__)

router = Router(name="exam")

LETTERS = ["أ", "ب", "ج", "د", "هـ", "و"]
MAX_ANSWERS_IN_STATE = 10


# ------------------------------------------------------------ الأزرار
def exam_start_menu() -> InlineKeyboardBuilder:
    """أنواع الاختبارات: كويز قصير، امتحان مرن 5–15، أو شامل 30–50."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⚡ كويز سريع (5 أسئلة)",
                                    callback_data="exm:new:5"))
    builder.row(InlineKeyboardButton(text="🎯 امتحان — اختر عدد الأسئلة (5–15)",
                                    callback_data="exm:pick:std"))
    builder.row(InlineKeyboardButton(text="📚 امتحان شامل (30–50 سؤالًا)",
                                    callback_data="exm:pick:full"))
    builder.row(InlineKeyboardButton(text="📄 من ملف أرسله",
                                    callback_data="exm:frompdf"))
    builder.row(back_button("⬅️ القائمة", "menu:main"))
    return builder


def size_keyboard(kind: str) -> InlineKeyboardBuilder:
    """أزرار اختيار العدد — نطاقات منفصلة للمرن والشامل."""
    builder = InlineKeyboardBuilder()
    if kind == "std":
        for count in (5, 8, 10, 12, 15):
            builder.row(InlineKeyboardButton(
                text=f"📝 {count} أسئلة" + (" — سريع" if count == 5 else
                                            " — متوازن" if count in (8, 10) else " — مركّز"),
                callback_data=f"exm:new:{count}",
            ))
        builder.row(back_button("⬅️ رجوع", "exm:menu"))
    else:
        for count, label in ((30, "شامل مصغّر"), (40, "شامل"), (50, "شامل كامل")):
            builder.row(InlineKeyboardButton(
                text=f"📚 {count} سؤالًا — {label}",
                callback_data=f"exm:new:{count}",
            ))
        builder.row(back_button("⬅️ رجوع", "exm:menu"))
    return builder


def size_intro(kind: str, difficulty: str) -> str:
    label = exam_service.DIFFICULTY_LABEL.get(difficulty, difficulty)
    if kind == "std":
        body = (
            "🎯 <b>امتحان — كم سؤالًا؟</b>\n\n"
            "اختر من 5 إلى 15 سؤالًا. الأسئلة القليلة أسرع للمراجعة، "
            "والأكثر تغطية أفضل للتحصيل."
        )
    else:
        body = (
            "📚 <b>امتحان شامل</b>\n\n"
            "من 30 إلى 50 سؤالًا — تغطية واسعة للمنهج كله.\n"
            "<i>⏳ الشامل يحتاج وقتًا أطول للتوليد (عدّة دفعات) — "
            "الأسئلة تُجمَّع على دفعات لضمان جودتها.</i>"
        )
    return f"{texts.header('quiz', '📝 نوع الامتحان')}\n\n{body}\n\n⚖️ الصعوبة: <b>{label}</b>"


def answer_keyboard(index: int, question) -> InlineKeyboardBuilder:
    """أزرار الإجابة — أرقامها تُتحقَّق مقابل الخيارات الفعلية."""
    builder = InlineKeyboardBuilder()
    for position, option in enumerate(question.options):
        label = f"{LETTERS[position] if position < len(LETTERS) else position + 1}. {option}"
        builder.row(InlineKeyboardButton(
            text=label[:60], callback_data=f"exm:ans:{index}:{position}"
        ))
    builder.row(InlineKeyboardButton(text="⏭️ تخطّي", callback_data=f"exm:ans:{index}:skip"))
    return builder


def question_text(question, index: int, total: int, difficulty: str) -> str:
    label = exam_service.DIFFICULTY_LABEL.get(difficulty, difficulty)
    return (
        f"❓ <b>سؤال {index + 1} من {total}</b>  <i>({label})</i>\n\n"
        f"{exam_service.safe(question.text)}"
    )


def start_intro(difficulty: str, count: int, used_ai: bool) -> str:
    label = exam_service.DIFFICULTY_LABEL.get(difficulty, difficulty)
    engine = "توليد ذكي" if used_ai else "توليد محلي"
    return (
        f"{texts.header('quiz', '📝 امتحان جديد')}\n\n"
        f"⚖️ الصعوبة: <b>{label}</b> — مضبوطة على مستواك\n"
        f"🔢 عدد الأسئلة: <b>{count}</b>\n"
        f"⚙️ المصدر: {engine}\n\n"
        "جاوب بأزرار تحت كل سؤال — والنتيجة هتظهر في الآخر."
    )


# ------------------------------------------------------------ البدء
@router.callback_query(F.data == "exm:menu")
async def cb_menu(callback: CallbackQuery, db_user: User, db_session) -> None:
    profile = await learner.get_profile(db_session, db_user.id)
    difficulty = learner.next_difficulty(profile) if profile else "easy"
    label = exam_service.DIFFICULTY_LABEL.get(difficulty, difficulty)
    await callback.message.edit_text(
        f"{texts.header('quiz', '📝 امتحانات')}\n\n"
        f"⚖️ صعوبة امتحانك القادم: <b>{label}</b>\n"
        "<i>بتتغيّر تلقائيًا مع مستواك — كل ما تتحسّن، يصعب.</i>\n\n"
        "⚡ كويز سريع · 🎯 امتحان 5–15 · 📚 شامل 30–50",
        reply_markup=exam_start_menu().as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exm:pick:"))
async def cb_pick_size(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يعرض أزرار عدد الأسئلة المناسبة للنوع المطلوب."""
    kind = callback.data.split(":", 2)[2]
    if kind not in ("std", "full"):
        await callback.answer("نوع غير معروف", show_alert=True)
        return
    profile = await learner.get_profile(db_session, db_user.id)
    difficulty = learner.next_difficulty(profile) if profile else "easy"
    await callback.message.edit_text(
        size_intro(kind, difficulty) + "\n\nاختر عدد الأسئلة:",
        reply_markup=size_keyboard(kind).as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exm:new:"))
async def cb_new(callback: CallbackQuery, db_user: User, db_session,
                 state: FSMContext) -> None:
    """يبدأ امتحانًا بالعدد المختار — بتحقّق من النطاق المسموح."""
    parts = callback.data.split(":")
    try:
        count = int(parts[2])
    except (IndexError, ValueError):
        count = exam_service.DEFAULT_QUESTION_COUNT

    # ⚠️ callback_data مدخل قابل للتزوير — نتحقق من النطاق قبل الاستهلاك
    if not exam_service.is_valid_size(count):
        await callback.answer(
            f"عدد غير مسموح — اختر بين {exam_service.MIN_STANDARD}–"
            f"{exam_service.MAX_STANDARD} أو {exam_service.MIN_COMPREHENSIVE}–"
            f"{exam_service.MAX_COMPREHENSIVE}",
            show_alert=True,
        )
        return

    # آخر موضوع شرح/تلخيص يجرّبه المستخدم يُستخدم كموضوع الامتحان
    previous = await state.get_data()
    topic = (previous.get("last_topic") or "معلومات عامة دراسية").strip()
    await state.clear()

    hint = "شامل" if count > exam_service.MAX_STANDARD else "امتحان"
    await callback.answer(f"⏳ جارٍ تجهيز {hint} من {count} سؤالًا…")
    await _run_exam(callback.message, db_user, db_session, state, topic, count)


async def _run_exam(message, db_user: User, db_session, state: FSMContext,
                    topic: str, count: int, material: str = "") -> None:
    """يولّد الامتحان ويبدأ السؤال الأول — مسار مشترك."""
    # السقف قبل التوليد: الامتحان الشامل يستهلك ٥ نداءات، ففحصه هنا ضروري.
    kind = "exam_full" if count > exam_service.MAX_STANDARD else "exam"
    allowed, _used, _limit = await ai_usage.check(db_session, db_user.id, kind)
    if not allowed and ai.is_configured():
        await message.answer(await ai_usage.limit_message(db_session, db_user.id),
                             parse_mode="HTML")
        return

    profile = await learner.get_profile(db_session, db_user.id)
    kind = exam_service.size_label(count)
    if count > exam_service.MAX_STANDARD:
        wait_text = (
            f"⏳ <b>جارٍ تجهيز {kind}</b>\n"
            "التوليد على دفعات لضمان جودة الأسئلة — قد يستغرق دقيقة أو أكثر.\n"
            "<i>لا تغلق الشات، سيصلك كل شيء هنا.</i>"
        )
    else:
        wait_text = f"⏳ جارٍ تجهيز {kind} على مقاس مستواك…"
    notice = await message.answer(wait_text, parse_mode="HTML")

    exam = await exam_service.generate(topic, profile, material=material, count=count)
    if exam.used_ai:
        kind = "exam_full" if count > exam_service.MAX_STANDARD else "exam"
        await ai_usage.record(db_session, db_user.id, kind,
                              ok=bool(exam.questions), provider=ai.get_provider().key)
        await db_session.commit()
    try:
        await notice.delete()
    except Exception as exc:  # تجميل فقط
        log.debug("تعذّر حذف رسالة الانتظار: %s", type(exc).__name__)

    if not exam.questions:
        await message.answer(
            "⚠️ تعذّر توليد أسئلة عن هذا الموضوع.\n"
            "جرّب موضوعًا مختلفًا أو أرسل ملفًا فيه محتوى.",
            reply_markup=exam_start_menu().as_markup(), parse_mode="HTML",
        )
        return

    await state.set_state(ExamFlow.answering)
    await state.update_data(
        answers=[None] * len(exam.questions),
        difficulty=exam.difficulty,
        topic=topic,
        questions=[{
            "text": q.text, "options": q.options,
            "correct": q.correct_index, "why": q.explanation,
        } for q in exam.questions],
        current=0,
    )

    intro = start_intro(exam.difficulty, len(exam.questions), exam.used_ai)
    if exam.note:
        intro += f"\n\n<i>{exam_service.safe(exam.note)}</i>"
    await message.answer(intro, parse_mode="HTML")

    first = exam.questions[0]
    await message.answer(
        question_text(first, 0, len(exam.questions), exam.difficulty),
        reply_markup=answer_keyboard(0, first).as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data == "exm:frompdf")
async def cb_frompdf(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ExamFlow.waiting_pdf)
    await callback.message.answer(
        "📄 أرسل ملف PDF وهعمل منه امتحان على مقاس مستواك.\n"
        "<i>لو الملف مصوّر هقراه بـOCR.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "exm:menu")).as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.message(ExamFlow.waiting_pdf, F.document)
async def on_pdf(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    name = message.document.file_name or "ملف"
    is_pdf = name.lower().endswith(".pdf") or (message.document.mime_type or "") == "application/pdf"
    if not is_pdf:
        await message.answer("⚠️ أرسل ملف PDF فقط.")
        return

    # فحص الحجم **قبل** التنزيل: بدونه ملف واحد كبير يجرّ البوت إلى تنزيل
    # + OCR + استدعاء AI ثم ينتهي بمهلة، وقد يمنع بقية المستخدمين.
    limit = settings.exam_pdf_max_mb
    size = getattr(message.document, "file_size", None) or 0
    if size > limit * 1024 * 1024:
        await message.answer(
            f"⚠️ <b>الملف كبير</b> — {size // (1024 * 1024)} ميجا، "
            f"والحدّ {limit} ميجا للامتحانات.\n\n"
            "قلّل عدد الصفحات أو اضغط الملف، أو ارفعه للمكتبة واطلب امتحانًا "
            "من نصه لاحقًا.",
            parse_mode="HTML",
        )
        return

    notice = await message.answer("⏳ جارٍ قراءة الملف…")
    try:
        buffer = await message.bot.download(message.document)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
    except Exception as exc:
        log.warning("فشل تنزيل PDF للامتحان: %s", exc)
        await notice.delete()
        await message.answer("⚠️ تعذّر تنزيل الملف.")
        return

    from ..services import pdf as pdf_service

    extracted = pdf_service.extract_text(data)
    text = extracted.text
    if not text.strip():
        try:
            from ..services import ocr as ocr_service
            result = await ocr_service.extract_text_from_pdf(data)
            text = result.text
        except Exception as exc:
            log.info("تعذّر OCR للامتحان: %s", type(exc).__name__)
            text = ""

    try:
        await notice.delete()
    except Exception as exc:
        log.debug("تعذّر حذف رسالة الانتظار: %s", type(exc).__name__)

    if not text.strip():
        await message.answer("⚠️ لم أتمكّن من استخراج نص من الملف.")
        return

    await state.clear()
    await _run_exam(message, db_user, db_session, state, topic=name[:60],
                    count=exam_service.DEFAULT_QUESTION_COUNT, material=text)


# ------------------------------------------------------------ الإجابة
@router.callback_query(F.data.startswith("exm:ans:"))
async def cb_answer(callback: CallbackQuery, db_user: User, db_session,
                    state: FSMContext) -> None:
    if await state.get_state() != ExamFlow.answering.state:
        await callback.answer("انتهت هذه الجلسة — ابدأ امتحانًا جديدًا", show_alert=True)
        return

    parts = callback.data.split(":")
    try:
        index = int(parts[2])
        raw_choice = parts[3]
    except (IndexError, ValueError):
        await callback.answer("اختيار غير صالح", show_alert=True)
        return

    data = await state.get_data()
    questions = data.get("questions") or []
    answers = list(data.get("answers") or [])

    # ---- تحقّق صارم: callback_data مدخل قابل للتزوير ----
    if not (0 <= index < len(questions)) or index >= len(answers):
        await callback.answer("سؤال غير معروف", show_alert=True)
        return
    if answers[index] is not None:      # إجابة مزدوجة لا تُحتسب مرتين
        await callback.answer("جاوبت على السؤال ده بالفعل")
        return

    question = questions[index]
    option_count = len(question.get("options") or [])
    if raw_choice != "skip":
        try:
            choice = int(raw_choice)
        except ValueError:
            await callback.answer("اختيار غير صالح", show_alert=True)
            return
        if not 0 <= choice < option_count:
            await callback.answer("اختيار خارج النطاق", show_alert=True)
            return
        answers[index] = choice

    await state.update_data(answers=answers, current=index + 1)

    # ---- آخر سؤال؟ اعرض النتيجة ----
    if index + 1 >= len(questions):
        await _finish(callback, db_user, db_session, state, questions, answers)
        return

    # ---- السؤال التالي ----
    nxt = index + 1
    next_question = questions[nxt]

    class _Q:
        text = next_question["text"]
        options = next_question["options"]

    await callback.message.edit_text(
        question_text(_Q, nxt, len(questions), data.get("difficulty", "easy")),
        reply_markup=answer_keyboard(nxt, _Q).as_markup(), parse_mode="HTML",
    )
    await callback.answer(
        "✅ تم" if raw_choice != "skip" else "⏭️ تخطّي"
    )


async def _finish(callback: CallbackQuery, db_user: User, db_session,
                  state: FSMContext, questions: list[dict],
                  answers: list[int | None]) -> None:
    """التصحيح النهائي + النتيجة + تغذية ملف المتعلّم."""
    from ..services.exam import Question

    objects = [
        Question(text=q["text"], options=q["options"],
                 correct_index=q["correct"], explanation=q.get("why", ""))
        for q in questions
    ]
    data = await state.get_data()
    difficulty = data.get("difficulty", "easy")
    topic = data.get("topic", "")
    await state.clear()

    result = exam_service.grade(objects, answers)

    # تغذية ملف المتعلّم: المستوى والصعوبة القادمة ونقاط الضعف
    outcome = await learner.record_quiz_result(
        db_session, db_user.id,
        correct=result.correct, total=result.total,
        difficulty=difficulty, topic=topic[:120],
    )
    await learner.log_event(
        db_session, db_user.id, "exam_finish", topic=topic[:120],
        difficulty=difficulty, is_correct=result.passed,
        detail=f"{result.correct}/{result.total}",
    )
    await db_session.commit()

    await callback.message.edit_text(
        exam_service.result_text(result, outcome), parse_mode="HTML",
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔁 امتحان تاني", callback_data="exm:menu"))
    builder.row(InlineKeyboardButton(text="🎯 تحليلي", callback_data="lrn:analytics"))
    builder.row(back_button("⬅️ القائمة", "menu:main"))
    await callback.message.answer(
        "تحب تعمل إيه كمان؟", reply_markup=builder.as_markup(),
    )
    await callback.answer("🏁 انتهى الامتحان")
