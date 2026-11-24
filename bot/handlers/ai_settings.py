"""واجهة الذكاء الاصطناعي — **للأدمن فقط**.

قرار المستخدم: «تكون الخاصية دى ظاهرة للادمن فقط، ومش ظاهرة للمستخدمين
العاديين»، و«لما أضيف ذكاء اصطناعي، يحصل تغيير في `.env`».

فالمسار صار:

    ⚙️ لوحة الإدارة → 🤖 الذكاء الاصطناعي  (أدمن + مالك)
      → «إيه أهم حاجة عندك؟» (6 اهتمامات) ← توصية مبنية عليه
      → استعرض كل المزوّدين: مميزات + عيوب + مقيّمات + التكلفة والخصوصية
      → «اختار ده» → (المفتاح إن لزم) → **يُكتب في `.env`** → يسري فورًا

ثلاثة ضمانات في هذا الملف:

1. **حجب مزدوج**: الزر لا يظهر لغير الأدمن، وكل معالج يتحقّق من الصلاحية
   مرة أخرى — فلا يكفي إرسال `callback_data` يدويًا (النص داخل زر لا يُعدّ
   حماية، لأن أي عميل يستطيع إرسال نفس القيمة).
2. **الكتابة في `.env` ذرّية** عبر `services/env_file.py`، مع نسخة
   احتياطية وصلاحية 600. ولو كان الملف غير قابل للكتابة (Railway/Vercel)
   يُطبَّق التغيير في الذاكرة فورًا ويُعلم الأدمن بذلك بصراحة.
3. **الشرح قبل الاختيار** محفوظ كما هو: كل مزوّد بمقيّماته الستة وعيوبه.
"""
from __future__ import annotations

import logging
import types
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..models import User

from ..services import agents as agents_service
from ..services import ai, ai_guide, ai_usage, audit, env_file, user_ai
from ..states import AiSetupFlow
from .helpers import back_button, escape

log = logging.getLogger(__name__)

router = Router(name="ai_settings")

LIST_PAGE = 6          # عدد المزوّدين في الصفحة الواحدة


# ---------------------------------------------------------------- الصلاحية
def can_manage_ai(user: User) -> bool:
    """من يرى هذه الشاشة: الأدمن والمالك فقط — لا الطالب ولا المشرف."""
    return bool(getattr(user, "level", 0) >= 2)


@dataclass
class EnvResult:
    """نتيجة الكتابة في `.env`: سطر للعرض + التغييرات + كيف انتهت.

    لماذا كائن لا نص فقط؟ لأن سجل التدقيق يحتاج معرفة **ماذا** تغيّر
    و**كيف** انتهت العمليّة (`ok` / `readonly` / `failed`)، والعرض يحتاج
    جملة واحدة مفهومة. الفصل بينهما هنا يمنع تمرير حالة عبر متغيّرات عامة.
    """

    message: str
    changes: list[tuple[str, str, str]]
    outcome: str = "ok"


def apply_to_env(provider: str, model: str = "", api_key: str = "") -> EnvResult:
    """يكتب اختيار الأدمن في `.env` ويُطبّقه فورًا. يُرجع سطر حالة للعرض.

    ثلاث حالات يجب أن يراها الأدمن بصدق:
    * كُتب في `.env` وطُبِّق فورًا.
    * `.env` غير قابل للكتابة (Railway/Vercel/ملف للقراءة فقط) ⇒ طُبِّق في
      الذاكرة فقط، والتغيير سيضيع عند إعادة التشغيل — نقولها صريحة.
    * فشل الكتابة لسبب آخر ⇒ طُبِّق في الذاكرة، والخطأ في السجل بلا مفتاح.
    """
    applied = "⚡ طُبِّق فورًا — بلا إعادة تشغيل."
    path = env_file.env_path()
    if not env_file.is_writable(path):
        env_file.patch_settings(**env_file.settings_for(
            ai_provider=provider, ai_model=model or None))
        return EnvResult(
            "⚠️ <b>لم أستطع الكتابة في <code>.env</code></b> "
            "(الملف غير موجود أو للقراءة فقط — شائع على Railway/Vercel).\n"
            f"طُبِّق الاختيار في الذاكرة فقط ({applied}) "
            "وسيضيع عند إعادة التشغيل — أضف القيمة يدويًا في لوحة الاستضافة.",
            [("AI_PROVIDER", "", provider)], "readonly",
        )
    try:
        before = {
            name: env_file.read_value(name, path)
            for name in ("AI_PROVIDER", "AI_MODEL", "AI_API_KEY")
        }
        env_file.apply_provider(provider, model=model, api_key=api_key, path=path)
        env_file.reload_settings()
        changes = [
            ("AI_PROVIDER", before["AI_PROVIDER"], provider),
            ("AI_MODEL", before["AI_MODEL"], model or before["AI_MODEL"]),
            ("AI_API_KEY", before["AI_API_KEY"], api_key or ""),
        ]
    except (OSError, ValueError) as exc:
        log.warning("فشل تحديث .env: %s", type(exc).__name__)
        env_file.patch_settings(**env_file.settings_for(
            ai_provider=provider, ai_model=model or None))
        return EnvResult(
            f"⚠️ تعذّر تحديث <code>.env</code> ({type(exc).__name__}) — "
            f"طُبِّق الاختيار في الذاكرة فقط. {applied}",
            [("AI_PROVIDER", "", provider)], "failed",
        )
    return EnvResult(f"📝 كُتب في <code>.env</code> · {applied}", changes, "ok")


