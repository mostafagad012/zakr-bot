"""تضمين النصوص (Embeddings) — طبقة قابلة للتبديل.

المزوّدون المتاحون:
  1) `fastembed` — نماذج عصبية حقيقية متعددة اللغات (ONNX، يعمل دون GPU)
  2) `none`      — بلا تضمين، ويُستخدم TF-IDF في semantic.py

السبب في الطبقة: النموذج متعدد اللغات يحتاج ~2 جيجا رام حرة. على سيرفر صغير
يمكن تركه معطّلًا ويعمل البحث الدلالي بـ TF-IDF دون أي كسر.

التفعيل في .env:
    EMBEDDING_MODEL=minishlab/potion-multilingual-128M

على PostgreSQL يمكن التخزين في pgvector — انظر docs/OPERATIONS.md.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

MAX_CHARS = 1800  # حدود نافذة النموذج


@dataclass(frozen=True)
class EmbeddingStatus:
    available: bool
    provider: str
    model: str
    dim: int
    reason: str = ""


@lru_cache(maxsize=1)
def _load_model(model_name: str):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name)


def get_provider() -> str:
    from ..config import settings

    return (settings.embedding_model or "").strip()


def is_available() -> bool:
    return bool(get_provider())


def status() -> EmbeddingStatus:
    """يفحص توفر المزوّد فعلًا (يحمّل النموذج عند أول طلب)."""
    model_name = get_provider()
    if not model_name:
        return EmbeddingStatus(False, "none", "", 0, "EMBEDDING_MODEL غير مُعدّ — يُستخدم TF-IDF")

    try:
        model = _load_model(model_name)
        sample = next(iter(model.embed(["فحص"])))
        return EmbeddingStatus(True, "fastembed", model_name, len(sample))
    except ImportError as exc:
        return EmbeddingStatus(False, "none", model_name, 0, f"fastembed غير مثبّتة: {exc}")
    except Exception as exc:  # فشل تنزيل النموذج أو نقص ذاكرة
        return EmbeddingStatus(False, "none", model_name, 0, f"تعذّر تحميل النموذج: {exc}")


def embed(text: str) -> list[float] | None:
    """يحوّل نصًا إلى متجه مُطبع (L2). يرجع None لو المزوّد غير متاح."""
    model_name = get_provider()
    if not model_name or not text.strip():
        return None

    try:
        model = _load_model(model_name)
        vector = next(iter(model.embed([text[:MAX_CHARS]])))
    except Exception as exc:
        log.warning("فشل التضمين: %s", exc)
        return None

    return normalize([float(x) for x in vector])


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if not norm:
        return vector
    return [x / norm for x in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """تشابه جيب التمام — يفترض أن المتجهات مطبّعة، لكن يتحقق أمانًا."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)
