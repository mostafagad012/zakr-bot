"""جداول تحليل المتعلّم + جدول البحث النصي (FTS)

Revision ID: f88640426147
Revises: b7d2e4f91c38
Create Date: 2026-11-14 21:43:18.752134

**لماذا هذا الترحيل موجود؟**

قبلَه كانت `learner_profiles` و`learning_events` **معرَّفتين في النماذج بلا
ترحيل**. والنتيجة أن من يتبع مسار الترقية الموثّق:

```bash
pip install asyncpg
alembic upgrade head
```

يحصل على قاعدة **بلا الجدولين** — فينهار «حسابي» و«تحليلي» وكل تتبّع
للتعلّم عند أول استخدام، بينما التثبيت الجديد (الذي ينشئ الجداول بـ
`create_all`) يعمل تمامًا. أي أن العطل يظهر فقط عند من رقّى — وهذا أسوأ
أنواع الأخطاء لأنه يمرّ من الاختبارات.

ونُضيف هنا كذلك جدول البحث `files_fts` بأنواعه: `create_all` لا يُنشئ
الجداول الافتراضية (Virtual Table) الخاصة بـ FTS5 ولا `tsvector` في
PostgreSQL، فالمُرقّي كان يفقد البحث داخل نصوص الملفات أيضًا.

> ملاحظة مقصودة: التوليد التلقائي اقترح أيضًا تغييرات أنواع على جداول
> قائمة (`ai_usage` / `config_audit` / `user_ai_settings`) — وهي **فروق
> تمثيل** لا فروق سلوك (‏`INTEGER` مقابل `BIGINT` على SQLite). رفضتُها:
> تغيير أنواع أعمدة فيها بيانات بلا سبب وظيفي خطر حقيقي (وفقد عمود
> `created_at` ليس مطلوبًا لأي شيء). الترحيل يفعل المطلوب فقط.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from bot.database import CREATE_FTS_PG, CREATE_FTS_PG_INDEX, CREATE_FTS_SQLITE

revision = "f88640426147"
down_revision = "b7d2e4f91c38"
branch_labels = None
depends_on = None


def _create_learner_tables() -> None:
    op.create_table(
        "learner_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False),
        sa.Column("explain_style", sa.String(length=20), nullable=False),
        sa.Column("length_pref", sa.String(length=20), nullable=False),
        sa.Column("uses_examples", sa.Boolean(), nullable=False),
        sa.Column("uses_analogies", sa.Boolean(), nullable=False),
        sa.Column("prefers_bullets", sa.Boolean(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("difficulty_pref", sa.String(length=10), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("quizzes_taken", sa.Integer(), nullable=False),
        sa.Column("quizzes_passed", sa.Integer(), nullable=False),
        sa.Column("correct_answers", sa.Integer(), nullable=False),
        sa.Column("total_answers", sa.Integer(), nullable=False),
        sa.Column("streak_days", sa.Integer(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accuracy_by_difficulty", sa.Text(), nullable=False),
        sa.Column("weak_topics", sa.Text(), nullable=False),
        sa.Column("survey_done", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("learner_profiles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_learner_profiles_user_id"),
                              ["user_id"], unique=True)

    op.create_table(
        "learning_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("topic", sa.String(length=120), nullable=True),
        sa.Column("difficulty", sa.String(length=10), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("learning_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_learning_events_created_at"),
                              ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_learning_events_kind"),
                              ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_learning_events_user_id"),
                              ["user_id"], unique=False)


def _create_fts(bind) -> None:
    """ينشئ جدول البحث بأنواعه — SQLite (FTS5) أو PostgreSQL (tsvector)."""
    if bind.dialect.name == "sqlite":
        bind.execute(sa.text(CREATE_FTS_SQLITE))
    else:
        bind.execute(sa.text(CREATE_FTS_PG))
        bind.execute(sa.text(CREATE_FTS_PG_INDEX))


def upgrade() -> None:
    _create_learner_tables()
    _create_fts(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.execute(sa.text("DROP TABLE IF EXISTS files_fts"))
    else:
        bind.execute(sa.text("DROP TABLE IF EXISTS files_fts"))
    with op.batch_alter_table("learning_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_learning_events_user_id"))
        batch_op.drop_index(batch_op.f("ix_learning_events_kind"))
        batch_op.drop_index(batch_op.f("ix_learning_events_created_at"))
    op.drop_table("learning_events")
    with op.batch_alter_table("learner_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_learner_profiles_user_id"))
    op.drop_table("learner_profiles")
