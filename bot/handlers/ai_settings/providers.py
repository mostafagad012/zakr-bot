"""استعراض المزوّدين واختيارهم: القائمة · البطاقة · التوصية · المقارنة · المفتاح والموديل."""
from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ... import texts
from ...models import User
from ...services import ai, ai_guide, audit, env_file, user_ai
from ...states import AiSetupFlow
from ..helpers import back_button, escape
from .core import log
from .core import (  # noqa: F401
    _deny, _provider_list, _running_exam, apply_to_env, can_manage_ai,
    intro_text, main_menu, priority_keyboard, provider_detail_keyboard,
    provider_detail_text, provider_keyboard, router, stored_key_notice,
    switch_warning,
)

@router.callback_query(F.data == "ai:menu")
async def cb_menu(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    row = await user_ai.get_row(db_session, db_user.id)
    configured = ai.is_configured()
    await callback.message.edit_text(
        intro_text(
            user_ai.active_label(row), configured,
            env_writable=env_file.is_writable(),
            env_name=env_file.env_path().name,
        ),
        reply_markup=main_menu(row.provider if row else "").as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(Command is None and F.data == "ai:help")
async def cb_help(callback: CallbackQuery, db_user: User) -> None:
    await cb_priority(callback, db_user)


async def cb_priority(callback: CallbackQuery, db_user: User) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    lines = [
        f"{texts.header('tutor', '🎯 ساعدني أختار')}",
        "",
        "سؤال واحد بس: <b>إيه أهم حاجة عندك في الذكاء الاصطناعي؟</b>",
        "",
    ]
    for _key, label, description in ai_guide.PRIORITIES:
        lines.append(f"{label} — <i>{escape(description)}</i>")
    lines += ["", "هختارلك 3 مناسبين وأوضّح مميزات وعيوب كل واحد."]
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=priority_keyboard().as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:pri:"))
async def cb_recommend(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    priority = callback.data.split(":", 2)[2]
    picks = ai_guide.recommend(priority, limit=3)

    label = next((lbl for key, lbl, _d in ai_guide.PRIORITIES if key == priority), priority)
    lines = [
        f"{texts.header('tutor', '🎯 ترشيحي لك')}",
        "",
        f"بناءً على اختيارك: <b>{escape(label)}</b>",
        "",
    ]
    builder = InlineKeyboardBuilder()
    for index, (key, _verdict) in enumerate(picks, start=1):
        provider = ai.PROVIDERS[key]
        tradeoff = ai_guide.tradeoff_summary(key)
        lines.append(f"{index}. <b>{escape(provider.label)}</b>")
        lines.append(f"   {escape(tradeoff)}")
        lines.append("")
        builder.row(InlineKeyboardButton(
            text=f"{index}. {provider.label} — التفاصيل",
            callback_data=f"ai:p:{key}",
        ))
    if not picks:
        lines.append("مفيش خيار مناسب لهذا الاهتمام — جرّب اهتمامًا آخر.")
    builder.row(back_button("⬅️ اهتمام آخر", "ai:help"))
    builder.row(back_button("⬅️ القائمة", "ai:menu"))

    await callback.message.edit_text(
        "\n".join(lines)[:4000], reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:list:"))
async def cb_list(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    try:
        page = int(callback.data.split(":")[-1])
    except ValueError:
        page = 0
    keys, total_pages = _provider_list(page)
    saved = await user_ai.saved_providers(db_session, db_user.id)

    lines = [
        f"{texts.header('tutor', '📋 كل الخيارات')}",
        "",
        "اضغط أي مزوّد لتقرأ مميزاته وعيوبه قبل ما تختار:",
        "",
    ]
    if saved:
        lines.append(
            "🔑 عندك مفاتيح محفوظة لـ: "
            + "، ".join(ai.PROVIDERS[k].label for k in saved if k in ai.PROVIDERS)
        )
        lines.append("")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=provider_keyboard(keys, page, total_pages).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:p:"))
async def cb_provider(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    key = callback.data.split(":", 2)[2]
    if key not in ai.PROVIDERS or key not in ai_guide.VERDICTS:
        await callback.answer("مزوّد غير معروف", show_alert=True)
        return

    masks = await user_ai.get_masked_keys(db_session, db_user.id)
    await callback.message.edit_text(
        provider_detail_text(key, masks.get(key, "")),
        reply_markup=provider_detail_keyboard(key, bool(masks.get(key))).as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:use:"))
async def cb_use(callback: CallbackQuery, db_user: User, db_session,
                 state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    """يختار المزوّد — أو يطلب مفتاحًا أولًا إن لزم."""
    key = callback.data.split(":", 2)[2]
    provider = ai.PROVIDERS.get(key)
    if provider is None:
        await callback.answer("مزوّد غير معروف", show_alert=True)
        return

    # المفتاح «الصالح» هو ما يصل فعلًا لطبقة الطلب: `.env` أو مفتاح خاص
    # بالمزوّد. أما مجرد وجود صف مشفّر في القاعدة فلا يكفي — وهذا كان
    # يمنع الأدمن من إدخال مفتاح جديد لأن الشاشة تقول «عندك مفتاح» وهو ميت.
    if provider.requires_key and not user_ai.has_usable_key(key):
        await state.set_state(AiSetupFlow.waiting_api_key)
        await state.update_data(ai_provider=key)
        await callback.message.edit_text(
            f"🔑 <b>مفتاح {escape(provider.label)}</b>\n\n"
            "الصق المفتاح في رسالة واحدة.\n\n"
            "🔒 <b>خصوصيتك:</b>\n"
            "• المفتاح يُخزَّن مشفّرًا في قاعدة بياناتك\n"
            "• لا يظهر في أي سجل ولا تُعرض إلا أول 4 وآخر 4 أحرف\n"
            "• تقدر تحذفه في أي وقت من «🔑 مفاتيحي»\n\n"
            + (f"🔗 هاته من: {escape(provider.key_url)}" if provider.key_url else ""),
            reply_markup=InlineKeyboardBuilder()
            .row(back_button("إلغاء", f"ai:p:{key}")).as_markup(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await callback.answer()
        return

    # المطلوب: «لما أضيف ذكاء اصطناعي، يحصل تغيير في .env»
    row = await user_ai.get_row(db_session, db_user.id)
    model = row.model if row else ""
    saved_key = (await user_ai.get_masked_keys(db_session, db_user.id)).get(key, "")

    # **ترتيب مقصود:** نكتب في `.env` أولًا، ولا نُثبّت الاختيار في القاعدة
    # إلا إذا نجحت الكتابة. كان العكس: لو كان `.env` للقراءة فقط (Railway)
    # أو رفض الملف القيمة، يبقى الاختيار مثبّتًا بلا أثر — فيظنّ الأدمن أنه
    # نجح بينما الطبقة لم تتغيّر، ويحتاج خطوة أخرى ليكتشف ذلك.
    result = apply_to_env(key, model=model)
    await audit.record_apply(db_session, db_user, result.changes, result.outcome)
    if result.outcome == "ok" or "الذاكرة" in result.message:
        # تطبيق في الذاكرة مقبول كذلك: يعمل الآن ويُنبَّه أنه يضيع عند إعادة
        # التشغيل (سلوك مقصود على الاستضافة ذات الملفات للقراءة فقط).
        await user_ai.set_provider(db_session, db_user.id, key)
    await db_session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🧪 اختبره الآن", callback_data="ai:test"))
    builder.row(InlineKeyboardButton(
        text="⚙️ اختر موديلًا محدّدًا", callback_data=f"ai:model:{key}"))
    builder.row(InlineKeyboardButton(
        text="📡 القائمة الحيّة للموديلات", callback_data=f"ai:models:{key}"))
    builder.row(InlineKeyboardButton(text="📝 ملاحظاتي", callback_data="ai:notes"))
    builder.row(back_button("⬅️ القائمة", "ai:menu"))

    exam_state = await _running_exam(state)
    warning = switch_warning(exam_state)
    await callback.message.edit_text(
        f"{'✅' if result.outcome == 'ok' else '⚠️'} <b>"
        f"{escape(provider.label)} صار مزوّد البوت</b>\n\n"
        f"🧠 الموديل: <code>{escape(ai._model())}</code>\n"
        f"{result.message}\n\n"
        "من دلوقتي كل شرح وتلخيص وامتحان عند <b>كل المستخدمين</b> "
        "هيستخدمه.\n"
        f"<i>ملاحظة: شاشة الـ AI مخفية عن الطلاب — {escape(saved_key or 'لا مفتاح محفوظ')}</i>"
        f"{warning}",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer("✅ تم وكتُب في .env")


@router.message(AiSetupFlow.waiting_api_key, F.text)
async def on_api_key(message: Message, db_user: User, db_session,
                     state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    key_name = data.get("ai_provider", "")
    api_key = (message.text or "").strip()

    # لو ضاعت الحالة (انتهت الجلسة أو أُعيد تشغيل البوت) المفتاح الملصوق
    # لا يجوز أن يُهدَر: نستخدم آخر مزوّد اختاره المستخدم من القاعدة.
    if key_name not in ai.PROVIDERS:
        row = await user_ai.get_row(db_session, db_user.id)
        key_name = (row.provider if row else "") or ""
    provider = ai.PROVIDERS.get(key_name)

    # امسح الرسالة فورًا — المفتاح لا يجوز أن يبقى في سجل الشات
    try:
        await message.delete()
    except Exception as exc:
        log.debug("تعذّر حذف رسالة المفتاح: %s", type(exc).__name__)

    if provider is None:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return

    if not api_key:
        await message.answer("⚠️ لم أجد مفتاحًا. جرّب مرة أخرى أو ألغِ.")
        return

    saved = await user_ai.set_key(db_session, db_user.id, key_name, api_key)
    if not saved:
        await message.answer(
            "⚠️ شكل المفتاح غير صحيح — تأكد أنه سطر واحد بلا مسافات داخله.",
        )
        return

    await user_ai.set_provider(db_session, db_user.id, key_name)
    await db_session.commit()
    await state.clear()

    # المفتاح يُكتب في `.env` **باسم مزوّده** (AI_API_KEY_GEMINI…) حتى يبقى
    # صالحًا بعد إعادة التشغيل، والمفتاح العام يُضبط أيضًا إن كان المزوّد
    # هو النشط — فالتغيير مطلوب في `.env` بحسب طلب الأدمن.
    result = apply_to_env(key_name, api_key=api_key)
    await audit.record_apply(db_session, db_user, result.changes, result.outcome)
    await db_session.commit()

    # نقيس **هذا** المفتاح على **هذا** المزوّد — لا المزوّد العام في `.env`.
    # قبل ذلك كان مستخدم يحفظ مفتاحًا سليمًا يقرأ «❌ غير صالح» لأن المزوّد
    # العام معطوب، وآخر يقرأ «✅ يعمل» ومفتاحه لا يعمل.
    #
    # وبلا `model` صريح: موديل مستخدمٍ آخر (أو الموديل العام) قد لا يكون
    # موجودًا عند هذا المزوّد، فيُقرأ الرفض كأنه عيب في المفتاح.
    _ok, report = await ai.healthcheck(provider=key_name, api_key=api_key)

    await message.answer(
        f"🔑 <b>تم حفظ مفتاح {escape(provider.label)}</b>\n"
        f"المعروض: <code>{escape(user_ai.mask_key(api_key))}</code> "
        "<i>(حُذفت رسالتك الأصلية)</i>\n"
        f"{result.message}\n\n"
        f"{escape(report)}",
        reply_markup=main_menu(key_name).as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai:model:"))
async def cb_model(callback: CallbackQuery, db_user: User, db_session,
                   state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    key = callback.data.split(":", 2)[2]
    provider = ai.PROVIDERS.get(key)
    if provider is None:
        await callback.answer("مزوّد غير معروف", show_alert=True)
        return
    await state.set_state(AiSetupFlow.waiting_model)
    await state.update_data(ai_provider=key)
    await callback.message.edit_text(
        f"⚙️ <b>موديل {escape(provider.label)}</b>\n\n"
        f"الافتراضي: <code>{escape(provider.default_model or '—')}</code>\n\n"
        "اكتب اسم الموديل، أو اضغط «القائمة الحيّة» لجلب الأسماء المتاحة الآن.\n"
        "<i>أسماء الموديلات تتغيّر بسرعة — القائمة الحيّة أصدق من أي قائمة ثابتة.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text="📡 القائمة الحيّة", callback_data=f"ai:models:{key}"))
        .row(InlineKeyboardButton(text="↩️ الافتراضي", callback_data=f"ai:usedefault:{key}"))
        .row(back_button("إلغاء", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSetupFlow.waiting_model, F.text)
async def on_model(message: Message, db_user: User, db_session,
                   state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    key = data.get("ai_provider", "")
    if key not in ai.PROVIDERS:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية.")
        return
    model = (message.text or "").strip()[:120]
    if not model or " " in model:
        await message.answer("⚠️ اسم الموديل سطر واحد بلا مسافات.")
        return

    await user_ai.set_provider(db_session, db_user.id, key, model)
    await db_session.commit()
    await state.clear()
    await message.answer(
        f"✅ تم — موديل <code>{escape(model)}</code> محفوظ لـ {escape(ai.PROVIDERS[key].label)}",
        reply_markup=main_menu(key).as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai:usedefault:"))
async def cb_use_default(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    key = callback.data.split(":", 2)[2]
    if key in ai.PROVIDERS:
        await user_ai.set_provider(db_session, db_user.id, key, "")
        await db_session.commit()
    await callback.message.edit_text(
        "✅ رجع للموديل الافتراضي.",
        reply_markup=main_menu(key if key in ai.PROVIDERS else "").as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:models:"))
async def cb_live_models(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    """يعرض أسماء الموديلات الحقيقية من المزوّد — لا تخمين."""
    key = callback.data.split(":", 2)[2]
    if key not in ai.PROVIDERS:
        await callback.answer("مزوّد غير معروف", show_alert=True)
        return

    await callback.answer("⏳ جاري الجلب…")
    # القائمة تُجلب بمفتاح المزوّد المطلوب بلا تغيير الإعداد النشط:
    # `key_for` يقرأ مفتاح كل مزوّد من البيئة/المحفوظ مباشرة.
    try:
        names = await ai.list_models(provider_key=key)
    except ai.AIError as exc:
        await callback.message.edit_text(
            f"⚠️ {escape(str(exc))}",
            reply_markup=InlineKeyboardBuilder()
            .row(back_button("⬅️ رجوع", f"ai:model:{key}")).as_markup(),
            parse_mode="HTML",
        )
        return

    names = [n for n in names if n]
    shown = names[:25]
    lines = [
        f"📡 <b>موديلات {escape(ai.PROVIDERS[key].label)}</b> ({len(names)} متاح)",
        "",
    ]
    lines += [f"  <code>{escape(n)}</code>" for n in shown]
    if len(names) > len(shown):
        lines.append(f"  … و{len(names) - len(shown)} غيرها")
    lines += ["", "انسخ أي اسم واضغط «⚙️ اختر موديلًا» ثم الصقه."]

    await callback.message.edit_text(
        "\n".join(lines)[:4000],
        reply_markup=InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text="⚙️ اكتب الموديل", callback_data=f"ai:model:{key}"))
        .row(back_button("⬅️ رجوع", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )


# ------------------------------------------------------------- المقارنة
@router.callback_query(F.data == "ai:cmp")
async def cb_compare(callback: CallbackQuery, db_user: User) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    keys = [k for k in ai_guide.compare_keys() if k in ai_guide.VERDICTS][:5]
    lines = [f"{texts.header('tutor', '⚖️ مقارنة سريعة')}", ""]
    for key in keys:
        provider = ai.PROVIDERS[key]
        verdict = ai_guide.VERDICTS[key]
        lines += [
            f"<b>{escape(provider.label)}</b>",
            f"  💰{verdict.free} 🎯{verdict.quality} 🇪🇬{verdict.arabic} "
            f"⚡{verdict.speed} 🛠️{verdict.ease} 🔒{verdict.privacy}",
            f"  {escape(ai_guide.tradeoff_summary(key))}",
            "",
        ]
    lines.append("<i>الترتيب: 💰 مجاني · 🎯 جودة · 🇪🇬 عربية · ⚡ سرعة · 🛠️ سهولة · 🔒 خصوصية</i>")

    builder = InlineKeyboardBuilder()
    row = []
    for key in keys:
        row.append(InlineKeyboardButton(
            text=ai.PROVIDERS[key].label[:12], callback_data=f"ai:p:{key}"
        ))
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(back_button("⬅️ رجوع", "ai:menu"))

    await callback.message.edit_text(
        "\n".join(lines)[:4000], reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()
