"""تدفّق رفع ملف: مادة ← فئة ← الملف ← وصف ← وسوم ← تخزين في قناة تيليجرام."""
from __future__ import annotations

import asyncio
import base64
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import texts
from ..config import settings
from ..models import FileStatus, User
from ..services import catalog, extract, files as files_service, pdf as pdf_service
from ..services import user_ai
from ..services import settings_service, store
from ..services.storage import StorageError
from .helpers import ProgressReporter, back_button, escape, slice_page, status_label

log = logging.getLogger(__name__)
router = Router(name="upload")

SKIP_WORDS = {"تخطي", "تخطى", "skip", "-", "لا"}


def _b64encode(data: bytes | None) -> str | None:
    """حالة FSM لا تقبل bytes — نحوّلها base64."""
    if not data:
        return None
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: str | None) -> bytes | None:
    if not value:
        return None
    return base64.b64decode(value.encode("ascii"))


def _cd(value: str) -> str:
    return value


# ------------------------------------------------------------- بداية التدفّق
@router.callback_query(F.data == "upload:start")
async def cb_upload_start(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    await start_subject_choice(callback, db_session, state, page=0)
    await callback.answer()


@router.callback_query(F.data.startswith("upload:cat:"))
async def cb_upload_with_subject(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    """رفع مباشر داخل مادة معيّنة (من صفحة المادة)."""
    subject_id = int(callback.data.split(":")[-1])
    await state.set_data({"subject_id": subject_id})
    await show_category_choice(callback.message, db_session, state, subject_id)
    await state.set_state("UploadFlow:choosing_category")
    await callback.answer()


async def start_subject_choice(target, db_session, state: FSMContext, page: int) -> None:
    departments = await catalog.list_departments(db_session)
    subjects = await catalog.list_subjects(db_session)

    builder = InlineKeyboardBuilder()
    if not departments and not subjects:
        builder.row(InlineKeyboardButton(text="لا توجد مواد — تواصل مع الأدمن", callback_data="noop:page"))
        builder.row(back_button(callback_data="menu:main"))
        await target.answer("📭 لا توجد مواد في المكتبة بعد.", reply_markup=builder.as_markup(),
                            parse_mode="HTML")
        await state.clear()
        return

    items, total_pages = slice_page(subjects, page + 1)
    for subject in items:
        builder.row(InlineKeyboardButton(
            text=f"📘 {escape(subject.name[:38])}", callback_data=_cd(f"up:subj:{subject.id}")
        ))
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"up:subjpage:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
        if page + 1 < total_pages:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"up:subjpage:{page + 1}"))
        builder.row(*row)
    builder.row(back_button(callback_data="menu:main"))

    await target.answer(
        texts.steps_header("upload", 1, 4) + f"\nاختر المادة ({len(subjects)} مادة):",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await state.set_state("UploadFlow:choosing_subject")


@router.callback_query(F.data.startswith("up:subjpage:"))
async def cb_upload_page(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    page = int(callback.data.split(":")[-1])
    subjects = await catalog.list_subjects(db_session)
    items, total_pages = slice_page(subjects, page + 1)

    builder = InlineKeyboardBuilder()
    for subject in items:
        builder.row(InlineKeyboardButton(
            text=f"📘 {escape(subject.name[:38])}", callback_data=_cd(f"up:subj:{subject.id}")
        ))
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"up:subjpage:{page - 1}"))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop:page"))
    if page + 1 < total_pages:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"up:subjpage:{page + 1}"))
    builder.row(*row)
    builder.row(InlineKeyboardButton(text="❌ إلغاء", callback_data="upload:cancel"))

    await callback.message.edit_text(
        texts.steps_header("upload", 1, 4) + f"\nاختر المادة — صفحة {page + 1} من {total_pages}:",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("up:subj:"))