async def _running_exam(state: FSMContext):
    """هل يوجد امتحان جارٍ لهذا الأدمن؟ يُقرأ من حالة FSM بلا أسئلة."""
    try:
        data = await state.get_data()
    except Exception as exc:      # حالة غير متاحة (خارج تدفّق)
        log.debug("تعذّرت قراءة حالة الامتحان: %s", type(exc).__name__)
        return None
    if not data.get("questions"):
        return None
    return types.SimpleNamespace(
        current=int(data.get("current") or 0),
        answers=data.get("answers") or [],
    )


def switch_warning(exam_state) -> str:
    """تحذير عند تبديل المزوّد أثناء امتحان جارٍ.

    لماذا؟ لأن الامتحان يُولَّد مسبقًا ونتائجه تُحسب على مقاس صعوبة محسوبة،
    وتبديل المزوّد في المنتصف يجعل النتائج غير قابلة للمقارنة ويُربك الطالب.
    لا نمنع التبديل — نُعلن مقدمًا فقط.
    """
    if exam_state is None:
        return ""
    answers = getattr(exam_state, "answers", None) or []
    current = int(getattr(exam_state, "current", 0) or 0)
    unanswered = sum(1 for item in answers if item is None)
    if current > 0 and unanswered:
        return (
            "\n\n⚠️ <b>فيه امتحان جارٍ</b> — التبديل الآن يغيّر أسلوب "
            "الأسئلة القادمة ويجعل المقارنة غير عادلة. "
            "الأفضل تكمّل امتحانك الأول."
        )
    return ""


async def _deny(target, text: str | None = None) -> None:
    """ردّ واحد موحّد للرافض — لا يكشف حتى وجود الشاشة."""
    message = text or "🔒 هذه الإعدادات للأدمن فقط."
    if isinstance(target, CallbackQuery):
        await target.answer(message, show_alert=True)
    else:
        await target.answer(message)


