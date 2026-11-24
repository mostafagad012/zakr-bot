"""قسم «حول البوت»: تعريف، مزايا، إحصائيات حيّة، واسم المطوّر."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..config import settings
from ..services import files as files_service
from ..services.monitor import human_uptime
from .helpers import back_button, escape

router = Router(name="about")

FEATURES = [
    ("📚", "مكتبة منظّمة", "قسم ← مستوى ← سنة ← فصل ← مادة ← فئة ← ملف"),
    ("📤", "رفع ملفات", "PDF وWord وصور وفيديو وروابط، مع وصف ووسوم"),
    ("🔍", "بحث ذكي", "في الأسماء والوسوم وداخل نصوص الـ PDF + بحث دلالي بالمعنى"),
    ("🔎", "OCR", "قراءة الملفات المصوّرة بالعربية والإنجليزية"),
    ("🛡️", "مراجعة", "الملفات تنتظر موافقة مشرف قبل ظهورها"),
    ("🧠", "كويزات", "امتحانات بأزرار مع نتائج وتحليل مستواك"),
    ("🧑‍🏫", "شرح وتلخيص ذكي", "شرح وتلخيص وامتحانات على مقاس أسلوبك"),
    ("🌐", "ترجمة", "نص ↔ عربية/إنجليزية"),
    ("📝", "تلخيص", "ملخص لأهم نقاط أي PDF"),
    ("💾", "نسخ احتياطي", "تلقائي كل 12 ساعة مع تدوير النسخ"),
]


def about_text(stats: dict | None = None) -> str:
    lines = [
        texts.header("about"),
        "",
        "🎓 بوت <b>ذاكر</b> — مساعدك الدراسي داخل تيليجرام: "
        "تصفّح، رفع، بحث داخل المحتوى، شرح ذكي، كويزات.",
        "",
        "✨ <b>المزايا</b>",
    ]
    for icon, title, description in FEATURES:
        lines.append(f"{icon} <b>{title}</b> — {description}")

    if stats:
        lines += [
            "",
            "📊 <b>المكتبة الآن</b>",
            f"📘 المواد: {stats['subjects']} | 📄 الملفات: {stats['files_total']}",
            f"📥 التحميلات: {stats['downloads']} | 👥 المستخدمون: {stats['users']}",
        ]

    lines += ["", f"🕓 وقت التشغيل: {human_uptime()}", f"🔖 الإصدار: {settings.version}"]

    if settings.developer_name:
        lines += ["", texts.DIVIDER, f"👨‍💻 <b>تطوير:</b> {escape(settings.developer_name)}"]

    return "\n".join(lines)[:4000]


def about_markup() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 إحصائيات تفصيلية", callback_data="about:stats"),
        InlineKeyboardButton(text="🩺 حالة النظام", callback_data="about:health"),
    )
    builder.row(back_button("⬅️ القائمة", "menu:main"))
    return builder


async def cmd_about(message: Message, db_session) -> None:
    stats = await files_service.library_stats(db_session)
    await message.answer(about_text(stats), reply_markup=about_markup().as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "about:show")
async def cb_about(callback: CallbackQuery, db_session) -> None:
    stats = await files_service.library_stats(db_session)
    await callback.message.edit_text(about_text(stats), reply_markup=about_markup().as_markup(),
                                     parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "about:stats")
async def cb_about_stats(callback: CallbackQuery, db_session) -> None:
    from ..services import embeddings as embeddings_service
    from ..services.files import human_size
    from ..services.semantic import count as vectors_count
    from ..services.semantic import embedding_coverage

    stats = await files_service.library_stats(db_session)
    from ..services import users as users_service
    top = await users_service.top_uploaders(db_session, limit=5)
    vectors = await vectors_count(db_session)
    neural, _total = await embedding_coverage(db_session)

    lines = [
        "📊 <b>إحصائيات المكتبة</b>",
        f"👥 المستخدمون: {stats['users']}",
        f"📘 المواد: {stats['subjects']}",
        f"📄 الملفات: {stats['files_total']} (منشورة {stats['files_approved']})",
        f"📥 التحميلات: {stats['downloads']}",
        f"📦 حجم التخزين: {human_size(stats['storage_bytes'])}",
        f"🧭 ملفات مفهرسة دلاليًا: {vectors}"
        + (f" (منها {neural} بالنموذج العصبي)" if neural else " (TF-IDF)"),
        "🔎 قراءة الملفات المصوّرة: عبر وكيل الذكاء الاصطناعي بمفتاحك",
        f"🧠 التضمين العصبي: {'مفعّل' if embeddings_service.is_available() else 'معطّل'}",
    ]
    if top:
        lines += ["", "<b>أكثر المساهمين</b>"]
        for i, user in enumerate(top, start=1):
            lines.append(f"{i}. {escape(user.display_name)} — {user.uploads_count} ملف")

    builder = InlineKeyboardBuilder()
    builder.row(back_button("⬅️ حول البوت", "about:show"))
    await callback.message.edit_text("\n".join(lines)[:4000], reply_markup=builder.as_markup(),
                                     parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "about:health")
async def cb_about_health(callback: CallbackQuery, db_session) -> None:
    from ..services.monitor import health_report

    report = await health_report(db_session)
    header = "🩺 <b>حالة النظام</b> — " + ("كل شيء يعمل ✅" if report.ok else "توجد ملاحظات ⚠️")
    builder = InlineKeyboardBuilder()
    builder.row(back_button("⬅️ حول البوت", "about:show"))
    await callback.message.edit_text(
        header + "\n\n" + "\n".join(report.lines)[:3500],
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()
