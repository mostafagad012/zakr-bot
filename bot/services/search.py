"""البحث في المكتبة.

الطبقة الأولى: FTS5 (trigram) على عنوان/وصف/وسوم/نص الملف المستخرج.
الطبقة الثانية (احتياطية): LIKE لو أعاد FTS صفر نتائج أو كان المصطلح قصيرًا.

على PostgreSQL تُستبدل دالة build_query بـ to_tsquery + plainto_tsquery
ونفس توقيع الدوال بدون تغيير أي كود في المتحكّمات.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import FileItem, FileStatus

log = logging.getLogger(__name__)

STOPWORDS = {
    "في", "من", "على", "عن", "الى", "إلى", "و", "او", "أو", "the", "a", "an",
    "of", "and", "or", "for", "is",
}


@dataclass(frozen=True)
class SearchHit:
    file: FileItem
    score: float
    snippet: str = ""


def normalize_terms(query: str) -> list[str]:
    """تفتيت الاستعلام إلى مصطلحات نظيفة."""
    if not query:
        return []
    cleaned = re.sub(r"[\u064B-\u0652\u0670]", "", query)  # إزالة التشكيل
    parts = [p.strip() for p in re.split(r"[\s,،]+", cleaned) if p.strip()]
    out: list[str] = []
    for p in parts:
        low = p.lower().strip('"')
        if not low or low in STOPWORDS:
            continue
        # ملاحظة: لا نقطع الحروف الملتصقة (و/ف/ب) لأن ذلك يكسر أسماء مثل "فيثاغورس".
        # البحث trigram يغطي المطابقة الجزئية أصلًا.
        if low not in out:
            out.append(low)
    return out


def build_match_query(terms: list[str]) -> str | None:
    """استعلام FTS5 آمن (كل مصطلح بين علامتي اقتباس)."""
    if not terms:
        return None
    safe = []
    for t in terms:
        t = t.replace('"', "")
        if not t:
            continue
        safe.append(f'"{t}"')
    return " AND ".join(safe) if safe else None


def _fts_query(dialect_name: str | None, terms: list[str]) -> tuple[str, dict] | None:
    """يبني استعلام FTS حسب المحرك. None = لا مصطلحات صالحة.

    ⚠️ صيغة `MATCH` خاصة بـ SQLite FTS5 وحدها؛ على PostgreSQL كانت
    ترفع ProgrammingError (لا OperationalError) فينهار البحث كله
    بدل التراجع لـ LIKE — هناك نستخدم tsvector بـ plainto_tsquery،
    وعمود المعرّف file_id لا rowid (انظر CREATE_FTS_PG).
    """
    if not terms:
        return None
    if not dialect_name or dialect_name == "sqlite":
        match = build_match_query(terms)
        if match is None:
            return None
        sql = (
            "SELECT rowid AS file_id, title, content, bm25(search_index) AS rank "
            "FROM search_index WHERE search_index MATCH :match "
            "ORDER BY rank ASC LIMIT :limit"
        )
        return sql, {"match": match}
    sql = (
        "SELECT file_id, title, content, "
        "ts_rank(tsv, plainto_tsquery('simple', :q)) AS rank "
        "FROM search_index WHERE tsv @@ plainto_tsquery('simple', :q) "
        "ORDER BY rank DESC LIMIT :limit"
    )
    return sql, {"q": " ".join(terms)}


def _snippet(content: str, term: str, width: int = 140) -> str:
    if not content:
        return ""
    idx = content.lower().find(term.lower())
    if idx == -1:
        return content[:width].strip()
    start = max(0, idx - width // 3)
    return ("…" if start > 0 else "") + content[start : start + width].strip() + "…"


async def search_files(
    session: AsyncSession,
    query: str,
    *,
    subject_id: int | None = None,
    category_id: int | None = None,
    only_approved: bool = True,
    limit: int = 15,
) -> list[SearchHit]:
    terms = normalize_terms(query)
    if not terms:
        return []

    hits = await _fts_search(session, terms, subject_id, category_id, only_approved, limit)
    if not hits:
        hits = await _like_search(session, terms, subject_id, category_id, only_approved, limit)
    return hits[:limit]


async def hybrid_search(
    session: AsyncSession,
    query: str,
    *,
    subject_id: int | None = None,
    category_id: int | None = None,
    only_approved: bool = True,
    limit: int = 15,
) -> list[SearchHit]:
    """دمج البحث النصي والدلالي بطريقة Reciprocal Rank Fusion.

    يجد الملفات المطابقة حرفيًا **و** الملفات القريبة في المعنى
    (مثال: البحث بـ«معدل التغير» يجد ملفًا عنوانه «المشتقات»).
    """
    from . import semantic

    exact = await search_files(
        session, query, subject_id=subject_id, category_id=category_id,
        only_approved=only_approved, limit=limit * 2,
    )
    semantic_hits = await semantic.rank(session, query, limit=limit * 2)

    fused: dict[int, tuple[float, SearchHit]] = {}
    for rank_position, hit in enumerate(exact):
        fused[hit.file.id] = (1.0 / (60 + rank_position), hit)

    if semantic_hits:
        files = await _load_files(
            session,
            [h.file_id for h in semantic_hits],
            subject_id,
            category_id,
            only_approved,
        )
        by_id = {f.id: f for f in files}
        for rank_position, hit in enumerate(semantic_hits):
            file_item = by_id.get(hit.file_id)
            if file_item is None:
                continue
            score = 1.0 / (60 + rank_position)
            entry = fused.get(file_item.id)
            new_hit = SearchHit(
                file=file_item,
                score=(entry[0] if entry else 0.0) + score,
                snippet=entry[1].snippet if entry else _snippet("", ""),
            )
            fused[file_item.id] = ((entry[0] if entry else 0.0) + score, new_hit)

    ordered = sorted(fused.values(), key=lambda pair: pair[0], reverse=True)
    return [hit for _score, hit in ordered[:limit]]


async def _fts_search(
    session: AsyncSession,
    terms: list[str],
    subject_id: int | None,
    category_id: int | None,
    only_approved: bool,
    limit: int,
) -> list[SearchHit]:
    dialect = session.bind.dialect.name if session.bind is not None else None
    built = _fts_query(dialect, terms)
    if built is None:
        return []
    sql, params = built
    params["limit"] = max(limit * 3, limit)
    try:
        rows = (
            await session.execute(text(sql), params)
        ).mappings().all()
    except SQLAlchemyError as exc:
        # أي فشل في طبقة FTS (صيغة/محرك) — نترك طبقة LIKE تتكفّل
        log.warning("فشل استعلام البحث النصي: %s", exc)
        return []

    ids = [int(r["file_id"]) for r in rows]
    if not ids:
        return []

    # اتجاه الدرجة يختلف: bm25 في SQLite «الأقل أفضل» و ts_rank «الأعلى أفضل»
    sign = 1.0 if dialect and dialect != "sqlite" else -1.0
    rank_by_id = {int(r["file_id"]): sign * float(r["rank"] or 0) for r in rows}
    content_by_id = {int(r["file_id"]): (r["content"] or "") for r in rows}

    files = await _load_files(session, ids, subject_id, category_id, only_approved)
    hits = [
        SearchHit(file=f, score=rank_by_id.get(f.id, 0.0),
                  snippet=_snippet(content_by_id.get(f.id, ""), terms[0]))
        for f in files
    ]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


async def _like_search(
    session: AsyncSession,
    terms: list[str],
    subject_id: int | None,
    category_id: int | None,
    only_approved: bool,
    limit: int,
) -> list[SearchHit]:
    stmt = select(FileItem)
    conds = [or_(*[FileItem.title.ilike(f"%{t}%") for t in terms])]
    conds.append(or_(*[FileItem.description.ilike(f"%{t}%") for t in terms]))
    conds.append(or_(*[FileItem.tags.ilike(f"%{t}%") for t in terms]))
    stmt = stmt.where(or_(*conds))
    files = await _load_files_stmt(session, stmt, subject_id, category_id, only_approved, limit)
    return [SearchHit(file=f, score=1.0, snippet=_snippet(f.description or f.title, terms[0])) for f in files]


def _base_conditions(subject_id, category_id, only_approved):
    conds = []
    if subject_id is not None:
        conds.append(FileItem.subject_id == subject_id)
    if category_id is not None:
        conds.append(FileItem.category_id == category_id)
    if only_approved:
        conds.append(FileItem.status == FileStatus.APPROVED.value)
    else:
        conds.append(FileItem.status != FileStatus.DELETED.value)
    return conds


async def _load_files(
    session: AsyncSession,
    ids: list[int],
    subject_id: int | None,
    category_id: int | None,
    only_approved: bool,
) -> list[FileItem]:
    stmt = select(FileItem).where(FileItem.id.in_(ids)).where(*_base_conditions(subject_id, category_id, only_approved))
    return list(await session.scalars(stmt))


async def _load_files_stmt(
    session: AsyncSession,
    stmt,
    subject_id: int | None,
    category_id: int | None,
    only_approved: bool,
    limit: int,
) -> list[FileItem]:
    stmt = stmt.where(*_base_conditions(subject_id, category_id, only_approved))
    stmt = stmt.order_by(FileItem.is_pinned.desc(), FileItem.downloads_count.desc(), FileItem.created_at.desc())
    return list(await session.scalars(stmt.limit(limit)))


async def recent_files(session: AsyncSession, limit: int = 10, subject_id: int | None = None) -> list[FileItem]:
    stmt = (
        select(FileItem)
        .where(FileItem.status == FileStatus.APPROVED.value)
        .order_by(FileItem.created_at.desc())
        .limit(limit)
    )
    if subject_id is not None:
        stmt = select(FileItem).where(
            FileItem.status == FileStatus.APPROVED.value, FileItem.subject_id == subject_id
        ).order_by(FileItem.created_at.desc()).limit(limit)
    return list(await session.scalars(stmt))

