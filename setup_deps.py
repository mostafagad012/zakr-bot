#!/usr/bin/env python3
"""تثبيت حاجيات بوت «ذاكر» — يعمل على أي منصّة، بلا اعتماديات مسبقة.

يُستدعى من `setup.sh` و`setup.py` معًا (مصدر واحد للحقيقة).

الفلسفة: **إجباري لا اختياري.**
  • على Wispbyte/Pterodactyl: يفوّض إلى `self_install.py` لتثبيت المجموعة
    الكاملة (`requirements-wispbyte.txt`) وأدوات OCR وبيانات اللغات، ثم يفشل
    بوضوح إذا تعذّر أي مكوّن.
  • على السيرفرات الأخرى: تُثبَّت حزم `requirements.txt` الأساسية.
  • على Termux/أندرويد فقط: نستخدم حزم `pkg` لما لا عجلة له، ونُعلن ما
    يستحيل صراحةً (onnxruntime) — لأن البناء على الجوال مستحيل فعلًا.

لماذا لا نستخدم `pip install -r requirements.txt` مباشرة؟
  لأن فشله يعطي رسالة واحدة مبهمة عن القائمة كلها. هنا نثبّت واحدة واحدة،
  فنعرف **بالضبط** أي حزمة فشلت ولماذا، ونعيد المحاولة بطرق بديلة.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQ = ROOT / "requirements.txt"


def is_wispbyte() -> bool:
    """كشف Wispbyte/Pterodactyl حتى لو استُدعي هذا الملف مباشرةً."""
    import os
    return (
        os.environ.get("ZAKR_WISPBYTE_MODE") == "1"
        or os.environ.get("WISPBYTE") == "1"
        or Path("/home/container").is_dir()
    )


def run_wispbyte_installer() -> int:
    """لا تسمح لمسار التثبيت القديم بتجاوز مجموعة Wispbyte الكاملة."""
    import os
    import subprocess

    installer = ROOT / "self_install.py"
    if not installer.is_file():
        bad("self_install.py مفقود — لا يمكن تثبيت مجموعة Wispbyte الكاملة.")
        return 1
    os.environ["ZAKR_WISPBYTE_MODE"] = "1"
    os.environ["ZAKR_INSTALL_OCR"] = "1"
    os.environ.pop("ZAKR_SKIP_OCR", None)
    info("Wispbyte: تحويل التثبيت إلى self_install.py (كل المكتبات + Tesseract + Poppler + اللغات).")
    try:
        proc = subprocess.run([sys.executable, "-u", str(installer)], cwd=str(ROOT))
    except OSError as exc:
        bad(f"تعذّر تشغيل self_install.py: {exc}")
        return 1
    return proc.returncode or 0

# أدوات مشتركة (طباعة/منصّة) — كانت منسوخة في ثلاثة ملفات
sys.path.insert(0, str(ROOT))
from setup_common import (  # noqa: E402
    C,
    bad,
    info,
    is_termux,
    ok,
    say,
    warn,
)

# بدائل عند فشل التثبيت العادي: اسم الحزمة -> خيارات pip إضافية
FALLBACKS: dict[str, list[list[str]]] = {
    "pillow": [["--only-binary=:all:"], ["--no-build-isolation"]],
    "numpy": [["--only-binary=:all:"]],
    "onnxruntime": [["--only-binary=:all:"], ["onnxruntime==1.20.1"]],
    "asyncpg": [["--only-binary=:all:"]],
    "greenlet": [["--only-binary=:all:"]],
    "pydantic": [["--only-binary=:all:"]],
}

# حزم لا تُثبَّت على Termux (لا عجلة أندرويد ولا حزمة pkg) — تُعلَن لا تُخفى
TERMUX_IMPOSSIBLE = {
    "onnxruntime": "فكّ التواء صور الموبايل (OCR العادي يعمل)",
    "asyncpg": "PostgreSQL/Supabase — استخدم SQLite على الجوال",
}

# على Termux: تُؤخذ من pkg لا من pip
TERMUX_PKG_MAP = {
    "pillow": (("python-pillow",), "PIL"),
    "numpy": (("python-numpy",), "numpy"),
    "greenlet": (("python-greenlet",), "greenlet"),
}


def parse_requirements(path: Path = REQ) -> list[str]:
    """يقرأ requirements.txt ويرجع سطور الحزم الفعّالة (بلا تعليقات)."""
    if not path.is_file():
        # ⚠️ لا تستخدم raise SystemExit("نص") — كود الخروج يصبح 0 (نجاح زائف)
        bad(f"لم أجد {path}")
        info("شغّل السكربت من مجلد المشروع (حيث requirements.txt).")
        raise SystemExit(1)
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def pkg_name(spec: str) -> str:
    """اسم الحزمة من سطر مثل: `aiogram>=3.13,<4` أو `SQLAlchemy[asyncio]>=2.0`."""
    for sep in ("[", "<", ">", "=", "!", "~", ";", " "):
        spec = spec.split(sep, 1)[0]
    return spec.strip().lower()


def have_module(name: str) -> bool:
    """هل الوحدة قابلة للاستيراد في هذه العملية؟"""
    import importlib.util

    return importlib.util.find_spec(name) is not None


def run_pip(py: str, args: list[str], *, quiet: bool = True) -> tuple[bool, str]:
    """يشغّل pip ويرجع (نجح؟, المخرجات)."""
    cmd = [py, "-m", "pip", "install", "--no-input", "--no-cache-dir"]
    if quiet:
        cmd.append("--quiet")
    cmd += args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


def pip_ready(py: str) -> bool:
    proc = subprocess.run(
        [py, "-m", "pip", "--version"], capture_output=True, text=True
    )
    return proc.returncode == 0


def bootstrap_pip(py: str) -> bool:
    """يضمن وجود pip: ensurepip ثم get-pip.py ثم لا شيء."""
    if pip_ready(py):
        return True
    info("pip غير موجود — تهيئته …")
    for args in (["ensurepip", "--upgrade"], ["ensurepip", "--upgrade", "--default-pip"]):
        proc = subprocess.run([py, "-m", *args], capture_output=True, text=True)
        if proc.returncode == 0 and pip_ready(py):
            ok("pip جاهز (ensurepip)")
            return True
    # آخر حل: get-pip.py
    import urllib.request

    tmp = ROOT / ".get-pip.py"
    try:
        info("تنزيل get-pip.py …")
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", tmp)  # noqa: S310
        proc = subprocess.run([py, str(tmp)], capture_output=True, text=True)
        if proc.returncode == 0 and pip_ready(py):
            ok("pip جاهز (get-pip)")
            return True
    except Exception as exc:  # noqa: BLE001
        warn(f"تعذّر get-pip: {exc}")
    finally:
        tmp.unlink(missing_ok=True)
    return pip_ready(py)


def install_one(py: str, spec: str) -> tuple[bool, str]:
    """يثبّت حزمة واحدة مع بدائل عند الفشل. يرجع (نجح؟, آخر خطأ)."""
    name = pkg_name(spec)
    last = ""

    attempts: list[list[str]] = [[spec]]
    attempts += [opts + [spec] for opts in FALLBACKS.get(name, [])]

    for i, args in enumerate(attempts, 1):
        good, out = run_pip(py, args)
        if good:
            return True, ""
        last = out
        if i < len(attempts):
            info(f"  محاولة {i + 1}/{len(attempts)} لـ{name} بطريقة أخرى …")

    # محاولة أخيرة بلا --quiet لنرى السبب الكامل
    if name in {"pytest", "pytest-asyncio"}:
        return False, last
    return False, last


def error_reason(output: str, limit: int = 4) -> list[str]:
    """يستخرج أسطر سبب الفشل من مخرجات pip."""
    keys = ("error:", "no matching distribution", "could not find a version",
            "failed building", "not supported", "requires", "no space",
            "permission denied", "ssl", "timeout", "killed")
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    picked = [ln for ln in lines if any(k in ln.lower() for k in keys)]
    return (picked or lines[-limit:])[:limit]


def termux_take_from_pkg(py: str, targets: list[str]) -> list[str]:
    """على Termux: يثبّت ما له حزمة pkg ويعيد قائمة ما يمكن تخطّيه."""
    done: list[str] = []
    for name, (pkgs, module) in TERMUX_PKG_MAP.items():
        if have_module(module):
            done.append(name)
            continue
        for pkg in pkgs:
            proc = subprocess.run(
                ["pkg", "install", "-y", pkg], capture_output=True, text=True
            )
            if proc.returncode == 0 and have_module(module):
                ok(f"{name} من pkg ({pkg})")
                done.append(name)
                break
    return done


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet_mode = "--quiet" in argv
    py = sys.executable

    # لا تسمح لمسارات setup.sh/setup.py القديمة باستخدام requirements.txt
    # على Wispbyte؛ هذا كان سبب ظهور alembic كحزمة ناقصة رغم وجود ملف المجموعة الكاملة.
    if is_wispbyte():
        return run_wispbyte_installer()

    say()
    say(f"{C.dim}══════ تثبيت حاجيات «ذاكر» ══════{C.reset}")
    info(f"بايثون: {sys.version.split()[0]}  ({py})")

    if not bootstrap_pip(py):
        bad("لا يوجد pip ولا يمكن تهيئته.")
        info("على Termux: pkg install python-pip")
        info("على ديبيان: sudo apt-get install -y python3-pip")
        return 1

    specs = parse_requirements()
    if not specs:
        bad("requirements.txt فاضي — لا شيء لأثبّته.")
        return 1
    info(f"عدد الحاجيات: {len(specs)}")

    termux = is_termux()
    from_pkg: list[str] = []
    impossible: dict[str, str] = {}

    if termux:
        info("المنصّة: أندرويد/Termux — ما لا عجلة له يُؤخذ من pkg أو يُعلَن")
        from_pkg = termux_take_from_pkg(py, specs)
        impossible = dict(TERMUX_IMPOSSIBLE)
        # فهرس TUR يعطي عجلات أندرويد حقيقية (pydantic-core/aiohttp/numpy)
        extra_index = ["--extra-index-url", "https://termux-user-repository.github.io/pypi/"]
        # منع أي بناء من المصدر على الجوال
        extra_index += ["--only-binary=:all:"]
    else:
        extra_index = []

    failed: dict[str, str] = {}
    skipped: list[str] = []

    say()
    say(f"{C.dim}── التثبيت (إجباري) ──{C.reset}")

    for spec in specs:
        name = pkg_name(spec)
        if name in impossible:
            skipped.append(name)
            continue
        if name in from_pkg:
            info(f"تخطّي {name} (مثبّتة من pkg)")
            continue

        # تثبيت جماعي سريع أولًا ثم فردي عند الفشل؟ لا — الفردي هو الضمانة.
        target = [spec]
        if extra_index:
            target = extra_index + [spec]
        good, out = run_pip(py, target)
        if good:
            if not quiet_mode:
                info(f"✔ {name}")
            continue
        if extra_index:
            # أعد المحاولة بالبدائل القياسية
            pass
        good2, out2 = install_one(py, spec)
        if good2:
            continue
        failed[name] = out2 or out

    say()
    say(f"{C.dim}── التحقق بالاستيراد الفعلي ──{C.reset}")
    # التحقق: نستورد فعليًا (اسم الحزمة ≠ اسم الموديول دائمًا)
    REQUIRED_MODULES = [
        ("aiogram", "aiogram"),
        ("SQLAlchemy", "sqlalchemy"),
        ("aiosqlite", "aiosqlite"),
        ("alembic", "alembic"),
        ("pypdf", "pypdf"),
        ("httpx", "httpx"),
    ]
    OPTIONAL_MODULES = [
        ("Pillow", "PIL"),
        ("pytesseract", "pytesseract"),
        ("pdf2image", "pdf2image"),
        ("numpy", "numpy"),
    ]
    missing_required: list[str] = []
    for label, mod in REQUIRED_MODULES:
        if have_module(mod):
            if not quiet_mode:
                info(f"✔ {label}")
        else:
            missing_required.append(label)

    missing_optional: list[str] = []
    for label, mod in OPTIONAL_MODULES:
        if not have_module(mod):
            missing_optional.append(label)

    say()
    if failed:
        bad(f"حزم فشل تثبيتها: {', '.join(sorted(failed))}")
        for name, out in failed.items():
            say(f"  {C.warn}سبب {name}:{C.reset}")
            for line in error_reason(out):
                say(f"    {line}")

    if missing_required:
        bad(f"حزم أساسية ناقصة بعد التثبيت: {', '.join(missing_required)}")
        say(f"{C.dim}  البوت لن يبدأ بدونها. جرّب:{C.reset}")
        say(f"    {py} -m pip install -r requirements.txt")
        return 1

    if skipped:
        warn("غير متوفّر على هذه المنصّة (البوت يعمل بدونه):")
        for name in skipped:
            info(f"{name} — {impossible.get(name, '')}")

    if missing_optional:
        warn(f"حزم اختيارية ناقصة (ميزة أقل، البوت يبدأ): {', '.join(missing_optional)}")

    if failed:
        info("الحزم الأساسية كلها موجودة، لكن راجع الفاشل أعلاه.")
        return 0

    say()
    ok("كل حاجيات البوت مثبّتة ومتحقَّقة بالاستيراد.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
