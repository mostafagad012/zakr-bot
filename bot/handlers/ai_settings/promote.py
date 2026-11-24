"""ترقية المفتاح المحفوظ إلى `.env` وحذف مفتاح مزوّد واختبار الإعداد."""
from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ... import texts
from ...models import User
from ...services import ai, audit, user_ai
from ..helpers import back_button, escape
from .core import log
from .keys import cb_keys
from .core import (  # noqa: F401
    _deny, _provider_list, _running_exam, apply_to_env, can_manage_ai,
    intro_text, main_menu, priority_keyboard, provider_detail_keyboard,
    provider_detail_text, provider_keyboard, router, stored_key_notice,
    switch_warning,
)

@router.callback_query(F.data.startswith("ai:promote:"))
async def cb_promote(callback: CallbackQuery, db_user: User, db_session) -> None:
    """ينقل مفتاحًا محفوظًا في القاعدة إلى `.env` — يُصلح «المفتاح الميت».

    الخطأ القديم: كنا نحفظ المفتاح مشفّرًا ونعرضه مقنّعًا ثم **لا نستخدمه**،
    فيفشل الطلب بـ 401 بلا سبب مفهوم للأدمن. هذا الزر يجعل المفتاح فعّالًا
    بنقرة واحدة صريحة، بدل نقلٍ صامت يُخفي ما يحدث في الملف.
    """
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    key = callback.data.split(":", 2)[2]
    provider = ai.PROVIDERS.get(key)
    if provider is None:
        await callback.answer("مزوّد غير معروف", show_alert=True)
        return

    row = await user_ai.get_row(db_session, db_user.id)
    saved = user_ai.plain_keys(row).get(key, "")
    if not saved:
        await callback.answer("لا يوجد مفتاح محفوظ لهذا المزوّد", show_alert=True)
        return

    await user_ai.set_provider(db_session, db_user.id, key)
    await db_session.commit()

    result = apply_to_env(key, api_key=saved)   # يكتب `<name>_api_key`
    await audit.record_apply(db_session, db_user, result.changes, result.outcome)
    await db_session.commit()

    ok, report = await ai.healthcheck()
    text = (
        f"🔑 <b>نُقل مفتاح {escape(provider.label)} إلى <code>.env</code></b>\n"
        f"المعروض: <code>{escape(user_ai.mask_key(saved))}</code>\n"
        f"{result.message}\n\n{escape(report)}"
    )
    # رسالة جديدة لا تعديل للأزرار: الأزرار القديمة (نقل/حذف) لم تعد صحيحة
    # بعد أن صار المفتاح فعّالًا، فإبقاؤها على الشاشة يضلّل الأدمن.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:      # تجميل فقط
        log.debug("تعذّر إزالة الأزرار القديمة: %s", type(exc).__name__)
    await callback.message.answer(
        text, reply_markup=main_menu(key).as_markup(), parse_mode="HTML")
    await callback.answer("✅ تم" if ok else "⚠️ راجع الرسالة")


@router.callback_query(F.data.startswith("ai:forget:"))
async def cb_forget(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    key = callback.data.split(":", 2)[2]
    provider = ai.PROVIDERS.get(key)
    removed = await user_ai.forget_key(db_session, db_user.id, key)
    await db_session.commit()
    await callback.answer(
        f"🗑️ حُذف مفتاح {provider.label}" if removed else "لا يوجد مفتاح محفوظ",
        show_alert=True,
    )
    await cb_keys(callback, db_user, db_session)


# ------------------------------------------------------------- الاختبار
@router.callback_query(F.data == "ai:test")
async def cb_test(callback: CallbackQuery, db_user: User, db_session) -> None:
    if not can_manage_ai(db_user):
        await _deny(callback)
        return
    await callback.answer("⏳ جاري الاختبار…")
    ok, report = await ai.healthcheck()

    builder = InlineKeyboardBuilder()
    if not ok:
        builder.row(InlineKeyboardButton(text="🎯 ساعدني أختار", callback_data="ai:help"))
        builder.row(InlineKeyboardButton(text="🔑 إدارة المفاتيح", callback_data="ai:keys"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))

    await callback.message.edit_text(
        f"{texts.header('tutor', '🧪 اختبار الإعداد')}\n\n{escape(report)}",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