async def cb_upload_subject(callback: CallbackQuery, db_session, state: FSMContext) -> None:
    subject_id = int(callback.data.split(":")[-1])
    subject = await catalog.get_subject(db_session, subject_id)
    await state.update_data(subject_id=subject_id)
    await show_category_choice(callback.message, db_session, state, subject_id, subject_name=subject.name)
    await state.set_state("UploadFlow:choosing_category")
    await callback.answer()


async def show_category_choice(message, db_session, state: FSMContext, subject_id: int,
                               subject_name: str | None = None) -> None:
    categories = await files_service.list_categories(db_session)
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.row(InlineKeyboardButton(
            text=f"{category.icon or '📁'} {escape(category.name)}",
            callback_data=_cd(f"up:cat:{category.id}"),
        ))
    builder.row(InlineKeyboardButton(text="❌ إلغاء", callback_data="upload:cancel"))
    title = f"المادة: {escape(subject_name)}\n" if subject_name else ""
    await message.answer(
        texts.steps_header("upload", 2, 4) + f"\n{title}اختر نوع الملف:",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("up:cat:"))
async def cb_upload_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[-1])
    await state.update_data(category_id=category_id)
    await state.set_state("UploadFlow:waiting_file")
    await callback.message.answer(
        texts.steps_header("upload", 3, 4) + "\n"
        f"📎 أرسل الملف الآن (الحد الأقصى {settings.max_upload_mb} ميجابايت).\n\n"
        "المقبول: مستند PDF/Word، صورة، فيديو، أو رابط.\n"
        "للإلغاء اضغط زر «إلغاء» في الأسفل.",
        parse_mode="HTML",
    )
    await callback.answer()


# ---------------------------------------------------------------- استقبال الملف
async def _extract_from_message(message: Message) -> tuple[bytes | None, str | None, str | None, str | None]:
    """يرجع (البايتات، اسم الملف، نوع MIME، رابط خارجي)."""
    document = message.document
    if document:
        if document.file_size and document.file_size > settings.max_upload_bytes:
            raise StorageError(
                f"حجم الملف {document.file_size // (1024 * 1024)}MB يتجاوز الحد "
                f"{settings.max_upload_mb}MB"
            )
        buffer = await message.bot.download(document)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
        return data, document.file_name, document.mime_type, None

    if message.photo:
        photo = message.photo[-1]
        buffer = await message.bot.download(photo)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
        return data, f"image_{photo.file_unique_id}.jpg", "image/jpeg", None

    if message.video:
        if message.video.file_size and message.video.file_size > settings.max_upload_bytes:
            raise StorageError("حجم الفيديو يتجاوز الحد المسموح")
        buffer = await message.bot.download(message.video)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
        return data, f"video_{message.video.file_unique_id}.mp4", "video/mp4", None

    if message.audio:
        buffer = await message.bot.download(message.audio)
        data = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer.read())
        return data, f"audio_{message.audio.file_unique_id}.mp3", "audio/mpeg", None

    if message.text and message.text.strip().lower().startswith(("http://", "https://")):
        return None, message.text.strip()[:200], None, message.text.strip()

    raise StorageError("أرسل مستندًا أو صورة أو فيديو أو رابطًا يبدأ بـ http")


