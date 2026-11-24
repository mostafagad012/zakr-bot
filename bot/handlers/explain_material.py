"""الشرح من مادة (PDF/ملف مكتبة) — مربوط بزرّ «الشرح».

**التدفّق المطلوب من المستخدم حرفيًا:**

    يضغط «اشرح» ← يختار مادة (PDF يرفعه أو ملفًا من مكتبته)
    ← يختار نوع الشرح ومستوى التفصيل واللغة من أزرار قدامه
    ← يكتب برومبته الحر (اختياري)
    ← يبدأ التحليل، والـAI يجيب **نفس اللي كتبه بالضبط حتى لو فيه رموز**

**كيف يتحقّق «بالضبط»؟** الحفظ الحرفي مسؤولية `bot/services/verbatim.py`:
رموز برومبت المستخدم تُخفى خلف علامات محميّة قبل إرسالها للنموذج، ويُطلب
من النموذج إعادتها كما هي؛ وعند العرض تُفكّ **بعد** تهريب HTML، فتعود
حرفية معروضة بدل أن تُهشَّم أو تُفسَّر كوسوم.

**لماذا الاختيارات أزرار لا كتابة حرة؟** لأن المستخدم قال «على حسب
الاختيارات الموجودة قدامه» — أزرار تعرض الخيارات صريحة وتُخزَّن في FSM،
فيبقى الخيار مرئيًا وقابلًا للتغيير قبل التشغيل بلا إعادة كتابة.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User
from ..services import files as files_service
from ..services import material, ocr as ocr_service, pdf, verbatim
from ..states import MaterialExplainFlow
from .helpers import back_button, escape

log = logging.getLogger(__name__)
router = Router(name="explain_material")

MAX_CALLBACK = 60
SEND_LIMIT = 3800

#: مفتاح تخزين الاختيارات داخل FSM
_KEY = "mat"


def _cd(value: str) -> str:
    if len(value.encode()) > MAX_CALLBACK:
        raise ValueError(f"callback_data طويل: {value}")
    return value


# ─────────────────────────────────────────────── شاشة البداية
def source_menu() -> object:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="📄 أرسل PDF أشرحه الآن", callback_data="exp:pdf"))
    builder.row(InlineKeyboardButton(
        text="📚 اختر من مكتبتي", callback_data="exp:lib:0"))
    builder.row(InlineKeyboardButton(
        text="📝 الصق نصًا أشرحه", callback_data="exp:text"))
    builder.row(back_button(callback_data="tut:explain"))
    return builder.as_markup()


async def show_source(callback: CallbackQuery) -> None:
    body = (
        texts.header("explain", subtitle="اشرح من مادتك — تحليل حقيقي للـPDF")
        + "\n\n"
        "🧠 <b>هحلّل الملف بنفسي</b> (أقسام · تعريفات · معادلات · أسئلة)\n"
        "بعدها تختار نوع الشرح ومستوى التفصيل، وتكتب برومبتك الحر —\n"
        "<b>وكل رمز تكتبه هيتحفظ حرفيًا في الشرح.</b>"
    )
    await callback.message.edit_text(body, reply_markup=source_menu(),
                                    parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "exp:start")
async def cb_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MaterialExplainFlow.waiting_file)
    await state.update_data(**{_KEY: _defaults()})
    await show_source(callback)


def _defaults() -> dict[str, object]:
    """الاختيارات الافتراضية — مطابقة لـ`ExplainOptions`."""
    return {
        "mode": material.MODE_EXPLAIN,
        "depth": material.DEPTH_NORMAL,
        "language": material.LANG_MIXED,
        "prompt": "",
        "text": "",
        "title": "",
    }


# ─────────────────────────────────────────── اختيار ملف من المكتبة
@router.callback_query(F.data.startswith("exp:lib:"))
async def cb_library(callback: CallbackQuery, db_user: User, db_session,
                     state: FSMContext) -> None:
    page = int(callback.data.split(":")[-1])
    items = await files_service.user_files(db_session, db_user.id, limit=100)
    # نُظهر أيضًا الملفات المنشورة إن لم يكن عنده ملفات
    if not items:
        items = await files_service.recent_published(db_session, limit=30) \
            if hasattr(files_service, "recent_published") else []

    page_size = 8
    chunk = items[page * page_size:(page + 1) * page_size]

    builder = InlineKeyboardBuilder()
    if not chunk:
        builder.row(InlineKeyboardButton(
            text="لا توجد ملفات — أرسل PDF", callback_data="exp:pdf"))
    for item in chunk:
        icon = {"pdf": "📄", "docx": "📝", "image": "🖼️"}.get(item.file_kind or "", "📎")
        builder.row(InlineKeyboardButton(
            text=f"{icon} {item.title[:34]}",
            callback_data=_cd(f"exp:use:{item.id}")))
    if len(items) > page_size:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=_cd(f"exp:lib:{page-1}")))
        nav.append(InlineKeyboardButton(text=f"{page+1}", callback_data="noop:page"))
        if (page + 1) * page_size < len(items):
            nav.append(InlineKeyboardButton(text="➡️", callback_data=_cd(f"exp:lib:{page+1}")))
        builder.row(*nav)
    builder.row(back_button("⬅️ رجوع", "exp:start"))

    await callback.message.edit_text(
        texts.header("explain", subtitle="اختر مادة من مكتبتك")
        + "\n\n<i>هقرأ الملف وأحلّله ثم أشرحه حسب اختياراتك.</i>",
        reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("exp:use:"))
async def cb_use_library_file(callback: CallbackQuery, db_user: User, db_session,
                              state: FSMContext) -> None:
    """يستخدم ملفًا مفهرسًا من المكتبة: نصّه المستخرج جاهز بلا تنزيل."""
    file_id = int(callback.data.split(":")[-1])
    item = await files_service.get_file(db_session, file_id)
    if item is None:
        await callback.answer("الملف غير موجود", show_alert=True)
        return

    text = await files_service.get_indexed_text(db_session, file_id)
    if not text or len(text.strip()) < material.tutor.MIN_TEXT_CHARS:
        await callback.answer(
            "مفيش نص مستخرج من الملف ده — ارفعه هنا كـPDF مباشرة.",
            show_alert=True)
        return

    await state.update_data(**{
        f"{_KEY}_text": text,
        f"{_KEY}_title": item.title,
    })
    await state.set_state(MaterialExplainFlow.choosing_mode)
    await _show_options(callback, state, kind="mode")


# ─────────────────────────────────────────── استقبال PDF مباشرة
@router.callback_query(F.data == "exp:pdf")
async def cb_ask_pdf(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MaterialExplainFlow.waiting_file)
    await callback.message.edit_text(
        texts.header("explain", subtitle="أرسل ملف PDF الآن")
        + "\n\n"
        "📄 ابعت الملف وأنا هحلّله: أقسام، تعريفات، معادلات، وأسئلة.\n"
        "🔎 ولو الملف مصوّر (سكانر) هقراه بـOCR تلقائيًا.\n\n"
        "<i>الحدّ الأقصى للحجم يحدده تيليجرام (٢٠ ميجا، أو ٢ جيجا مع "
        "Bot API محلي).</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("✖️ إلغاء", "exp:start")).as_markup(),
        parse_mode="HTML")
    await callback.answer()


@router.message(MaterialExplainFlow.waiting_file, F.document)
async def on_pdf(message: Message, db_user: User, db_session,
                 state: FSMContext) -> None:
    name = message.document.file_name or "ملف"
    is_pdf = (name.lower().endswith(".pdf")
              or (message.document.mime_type or "") == "application/pdf")
    if not is_pdf:
        await message.answer("⚠️ أرسل ملف PDF فقط (أو اختر من مكتبتك).")
        return

    notice = await message.answer("⏳ جارٍ تنزيل الملف…")
    try:
        buffer = await message.bot.download(message.document)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
    except Exception as exc:                       # noqa: BLE001
        log.warning("فشل تنزيل PDF للشرح: %s", exc)
        await _safe_delete(notice)
        await message.answer("⚠️ تعذّر تنزيل الملف — جرّب تاني.")
        return
    await _safe_delete(notice)

    # استخراج النص: pypdf أولًا، ثم OCR للملفات المصوّرة
    try:
        extracted = pdf.extract_text(data)
        text = extracted.text or ""
    except Exception as exc:                       # noqa: BLE001
        log.info("فشل استخراج نص PDF: %s", type(exc).__name__)
        text = ""

    if len(text.strip()) < material.tutor.MIN_TEXT_CHARS:
        note = await message.answer("🔎 الملف مصوّر — جارٍ قراءته بـOCR…")
        try:
            result = await ocr_service.extract_text_from_pdf(data)
            text = result.text or ""
        except Exception as exc:                   # noqa: BLE001
            log.info("تعذّر OCR للشرح: %s", type(exc).__name__)
            text = ""
        await _safe_delete(note)

    if len(text.strip()) < material.tutor.MIN_TEXT_CHARS:
        await message.answer(
            "⚠️ <b>مفيش نص أستخرجه من الملف.</b>\n\n"
            "لو الملف صور/سكانر، لازم OCR — تأكد إن tesseract مثبّت "
            "(السكربت بيثبّته تلقائيًا).\n"
            "ولو الملف محمي بكلمة سر، فكّ الحماية وابعته تاني.",
            parse_mode="HTML")
        return

    await state.update_data(**{f"{_KEY}_text": text, f"{_KEY}_title": name[:80]})
    await state.set_state(MaterialExplainFlow.choosing_mode)
    await _show_options_msg(message, state, kind="mode")


async def _safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except Exception as exc:                       # noqa: BLE001
        log.debug("تعذّر حذف رسالة الحالة: %s", type(exc).__name__)


# ─────────────────────────────────────────── نص ملصوق
@router.callback_query(F.data == "exp:text")
async def cb_ask_text(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MaterialExplainFlow.waiting_file)
    await callback.message.edit_text(
        "📝 <b>الصق النص اللي عايز أشرحه</b> في رسالة واحدة.\n\n"
        "<i>لو طويل، اقسمه — أو ارفعه كملف PDF.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("✖️ إلغاء", "exp:start")).as_markup(),
        parse_mode="HTML")
    await callback.answer()


@router.message(MaterialExplainFlow.waiting_file, F.text)
async def on_pasted_text(message: Message, state: FSMContext) -> None:
    body = (message.text or "").strip()
    if len(body) < material.tutor.MIN_TEXT_CHARS:
        await message.answer("⚠️ النص قصير جدًا — اكتب فقرة كاملة على الأقل.")
        return
    await state.update_data(**{f"{_KEY}_text": body, f"{_KEY}_title": "نص ملصوق"})
    await state.set_state(MaterialExplainFlow.choosing_mode)
    await _show_options_msg(message, state, kind="mode")


# ─────────────────────────────────────────── شاشات الاختيارات
async def _options_kb(db_session, state: FSMContext, kind: str):
    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    builder = InlineKeyboardBuilder()

    if kind == "mode":
        current = cfg.get("mode", material.MODE_EXPLAIN)
        for key, label in material.MODES.items():
            mark = "✅ " if key == current else ""
            builder.row(InlineKeyboardButton(
                text=f"{mark}{label}", callback_data=_cd(f"exp:set:mode:{key}")))
        builder.row(InlineKeyboardButton(
            text="التالي: مستوى التفصيل ➡️", callback_data="exp:step:depth"))
    elif kind == "depth":
        current = cfg.get("depth", material.DEPTH_NORMAL)
        for key, label in material.DEPTHS.items():
            mark = "✅ " if key == current else ""
            builder.row(InlineKeyboardButton(
                text=f"{mark}{label}", callback_data=_cd(f"exp:set:depth:{key}")))
        builder.row(InlineKeyboardButton(
            text="التالي: لغة الشرح ➡️", callback_data="exp:step:language"))
    elif kind == "language":
        current = cfg.get("language", material.LANG_MIXED)
        for key, label in material.LANGUAGES.items():
            mark = "✅ " if key == current else ""
            builder.row(InlineKeyboardButton(
                text=f"{mark}{label}", callback_data=_cd(f"exp:set:language:{key}")))
        builder.row(InlineKeyboardButton(
            text="التالي: برومبتك الحر ✍️", callback_data="exp:step:prompt"))
    else:                                   # المراجعة النهائية
        builder.row(InlineKeyboardButton(
            text="🚀 ابدأ الشرح", callback_data="exp:go"))
        builder.row(InlineKeyboardButton(
            text="✍️ اكتب/غيّر البرومبت", callback_data="exp:step:prompt"))

    builder.row(InlineKeyboardButton(text="✖️ إلغاء", callback_data="exp:start"))
    return builder.as_markup()


async def _options_body(db_session, state: FSMContext, kind: str) -> str:
    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    title = cfg.get("title") or "مادة"
    mode = material.MODES.get(cfg.get("mode", ""), "—")
    depth = material.DEPTHS.get(cfg.get("depth", ""), "—")
    lang = material.LANGUAGES.get(cfg.get("language", ""), "—")
    prompt = str(cfg.get("prompt") or "")

    head = texts.header("explain", subtitle=f"مادتك: {escape(title[:60])}")
    status = (
        f"\n\n<b>اختياراتك الحالية:</b>\n"
        f"• نوع الشرح: <b>{mode}</b>\n"
        f"• التفصيل: <b>{depth}</b>\n"
        f"• اللغة: <b>{lang}</b>\n"
    )
    if prompt:
        status += (f"\n<b>برومبتك المحفوظ حرفيًا:</b>\n"
                   f"<code>{escape(prompt[:400])}</code>\n"
                   f"<i>{verbatim.verbatim_report(prompt)}</i>")
    else:
        status += "\n<i>لسه مفيش برومبت حر — تقدر تكتبه في الخطوة الأخيرة.</i>"

    step = {"mode": "١/٤ نوع الشرح",
            "depth": "٢/٤ مستوى التفصيل",
            "language": "٣/٤ لغة الشرح",
            "review": "٤/٤ مراجعة وبدء"}.get(kind, "")
    return f"{head}\n\n<b>{step}</b>{status}"


async def _show_options(callback: CallbackQuery, state: FSMContext, kind: str) -> None:
    await callback.message.edit_text(
        await _options_body(None, state, kind),
        reply_markup=await _options_kb(None, state, kind), parse_mode="HTML")
    await callback.answer()


async def _show_options_msg(message: Message, state: FSMContext, kind: str) -> None:
    await message.answer(
        await _options_body(None, state, kind),
        reply_markup=await _options_kb(None, state, kind), parse_mode="HTML")


@router.callback_query(F.data.startswith("exp:set:"))
async def cb_set_option(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, field, value = callback.data.split(":", 3)
    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    cfg[field] = value
    await state.update_data(**{_KEY: cfg})
    await _show_options(callback, state, kind=field)


@router.callback_query(F.data.startswith("exp:step:"))
async def cb_step(callback: CallbackQuery, state: FSMContext) -> None:
    step = callback.data.split(":")[-1]
    if step == "prompt":
        await _ask_prompt(callback, state)
        return
    await _show_options(callback, state, kind=step)


async def _ask_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MaterialExplainFlow.waiting_prompt)
    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    current = str(cfg.get("prompt") or "")
    body = (
        "✍️ <b>اكتب برومبتك الحر</b> (اختياري)\n\n"
        "اكتب بالظبط اللي عايزه، وأنا هشرح على أساسه — <b>وكل رمز أو "
        "معادلة تكتبها هتظهر زي ما كتبتها بالظبط</b> كمان.\n\n"
        "أمثلة:\n"
        "• <code>اشرح علاقة V = I × R بمثال عملي</code>\n"
        "• <code>قارن بين ≤ و ≥ في المتباينات</code>\n"
        "• <code>اشرح ٣ نقاط بس، بأسلوب امتحان</code>\n\n"
        "<i>لو مش عايز برومبت، اضغط «بدون برومبت».</i>"
    )
    if current:
        body += f"\n\n<b>المحفوظ حاليًا:</b>\n<code>{escape(current[:400])}</code>"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⏭️ بدون برومبت", callback_data="exp:step:review"))
    if current:
        builder.row(InlineKeyboardButton(
            text="🧹 امسح البرومبت", callback_data="exp:clearprompt"))
    builder.row(InlineKeyboardButton(text="✖️ إلغاء", callback_data="exp:start"))
    await callback.message.edit_text(body, reply_markup=builder.as_markup(),
                                     parse_mode="HTML")
    await callback.answer()


@router.message(MaterialExplainFlow.waiting_prompt, F.text)
async def on_prompt(message: Message, state: FSMContext) -> None:
    """يحفظ البرومبت **حرفيًا** — بلا تنقية ولا تعديل.

    لا `.strip()` على الداخل: المسافات جزء من نص المستخدم. النقطاع
    الوحيد هو الحدّ الأقصى (١٥٠٠ حرف) المعلن في الشاشة.
    """
    raw = message.text or ""
    if len(raw) > material.MAX_PROMPT_CHARS:
        await message.answer(
            f"⚠️ البرومبت طويل ({len(raw)} حرفًا) — الحدّ "
            f"{material.MAX_PROMPT_CHARS}. اختصره شوية.",
            parse_mode="HTML")
        return

    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    cfg["prompt"] = raw
    await state.update_data(**{_KEY: cfg})
    await state.set_state(MaterialExplainFlow.choosing_mode)

    report = verbatim.verbatim_report(raw)
    await message.answer(
        f"✅ <b>حفظت برومبتك حرفيًا.</b>\n\n"
        f"<code>{escape(raw[:600])}</code>\n\n<i>{escape(report)}</i>",
        parse_mode="HTML")
    await _show_options_msg(message, state, kind="review")


@router.callback_query(F.data == "exp:clearprompt")
async def cb_clear_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    cfg["prompt"] = ""
    await state.update_data(**{_KEY: cfg})
    await state.set_state(MaterialExplainFlow.choosing_mode)
    await callback.answer("🧹 اتمسح")
    await _show_options(callback, state, kind="review")


# ─────────────────────────────────────────── التشغيل
@router.callback_query(F.data == "exp:go")
async def cb_go(callback: CallbackQuery, db_user: User, db_session,
                state: FSMContext) -> None:
    data = await state.get_data()
    cfg = data.get(_KEY, _defaults())
    text = str(cfg.get("text") or "")
    title = str(cfg.get("title") or "")

    if len(text.strip()) < material.tutor.MIN_TEXT_CHARS:
        await callback.answer("مفيش مادة محمّلة — ابدأ من الأول", show_alert=True)
        return

    options = material.ExplainOptions(
        mode=str(cfg.get("mode") or material.MODE_EXPLAIN),
        depth=str(cfg.get("depth") or material.DEPTH_NORMAL),
        language=str(cfg.get("language") or material.LANG_MIXED),
        prompt=str(cfg.get("prompt") or ""),
    ).normalized()

    await callback.answer("🚀 جارٍ التحليل…")
    await state.clear()

    # تعديل الرسالة إلى حالة "جارٍ" — بدل رسالة حالة منفصلة تُترك عالقة
    try:
        await callback.message.edit_text(
            f"🧠 <b>بحلّل المادة…</b>\n\n"
            f"📄 {escape(title[:80])}\n"
            f"<i>{escape(material.MODES[options.mode])} · "
            f"{escape(material.DEPTHS[options.depth])}</i>",
            parse_mode="HTML")
    except Exception:                              # noqa: BLE001
        pass

    from ..services import ai_usage, learner
    from ..services import ai as ai_service

    allowed, _u, _l = await ai_usage.check(db_session, db_user.id)
    if not allowed:
        await callback.message.answer(
            await ai_usage.limit_message(db_session, db_user.id), parse_mode="HTML")
        return

    profile = await learner.get_profile(db_session, db_user.id)
    result = await material.explain_material(text, options, profile, title=title)

    # الحماية تُعاد بناؤها هنا بنفس نص البرومبت، لتعمل مع فكّ الرموز عند العرض
    protected = verbatim.protect(options.prompt) if options.prompt else None

    if not result.ok:
        await callback.message.answer(
            f"⚠️ {escape(result.note or 'تعذّر الشرح.')}", parse_mode="HTML")
        return

    await ai_usage.record(db_session, db_user.id, "explain",
                          ok=True, provider=ai_service.get_provider().key)
    await db_session.commit()

    body = material.render(result, title=title, options=options, protected=protected)
    await _send_long(callback.message, body)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="🔄 اشرح بنمط تاني", callback_data="exp:start"))
    builder.row(InlineKeyboardButton(
        text="🎨 تعديل أسلوبي", callback_data="lrn:start"))
    builder.row(back_button(callback_data="menu:main"))
    await callback.message.answer("تحب نعمل إيه كمان؟",
                                 reply_markup=builder.as_markup())


async def _send_long(message: Message, body: str) -> None:
    """يرسل نصًا طويلًا على رسائل عند حدود الكلمات."""
    remaining = (body or "").strip()
    first = True
    while remaining:
        if len(remaining) <= SEND_LIMIT:
            part, remaining = remaining, ""
        else:
            window = remaining[:SEND_LIMIT]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
            if cut < SEND_LIMIT // 2:
                cut = SEND_LIMIT
            part, remaining = remaining[:cut].rstrip(), remaining[cut:].lstrip()
        kwargs = {"parse_mode": "HTML", "disable_web_page_preview": True}
        if first:
            await message.answer(part, **kwargs)
            first = False
        else:
            await message.answer(part, **kwargs)
