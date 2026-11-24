"""البداية والقائمة الرئيسية والمساعدة."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from .. import texts
from ..models import BotSetting, User
from .helpers import escape

router = Router(name="start")


async def get_setting(db_session, key: str, default: str = "") -> str:
    value = await db_session.scalar(select(BotSetting.value).where(BotSetting.key == key))
    return value if value is not None else default


def main_menu(user: User | None = None) -> InlineKeyboardMarkup:
    """القائمة الرئيسية — أزرار الإدارة تظهر لأصحاب الصلاحية فقط.

    `user=None` يعني «طالب» (أضيق صلاحية): تُستدعى القائمة من مسارات لا
    تعرف المستخدم، فلا يجوز أن تكشف زر إدارة بالخطأ.
    """
    level = getattr(user, "level", 0) if user is not None else 0
    is_admin = level >= 2          # أدمن / مالك
    is_staff = level >= 1          # مشرف فأعلى

    rows = [
        [
            InlineKeyboardButton(text="📚 المكتبة", callback_data="nav:dept:0"),
            InlineKeyboardButton(text="🔍 بحث", callback_data="search:ask"),
        ],
        [
            InlineKeyboardButton(text="📤 رفع ملف", callback_data="upload:start"),
            InlineKeyboardButton(text="🆕 أحدث الملفات", callback_data="files:recent:0"),
        ],
        [
            # إدارة المواد: كل ما رفعه المستخدم — يعدّله أو يحذفه
            InlineKeyboardButton(text="🗂️ موادي (تعديل/حذف)", callback_data="own:list"),
        ],
        [
            InlineKeyboardButton(text="🧠 كويزات", callback_data="quiz:list:0"),
            InlineKeyboardButton(text="📝 امتحان", callback_data="exm:menu"),
        ],
        [
            InlineKeyboardButton(text="🧑‍🏫 اشرح لي", callback_data="tut:explain"),
            InlineKeyboardButton(text="📝 تلخيص ذكي", callback_data="tut:summarize"),
        ],
        [
            # الشرح من مادة: تحليل PDF + اختيارات + برومبت حر
            InlineKeyboardButton(text="📄 اشرح PDF/مادتي", callback_data="exp:start"),
        ],
        [
            InlineKeyboardButton(text="🌐 ترجمة", callback_data="translate:ask"),
            InlineKeyboardButton(text="📝 تلخيص PDF", callback_data="summarize:ask"),
        ],
        [
            InlineKeyboardButton(text="👤 حسابي", callback_data="profile:me"),
            InlineKeyboardButton(text="🎯 تحليلي", callback_data="lrn:analytics"),
        ],
        ]

    # ⚙️ إعدادات الذكاء الاصطناعي — **أدمن ومالك فقط** (مخفية عن الطلاب
    # والمشرفين). هي التي تكتب في `.env`.
    if is_admin:
        rows.append([
            InlineKeyboardButton(text="🤖 الذكاء الاصطناعي", callback_data="ai:menu"),
            InlineKeyboardButton(text="🛠️ لوحة الإدارة", callback_data="adm:menu"),
        ])
    elif is_staff:
        rows.append([
            InlineKeyboardButton(text="🛠️ لوحة الإدارة", callback_data="adm:menu"),
        ])

    rows.append([
        InlineKeyboardButton(text="❓ المساعدة", callback_data="help:show"),
        InlineKeyboardButton(text="ℹ️ حول البوت", callback_data="about:show"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


HELP_TEXT = (
    f"{texts.header('help', 'المساعدة — طريقة الاستخدام')}\n\n"
    "📚 <b>المكتبة</b> — تصفّح: قسم ← مستوى ← سنة ← فصل ← مادة ← فئة ← ملف\n"
    "🔍 <b>بحث</b> — اكتب أي كلمة في الشات وهتبحث لوحدها في الأسماء "
    "والوصف والوسوم وداخل نصوص الـ PDF\n"
    "📤 <b>رفع ملف</b> — ارفع ملفًا لمادة، وبعد موافقة المشرف يظهر للجميع\n"
    "🗂️ <b>موادي</b> — عدّل أي ملف رفعته (عنوان/وصف/وسوم/فئة/رابط) أو احذفه\n"
    "🧑‍🏫 <b>اشرح لي</b> — شرح أي موضوع بأسلوبك ومستواك\n"
    "📄 <b>اشرح PDF/مادتي</b> — أرسل ملفًا (أو اختر من مكتبتك) فأحلّله، "
    "تختار نمط الشرح والتفصيل واللغة، وتكتب برومبتك الحر — "
    "<b>ورموزه تُحفظ حرفيًا</b>\n"
    "📝 <b>تلخيص ذكي</b> — أرسل PDF أو نصًا وخُذ ملخصًا على مقاسك\n"
    "🧠 <b>كويزات</b> — امتحانات بأزرار مع نتيجة وتحليل مستواك\n"
    "🌐 <b>ترجمة</b> — ترجم أي نص للعربية أو الإنجليزية\n"
    "👤 <b>حسابي</b> — مستواك وتخصيص أسلوب الشرح\n\n"
    f"{texts.DIVIDER}\n"
    "🎛️ <b>كل شيء بالأزرار</b> — مش محتاج تحفظ أوامر. "
    "لو ضيّعت، اضغط «📋 القائمة».\n"
    "لإلغاء أي خطوة جارية: زر «رجوع» أو «إلغاء» في نفس الرسالة."
)


@router.message(CommandStart())
async def cmd_start(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    await state.clear()
    welcome = await get_setting(db_session, "welcome_message")
    text = (
        f"👋 <b>{welcome or 'أهلًا بك في ذاكر - مساعدك الدراسى'}</b>\n\n"
        f"🎓 مرحبًا <b>{escape(db_user.display_name)}</b>\n"
        f"{texts.header('home')}\nاختر قسمًا للبدء:"
    )
    await message.answer(text, reply_markup=main_menu(db_user), parse_mode="HTML")


@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery, db_user: User,
                       state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.header("home") + "\nاختر قسمًا:", parse_mode="HTML")
    await callback.message.edit_reply_markup(reply_markup=main_menu(db_user))
    await callback.answer()


@router.callback_query(F.data == "help:show")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.message.answer(HELP_TEXT, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "noop:page")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
