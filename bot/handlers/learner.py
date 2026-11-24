"""تحليل المتعلّم: الاستبيان بأزرار، شاشة «تحليلي»، وتعديل الأسلوب.

كل الاختيارات تُتحقّق في طبقة الخدمة (`learner.save_survey`) قبل الحفظ،
لأن `callback_data` مدخل من المستخدم يمكن تزويره.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User
from ..services import learner
from .helpers import back_button, profile_card_text

router = Router(name="learner")

# مفاتيح الخطوات — ثابتة حتى لا تتغيّر الروابط بين النسخ
STEP_STYLE = "style"
STEP_LENGTH = "length"
STEP_LANG = "lang"
STEP_EXTRAS = "extras"

TOTAL_STEPS = 4


def _progress(step: int) -> str:
    """مؤشّر تقدّم بسيط: ●●●○"""
    return "●" * step + "○" * (TOTAL_STEPS - step)


# ----------------------------------------------------------- الأزرار
def style_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key, label, desc, _instruction in learner.EXPLAIN_STYLES:
        builder.row(InlineKeyboardButton(
            text=label[:28], callback_data=f"lrn:style:{key}"
        ))
        builder.row(InlineKeyboardButton(
            text=f"   ↳ {desc[:52]}", callback_data="noop:page"
        ))
    builder.row(back_button("⬅️ حسابي", "profile:me"))
    return builder


def length_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key, label, desc in learner.LENGTHS:
        builder.row(InlineKeyboardButton(
            text=f"{label} — {desc}", callback_data=f"lrn:len:{key}"
        ))
    builder.row(back_button("⬅️ رجوع", "lrn:style"))
    return builder


def language_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key, label in learner.LANGUAGES:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"lrn:lang:{key}"))
    builder.row(back_button("⬅️ رجوع", "lrn:len"))
    return builder


def extras_keyboard(profile) -> InlineKeyboardBuilder:
    """خصائص نعم/لا — تُعرض حالتها الحالية مباشرة."""
    builder = InlineKeyboardBuilder()

    def mark(active: bool) -> str:
        return "✅" if active else "⬜"

    builder.row(InlineKeyboardButton(
        text=f"{mark(profile.uses_examples)} أمثلة عملية",
        callback_data="lrn:tog:examples",
    ))
    builder.row(InlineKeyboardButton(
        text=f"{mark(profile.uses_analogies)} تشبيهات من الحياة",
        callback_data="lrn:tog:analogies",
    ))
    builder.row(InlineKeyboardButton(
        text=f"{mark(profile.prefers_bullets)} نقاط مرقّمة (لا كتلة نص)",
        callback_data="lrn:tog:bullets",
    ))
    builder.row(InlineKeyboardButton(
        text="✅ تم — احفظ تخصيصي", callback_data="lrn:done"
    ))
    builder.row(back_button("⬅️ رجوع", "lrn:lang"))
    return builder


# ----------------------------------------------------------- النصوص
def style_intro() -> str:
    return (
        f"{texts.header('profile', '🎨 أسلوب الشرح')}\n"
        f"{_progress(1)} <b>الخطوة 1 من {TOTAL_STEPS}</b>\n\n"
        "إزاي تحب أشرحلك؟ اختيارك بيغيّر شكل الشرح والتلخيص والامتحانات.\n\n"
        "<i>مش متأكد؟ اختار اللي بيوصفك أكتر — وتقدر تغيّره في أي وقت.</i>"
    )


def length_intro(style: str) -> str:
    return (
        f"{texts.header('profile', '📏 الطول المفضّل')}\n"
        f"{_progress(2)} <b>الخطوة 2 من {TOTAL_STEPS}</b>\n\n"
        f"اخترت: {learner.style_label(style)}\n\n"
        "وعايز الشرح يكون قد إيه؟"
    )


def lang_intro() -> str:
    return (
        f"{texts.header('profile', '🗣️ لغة الشرح')}\n"
        f"{_progress(3)} <b>الخطوة 3 من {TOTAL_STEPS}</b>\n\n"
        "أشرحلك بالعربي ولا بالإنجليزي؟"
    )


def extras_intro() -> str:
    return (
        f"{texts.header('profile', '⚙️ تفاصيل إضافية')}\n"
        f"{_progress(4)} <b>الخطوة 4 من {TOTAL_STEPS}</b>\n\n"
        "اضغط لتفعيل/إلغاء أي بند، ولما تخلص اضغط «تم»:"
    )


# ----------------------------------------------------------- المعالجات
@router.callback_query(F.data == "lrn:start")
async def cb_start(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يبدأ الاستبيان من أوله."""
    await callback.message.edit_text(
        style_intro(), reply_markup=style_keyboard().as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "lrn:style")