@router.message("UploadFlow:waiting_file")
async def handle_uploaded_file(message: Message, db_user: User, db_session, state: FSMContext) -> bool:
    """يستقبل الملف ضمن تدفّق الرفع. يرجع True لو تعامل مع الرسالة."""
    data_state = await state.get_data()
    subject_id = data_state.get("subject_id")
    category_id = data_state.get("category_id")
    if not subject_id or not category_id:
        return False

    if (message.text or "").strip().lower() in SKIP_WORDS:
        await state.clear()
        await message.answer("❌ تم إلغاء الرفع.")
        return True

    try:
        data, filename, mime, url = await _extract_from_message(message)
    except StorageError as exc:
        await message.answer(f"⚠️ {exc}\nأعد الإرسال أو اضغط «إلغاء»")
        return True
    except Exception as exc:  # فشل تنزيل من تيليجرام
        log.warning("فشل تنزيل الملف المرفوع: %s", exc)
        await message.answer("⚠️ تعذّر استلام الملف من تيليجرام. أعد المحاولة.")
        return True

    await state.update_data(
        has_file=True,
        original_filename=filename,
        mime_type=mime,
        external_url=url,
        file_size=len(data) if data else None,
        payload_b64=_b64encode(data) if data else None,
    )

    # استخراج نص PDF للبحث والتلخيص، مع OCR للملفات المصوّرة
    extracted = None
    pages = None
    is_pdf = data and (
        (mime or "").lower() == "application/pdf" or (filename or "").lower().endswith(".pdf")
    )
    if is_pdf:
        # حاجب — في خيط منفصل (الملفات الكبيرة كانت تجمّد البوت كله)
        result = await asyncio.to_thread(pdf_service.extract_text, data)
        extracted = result.text or None
        pages = result.pages

        if result.is_scanned:
            # ملف مصوّر: لا OCR محلي بعد الآن — يُرسَل إلى وكيل الرؤية
            # الذي اختاره المستخدم، بمفتاحه هو.
            extracted = await _read_with_vision(message, db_user, db_session, data,
                                               filename=filename, mime=mime)

    await state.update_data(extracted_text=extracted, pages_count=pages)
    await state.set_state("UploadFlow:waiting_description")

    await message.answer(
        "✅ <b>تم استلام الملف</b>\n\n"
        + texts.steps_header("upload", 4, 4)
        + "\n✏️ اكتب وصفًا مختصرًا للملف، أو أرسل «تخطي»:",
        parse_mode="HTML",
    )
    return True



async def _read_with_vision(
    message: Message, db_user: User, db_session, data: bytes | None,
    *, filename: str | None = None, mime: str | None = None,
    on_progress=None,
) -> str | None:
    """يقرأ ملفًا مصوّرًا عبر مزوّد المستخدم. لا يرمي — يُبلّغ ويعيد None.

    **لماذا لا نرمي استثناءً؟** لأن قراءة الملف ميزة *مساعدة*: فشلها لا
    يجوز أن يُسقط الرفع نفسه. الطالب قد يريد رفع المحاضرة للتنزيل فقط،
    فمن الظلم أن يخسر الرفع لأن مفتاحه انتهى.
    """
    if not data:
        return None

    provider, key, model = await user_ai.vision_choice(db_session, db_user.id)
    if not key:
        await message.answer(
            "ℹ️ <b>الملف مصوّر — لا أستطيع قراءة نصه.</b>\n\n"
            "قراءة الملفات المصوّرة تتم عبر وكيل ذكاء اصطناعي، وليس عندك "
            "مفتاح محفوظ.\n"
            "افتح ⚙️ <b>إعدادات الذكاء الاصطناعي</b> ← أضف مفتاحك، ثم "
            "أعد رفع الملف — أو ارفعه كما هو وسيبقى متاحًا للتنزيل "
            "(بلا بحث داخل نصه).",
            parse_mode="HTML",
        )
        return None

    if not extract.supports_vision(provider):
        await message.answer(f"ℹ️ {extract.vision_reason(provider)}")
        return None

    reporter = ProgressReporter(
        message, "قراءة الملف بالذكاء الاصطناعي",
        total=None, stage=f"الوكيل: {ai_label(provider)}")
    await reporter.start()
    try:
        result = await extract.extract_async(
            data, filename=filename or "", mime=mime or "",
            provider_key=provider, api_key=key, model=model or None,
            on_progress=reporter.report if on_progress is None else on_progress,
        )
    except extract.ExtractError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}", parse_mode="HTML")
        return None
    except Exception as exc:               # noqa: BLE001 — لا نُسقط الرفع
        log.info("تعذّر استخراج النص عبر الرؤية: %s", type(exc).__name__)
        await message.answer(
            "⚠️ تعذّر على الوكيل قراءة الملف. الملف مرفوع وسيُتاح للتنزيل، "
            "لكن البحث داخل نصه لن يعمل."
        )
        return None
    finally:
        await reporter.finish()

    if result.ok:
        note = f"✅ <b>قُرئ النص بالذكاء الاصطناعي</b> ({ai_label(provider)})"
        if result.pages > 1:
            note += f"\n📄 الصفحات: {result.pages_done} من {result.pages}"
        if result.notes:
            note += "\n" + "\n".join(f"• {escape(n)}" for n in result.notes)
        note += "\n🔎 البحث داخل الملف سيعمل."
        await message.answer(note, parse_mode="HTML")
        return result.text

    await message.answer(
        "⚠️ لم أستخرج نصًا من الملف — قد تكون صفحاته فارغة أو الصور غير واضحة."
    )
    return None


