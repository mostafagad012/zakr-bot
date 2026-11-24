"""إعدادات `.env` · حدود الاستهلاك · سجل التغييرات · الملاحظات · `/ai`."""
from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ... import texts
from ...models import User
from ...services import ai, ai_usage, audit, env_file, user_ai
from ...states import AiSetupFlow
from ..helpers import back_button, escape
from .core import (  # noqa: F401
    _deny, _provider_list, _running_exam, apply_to_env, can_manage_ai,
    intro_text, main_menu, priority_keyboard, provider_detail_keyboard,
    provider_detail_text, provider_keyboard, router, stored_key_notice,
    switch_warning,
)

def usage_text(stats: dict, limit: int) -> str:
    """تقرير استهلاك الذكاء الاصطناعي — أرقام لا انطباعات."""
    lines = [f"{texts.header('tutor', '📊 استهلاك الذكاء الاصطناعي')}", ""]
    lines.append(f"⏱️ آخر <b>{stats['hours']}</b> ساعة")
    lines.append(f"• الطلبات: <b>{stats['total']}</b>")
    lines.append(f"• ناجحة: {stats['ok']} · فاشلة: {stats['failed']} "
                 f"(نسبة النجاح {stats['success_rate']}%)")
    lines.append(f"• مستخدمون نشطون: {stats['active_users']}")

    if stats["by_kind"]:
        lines += ["", "🧩 <b>حسب النوع</b>"]
        for kind, count in stats["by_kind"]:
            label = ai_usage.KIND_LABELS.get(kind, kind)
            lines.append(f"  • {escape(label)}: {count}")

    if stats["by_provider"]:
        lines += ["", "🔌 <b>حسب المزوّد</b>"]
        for name, count in stats["by_provider"]:
            label = ai.PROVIDERS[name].label if name in ai.PROVIDERS else name
            lines.append(f"  • {escape(label)}: {count}")

    if stats["top_users"]:
        lines += ["", "👥 <b>أكثر استخدامًا</b>"]
        lines += [f"  • {escape(name)}: {count}" for name, count in stats["top_users"]]

    lines += ["", f"🎚️ الحدّ اليومي للطالب: <b>{limit}</b> طلبًا"]

    if stats["failed"]:
        worst = stats["worst_provider"]
        worst_label = ai.PROVIDERS[worst].label if worst in ai.PROVIDERS else worst
        lines.append(
            f"\n⚠️ أعلى فشل: <b>{escape(worst_label or 'غير معروف')}</b> — "
            "جرّب «🧪 اختبر الإعداد»."
        )
    if not stats["total"]:
        lines.append("\n<i>لا استخدام بعد — الأرقام تظهر بعد أول شرح أو امتحان.</i>")
    return "\n".join(lines)[:4000]


