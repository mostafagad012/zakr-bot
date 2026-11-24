"""إدارة المفاتيح: العرض · التسمية · النقل إلى `.env` · الحذف · الاختبار."""
from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ... import texts
from ...models import User
from ...services import agents as agents_service
from ...services import ai, audit, env_file, user_ai
from ...states import AiSetupFlow
from ..helpers import back_button, escape
from .core import log
from .core import (  # noqa: F401
    _deny, _provider_list, _running_exam, apply_to_env, can_manage_ai,
    intro_text, main_menu, priority_keyboard, provider_detail_keyboard,
    provider_detail_text, provider_keyboard, router, stored_key_notice,
    switch_warning,
)

def keys_text() -> str:
    """جدول المفاتيح: الاسم · الحالة · مصدره (وكيل/ثابت/قديم)."""
    raw_names = env_file.list_editable_keys()
    # (الاسم القياسي، أول شكل ظهر في الملف) — لا صفّين لنفس المتغيّر
    names: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        canonical = env_file.canonical_key(raw)
        if canonical not in seen:
            seen.add(canonical)
            names.append(canonical)
    lines = [f"{texts.header('tutor', ' إدارة مفاتيح الـ API')}", ""]
    if not names:
        lines += [
            "<i>لا يوجد أي متغيّر مفتاح في <code>.env</code> بعد.</i>",
            "",
            "اضغط «➕ أضف وكيلًا/مفتاحًا» لتبدأ — أو أضف مزوّدًا كاملًا.",
        ]
        return "\n".join(lines)

    keys = [n for n in names if env_file.split_agent_var(n)
            and env_file.split_agent_var(n)[1] == "_api_key"]
    others = [n for n in names if n not in keys]

    lines.append(f"🔑 <b>المفاتيح ({len(keys)})</b>")
    for name in keys:
        state = env_file.key_state(name)
        if name.replace("_api_key", "") in agents_service.RESERVED_PROVIDERS:
            origin = "مزوّد ثابت"
        else:
            origin = "وكيل مخصّص"
        lines.append(f"• <code>{escape(name)}</code> — "
                     f"{KEY_STATE_MARKS.get(state, state)} · <i>{origin}</i>")

    if others:
        lines += ["", f"🧩 <b>عناوين وموديلات ({len(others)})</b>"]
        for name in others:
            mark = "✅" if env_file.read_value(name) else "⚠️ فارغ"
            lines.append(f"• <code>{escape(name)}</code> — {mark}")

    # مزوّدون معروفون بلا مفتاح في الملف — أهم حالة «إضافة»: المستخدم
    # يريد مفتاحًا لمزوّد يعرفه، ولا يوجد سطر بعد فلا زر «تحديث» له.
    missing = [name for name in agents_service.RESERVED_PROVIDERS
               if not env_file.read_value(f"{name}_api_key")]
    if missing:
        lines += ["", f"➕ <b>بلا مفتاح ({len(missing)}):</b> "
                      + " · ".join(missing[:8]) + (" …" if len(missing) > 8 else "")]

    active = ai.get_provider().key
    lines += [
        "",
        f"⚙️ المزوّد النشط الآن: <b>{escape(active)}</b>",
        "🔒 القيم لا تُعرض — الحالة فقط. اضغط على المفتاح لتعديله.",
    ]
    return "\n".join(lines)[:4000]


def missing_providers() -> list[str]:
    """مزوّدون ثابتون ليس لهم مفتاح في الملف — مرشّحو «إضافة مفتاح»."""
    return [name for name in agents_service.RESERVED_PROVIDERS
            if not env_file.read_value(env_file.provider_key_name(name))]


