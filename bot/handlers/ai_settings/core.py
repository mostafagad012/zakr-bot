"""النواة المشتركة لواجهة الذكاء الاصطناعي: الاستيرادات + المساعدات.

**لماذا نواة؟** الوحدات الخمس كلها تحتاج نفس الشيء: الصلاحية، `apply_to_env`،
لوحات المفاتيح، ونصوص الشرح. نسخها في كل وحدة يعني انحرافًا بعد أول تعديل —
نفس العيب الذي أُصلح في `setup_common.py`. هنا **مصدر واحد للحقيقة**.
"""
from __future__ import annotations

import logging
import types
from dataclasses import dataclass

import types as _types  # noqa: F401 — يُستخدم في _running_exam

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ... import texts
from ...models import User
from ...services import ai, ai_guide, env_file
from ..helpers import back_button, escape

log = logging.getLogger(__name__)

# الموجّه الوحيد لكل شاشات الذكاء الاصطناعي — الوحدات تُسجّل عليه
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
