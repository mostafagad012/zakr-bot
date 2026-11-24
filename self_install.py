#!/usr/bin/env python3
"""نظام «التثبيت الذاتي» لبوت ذاكر — يحوّل أي منصّة إلى منصّة تعمل.

**المشكلة التي يحلّها:** المنصّات مثل Wispbyte / Railway / Render / Koyeb / Pterodactyl
تعطيك حاوية **بلا root وبلا apt**. كتابة `apt-get install tesseract` في أمر
التشغيل تنتهي بـ `Permission denied`. والنتيجة المعتادة: بوت «يشتغل» على
جهازك ويموت على السيرفر.

**الحلّ (مُختبَر، لا نظري):** كل ما يحتاجه البوت فعلًا نوعان:

  ① حزم بايثون      → pip، ولا تحتاج root إطلاقًا.
  ② برامج نظام      → tesseract + poppler (لقراءة الملفات المصوّرة فقط).
                       تُنزَّل كملفات `.deb` بـ`apt-get download` (لا يحتاج
                       root)، تُستخرج بـ`dpkg-deb -x` داخل `.ocr/`، ويُشغَّل
                       البوت مع `PATH`/`LD_LIBRARY_PATH` موجّهين إليها.

بهذا **لا يوجد أمر واحد يحتاج صلاحية مدير** — وهو شرط الوجود على أي لوحة.

⚠️ صدق ضروري (لا أخفي حدًّا): حزم `.deb` مبنية لمعمارية الصورة ونظامها
   (x86_64/glibc). على Alpine (musl) لا تعمل — ونقول ذلك بصراحة بدل خطأ
   غامض. وعلى Termux/أندرويد المسار مختلف تمامًا (`pkg`)، وهو مُعالَج هناك.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from setup_common import (  # noqa: E402
    C,
    bad,
    info,
    is_container,
    is_termux,
    is_vercel,
    ok,
    platform_name,
    say,
    warn,
)

# ────────────────────────────────────────────────── حزم النظام المطلوبة
# tesseract-ocr-osd **إجباري** لا تجميلي: بدونه يفشل كشف لغة الصفحة، فيسقط
# كل ملف على اللغة الافتراضية ويخرج نص PDF الإنجليزي مشوّهًا بالعربية.
SYSTEM_PACKAGES = (
    "tesseract-ocr",
    "tesseract-ocr-ara",
    "tesseract-ocr-eng",
    "tesseract-ocr-osd",
    "poppler-utils",
)

# حزم بايثون التي **بدونها لا يبدأ البوت** — يُتحقّق منها بالاستيراد لا بالاسم
# (اسم الحزمة ≠ اسم الموديول: Pillow → PIL، SQLAlchemy → sqlalchemy).
BOOT_MODULES = (
    ("aiogram", "aiogram"),
    ("SQLAlchemy", "sqlalchemy"),
    ("aiosqlite", "aiosqlite"),
    ("pypdf", "pypdf"),
    ("httpx", "httpx"),
)
# إضافات اختيارية لا تُحمّل على كل استضافة: تقلّل حجم الإقلاع والقرص.
OPTIONAL_MODULES = (
    ("Pillow", "PIL"),
    ("numpy", "numpy"),
    ("pytesseract", "pytesseract"),
    ("pdf2image", "pdf2image"),
    ("onnxruntime", "onnxruntime"),
    ("openpyxl", "openpyxl"),
    ("asyncpg", "asyncpg"),
)


# ─────────────────────────────────────────────────────────── أدوات صغيرة
def have(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def run(cmd: list[str], *, timeout: int = 900, cwd: Path | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd or ROOT)
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def python_ok() -> bool:
    """إصدارات الإنتاج المدعومة والمختبرة للحزم الثقيلة: Python 3.11 أو 3.12."""
    return (3, 11) <= sys.version_info[:2] < (3, 13)


def _pip(*args: str) -> tuple[bool, str]:
    return run([sys.executable, "-m", "pip", "install", "--no-input", "--no-cache-dir", *args])


def ensure_pip() -> bool:
    """يضمن pip: الحاضر ← ensurepip ← get-pip.py. لا يفترض شيئًا."""
    if run([sys.executable, "-m", "pip", "--version"], timeout=60)[0]:
        return True
    info("pip غير موجود — تهيئته …")
    for args in (["ensurepip", "--upgrade"], ["ensurepip", "--upgrade", "--default-pip"]):
        if run([sys.executable, "-m", *args], timeout=300)[0] and run(
            [sys.executable, "-m", "pip", "--version"], timeout=60
        )[0]:
            ok("pip جاهز (ensurepip)")
            return True
    import urllib.request

    tmp = ROOT / ".get-pip.py"
    try:
        info("تنزيل get-pip.py …")
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", tmp)  # noqa: S310
        if run([sys.executable, str(tmp)], timeout=600)[0]:
            ok("pip جاهز (get-pip)")
            return True
    except Exception as exc:  # noqa: BLE001
        warn(f"تعذّر get-pip: {exc}")
    finally:
        tmp.unlink(missing_ok=True)
    return run([sys.executable, "-m", "pip", "--version"], timeout=60)[0]


# ───────────────────────────────────────── ① حزم بايثون (إجباري)
def is_wispbyte_mode() -> bool:
    """كشف وضع Wispbyte/Pterodactyl أو تفعيله صراحةً."""
    return (
        os.environ.get("ZAKR_WISPBYTE_MODE") == "1"
        or os.environ.get("WISPBYTE") == "1"
        or Path("/home/container").is_dir()
    )


WISPBYTE_MODULES = (
    ("aiogram", "aiogram"),
    ("SQLAlchemy", "sqlalchemy"),
    ("aiosqlite", "aiosqlite"),
    ("Alembic", "alembic"),
    ("pypdf", "pypdf"),
    ("httpx", "httpx"),
    ("asyncpg", "asyncpg"),
    ("Pillow", "PIL"),
    ("numpy", "numpy"),
    ("pytesseract", "pytesseract"),
    ("pdf2image", "pdf2image"),
    ("openpyxl", "openpyxl"),
    ("onnxruntime", "onnxruntime"),
    ("fastembed", "fastembed"),
)


def install_python_deps() -> int:
    """يثبّت حاجيات البوت ويتحقق من كل ميزة؛ وضع Wispbyte صارم بالكامل."""
    say()
    say(f"{C.dim}══ ① حاجيات بايثون ══{C.reset}")

    if not ensure_pip():
        bad("لا يوجد pip ولا يمكن تهيئته — لا أستطيع تثبيت أي حاجة.")
        info("على Termux: pkg install python-pip")
        return 1

    if not python_ok():
        bad(f"إصدار Python {sys.version.split()[0]} غير مدعوم لمجموعة Wispbyte الكاملة.")
        info("اختر Python 3.11 أو 3.12 من Startup / Docker Image في لوحة الاستضافة.")
        return 1

    if is_wispbyte_mode():
        requirements = ROOT / "requirements-wispbyte.txt"
        if not requirements.is_file():
            bad("requirements-wispbyte.txt مفقود — لا يمكن تجهيز كل ميزات Wispbyte.")
            return 1
        info("Wispbyte: تثبيت مجموعة الإنتاج الكاملة (OCR + Excel + PostgreSQL + ONNX + البحث الدلالي) …")
        attempts = (
            ["-r", str(requirements)],
            ["--prefer-binary", "-r", str(requirements)],
        )
        last_output = ""
        installed = False
        for args in attempts:
            good, last_output = _pip(*args)
            if good:
                installed = True
                break
            warn("محاولة تثبيت كاملة لم تنجح؛ إعادة المحاولة باستخدام العجلات الجاهزة …")
        if not installed:
            say(last_output[-5000:])
            bad("فشل تثبيت مجموعة Wispbyte الكاملة. لن يبدأ البوت بنواقص.")
            return 1
        import importlib
        missing = []
        for label, module in WISPBYTE_MODULES:
            try:
                importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001
                missing.append(f"{label} ({type(exc).__name__}: {exc})")
        if missing:
            bad("حزم لم تجتز الاستيراد الفعلي بعد التثبيت: " + "; ".join(missing))
            return 1
        ok(f"كل مكتبات Python المطلوبة مثبتة ({len(WISPBYTE_MODULES)} مجموعة).")
        return 0

    # المسار المفضّل: المُثبِّت الموحّد (يعرف Termux وpkg وعجلات TUR)
    deps = ROOT / "setup_deps.py"
    if deps.is_file():
        info("تشغيل المُثبِّت الموحّد setup_deps.py …")
        good, out = run([sys.executable, str(deps)])
        if not good:
            say(out[-2000:])
            warn("المُثبِّت الموحّد لم يُكمل — سأكمل بالمسار المباشر أدناه.")

    # تثبيت مباشر لما ينقص فقط (لا نُعيد تثبيت الموجود — أسرع وأأمن)
    missing = [label for label, mod in BOOT_MODULES if not have(mod)]
    if missing:
        info(f"حزم ناقصة للإقلاع: {', '.join(missing)} — تثبيت …")
        good, out = _pip("-r", "requirements.txt")
        if not good:
            # تجربة أخيرة: عجلات جاهزة فقط (تفشل البناءات على الحاويات الضيقة)
            warn("التثبيت من القائمة فشل — محاولة بعجلات جاهزة فقط …")
            good, out = _pip("--only-binary=:all:", "-r", "requirements.txt")
        if not good:
            say(out[-2500:])

    # الإضافات الثقيلة حسب الحاجة فقط. PostgreSQL يُفعّل تلقائيًا عند اختيار
    # DATABASE_URL، بينما OCR/Excel يحتاجان طلبًا صريحًا لتقليل حجم الاستضافة.
    optional_requested = (
        os.environ.get("ZAKR_INSTALL_OPTIONAL") == "1"
        or os.environ.get("ZAKR_INSTALL_OCR") == "1"
    )
    database_url = os.environ.get("DATABASE_URL", "").lower()
    requested = {"asyncpg"} if database_url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")) else set()
    if optional_requested:
        requested.update({"Pillow", "numpy", "pytesseract", "pdf2image", "openpyxl"})
    for label, mod in OPTIONAL_MODULES:
        if label not in requested or have(mod):
            continue
        good, out = _pip(label)
        if not good:
            info(f"إضافة اختيارية غير متوفّرة: {label} — {out[-500:]}")

    # ── التحقّق النهائي: الاستيراد الفعلي
    still = [label for label, mod in BOOT_MODULES if not have(mod)]
    if still:
        bad(f"حزم أساسية لا تُستورد بعد التثبيت: {', '.join(still)}")
        info("البوت لن يبدأ بدونها. جرّب يدويًا: pip install -r requirements.txt")
        return 1
    ok(f"حاجيات بايثون جاهزة ({len(BOOT_MODULES)} حزمة إقلاع متحقَّقة بالاستيراد)")
    return 0


# ─────────────────────────── ② برامج النظام (tesseract/poppler) بلا root
def _has_ocr_binaries() -> tuple[bool, bool]:
    """(tesseract يعمل؟, pdftoppm يعمل؟) — بالتنفيذ لا بـ`which`."""
    from bot.services import ocr as ocr_service

    ocr_service._binary_works.cache_clear()      # noqa: SLF001 — نتحقّق طازجًا
    return (
        ocr_service._binary_works("tesseract"),        # noqa: SLF001
        ocr_service._binary_works("pdftoppm", "-v"),   # noqa: SLF001
    )


def ensure_system_deps() -> int:
    """يوصل tesseract/poppler إلى البوت على أي منصّة — بلا root إن لزم.

    الترتيب مقصود: الأرخص والأكثر أمانًا أولًا.
      ① مثبّتة في النظام أصلًا → لا شيء.
      ② Termux → pkg (هو مدير الحزم الصحيح هناك، لا apt).
      ③ apt-get download + dpkg-deb -x داخل `.ocr/` → لا يحتاج root.
      ④ فشل كل ذلك → البوت يعمل، والصور وحدها لا تُقرأ (يُقال بصراحة).
    """
    say()
    say(f"{C.dim}══ ② برامج النظام (قراءة الملفات المصوّرة) ══{C.reset}")

    tesseract_ok, poppler_ok = _has_ocr_binaries()
    if tesseract_ok and poppler_ok:
        ok("tesseract و pdftoppm يعملان — لا حاجة لأي تثبيت")
        if is_wispbyte_mode():
            _ensure_arabic_data()
            from bot.services import ocr as ocr_service
            langs = set(ocr_service.installed_languages())
            missing_langs = {"ara", "eng", "osd"} - langs
            if missing_langs:
                bad("لغات OCR المطلوبة غير مكتملة: " + ", ".join(sorted(missing_langs)))
                return 1
        return 0

    plat = platform_name()
    info(f"المنصّة: {plat}")

    # ── Termux / أندرويد: المسار الصحيح هو pkg
    if is_termux():
        pairs = (("tesseract", "tesseract"), ("poppler", "pdftoppm"))
        need = [
            pkg for pkg, binary in pairs
            if not (tesseract_ok if binary == "tesseract" else poppler_ok)
        ]
        info(f"تثبيت من pkg: {' '.join(need)} …")
        good, out = run(["pkg", "install", "-y", *need], timeout=1800)
        if not good:
            say(out[-1500:])
            warn("فشل pkg — جرّب يدويًا: pkg install tesseract poppler")
        else:
            ok("حزم أندرويد مثبّتة")
            from bot.services import ocr as ocr_service

            ocr_service.refresh_local_ocr_env()
        # بيانات اللغة العربية لا تأتي مع حزمة tesseract على أندرويد
        _ensure_arabic_data()
        return 0

    # ── بقية المنصّات: التنزيل المحلي بلا root
    fetcher = ROOT / "ocr_fetch.py"
    strict = is_wispbyte_mode()
    if not strict and (os.environ.get("ZAKR_SKIP_OCR") == "1" or os.environ.get("ZAKR_INSTALL_OCR") != "1"):
        info("OCR اختياري ومُعطّل افتراضيًا لتقليل الحجم. فعّله بـ ZAKR_INSTALL_OCR=1")
        return 0
    if strict:
        info("Wispbyte: OCR إلزامي — لن نسمح بإقلاع ناقص الميزات.")
    if not fetcher.is_file():
        if strict:
            bad("ملف ocr_fetch.py مفقود؛ OCR لا يمكن تجهيزه.")
            return 1
        warn("ملف ocr_fetch.py غير موجود — لا أستطيع تنزيل OCR")
        return 0

    info("تنزيل tesseract وpoppler محليًا (بلا root، بلا apt install) …")
    good, out = run([sys.executable, str(fetcher)], timeout=1800)
    if good:
        # ⚠️ لا بدّ من إعادة قراءة المسارات: استيراد `bot.services.ocr` حدث
        # قبل وجود `.ocr/`، فبقيت المخابئ تقول «غير متاح» رغم نجاح التنزيل.
        from bot.services import ocr as ocr_service

        ocr_service.refresh_local_ocr_env()
        # إثبات لا افتراض: نُعيد الفحص بعد التنزيل ونقول للمستخدم الحقيقة.
        if ocr_service.is_available():
            ok("قارئ الملفات المصوّرة جاهز في .ocr/ — ويعمل فعلًا")
        else:
            # التنزيل نجح والاستخراج نجح، لكن الملف لا يُنفَّذ: أشهر سبب هو
            # صلاحية التنفيذ (نظام ملفات مركّب noexec) أو معمارية مختلفة.
            bad("نُزِّل tesseract لكنه لا يُنفَّذ على هذه الصورة.")
            info("السبب الأشهر: نظام ملفات يمنع التنفيذ (noexec) أو معمارية غير x86_64.")
            info("البوت يعمل — والصور وحدها لن تُقرأ. استخدم ZAKR_SKIP_OCR=1 للإسكات.")
        _ensure_arabic_data()
        if strict:
            tesseract_ok, poppler_ok = _has_ocr_binaries()
            langs = ocr_service.installed_languages()
            missing_langs = {"ara", "eng", "osd"} - set(langs)
            if not (tesseract_ok and poppler_ok) or missing_langs:
                bad(f"OCR غير مكتمل على Wispbyte. لغات ناقصة: {', '.join(sorted(missing_langs)) or 'لا شيء'}")
                return 1
        return 0

    if strict:
        # قد تكون الملفات التنفيذية نُزّلت بنجاح لكن ocr_fetch رفض النتيجة
        # بسبب غياب language packs؛ نحاول استكمالها ثم نتحقق من الأدوات فعليًا.
        try:
            from bot.services import ocr as ocr_service
            ocr_service.refresh_local_ocr_env()
            _ensure_arabic_data()
            tesseract_ok, poppler_ok = _has_ocr_binaries()
            langs = set(ocr_service.installed_languages())
            missing_langs = {"ara", "eng", "osd"} - langs
            if tesseract_ok and poppler_ok and not missing_langs:
                ok("اكتمل OCR بعد استكمال بيانات اللغات والتحقق الفعلي.")
                return 0
        except Exception as exc:  # noqa: BLE001
            info(f"فشل التحقق النهائي من OCR: {type(exc).__name__}: {exc}")
        say(out[-2500:])
        bad("تعذّر تثبيت Tesseract/Poppler أو بيانات اللغات كاملة على Wispbyte — لن يبدأ البوت.")
        info("تأكد أن صورة الحاوية Debian/Ubuntu وتحتوي apt-get وdpkg-deb وأن الخطة تسمح بالتنزيل والتنفيذ.")
        return 1
    say(out[-2500:])
    warn("تعذّر توفير tesseract — البوت يعمل، والملفات المصوّرة لن تُقرأ.")
    if is_container() or is_vercel():
        info("لوحتك تمنع تثبيت حزم النظام. الحلّ: أضف tesseract إلى صورة Docker")
        info("إن كانت لوحتك تدعم Dockerfile (Dockerfile المشروع يفعله تلقائيًا)،")
        info("أو اختر صورة/استضافة تسمح بـapt. ملفات PDF النصّية تعمل بلا OCR.")
    info("للتخطّي دائمًا وبصمت: أضف ZAKR_SKIP_OCR=1 إلى متغيّرات البيئة.")
    return 0


def _ensure_arabic_data() -> None:
    """يضمن بيانات Tesseract للغات العربية والإنجليزية وOSD بلا root."""
    import urllib.request

    from bot.services import ocr as ocr_service

    try:
        installed = set(ocr_service.installed_languages())
    except Exception:  # noqa: BLE001
        installed = set()

    target = ocr_service.tessdata_dir()
    if target is None or not target.is_dir():
        target = ocr_service.OCR_DIR / "usr" / "share" / "tesseract-ocr" / "5" / "tessdata"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warn(f"لم أستطع تجهيز مجلد بيانات OCR: {exc}")
        return

    for language in ("ara", "eng", "osd"):
        if language in installed:
            ok(f"بيانات OCR {language} متوفّرة")
            continue
        url = f"https://github.com/tesseract-ocr/tessdata_fast/raw/main/{language}.traineddata"
        dest = target / f"{language}.traineddata"
        try:
            info(f"تنزيل بيانات OCR للغة {language} …")
            urllib.request.urlretrieve(url, dest)  # noqa: S310
            if dest.stat().st_size > 100_000:
                ok(f"بيانات OCR {language} جاهزة")
            else:
                dest.unlink(missing_ok=True)
                warn(f"ملف بيانات OCR {language} غير مكتمل")
        except Exception as exc:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            warn(f"تعذّر تنزيل بيانات OCR {language}: {type(exc).__name__}")


# ───────────────────────────────────────────── ③ فالidation نهائي
def verify_ready() -> int:
    """يتأكد أن البوت **يبدأ فعلًا** — قبل أن نسلّمه للمستخدم أو للوحة."""
    say()
    say(f"{C.dim}══ ③ التحقّق النهائي ══{C.reset}")
    problems: list[str] = []

    if not have("aiogram"):
        problems.append("aiogram غير موجود")
    if not have("sqlalchemy"):
        problems.append("SQLAlchemy غير موجود")

    # استيراد البوت كاملًا — يكشف انكسارًا في أي موجّه أو خدمة
    try:
        sys.path.insert(0, str(ROOT))
        import bot.main  # noqa: F401

        ok(f"البوت يُستورد سليمًا ({len(bot.main.ROUTER_ORDER)} موجّهًا)")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"تعذّر استيراد bot.main: {type(exc).__name__}: {exc}")

    from bot.services import ocr as ocr_service

    if ocr_service.PIL_AVAILABLE and ocr_service.is_available():
        langs = ocr_service.installed_languages()
        ok("قراءة الملفات المصوّرة جاهزة" + (f" — اللغات: {', '.join(langs)}" if langs else ""))
        if "ara" not in langs:
            warn("العربية غير متوفّرة في tessdata — OCR العربي ضعيف")
    else:
        warn(f"قراءة الملفات المصوّرة معطّلة: {ocr_service.missing_reason()}")
        info("الرفع وكل الميزات الأخرى تعمل — الصور فقط لا تُقرأ.")

    if problems:
        for item in problems:
            bad(item)
        return 1
    return 0


# ─────────────────────────────────────────── تقرير المنصّة (للتشخيص)
def platform_report() -> None:
    say()
    say(f"{C.dim}══ المنصّة ══{C.reset}")
    info(f"الاسم: {platform_name()}")
    info(f"بايثون: {sys.version.split()[0]}  ({sys.executable})")
    info(f"المشروع: {ROOT}")
    info(f"root: {'نعم' if os.geteuid() == 0 else 'لا'}")
    for tool in ("apt-get", "apt", "dpkg-deb", "apk", "pkg", "bash"):
        info(f"{tool}: {shutil.which(tool) or '—'}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in argv or os.environ.get("ZAKR_QUIET") == "1"

    say(f"{C.bold}╔══════════════════════════════════════════╗{C.reset}")
    say(f"{C.bold}║  ذاكر — تجهيز المنصّة (self-install)     ║{C.reset}")
    say(f"{C.bold}╚══════════════════════════════════════════╝{C.reset}")

    if not quiet:
        platform_report()

    if "--check" in argv:
        return verify_ready()

    strict = is_wispbyte_mode()
    if strict:
        os.environ["ZAKR_WISPBYTE_MODE"] = "1"
        os.environ["ZAKR_INSTALL_OCR"] = "1"
        os.environ.pop("ZAKR_SKIP_OCR", None)
    rc = install_python_deps()
    if rc != 0:
        return rc
    rc = ensure_system_deps()
    if rc != 0:
        return rc
    rc = verify_ready()
    if rc != 0:
        return rc
    if strict:
        ok("Wispbyte preflight ناجح: جميع الاعتماديات وميزات OCR جاهزة.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
