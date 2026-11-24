"""جدولا استهلاك الذكاء الاصطناعي وسجل تدقيق الإعداد

Revision ID: b7d2e4f91c38
Revises: 9c1f7a2d4e60
Create Date: 2026-11-08

`ai_usage`: سطر لكل طلب ذكاء اصطناعي — أساس السقف اليومي ولوحة الأدمن.
`config_audit`: من غيّر `.env` وماذا تغيّر (بلا أي قيمة سرية).

الهجرة آمنة على قاعدة أُنشئت بـ`ensure_schema()` مباشرة: كل عملية محميّة
بفحص وجود الجدول.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d2e4f91c38"
down_revision = "9c1f7a2d4e60"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()

    if "ai_usage" not in tables:
        op.create_table(
            "ai_usage",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=True),
            sa.Column("provider", sa.String(length=30), nullable=False,
                      server_default=""),
            sa.Column("kind", sa.String(length=20), nullable=False,
                      server_default=""),
            sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
            # لا نُخزّن نص الطلب ولا الرد — تكلفة وخصوصية
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])
        op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"])

    if "config_audit" not in tables:
        op.create_table(
            "config_audit",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("actor_id", sa.BigInteger(), nullable=True),
            sa.Column("actor_name", sa.String(length=120), nullable=False,
                      server_default=""),
            sa.Column("key", sa.String(length=60), nullable=False, server_default=""),
            sa.Column("old_value", sa.String(length=120), nullable=False,
                      server_default=""),
            sa.Column("new_value", sa.String(length=120), nullable=False,
                      server_default=""),
            sa.Column("outcome", sa.String(length=20), nullable=False,
                      server_default="ok"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_config_audit_actor_id", "config_audit", ["actor_id"])
        op.create_index("ix_config_audit_key", "config_audit", ["key"])
        op.create_index("ix_config_audit_created_at", "config_audit", ["created_at"])


def downgrade() -> None:
    tables = _tables()
    for name in ("config_audit", "ai_usage"):
        if name in tables:
            op.drop_table(name)
