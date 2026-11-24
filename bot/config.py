"""تحميل الإعدادات من متغيرات البيئة / ملف .env"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str | Path = ".env") -> None:
    """قارئ .env بسيط بدون اعتماديات خارجية."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
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
    # قراءة الملفات المصوّرة صارت عبر وكيل الرؤية بمفتاح المستخدم،
    # فلم يبقَ إعداد للمحرّك المحلي. حدود التكلفة في bot/services/extract.py.

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
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        return Path(self.database_url.split("///", 1)[-1])

    # ---------------------------------------------- قيود لوحات الاستضافة
    @property
    def runs_on_panel(self) -> bool:
        """هل نعمل على لوحة استضافة (Wispbyte/Pterodactyl/حاوية)؟

        الكشف بعلامات بيئية ومن مسار المشروع — لا بالاسم وحده، لأن اللوحات
        تشغّل من `/home/container` عادةً وتضع علامات مثل `WISPBYTE`/`SERVER_MEMORY`.
        """
        if os.environ.get("WISPBYTE") or os.environ.get("PTERODACTYL"):
            return True
        if os.environ.get("SERVER_MEMORY") or os.environ.get("CONTAINER_ENV"):
            return True
        if os.environ.get("VERCEL") or os.environ.get("RAILWAY_ENVIRONMENT"):
            return True
        try:
            return Path("/home/container").is_dir()
        except OSError:
            return False

    @property
    def sqlite_is_ephemeral(self) -> bool:
        """هل قاعدة SQLite على قرص يُمسح عند إعادة النشر؟

        ⚠️ العِلّة التي يمنعها هذا الفحص: Wispbyte (وكل حاويات Pterodactyl)
        تمسح كل ما كُتب في نظام الملفات عند إعادة النشر. البوت كان يعمل
        بـSQLite الافتراضية بلا تحذير ⇒ كل إعادة نشر تمحو المستخدمين
        والملفات والإعدادات، والمستخدم يرى «المكتبة فَرَغَت» بلا سبب واضح.
        """
        if not self.is_sqlite or not self.runs_on_panel:
            return False
        path = self.sqlite_path
        if path is None:
            return False
        # مسار داخل وحدة تخزين دائمة يبدأ بـ/mnt أو /data أو /storage عادةً
        text = str(path)
        persistent_hints = ("/mnt/", "/data/", "/storage/", "/volume/", "/var/lib/")
        if any(text.startswith(h) or f"/{h}" in text for h in persistent_hints):
            return False
        return True

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings(dotenv: str | Path = ".env") -> Settings:
    _load_dotenv(dotenv)
    return Settings(
        bot_token=_get("BOT_TOKEN"),
        owner_id=_get_int("OWNER_ID", 0),
        storage_channel=_get("STORAGE_CHANNEL"),
        database_url=_get("DATABASE_URL", "sqlite+aiosqlite:///./data/library_bot.db"),
        max_upload_mb=_get_int("MAX_UPLOAD_MB", 20),
        require_review=_get_bool("REQUIRE_REVIEW", True),
        backup_dir=_get("BACKUP_DIR", "./backups"),
        backup_keep=_get_int("BACKUP_KEEP", 14),
        bot_api_base=_get("BOT_API_BASE", ""),
        developer_name=_get("DEVELOPER_NAME", ""),
        embedding_model=_get("EMBEDDING_MODEL", ""),
        version=_get("BOT_VERSION", "1.0.0"),
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
