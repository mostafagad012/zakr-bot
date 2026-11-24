"""تحليل المتعلّم: بناء ملفه، تخصيص الأسلوب، وتدريج المستوى.

المبدأ: **كل قرار قابل للتفسير ومبني على بيانات مخزّنة**، لا على تخمين
لحظي. الملف (`LearnerProfile`) يُبنى من مصدرين:

1. **استبيان بأزرار** عند أول استخدام → يحدّد أسلوب الشرح واللغة والطول.
2. **سلوك فعلي** (نتائج الامتحانات) → يعدّل الصعوبة والمستوى تدريجيًا،
   ويكشف نقاط الضعف.

ولماذا مزج الاثنين؟ لأن المستخدم يعرف تفضيله (سريع/مفصّل) لكنه لا يعرف
مستواه الحقيقي، والنظام يعرف مستواه من النتائج لكنه لا يعرف ذوقه. الجمع
يعطي تخصيصًا أدقّ من أي منهما وحده.

لا يعتمد هذا الموديول على شبكة ولا على الذكاء الاصطناعي: كل شيء يعمل
بلا مفتاح، وقرارات الصعوبة والمستوى رياضية بحتة وقابلة للاختبار.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LearnerProfile, LearningEvent, QuizAttempt

log = logging.getLogger(__name__)

# ------------------------------------------------------------ أسلوب الشرح
# كل خيار: (المفتاح المخزَّن، التسمية المعروضة، الوصف، تعليمة للنموذج)
EXPLAIN_STYLES: tuple[tuple[str, str, str, str], ...] = (
    ("بسيط", "🟢 بسيط ومباشر",
     "كلام سهل، بلا مصطلحات معقّدة",
     "اشرح بلغة بسيطة جدًا كأنك تشرح لشخص يسمع الموضوع لأول مرة. "
     "تجنّب المصطلحات، وإن اضطررت فسّرها في نفس السطر."),
    ("أكاديمي", "🎓 أكاديمي دقيق",
     "مصطلحات ومفاهيم كما في الكتاب",
     "اشرح بأسلوب أكاديمي دقيق مع المصطلحات العلمية الصحيحة "
     "وتعريفاتها، مع الحفاظ على التنظيم المنطقي."),
    ("قصصي", "📖 قصصي وسردي",
     "حكاية متسلسلة تثبت في الذاكرة",
     "اشرح على شكل قصة/سيناريو متسلسل بالأحداث، واربط المفاهيم "
     "بموقف واقعي ملموس حتى تُحفظ في الذاكرة."),
    ("خطوة_بخطوة", "🪜 خطوة بخطوة",
     "ترتيب رقمي واضح، لا يقفز خطوات",
     "اشرح بخطوات مرقّمة، كل خطوة تعتمد على التي قبلها، ولا تقفز "
     "أي خطوة وسيطة حتى لو بدت واضحة."),
    ("أمثلة", "🧩 بالأمثلة العملية",
     "كل فكرة ومعاها مثال محلول",
     "قدّم كل مفهوم مصحوبًا بمثال محلول كامل، ثم تمرينًا صغيرًا "
     "للتحقق من الفهم."),
    ("مقارنات", "⚖️ بالمقارنات والجداول",
     "يفرّق بين المتشابهات بجدول",
     "اشرح بالمقارنة: بيّن الفروق والتشابه بين المفاهيم المتقاربة "
     "في جدول أو نقاط متقابلة، لأن الالتباس بينها هو المشكلة."),
)

LENGTHS: tuple[tuple[str, str, str], ...] = (
    ("قصير", "⚡ قصير ومركّز", "أهم النقاط فقط"),
    ("متوسط", "📐 متوسط", "توازن بين الشرح والاختصار"),
    ("مفصّل", "📚 مفصّل وموسّع", "كل التفاصيل والأمثلة"),
)

LANGUAGES: tuple[tuple[str, str], ...] = (
    ("ar", "🇪🇬 عربي"),
    ("mixed", "🌐 عربي + المصطلح الإنجليزي"),
    ("en", "🇬🇧 إنجليزي"),
)

# عدد النقاط المطلوبة للانتقال إلى المستوى التالي
XP_PER_LEVEL = 100
MAX_LEVEL = 10

# عتبات ضبط الصعوبة تلقائيًا
PROMOTE_ACCURACY = 75.0   # فوقها: يستحق صعوبة أعلى
DEMOTE_ACCURACY = 45.0    # تحتها: يحتاج تخفيفًا


def style_instruction(style: str) -> str:
    """التعليمة النصية لأسلوب الشرح — تُمرَّر للنموذج حرفيًا."""
    for key, _label, _desc, instruction in EXPLAIN_STYLES:
        if key == style:
            return instruction
    return EXPLAIN_STYLES[0][3]


def style_label(style: str) -> str:
    for key, label, _desc, _instruction in EXPLAIN_STYLES:
        if key == style:
            return label
    return style


def length_instruction(length: str) -> str:
    """تعليمة الطول المطلوب."""
    mapping = {
        "قصير": "كن موجزًا جدًا: أهم النقاط في حدود فقرات قليلة.",
        "متوسط": "اجعل الشرح متوسط الطول: فكرة رئيسية + توضيح لكل جزء.",
        "مفصّل": "وسّع الشرح: تفاصيل كاملة، أمثلة متعددة، وحالات خاصة.",
    }
    return mapping.get(length, mapping["متوسط"])


def language_instruction(language: str) -> str:
    mapping = {
        "ar": "اكتب كل الرد بالعربية الفصحى المبسّطة.",
        "mixed": "اكتب بالعربية، وأبقِ المصطلح العلمي بالإنجليزية بين قوسين عند أول ظهور.",
        "en": "Write the entire response in clear English.",
    }
    return mapping.get(language, mapping["ar"])


# ------------------------------------------------------------ الملف نفسه
async def get_or_create(session: AsyncSession, user_id: int) -> LearnerProfile:
    """يجلب ملف المتعلّم أو ينشئه بقيم افتراضية معقولة."""
    profile = await session.scalar(
        select(LearnerProfile).where(LearnerProfile.user_id == user_id)
    )
    if profile is not None:
        return profile
    profile = LearnerProfile(user_id=user_id)
    session.add(profile)
    await session.flush()
    return profile


async def get_profile(session: AsyncSession, user_id: int) -> LearnerProfile | None:
    return await session.scalar(
        select(LearnerProfile).where(LearnerProfile.user_id == user_id)
    )


async def save_survey(
    session: AsyncSession,
    user_id: int,
    *,
    explain_style: str | None = None,
    length_pref: str | None = None,
    language: str | None = None,
    uses_examples: bool | None = None,
    uses_analogies: bool | None = None,
    prefers_bullets: bool | None = None,
) -> LearnerProfile:
    """يحفظ إجابات الاستبيان ويتحقق من صحتها.

    التحقق مهم: القيم تأتي من `callback_data` وهو **مدخل من المستخدم**
    يمكن تزويره، فلا تُخزَّن كما هي قبل مطابقتها بالقوائم المعروفة.
    """
    profile = await get_or_create(session, user_id)
    valid_styles = {key for key, *_ in EXPLAIN_STYLES}
    valid_lengths = {key for key, *_ in LENGTHS}
    valid_langs = {key for key, _ in LANGUAGES}

    if explain_style is not None:
        if explain_style not in valid_styles:
            raise ValueError("أسلوب شرح غير معروف")
        profile.explain_style = explain_style
    if length_pref is not None:
        if length_pref not in valid_lengths:
            raise ValueError("طول غير معروف")
        profile.length_pref = length_pref
    if language is not None:
        if language not in valid_langs:
            raise ValueError("لغة غير معروفة")
        profile.language = language
    if uses_examples is not None:
        profile.uses_examples = bool(uses_examples)
    if uses_analogies is not None:
        profile.uses_analogies = bool(uses_analogies)
    if prefers_bullets is not None:
        profile.prefers_bullets = bool(prefers_bullets)

    profile.survey_done = True
    profile.last_activity_at = datetime.now(timezone.utc)
    await session.flush()
    return profile


def build_personalization(profile: LearnerProfile | None) -> str:
    """يبني كتلة التعليمات الشخصية التي تُمرَّر للنموذج.

    كل تعليمة مستخرجة من حقل مخزَّن — لا اجتهاد لحظي، فيبقى السلوك
    ثابتًا وقابلًا للمراجعة.
    """
    if profile is None:
        return (
            "أسلوب المستخدم غير معروف بعد. اشرح بلغة بسيطة ومتوسطة الطول "
            "مع أمثلة، واعرض عليه تخصيص الأسلوب."
        )

    parts = [
        style_instruction(profile.explain_style),
        length_instruction(profile.length_pref),
        language_instruction(profile.language),
    ]
    if profile.prefers_bullets:
        parts.append("نظّم الرد في نقاط قصيرة لا كتلة نص واحدة.")
    if profile.uses_examples:
        parts.append("أضف مثالًا تطبيقيًا على الأقل.")
    if profile.uses_analogies:
        parts.append("استخدم تشبيهًا من الحياة اليومية لتقريب الفكرة.")

    parts.append(
        f"مستواه الحالي {profile.level} من {MAX_LEVEL}"
        + (f"، ونسبة إجاباته الصحيحة {profile.accuracy_percent}%" if profile.total_answers else "")
        + ". اضبط عمق الشرح على هذا المستوى."
    )
    weak = _load_json(profile.weak_topics)
    if weak:
        top = sorted(weak.items(), key=lambda kv: kv[1], reverse=True)[:3]
        parts.append(
            "نقاط ضعفه المعروفة (ركّز عليها): "
            + "، ".join(f"{topic} ({count} خطأ)" for topic, count in top)
        )
    return "\n".join(f"- {p}" for p in parts)


def _load_json(raw: str | None) -> dict:
    """قراءة JSON مخزَّن بأمان — بيانات تالفة لا تُسقط البوت."""
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------- تدريج الصعوبة والمستوى
@dataclass(frozen=True)
class QuizOutcome:
    """نتيجة تحديث الملف بعد امتحان — تُعرض للمستخدم كتفسير."""

    level_before: int
    level_after: int
    xp_gained: int
    next_difficulty: str
    message: str
    weak_topics: list[str] = field(default_factory=list)

    @property
    def leveled_up(self) -> bool:
        return self.level_after > self.level_before


def next_difficulty(profile: LearnerProfile) -> str:
    """الصعوبة المقترحة للامتحان القادم.

    القاعدة: المستوى يحدّد الأرضية، والنتائج الأخيرة تحرّك سقفها.
    مبتدئ لا يُعطى «صعب» لمجرد أنه طلب ذلك، ولا متقدّم يُعطى «سهل» دائمًا
    لأن الملل يقتل التعلّم كما يقتله الإحباط.
    """
    if profile.difficulty_pref and profile.difficulty_pref != "auto":
        return profile.difficulty_pref

    accuracy = profile.accuracy_percent
    answers = profile.total_answers or 0
    level = profile.level or 1
    # بلا سجل كافٍ: الصعوبة تتبع المستوى وحده
    if answers < 10:
        if level <= 3:
            return "easy"
        if level <= 6:
            return "medium"
        return "hard"

    if accuracy >= PROMOTE_ACCURACY:
        return "medium" if level <= 2 else "hard"
    if accuracy <= DEMOTE_ACCURACY:
        return "easy" if level <= 4 else "medium"
    return "medium"


async def record_quiz_result(
    session: AsyncSession,
    user_id: int,
    *,
    correct: int,
    total: int,
    difficulty: str | None = None,
    topic: str | None = None,
) -> QuizOutcome:
    """يحدّث الملف بعد امتحان: خبرة، مستوى، صعوبة قادمة، ونقاط ضعف.

    قواعد المستوى (مقصودة وبسيطة وقابلة للتفسير للمستخدم):
    * الخبرة = عدد الإجابات الصحيحة × 10، مضروبة في وزن الصعوبة
      (سهل 1، متوسط 1.5، صعب 2) — فالإجابة الصحيحة في الصعب تساوي ضعفها.
    * الانتقال لمستوى أعلى عند بلوغ العتبة، مع سقف `MAX_LEVEL`.
    * **لا نُنزل المستوى أبدًا** بسبب امتحان واحد؛ نُنزل الصعوبة فقط.
      إنزال المستوى عقاب يُحبط المتعلّم، وتخفيف الصعوبة يحقّق نفس الهدف
      تربويًا بلا أثر نفسي سلبي.
    """
    profile = await get_or_create(session, user_id)
    level_before = profile.level
    difficulty = difficulty or "medium"
    weight = {"easy": 1.0, "medium": 1.5, "hard": 2.0}.get(difficulty, 1.5)

    gained = int(round(max(0, correct) * 10 * weight))
    profile.xp += gained
    profile.quizzes_taken += 1
    profile.correct_answers += max(0, correct)
    profile.total_answers += max(0, total)
    if total and correct / total >= 0.6:
        profile.quizzes_passed += 1

    # صعود المستويات
    while profile.xp >= XP_PER_LEVEL and profile.level < MAX_LEVEL:
        profile.xp -= XP_PER_LEVEL
        profile.level += 1

    # تتبّع دقّة كل صعوبة
    by_diff = _load_json(profile.accuracy_by_difficulty)
    bucket = by_diff.get(difficulty)
    if not isinstance(bucket, list):
        bucket = []
    bucket.append(1 if total and correct / total >= 0.6 else 0)
    by_diff[difficulty] = bucket[-10:]  # آخر 10 محاولات فقط
    profile.accuracy_by_difficulty = _dump_json(by_diff)

    # نقاط الضعف: الموضوع يتراكم بعدد الأخطاء
    weak: list[str] = []
    if topic and correct < total:
        topics = _load_json(profile.weak_topics)
        topics[topic] = int(topics.get(topic, 0)) + (total - correct)
        profile.weak_topics = _dump_json(topics)
        weak = [t for t, _ in sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:3]]

    # السلسلة اليومية
    now = datetime.now(timezone.utc)
    last = profile.last_activity_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        gap = now.date() - last.date()
        if gap == timedelta(days=1):
            profile.streak_days += 1
        elif gap > timedelta(days=1):
            profile.streak_days = 1
    else:
        profile.streak_days = 1
    profile.last_activity_at = now

    await session.flush()

    upcoming = next_difficulty(profile)
    if profile.level > level_before:
        message = f"🎉 ارتقيت إلى المستوى {profile.level} من {MAX_LEVEL}!"
    elif profile.xp >= XP_PER_LEVEL * 0.7:
        message = f"💪 اقتربت من المستوى التالي ({profile.xp}/{XP_PER_LEVEL} خبرة)"
    else:
        message = f"📈 تقدّمت: +{gained} خبرة"

    return QuizOutcome(
        level_before=level_before,
        level_after=profile.level,
        xp_gained=gained,
        next_difficulty=upcoming,
        message=message,
        weak_topics=weak,
    )


async def log_event(
    session: AsyncSession,
    user_id: int,
    kind: str,
    *,
    subject_id: int | None = None,
    topic: str | None = None,
    difficulty: str | None = None,
    is_correct: bool | None = None,
    detail: str | None = None,
) -> LearningEvent:
    """يسجّل حدث تعلّم — بلا commit (المتصل يقرّر)."""
    event = LearningEvent(
        user_id=user_id,
        kind=kind,
        subject_id=subject_id,
        topic=topic,
        difficulty=difficulty,
        is_correct=is_correct,
        detail=(detail or "")[:500] or None,
    )
    session.add(event)
    await session.flush()
    return event


async def recent_attempts(
    session: AsyncSession, user_id: int, limit: int = 5
) -> list[QuizAttempt]:
    """آخر محاولات الامتحانات — تُعرض في شاشة «حسابي»."""
    return list(
        await session.scalars(
            select(QuizAttempt)
            .where(QuizAttempt.user_id == user_id)
            .order_by(QuizAttempt.created_at.desc())
            .limit(limit)
        )
    )


def summary_text(profile: LearnerProfile) -> str:
    """ملخّص نصي لملف المتعلّم — يُعرض للمستخدم في «تحليلي»."""
    weak = _load_json(profile.weak_topics)
    lines = [
        "🎯 <b>تحليلي</b>",
        f"🪜 المستوى: <b>{profile.level}</b> / {MAX_LEVEL} (خبرة {profile.xp}/{XP_PER_LEVEL})",
        f"📊 الدقّة الإجمالية: <b>{profile.accuracy_percent}%</b> "
        f"({profile.correct_answers}/{profile.total_answers})",
        f"🧠 أسلوب الشرح: {style_label(profile.explain_style)}",
        f"📏 الطول المفضّل: {profile.length_pref}",
        f"🗣️ اللغة: {profile.language}",
        f"⚖️ الصعوبة القادمة: {next_difficulty(profile)}",
    ]
    if profile.streak_days:
        lines.append(f"🔥 أيام متتالية: {profile.streak_days}")
    if weak:
        top = sorted(weak.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines.append("⚠️ نقاط تحتاج مراجعة: " + "، ".join(t for t, _ in top))
    if not profile.survey_done:
        lines.append("\n💡 خصّص أسلوبك من: 👤 حسابي → 🎨 أسلوب الشرح")
    return "\n".join(lines)
