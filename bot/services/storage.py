"""التخزين: كل الملفات تذهب إلى جروب/قناة تيليجرام.

الآلية:
1. البوت (أدمن في قناة التخزين) يرسل الملف إلى القناة ويحفظ storage_message_id.
2. مع كل ملف رسالة "غلاف" (JSON) تحمل البيانات الوصفية لسهولة الاسترجاع اليدوي.
3. التحميل للطلاب يتم عبر forward/copy_message من القناة — بلا إعادة رفع.

الحدود المعروفة (Bot API الرسمي):
  - رفع/تنزيل حتى 20 ميجابايت للملف الواحد.
  - لتجاوز ذلك: شغّل سيرفر Bot API محلي وفعّل BOT_API_BASE (الحد يصبح 2 جيجا).
"""
from __future__ import annotations

import json
import logging
import mimetypes
from dataclasses import dataclass
from io import BytesIO

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import BufferedInputFile

log = logging.getLogger(__name__)

ENVELOPE_PREFIX = "LIBMETA "


@dataclass(frozen=True)
class StoredObject:
    message_id: int
    file_id: str | None = None
    unique_file_id: str | None = None
    size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class DownloadedFile:
    data: bytes
    filename: str
    mime_type: str | None = None


class StorageError(Exception):
    pass


