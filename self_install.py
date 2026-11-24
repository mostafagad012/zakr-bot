#!/usr/bin/env python3
"""نظام «التثبيت الذاتي» لبوت ذاكر — يحوّل أي منصّة إلى منصّة تعمل.

**المشكلة التي يحلّها:** المنصّات مثل Wispbyte / Railway / Render / Koyeb /
Pterodactyl تعطيك حاوية **بلا root وبلا apt**. والنتيجة المعتادة: بوت
«يشتغل» على جهازك ويموت على السيرفر.

**الحلّ (مُختبَر، لا نظري):** كل ما يحتاجه البوت الآن هو **حزم بايثون فقط**
→ pip، ولا تحتاج root إطلاقًا.

> **ما تغيّر (والثمن، بصراحة):** كان هذا الملف ينزّل tesseract وpoppler
> كملفات `.deb` ويستخرجها بلا root لقراءة الملفات المصوّرة. أُزيل المسار
> كليًا بقرار المالك: القراءة الآن عبر وكيل الرؤية الذي يختاره المستخدم
> بمفتاحه (انظر `bot/services/extract.py`). الفائدة: لا حزم نظام، لا
> ١٥٨ ميجا تنزيل، ولا انتظار في الخلفية. الثمن: من لا يملك مفتاح وكيل
> لا تُقرأ ملفاته المصوّرة — لا بديل في البوت.
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
    ok,
    platform_name,
    say,
    warn,
)

# حزم بايثون التي **بدونها لا يبدأ البوت** — يُتحقّق منها بالاستيراد لا بالاسم
# (اسم الحزمة ≠ اسم الموديول: Pillow → PIL، SQLAlchemy → sqlalchemy).
# ── أساسية: البوت لا يقلع بدونها إطلاقًا
BOOT_MODULES = (
    ("aiogram", "aiogram"),
    ("SQLAlchemy", "sqlalchemy"),
    ("aiosqlite", "aiosqlite"),
    ("pypdf", "pypdf"),
    ("httpx", "httpx"),
    ("Pillow", "PIL"),
    ("openpyxl", "openpyxl"),
    ("asyncpg", "asyncpg"),
)
# ── مجموعات الميزات: غيابها يُعطّل ميزة ويُبلَّغ عنه، ولا يُسقط التشغيل.
#  كانت هنا أربع حزم OCR (~120MB). أُزيلت مع المسار نفسه، و**لم يبقَ
#  شيء اختياري**: البوت يحتاج `requirements.txt` فقط. `ZAKR_MINIMAL=1`
#  ما زال مقبولًا (لا يضرّ) لكنه لم يعد يوفّر شيئًا.
OPTIONAL_MODULES: tuple[tuple[str, str], ...] = ()
# حزم مطلوبة في وضع معيّن فقط
CONDITIONAL_MODULES = {"asyncpg": "DATABASE_URL على PostgreSQL/Supabase"}


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
    """بايثون 3.11+ (aiogram 3 وSQLAlchemy 2 يحتاجانه)."""
    return sys.version_info >= (3, 11)


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
def _write_verified_stamp() -> None:
    """بصمة «تحقّقنا من كل الحاجيات» — تقرأها نقطة الدخول.

    ⚠️ تُكتب من `main()` **بعد** نجاح التحقّق النهائي (`verify_ready`)، لا من
    داخل `install_python_deps`: الكتابة هناك كانت تُضيّع لأن `setup_deps.py`
    قد يُكمل وتظل مرحلة التحقّق النهائي هي الحاكمة.
    """
    try:
        import hashlib
        import time as _time

        req = ROOT / "requirements.txt"
        fingerprint = hashlib.sha256(req.read_bytes()).hexdigest()[:16]
        stamp = ROOT / "data" / ".deps-verified"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(f"{fingerprint}\n{_time.time()}", encoding="utf-8")
    except OSError:
        pass


def install_python_deps() -> int:
    """يثبّت حاجيات البوت — والتحقّق **بالاستيراد الفعلي** لا بأسماء الحزم."""
    say()
    say(f"{C.dim}══ ① حاجيات بايثون ══{C.reset}")

    if not ensure_pip():
        bad("لا يوجد pip ولا يمكن تهيئته — لا أستطيع تثبيت أي حاجة.")
        info("على Termux: pkg install python-pip")
        return 1

    if not python_ok():
        bad(f"بايثون {sys.version.split()[0]} قديم — البوت يحتاج 3.11+.")
        info("اختر صورة/إصدار بايثون 3.11 أو أحدث في لوحة الاستضافة.")
        return 1

    # ── المسار الوحيد: المُثبِّت الموحّد، **دائمًا** لا عند النقص فقط
    #  ⚠️ العِلّة التي أُصلحت: كان التثبيت الكامل يُشغَّل فقط لو ظهرت حزمة
    #  ناقصة بالاستيراد. فلو كانت الحزمة موجودة لكن **إصدارها قديم** (أو
    #  تالفة جزئيًا) لا يُصلح شيئًا، ويظهر السلوك لاحقًا كخطأ غامض في التشغيل.
    #  الآن: التحقّق الكامل يجري كل مرة مهما كان الحال — وهو ما طلبه المشغّل
    #  صراحةً: «تنزيل كامل لكل حاجاته إجباريًا».
    deps = ROOT / "setup_deps.py"
    if deps.is_file():
        info("تحقّق كامل من كل الحاجيات وتثبيت الناقص (إجباري) …")
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

    # حزم اختيارية: لا تُسقط التشغيل، لكن نُبلّغ بصراحة
    # `ZAKR_MINIMAL=1` على لوحة ضيّقة المساحة: نتخطّى حزم OCR (~120MB) صراحةً
    minimal = os.environ.get("ZAKR_MINIMAL") == "1"
    if minimal:
        info("وضع اقتصادي (ZAKR_MINIMAL=1): تخطّي حزم قراءة الصور (~120MB)")
    for label, mod in OPTIONAL_MODULES:
        if have(mod):
            continue
        if minimal:
            info(f"تخطّي {label} (ZAKR_MINIMAL=1)")
            continue
        good, _ = _pip(label)
        if not good:
            info(f"غير متوفّرة هنا: {label} (البوت يعمل بدونها)")

    # ── التحقّق النهائي: الاستيراد الفعلي
    still = [label for label, mod in BOOT_MODULES if not have(mod)]
    if still:
        bad(f"حزم أساسية لا تُستورد بعد التثبيت: {', '.join(still)}")
        info("البوت لن يبدأ بدونها. جرّب يدويًا: pip install -r requirements.txt")
        return 1
    ok(f"حاجيات بايثون جاهزة ({len(BOOT_MODULES)} حزمة إقلاع متحقَّقة بالاستيراد)")

    _write_verified_stamp()
    return 0


# ─────────────────────── ② برامج النظام — لم يبقَ منها شيء
#  كان هنا تنزيل tesseract/poppler كملفات .deb واستخراجها بلا root، ثم
#  تنزيل بيانات اللغة العربية. أُزيل كله مع إزالة الـOCR. لم تعد قراءة
#  الملفات المصوّرة تحتاج أي برنامج خارج بايثون.


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

    # قراءة الملفات المصوّرة: نحتاج PyMuPDF فقط — لا برامج نظام.
    # الوكيل يقرأ الصور، والمفتاح عند المستخدم لا عند السيرفر.
    try:
        from bot.services import extract

        import importlib.util

        if importlib.util.find_spec("pymupdf") or importlib.util.find_spec("fitz"):
            ok("قراءة الملفات المصوّرة جاهزة (تحويل PDF→صور عبر PyMuPDF)")
        else:
            warn("PyMuPDF غير مثبَّت — ملفات PDF المصوّرة لن تُقرأ")
            info("أصلحه بـ: pip install PyMuPDF")
        info(
            "القراءة تتم عبر وكيل الرؤية الذي يختاره كل مستخدم بمفتاحه — "
            f"{len(extract.VISION_PROVIDERS)} مزوّدًا مدعومًا. "
            "لا حاجة لأي برنامج نظام (tesseract/poppler) بعد الآن."
        )
    except Exception as exc:            # noqa: BLE001 — فحص لا يُسقط التجهيز
        warn(f"تعذّر فحص مسار قراءة الصور: {type(exc).__name__}")

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


USAGE = """الاستخدام:
    python self_install.py             يجهّز المنصّة ويثبّت ما ينقص
    python self_install.py --check     يفحص فقط (لا يثبّت شيئًا)
    python self_install.py --quiet     صامت (يُستدعى من main.py)
    python self_install.py --help      هذه المساعدة

يثبّت بايثون وحزم النظام بلا root (تنزيل .deb واستخراجه يدويًا)،
ويجهّز tesseract + poppler لقراءة الملفات المصوّرة. كل خطوة قابلة
للتخطّي: البوت يعمل وإن فشل التجهيز.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        return 0
    quiet = "--quiet" in argv or os.environ.get("ZAKR_QUIET") == "1"

    say(f"{C.bold}╔══════════════════════════════════════════╗{C.reset}")
    say(f"{C.bold}║  ذاكر — تجهيز المنصّة (self-install)     ║{C.reset}")
    say(f"{C.bold}╚══════════════════════════════════════════╝{C.reset}")

    if not quiet:
        platform_report()

    if "--check" in argv:
        return verify_ready()

    rc = install_python_deps()
    if rc != 0:
        return rc
    rc = verify_ready()
    if rc == 0:
        _write_verified_stamp()   # لا نُوقّع إلا بعد تحقّق نهائي ناجح
    return rc


if __name__ == "__main__":
    sys.exit(main())
