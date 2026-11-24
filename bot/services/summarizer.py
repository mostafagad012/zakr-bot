"""تلخيص استخراجي (Extractive / TextRank مبسّط) — يعمل دون API خارجي أو مفاتيح.

المنهج:
1. تقسيم النص لجمل (يدعم العربية والإنجليزية).
2. تسجيل كل جملة = تكرار كلماتها المفتاحية + مكافأة موضع مبكر + عقاب طول غير مناسب.
3. إرجاع أعلى الجمل ترتيبًا مع الحفاظ على تسلسلها الأصلي.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

SENTENCE_SPLIT = re.compile(r"(?<=[.!؟?\n])\s+|(?<=\n)")
WORD_RE = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)

STOPWORDS_AR = {
    "في", "من", "على", "علي", "عن", "إلى", "الى", "أن", "ان", "إن", "أنه", "هذا", "هذه", "ذلك",
    "التي", "الذي", "الذين", "ما", "لا", "لم", "لن", "قد", "هو", "هي", "هم", "كان", "كانت",
    "بين", "عند", "بعد", "قبل", "مع", "أو", "او", "و", "ثم", "كل", "بعض", "غير", "حتى",
    "كما", "حيث", "لكن", "بل", "إذا", "اذا", "أي", "اي", "به", "بها", "له", "لها", "ولا",
    "فقط", "أيضا", "ايضا", "هنا", "هناك", "هكذا", "تلك", "أما", "اما",
}
STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was",
    "were", "be", "been", "with", "as", "at", "by", "from", "this", "that", "these",
    "those", "it", "its", "we", "you", "they", "he", "she", "not", "but", "if", "then",
}
STOPWORDS = STOPWORDS_AR | STOPWORDS_EN


@dataclass(frozen=True)
class Summary:
    text: str
    sentences_used: int
    total_sentences: int
    ratio: float


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    raw = SENTENCE_SPLIT.split(text.replace("\r", ""))
    out: list[str] = []
    for chunk in raw:
        for line in (chunk or "").split("\n"):
            s = re.sub(r"\s+", " ", line).strip(" \t-•·*\u200f\u200e")
            if len(s) >= 25:  # تجاهل العناوين القصيرة وبقايا التقطيع
                out.append(s)
    return out


ARABIC_CHAR_MAP = str.maketrans(
    {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",  # الهمزات على الألف
        "ى": "ي",                                 # الألف المقصورة
        "ؤ": "و",                                 # الهمزة على الواو
        "ئ": "ي",                                 # الهمزة على الياء
        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    }
)

DIACRITICS_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def normalize_word(word: str) -> str:
    """تطبيع صرفي خفيف للعربية يوحّد صيغ الكلمة الواحدة.

    1) توحيد الحروف (همزات، ألف مقصورة، أرقام هندية)
    2) إزالة التشكيل والتطويل
    3) إزالة «ال» التعريف
    4) إزالة تاء التأنيث وجمع المؤنث السالم

    مقصود أن يكون محافظًا — لا يُجري إعرابًا كاملًا، وأي قاعدة
    تُنتج كلمة أقصر من 3 أحرف تُلغى ويُستخدم الأصل.
    """
    base = DIACRITICS_RE.sub("", word.lower()).translate(ARABIC_CHAR_MAP)
    w = base

    while w.startswith("ال") and len(w) > 4:
        w = w[2:]

    # تاء التأنيث: «أسئلة» و«اسئلة» → «اسئل»
    if w.endswith("ة") and len(w) > 4:
        w = w[:-1]
    # جمع المؤنث السالم: «امتحانات» → «امتحان»
    elif w.endswith("ات") and len(w) > 5:
        w = w[:-2]

    return w if len(w) >= 3 else base


def tokenize(text: str, *, normalize: bool = True) -> list[str]:
    words = [w.lower() for w in WORD_RE.findall(text or "")]
    if not normalize:
        return words
    return [normalize_word(w) for w in words]


def keywords(text: str, top: int = 40) -> dict[str, float]:
    words = [w for w in tokenize(text) if w not in STOPWORDS and len(w) > 2]
    if not words:
        return {}
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    max_freq = max(freq.values())
    scored = {w: c / max_freq for w, c in freq.items()}
    return dict(sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:top])


def summarize(
    text: str,
    max_sentences: int = 7,
    max_chars: int = 1800,
    min_words: int = 4,
) -> Summary:
    sentences = split_sentences(text)
    total = len(sentences)
    if total == 0:
        return Summary("", 0, 0, 0.0)
    if total <= max_sentences:
        joined = "\n".join(f"• {s}" for s in sentences)[:max_chars]
        return Summary(joined, total, total, 1.0)

    kw = keywords(text)
    if not kw:
        picked = sentences[:max_sentences]
        return Summary("\n".join(f"• {s}" for s in picked)[:max_chars], len(picked), total,
                       len(picked) / total)

    scored: list[tuple[float, int, str]] = []
    for i, s in enumerate(sentences):
        words = [w for w in tokenize(s) if w not in STOPWORDS and len(w) > 2]
        if len(words) < min_words:
            continue
        coverage = sum(kw.get(w, 0.0) for w in words) / math.sqrt(len(words))
        position_bonus = 1.0 + (0.25 if i < 3 else 0.0) - (0.15 if i > total * 0.85 else 0.0)
        length_penalty = 1.0
        if len(words) > 60:
            length_penalty = 0.8
        elif len(words) < 6:
            length_penalty = 0.6
        scored.append((coverage * position_bonus * length_penalty, i, s))

    scored.sort(key=lambda t: t[0], reverse=True)
    chosen = sorted(scored[:max_sentences], key=lambda t: t[1])
    bullets = [s for _, _, s in chosen]
    text_out = "\n".join(f"• {b}" for b in bullets)[:max_chars]
    return Summary(
        text=text_out,
        sentences_used=len(bullets),
        total_sentences=total,
        ratio=len(bullets) / total,
    )
