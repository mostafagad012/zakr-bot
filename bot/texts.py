"""نصوص وتنسيقات واجهة البوت — مصدر واحد ينظّم كلام كل الأقسام.

لماذا ملف مركزي؟ كانت رسائل الأقسام (مكتبة/بحث/رفع/كويز/إدارة…)
متناثرة الصياغة والإيموجي داخل كل handler، فأي تحسين شكلي يحتاج
مطاردة في ٩ ملفات. هنا تعيش:

* إيموجي وأسماء الأقسام (جدول `SECTIONS`) — غيّر من هنا ينتشر.
* عناصر بناء الرسائل: ترويسة، فاصل، سطر معلومة، بطاقة ملف.
* عدّاد التقدّم الحي: شريط تقدّم + زمن منقضٍ + متبقٍّ تقديري،
  تعرضه رسالة حالة أثناء المعالجات الطويلة (OCR/رفع) — انظر
  `ProgressReporter` في `handlers/helpers.py`.

كل الدوال نقيّة (نص في ← نص خارج) وقابلة للاختبار بلا تيليجرام.
"""
from __future__ import annotations

DIVIDER = "➖➖➖➖➖➖➖➖"

# إيموجي كل قسم — مصدر الحقيقة الوحيد لأزرار القائمة وترويساتها.
SECTIONS = {
    "library": ("📚", "المكتبة"),
    "search": ("🔍", "بحث"),
    "upload": ("📤", "رفع ملف"),
    "recent": ("🆕", "أحدث الملفات"),
    "quiz": ("🧠", "كويزات"),
    "translate": ("🌐", "ترجمة"),
    "summarize": ("📝", "تلخيص PDF"),
    "help": ("❓", "المساعدة"),
    "about": ("ℹ️", "حول البوت"),
    "admin": ("🛠️", "لوحة الإدارة"),
    "profile": ("👤", "حسابي"),
    "explain": ("🧑‍🏫", "اشرح لي"),
    "tutor": ("🤖", "الشرح الذكي"),
    "analytics": ("🎯", "تحليلي"),
    "materials": ("🗂️", "موادي"),
    "file": ("📄", "ملف"),
    "subject": ("📘", "مادة"),
    "category": ("📁", "فئة"),
    "home": ("📋", "القائمة الرئيسية"),
}


def header(key: str, title: str | None = None, subtitle: str | None = None) -> str:
    """ترويسة قسم موحّدة: «📚 <b>المكتبة</b>» + سطر فرعي اختياري."""
    emoji, default_title = SECTIONS.get(key, ("🔹", key))
    text = f"{emoji} <b>{title or default_title}</b>"
    if subtitle:
        text += f"\n{subtitle}"
    return text


def info_row(emoji: str, label: str, value: object) -> str:
    """سطر معلومة منظّم: «📦 الحجم: 2.4 MB»."""
    return f"{emoji} {label}: {value}"


def info_block(rows: list[tuple[str, str, object]]) -> str:
    return "\n".join(info_row(emoji, label, value) for emoji, label, value in rows)


def progress_bar(done: int, total: int | None, width: int = 12) -> str:
    """شريط تقدّم نصي: «███████░░░░░». بلا إجمالي → شريط غير محدد."""
    if not total or total <= 0:
        return "░" * width
    ratio = max(0.0, min(1.0, done / total))
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def progress_percent(done: int, total: int | None) -> str:
    if not total or total <= 0:
        return ""
    return f"{round(100 * max(0, min(done, total)) / total)}%"


def fmt_duration(seconds: float) -> str:
    """زمن مقروء: «0:07»، «1:35»، «1:02:05» بعد الساعة."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def eta_seconds(elapsed: float, done: int, total: int | None) -> float | None:
    """المتبقّي التقديري من معدّل الإنجاز الحالي. None = لا يُعرف بعد."""
    if not total or done <= 0 or done >= total or elapsed <= 0:
        return None
    return elapsed / done * (total - done)


def processing_card(title: str, stage: str, done: int, total: int | None,
                    elapsed: float, frame: int = 0) -> str:
    """بطاقة «الملف شغّال الآن» الحية التي تُحرَّر كل ثوانٍ.

    تعرض: العنوان، المرحلة، صفحة/خطوة الحالية من الإجمالي، الشريط،
    المنقضي والمتبقّي التقديري — حتى يعرف المستخدم أن الملف يُعالَج
    وفاضل قد إيه بدل رسالة «جارٍ…» صامتة قد تعلّق لدقائق (OCR).
    """
    spinner = "⏳" if frame % 2 == 0 else "⌛"
    lines = [f"{spinner} <b>{title}</b>", f"🔧 المرحلة: {stage}"]
    if total:
        lines.append(f"📄 التقدّم: {min(done, total)} من {total}")
        percent = progress_percent(done, total)
        lines.append(f"{progress_bar(done, total)} {percent}".rstrip())
    elif done:
        lines.append(f"📄 تمّت معالجة: {done}")
    lines.append(f"⏱️ الوقت المنقضي: {fmt_duration(elapsed)}")
    eta = eta_seconds(elapsed, done, total)
    if eta is not None:
        lines.append(f"⏳ المتبقّي تقريبًا: {fmt_duration(eta)}")
    elif total and done >= total:
        lines.append("✨ جارٍ الإنهاء…")
    return "\n".join(lines)


def steps_header(key: str, step: int, total_steps: int, title: str | None = None) -> str:
    """ترويسة معالج متعدد الخطوات مع شريط: «📤 <b>رفع ملف — الخطوة 2/4</b>»."""
    base = header(key, title)
    return f"{base} — الخطوة {step}/{total_steps}\n{progress_bar(step, total_steps, width=8)}"
