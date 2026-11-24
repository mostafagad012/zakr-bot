"""الوكلاء المضافون: مزوّد مخصّص (اسم · رابط · مفتاح · موديل)."""
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

def agents_text() -> str:
    """قائمة الوكلاء المضافين — من `.env` مباشرة."""
    agents = agents_service.discover(set(ai.PROVIDERS) - set(agents_service.discover()))
    lines = [f"{texts.header('tutor', 'الوكلاء المضافون')}", ""]
    if not agents:
        lines.append("<i>لا يوجد وكيل مخصّص بعد — كل المزوّدين ثابتون في الكود.</i>")
    for name, agent in sorted(agents.items()):
        state = "✅ جاهز" if agent.is_complete else "⚠️ ناقص (لا عنوان API)"
        lines.append(f"• <b>{escape(name)}</b> — {state}")
        lines.append(f"  <code>{escape(agent.key_var)}</code>"
                     f"{' ✔' if agent.api_key else ' ✖'}")
        if agent.base_url:
            lines.append(f"  <code>{escape(agent.url_var)}</code> = "
                         f"<code>{escape(agent.base_url[:50])}</code>")
        if agent.model:
            lines.append(f"  <code>{escape(agent.model_var)}</code> = "
                         f"<code>{escape(agent.model)}</code>")
    lines += ["", "<i>كل وكيل يعمل كأنه مزوّد ثابت: يظهر في القوائم ويعمل "
                  "في الشرح والامتحانات.</i>"]
    return "\n".join(lines)[:4000]



AGENT_INTRO = (
    "➕ <b>أضف وكيلًا/موديلًا جديدًا</b>\n\n"
    "أي خدمة تتكلّم واجهة OpenAI تعمل هنا (OpenAI-compatible): خدمة سحابية، "
    "أو موديل محلي على جهازك.\n\n"
    "<b>الخطوات:</b> الاسم ← عنوان الـ API ← المفتاح (اختياري للمحلي).\n\n"
    "📝 <b>ما سيُكتب في <code>.env</code>:</b>\n"
    "<code>NAME_api_key=…</code>\n"
    "<code>NAME_base_url=…</code>\n"
    "<code>NAME_model=…</code>\n\n"
    "أرسل <b>الاسم</b> الآن — حروف إنجليزية صغيرة وأرقام و<code>_</code> "
    "(مثال: <code>aiagent</code>)."
)