def ai_label(provider: str) -> str:
    from ..services import ai

    found = ai.PROVIDERS.get((provider or "").lower())
    return found.label if found else (provider or "غير محدّد")


# --------------------------------------------------------------- الوصف والوسوم
@router.message(F.text, "UploadFlow:waiting_description")
async def on_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    description = None if text.lower() in SKIP_WORDS else text[:1000]
    await state.update_data(description=description)
    await state.set_state("UploadFlow:waiting_tags")
    await message.answer("🏷️ اكتب وسومًا مفصولة بفواصل (مثل: تفاضل، ترم أول)، أو أرسل «تخطي»:")


@router.message(F.text, "UploadFlow:waiting_tags")
async def on_tags(message: Message, db_user: User, db_session, state: FSMContext) -> None:
    text = (message.text or "").strip()
    tags = None if text.lower() in SKIP_WORDS else text[:300]
    await finalize_upload(message, db_user, db_session, state, tags)


async def finalize_upload(message: Message, db_user: User, db_session, state: FSMContext,
                          tags: str | None) -> None:
    data_state = await state.get_data()
    subject_id = data_state.get("subject_id")
    category_id = data_state.get("category_id")
    if not subject_id or not category_id or not data_state.get("has_file"):
        await state.clear()
        await message.answer("⚠️ انتهت جلسة الرفع. ابدأ من جديد: 📤 رفع ملف")
        return

    subject = await catalog.get_subject(db_session, subject_id)

    # كشف التكرار بالمحتوى (sha256): البنية التحتية موجودة ومختبَرة
    # (sha256_of/duplicate_of/عمود مفهرس) لكنها لم تكن موصّلة هنا —
    # بلا هذا الفحص تُحفظ نسخ مكررة من نفس الملف.
    # الملف. الروابط الخارجية بلا بايتات فلا ينطبق عليها الفحص.
    raw = None if data_state.get("external_url") else _b64decode(data_state.get("payload_b64"))
    sha256 = files_service.sha256_of(raw) if raw else None
    if sha256:
        duplicate = await files_service.duplicate_of(db_session, sha256)
        if duplicate is not None:
            await state.clear()
            await message.answer(
                "⚠️ <b>هذا الملف مرفوع من قبل — لم تُحفظ نسخة ثانية</b>\n"
                + texts.info_block([
                    ("📄", "المطابق", escape(duplicate.title)),
                    ("🆔", "معرّفه", duplicate.id),
                    ("📌", "حالتُه", status_label(duplicate.status)),
                ]),
                parse_mode="HTML",
            )
            return

    # تخزين الملف في قناة تيليجرام
    storage = store.get_backend()
    try:
        if data_state.get("external_url"):
            stored = await storage.store_link(
                data_state["external_url"],
                data_state.get("original_filename") or "رابط",
                meta={"subject": subject.name, "uploader": db_user.telegram_id},
            )
        else:
            if not raw:
                await state.clear()
                await message.answer("❌ لم نتمكّن من الاحتفاظ بالملف. أعد المحاولة من زر 📤 رفع ملف")
                return
            stored = await storage.store_bytes(
                raw,
                data_state.get("original_filename") or "file",
                caption=f"📥 {subject.name} — {db_user.display_name}",
                meta={"subject": subject.name, "uploader": db_user.telegram_id},
            )
    except StorageError as exc:
        await state.clear()
        await message.answer(f"❌ فشل التخزين: {exc}")
        return

    require_review = await settings_service.get_bool(db_session, "require_review", settings.require_review)
    status = FileStatus.PENDING if require_review else FileStatus.APPROVED
    item = await files_service.register_file(
        db_session,
        subject_id=subject_id,
        category_id=category_id,
        uploader_id=db_user.id,
        title=(data_state.get("original_filename") or "ملف")[:240],
        description=data_state.get("description"),
        tags=tags,
        original_filename=data_state.get("original_filename"),
        file_size=data_state.get("file_size"),
        mime_type=data_state.get("mime_type"),
        storage_message_id=stored.message_id,
        tg_file_id=stored.file_id,
        tg_unique_file_id=stored.unique_file_id,
        external_url=data_state.get("external_url"),
        sha256=sha256,
        pages_count=data_state.get("pages_count"),
        status=status,
        extracted_text=data_state.get("extracted_text"),
    )
    db_user.uploads_count += 1

    await db_session.commit()
    await state.clear()

    card_rows = [
        ("📄", "العنوان", escape(item.title)),
        ("📘", "المادة", escape(subject.name if subject else "—")),
    ]
    if status == FileStatus.APPROVED:
        await message.answer(
            "✅ <b>تم نشر الملف بنجاح</b>\n" + texts.info_block(card_rows),
            parse_mode="HTML",
        )
        await notify_staff(db_session, message.bot, item, approved=True)
    else:
        await message.answer(
            "⏳ <b>تم استلام الملف — قيد المراجعة</b>\n" + texts.info_block(card_rows)
            + "\n\nسيظهر للطلاب بعد موافقة أحد المشرفين.",
            parse_mode="HTML",
        )
        await notify_staff(db_session, message.bot, item, approved=False)


