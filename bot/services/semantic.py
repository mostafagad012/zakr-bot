"""بحث دلالي محلي (TF-IDF + جيب التمام) — يعمل على SQLite و PostgreSQL بلا إضافات.

الفكرة: لكل ملف متجه تكرار كلمات (مخزَّن JSON)، وعند البحث نحسب تشابه جيب التمام
بين متجه الاستعلام ومتجهات الملفات، فنجد ملفات «عن نفس الموضوع» حتى لو
لم تحتوي كلمة البحث حرفيًا.

**الترقية إلى pgvector** (على PostgreSQL فقط) موثّقة في docs/OPERATIONS.md:
تُستبدل دالة rank() باستعلام `ORDER BY embedding <=> :query` دون تغيير أي نداء خارجي.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings as embeddings_service
from .summarizer import STOPWORDS, tokenize

log = logging.getLogger(__name__)

VECTORS_TABLE = "file_vectors"
MAX_TERMS_PER_VECTOR = 200

@dataclass(frozen=True)
class SemanticHit:
    file_id: int
    score: float


def build_term_vector(*texts: str) -> tuple[dict[str, int], int]:
    """يبني متجه تكرار الكلمات من نص أو أكثر."""
    counts: dict[str, int] = {}
    total = 0
    for chunk in texts:
        for word in tokenize(chunk or ""):
            if word in STOPWORDS or len(word) < 3:
                continue
            counts[word] = counts.get(word, 0) + 1
            total += 1

    if not counts:
        return {}, 0

    # نبقي أهم الكلمات فقط حتى لا ينتفخ حجم الجدول
    if len(counts) > MAX_TERMS_PER_VECTOR:
        counts = dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:MAX_TERMS_PER_VECTOR])
    return counts, total


def vector_norm(counts: dict[str, int]) -> float:
    return math.sqrt(sum(v * v for v in counts.values())) or 0.0


async def index(session: AsyncSession, file_id: int, *texts: str) -> bool:
    """يحسب ويحفظ متجه TF-IDF + التضمين العصبي إن كان مفعّلًا.

    التضمين يعمل في thread منفصل لأن النموذج العصبي يحجب الخيط.
    """
    counts, total = build_term_vector(*texts)
    if not counts:
        await session.execute(
            text(f"DELETE FROM {VECTORS_TABLE} WHERE file_id = :fid"), {"fid": file_id}
        )
        return False

    combined = " ".join(t for t in texts if t)
    model_name = embeddings_service.get_provider()
    vector = None
    if model_name:
        vector = await asyncio.to_thread(embeddings_service.embed, combined)

    await session.execute(
        text(f"DELETE FROM {VECTORS_TABLE} WHERE file_id = :fid"), {"fid": file_id}
    )
    await session.execute(
        text(
            f"INSERT INTO {VECTORS_TABLE} (file_id, terms, norm, tokens, embedding, embedding_model) "
            "VALUES (:fid, :terms, :norm, :tokens, :embedding, :model)"
        ),
        {
            "fid": file_id,
            "terms": json.dumps(counts, ensure_ascii=False),
            "norm": vector_norm(counts),
            "tokens": total,
            "embedding": json.dumps(vector) if vector else None,
            "model": model_name if vector else None,
        },
    )
    return True


async def unindex(session: AsyncSession, file_id: int) -> None:
    await session.execute(text(f"DELETE FROM {VECTORS_TABLE} WHERE file_id = :fid"), {"fid": file_id})


async def _load_all(session: AsyncSession) -> list[tuple[int, dict[str, int], float]]:
    rows = (
        await session.execute(text(f"SELECT file_id, terms, norm FROM {VECTORS_TABLE}"))
    ).all()
    out = []
    for file_id, terms_json, norm in rows:
        try:
            out.append((int(file_id), json.loads(terms_json), float(norm)))
        except (ValueError, TypeError):
            continue
    return out


async def tfidf_rank(session: AsyncSession, query: str, limit: int = 10, min_score: float = 0.05) -> list[SemanticHit]:
    """يرتّب الملفات حسب تشابه جيب التمام مع الاستعلام (TF-IDF)."""
    query_counts, _ = build_term_vector(query)
    if not query_counts:
        return []

    corpus = await _load_all(session)
    if not corpus:
        return []

    # IDF عبر المجموعة الحالية
    doc_count = len(corpus)
    df: dict[str, int] = {}
    for _fid, counts, _norm in corpus:
        for term in query_counts:
            if term in counts:
                df[term] = df.get(term, 0) + 1

    def idf(term: str) -> float:
        return math.log((doc_count + 1) / (df.get(term, 0) + 1)) + 1.0

    query_vec = {term: (1 + math.log(count)) * idf(term) for term, count in query_counts.items()}
    query_norm = vector_norm(query_vec)
    if not query_norm:
        return []

    hits: list[SemanticHit] = []
    for file_id, counts, _norm in corpus:
        dot = 0.0
        doc_sq = 0.0
        for term, count in counts.items():
            weight = (1 + math.log(count)) * idf(term)
            doc_sq += weight * weight
            if term in query_vec:
                dot += weight * query_vec[term]
        if dot <= 0 or doc_sq <= 0:
            continue
        score = dot / (query_norm * math.sqrt(doc_sq))
        if score >= min_score:
            hits.append(SemanticHit(file_id=file_id, score=round(score, 4)))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


async def count(session: AsyncSession) -> int:
    return (await session.execute(text(f"SELECT COUNT(*) FROM {VECTORS_TABLE}"))).scalar() or 0


async def embed_rank(
    session: AsyncSession, query: str, limit: int = 10, min_score: float = 0.30
) -> list[SemanticHit]:
    """ترتيب بالتضمين العصبي. يرجع قائمة فارغة لو المزوّد أو المتجهات غير متاحة."""
    if not embeddings_service.is_available():
        return []

    query_vector = await asyncio.to_thread(embeddings_service.embed, query)
    if not query_vector:
        return []

    rows = (
        await session.execute(
            text(f"SELECT file_id, embedding FROM {VECTORS_TABLE} WHERE embedding IS NOT NULL")
        )
    ).all()
    if not rows:
        return []

    hits: list[SemanticHit] = []
    for file_id, raw in rows:
        try:
            vector = json.loads(raw)
        except (TypeError, ValueError):
            continue
        score = embeddings_service.cosine(query_vector, vector)
        if score >= min_score:
            hits.append(SemanticHit(file_id=int(file_id), score=round(score, 4)))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


async def rank(session: AsyncSession, query: str, limit: int = 10, min_score: float = 0.05) -> list[SemanticHit]:
    """الترتيب الدلالي: يستخدم التضمين العصبي إن وُجد، وإلا TF-IDF.

    التوقيع موحّد حتى لا يتأثر أي نداء خارجي بتفعيل المزوّد العصبي أو تعطيله.
    """
    neural = await embed_rank(session, query, limit=limit)
    if neural:
        return neural
    return await tfidf_rank(session, query, limit=limit, min_score=min_score)


async def embedding_coverage(session: AsyncSession) -> tuple[int, int]:
    """(عدد الملفات ذات التضمين العصبي، إجمالي الملفات المفهرسة)."""
    total = await count(session)
    with_embedding = (
        await session.execute(
            text(f"SELECT COUNT(*) FROM {VECTORS_TABLE} WHERE embedding IS NOT NULL")
        )
    ).scalar() or 0
    return int(with_embedding), int(total)