# ------------------------------------------------------------- العرض
def _provider_list(page: int = 0) -> tuple[list[str], int]:
    keys = [k for k in ai.PROVIDER_ORDER if k in ai_guide.VERDICTS]
    total_pages = max(1, (len(keys) + LIST_PAGE - 1) // LIST_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * LIST_PAGE
    return keys[start : start + LIST_PAGE], total_pages


def main_menu(model_provider: str = "") -> InlineKeyboardBuilder:
    """القائمة الرئيسية. `model_provider` يُظهر زر اختيار الموديل للمزوّد النشط."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎯 ساعدني أختار", callback_data="ai:help"))
    builder.row(InlineKeyboardButton(text="📋 استعرض كل الخيارات", callback_data="ai:list:0"))
    builder.row(InlineKeyboardButton(
        text="🧩 الوكلاء المضافون (أضف مزوّدًا)", callback_data="ai:agents"))
    builder.row(InlineKeyboardButton(text="⚖️ قارن بين الأشهر", callback_data="ai:cmp"))
    builder.row(InlineKeyboardButton(
        text="🔑 إدارة المفاتيح (تحديث · تسمية · حذف)", callback_data="ai:keys"))
    builder.row(InlineKeyboardButton(text="🧪 اختبر الإعداد الحالي", callback_data="ai:test"))
    builder.row(InlineKeyboardButton(text="📊 استهلاك الـ AI", callback_data="ai:usage"))
    builder.row(InlineKeyboardButton(text="📄 إعدادات .env الحالية", callback_data="ai:env"))
    builder.row(InlineKeyboardButton(text="🗂️ سجل التغييرات", callback_data="ai:audit"))
    if model_provider and model_provider in ai.PROVIDERS:
        builder.row(InlineKeyboardButton(
            text="⚙️ اختر موديلًا محدّدًا",
            callback_data=f"ai:model:{model_provider}",
        ))
    builder.row(back_button("⬅️ القائمة", "menu:main"))
    return builder


def priority_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key, label, _description in ai_guide.PRIORITIES:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"ai:pri:{key}"))
    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    return builder


def provider_keyboard(keys: list[str], page: int, total_pages: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key in keys:
        verdict = ai_guide.VERDICTS.get(key)
        if verdict is None:
            continue
        builder.row(InlineKeyboardButton(
            text=f"{verdict.tagline[:46]}",
            callback_data=f"ai:p:{key}",
        ))
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"ai:list:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
        if page + 1 < total_pages:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"ai:list:{page + 1}"))
        builder.row(*row)
    builder.row(back_button("⬅️ رجوع", "ai:menu"))
    return builder


def provider_detail_keyboard(key: str, has_key: bool) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="✅ اختار ده", callback_data=f"ai:use:{key}"
    ))
    if has_key:
        builder.row(InlineKeyboardButton(
            text="🗑️ احذف مفتاحي لهذا المزوّد", callback_data=f"ai:forget:{key}"
        ))
    builder.row(back_button("⬅️ كل الخيارات", "ai:list:0"))
    return builder


def intro_text(active: str, configured: bool, env_writable: bool = True,
               env_name: str = ".env") -> str:
    """شاشة الأدمن: الحالة الفعلية + مكان الحفظ + كيف يُطبَّق التغيير."""
    state_line = (
        "✅ الذكاء الاصطناعي مضبوط وشغّال لكل المستخدمين"
        if configured
        else "⚠️ لا يوجد مفتاح فعّال — الميزات الذكية معطّلة الآن"
    )
    if env_writable:
        env_line = f"📝 أي اختيار هنا <b>يُكتب في <code>{escape(env_name)}</code></b> ويُطبَّق فورًا."
    else:
        env_line = (
            f"⚠️ <code>{escape(env_name)}</code> غير قابل للكتابة "
            "(Railway/Vercel أو ملف للقراءة فقط) — سيُطبَّق في الذاكرة فقط."
        )
    return (
        f"{texts.header('tutor', '🤖 إعداد الذكاء الاصطناعي')}\n\n"
        "هذه الشاشة <b>للأدمن فقط</b> — الطلاب لا يرونها. "
        "اختيارك هنا يسري على <b>كل مستخدمي البوت</b> "
        "(الشرح، التلخيص، الامتحانات).\n\n"
        f"🎛️ الحالي: <b>{escape(active)}</b>\n"
        f"{state_line}\n"
        f"{env_line}\n\n"
        "💡 <b>مش عارف تختار؟</b> اضغط «🎯 ساعدني أختار» — هسألك سؤالًا "
        "واحدًا وأرشّحلك المناسب بمميزاته وعيوبه قبل ما تقرّر."
    )


def stored_key_notice(key: str, saved_mask: str) -> str:
    """لافتة صريحة: المفتاح محفوظ لكنه غير مكتوب في `.env` ⇒ غير فعّال."""
    if not saved_mask:
        return ""
    if env_file.read_value(env_file.provider_key_name(key)) or env_file.read_value("AI_API_KEY"):
        return ""
    return (
        "\n⚠️ <b>مفتاحك المحفوظ غير فعّال</b> — لم يُكتب في <code>.env</code> "
        "بعد. اضغط «📥 انقله» من شاشة المفاتيح."
    )


def provider_detail_text(key: str, saved_mask: str = "") -> str:
    """بطاقة المزوّد الكاملة: مميزاته، عيوبه، مقيّماته، وتكلفته."""
    provider = ai.PROVIDERS.get(key)
    verdict = ai_guide.VERDICTS.get(key)
    if provider is None or verdict is None:
        return "⚠️ مزوّد غير معروف"

    lines = [
        f"<b>{escape(provider.label)}</b>",
        f"<i>{escape(verdict.tagline)}</i>",
        "",
    ]
    if verdict.best_for:
        lines.append("🎯 <b>مناسب لـ:</b> " + " · ".join(verdict.best_for))
        lines.append("")

    lines.append("✅ <b>المميزات</b>")
    lines += [f"  • {escape(pro)}" for pro in verdict.pros]
    lines += ["", "⚠️ <b>العيوب</b>"]
    lines += [f"  • {escape(con)}" for con in verdict.cons]

    lines += [
        "",
        "📊 <b>التقييم من 5</b>",
        f"  💰 المجاني: {ai_guide.score_bar(verdict.free)} {verdict.free}",
        f"  🎯 الجودة: {ai_guide.score_bar(verdict.quality)} {verdict.quality}",
        f"  🇪🇬 العربية: {ai_guide.score_bar(verdict.arabic)} {verdict.arabic}",
        f"  ⚡ السرعة: {ai_guide.score_bar(verdict.speed)} {verdict.speed}",
        f"  🛠️ سهولة الإعداد: {ai_guide.score_bar(verdict.ease)} {verdict.ease}",
        f"  🔒 الخصوصية: {ai_guide.score_bar(verdict.privacy)} {verdict.privacy}",
    ]

    lines += [
        "",
        f"💵 <b>التكلفة:</b> {escape(verdict.cost_hint or 'غير محدّدة')}",
        f"💳 <b>بطاقة بنكية:</b> {'نعم مطلوبة' if verdict.needs_card else 'لا، يكفي تسجيل مجاني'}",
    ]
    if verdict.offline_capable:
        lines.append("📴 <b>يعمل بلا إنترنت:</b> نعم")
    if verdict.provider not in ("custom",):
        lines.append(f"🧠 <b>الموديل الافتراضي:</b> {escape(provider.default_model or '—')}")

    tradeoff = ai_guide.tradeoff_summary(key)
    if tradeoff:
        lines += ["", f"⚖️ <b>الخلاصة:</b> {escape(tradeoff)}"]

    if saved_mask:
        lines += ["", f"🔑 مفتاحك المحفوظ: <code>{escape(saved_mask)}</code>"
                  + stored_key_notice(key, saved_mask)]

    if provider.key_url:
        lines += ["", f"🔗 المفتاح من: {escape(provider.key_url)}"]

    return "\n".join(lines)[:4000]


# ------------------------------------------------------------- الشاشات
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

    ok, report = await ai.healthcheck()

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


# ------------------------------------------------------------- المفاتيح
# ===========================================================================
# إدارة مفاتيح الـ API: عرض · تحديث · تسمية · حذف · إضافة
# ===========================================================================
# **المطلوب:** «خليني برضه أقدر أحدّث المفاتيح بتاعة الـ API للنماذج لو خلصت،
# أو أعدّل عليهم — تسمية أو تحديث أو حذف أو إضافة».
#
# قرار التصميم: شاشة واحدة تسرد **كل** متغيّر مفتاح موجود في `.env` (لا
# المزوّدين المعروفين فقط)، ولكل واحد أربعة أزرار. والسبب أن مهمة المالك هنا
# هي إدارة ملفه: مفتاح لمزوّد غير مسجّل — أو كتبه بيده — يجب أن يظهر ويُدار،
# لا أن يختفي لأنه غير موجود في الكود.

KEY_STATE_MARKS = {
    "set": "✅ مضبوط",
    "empty": "⚠️ فارغ",
    "duplicate": "🔀 مكرّر بصيغتين",
}


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


# ---------------------------------------------------------------------------
# الشاشة القديمة (مفاتيح القاعدة) — لم تُحذف لأن المفاتيح المشفّرة القديمة
# ما زالت قابلة للنقل إلى `.env`؛ لكن صار لها مدخل منفصل حتى لا تختلط
# بإدارة ملف `.env` نفسها.
# ---------------------------------------------------------------------------


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


# ------------------------------------------------------ إضافة وكيل جديد
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


# --------------------------------------------------- لوحة الاستهلاك
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
    from ..config import settings as live
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

    from ..config import settings as live

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
    from ..config import settings as live
    return (live.ai_provider or "auto").strip() or "auto"


def current_model() -> str:
    from ..config import settings as live
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