async def cb_style_step(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        style_intro(), reply_markup=style_keyboard().as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lrn:style:"))
async def cb_pick_style(callback: CallbackQuery, db_user: User, db_session) -> None:
    style = callback.data.split(":", 2)[2]
    try:
        await learner.save_survey(db_session, db_user.id, explain_style=style)
        await db_session.commit()
    except ValueError:
        await callback.answer("اختيار غير معروف", show_alert=True)
        return
    await callback.message.edit_text(
        length_intro(style), reply_markup=length_keyboard().as_markup(), parse_mode="HTML"
    )
    await callback.answer(f"✅ {learner.style_label(style)}")


@router.callback_query(F.data == "lrn:len")
async def cb_length_step(callback: CallbackQuery, db_user: User, db_session) -> None:
    profile = await learner.get_or_create(db_session, db_user.id)
    await callback.message.edit_text(
        length_intro(profile.explain_style),
        reply_markup=length_keyboard().as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lrn:len:"))
async def cb_pick_length(callback: CallbackQuery, db_user: User, db_session) -> None:
    length = callback.data.split(":", 2)[2]
    try:
        await learner.save_survey(db_session, db_user.id, length_pref=length)
        await db_session.commit()
    except ValueError:
        await callback.answer("اختيار غير معروف", show_alert=True)
        return
    await callback.message.edit_text(
        lang_intro(), reply_markup=language_keyboard().as_markup(), parse_mode="HTML"
    )
    await callback.answer(f"✅ {length}")


@router.callback_query(F.data == "lrn:lang")
async def cb_lang_step(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        lang_intro(), reply_markup=language_keyboard().as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lrn:lang:"))
async def cb_pick_lang(callback: CallbackQuery, db_user: User, db_session) -> None:
    lang = callback.data.split(":", 2)[2]
    try:
        await learner.save_survey(db_session, db_user.id, language=lang)
        await db_session.commit()
    except ValueError:
        await callback.answer("اختيار غير معروف", show_alert=True)
        return
    profile = await learner.get_or_create(db_session, db_user.id)
    await callback.message.edit_text(
        extras_intro(), reply_markup=extras_keyboard(profile).as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lrn:tog:"))
async def cb_toggle(callback: CallbackQuery, db_user: User, db_session) -> None:
    """يقلب خاصية نعم/لا ثم يعيد رسم نفس الشاشة بحالتها الجديدة."""
    what = callback.data.split(":", 2)[2]
    profile = await learner.get_or_create(db_session, db_user.id)

    field_by_key = {
        "examples": "uses_examples",
        "analogies": "uses_analogies",
        "bullets": "prefers_bullets",
    }
    field = field_by_key.get(what)
    if field is None:
        await callback.answer("خيار غير معروف", show_alert=True)
        return

    new_value = not bool(getattr(profile, field))
    await learner.save_survey(db_session, db_user.id, **{field: new_value})
    await db_session.commit()

    profile = await learner.get_or_create(db_session, db_user.id)
    await callback.message.edit_text(
        extras_intro(), reply_markup=extras_keyboard(profile).as_markup(), parse_mode="HTML"
    )
    await callback.answer("✅ مفعّل" if new_value else "أُلغي")


@router.callback_query(F.data == "lrn:done")
async def cb_done(callback: CallbackQuery, db_user: User, db_session) -> None:
    profile = await learner.get_or_create(db_session, db_user.id)
    await db_session.commit()
    await callback.message.edit_text(
        "✅ <b>تم تخصيص تجربتك</b>\n\n" + learner.summary_text(profile)
        + "\n\n<i>كل ما تشرح أو تلخّص أو تمتحن هيبقى على مقاسك، والمستوى "
          "هيترقّى تلقائيًا كل ما نتايجك تتحسن.</i>",
        reply_markup=InlineKeyboardBuilder()
        .row(back_button("👤 حسابي", "profile:me"))
        .as_markup(),
        parse_mode="HTML",
    )
    await callback.answer("✅ تم")


# ----------------------------------------------------------- شاشة تحليلي
async def analytics_text(db_session, user_id: int) -> str:
    profile = await learner.get_profile(db_session, user_id)
    if profile is None:
        return (
            f"{texts.header('profile', '🎯 تحليلي')}\n\n"
            "لسه مفيش بيانات عنك. جرّب امتحان أو خصّص أسلوب الشرح، "
            "وبعدها هيظهر هنا تحليل مستواك والمواضيع اللي محتاجة مراجعة."
        )
    attempts = await learner.recent_attempts(db_session, user_id, limit=5)
    lines = [learner.summary_text(profile)]
    if attempts:
        lines.append("\n🧾 <b>آخر الامتحانات</b>")
        for attempt in attempts:
            percent = round(attempt.score * 100 / attempt.total) if attempt.total else 0
            mark = "✅" if percent >= 60 else "📝"
            lines.append(
                f"{mark} {attempt.score}/{attempt.total} — {percent}%"
            )
    return "\n".join(lines)


@router.callback_query(F.data == "lrn:analytics")
async def cb_analytics(callback: CallbackQuery, db_user: User, db_session) -> None:
    profile = await learner.get_profile(db_session, db_user.id)
    builder = InlineKeyboardBuilder()
    if profile is None or not profile.survey_done:
        builder.row(InlineKeyboardButton(
            text="🎨 خصّص أسلوبي الآن", callback_data="lrn:start"
        ))
    else:
        builder.row(InlineKeyboardButton(
            text="🎨 تعديل أسلوب الشرح", callback_data="lrn:start"
        ))
    builder.row(back_button("⬅️ حسابي", "profile:me"))
    await callback.message.edit_text(
        await analytics_text(db_session, db_user.id),
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


async def cmd_me(message: Message, db_user: User, db_session) -> None:
    """أمر سريع لتحليلي — يعمل بلا أزرار."""
    await message.answer(await analytics_text(db_session, db_user.id), parse_mode="HTML")


@router.callback_query(F.data == "profile:me")
async def cb_profile(callback: CallbackQuery, db_user: User, db_session, state: FSMContext) -> None:
    """حسابي: بطاقة الحساب + ملخص التحليل + مدخل التخصيص."""
    await state.clear()
    profile = await learner.get_profile(db_session, db_user.id)
    body = profile_card_text(db_user)
    if profile is not None and profile.survey_done:
        body += (
            f"\n\n🧠 أسلوبك: {learner.style_label(profile.explain_style)}"
            f"\n🪜 مستواك: {profile.level}/{learner.MAX_LEVEL}"
        )
    else:
        body += "\n\n💡 <i>خصّص أسلوب الشرح لتحصل على شرح على مقاسك</i>"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎯 تحليلي", callback_data="lrn:analytics"))
    builder.row(InlineKeyboardButton(
        text="🎨 أسلوب الشرح" if profile is None or not profile.survey_done
        else "🎨 تعديل أسلوب الشرح",
        callback_data="lrn:start",
    ))
    builder.row(back_button(callback_data="menu:main"))
    await callback.message.edit_text(
        body, reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()
