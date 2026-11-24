"""أدوات مشتركة: ترقيم الصفحات، أزرار التنقّل، تنسيق رسائل الملفات."""
from __future__ import annotations

import asyncio
import logging
import math
import time

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton

from .. import texts
from ..models import FileItem, FileStatus, User, UserRole

log = logging.getLogger(__name__)

PAGE_SIZE = 8


def paginate(total: int, page: int) -> int:
    pages = max(1, math.ceil(total / PAGE_SIZE))
    return max(1, min(page, pages))


def slice_page(items: list, page: int) -> tuple[list, int]:
    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = paginate(len(items), page)
    start = (page - 1) * PAGE_SIZE
    return items[start : start + PAGE_SIZE], total_pages


def back_button(label: str = "⬅️ رجوع", callback_data: str = "menu:main") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=callback_data)


class ProgressReporter:
    """رسالة حالة حيّة أثناء معالجة طويلة: «الملف شغّال وفاضل قد إيه».

    تُحرَّر كل ثوانٍ (لا مع كل صفحة — حدود flood في تيليجرام) فتعرض
    شريط التقدّم والمنقضي والمتبقّي التقديري من `bot/texts.py`.
    `report()` آمنة الاستدعاء من أي خيط — OCR يعمل في خيط عامل عبر
    `asyncio.to_thread` ويبثّ عدد الصفحات المنجزة من هناك.
    """

    TICK_SECONDS = 2.5

    def __init__(self, message, title: str, total: int | None = None,
                 stage: str = "جارٍ البدء") -> None:
        self._anchor = message
        self._title = title
        self._total = total
        self._stage = stage
        self._done = 0
        self._started = 0.0
        self._msg = None
        self._task: asyncio.Task | None = None
        self._last_text = ""
        self._frame = 0

    async def start(self) -> None:
        self._started = time.monotonic()
        self._msg = await self._anchor.answer(self._render(), parse_mode="HTML")
        self._task = asyncio.create_task(self._ticker())

    def report(self, done: int) -> None:
        """يستدعيها العامل (من خيط آخر) بعدد الوحدات المنجزة."""
        if done > self._done:
            self._done = done

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def _render(self) -> str:
        self._frame += 1
        return texts.processing_card(
            self._title, self._stage, self._done, self._total,
            time.monotonic() - self._started, self._frame,
        )

    async def _ticker(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.TICK_SECONDS)
                await self._refresh()
        except asyncio.CancelledError:
            pass

    async def _refresh(self) -> None:
        if self._msg is None:
            return
        text = self._render()
        if text == self._last_text:
            return
        self._last_text = text
        try:
            await self._msg.edit_text(text, parse_mode="HTML")
        except TelegramBadRequest as exc:
            # الأسباب المتوقّعة: نصّ لم يتغيّر، رسالة محذوفة، أو HTML غير صالح.
            # لا نُسقط المهمة بسبب عنصر تجميلي — لكن نُسجّل لنعرف لو تكرّر.
            log.debug("تعذّر تحديث رسالة التقدّم: %s", exc)
        except TelegramRetryAfter:
            # حدّ المعدّل: نتجاهل هذا التحديث فقط؛ العدّاد يكمل.
            log.debug("حدّ معدّل تيليجرام — تخطّي تحديث التقدّم")

    async def finish(self) -> None:
        """يوقف العدّاد ويمسح رسالته (النتيجة تُرسل منفصلة)."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._msg is not None:
            try:
                await self._msg.delete()
            except TelegramBadRequest as exc:
                # محذوفة أصلًا أو قديمة — لا شيء يستحق الفشل هنا.
                log.debug("تعذّر حذف رسالة التقدّم: %s", exc)
            self._msg = None


def escape(text: str | None) -> str:
    if not text:
        return ""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def file_caption(item: FileItem, uploader: User | None = None) -> str:
    parts = [f"📚 <b>{escape(item.title)}</b>"]
    if item.description:
        parts.append(escape(item.description[:400]))
    meta = []
    if uploader:
        meta.append(f"الرافع: {escape(uploader.display_name)}")
    if item.created_at:
        meta.append(f"التاريخ: {item.created_at.strftime('%Y-%m-%d')}")
    meta.append(f"التحميلات: {item.downloads_count}")
    if item.tag_list:
        meta.append("الوسوم: " + ", ".join(escape(t) for t in item.tag_list[:8]))
    parts.append("\n".join(meta))
    return "\n\n".join(parts)[:1000]


ROLE_LABEL = {
    UserRole.STUDENT.value: "طالب",
    UserRole.MODERATOR.value: "مشرف",
    UserRole.ADMIN.value: "أدمن",
    UserRole.OWNER.value: "المالك",
}


def role_label(role: str) -> str:
    return ROLE_LABEL.get(role, role)


STATUS_LABEL = {
    FileStatus.PENDING.value: "⏳ قيد المراجعة",
    FileStatus.APPROVED.value: "✅ منشور",
    FileStatus.REJECTED.value: "❌ مرفوض",
    FileStatus.DELETED.value: "🗑️ محذوف",
}


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status)


def profile_card_text(user: User) -> str:
    """بطاقة الحساب: الصلاحية والنشاط (بلا نقاط — أُزيل نظام النقاط)."""
    return "\n".join([
        texts.header("profile", escape(user.display_name)),
        texts.info_block([
            ("🎖️", "الصلاحية", role_label(user.role)),
            ("📤", "الملفات المرفوعة", user.uploads_count),
            ("📥", "التحميلات", user.downloads_count),
        ]),
    ])


