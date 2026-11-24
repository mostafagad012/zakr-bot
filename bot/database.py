"""إعداد محرك قاعدة البيانات والجلسات (async).

يدعم SQLite الآن، وأي PostgreSQL (مثل Supabase) بمجرد تغيير DATABASE_URL.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base

log = logging.getLogger(__name__)

FTS_TABLE = "search_index"

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

CREATE_FTS_SQLITE = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    title, description, tags, content,
    tokenize = 'trigram'
)
"""

# PostgreSQL/Supabase: نفس الواجهة، تنفيذ مختلف (tsvector + GIN)
CREATE_FTS_PG = f"""
CREATE TABLE IF NOT EXISTS {FTS_TABLE} (
    file_id BIGINT PRIMARY KEY,
    title TEXT, description TEXT, tags TEXT, content TEXT,
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(title,'') || ' ' || coalesce(description,'') || ' ' ||
            coalesce(tags,'') || ' ' || coalesce(content,''))
    ) STORED
)
"""

CREATE_FTS_PG_INDEX = f"CREATE INDEX IF NOT EXISTS ix_{FTS_TABLE}_tsv ON {FTS_TABLE} USING GIN (tsv)"


def init_engine(database_url: str) -> AsyncEngine:
    """ينشئ المحرك العالمي. يُنادى مرة واحدة عند الإقلاع."""
    global _engine, _session_factory

    if database_url.startswith("sqlite"):
        db_path = database_url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(
            database_url,
            echo=False,
            future=True,
            poolclass=StaticPool if db_path == ":memory:" else None,
        )

        # إعدادات كل اتصال SQLite. ⚠️ تنفيذ PRAGMA داخل معاملة إنشاء
        # الجداول (كما كان) لا يفعّل journal_mode أصلًا، و foreign_keys
        # إعداد لكل اتصال — من هنا تُطبَّق على كل اتصال جديد فعليًا.
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    else:
        engine = create_async_engine(database_url, echo=False, future=True, pool_pre_ping=True)

    _engine = engine
    _session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("init_engine() لم يُستدعَ بعد")
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("init_engine() لم يُستدعَ بعد")
    return _session_factory


def get_session() -> AsyncSession:
    """يرجّع جلسة تُستخدم كـ context manager: `async with get_session() as s:`"""
    return session_factory()()


async def dispose_engine() -> None:
    """يغلق الاتصالات عند التوقف.

    **علة حقيقية:** المحرك كان يُنشأ عند الإقلاع ولا يُغلق أبدًا، فالاتصالات
    (وبالأخص على PostgreSQL/Supabase) تبقى معلّقة حتى يقرّر الخادم قطعها —
    فتُستهلك حصة الاتصالات مع كل إعادة تشغيل. الإغلاق الصريح يجعل التوقف
    نظيفًا ويُفرج عن الاتصالات فورًا.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        log.info("قاعدة البيانات: أُغلقت الاتصالات")
    _engine = None
    _session_factory = None


def is_sqlite(engine: AsyncEngine | None = None) -> bool:
    eng = engine or get_engine()
    return eng.dialect.name == "sqlite"


async def ensure_schema(engine: AsyncEngine | None = None) -> None:
    """ينشئ الجداول + جدول البحث النصي (FTS) + البيانات الافتراضية."""
    from .seed import seed_defaults

    eng = engine or get_engine()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if eng.dialect.name == "sqlite":
            await conn.execute(text(CREATE_FTS_SQLITE))
        else:
            await conn.execute(text(CREATE_FTS_PG))
            await conn.execute(text(CREATE_FTS_PG_INDEX))

    await seed_defaults()
