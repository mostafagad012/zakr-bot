"""ترجمة النصوص + تلخيص PDF."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..services import pdf as pdf_service
from ..services import summarizer
from ..services import translate as translate_service
from ..states import SummarizeFlow, TranslateFlow
from .helpers import back_button, escape

log = logging.getLogger(__name__)
router = Router(name="tools")


# ------------------------------------------------------------------ الترجمة
def _lang_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇪🇬 إلى العربية", callback_data="tr:lang:ar"),
        InlineKeyboardButton(text="🇬🇧 إلى الإنجليزية", callback_data="tr:lang:en"),
    )
    builder.row(back_button(callback_data="menu:main"))
    return builder


@router.callback_query(F.data == "translate:ask")
async def cb_translate_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TranslateFlow.waiting_text)
    await state.update_data(target="auto")
    await callback.message.answer(
        texts.header("translate", subtitle="اختر اللغة الهدف، ثم أرسل النص:"),
        reply_markup=_lang_keyboard().as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tr:lang:"))
async def cb_translate_lang(callback: CallbackQuery, state: FSMContext) -> None:
    target = callback.data.split(":")[-1]
    await state.set_state(TranslateFlow.waiting_text)
    await state.update_data(target=target)
    await callback.message.edit_text(
        f"🌐 اللغة الهدف: <b>{translate_service.LANG_NAMES.get(target, target)}</b>\n"
        "أرسل النص الآن (النصوص الطويلة تُقسَّم تلقائيًا).",
        parse_mode="HTML",
    )
    await callback.answer()


async def cmd_translate(message: Message, command: CommandObject, state: FSMContext) -> None:
    """اختصار داخلي (لم يعد أمرًا): يترجم نصًا للغة الأخرى."""
    text = (command.args or "").strip()
    if not text:
        await state.set_state(TranslateFlow.waiting_text)
        await state.update_data(target="auto")
        await message.answer("🌐 أرسل النص المراد ترجمته:")
        return
    await _do_translate(message, text, "auto")


@router.message(F.text, TranslateFlow.waiting_text)
async def on_translate_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = data.get("target") or "auto"
    await state.clear()
    await _do_translate(message, message.text or "", target)


async def _do_translate(message: Message, text: str, target: str) -> None:
    text = text.strip()
    if not text:
        await message.answer("لا يوجد نص للترجمة.")
        return

    if target == "auto":
        target = "en" if translate_service.looks_arabic(text) else "ar"

    notice = await message.answer("⏳ جارٍ الترجمة…")
    try:
        result = await translate_service.translate_text(text, target=target)
    except translate_service.TranslationError as exc:
        await notice.delete()
        await message.answer(f"⚠️ تعذّرت الترجمة: {exc}")
        return

    await notice.delete()
    # كان `result[:4000]` يقتطع الترجمة **صامتًا**: نص طويل يُترجم كاملًا
    # ثم يُعرض نصفه بلا أي إشارة. الآن تُقسَّم على رسائل، وإن قُسّمت
    # يقول للمستخدم ذلك صراحةً.
    head = texts.header(
        "translate", f"الترجمة إلى {translate_service.LANG_NAMES.get(target, target)}"
    )
    body = escape(result)
    parts = texts.split_for_send(head + "\n\n" + body)
    if len(parts) > 1:
        parts[0] += texts.split_note(len(head) + len(body) + 2)
    for part in parts:
        await message.answer(part, parse_mode="HTML")


# --------------------------------------------------------------- تلخيص PDF
@router.callback_query(F.data == "summarize:ask")
async def cb_summarize_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SummarizeFlow.waiting_pdf)
    await callback.message.answer(
        texts.header("summarize", subtitle="أرسل ملف PDF الآن وسأستخرج أهم النقاط.")
        + "\n\nℹ️ ملاحظة: يعمل مع ملفات PDF النصية، وليس المصوّرة.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(F.document, SummarizeFlow.waiting_pdf)
async def on_pdf_for_summary(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not (message.document.file_name or "").lower().endswith(".pdf") and \
            (message.document.mime_type or "") != "application/pdf":
        await message.answer("⚠️ أرسل ملف PDF فقط.")
        return

    notice = await message.answer("⏳ جارٍ قراءة الملف واستخراج النص…")
    try:
        buffer = await message.bot.download(message.document)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
    except Exception as exc:
        log.warning("فشل تنزيل PDF للتخليص: %s", exc)
        await notice.delete()
        await message.answer("⚠️ تعذّر تنزيل الملف.")
        return

    extracted = await asyncio.to_thread(pdf_service.extract_text, data)
    if not extracted.text.strip():
        await notice.delete()
        await message.answer(
            "⚠️ لم أتمكّن من استخراج نص من هذا الملف.\n"
            "غالبًا هو ملف مصوّر (سكانر) — ارفعه من «رفع ملف» ليُقرأ "
            "بالذكاء الاصطناعي، ثم ترجمه."
        )
        return

    summary = summarizer.summarize(extracted.text, max_sentences=8)
    await notice.delete()
    for part in texts.split_for_send(
        texts.header("summarize", f"ملخص {escape(message.document.file_name)}") + "\n"
        + texts.info_block([
            ("📄", "الصفحات", extracted.pages),
            ("🧩", "الجمل المستخدمة", f"{summary.sentences_used}/{summary.total_sentences}"),
        ])
        + f"\n\n{escape(summary.text)}"
    ):
        await message.answer(part, parse_mode="HTML")


@router.message(F.text, SummarizeFlow.waiting_pdf)
async def on_text_for_summary(message: Message, state: FSMContext) -> None:
    """يسمح بتلخيص نص مباشر بدل PDF."""
    await state.clear()
    summary = summarizer.summarize(message.text or "", max_sentences=8)
    if not summary.text:
        await message.answer("⚠️ النص قصير جدًا ولا يحتاج تلخيصًا.")
        return
    # `max_sentences=8` مع `max_chars=1800` يجعل التجاوز نظريًا، لكن الحدّ
    # لا يُترك للحظّ: نفس دالة التقسيم تُستخدم في كل ردّ نصّي طويل.
    for part in texts.split_for_send(
        texts.header("summarize", "الملخص") + f"\n\n{escape(summary.text)}"
    ):
        await message.answer(part, parse_mode="HTML")