@router.callback_query(F.data == "ai:usage")
async def cb_usage(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    from ...config import settings as live
    from ..services import settings_service

    limit = await settings_service.get_int(
        db_session, "ai_daily_limit", ai_usage.DEFAULT_DAILY_LIMIT)
    stats = await ai_usage.stats(db_session, hours=24)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🕓 ٧ أيام", callback_data="ai:usage:168"))
    builder.row(InlineKeyboardButton(text="🧹 تصفير العدّادات", callback_data="ai:usage:reset"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))

    _ = live
    await callback.message.edit_text(
        usage_text(stats, limit),
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "ai:usage:168")
async def cb_usage_week(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    from ..services import settings_service

    limit = await settings_service.get_int(
        db_session, "ai_daily_limit", ai_usage.DEFAULT_DAILY_LIMIT)
    stats = await ai_usage.stats(db_session, hours=168)
    await callback.message.edit_text(
        usage_text(stats, limit),
        reply_markup=InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text="🕐 ٢٤ ساعة", callback_data="ai:usage"))
        .row(back_button("⬅️ رجوع", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "ai:usage:reset")
async def cb_usage_reset(callback: CallbackQuery, db_user: User, db_session) -> None:
    """تصفير العدّادات — لخفض السقف بعد اختبار، أو بعد تجاوز جماعي."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    removed = await ai_usage.prune(db_session, keep_days=0)
    await db_session.commit()
    await callback.answer(f"🧹 حُذف {removed} سطرًا من سجل الاستهلاك", show_alert=True)


# ------------------------------------------------------- سجل التغييرات
def audit_text(entries: list, writable: bool) -> str:
    lines = [f"{texts.header('tutor', '🗂️ سجل تغييرات الإعداد')}", ""]
    if not entries:
        lines.append("<i>لا تغييرات بعد.</i>")
    else:
        for entry in entries:
            lines.append("• " + escape(audit.describe(entry)))
    lines += ["", "<i>المفاتيح تُسجَّل كعلامة فقط — لا قيمتها.</i>"]
    if not writable:
        lines.append("⚠️ <code>.env</code> غير قابل للكتابة — التغييرات في الذاكرة فقط.")
    return "\n".join(lines)[:4000]


@router.callback_query(F.data == "ai:audit")
async def cb_audit(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    entries = await audit.recent(db_session, limit=12)
    builder = InlineKeyboardBuilder()
    backups = env_file.list_backups()
    if backups:
        builder.row(InlineKeyboardButton(
            text="↩️ استعادة آخر نسخة من .env", callback_data="ai:restore"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    await callback.message.edit_text(
        audit_text(entries, env_file.is_writable()),
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "ai:restore")
async def cb_restore(callback: CallbackQuery, db_user: User, db_session) -> None:
    """استعادة `.env` من آخر نسخة — شبكة أمان عند قيمة تُبطل الإقلاع."""
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    try:
        name = env_file.restore_backup()
    except (FileNotFoundError, OSError) as exc:
        await callback.answer(f"⚠️ تعذّرت الاستعادة: {type(exc).__name__}",
                              show_alert=True)
        return

    from ...config import settings as live

    await audit.log_change(
        db_session, db_user, "AI_PARAM", "", "",
        "ok",
    )
    await db_session.commit()
    _ = live
    await callback.answer(f"↩️ استُعيدت {name}", show_alert=True)
    await cb_audit(callback, db_user, db_session)


# ------------------------------------------------------------ ملف .env
def env_state_text() -> str:
    """عرض ما يخصّ الذكاء الاصطناعي في `.env` — بلا كشف أي مفتاح."""
    path = env_file.env_path()
    lines = [f"{texts.header('tutor', 'إعدادات .env')}", ""]
    if not path.exists():
        lines.append(f"⚠️ لا يوجد ملف <code>{escape(path.name)}</code> في "
                     "مجلد التشغيل — البوت يعمل من متغيّرات البيئة مباشرة.")
    else:
        lines.append(f"📁 الملف: <code>{escape(str(path))}</code>")
        lines.append(
            "✍️ قابل للكتابة: <b>" + ("نعم" if env_file.is_writable(path) else "لا") + "</b>"
        )
        if path.with_suffix(path.suffix + ".bak").exists():
            lines.append("💾 نسخة احتياطية: <code>"
                         f"{escape(path.with_suffix(path.suffix + '.bak').name)}</code>")

    lines += ["", "🤖 <b>المتغيّرات الفعّالة الآن</b>"]
    current = {
        "AI_PROVIDER": settings_provider(),
        "AI_MODEL": current_model(),
        "AI_BASE_URL": ai.settings.ai_base_url or "—",
        "AI_API_KEY": "مضبوط ✔" if ai.settings.ai_api_key else "فارغ ✖",
    }
    for name, value in current.items():
        lines.append(f"  <code>{name}</code> = <code>{escape(str(value) or '—')}</code>")

    lines += ["", "🔑 <b>مفاتيح مزوّدين في الملف</b>"]
    keys_in_file = env_file.list_provider_keys(path)
    lines.append("  • " + ("\n  • ".join(escape(k) for k in keys_in_file)
                           if keys_in_file else "<i>لا يوجد</i>"))
    lines += ["", "<i>المفاتيح لا تُعرض أبدًا في الشاشة — مجرد وجودها.</i>"]
    return "\n".join(lines)


def settings_provider() -> str:
    from ...config import settings as live
    return (live.ai_provider or "auto").strip() or "auto"


def current_model() -> str:
    from ...config import settings as live
    return (live.ai_model or "").strip() or "افتراضي المزوّد"


@router.callback_query(F.data == "ai:env")
async def cb_env(callback: CallbackQuery, db_user: User) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="✏️ عدّل AI_MODEL", callback_data="ai:env:set:AI_MODEL"))
    builder.row(InlineKeyboardButton(
        text="✏️ عدّل AI_PROVIDER", callback_data="ai:env:set:AI_PROVIDER"))
    builder.row(InlineKeyboardButton(
        text="✏️ عدّل AI_BASE_URL", callback_data="ai:env:set:AI_BASE_URL"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    await callback.message.edit_text(
        env_state_text()[:4000], reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:env:set:"))
async def cb_env_set(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    name = callback.data.split(":", 3)[3]
    if name not in env_file.EDITABLE_KEYS:
        await callback.answer("متغيّر غير مسموح", show_alert=True)
        return
    await state.set_state(AiSetupFlow.waiting_env_value)
    await state.update_data(env_key=name)
    await callback.message.edit_text(
        f"✏️ <b>{escape(name)}</b>\n\n"
        "اكتب القيمة الجديدة في رسالة واحدة (أو «حذف» لتفريغها).\n"
        "<i>ستُكتب في <code>.env</code> فورًا وتُطبَّق بلا إعادة تشغيل.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "ai:env")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSetupFlow.waiting_env_value, F.text)
async def on_env_value(message: Message, db_user: User, db_session,
                       state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    data = await state.get_data()
    name = data.get("env_key", "")
    value = (message.text or "").strip()
    await state.clear()

    if name not in env_file.EDITABLE_KEYS:
        await message.answer("⚠️ جلسة منتهية — ابدأ من جديد.")
        return

    old_value = env_file.read_value(name)
    try:
        if value.lower() in ("حذف", "delete", "-"):
            env_file.remove_value(name)
            env_file.reload_settings()
            await audit.record_apply(db_session, db_user,
                                     [(name, old_value, "")], "ok")
            await db_session.commit()
            await message.answer(
                f"🗑️ حُذف <code>{escape(name)}</code> من <code>.env</code>.",
                reply_markup=main_menu(db_user).as_markup(), parse_mode="HTML")
            return
        env_file.write_value(name, value)
        env_file.reload_settings()
        await audit.record_apply(db_session, db_user,
                                 [(name, old_value, value)], "ok")
        await db_session.commit()
    except (OSError, ValueError) as exc:
        await audit.record_apply(db_session, db_user,
                                 [(name, old_value, value)], "failed")
        await db_session.commit()
        await message.answer(
            f"⚠️ تعذّر التعديل ({type(exc).__name__}) — تحقّق من صلاحية الملف.",
            parse_mode="HTML")
        return

    await message.answer(
        f"✅ <code>{escape(name)}</code> صار <code>{escape(value[:60])}</code> "
        "في <code>.env</code> — وطُبِّق فورًا.",
        reply_markup=main_menu(db_user).as_markup(), parse_mode="HTML",
    )


# ------------------------------------------------------------- ملاحظاتي
@router.message(Command("ai"))
async def cmd_ai(message: Message, db_user: User, db_session) -> None:
    """اختصار للأدمن: `/ai` يفتح الشاشة مباشرة."""
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    row = await user_ai.get_row(db_session, db_user.id)
    await message.answer(
        intro_text(
            user_ai.active_label(row), ai.is_configured(),
            env_writable=env_file.is_writable(),
            env_name=env_file.env_path().name,
        ),
        reply_markup=main_menu(db_user).as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data == "ai:notes")
async def cb_notes(callback: CallbackQuery, db_user: User, db_session,
                   state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    row = await user_ai.get_or_create(db_session, db_user.id)
    await state.set_state(AiSetupFlow.waiting_notes)
    await callback.message.edit_text(
        f"{texts.header('tutor', '📝 ملاحظاتي للتخصيص')}\n\n"
        f"الحالي: {escape(row.notes) if row.notes else '<i>فارغ</i>'}\n\n"
        "اكتب ما تريد أن يراعيه الشرح (مثال: «أنا ضعيف في الرياضيات، "
        "اشرح بالخطوات وببطء»).\n"
        "<i>يُضاف لكل شرح وتلخيص وامتحان.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("إلغاء", "ai:menu")).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSetupFlow.waiting_notes, F.text)
async def on_notes(message: Message, db_user: User, db_session,
                   state: FSMContext) -> None:
    if not can_manage_ai(db_user):
        await _deny(message)
        return
    await state.clear()
    await user_ai.set_notes(db_session, db_user.id, message.text or "")
    await db_session.commit()
    await message.answer(
        "✅ حُفظت ملاحظاتك — ستُراعى في الشرح والتلخيص والامتحانات.",
        reply_markup=main_menu(db_user).as_markup(), parse_mode="HTML",
    )
