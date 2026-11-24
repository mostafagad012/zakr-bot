"""الكتابة الآمنة في `.env` — للأدمن فقط.

لماذا هذا الملف موجود؟ لأن الأدمن يريد أن يضيف مزوّد ذكاء اصطناعي من داخل
البوت، وأن ينعكس ذلك على **ملف `.env`** لا على قاعدة البيانات وحدها. لكن
الكتابة في ملف إعدادات أثناء تشغيل البوت عملية خطرة إن لم تُضبط:

1. **الملف يُقرأ كاملًا ويُعاد بناؤه**، ولا نستخدم بحثًا/استبدالًا أعمى.
2. **كل الأسطر الأخرى تبقى كما هي** — التعليقات، الترتيب، والقيم غير
   المتعلّقة بالذكاء الاصطناعي. لا نُعيد ترتيب ملف الأدمن.
3. **كتابة ذرّية** (ملف مؤقت + `os.replace`): انقطاع الكهرباء أو قتل
   العملية في منتصف الكتابة لا يترك `.env` نصف مكتوب — وهو ما يُفقد البوت
   إقلاعه في التشغيل التالي.
4. **نسخة احتياطية قبل كل تعديل** (`.env.bak`) وصلاحية `600`: الملف يحتوي
   مفاتيح، فلا يجوز أن يقرأه غير مالك العملية.
5. **المفتاح لا يُسجَّل ولا يُعرض**: الدوال هنا تتلقى قيمًا وتكتبها، ولا
   تُرجع المفتاح في أي رسالة خطأ.
6. **تطبيق فوري بلا إعادة تشغيل**: بعد كل كتابة تُحدَّث `config.settings`
   في الذاكرة (لأنها `frozen` نستبدلها ببساطة)، فيسري التغيير على كل
   مستخدمي البوت في نفس اللحظة.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    """وقت الآن بتوقيت UTC — مصدر واحد للطوابع الزمنية."""
    return datetime.now(UTC)

# المتغيّرات التي يديرها هذا الملف. أي مفتاح خارجها يُرفض — لا نسمح
# بتعديل متغيّرات حساسة أخرى (BOT_TOKEN، DATABASE_URL، OWNER_ID…) من الشات.
EDITABLE_KEYS: tuple[str, ...] = (
    "AI_PROVIDER",
    "AI_API_KEY",
    "AI_MODEL",
    "AI_BASE_URL",
    "AI_TEMPERATURE",
    "AI_TIMEOUT",
)

def env_path(path: str | Path | None = None) -> Path:
    """مسار `.env` — قابل للتجاوز في الاختبارات."""
    return Path(path or os.environ.get("ENV_FILE_PATH") or ".env")


def provider_key_name(provider: str) -> str:
    """اسم المتغيّر لمفتاح مزوّد: `gemini` ⇒ `gemini_api_key`.

    التسمية المطلوبة من المالك هي `<اسم المزوّد>_api_key`، فاسم المزوّد هو
    الذي يظهر في الملف مباشرة — أوضح لمن يفتح `.env` بيده.
    """
    import re

    name = (provider or "").strip().lower().replace("-", "_")
    name = re.sub(r"[^a-z0-9_]", "", name)
    return f"{name}_api_key"


def provider_url_name(provider: str) -> str:
    """`gemini` ⇒ `gemini_base_url` — عنوان الوكيل المخصّص."""
    return provider_key_name(provider).replace("_api_key", "_base_url")


def provider_model_name(provider: str) -> str:
    """`gemini` ⇒ `gemini_model` — الموديل الافتراضي للوكيل."""
    return provider_key_name(provider).replace("_api_key", "_model")


# لاحقات الوكلاء: `<name>_api_key` و`<name>_base_url` و`<name>_model`.
_AGENT_SUFFIXES = ("_api_key", "_base_url", "_model")


def is_allowed(key: str) -> bool:
    """هل يُسمح بتعديل هذا المتغيّر؟

    المسموح: متغيّرات `AI_*` في القائمة، ومتغيّرات الوكلاء
    (`<name>_api_key` · `<name>_base_url` · `<name>_model`)، والصيغة القديمة
    `AI_API_KEY_<PROVIDER>` للتوافق. أما `BOT_TOKEN`/`DATABASE_URL` فممنوعة.
    """
    if key in EDITABLE_KEYS:
        return True
    if key.startswith("AI_API_KEY_"):
        suffix = key[len("AI_API_KEY_"):]
        return suffix.isalnum() and suffix.isupper()

    lowered = key.lower()
    for suffix in _AGENT_SUFFIXES:
        if lowered.endswith(suffix):
            base = key[: -len(suffix)]
            if not base:
                return False
            return _name_allowed(base, suffix)
    return False


def _name_allowed(base: str, suffix: str) -> bool:
    """هل الاسم الأساسي صالح لهذا المتغيّر؟

    **قاعدة حقيقية اكتُشفت بالفحص**: اسم الوكيل لا يطابق اسم متغيّره دائمًا.
    الواجهات المعروفة تكتب `GEMINI_API_KEY` (بحروف كبيرة)، و`is_allowed`
    كانت ترفضها لأنها تفحص الاسم الأساسي بقواعد الأسماء الصغيرة. وفي
    الملف الواحد قد يوجد `gemini_api_key` و`GEMINI_API_KEY` معًا — فأي
    قراءة أو حذف بالمفتاح الخطأ يمسح سطرًا غير المقصود.

    الحلّ: نقبل الشكلين، **ونحوّل دائمًا إلى الشكل الصغير القياسي** قبل أي
    قراءة/كتابة/حذف (انظر `canonical_key`).
    """
    from . import agents as agents_service

    lowered = base.lower()
    # مزوّد ثابت معروف: متغيّراته باسمه هي **الطريقة الصحيحة** (`groq_api_key`).
    # رفضها كان يعني أن الأدمن لا يستطيع حفظ مفتاح لمزوّد ثابت أصلًا — وهو
    # بالضبط ما طلبه المالك (`groq` ⇒ `groq_api_key`).
    if lowered in agents_service.RESERVED_PROVIDERS:
        return True
    if agents_service.validate_name(base)[0]:
        return True
    if lowered != base and agents_service.validate_name(lowered)[0]:
        return True
    # مزوّد محلي: `<name>_base_url` / `<name>_model` بلا مفتاح.
    # `<name>_api_key` لا يُقبل هكذا — الاسم الخطأ في المفتاح نفسه أسوأ من
    # رفضه، لأن المفتاح هو ما يُرسَل فعلًا.
    return suffix in ("_base_url", "_model") and (
        lowered in agents_service.discover() or base in agents_service.discover()
    )


def split_agent_var(key: str) -> tuple[str, str] | None:
    """`gemini_api_key` ⇒ (`gemini`, `_api_key`) — أو None إن لم يكن متغيّر وكيل."""
    name = (key or "").strip()
    if name.startswith("AI_API_KEY_"):
        suffix = name[len("AI_API_KEY_"):]
        if suffix.isalnum() and suffix.isupper():
            return suffix.lower(), "_api_key"
        return None
    # اللواحق تُطابَق **بلا حساسية لحالة الأحرف**: الواجهات المعروفة تكتب
    # `GEMINI_API_KEY`، وبمطابقة حسّاسة كان هذا السطر غير مرئي للبوت أصلًا
    # (لا يُقرأ ولا يُحذف ولا يُعدّل) — وهو أول ما لاحظه الفحص.
    lowered = name.lower()
    # `AI_MODEL` / `AI_BASE_URL` / `AI_PROVIDER` إعدادات عامة، لا «وكيلًا
    # اسمه ai» — وبلا هذا السطر تُقرأ `AI_MODEL` كـ`ai_model` فتُكتب في
    # مكان غير مكانها (خطأ حقيقي ظهر في الاختبارات).
    if lowered.startswith("ai_"):
        return None
    for suffix in _AGENT_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            return lowered[: -len(suffix)], suffix
    return None


def canonical_key(key: str) -> str:
    """الشكل الواحد الذي نقرأ/نكتب به: `<name>_suffix` بحروف صغيرة.

    بلا هذا، `GEMINI_API_KEY` (كما تكتبها واجهات المزوّدين) غير مرئية
    للبوت، ولو أضافها الأدمن يدويًا لظنّ أن المفتاح لم يُحفظ.
    """
    parsed = split_agent_var(key)
    if parsed is None:
        return (key or "").strip()
    base, suffix = parsed
    return f"{base.lower()}{suffix}"


def agent_vars_matching(name: str, path: str | Path | None = None) -> dict[str, str]:
    """كل أسطر الوكيل في الملف بأي شكل (`gemini_api_key` أو `GEMINI_API_KEY`).

    يُرجع {اسم السطر كما هو: القيمة}. مفيد للترقية والحذف الآمن: نحذف كل
    الصيغ لا واحدة فقط، فلا يبقى سطر «ميت» يظهر في شاشة .env بلا معنى.
    """
    wanted = (name or "").strip().lower()
    found: dict[str, str] = {}
    for line in _read_lines(env_path(path)):
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            continue
        raw_name, _, value = stripped.partition("=")
        raw_name = raw_name.strip()
        parsed = split_agent_var(raw_name)
        if parsed is None:
            continue
        base, _suffix = parsed
        if base.lower() == wanted:
            found[raw_name] = value.strip()
    return found


def is_writable(path: str | Path | None = None) -> bool:
    """هل نستطيع الكتابة؟ في Railway/Vercel قد يكون الملف للقراءة فقط."""
    target = env_path(path)
    return target.exists() and os.access(target, os.W_OK)


def _shorten(value: str, limit: int = 60) -> str:
    """عرض آمن للقيمة في السجل — المفاتيح لا تُسجَّل كاملة أبدًا."""
    value = value or ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"[:limit]


# كم نسخة احتياطية نحتفظ بها. النسخة الواحدة تكفي للتراجع خطوة، لكنها لا
# تكفي لاكتشاف «إمتى اتغيّر المفتاح ومين غيّره» بعد أسبوع.
BACKUP_KEEP = 5


def backup_path(path: str | Path | None = None) -> Path:
    """آخر نسخة احتياطية: `.env` ⇒ `.env.bak`."""
    target = Path(path) if path is not None else env_path()
    return target.with_name(target.name + ".bak")


def backups_dir(path: str | Path | None = None) -> Path:
    """مجلد النسخ المؤرخة: `.env.backups/`."""
    target = Path(path) if path is not None else env_path()
    return target.with_name(target.name + ".backups")


def _backup(path: Path) -> None:
    """نسخة `.bak` + نسخة مؤرخة، ثم تدوير الأقدم.

    لماذا نسختان؟ `.bak` هي «رجوع خطوة واحدة» السريع، والمؤرخة تحفظ تاريخ
    التعديلات (مفيدة عند تتبّع «مين غيّر المفتاح الشهر اللي فات»).
    فشل النسخ لا يمنع الكتابة — لكنه يُسجَّل بصراحة.
    """
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copy2(path, backup_path(path))
        os.chmod(backup_path(path), stat.S_IRUSR | stat.S_IWUSR)

        folder = backups_dir(path)
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / f"{stamp}-{path.name}.bak"
        counter = 1
        while target.exists():                      # تعديلان في نفس الثانية
            target = folder / f"{stamp}-{counter}-{path.name}.bak"
            counter += 1
        shutil.copy2(path, target)
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)

        old = sorted(folder.glob("*.bak"), reverse=True)[BACKUP_KEEP:]
        for stale in old:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("تعذّر إنشاء نسخة احتياطية: %s", type(exc).__name__)


def list_backups(path: str | Path | None = None) -> list[Path]:
    """النسخ المؤرخة، الأحدث أولًا."""
    folder = backups_dir(path)
    if not folder.exists():
        return []
    return sorted(folder.glob("*.bak"), reverse=True)


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        # ملف بترميز غريب: لا نخمّن، نرفض التعديل ونُعلم الأدمن.
        raise OSError(f"تعذّرت قراءة {path.name}: {type(exc).__name__}") from exc


def _set_in_lines(lines: list[str], key: str, value: str) -> list[str]:
    """يضبط `key=value` مع الحفاظ على مكان السطر وتعليقاته الأخرى."""
    prefix = f"{key}="
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        # سطر معطَّل بالتعليق ("# KEY=…" أو "#KEY=…"): نُعيده فعّالًا في
        # نفس مكانه بدل تكرار المتغيّر في نهاية الملف.
        commented = stripped.lstrip("#").strip()
        if commented.startswith(prefix):
            lines[index] = f"{key}={value}"
            return lines
        if stripped.startswith(prefix):
            lines[index] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


def _replace_in_lines(lines: list[str], drops: list[str], key: str,
                      value: str) -> list[str]:
    """يحذف كل `drops` ثم يضبط `key` — في **كتابة واحدة**.

    لماذا لا نستخدم `remove_value` ثم `write_value`؟ لأن كل واحدة منهما
    تكتب الملف كاملًا (ونسخة احتياطية)، فبين الكتابتين لحظة يمكن أن تُقرأ
    فيها قيمة قديمة، أو ينقطع التنفيذ فيبقى الملف نصف معدَّل. هنا نُنشئ
    المحتوى النهائي مرة واحدة.
    """
    drop_prefixes = tuple(f"{name}=" for name in drops)
    cleaned = [line for line in lines
               if not line.strip().startswith(drop_prefixes)]
    return _set_in_lines(cleaned, key, value)


def _write_atomic(path: Path, lines: list[str]) -> None:
    """كتابة ذرّية + نسخة احتياطية + صلاحية 600."""
    content = "\n".join(lines).rstrip("\n") + "\n"

    if path.exists():
        _backup(path)

    directory = path.parent or Path(".")
    handle, temp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, stat.S_IRUSR | stat.S_IWUSR)   # 600
        os.replace(temp_name, path)                        # ذرّي
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise


def read_value(key: str, path: str | Path | None = None) -> str:
    """القيمة الحالية لمتغيّر — للتدقيق وللعرض (تستخدمها شاشة .env)."""
    if not is_allowed(key):
        return ""
    wanted = canonical_key(key)
    for line in _read_lines(env_path(path)):
        stripped = line.strip()
        if stripped.startswith(f"{wanted}="):
            return stripped.split("=", 1)[1].strip()
    # الصيغة الأخرى من نفس المتغيّر (`GEMINI_API_KEY` مقابل `gemini_api_key`)
    parsed = split_agent_var(key)
    if parsed is not None:
        for raw_name, value in agent_vars_matching(parsed[0], path).items():
            if value:
                return value
    return ""


def write_value(key: str, value: str, path: str | Path | None = None) -> bool:
    """يضبط متغيّرًا واحدًا. يُرجع True عند النجاح."""
    if not is_allowed(key):
        raise ValueError(f"متغيّر غير مسموح بتعديله: {key}")
    # سطر جديد داخل القيمة = سطر جديد في الملف = إمكانية كتابة متغيّر آخر
    # (أو تعليق بقية الملف). المفتاح يأتي من رسالة تيليجرام، والرسائل تقبل
    # الأسطر المتعددة — فنرفضها هنا في نقطة واحدة تحمي كل المسارات.
    if any(ch in value for ch in ("\n", "\r", "\0")):
        raise ValueError("قيمة غير صالحة: تحتوي سطرًا جديدًا")
    target = env_path(path)
    lines = _read_lines(target)
    canonical = canonical_key(key)
    # الصيغ الأخرى لنفس المتغيّر (`GEMINI_API_KEY` مقابل `gemini_api_key`)
    # تُحذف في نفس الكتابة: بقاؤها يعني سطرين لنفس المفتاح، وأيّهما يُقرأ
    # يصير رهينة الترتيب — وهو ما يجعل البوت يبدو كأنه تجاهل تعديلك.
    duplicates: list[str] = []
    duplicates = same_var_variants(key, path)
    if duplicates:
        _write_atomic(target, _replace_in_lines(
            lines, duplicates, canonical, value.strip()))
    else:
        _write_atomic(target, _set_in_lines(lines, canonical, value.strip()))
    log.info("env: %s ← %s", canonical, _shorten(value))
    return True


def remove_value(key: str, path: str | Path | None = None) -> bool:
    """يحذف متغيّرًا (مثل مفتاح مزوّد لم يعد مستخدمًا)."""
    if not is_allowed(key):
        raise ValueError(f"متغيّر غير مسموح بحذفه: {key}")
    target = env_path(path)
    lines = _read_lines(target)
    names = [canonical_key(key)] + same_var_variants(key, path)
    if not any(line.strip().startswith(tuple(f"{n}=" for n in names))
               for line in lines):
        return False
    # نحذف كل الصيغ: لو بقيت واحدة يظل المفتاح يُرسَل ويبدو الحذف كاذبًا
    keep = [line for line in lines
            if not line.strip().startswith(tuple(f"{n}=" for n in names))]
    _write_atomic(target, keep)
    log.info("env: حُذف %s", ", ".join(names))
    return True


def _generic_key_owner(path: str | Path | None = None) -> str:
    """مزوّد المفتاح العام الموجود في `AI_API_KEY` (أو "" إن لم يُعرف)."""
    from . import ai as ai_layer

    for line in _read_lines(env_path(path)):
        stripped = line.strip()
        if stripped.startswith("AI_API_KEY="):
            return ai_layer.detect_provider_from_key(stripped.split("=", 1)[1].strip()) or ""
    return ""


def apply_provider(provider: str, model: str = "", api_key: str = "",
                   path: str | Path | None = None) -> list[str]:
    """العمليّة الكاملة التي يطلبها الأدمن: مزوّد (+ موديل + مفتاح) في `.env`.

    **نقطة دقيقة ومهمة**: لو بدّل الأدمن المزوّد بلا مفتاح جديد، والمفتاح
    العام (`AI_API_KEY`) يخصّ المزوّد القديم، فسيُرسَل مفتاح Gemini إلى Groq
    فيردّ 401 محيّرًا. لذلك نحذف المفتاح العام المتعارض، ونعتمد على مفتاح
    المزوّد الجديد إن كان محفوظًا باسمه (`<provider>_api_key`).

    يُرجع أسماء المتغيّرات التي تغيّرت — للعرض، بلا أي قيمة.
    """
    changed: list[str] = []
    write_value("AI_PROVIDER", provider, path)
    changed.append("AI_PROVIDER")
    if model.strip():
        write_value("AI_MODEL", model, path)
        changed.append("AI_MODEL")

    if api_key.strip():
        # التسمية المطلوبة: مفتاح المزوّد باسمه `<name>_api_key`، لا `AI_API_KEY`
        # العام. كتابته في العام كانت تجعل المفتاح «يعمل» ثم يضيع أثره عند
        # تبديل المزوّد، وتخالف العقد الذي بنيناه في المرحلة السابقة.
        generic_owner = _generic_key_owner(path)
        if generic_owner:
            # مفتاح عام معروف المالك لا يبقى بعد حفظ مفتاح باسم المزوّد:
            # إما أنه لمزوّد آخر (خطر إرساله بالغلط) أو لنفس المزوّد (سطر
            # مكرّر يُربك كل من يفتح الملف). الغامض وحده نتركه — لا نخمّن.
            remove_value("AI_API_KEY", path)
            changed.append("AI_API_KEY (حُذف — صار المفتاح باسم المزوّد)")
        specific = provider_key_name(provider)
        write_value(specific, api_key, path)
        changed.append(specific)
        return changed

    owner = _generic_key_owner(path)
    if owner and owner != provider:
        remove_value("AI_API_KEY", path)
        changed.append("AI_API_KEY (حُذف — كان لمزوّد آخر)")
    return changed


# ---------------------------------------------------------------------------
# إدارة المفاتيح: إضافة · تحديث · تسمية · حذف
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyResult:
    """نتيجة عملية على مفتاح — بلا قيمة المفتاح أبدًا."""

    ok: bool
    message: str
    changed: tuple[str, ...] = ()


def list_editable_keys(path: str | Path | None = None) -> list[str]:
    """كل المتغيّرات القابلة للتعديل الموجودة فعلًا في الملف (بلا قيم).

    نميّز ثلاث فئات لأن الشاشة تعالجها الشاشة: مفاتيح مزوّدين/وكلاء، عناوين
    وموديلات، ومتغيّرات `AI_*` العامة. الأهم: نُظهر **أي** متغيّر مفتاح
    موجود — مستقلًا عن كونه معروفًا في `PROVIDERS` — لأن مهمة المالك هي
    «أقدر أحدّث أو أعدّل أي مفتاح عندي»، ومفتاح غير معروف قد يكون لمزوّد
    أضافه يدويًا.
    """
    target = env_path(path)
    names: list[str] = []
    for line in _read_lines(target):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if is_allowed(name):
            names.append(name)
    return names


def same_var_variants(name: str, path: str | Path | None = None) -> list[str]:
    """أشكال **نفس** المتغيّر في الملف بأي حالة أحرف: `gemini_api_key` و`GEMINI_API_KEY`.

    **خطأ حقيقي وقع وأُصلح:** استخدمت `agent_vars_matching` هنا، وهي تُرجع
    كل متغيّرات الوكيل (`_api_key` و`_base_url` و`_model`). فكتابة
    `my_agent_base_url` كانت **تمسح مفتاح الوكيل وموديله** لأنهما «تكرار»
    بحسب ذلك الفحص الخاطئ. الفرق بين «نفس السطر بصيغة أخرى» و«سطر آخر
    لنفس الوكيل» هو أهم فرق في هذا الملف.
    """
    parsed = split_agent_var(name)
    if parsed is None:
        return []
    base, suffix = parsed
    mine = canonical_key(name)
    return [raw for raw in agent_vars_matching(base, path)
            if raw != mine and split_agent_var(raw) == (base.lower(), suffix)]


def explicit_variants(name: str, path: str | Path | None = None) -> list[str]:
    """أسماء الأسطر الموجودة فعلًا لنفس المتغيّر (بأي صيغة: جديدة أو قديمة)."""
    names = [canonical_key(name)] + same_var_variants(name, path)
    prefixes = tuple(f"{entry}=" for entry in names)
    found: list[str] = []
    for line in _read_lines(env_path(path)):
        stripped = line.strip()
        if stripped.startswith(prefixes):
            found.append(stripped.split("=", 1)[0].strip())
    return found


def key_state(name: str, path: str | Path | None = None) -> str:
    """حالة المفتاح كما يراها البوت: مضبوط · فارغ · مكرّر بصيغتين.

    «مكرّر» ليست تفصيلة شكلية: لو الملف فيه `gemini_api_key` قديم فارغ
    و`GEMINI_API_KEY` جديد مضبوط، فالقراءة قد تأخذ الفارغ فيظهر كأن لا مفتاح.
    """
    parsed = split_agent_var(name)
    if parsed is None:
        return "set" if read_value(name, path) else "empty"
    base, suffix = parsed
    variants = {raw: value
                for raw, value in agent_vars_matching(base, path).items()
                if split_agent_var(raw) == (base.lower(), suffix)}
    filled = [raw for raw, value in variants.items() if value]
    # التكرار الحقيقي = أكثر من سطر **بعد** التطبيع (لا اختلاف حالة الأحرف،
    # فذلك يُدمج تلقائيًا عند أول كتابة).
    distinct = {canonical_key(raw) for raw in variants}
    if len(distinct) > 1:
        return "duplicate"
    if filled:
        return "set"
    return "empty"


def update_key(name: str, value: str,
               path: str | Path | None = None) -> KeyResult:
    """يضبط قيمة متغيّر — مع التحقّق من أن التغيير صار فعّالًا فعلًا.

    **لماذا نتحقّق ونقرأ مرة أخرى؟** لأن البوت كان يكتب في `.env` ويقول
    «تم» بينما الذاكرة (أو سطر مكرّر بصيغة أخرى) تحتفظ بالقيمة القديمة.
    فنسجّل نجاحًا فقط إذا كانت القيمة المقروءة بعد الكتابة هي القيمة
    المطلوبة (أو مقنّعة منها نفسها للأسرار).
    """
    try:
        write_value(name, value, path)
    except ValueError as exc:
        return KeyResult(False, f"⚠️ {exc}")
    except OSError as exc:
        return KeyResult(False, f"⚠️ تعذّرت الكتابة في <code>.env</code> "
                                f"({type(exc).__name__})")

    canonical = canonical_key(name)
    reload_settings()
    current = read_value(canonical, path)
    if current != value.strip():
        return KeyResult(False,
                         "⚠️ كُتبت القيمة لكن القراءة أعادت شيئًا آخر — "
                         "راجع <code>.env</code> يدويًا (قد يكون فيه سطر مكرّر).",
                         (canonical,))
    return KeyResult(True, "✅ حُدِّث وفُعِّل فورًا", (canonical,))


def rename_key(old_name: str, new_name: str,
               path: str | Path | None = None) -> KeyResult:
    """يغيّر اسم متغيّر ويحافظ على قيمته — بلا فقدان المفتاح.

    **الترتيب مقصود**: نكتب الجديد **قبل** حذف القديم. لو حذفنا أولًا وانقطعت
    العملية (أو رفض الملف الكتابة) لضاع المفتاح ولم يبقَ ما نرجعه.
    """
    old = (old_name or "").strip()
    new = (new_name or "").strip()
    if not old or not new or old == new:
        return KeyResult(False, "⚠️ اختر اسمين مختلفين")
    if not is_allowed(old):
        return KeyResult(False, "⚠️ الاسم القديم غير قابل للتعديل")
    if not is_allowed(new):
        return KeyResult(False, "⚠️ الاسم الجديد غير مسموح "
                                "(اقرأ قواعد التسمية)")
    if split_agent_var(new) is None:
        # `AI_MODEL` و`AI_PROVIDER` إعدادات عامة لا متغيّرات مفتاح؛ تغيير
        # تسمية مفتاح إليها يُفسد الإعداد ويكسر البوت.
        return KeyResult(False, "⚠️ الاسم الجديد لازم ينتهي بـ "
                                "<code>_api_key</code> (أو <code>_base_url</code> "
                                "أو <code>_model</code>)")
    value = read_value(old, path)
    if not value:
        return KeyResult(False, "⚠️ المتغيّر القديم لا قيمة له — لا شيء أنقله")
    # «مستخدم بالفعل» يعني سطرًا آخر — لا سطر المصدر نفسه بصيغة قديمة
    # (`AI_API_KEY_GROQ` ← `groq_api_key` هو نقل مشروع لنفس المفتاح).
    source_lines = set(explicit_variants(old, path))
    if [raw for raw in explicit_variants(new, path) if raw not in source_lines]:
        return KeyResult(False, "⚠️ الاسم الجديد مستخدم بالفعل — "
                                "اختر اسمًا آخر أو احذف القديم أولًا")
    # كتابة واحدة: (القديم وكل صيغه) يُحذف، والجديد يُكتب — بلا نافذة
    # يمكن أن يوجد فيها متغيّران بنفس القيمة أو لا يوجد أي منهما.
    try:
        target = env_path(path)
        old_names = [canonical_key(old)] + same_var_variants(old, path)
        # المتغيّر الهدف قد يكون له أشكال بحالة أحرف مختلفة (`MY_KEY_API_KEY`)
        # — نُزيلها أيضًا وإلا بقيت نسخة قديمة تُقرأ بدل الجديدة.
        new_names = [canonical_key(new)] + same_var_variants(new, path)
        old_names += [raw for raw in new_names if raw not in old_names]
        _write_atomic(target, _replace_in_lines(
            _read_lines(target), old_names, canonical_key(new), value))
    except (OSError, ValueError) as exc:
        return KeyResult(False, f"⚠️ تعذّر تغيير الاسم ({type(exc).__name__})")
    reload_settings()
    return KeyResult(True, "✅ تغيّر الاسم والقيمة محفوظة كما هي",
                     (canonical_key(old), canonical_key(new)))


def delete_key(name: str, path: str | Path | None = None) -> KeyResult:
    """يحذف متغيّرًا بكل صيغه (`gemini_api_key` و`GEMINI_API_KEY`)."""
    if not is_allowed(name):
        return KeyResult(False, "⚠️ هذا المتغيّر غير قابل للحذف من هنا")
    removed: list[str] = []
    # نفس المتغيّر بكل أشكاله فقط — لا `_base_url` ولا `_model` (تلك تُحذف
    # بـ`delete_agent` وحدها، وبموافقة صريحة من الأدمن).
    targets = [canonical_key(name)] + same_var_variants(name, path)
    try:
        for target in targets:
            if remove_value(target, path):
                removed.append(target)
    except (OSError, ValueError) as exc:
        return KeyResult(False, f"⚠️ تعذّر الحذف ({type(exc).__name__})",
                         tuple(removed))
    if not removed:
        return KeyResult(False, "⚠️ المتغيّر غير موجود في الملف أصلًا")
    reload_settings()
    return KeyResult(True, "🗑️ حُذف — وأُعيد تحميل الإعدادات", tuple(removed))


def delete_agent(name: str, path: str | Path | None = None) -> KeyResult:
    """يحذف وكيلًا كاملًا: متغيّراته الثلاثة بكل صيغها.

    لا نحذف شيئًا من ملف `.env` بلا أن نذكره بالاسم في الردّ — الملف ملك
    الأدمن، ومسح شيء منه بلا إخباره خطأ حتى لو كان «تنظيفًا».
    """
    removed: list[str] = []
    for suffix in _AGENT_SUFFIXES:
        result = delete_key(f"{name}{suffix}", path)
        removed += list(result.changed)
    if not removed:
        return KeyResult(False, "⚠️ لا يوجد متغيّرات بهذا الاسم")
    return KeyResult(True, "🗑️ حُذف الوكيل ومتغيّراته", tuple(removed))


def reload_settings() -> None:
    """يعيد بناء `settings` في الذاكرة من البيئة الحالية — بلا إعادة تشغيل.

    `Settings` مجمّدة (`frozen`)، ولذلك نستبدل الكائن بدل تعديله: من يفعل
    `from ..config import settings` يحمل مرجعًا قديمًا، فنُحدّث كل مرجع
    تحمله الموديولات:
    `config.settings` و`ai.settings` و`user_ai.settings` …
    """
    fresh = _settings_from_file(env_path())
    config.settings = fresh
    updated = 0
    for module_name in ("bot.services.ai", "bot.services.user_ai",
                        "bot.services.settings_service", "bot.services.embeddings"):
        module = _module(module_name)
        if module is not None and getattr(module, "settings", None) is not None:
            module.settings = fresh          # type: ignore[attr-defined]
            updated += 1
    # المفاتيح الخاصة بكل مزوّد تُقرأ من البيئة عند كل نداء، فلا تحتاج تحديثًا.
    # أما الوكلاء الجدد فيحتاجون التسجيل في `PROVIDERS` ليظهروا كمزوّدين.
    try:
        added = _module("bot.services.ai").register_agents()   # type: ignore[union-attr]
        if added:
            log.info("env: أُضيف وكلاء: %s", ", ".join(added))
    except Exception as exc:      # pragma: no cover
        log.warning("تعذّر تسجيل الوكلاء بعد التحديث: %s", type(exc).__name__)
    log.info("env: أُعيد تحميل الإعدادات (%d موديول)", updated)


def _settings_from_file(path: Path):
    """يبني `Settings` من الملف **كما هو الآن**.

    مهم: `load_settings` تستخدم `os.environ.setdefault`، فالبيئة تحتفظ
    بالقيمة القديمة ولا تتحدّث. لذلك نُحدّث `os.environ` بقيم الملف أولًا،
    وإلا لظهر للمستخدم نجاح الكتابة وبقي البوت على الإعداد القديم.
    """
    for line in _read_lines(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        # الملف هو المصدر: نكتب **كل** قيمة موجودة فيه على البيئة.
        # الشرط القديم `name in os.environ` كان يترك القيم القديمة تغلب
        # (فلا يتغيّر `AI_PROVIDER` ولا تُقرأ متغيّرات الوكيل الجديد أصلًا
        # لأنه لم يكن في البيئة) — فيُحفظ التعديل ويبقى البوت على القديم.
        if name.isidentifier():
            os.environ[name] = value.strip().strip('"').strip("'")
    return config.load_settings(path)


def _module(name: str):
    import sys

    return sys.modules.get(name)


def patch_settings(**changes) -> None:
    """تحديث فوري للذاكرة حتى لو تعذّرت كتابة الملف (سيرفر للقراءة فقط)."""
    fresh = replace(config.settings, **changes)
    config.settings = fresh
    for module_name in ("bot.services.ai", "bot.services.user_ai"):
        module = _module(module_name)
        if module is not None and getattr(module, "settings", None) is not None:
            module.settings = fresh          # type: ignore[attr-defined]


def settings_for(*, ai_provider: str | None = None, ai_model: str | None = None) -> dict:
    """تغييرات جاهزة لـ `patch_settings` — تُستدعى بعد الكتابة الناجحة."""
    changes: dict[str, str] = {}
    if ai_provider is not None:
        changes["ai_provider"] = ai_provider
    if ai_model is not None:
        changes["ai_model"] = ai_model
    return {key: value for key, value in changes.items() if value is not None}


def list_provider_keys(path: str | Path | None = None) -> list[str]:
    """أسماء مفاتيح المزوّدين الموجودة في الملف — **بلا قيمها**."""
    target = env_path(path)
    names: list[str] = []
    for line in _read_lines(target):
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            continue
        name = stripped.split("=", 1)[0].strip()
        if name.endswith(_AGENT_SUFFIXES) or name.startswith("AI_API_KEY_"):
            names.append(name)
    return names


def restore_backup(name: str = "", path: str | Path | None = None) -> str:
    """يُرجع `.env` إلى نسخة سابقة. يُرجع اسم النسخة التي استُعيدت.

    لماذا؟ لأن أسوأ سيناريو في الكتابة الآلية هو أن يكتب الأدمن قيمة تُبطل
    الإقلاع (مزوّد غير موجود، أو موديل خاطئ) ثم لا يستطيع فتح البوت ليُصلحها.
    فالرجوع إلى نسخة معروفة الصلاح يجب أن يكون بضغطة من نفس الشاشة.
    """
    target = env_path(path)
    candidates = list_backups(target)
    if not candidates and not backup_path(target).exists():
        raise FileNotFoundError("لا توجد نسخة احتياطية")

    if name:
        chosen = next((item for item in candidates if item.name == name), None)
        if chosen is None:
            raise FileNotFoundError("النسخة المطلوبة غير موجودة")
    else:
        chosen = candidates[0] if candidates else backup_path(target)

    lines = _read_lines(chosen)
    current = _read_lines(target)
    # نحتفظ بالحالي أولًا حتى يكون الرجوع نفسه قابلًا للرجوع
    if current:
        _backup(target)
    _write_atomic(target, lines)
    reload_settings()
    log.info("env: استُعيدت نسخة %s", chosen.name)
    return chosen.name


def escape_html(text: str) -> str:
    """تهريب صغير محلي — لتفادي استيراد aiogram في طبقة الخدمات."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