@router.callback_query(F.data == "ai:agent:new")
async def cb_agent_new(callback: CallbackQuery, db_user: User,
                       state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    if not env_file.is_writable():
        await callback.answer(
            "⚠️ .env غير قابل للكتابة — أضف الوكيل يدويًا في ملف الإعدادات",
            show_alert=True,
        )
        return
    await state.set_state(AiSetupFlow.waiting_agent_name)
    await callback.message.edit_text(
        AGENT_INTRO,
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "ai:agents")
async def cb_agents(callback: CallbackQuery, db_user: User) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="➕ أضف وكيلًا جديدًا", callback_data="ai:agent:new"))
    for name in sorted(agents_service.discover(set(ai.PROVIDERS))):
        builder.row(InlineKeyboardButton(
            text=f"🗑️ احذف {name}", callback_data=f"ai:agent:del:{name}"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    await callback.message.edit_text(
        agents_text(), reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.message(AiSetupFlow.waiting_agent_name, F.text)
async def on_agent_name(message: Message, db_user: User, state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    raw = (message.text or "").strip()
    if raw.lower() in ("الغاء", "إلغاء", "cancel", "/cancel"):
        await state.clear()
        await message.answer("أُلغي.", reply_markup=main_menu(db_user).as_markup())
        return

    ok, result = agents_service.validate_name(raw, set(ai.PROVIDERS))
    if not ok:
        # الاسم يُصبح جزءًا من اسم متغيّر بيئة — لا نكتبه بلا تحقّق
        await message.answer(
            f"⚠️ {escape(result)}\n\nجرّب اسمًا آخر:",
            reply_markup=InlineKeyboardBuilder()
            .row(back_button("إلغاء", "ai:menu")).as_markup(),
            parse_mode="HTML",
        )
        return

    name = result
    if agents_service.from_env(name).base_url:
        await message.answer(
            f"⚠️ يوجد وكيل باسم <code>{escape(name)}</code> بالفعل.\n"
            "اختر اسمًا آخر أو احذفه أولًا من «🧩 الوكلاء المضافون».",
            parse_mode="HTML",
        )
        return

    await state.update_data(agent_name=name)
    await state.set_state(AiSetupFlow.waiting_agent_url)
    key_var, url_var, model_var = agents_service.env_vars_for(name)
    await message.answer(
        f"✅ الاسم مقبول: <code>{escape(name)}</code>\n\n"
        f"الآن <b>عنوان الـ API</b> (ينتهي عادةً بـ <code>/v1</code>).\n"
        f"سيُكتب في <code>{escape(url_var)}</code>.\n\n"
        "مثال: <code>https://api.example.com/v1</code>\n"
        "أو موديل محلي: <code>http://localhost:11434/v1</code>\n\n"
        f"<i>وسيُكتب مفتاحك لاحقًا في <code>{escape(key_var)}</code>.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )


@router.message(AiSetupFlow.waiting_agent_url, F.text)
async def on_agent_url(message: Message, db_user: User, state: FSMContext) -> None:
    if can_manage_ai(db_user) is False:
        await _deny(message)
        return
    url = (message.text or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        await message.answer(
            "⚠️ العنوان لازم يبدأ بـ <code>http://</code> أو <code>https://</code>.",
            parse_mode="HTML")
        return
    if len(url) < 12 or len(url) > 200:
        await message.answer("⚠️ عنوان غير معقول — تحقّق منه.", parse_mode="HTML")
        return

    data = await state.get_data()
    name = data.get("agent_name", "")
    if not name:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return

    await state.update_data(agent_url=url)
    await state.set_state(AiSetupFlow.waiting_agent_key)
    await message.answer(
        f"✅ العنوان محفوظ.\n\n"
        f"الآن <b>المفتاح</b> — سيُكتب في <code>{escape(name)}_api_key</code>.\n\n"
        "🔒 <b>خصوصيتك:</b> رسالتك تُحذف فورًا، والمفتاح لا يُسجَّل ولا يُعرض "
        "كاملًا في أي شاشة.\n\n"
        "<i>موديل محلي بلا مفتاح؟ اكتب <b>تخطّي</b>.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )


@router.message(AiSetupFlow.waiting_agent_key, F.text)
async def on_agent_key(message: Message, db_user: User, state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    name = data.get("agent_name", "")
    url = data.get("agent_url", "")
    if not name or not url:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return

    raw = (message.text or "").strip()
    # المفتاح لا يبقى في سجل الشات — الحذف قبل أي شيء آخر
    try:
        await message.delete()
    except Exception as exc:
        log.debug("تعذّر حذف رسالة المفتاح: %s", type(exc).__name__)

    api_key = "" if raw.lower() in ("تخطي", "skip", "-", "بدون") else raw
    if api_key and (len(api_key) < 8 or len(api_key) > 300
                    or any(ch.isspace() for ch in api_key)):
        await state.set_state(AiSetupFlow.waiting_agent_key)
        await message.answer(
            "⚠️ المفتاح غير معقول (طوله أو مسافاته).\n"
            "أعد إرساله، أو اكتب <b>تخطّي</b> للموديلات المحلية.",
            parse_mode="HTML")
        return

    await state.update_data(agent_key=api_key)
    await state.set_state(AiSetupFlow.waiting_agent_model)
    await message.answer(
        f"✅ استُلم المفتاح وسيُكتب في <code>{escape(name)}_api_key</code> "
        "(يُعرض مقنّعًا فقط).\n\n"
        f"الآن <b>اسم الموديل</b> — سيُكتب في <code>{escape(name)}_model</code>.\n\n"
        "أمثلة: <code>gpt-4o-mini</code> · <code>llama-3.3-70b-versatile</code> · "
        "<code>qwen2.5:7b</code>\n\n"
        "<i>لا تعرفه؟ اكتب <b>تخطّي</b> — سيُجرَّب موديل افتراضي.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )


@router.message(AiSetupFlow.waiting_agent_model, F.text)
async def on_agent_model(message: Message, db_user: User, db_session,
                         state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    name = data.get("agent_name", "")
    url = data.get("agent_url", "")
    api_key = data.get("agent_key", "")
    if not name or not url:
        await state.clear()
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return

    raw = (message.text or "").strip()
    model = "" if raw.lower() in ("تخطي", "skip", "-", "بدون") else raw
    if model and (" " in model or len(model) > 120):
        await message.answer(
            "⚠️ اسم الموديل بلا مسافات (مثال: <code>gpt-4o-mini</code>).",
            parse_mode="HTML")
        return

    await state.clear()
    agent = agents_service.Agent(name=name)
    path = env_file.env_path()
    try:
        env_file.write_value(agent.url_var, url, path)
        if api_key:
            env_file.write_value(agent.key_var, api_key, path)
        if model:
            env_file.write_value(agent.model_var, model, path)
    except (OSError, ValueError) as exc:
        await message.answer(
            f"⚠️ تعذّرت الكتابة في <code>.env</code> ({type(exc).__name__}).",
            parse_mode="HTML")
        return

    env_file.reload_settings()          # يسجّل الوكيل مزوّدًا فورًا
    await audit.record_apply(db_session, db_user, [
        (agent.url_var, "", url),
        (agent.key_var, "", api_key),
        (agent.model_var, "", model),
    ], "ok")
    await db_session.commit()

    registered = name in ai.PROVIDERS
    resolved_model = ai.provider_model(name) if registered else ""
    complete = bool(resolved_model)
    builder = InlineKeyboardBuilder()
    if complete:
        builder.row(InlineKeyboardButton(
            text="✅ اختاره الآن", callback_data=f"ai:use:{name}"))
    builder.row(InlineKeyboardButton(
        text="🧩 الوكلاء المضافون", callback_data="ai:agents"))
    builder.row(back_button("⬅️ القائمة", "ai:menu"))

    lines = [
        f"✅ <b>أُضيف الوكيل: {escape(name)}</b>",
        "",
        f"📝 <code>{escape(agent.url_var)}</code> = <code>{escape(url[:60])}</code>",
    ]
    if api_key:
        lines.append(f"🔑 <code>{escape(agent.key_var)}</code> = "
                     f"<code>{escape(user_ai.mask_key(api_key))}</code>")
    else:
        lines.append("🔑 بلا مفتاح (موديل محلي)")
    lines.append(f"🧠 <code>{escape(agent.model_var)}</code> = "
                 f"<code>{escape(resolved_model or 'افتراضي')}</code>")
    lines.append("")
    if not registered:
        lines.append("⚠️ سُجّل في الملف لكن لم يُفعَّل — تحقّق من العنوان.")
    elif not complete:
        lines.append("⚠️ بلا موديل محدد — أضف "
                     f"<code>{escape(agent.model_var)}</code> ليعمل.")
    else:
        lines.append("⚡ مُفعَّل فورًا — بلا إعادة تشغيل.")
    lines += ["", "<i>تقدر تختاره كمزوّد البوت من زر «اختاره الآن».</i>"]
    await message.answer(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("ai:agent:del:"))
async def cb_agent_delete(callback: CallbackQuery, db_user: User,
                          db_session) -> None:
    """يحذف متغيّرات الوكيل من `.env` — لا يمسّ متغيّرًا آخر."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    name = callback.data.split(":", 3)[3]
    if agents_service.validate_name(name)[0] is False:
        await callback.answer("اسم غير صالح", show_alert=True)
        return

    agent = agents_service.Agent(name=name)
    removed = 0
    for variable in (agent.key_var, agent.url_var, agent.model_var):
        if env_file.read_value(variable):
            env_file.remove_value(variable)
            removed += 1
    env_file.reload_settings()
    ai.PROVIDERS.pop(name, None)
    if name in ai.PROVIDER_ORDER:
        ai.PROVIDER_ORDER.remove(name)

    await audit.record_apply(db_session, db_user,
                             [(agent.url_var, "قيمة", "")], "ok")
    await db_session.commit()
    await callback.answer(f"🗑️ حُذف الوكيل ({removed} متغيّرًا)", show_alert=True)
    await cb_agents(callback, db_user)
