#!/usr/bin/env python3
"""تنزيل قارئ الملفات المصوّرة (tesseract) وتشغيله — **بلا root وبلا apt**.

المشكلة: لوحات الاستضافة (Wispbyte / Pterodactyl / أي حاوية) تعطي المستخدم
`container` عاديًا: لا `sudo`، ولا `apt install`، ولا صلاحية كتابة في
`/usr/bin`. فحزمة `tesseract` لا يمكن تثبيتها بالطريقة المعتادة — ومع ذلك
البوت يحتاجها لقراءة الملفات المصوّرة.

الحل (مُختبَر فعليًا، لا نظريًا):
  `apt-get download` **لا يحتاج root** — ينزّل ملف `.deb` فقط. ونستخرجه
  بـ`dpkg-deb -x` (أو `ar`+`tar` كبديل) في مجلد داخل المشروع، ثم نشغّل
  الملف التنفيذي مع `LD_LIBRARY_PATH` و`TESSDATA_PREFIX` موجّهين للمجلد.

النتيجة المقيسة: tesseract 5.5.0 + ara + eng + pdftoppm، في ~158 ميجا
(أو ~12 ميجا لو tesseract وحده بلا pdftoppm).

⚠️ صدق ضروري: هذه الحزم مُجمَّعة لمعمارية ونظام الصورة (x86_64 / glibc).
  على صورة Alpine (musl) لا تعمل — والفحص يكشف ذلك ويقول السبب بدل أن
  يظهر خطأ غامض لاحقًا.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# مجلد الاستخراج قابل للتغيير (اختبارات/تثبيتات متعددة) — الافتراضي .ocr/
OCR_DIR = Path(os.environ.get("ZAKR_OCR_DIR") or (ROOT / ".ocr")).resolve()

# أدوات مشتركة — كانت منسوخة حرفيًا في ثلاثة ملفات
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

# الحزم المطلوبة. الترتيب مهم: المكتبات قبل الملفات التنفيذية.
PACKAGES = [
    "tesseract-ocr",
    "tesseract-ocr-ara",
    "tesseract-ocr-eng",
    "tesseract-ocr-osd",   # ضروري: كشف الخط/الاتجاه (OSD) — بدونه يفشل detect_languages
    "libtesseract5",
    "libleptonica5",
    "libleptonica6",
    "libarchive13t64",
    "libgif7",
    "poppler-utils",
]

# بدائل لأسماء الحزم بين إصدارات ديبيان/أوبنتو
PACKAGE_ALIASES = {
    "libgpgmepp6": ("libgpgmepp6t64", "libgpgmepp6"),
    "libnss3": ("libnss3",),
    "libnspr4": ("libnspr4",),
    "libarchive13t64": ("libarchive13t64", "libarchive13"),
    "libgif7": ("libgif7", "libgif7t64"),
    "libtesseract5": ("libtesseract5", "libtesseract5t64"),
    # Tesseract 5.3/5.4 على Ubuntu 22.04 يطلب SONAME liblept.so.5؛
    # اسم حزمة Debian/Ubuntu المقابل غالبًا libleptonica5 وليس libleptonica6.
    "liblept5": ("libleptonica5", "liblept5", "libleptonica6", "libleptonica6t64"),
    "libleptonica5": ("libleptonica5", "libleptonica6", "libleptonica6t64"),
    "libleptonica6": ("libleptonica6", "libleptonica6t64", "libleptonica5"),
    "libpoppler147": ("libpoppler147", "libpoppler126", "libpoppler-glib8"),
}

DEBIAN_PREFIXES = ["/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
                   "/usr/lib", "/lib/x86_64-linux-gnu"]


# ─────────────────────────────────────────────────── خصائص البيئة
def is_musl() -> bool:
    """Alpine وأمثالها: الحزم المبنية لـglibc لن تعمل."""
    if Path("/etc/alpine-release").exists():
        return True
    try:
        out = subprocess.run(["ldd", "--version"], capture_output=True, text=True)
        blob = (out.stdout or "") + (out.stderr or "")
        return "musl" in blob.lower()
    except OSError:
        return False


def lib_paths() -> str:
    """مسار المكتبات داخل مجلد OCR (نبحث في كل مجلدات lib)."""
    parts: list[str] = []
    for base in (OCR_DIR / "usr/lib", OCR_DIR / "lib", OCR_DIR / "usr/lib/x86_64-linux-gnu"):
        if base.is_dir():
            parts.append(str(base))
            for sub in base.iterdir():
                if sub.is_dir() and "linux-gnu" in sub.name:
                    parts.append(str(sub))
    return ":".join(dict.fromkeys(parts))


def tessdata_path() -> str:
    for p in sorted((OCR_DIR / "usr/share").glob("tesseract-ocr/*/tessdata")):
        return str(p)
    for p in sorted((OCR_DIR / "usr/share").glob("tessdata")):
        return str(p)
    return ""


def binary(name: str) -> Path | None:
    for d in (OCR_DIR / "usr/bin", OCR_DIR / "bin"):
        cand = d / name
        if cand.is_file():
            return cand
    return None


def run_env() -> dict[str, str]:
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = lib_paths() + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    td = tessdata_path()
    if td:
        env["TESSDATA_PREFIX"] = td
    return env


def works(name: str, flag: str = "--version") -> bool:
    """هل الملف التنفيذي يعمل فعلًا (لا مجرد موجود)؟"""
    exe = binary(name)
    if exe is None:
        return False
    try:
        p = subprocess.run([str(exe), flag], capture_output=True, text=True,
                           env=run_env(), timeout=60)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ─────────────────────────────────────────────────────── التنزيل
def have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def download(pkg: str, dest: Path) -> Path | None:
    """ينزّل .deb بلا root. يرجع المسار أو None."""
    names = PACKAGE_ALIASES.get(pkg, (pkg,))
    for name in names:
        if have_tool("apt-get"):
            subprocess.run(["apt-get", "download", name], cwd=str(dest),
                           capture_output=True, text=True)
        elif have_tool("apt"):
            subprocess.run(["apt", "download", name], cwd=str(dest),
                           capture_output=True, text=True)
        else:
            return None
        found = list(dest.glob(f"{name.replace(':', '%3a')}_*.deb")) or list(dest.glob(f"{name}_*.deb"))
        if found:
            return found[0]
    return None


def extract(deb: Path, dest: Path) -> bool:
    """يفكّ .deb — dpkg-deb إن وُجد، وإلا ar + tar (يعملان بلا root)."""
    if have_tool("dpkg-deb"):
        p = subprocess.run(["dpkg-deb", "-x", str(deb), str(dest)],
                           capture_output=True, text=True)
        if p.returncode == 0:
            return True
    # يدويًا: .deb = ar archive فيه data.tar.*
    if not have_tool("ar"):
        return False
    work = dest.parent / ".ar"
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ar", "x", str(deb)], cwd=str(work), capture_output=True)
    tarballs = list(work.glob("data.tar*"))
    if not tarballs:
        return False
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-xf", str(tarballs[0]), "-C", str(dest)], capture_output=True)
    return True


def missing_libs(exe: Path) -> list[str]:
    """أسماء المكتبات الناقصة لملف تنفيذي (بصيغة apt: libfoo.so.6 → libfoo6)."""
    try:
        out = subprocess.run(["ldd", str(exe)], capture_output=True,
                             text=True, env=run_env()).stdout
    except OSError:
        return []
    missing = []
    for line in out.splitlines():
        if "not found" not in line:
            continue
        so = line.split("=>")[0].strip()            # libgpgmepp.so.6
        base = so.split(".so")[0]                    # libgpgmepp
        ver = so.split(".so.")[-1].split(".")[0] if ".so." in so else ""
        missing.append(base + ver)                   # libgpgmepp6
    return missing


def resolve_deps(exe: Path, *, depth: int = 4) -> int:
    """يحلّ مكتبات exe الناقصة递归ًيًا بـ apt-get download.

    الطريقة: نبني اسم الحزمة من اسم المكتبة، ونبحث في فهرس apt عن تطابق
    حقيقي (لأن ديبيان تضيف لواحق مثل t64 على بعض الحزم). هذا يمنع الاعتماد
    على أسماء ثابتة تتغيّر بين الإصدارات.
    """
    work = ROOT / ".ocr-debs"
    work.mkdir(parents=True, exist_ok=True)
    fixed = 0

    for _ in range(depth):
        missing = missing_libs(exe)
        if not missing:
            break
        progressed = False
        for lib in missing:
            pkg = find_package(lib)
            if not pkg:
                continue
            deb = download(pkg, work)
            if deb and extract(deb, OCR_DIR):
                fixed += 1
                progressed = True
        if not progressed:
            break
    return fixed


def find_package(lib: str) -> str | None:
    """يبحث عن اسم الحزمة الحقيقي الذي يوفّر هذه المكتبة."""
    # 1) اسم مباشر أو بديل معروف
    for cand in PACKAGE_ALIASES.get(lib, (lib,)):
        if subprocess.run(["apt-get", "download", "--print-uris", cand],
                          capture_output=True, text=True).stdout.strip():
            return cand
    # 2) بحث في الفهرس: أي حزمة توفر lib*.so.N
    try:
        out = subprocess.run(["apt-cache", "search", lib], capture_output=True,
                             text=True).stdout
    except OSError:
        return None
    for line in out.splitlines():
        name = line.split(" - ")[0].strip()
        if not name:
            continue
        # الاسم المطلوب يجب أن يبدأ بنفس الجذر
        root = lib.split("-")[0]
        if name.startswith(root):
            return name
    return None


def step_install(*, with_poppler: bool, force: bool = False) -> int:
    say()
    say(f"{C.dim}══════ تنزيل قارئ الملفات المصوّرة (tesseract) ══════{C.reset}")

    if is_termux():
        info("أندرويد/Termux: استخدم pkg بدلًا من هذا الملف")
        info("  pkg install tesseract poppler")
        return 1

    if is_musl():
        bad("هذه الصورة Alpine (musl) — حزم ديبيان (glibc) لا تعمل عليها.")
        info("الحلول: استخدم صورة Debian/Ubuntu، أو ثبّت tesseract في الصورة نفسها.")
        return 2

    # هل يعمل أصلًا مثبّتًا في النظام؟ (--force يتخطّى هذا للاختبار/الإصلاح)
    if not force and shutil.which("tesseract") and works("tesseract"):
        ok("tesseract مثبّت في النظام ويعمل — لا حاجة للتنزيل")
        return 0

    if not (have_tool("apt-get") or have_tool("apt")):
        bad("لا يوجد apt/apt-get في هذه الصورة — لا أستطيع تنزيل الحزم.")
        info("لوحتك تعطي حاوية بلا مدير حزم. اطلب من المستضيف صورة فيها tesseract،")
        info("أو استخدم صورة Debian/Ubuntu.")
        return 2


    pkgs = [p for p in PACKAGES if with_poppler or not p.startswith("poppler")]

    OCR_DIR.mkdir(parents=True, exist_ok=True)
    work = ROOT / ".ocr-debs"
    work.mkdir(parents=True, exist_ok=True)

    info(f"تنزيل {len(pkgs)} حزمة (بلا root، بلا apt install) …")
    got = 0
    for pkg in pkgs:
        deb = download(pkg, work)
        if deb is None:
            # بعض الحزم اختيارية (مكتبات فرعية قد لا تُذكر بالاسم)
            info(f"لم أجد {pkg} — أتخطّاه")
            continue
        if extract(deb, OCR_DIR):
            got += 1
        else:
            warn(f"تعذّر فكّ {deb.name}")

    ok(f"استُخرجت {got} حزمة إلى {OCR_DIR.name}/")

    # حلّ مكتبات كل برنامج على حدة؛ نجاح pdftoppm لا يعني أن Tesseract
    # يملك اعتمادياته. كان هذا الشرط يُسقط liblept.so.5 إذا عمل Poppler.
    for binary_name, flag in (("tesseract", "--version"), ("pdftoppm", "-v")):
        if binary_name == "pdftoppm" and not with_poppler:
            continue
        exe = binary(binary_name)
        if exe is not None and not works(binary_name, flag):
            info(f"حلّ مكتبات {binary_name} الناقصة تلقائيًا …")
            n = resolve_deps(exe)
            if n:
                info(f"أُضيفت {n} مكتبة/حزمة اعتماد")

    shutil.rmtree(work, ignore_errors=True)

    # ── التحقق الفعلي
    say()
    say(f"{C.dim}── التحقق الفعلي ──{C.reset}")
    rc = 0

    if works("tesseract"):
        exe = binary("tesseract")
        ver = subprocess.run([str(exe), "--version"], capture_output=True,
                             text=True, env=run_env()).stdout.splitlines()
        ok(f"tesseract يعمل: {(ver[0] if ver else '')}")
        sd = tessdata_path()
        info(f"tessdata: {sd or 'غير موجود'}")
        if sd:
            langs = sorted(Path(sd).glob("*.traineddata"))
            names = [f.name.split(".")[0] for f in langs]
            info(f"لغات: {', '.join(names) or 'لا شيء'}")
            if "ara" not in names:
                warn("العربية (ara) غير موجودة — OCR العربي لن يعمل")
                rc = max(rc, 1)
            if "osd" not in names:
                # بدونه يفشل كشف الخط ويُستخدم الافتراضي دائمًا
                warn("osd غير موجود — كشف لغة الصفحة لن يعمل (يُستخدم الافتراضي)")
                rc = max(rc, 1)
    else:
        bad("tesseract لا يعمل بعد التنزيل")
        exe = binary("tesseract")
        if exe:
            detail = subprocess.run(["ldd", str(exe)], capture_output=True,
                                    text=True, env=run_env()).stdout
            missing = [ln.strip() for ln in detail.splitlines() if "not found" in ln]
            if missing:
                info("مكتبات ناقصة:")
                for m in missing[:6]:
                    info(f"  {m}")
        rc = max(rc, 1)

    if with_poppler:
        if works("pdftoppm", "-v"):
            ok("pdftoppm يعمل (تحويل PDF إلى صورة)")
        else:
            warn("pdftoppm لا يعمل — يمكن قراءة الصور (JPG/PNG) لكن ليس PDF")
            info("البوت يعمل بدونه؛ رفع الصور المصوّرة يعمل.")

    # ── كتابة ملف البيئة ليقرأه البوت
    write_env_file()
    say()
    if rc == 0:
        ok("قارئ الملفات المصوّرة جاهز — يُستخدم تلقائيًا عند تشغيل البوت.")
    return rc


def write_env_file() -> None:
    """يكتب مسارات OCR في ملف يستورده البوت/setup عند الإقلاع."""
    envf = OCR_DIR / "env.sh"
    envf.parent.mkdir(parents=True, exist_ok=True)
    bindir = OCR_DIR / "usr/bin"
    lines = [
        "# مولَّد تلقائيًا بواسطة ocr_fetch.py — لا تعدّله يدويًا",
        f'export OCR_BIN_DIR="{bindir}"',
        # ⚠️ بدون إضافة المجلد إلى PATH لن يجد البوت "tesseract" إطلاقًا،
        #    وshutil.which ترجع None مع أن الملف موجود — عِلّة حقيقية وقعت.
        f'export PATH="{bindir}:$PATH"',
        f'export LD_LIBRARY_PATH="{lib_paths()}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"',
    ]
    td = tessdata_path()
    if td:
        lines.append(f'export TESSDATA_PREFIX="{td}"')
    envf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    info(f"كُتبت المسارات في {envf}")


def ensure_on_path() -> None:
    """يضيف مجلد OCR إلى PATH/المكتبات في هذه العملية (للاستدعاء البرمجي)."""
    bindir = OCR_DIR / "usr/bin"
    if bindir.is_dir():
        os.environ["PATH"] = f"{bindir}:{os.environ.get('PATH', '')}"
    lp = lib_paths()
    if lp:
        os.environ["LD_LIBRARY_PATH"] = lp + (
            ":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else ""
        )
    td = tessdata_path()
    if td:
        os.environ["TESSDATA_PREFIX"] = td


def status() -> int:
    """يطبع حالة OCR الحالية بلا تعديل."""
    say(f"{C.dim}── حالة قارئ الملفات المصوّرة ──{C.reset}")
    info(f"مجلد OCR: {OCR_DIR} ({'موجود' if OCR_DIR.is_dir() else 'غير موجود'})")

    sys_t = shutil.which("tesseract")
    if sys_t:
        ok(f"tesseract في النظام: {sys_t}")
    elif works("tesseract"):
        ok("tesseract من مجلد OCR المحلي")
    else:
        warn("tesseract غير متوفّر")

    if works("pdftoppm", "-v") or shutil.which("pdftoppm"):
        ok("pdftoppm متوفّر")
    else:
        warn("pdftoppm غير متوفّر")

    td = tessdata_path() or "/usr/share/tesseract-ocr/5/tessdata"
    if Path(td).is_dir():
        names = [f.name.split(".")[0] for f in Path(td).glob("*.traineddata")]
        info(f"اللغات: {', '.join(sorted(names)) or 'لا شيء'}")
        if "ara" in names:
            ok("العربية متوفّرة")
        else:
            warn("العربية (ara) غير موجودة")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--help" in argv or "-h" in argv:
        say(__doc__)
        return 0

    if "--status" in argv or "--check" in argv:
        return status()

    # --tesseract-only: أخفّ بكثير (~12 ميجا) لو تكفي الصور بلا PDF
    with_poppler = "--tesseract-only" not in argv

    force = "--force" in argv
    rc = step_install(with_poppler=with_poppler, force=force)
    if rc == 0:
        ensure_on_path()
    return rc


if __name__ == "__main__":
    sys.exit(main())