class TelegramStorage:
    def __init__(self, bot: Bot, channel: str):
        if not channel:
            raise StorageError("STORAGE_CHANNEL غير مُعدّ")
        self.bot = bot
        self.channel = channel

    # ------------------------------------------------------------- الكتابة
    async def store_bytes(
        self,
        data: bytes,
        filename: str,
        caption: str | None = None,
        meta: dict | None = None,
    ) -> StoredObject:
        """يرفع ملفًا (من الذاكرة) إلى قناة التخزين."""
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        payload = BufferedInputFile(data, filename=filename)
        try:
            msg = await self.bot.send_document(
                self.channel, payload, caption=(caption or "")[:1000] or None
            )
        except TelegramForbiddenError as exc:
            raise StorageError(
                "البوت لا يملك صلاحية النشر في قناة التخزين. اجعله أدمن ثم أعد المحاولة."
            ) from exc
        except TelegramBadRequest as exc:
            raise StorageError(f"رفض تيليجرام الملف: {exc}") from exc

        await self._write_envelope(msg.message_id, filename, meta)
        doc = msg.document
        return StoredObject(
            message_id=msg.message_id,
            file_id=doc.file_id if doc else None,
            unique_file_id=doc.file_unique_id if doc else None,
            size=doc.file_size if doc else len(data),
            mime_type=doc.mime_type if doc else mime,
        )

    async def store_link(
        self,
        url: str,
        title: str,
        meta: dict | None = None,
    ) -> StoredObject:
        """يحفظ رابطًا خارجيًا كمنشور نصي في قناة التخزين (بدون تنزيل)."""
        text = f"🔗 {title}\n{url}"
        msg = await self.bot.send_message(self.channel, text[:4000], disable_web_page_preview=True)
        await self._write_envelope(msg.message_id, title, meta)
        return StoredObject(message_id=msg.message_id, file_id=None, unique_file_id=None)

    async def _write_envelope(self, ref_message_id: int, filename: str, meta: dict | None) -> None:
        envelope = {"v": 1, "ref": ref_message_id, "name": filename, **(meta or {})}
        try:
            await self.bot.send_message(
                self.channel,
                ENVELOPE_PREFIX + json.dumps(envelope, ensure_ascii=False)[:3800],
                disable_web_page_preview=True,
            )
        except Exception:  # الغلاف اختياري — لا يُفشل الرفع
            log.warning("تعذّرت كتابة غلاف البيانات للملف %s", filename, exc_info=True)

    # ------------------------------------------------------------- القراءة
    async def forward_to(
        self,
        chat_id: int,
        storage_message_id: int,
        caption: str | None = None,
    ) -> int:
        """يرسل الملف من قناة التخزين إلى المستخدم (نسخ بلا إعادة رفع)."""
        try:
            msg = await self.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=self.channel,
                message_id=storage_message_id,
                caption=(caption or "")[:1000] or None,
            )
            return msg.message_id
        except TelegramForbiddenError as exc:
            raise StorageError("تعذّر إرسال الملف: المستخدم حظر البوت") from exc
        except TelegramBadRequest as exc:
            raise StorageError(f"تعذّر إرسال الملف من التخزين: {exc}") from exc

    async def fetch_bytes(self, file_id: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        """ينزّل بايتات ملف عبر file_id (لاستخراج النص/حساب البصمة)."""
        try:
            tg_file = await self.bot.get_file(file_id)
        except TelegramBadRequest as exc:
            raise StorageError(f"الملف غير متاح للتنزيل: {exc}") from exc

        if tg_file.file_size and tg_file.file_size > max_bytes:
            raise StorageError(
                f"حجم الملف {tg_file.file_size // (1024*1024)}MB يتجاوز حد المعالجة "
                f"{max_bytes // (1024*1024)}MB"
            )

        buffer = BytesIO()
        await self.bot.download(tg_file, buffer)
        data = buffer.getvalue()
        buffer.close()
        if len(data) > max_bytes:
            raise StorageError("الملف أكبر من حد المعالجة المسموح")
        return data

    async def delete(self, storage_message_id: int | None) -> bool:
        """يحذف منشور الملف من قناة التخزين (يستخدمه الأدمن)."""
        if not storage_message_id:
            return False
        try:
            await self.bot.delete_message(self.channel, storage_message_id)
            return True
        except TelegramBadRequest as exc:
            log.warning("تعذّر حذف المنشور %s: %s", storage_message_id, exc)
            return False

    async def healthcheck(self) -> tuple[bool, str]:
        """فحص التخزين — **لا يرمي استثناءً أبدًا**.

        كان يلتقط أخطاء تيليجرام فقط، فأي خطأ شبكة عابر (`TelegramNetworkError`
        عند انقطاع النت ولو لحظة) كان **يُسقِط البوت عند الإقلاع** برسالة
        تتبّع بايثون بدل أن يعمل بلا تخزين ويحاول لاحقًا.
        """
        try:
            me = await self.bot.get_me()
            probe = await self.bot.send_message(self.channel, "🔧 فحص الاتصال بقناة التخزين")
            await self.bot.delete_message(self.channel, probe.message_id)
            return True, f"قناة التخزين جاهزة (البوت @{me.username})"
        except TelegramForbiddenError:
            return False, "البوت ليس أدمن في قناة التخزين أو لا يملك صلاحية النشر"
        except TelegramBadRequest as exc:
            return False, f"خطأ في قناة التخزين: {exc}"
        except (TelegramUnauthorizedError, TelegramRetryAfter) as exc:
            return False, f"تيليجرام رفض الطلب ({type(exc).__name__}) — راجع BOT_TOKEN"
        except Exception as exc:      # شبكة/مهلة/أي عطل غير متوقّع
            return False, (f"تعذّر الوصول إلى تيليجرام ({type(exc).__name__}) — "
                           "سيعمل البوت ويُعاد الفحص لاحقًا")


class MemoryStorage:
    """تخزين وهمي للاختبارات — يحفظ البايتات في الذاكرة بدل تيليجرام."""

    def __init__(self, max_bytes: int = 20 * 1024 * 1024):
        self.objects: dict[int, bytes] = {}
        self._next_id = 1
        self.max_bytes = max_bytes
        self.forwarded: list[tuple[int, int]] = []
        self.deleted: list[int] = []

    async def store_bytes(self, data: bytes, filename: str, caption=None, meta=None) -> StoredObject:
        mid = self._next_id
        self._next_id += 1
        self.objects[mid] = data
        return StoredObject(
            message_id=mid,
            file_id=f"file_{mid}",
            unique_file_id=f"uniq_{mid}",
            size=len(data),
            mime_type=mimetypes.guess_type(filename)[0],
        )

    async def store_link(self, url: str, title: str, meta=None) -> StoredObject:
        mid = self._next_id
        self._next_id += 1
        self.objects[mid] = url.encode()
        return StoredObject(message_id=mid, file_id=f"file_{mid}", unique_file_id=f"uniq_{mid}")

    async def forward_to(self, chat_id: int, storage_message_id: int, caption=None) -> int:
        if storage_message_id not in self.objects:
            raise StorageError("الملف غير موجود في التخزين")
        self.forwarded.append((chat_id, storage_message_id))
        return storage_message_id

    async def fetch_bytes(self, file_id: str, max_bytes: int | None = None) -> bytes:
        try:
            mid = int(file_id.split("_")[-1])
        except ValueError as exc:
            raise StorageError("معرّف ملف غير صالح") from exc
        if mid not in self.objects:
            raise StorageError("الملف غير موجود")
        return self.objects[mid]

    async def delete(self, storage_message_id: int | None) -> bool:
        if storage_message_id in self.objects:
            del self.objects[storage_message_id]
            self.deleted.append(storage_message_id)
            return True
        return False

    async def healthcheck(self) -> tuple[bool, str]:
        return True, "تخزين اختباري (ذاكرة)"
