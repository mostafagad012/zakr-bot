"""Alembic: بيئة الترحيل.

ملاحظة: Alembic يعمل بمحرك متزامن، لذلك نحوّل `sqlite+aiosqlite` إلى `sqlite`
و `postgresql+asyncpg` إلى `postgresql` تلقائيًا.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bot.config import settings
from bot.models import Base

def sync_url(url: str) -> str:
    return (
        url.replace("sqlite+aiosqlite://", "sqlite://")
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql+psycopg://", "postgresql+psycopg2://")
    )

# ⚠️ عِلّة حقيقية: `alembic upgrade head` كان يفشل بـ
#    `sqlite3.OperationalError: unable to open database file` على نسخة نظيفة،
#    لأن SQLite **لا يُنشئ المجلد الأب** — والبوت وحده هو من ينشئ `data/`
#    عند إقلاعه. من يشغّل الترحيل أولًا (أو على قاعدة جديدة) يفشل بلا سبب
#    واضح. نُنشئ المجلد هنا قبل أي اتصال.
def _ensure_parent_dir(url: str) -> None:
    """ينشئ مجلد قاعدة SQLite إن لم يكن موجودًا."""
    from pathlib import Path

    if not url.startswith("sqlite"):
        return
    path = url.split("///", 1)[-1]
    if not path or path == ":memory:":
        return
    try:
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass          # نترك Alembic يُبلّغ عن السبب الحقيقي

def _check_sync_driver(url: str) -> None:
    """رسالة عربية واضحة بدل `ModuleNotFoundError: psycopg2` عند الترقية.

    الترحيل يحتاج مشغّلًا **متزامنًا** (`psycopg2`)، والبوت يعتمد غير
    المتزامن (`asyncpg`). من يتبع دليل الترقية حرفيًا
    (`pip install asyncpg` ثم `alembic upgrade head`) كان يرى:

        ModuleNotFoundError: No module named 'psycopg2'

    بلا أي إشارة إلى الحل. نُبلّغ بالحلّ مباشرة.
    """
    if not url.startswith("postgresql"):
        return
    try:
        import importlib.util

        if importlib.util.find_spec("psycopg2") is not None:
            return
    except (ImportError, ValueError):
        return
    import sys

    sys.stderr.write(
        "\n✖ الترحيل على PostgreSQL يحتاج المشغّل المتزامن psycopg2.\n"
        "   البوت نفسه يستخدم asyncpg، لكن Alembic يعمل بمحرك متزامن.\n"
        "   الحل:  pip install 'psycopg2-binary>=2.9'\n"
        "   أو:    pip install -r requirements-migrate.txt\n\n"
    )
    raise SystemExit(1)


_sync_url = sync_url(settings.database_url)
_ensure_parent_dir(_sync_url)
_check_sync_driver(_sync_url)

config = context.config
config.set_main_option("sqlalchemy.url", _sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def _stamp_bot_created_schema(connection) -> None:
    """يختم قاعدة أنشأها البوت بـ`create_all` بدلًا من محاولة إعادة بنائها.

    البوت ينشئ الجداول كلها عند أول إقلاع. من يشغّل بعدها
    `alembic upgrade head` (وهو المسار الموثّق للترقية) كان يفشل بـ
    `table academic_years already exists` عند أول نسخة، فيظنّ أن قاعدته
    تالفة. لا يمكن التمييز إلا بشرط دقيق: **كل** جداول النماذج موجودة
    وبلا أي صفّ إصدار. فإن نقص جدول واحد فالقاعدة مرّت بترحيل جزئي
    ونترك Alembic يكمل عمله العادي (وهذا ما كسرته محاولة سابقة كانت
    تكتفي بوجود جدول `files` فختمت خطأً قاعدةً لم تكتمل).
    """
    from sqlalchemy import inspect

    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables:
        row = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).first()
        if row:                      # مختومة فعلًا — لا شيء نفعله
            return
    if not set(target_metadata.tables).issubset(tables):
        return                       # قاعدة مرّت بترحيل جزئي أو جديدة

    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(config).get_heads()
    if not heads:
        return
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    connection.exec_driver_sql("DELETE FROM alembic_version")
    connection.exec_driver_sql(
        "INSERT INTO alembic_version (version_num) VALUES (?)", (heads[0],)
    )
    connection.commit()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _stamp_bot_created_schema(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # مطلوب لتعديلات SQLite
            # SQLite يُنفّذ commit ضمنيًا مع كل DDL، فينتهي آخر أمر DDL في
            # الترحيل بمعاملة جديدة يكتب فيها Alembic رقم الإصدار ثم لا
            # يُثبّتها، فتظهر القاعدة كأنها **غير مرقّمة** وتُعاد الترقية من
            # الصفر في اليوم التالي فتفشل بـ «table already exists».
            # المعاملة لكل ترحيل + commit صريح في النهاية يُثبّت الأرقام.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()   # تثبيت رقم الإصدار الأخير بعد آخر DDL

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
