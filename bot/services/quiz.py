"""محرك الكويزات: إنشاء، توليد، وتصحيح."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Quiz, QuizAttempt, QuizDifficulty, QuizQuestion
from .summarizer import keywords, split_sentences, tokenize

OPTION_SEP = "||"


@dataclass(frozen=True)
class QuizResult:
    correct: int
    total: int
    passed: bool
    score_percent: int


async def create_quiz(
    session: AsyncSession,
    title: str,
    subject_id: int | None = None,
    difficulty: str = QuizDifficulty.MEDIUM.value,
    pass_percent: int = 60,
    created_by: int | None = None,
    questions: list[tuple[str, list[str], int, str | None]] | None = None,
) -> Quiz:
    quiz = Quiz(
        title=title,
        subject_id=subject_id,
        difficulty=difficulty,
        pass_percent=pass_percent,
        created_by=created_by,
    )
    session.add(quiz)
    await session.flush()
    for text, options, correct_index, explanation in questions or []:
        await add_question(session, quiz.id, text, options, correct_index, explanation)
    return quiz


async def add_question(
    session: AsyncSession,
    quiz_id: int,
    text: str,
    options: list[str],
    correct_index: int,
    explanation: str | None = None,
) -> QuizQuestion:
    if len(options) < 2:
        raise ValueError("يجب توفير خيارين على الأقل")
    if not 0 <= correct_index < len(options):
        raise ValueError("رقم الإجابة الصحيحة خارج نطاق الخيارات")
    question = QuizQuestion(
        quiz_id=quiz_id,
        text=text.strip(),
        options=OPTION_SEP.join(o.strip() for o in options),
        correct_index=correct_index,
        explanation=explanation,
    )
    session.add(question)
    await session.flush()
    return question


async def get_quiz(session: AsyncSession, quiz_id: int) -> Quiz | None:
    return await session.scalar(
        select(Quiz).options(selectinload(Quiz.questions)).where(Quiz.id == quiz_id)
    )


async def list_quizzes(
    session: AsyncSession, subject_id: int | None = None, only_active: bool = True
) -> list[Quiz]:
    stmt = select(Quiz).options(selectinload(Quiz.questions))
    if subject_id is not None:
        stmt = stmt.where(Quiz.subject_id == subject_id)
    if only_active:
        stmt = stmt.where(Quiz.is_active.is_(True))
    return list(await session.scalars(stmt.order_by(Quiz.created_at.desc())))


async def submit_attempt(
    session: AsyncSession,
    quiz: Quiz,
    user_id: int,
    answers: list[int | None],
) -> QuizResult:
    """يصحّح الإجابات ويسجّل المحاولة (بدون commit)."""
    # تحميل صريح للأسئلة: الوصول إلى علاقة lazy داخل async يرمي MissingGreenlet
    questions = sorted(
        await session.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz.id)),
        key=lambda q: q.id,
    )
    total = len(questions)
    correct = 0
    # strict=False مقصود: الإجابات قد تكون أقصر (طالب لم يجب على الكل)
    for question, answer in zip(questions, answers, strict=False):
        if answer is not None and int(answer) == question.correct_index:
            correct += 1

    score_percent = int(round(correct / total * 100)) if total else 0
    passed = score_percent >= quiz.pass_percent

    # لا نقاط مكافأة: النتيجة تُسجَّل للتحليل والمستوى (انظر learner.py)
    session.add(
        QuizAttempt(
            quiz_id=quiz.id,
            user_id=user_id,
            score=correct,
            total=total,
        )
    )
    await session.flush()
    return QuizResult(correct, total, passed, score_percent)


# ------------------------------------------------------------ التوليد التلقائي
def _distractors(word: str, pool: list[str], count: int = 3, rng: random.Random | None = None) -> list[str]:
    """يختار خيارات خاطئة قريبة في الطول من الإجابة الصحيحة.

    rng إلزامي عمليًا لضمان قابلية إعادة التوليد بنفس البذرة.
    """
    rand = rng or random
    pool = [w for w in pool if w and w.lower() != word.lower()]
    same_len = [w for w in pool if abs(len(w) - len(word)) <= 3]
    chosen = rand.sample(same_len, min(count, len(same_len))) if same_len else []
    if len(chosen) < count:
        extra = [w for w in pool if w not in chosen]
        if extra:
            chosen += rand.sample(extra, min(count - len(chosen), len(extra)))
    return chosen[:count]


def generate_from_text(text: str, count: int = 5, seed: int | None = None) -> list[dict]:
    """يولّد أسئلة "أكمل الفراغ" من نص (مثل نص PDF مستخرج).

    الناتج: [{"text": ..., "options": [...], "correct_index": n}, ...]
    """
    rng = random.Random(seed)
    sentences = [s for s in split_sentences(text) if 6 <= len(tokenize(s)) <= 40]
    if not sentences:
        return []

    kw_scores = keywords(text, top=60)
    kw = list(kw_scores.keys())
    if len(kw) < 4:
        return []

    pool = [w for w in kw if len(w) > 3]
    if len(pool) < 4:
        return []

    questions: list[dict] = []
    used_words: set[str] = set()
    rng.shuffle(sentences)

    for sentence in sentences:
        if len(questions) >= count:
            break
        words = [w for w in tokenize(sentence) if w in kw and w not in used_words and len(w) > 3]
        if not words:
            continue
        target = max(words, key=lambda w: kw_scores[w])
        options = _distractors(target, pool, 3, rng=rng)
        if len(options) < 3:
            continue
        answers = [target] + options
        rng.shuffle(answers)
        correct_index = answers.index(target)

        blanked = re.sub(re.escape(target), "______", sentence, count=1, flags=re.IGNORECASE)
        questions.append(
            {"text": blanked, "options": answers, "correct_index": correct_index, "explanation": sentence}
        )
        used_words.add(target)

    return questions
