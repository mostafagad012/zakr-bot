"""واجهة الشرح والتلخيص الذكي: أزرار + مسار نصي حر.

التصميم: كل مسار يعمل **بلا ذكاء اصطناعي** أيضًا (يتراجع للملخص المحلي
أو رسالة واضحة)، فلا تتعطّل ميزة بسبب غياب مفتاح.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User
from ..services import ai, ai_usage, learner, pdf, tutor
from ..states import TutorFlow
from .helpers import ProgressReporter, back_button

log = logging.getLogger(__name__)

router = Router(name="tutor")

# حدّ الرسالة الواحدة في تيليجرام، ونترك هامشًا للترويسة
SEND_LIMIT = 3800


def _chunks_for_send(text: str) -> list[str]:
    """يقسّم ردًا طويلًا إلى رسائل متتابعة عند حدود الكلمات."""
    remaining = (text or "").strip()
    if not remaining:
        return []
    parts: list[str] = []
    while remaining:
        if len(remaining) <= SEND_LIMIT:
            parts.append(remaining)
            break
        window = remaining[:SEND_LIMIT]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut < SEND_LIMIT // 2:
            cut = SEND_LIMIT
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return parts


async def _send_long(message: Message, body: str) -> None:
    """يرسل نصًا طويلًا على رسائل — الرسالة الأولى ردًّا، والباقي تابعًا."""
    parts = _chunks_for_send(body)
    for index, part in enumerate(parts):
        if index == 0:
            await message.reply(part, parse_mode="HTML",
                                disable_web_page_preview=True)
        else:
            await message.answer(part, parse_mode="HTML",
                                 disable_web_page_preview=True)


# ------------------------------------------------------------ الشاشات
def explain_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    # الأوّل والمقصود: الشرح من **مادة حقيقية** مع تحليل PDF وبرومبت حر
    builder.row(InlineKeyboardButton(text="📄 اشرح من مادة (PDF/مكتبتي)",
                                    callback_data="exp:start"))
    builder.row(InlineKeyboardButton(text="✍️ اكتب موضوعًا أشرحه",
                                    callback_data="tut:ask"))
    builder.row(InlineKeyboardButton(text="🎨 تعديل أسلوب الشرح",
                                    callback_data="lrn:start"))
    builder.row(back_button(callback_data="menu:main"))
    return builder


def summarize_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📄 أرسل PDF للتلخيص الذكي",
                                    callback_data="tut:pdf"))
    builder.row(InlineKeyboardButton(text="📝 ألخّص نصًا أكتبه",
                                    callback_data="tut:text"))
    builder.row(back_button(callback_data="menu:main"))
    return builder


async def _status_line(db_session, user_id: int) -> str:
    """سطر يوضّح للمستخدم هل الذكاء الاصطناعي مفعّل وهل أسلوبه مخصّص."""
    profile = await learner.get_profile(db_session, user_id)
    lines = []
    if ai.is_configured():
        lines.append(
            f"🤖 <b>الشرح الذكي مفعّل</b> — {ai.get_provider().label}"
        )
    else:
        lines.append(
            "ℹ️ الشرح الذكي غير مفعّل (يحتاج مفتاح في .env) — "
            "التلخيص المحلي يعمل عادي."
        )
    if profile is not None and profile.survey_done:
        lines.append(f"🎨 أسلوبك الحالي: {learner.style_label(profile.explain_style)}")
    else:
        lines.append("💡 خصّص أسلوبك أولًا لتحصل على شرح على مقاسك.")
    return "\n".join(lines)


@router.callback_query(F.data == "tut:explain")
async def cb_explain_menu(callback: CallbackQuery, db_user: User, db_session) -> None:
    await callback.message.edit_text(
        texts.header("explain", subtitle="اشرح أي موضوع بأسلوبك") + "\n\n"
        + await _status_line(db_session, db_user.id),
        reply_markup=explain_menu().as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "tut:summarize")
async def cb_summarize_menu(callback: CallbackQuery, db_user: User, db_session) -> None:
    await callback.message.edit_text(
        texts.header("summarize", subtitle="تلخيص على مقاس أسلوبك") + "\n\n"
        + await _status_line(db_session, db_user.id),
        reply_markup=summarize_menu().as_markup(), parse_mode="HTML",
    )
    await callback.answer()


# ------------------------------------------------------------ الشرح
@router.callback_query(F.data == "tut:ask")
async def cb_ask(callback: CallbackQuery, db_user: User, db_session,
                 state: FSMContext) -> None:
    if not ai.is_configured():
        await callback.answer("الشرح الذكي غير مفعّل — يحتاج مفتاحًا", show_alert=True)
        return
    await state.set_state(TutorFlow.waiting_topic)
    profile = await learner.get_profile(db_session, db_user.id)
    style = learner.style_label(profile.explain_style) if profile else "بسيط"
    await callback.message.answer(
        f"✍️ <b>اكتب الموضوع اللي محتاج شرحه</b>\n"
        f"<i>مثال: «قانون أوم» أو «الفرق بين الخلية الحيوانية والنباتية»</i>\n\n"
        f"هشرحلك بأسلوب: {style}",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "tut:explain")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TutorFlow.waiting_topic, F.text)
async def on_topic(message: Message, db_user: User, db_session,
                   state: FSMContext) -> None:
    await state.clear()
    topic = (message.text or "").strip()
    if len(topic) < 2:
        await message.answer("⚠️ اكتب موضوعًا أوضح (كلمتان على الأقل).")
        return

    # السقف يُفحص **قبل** إنفاق أي رصيد على المزوّد
    allowed, _used, _limit = await ai_usage.check(db_session, db_user.id)
    if not allowed:
        await message.answer(await ai_usage.limit_message(db_session, db_user.id),
                             parse_mode="HTML")
        return

    reporter = ProgressReporter(message, f"شرح: {topic[:40]}", stage="جارٍ التحضير")
    await reporter.start()
    try:
        profile = await learner.get_profile(db_session, db_user.id)
        answer = await tutor.explain(topic, profile)
    finally:
        await reporter.finish()

    await ai_usage.record(db_session, db_user.id, "explain",
                          ok=bool(answer.text), provider=ai.get_provider().key)
    await db_session.commit()

    if not answer.text:
        await message.answer(f"⚠️ {tutor._safe(answer.note) or 'تعذّر الشرح.'}",
                             parse_mode="HTML")
        return

    await _send_long(message, tutor.render(f"شرح: {topic}", answer))
    await learner.log_event(db_session, db_user.id, "explain",
                            topic=topic[:120], detail="شرح ذكي" if answer.used_ai else "شرح محلي")
    await db_session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 اشرح بطريقة تانية", callback_data=f"tut:retry:{topic[:60]}"),
        InlineKeyboardButton(text="🎨 تغيير أسلوبي", callback_data="lrn:start"),
    )
    builder.row(back_button(callback_data="tut:explain"))
    await message.answer("تحب أعمل إيه كمان؟", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("tut:retry:"))
async def cb_retry(callback: CallbackQuery, db_user: User, db_session) -> None:
    """إعادة الشرح نفسه — الأسلوب الحالي قد يكون تغيّر."""
    topic = callback.data.split(":", 2)[2][:300]
    if not topic:
        await callback.answer("لا يوجد موضوع", show_alert=True)
        return
    await callback.answer("🔄 جارٍ إعادة الشرح…")
    profile = await learner.get_profile(db_session, db_user.id)
    answer = await tutor.explain(topic, profile)
    if not answer.text:
        await callback.message.answer(f"⚠️ {tutor._safe(answer.note)}", parse_mode="HTML")
        return
    await _send_long(callback.message, tutor.render(f"شرح: {topic}", answer))


# ------------------------------------------------------------ التلخيص
@router.callback_query(F.data == "tut:pdf")
async def cb_pdf(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TutorFlow.waiting_pdf)
    await callback.message.answer(
        texts.header("summarize", "أرسل ملف PDF الآن") + "\n\n"
        "🤖 هيكون التلخيص على مقاس أسلوبك، ولو الملف مصوّر (سكانر) "
        "اقدر أقراه بـOCR.",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "tut:summarize")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "tut:text")
async def cb_text(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TutorFlow.waiting_text)
    await callback.message.answer(
        "📝 <b>ابعت النص اللي عايز ألخّصه</b> في رسالة واحدة.",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "tut:summarize")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


async def _deliver_summary(message: Message, db_session, user_id: int,
                           text: str, title: str) -> None:
    """يشغّل التلخيص ويسلّمه — مسار مشترك لنص/PDF."""
    # التلخيص المحلي لا ينفق رصيدًا — السقف يخصّ المسار الذكي وحده.
    # نفحصه فقط إن كان الذكاء مفعّلًا، حتى لا نمنع ميزة مجانية.
    if ai.is_configured():
        allowed, _used, _limit = await ai_usage.check(db_session, user_id)
        if not allowed:
            await message.answer(await ai_usage.limit_message(db_session, user_id),
                                 parse_mode="HTML")
            return

    profile = await learner.get_profile(db_session, user_id)
    reporter = ProgressReporter(message, f"تلخيص: {title[:40]}", stage="جارٍ القراءة")
    await reporter.start()
    try:
        answer = await tutor.summarize(text, profile, title=title)
        if answer.used_ai:
            reporter.set_stage("صياغة الملخص")
    finally:
        await reporter.finish()

    if answer.used_ai:
        await ai_usage.record(db_session, user_id, "summary",
                              ok=bool(answer.text), provider=ai.get_provider().key)

    if not answer.text:
        await message.answer(f"⚠️ {tutor._safe(answer.note) or 'تعذّر التلخيص.'}",
                             parse_mode="HTML")
        return

    await _send_long(message, tutor.render(f"ملخص: {title}", answer))
    await learner.log_event(db_session, user_id, "summarize",
                            topic=title[:120],
                            detail="ذكي" if answer.used_ai else "محلي")
    await db_session.commit()


@router.message(TutorFlow.waiting_pdf, F.document)
async def on_pdf(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    await state.clear()
    name = message.document.file_name or "ملف"
    is_pdf = name.lower().endswith(".pdf") or (message.document.mime_type or "") == "application/pdf"
    if not is_pdf:
        await message.answer("⚠️ أرسل ملف PDF فقط.")
        return

    notice = await message.answer("⏳ جارٍ تنزيل الملف…")
    try:
        buffer = await message.bot.download(message.document)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
    except Exception as exc:
        log.warning("فشل تنزيل PDF للشرح: %s", exc)
        await notice.delete()
        await message.answer("⚠️ تعذّر تنزيل الملف.")
        return
    finally:
        try:
            await notice.delete()
        except Exception as exc:  # حذف رسالة الحالة — تجميل لا يكسر العمل
            log.debug("تعذّر حذف رسالة الحالة: %s", type(exc).__name__)

    extracted = pdf.extract_text(data)
    text = extracted.text
    if not text.strip():
        # ملف مصوّر: نجرّب OCR تلقائيًا بدل إبلاغ المستخدم بالفشل
        ocr_notice = await message.answer("🔎 الملف مصوّر — جارٍ قراءته بـOCR…")
        try:
            from ..services import ocr as ocr_service
            result = await ocr_service.extract_text_from_pdf(data)
            text = result.text
        except Exception as exc:
            log.info("تعذّر OCR للملخص: %s", type(exc).__name__)
            text = ""
        finally:
            try:
                await ocr_notice.delete()
            except Exception as exc:  # تجميل — لا يكسر التلخيص
                log.debug("تعذّر حذف رسالة OCR: %s", type(exc).__name__)

    if not text.strip():
        await message.answer(
            "⚠️ لم أتمكّن من استخراج نص من الملف.\n"
            "لو الملف صور فقط فمحتاج OCR — تأكد من تثبيت tesseract على السيرفر."
        )
        return

    await _deliver_summary(message, db_session, db_user.id, text, name[:60])


@router.message(TutorFlow.waiting_text, F.text)
async def on_text(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    await state.clear()
    if len(message.text or "") > ai.MAX_INPUT_CHARS * 2:
        await message.answer("⚠️ النص طويل جدًا — أرسله كملف PDF بدل ذلك.")
        return
    await _deliver_summary(message, db_session, db_user.id, message.text or "", "نصك")


# ------------------------------------------------------------ أوامر


