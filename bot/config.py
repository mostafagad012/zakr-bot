"""تحميل الإعدادات من متغيرات البيئة / ملف .env"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str | Path = ".env") -> None:
    """قارئ .env بسيط بدون اعتماديات خارجية."""
    p = Path(path)
    if not p.exists():
        return
    # utf-8-sig يتعامل مع ملفات .env المحفوظة من محررات تضيف BOM.
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not (key[0].isalpha() or key[0] == "_") or not all(
            char.isalnum() or char == "_" for char in key
        ):
            continue
        value = value.strip()
        if value[:1] in {"'", '"'}:
            try:
                parsed = shlex.split(value, comments=True, posix=True)
                value = parsed[0] if parsed else ""
            except ValueError:
                # لا نحمّل قيمة مكسورة بصمت؛ اتركها كما كُتبت ليظهر الخطأ
                # عند التحقق من الإعدادات بدل إسقاط بقية الملف.
                continue
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "نعم"}


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = _get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    storage_channel: str
    database_url: str
    max_upload_mb: int = 20
    require_review: bool = True
    backup_dir: str = "./backups"
    backup_keep: int = 14
    bot_api_base: str = ""
    developer_name: str = ""
    version: str = "1.0.0"
    embedding_model: str = ""
    ocr_langs: str = ""   # فارغة = كشف تلقائي للغة من خط الصفحة
    ocr_dewarp: bool = True  # فكّ التواء UVDoc + tesseract معًا (عطّله بـ OCR_DEWARP=0)

    # ---------------------------------------------- الذكاء الاصطناعي (AI)
    # كل الخدمات الكبرى تعرض واجهة متوافقة مع OpenAI، فمزوّد واحد يكفي:
    # openai | gemini | groq | openrouter | custom
    ai_provider: str = "openai"
    ai_api_key: str = ""
    # مفاتيح خاصة بكل مزوّد: AI_API_KEY_GEMINI، AI_API_KEY_GROQ…
    ai_provider_keys: dict[str, str] = field(default_factory=dict)
    ai_model: str = ""        # فارغ = الاسم الافتراضي للمزوّد
    ai_base_url: str = ""     # مطلوب فقط لمزوّد custom
    ai_temperature: float = 0.6
    ai_timeout: float = 60.0
    # سقف ملف PDF للامتحان — أقل من سقف الرفع لأن المسار هنا أثقل (OCR + AI)
    exam_pdf_max_mb: int = 10

    @property
    def ai_enabled(self) -> bool:
        """هل طبقة الذكاء الاصطناعي جاهزة؟

        ملاحظة: المفتاح قد يأتي من `.env` **أو** من مفتاح المستخدم المحفوظ
        في قاعدة البيانات، فلا يصحّ الحكم من `ai_api_key` وحدها. الفحص
        الحقيقي في `bot/services/user_ai.py` (المفتاح + `.env`).
        """
        return bool(self.ai_api_key.strip()) or self.ai_provider not in {"", "none"}

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        return Path(self.database_url.split("///", 1)[-1])

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _normalize_database_url(url: str) -> str:
    """توحيد صيغ PostgreSQL الشائعة التي تصدرها لوحات الاستضافة."""
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def load_settings(dotenv: str | Path = ".env") -> Settings:
    _load_dotenv(dotenv)
    return Settings(
        bot_token=_get("BOT_TOKEN"),
        owner_id=_get_int("OWNER_ID", 0),
        storage_channel=_get("STORAGE_CHANNEL"),
        database_url=_normalize_database_url(_get("DATABASE_URL", "sqlite+aiosqlite:///./data/library_bot.db")),
        max_upload_mb=_get_int("MAX_UPLOAD_MB", 20),
        require_review=_get_bool("REQUIRE_REVIEW", True),
        backup_dir=_get("BACKUP_DIR", "./backups"),
        backup_keep=_get_int("BACKUP_KEEP", 14),
        bot_api_base=_get("BOT_API_BASE", ""),
        developer_name=_get("DEVELOPER_NAME", ""),
        embedding_model=_get("EMBEDDING_MODEL", ""),
        version=_get("BOT_VERSION", "1.0.0"),
        ocr_langs=_get("OCR_LANGS", ""),
        ocr_dewarp=_get_bool("OCR_DEWARP", True),
        ai_provider=_get("AI_PROVIDER", "openai"),
        ai_api_key=_get("AI_API_KEY"),
        ai_provider_keys={
            _k.removeprefix("AI_API_KEY_").lower(): _v
            for _k, _v in os.environ.items()
            if _k.startswith("AI_API_KEY_") and _v.strip()
        },
        ai_model=_get("AI_MODEL"),
        ai_base_url=_get("AI_BASE_URL"),
        ai_temperature=_get_float("AI_TEMPERATURE", 0.6),
        ai_timeout=_get_float("AI_TIMEOUT", 60.0),
        exam_pdf_max_mb=_get_int("EXAM_PDF_MAX_MB", 10),
    )


settings = load_settings()
