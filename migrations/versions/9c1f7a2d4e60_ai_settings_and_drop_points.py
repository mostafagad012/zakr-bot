"""إزالة نظام النقاط + جدول إعدادات الذكاء الاصطناعي للمستخدم

Revision ID: 9c1f7a2d4e60
Revises: 4be55abd8517
Create Date: 2026-11-08

ما تفعله هذه الهجرة:

1. تحذف آثار نظام النقاط بالكامل (قرار المستخدم: «شيل نظام النقاط نهائيًا»):
   `users.points`، `quizzes.reward_points`، `quiz_attempts.points_awarded`،
   وجدول `points_ledger` كاملًا.
2. تُنشئ `user_ai_settings` — إعداد الذكاء الاصطناعي الخاص بكل مستخدم
   («AI على مزاجه»): المزوّد المختار، الموديل، المفاتيح المشفّرة، الملاحظات.

كل العمليات محميّة بفحص وجود الجدول/العمود، فالهجرة آمنة على قاعدة
بيانات أُنشئت من `ensure_schema()` مباشرة (بلا سجل هجرات).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9c1f7a2d4e60"
down_revision = "4be55abd8517"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    inspector = _inspector()
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    tables = _tables()

    # ---------------------------------------------------- 1) نظام النقاط
    with op.batch_alter_table("users") as batch:
        if "points" in _columns("users"):
            batch.drop_column("points")

    with op.batch_alter_table("quizzes") as batch:
        if "reward_points" in _columns("quizzes"):
            batch.drop_column("reward_points")

    with op.batch_alter_table("quiz_attempts") as batch:
        if "points_awarded" in _columns("quiz_attempts"):
            batch.drop_column("points_awarded")

    if "points_ledger" in tables:
        # الفهارس تُحذف مع الجدول في SQLite/PostgreSQL
        op.drop_table("points_ledger")

    # --------------------------------------- 2) إعدادات الذكاء الاصطناعي
    if "user_ai_settings" not in tables:
        op.create_table(
            "user_ai_settings",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False,
                      server_default=""),
            sa.Column("model", sa.String(length=120), nullable=False,
                      server_default=""),
            # المفاتيح مشفّرة (XOR+HMAC) قبل التخزين — لا نص صريح في القاعدة
            sa.Column("keys_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("auto_provider", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_user_ai_settings_user_id", "user_ai_settings",
                        ["user_id"], unique=True)


def downgrade() -> None:
    """التراجع يُعيد النظام القديم — مفيد فقط لبيئة تطوير."""
    if "user_ai_settings" in _tables():
        op.drop_index("ix_user_ai_settings_user_id", table_name="user_ai_settings")
        op.drop_table("user_ai_settings")

    if "points" not in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("points", sa.Integer(), nullable=False,
                                       server_default="0"))

    if "reward_points" not in _columns("quizzes"):
        with op.batch_alter_table("quizzes") as batch:
            batch.add_column(sa.Column("reward_points", sa.Integer(),
                                       nullable=False, server_default="10"))

    if "points_awarded" not in _columns("quiz_attempts"):
        with op.batch_alter_table("quiz_attempts") as batch:
            batch.add_column(sa.Column("points_awarded", sa.Integer(),
                                       nullable=False, server_default="0"))

    # ── إعادة جدول سجلّ النقاط كما كان في الترحيل الأول ──────────────
    # كان ناقصًا هنا: الـupgrade يُسقط الجدول (وفهارسه تُسقط معه)، لكن
    # الـdowngrade لم يُعِد سوى الأعمدة. النتيجة: التراجع ينجح ظاهريًا
    # ثم يفشل الترحيل الأول عند `DROP INDEX ix_points_user_time`
    # (لا يوجد فهرس). المُصحَّح: نُعيد الجدول **بفهارسه الثلاثة**.
    if "points_ledger" not in _tables():
        op.create_table(
            "points_ledger",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=False),
            sa.Column("delta", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=30), nullable=False),
            sa.Column("file_id", sa.Integer(), nullable=True),
            sa.Column("note", sa.String(length=240), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("points_ledger") as batch:
            batch.create_index("ix_points_ledger_reason", ["reason"], unique=False)
            batch.create_index("ix_points_ledger_user_id", ["user_id"], unique=False)
            batch.create_index("ix_points_user_time", ["user_id", "created_at"],
                               unique=False)