async def notify_staff(db_session, bot, item, approved: bool) -> None:
    """يبلّغ المشرفين والأدمن بوجود ملف جديد للمراجعة."""
    from sqlalchemy import select

    from ..models import User, UserRole

    staff_ids = (
        await db_session.scalars(
            select(User.telegram_id).where(
                User.role.in_(
                    [UserRole.MODERATOR.value, UserRole.ADMIN.value, UserRole.OWNER.value]
                )
            )
        )
    ).all()

    text = (
        f"📥 <b>ملف جديد — {'منشور ✅' if approved else 'بانتظار المراجعة ⏳'}</b>\n"
        + texts.info_block([
            ("📄", "العنوان", escape(item.title)),
            ("🆔", "المعرّف", item.id),
        ])
        + "\nراجع الملفات من لوحة الإدارة."
    )
    for tg_id in staff_ids:
        try:
            await bot.send_message(tg_id, text, parse_mode="HTML")
        except Exception as exc:
            log.debug("تعذّر إبلاغ %s: %s", tg_id, exc)


# ------------------------------------------------------------------- الإلغاء
@router.callback_query(F.data == "upload:cancel")
async def cb_cancel(callback: CallbackQuery, db_user: User,
                    state: FSMContext) -> None:
    await state.clear()
    from .start import main_menu

    await callback.message.edit_text("❌ تم إلغاء الرفع.")
    await callback.message.answer(texts.header("home") + "\nاختر قسمًا:",
                                  reply_markup=main_menu(db_user),
                                  parse_mode="HTML")
    await callback.answer()
