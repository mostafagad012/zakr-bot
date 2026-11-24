"""نماذج قاعدة البيانات (SQLAlchemy 2.0)

مصممة لتكون متوافقة مع SQLite الآن و PostgreSQL/Supabase لاحقًا:
لا أنواع خاصة بمحرك معيّن، لا أعمدة ARRAY، والمفاتيح كلها INTEGER/BIGINT.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# BIGINT على PostgreSQL و INTEGER على SQLite حتى يعمل الترقيم التلقائي
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    STUDENT = "student"      # طالب: تصفّح وتحميل ورفع
    MODERATOR = "moderator"  # مشرف: مراجعة الملفات وإدارة المواد
    ADMIN = "admin"          # أدمن: كل صلاحيات المشرف + إدارة المستخدمين
    OWNER = "owner"          # المالك: وحيد، لا يُنزع


class FileStatus(str, enum.Enum):
    PENDING = "pending"    # بانتظار مراجعة
    APPROVED = "approved"  # منشور و visible للطلاب
    REJECTED = "rejected"  # مرفوض
    DELETED = "deleted"    # محذوف من المكتبة


class QuizDifficulty(str, enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


# ---------------------------------------------------------------- الصلاحيات
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)  # مفتاح بديل
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)  # المفتاح الطبيعي
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default=UserRole.STUDENT.value, index=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    uploads_count: Mapped[int] = mapped_column(Integer, default=0)
    downloads_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    files: Mapped[list["FileItem"]] = relationship(
        back_populates="uploader", foreign_keys="FileItem.uploader_id"
    )
    reviewed_files: Mapped[list["FileItem"]] = relationship(foreign_keys="FileItem.reviewed_by")

    # مستويات الصلاحية كرقم لسهولة المقارنة
    LEVELS = {"student": 0, "moderator": 1, "admin": 2, "owner": 3}

    @property
    def level(self) -> int:
        return self.LEVELS.get(self.role, 0)

    def is_staff(self) -> bool:
        return self.level >= 1

    def is_admin(self) -> bool:
        return self.level >= 2

    @property
    def display_name(self) -> str:
        name = self.first_name or self.username or str(self.telegram_id)
        return name


# ------------------------------------------------------- الهيكل الأكاديمي
class Department(Base):
    """قسم / تخصص (مثال: حاسبات ومعلومات، طب، تجارة)"""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subjects: Mapped[list["Subject"]] = relationship(back_populates="department")


class Level(Base):
    """مستوى دراسي (مثال: الفرقة الأولى، السنة الثانية)"""

    __tablename__ = "levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AcademicYear(Base):
    """عام دراسي (مثال: 2025-2026)"""

    __tablename__ = "academic_years"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Semester(Base):
    """فصل دراسي (مثال: الترم الأول، الترم الثاني، صيفي)"""

    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Subject(Base):
    """مادة دراسية — مربوطة بالقسم والمستوى والعام والفصل"""

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("department_id", "level_id", "year_id", "semester_id", "name",
                         name="uq_subject_path"),
        Index("ix_subjects_browse", "department_id", "level_id", "year_id", "semester_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"))
    level_id: Mapped[int | None] = mapped_column(ForeignKey("levels.id", ondelete="SET NULL"), nullable=True)
    year_id: Mapped[int | None] = mapped_column(ForeignKey("academic_years.id", ondelete="SET NULL"), nullable=True)
    semester_id: Mapped[int | None] = mapped_column(ForeignKey("semesters.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    department: Mapped["Department"] = relationship(back_populates="subjects")
    level: Mapped["Level | None"] = relationship()
    year: Mapped["AcademicYear | None"] = relationship()
    semester: Mapped["Semester | None"] = relationship()
    files: Mapped[list["FileItem"]] = relationship(back_populates="subject")


class FileCategory(Base):
    """فئة الملف داخل المادة (محاضرات، ملخصات، امتحانات، فيديوهات، إضافي)"""

    __tablename__ = "file_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------------ الملفات
class FileItem(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_visible", "subject_id", "category_id", "status"),
        Index("ix_files_status", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("file_categories.id", ondelete="RESTRICT"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # مفصولة بفواصل

    original_filename: Mapped[str | None] = mapped_column(String(240), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pdf/docx/image/video/link

    # التخزين في تيليجرام
    storage_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_unique_file_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # رابط خارجي اختياري
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # معالجة المحتوى
    pages_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_extracted: Mapped[bool] = mapped_column(Boolean, default=False)

    # ملاحظة: لا index=True هنا — الفهرس المركّب ix_files_status يغطّي عمود status
    status: Mapped[str] = mapped_column(String(16), default=FileStatus.PENDING.value)
    downloads_count: Mapped[int] = mapped_column(Integer, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subject: Mapped["Subject"] = relationship(back_populates="files")
    category: Mapped["FileCategory"] = relationship()
    uploader: Mapped["User | None"] = relationship(back_populates="files", foreign_keys=[uploader_id])

    @property
    def tag_list(self) -> list[str]:
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class FileVector(Base):
    """متجه TF-IDF لكل ملف — أساس البحث الدلالي.

    يُخزَّن كـ JSON نصي ليعمل على SQLite وPostgreSQL بلا إضافات.
    الترقية إلى pgvector موثّقة في docs/OPERATIONS.md.
    """

    __tablename__ = "file_vectors"

    file_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    terms: Mapped[str] = mapped_column(Text)
    norm: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    # متجه التضمين العصبي (JSON نصي ليعمل على SQLite وPostgreSQL بلا إضافات).
    # فارغ = لم يُحسب (المزوّد غير مفعّل أو النص قصير).
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)


# ------------------------------------------------------------ سجل الرقابة
class ModerationLog(Base):
    __tablename__ = "moderation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20))   # file/user/subject
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(30))        # approve/reject/delete/ban/promote
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------- الكويزات
class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(180))
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10), default=QuizDifficulty.MEDIUM.value)
    pass_percent: Mapped[int] = mapped_column(Integer, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    questions: Mapped[list["QuizQuestion"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan"
    )


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[str] = mapped_column(Text)          # خيارات مفصولة بـ ||
    correct_index: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    quiz: Mapped["Quiz"] = relationship(back_populates="questions")

    @property
    def option_list(self) -> list[str]:
        return [o.strip() for o in self.options.split("||") if o.strip()]


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ الإعدادات
class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


DEFAULT_CATEGORIES = [
    ("محاضرات", "📘", 1),
    ("ملخصات", "📝", 2),
    ("امتحانات سابقة", "📄", 3),
    ("فيديوهات", "🎬", 4),
    ("ملفات إضافية", "📎", 5),
]

DEFAULT_LEVELS = ["الفرقة الأولى", "الفرقة الثانية", "الفرقة الثالثة", "الفرقة الرابعة"]
DEFAULT_SEMESTERS = ["الترم الأول", "الترم الثاني", "الفصل الصيفي"]


# ================================================ إعدادات المستخدم (AI)
class UserAiSetting(Base):
    """إعدادات الذكاء الاصطناعي الخاصة بمستخدم واحد.

    لماذا جدول منفصل عن `learner_profiles`؟ لأن هذه **تفضيلات تقنية**
    (أي مزوّد/موديل) لا خصائص تعليمية، وقد يحفظ المستخدم مفاتيح عدة
    مزوّدين ويبدّل بينهم. وفصلها يمنع خلط بيانات التعلّم بالأسرار.
    """

    __tablename__ = "user_ai_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    # مفاتيح محفوظة (JSON نصي): {"gemini": "...", "groq": "..."}
    # ⚠️ تُخزَّن في قاعدة البيانات — لا تُسجَّل ولا تُعرض كاملة أبدًا.
    keys_json: Mapped[str] = mapped_column(Text, default="{}")
    # صور/نصوص المادة المرجعية للتخصيص (نص حر يكتبه المستخدم)
    notes: Mapped[str] = mapped_column(Text, default="")
    auto_provider: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ============================================ سجل تغييرات الإعداد (تدقيق)
class ConfigAudit(Base):
    """من غيّر `.env` وامتى وماذا تغيّر — **بلا أي قيمة سرية**.

    لماذا جدول ولا السجل (log)؟ لأن السؤال «مين بدّل المزوّد قبل ما البوت
    يقع؟» يُطرح بعد أيام، وسجلات systemd/الملفات تكون قد دُوّرت. والجدول
    يعيش مع قاعدة البيانات وينسخ معها في النسخ الاحتياطي.

    **قاعدة صارمة**: `old_value`/`new_value` لا تحمل مفتاحًا أبدًا — للمفاتيح
    يُسجَّل `«مضبوط»` أو `«حُذف»` فقط.
    """

    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_name: Mapped[str] = mapped_column(String(120), default="")
    key: Mapped[str] = mapped_column(String(60), index=True)     # AI_PROVIDER…
    old_value: Mapped[str] = mapped_column(String(120), default="")
    new_value: Mapped[str] = mapped_column(String(120), default="")
    outcome: Mapped[str] = mapped_column(String(20), default="ok")  # ok|readonly|failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ================================================== استهلاك الذكاء الاصطناعي
class AiUsage(Base):
    """سطر لكل طلب ذكاء اصطناعي — أساس السقف اليومي ولوحة الأدمن.

    لماذا صف لكل طلب لا عدّاد فقط؟ لأن العدّاد لا يجيب على «إيه المزوّد
    اللي بيفشل؟» ولا «مين بيستهلك أكتر؟». والصفوف رخيصة، ونحذف القديم منها
    دوريًا (`prune`).
    """

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), default="")
    kind: Mapped[str] = mapped_column(String(20), default="")   # explain/summary/exam/…
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    # مقصود: لا نحفظ نص الطلب ولا الرد — التكلفة والخصوصية معًا
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ============================================================ ملف المتعلّم
class LearnerProfile(Base):
    """ملف تحليل المتعلّم — أساس تخصيص الشرح والتلخيص والامتحانات.

    لماذا جدول منفصل لا أعمدة في `users`؟ لأن هذه بيانات تتغيّر كثيرًا
    (كل امتحان يحدّثها) ولا تخصّ الصلاحيات ولا الحساب، وخلطها في `users`
    يجعل كل استعلام مستخدم يحملها بلا داعٍ.
    """

    __tablename__ = "learner_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    # ---- أسلوب الشرح (من الاستبيان) ----
    # بسيط | أكاديمي | قصصي | خطوة_بخطوة | أمثلة | مقارنات
    explain_style: Mapped[str] = mapped_column(String(20), default="بسيط")
    # قصير | متوسط | مفصّل
    length_pref: Mapped[str] = mapped_column(String(20), default="متوسط")
    uses_examples: Mapped[bool] = mapped_column(Boolean, default=True)
    uses_analogies: Mapped[bool] = mapped_column(Boolean, default=False)
    prefers_bullets: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(10), default="ar")   # ar | en | mixed
    difficulty_pref: Mapped[str] = mapped_column(String(10), default="auto")  # auto|easy|medium|hard

    # ---- المستوى المتدرّج (يُحدَّث تلقائيًا من نتائج الامتحانات) ----
    level: Mapped[int] = mapped_column(Integer, default=1)   # 1..10
    xp: Mapped[int] = mapped_column(Integer, default=0)      # نقاط خبرة داخل المستوى

    # ---- إحصاءات تُبنى تراكميًا ----
    quizzes_taken: Mapped[int] = mapped_column(Integer, default=0)
    quizzes_passed: Mapped[int] = mapped_column(Integer, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0)
    total_answers: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- تخصيص مشتق: نسب الصواب لكل صعوبة (JSON نصي) ----
    # مثال: {"easy": [1,2,3], "medium": [0,1,1], "hard": [0,0]}
    accuracy_by_difficulty: Mapped[str] = mapped_column(Text, default="{}")

    # ---- ما يحتاجه المستخدم للتحسّن (يستنتجه النظام) ----
    weak_topics: Mapped[str] = mapped_column(Text, default="{}")   # JSON: {"موضوع": عدد_الأخطاء}

    survey_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    @property
    def accuracy_percent(self) -> float:
        """نسبة الدقّة — محصّنة ضد None قبل الحفظ/التحميل الكامل.

        الكائن قبل `flush` لا تحمل حقوله القيم الافتراضية (تُطبَّق في
        قاعدة البيانات)، فمقارنة None كانت تُسقط التحليل.
        """
        total = self.total_answers or 0
        if total <= 0:
            return 0.0
        return round((self.correct_answers or 0) * 100.0 / total, 1)


class LearningEvent(Base):
    """سجل أحداث التعلّم — يغذّي التحليل ويسمح بمراجعة أي قرار.

    كل حدث صغير ومستقل (شرح/تلخيص/إجابة) حتى يمكن حساب التحليلات
    لاحقًا دون إعادة تصميم الجداول.
    """

    __tablename__ = "learning_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # explain | summarize | quiz_answer | quiz_finish | survey
    kind: Mapped[str] = mapped_column(String(20), index=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    topic: Mapped[str | None] = mapped_column(String(120), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