def keys_menu() -> InlineKeyboardBuilder:
    """أزرار الشاشة: مفتاح واحد في كل صف (تحديث · تسمية · حذف)."""
    builder = InlineKeyboardBuilder()
    shown: set[str] = set()
    for name in map(env_file.canonical_key, env_file.list_editable_keys()):
        parsed = env_file.split_agent_var(name)
        if parsed is None or parsed[1] != "_api_key" or name in shown:
            continue
        shown.add(name)
        short = name.replace("_api_key", "")
        builder.row(
            InlineKeyboardButton(text=f"✏️ حدّث {short}",
                                 callback_data=f"ai:key:set:{name}"),
            InlineKeyboardButton(text=f"🏷️ سمّ {short}",
                                 callback_data=f"ai:key:ren:{name}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"ai:key:del:{name}"),
        )
    missing = missing_providers()
    # الأكثر استخدامًا أولًا: صفّان فقط، والباقي في شاشة «مزوّد آخر»
    for index in range(0, min(len(missing), 4), 2):
        builder.row(*[
            InlineKeyboardButton(text=f"➕ مفتاح {name}",
                                 callback_data=f"ai:key:new:{name}")
            for name in missing[index:index + 2]
        ])
    if len(missing) > 4:
        builder.row(InlineKeyboardButton(
            text=f"➕ مزوّد آخر ({len(missing) - 4})",
            callback_data="ai:keys:add"))
    builder.row(InlineKeyboardButton(
        text="➕ أضف وكيلًا/مفتاحًا جديدًا", callback_data="ai:agent:new"))
    builder.row(InlineKeyboardButton(
        text="🗄️ مفاتيح القاعدة (النسخة القديمة)", callback_data="ai:keys:saved"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    return builder



KEY_STATE_MARKS = {
    "set": "✅ مضبوط",
    "empty": "⚠️ فارغ",
    "duplicate": "🔀 مكرّر بصيغتين",
}

@router.callback_query(F.data == "ai:keys")
async def cb_keys_screen(callback: CallbackQuery, db_user: User) -> None:
    """شاشة إدارة المفاتيح (كانت سابقًا شاشة المفاتيح المحفوظة في القاعدة)."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    if not env_file.is_writable():
        await callback.answer(
            "⚠️ .env غير قابل للكتابة هنا (Railway/Vercel) — "
            "عدّل المفاتيح من لوحة الاستضافة",
            show_alert=True,
        )
        return
    await callback.message.edit_text(
        keys_text(), reply_markup=keys_menu().as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ai:key:set:"))
async def cb_key_set(callback: CallbackQuery, db_user: User,
                     state: FSMContext) -> None:
    """تحديث قيمة مفتاح موجود — بلا حذف وإعادة إضافة."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    name = callback.data.split(":", 3)[3]
    if not env_file.is_allowed(name):
        await callback.answer("متغيّر غير معروف", show_alert=True)
        return
    await state.set_state(AiSetupFlow.waiting_key_value)
    await state.update_data(key_name=name)

    current = env_file.read_value(name)
    shown = f"<code>{escape(user_ai.mask_key(current))}</code>" if current \
        else "<i>فارغ</i>"
    await callback.message.edit_text(
        f"✏️ <b>تحديث <code>{escape(name)}</code></b>\n\n"
        f"القيمة الحالية: {shown}\n\n"
        "أرسل <b>القيمة الجديدة</b> الآن.\n\n"
        "🔒 رسالتك ستُحذف فورًا، ولن تظهر كاملة في أي شاشة أو سجل.\n"
        "↩️ <i>غيّرت رأيك؟ «رجوع» — لا شيء يتغيّر.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("رجوع", "ai:keys")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSetupFlow.waiting_key_value, F.text)
async def on_key_value(message: Message, db_user: User, db_session,
                       state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    name = data.get("key_name", "")
    if not name:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return

    raw = (message.text or "").strip()
    try:
        await message.delete()          # المفتاح لا يبقى في سجل الشات
    except Exception as exc:
        log.debug("تعذّر حذف رسالة المفتاح: %s", type(exc).__name__)

    if raw.lower() in ("الغاء", "إلغاء", "cancel", "/cancel"):
        await state.clear()
        await message.answer("أُلغي — لم يتغيّر شيء.",
                             reply_markup=main_menu().as_markup())
        return
    if len(raw) < 8 or len(raw) > 300 or any(ch.isspace() for ch in raw):
        await message.answer(
            "⚠️ القيمة غير معقولة (طولها أو فيها مسافات).\n"
            "أعد إرسالها — أو «إلغاء» للخروج.",
            reply_markup=InlineKeyboardBuilder()
            .row(back_button("إلغاء", "ai:keys")).as_markup(),
            parse_mode="HTML")
        return

    before = env_file.read_value(name)
    result = env_file.update_key(name, raw)
    await state.clear()
    await audit.record_apply(db_session, db_user, result.changed, 
                             "ok" if result.ok else "failed")
    await db_session.commit()

    ok_now, report = await ai.healthcheck()
    lines = [f"<b>{escape(name)}</b>", result.message]
    if result.ok:
        lines.append(f"🔑 المعروض: <code>{escape(user_ai.mask_key(raw))}</code>")
        lines.append(f"قبل: <code>{escape(user_ai.mask_key(before)) or '—'}</code>")
        lines.append("")
        lines.append(escape(report))
    await message.answer(
        "\n".join(lines),
        reply_markup=main_menu(ai.get_provider().key).as_markup(),
        parse_mode="HTML")
    await message.answer("✅ المفتاح يعمل" if ok_now else "⚠️ راجع الرسالة أعلاه")


@router.callback_query(F.data == "ai:keys:add")
async def cb_keys_add(callback: CallbackQuery, db_user: User) -> None:
    """كل المزوّدين الذين يمكن إضافة مفتاح لهم (ثلاثة في السطر)."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    missing = missing_providers()
    builder = InlineKeyboardBuilder()
    for index in range(0, len(missing), 3):
        builder.row(*[
            InlineKeyboardButton(
                text=ai.PROVIDERS[name].label.split(" (")[0]
                if name in ai.PROVIDERS else name,
                callback_data=f"ai:key:new:{name}")
            for name in missing[index:index + 3]
        ])
    builder.row(back_button("⬅️ رجوع", "ai:keys"))
    text = ("➕ <b>أضف مفتاحًا لمزوّد</b>\n\n"
            "اختر المزوّد — سيُكتب المفتاح في <code>.env</code> باسمه "
            "(<code>&lt;name&gt;_api_key</code>) ويعمل فورًا.\n\n"
            "<i>لا يوجد مزوّدك في القائمة؟ استخدم «➕ أضف وكيلًا جديدًا» "
            "لإضافة أي خدمة تتكلّم OpenAI-compatible.</i>")
    if not missing:
        text = ("✅ <b>كل المزوّدين المعروفين لهم مفاتيح بالفعل.</b>\n\n"
                "لتغيير واحد منهم استخدم «✏️ حدّث» من شاشة المفاتيح.")
    await callback.message.edit_text(
        text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ai:key:new:"))
async def cb_key_new(callback: CallbackQuery, db_user: User,
                     state: FSMContext) -> None:
    """إضافة مفتاح لمزوّد معروف ليس له سطر في `.env` بعد.

    **لماذا مسار منفصل؟** لأن «تحديث» يعمل على سطر موجود؛ ومعظم حالات
    الإضافة هي مزوّد يعرفه البوت ولا مفتاح له، فلا يوجد ما يُحدَّث. الاسم
    يُكتب بالتسمية المعتمدة `<provider>_api_key` بلا أن يختاره المستخدم.
    """
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    provider = callback.data.split(":", 3)[3].strip().lower()
    if provider not in agents_service.RESERVED_PROVIDERS:
        await callback.answer("مزوّد غير معروف", show_alert=True)
        return
    name = env_file.provider_key_name(provider)
    if not env_file.is_allowed(name):
        await callback.answer("لا يمكن إضافة مفتاح بهذا الاسم", show_alert=True)
        return
    await state.set_state(AiSetupFlow.waiting_key_value)
    await state.update_data(key_name=name)

    label = ai.PROVIDERS[provider].label if provider in ai.PROVIDERS else provider
    key_url = ai.PROVIDERS[provider].key_url if provider in ai.PROVIDERS else ""
    lines = [
        f"➕ <b>مفتاح {escape(label)}</b>",
        "",
        f"سيُكتب في <code>{escape(name)}</code>.",
        "",
        "أرسل <b>المفتاح</b> الآن.",
    ]
    if key_url:
        lines += ["", f"🔗 تحصل على مفتاح مجاني من: {escape(key_url)}"]
    lines += ["", "🔒 رسالتك ستُحذف فورًا، ولن تظهر كاملة في أي شاشة."]
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("رجوع", "ai:keys")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:key:ren:"))
async def cb_key_rename(callback: CallbackQuery, db_user: User,
                        state: FSMContext) -> None:
    """تغيير اسم المتغيّر مع الحفاظ على قيمته."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    name = callback.data.split(":", 3)[3]
    if not env_file.is_allowed(name):
        await callback.answer("متغيّر غير معروف", show_alert=True)
        return
    await state.set_state(AiSetupFlow.waiting_key_rename)
    await state.update_data(key_name=name)
    await callback.message.edit_text(
        f"🏷️ <b>تسمية <code>{escape(name)}</code></b>\n\n"
        "أرسل <b>الاسم الجديد</b> — والقيمة تنتقل كما هي بلا فقدان.\n\n"
        "<b>الأمثلة:</b>\n"
        "<code>gemini_api_key</code> · <code>AI_API_KEY_GEMINI</code> · "
        "<code>my_key_api_key</code>\n\n"
        "📌 <i>لاحظ أن التسمية المعتمدة هي <code>&lt;الاسم&gt;_api_key</code> "
        "— والاسم بالحروف الصغيرة.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("رجوع", "ai:keys")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSetupFlow.waiting_key_rename, F.text)
async def on_key_rename(message: Message, db_user: User, db_session,
                        state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    old_name = data.get("key_name", "")
    new_name = (message.text or "").strip()
    if not old_name:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return
    if new_name.lower() in ("الغاء", "إلغاء", "cancel", "/cancel"):
        await state.clear()
        await message.answer("أُلغي — لم يتغيّر شيء.",
                             reply_markup=main_menu().as_markup())
        return

    # نقبل ما يكتبه الأدمن ونُطبّعه: مسافة أو شرطة ⇒ `_`، والحروف صغيرة
    normalized = env_file.canonical_key(
        new_name.strip().replace(" ", "_").replace("-", "_").lower())
    result = env_file.rename_key(old_name, normalized)
    await state.clear()
    await audit.record_apply(db_session, db_user, result.changed,
                            "ok" if result.ok else "failed")
    await db_session.commit()

    lines = [result.message]
    if result.ok:
        lines += [
            "",
            f"<code>{escape(old_name)}</code> → "
            f"<code>{escape(normalized)}</code>",
            "⚡ مُفعَّل فورًا — لا حاجة لإعادة تشغيل.",
        ]
        if normalized.startswith("AI_API_KEY_"):
            lines.append(
                "💡 <i>هذه الصيغة القديمة — مقروءة لكن الأفضل "
                "<code>&lt;name&gt;_api_key</code>.</i>")
    await message.answer(
        "\n".join(lines), reply_markup=keys_menu().as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("ai:key:del:"))
async def cb_key_delete_ask(callback: CallbackQuery, db_user: User) -> None:
    """تأكيد قبل الحذف — لا نحذف مفتاحًا بنقرة واحدة بالغلط."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    name = callback.data.split(":", 3)[3]
    if not env_file.is_allowed(name) or not env_file.read_value(name):
        await callback.answer("المتغيّر غير موجود", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗑️ <b>حذف <code>{escape(name)}</code>؟</b>\n\n"
        "سيُمسح السطر من <code>.env</code> فورًا (والنسخة الاحتياطية "
        f"<code>{escape(env_file.backup_path().name)}</code> تبقى للرجوع).\n\n"
        "⚠️ إذا كان هذا المفتاح هو المستخدم حاليًا فسيتوقّف الشرح الذكي "
        "حتى تضع مفتاحًا آخر — بقية البوت تعمل عادي.",
        reply_markup=InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text="✅ نعم احذفه",
                                 callback_data=f"ai:key:delok:{name}"),
             back_button("⬅️ لا، رجوع", "ai:keys")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:key:delok:"))
async def cb_key_delete(callback: CallbackQuery, db_user: User,
                        db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    name = callback.data.split(":", 3)[3]
    result = env_file.delete_key(name)
    await audit.record_apply(db_session, db_user, result.changed,
                            "ok" if result.ok else "failed")
    await db_session.commit()
    ok_now, report = await ai.healthcheck()
    await callback.message.edit_text(
        f"{result.message}\n\n" + (
            f"🧪 <b>الحالة بعد الحذف:</b>\n{escape(report)}" if ok_now
            else "⚠️ لا يوجد مزوّد جاهز الآن — أضف مفتاحًا من الشاشة نفسها."
        ),
        reply_markup=keys_menu().as_markup(), parse_mode="HTML")
    await callback.answer("🗑️ حُذف" if result.ok else "⚠️ راجع الرسالة")


@router.callback_query(F.data == "ai:keys:saved")
async def cb_keys(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    masks = await user_ai.get_masked_keys(db_session, db_user.id)
    lines = [f"{texts.header('tutor', '🔑 المفاتيح المحفوظة')}", ""]
    builder = InlineKeyboardBuilder()

    if not masks:
        lines += [
            "لا توجد مفاتيح محفوظة بعد.",
            "",
            "💡 تحفظ مفتاحًا عند اختيار مزوّد يحتاج مفتاحًا — يُخزَّن مشفّرًا.",
        ]
    else:
        active = user_ai.env_provider()
        for key, masked in masks.items():
            provider = ai.PROVIDERS.get(key)
            label = provider.label if provider else key
            live = " ✅ فعّال" if (key == active and (
                env_file.read_value(env_file.provider_key_name(key))
                or env_file.read_value("AI_API_KEY"))) else ""
            lines.append(
                f"• <b>{escape(label)}</b>: <code>{escape(masked)}</code>{live}")
            # مفتاح محفوظ لا يوجد له مقابل في .env = مفتاح «ميت» يفشل بصمت
            if not env_file.read_value(env_file.provider_key_name(key)):
                builder.row(InlineKeyboardButton(
                    text=f"📥 انقل {label[:14]} إلى .env",
                    callback_data=f"ai:promote:{key}",
                ))
            builder.row(InlineKeyboardButton(
                text=f"🗑️ احذف مفتاح {label[:16]}", callback_data=f"ai:forget:{key}"
            ))
        lines += [
            "",
            "🔒 لا يُعرض المفتاح كاملًا أبدًا — أول 4 وآخر 4 أحرف فقط.",
            "📥 المفتاح المحفوظ في القاعدة **لا يعمل** حتى يُكتب في "
            "<code>.env</code> — استخدم زر النقل.",
        ]

    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()
