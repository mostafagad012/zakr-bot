#!/usr/bin/env python3
"""بوت «ذاكر» — نقطة الإقلاع ببايثون (تعمل على Wispbyte وVercel وأي سيرفر).

الغرض: ملف واحد تشغّله فيعمل **كل** ما يحتاجه البوت بلا أي خطوة يدوية:

    python setup.py              # تثبيت الحاجيات + تجهيز .env + فحص + تشغيل
    python setup.py --install    # تثبيت الحاجيات فقط (إجباري)
    python setup.py --check      # فحص البيئة بلا تعديل
    python setup.py --no-start   # جهّز كل شيء ولا تشغّل
    python setup.py --wispbyte   # وضع لوحة Wispbyte (env vars لا .env)

لماذا هذا الملف؟
  لوحات الاستضافة (Wispbyte/Pterodactyl) لا تعطي shell تفاعليًا، ولا `sudo`،
  ولا `apt`. الأمر الوحيد الذي تكتبه هو **Startup Command**. فإن لم يكن
  الإعداد كله داخل ملف واحد، لن يعمل البوت إطلاقًا.

  وكذلك على Vercel: لا يوجد سيرفر دائم، ولا صلاحية كتابة إلا في /tmp.

يميّز تلقائيًا:
  • Wispbyte/Pterodactyl  → /home/container موجود + متغيرات في البيئة
  • Termux/أندرويد        → PREFIX يحوي com.termux
  • Vercel                → VERCEL=1
  • سيرفر عادي            → خلاف ذلك

لا يستخدم أي اعتمادية خارجية قبل تثبيتها — بايثون فقط.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# أدوات مشتركة — كانت منسوخة حرفيًا في عدة ملفات إعداد
from setup_common import (  # noqa: E402
    C,
    bad,
    banner,
    head,
    info,
    is_placeholder,
    masked,
    ok,
    platform_name as _platform_name,
    warn,
)

ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
DATA_DIR = ROOT / "data"
REQUIRED_VARS = ("BOT_TOKEN", "OWNER_ID", "STORAGE_CHANNEL")


# ─────────────────────────────────────────────────────── كشف المنصّة
_FORCE_PANEL = False


def platform_name() -> str:
    """اسم المنصّة — التفاصيل في setup_common.platform_name (مصدر واحد)."""
    return _platform_name(force_panel=_FORCE_PANEL)


def is_container() -> bool:
    return platform_name() in {"wispbyte", "vercel"}


# ────────────────────────────────────────────────── قراءة متغيّرات البيئة
def load_env_file() -> None:
    """يقرأ .env **بلا الكتابة فوق** متغيرات البيئة الموجودة.

    مهم على Wispbyte: المتغيرات تأتي من لوحة التحكّم، ويجب أن تفوز على أي
    ملف قديم. `os.environ.setdefault` تحقّق ذلك.
    """
    if not ENV_FILE.is_file():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def env_value(key: str) -> str:
    return os.environ.get(key, "").strip()


# ───────────────────────────────────────────────────────────── التثبيت
def step_self_install() -> int:
    """يُفوّض إلى `self_install.py` — مصدر واحد للتثبيت على كل المنصّات.

    لماذا لم نُبقِ المنطق هنا؟ لأن `main.py` يحتاجه **قبل** أن يستورد أي
    حزمة خارجية. تكراره في مكانين يعني انحرافًا مؤكّدًا بين المسارين.
    """
    head("1/4 التثبيت الذاتي (بايثون + نظام)")
    script = ROOT / "self_install.py"
    if not script.is_file():
        bad("ملف self_install.py مفقود — لا يمكن تجهيز المنصّة.")
        return 1
    proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT))
    if proc.returncode != 0:
        bad("التثبيت الذاتي فشل — البوت لن يبدأ.")
        return proc.returncode
    return 0


def step_install() -> int:
    head("1/4 تثبيت الحاجيات")
    dep = ROOT / "setup_deps.py"
    if not dep.is_file():
        bad("لم أجد setup_deps.py بجوار setup.py — الملفان يجب أن يكونا معًا.")
        return 1
    proc = subprocess.run([sys.executable, str(dep)])
    if proc.returncode != 0:
        bad("تثبيت الحاجيات فشل — البوت لن يبدأ.")
        info("السبب مذكور أعلاه بالاسم. أعد المحاولة بعد إصلاحه.")
        return proc.returncode
    return 0


# ────────────────────────────────────────────────────── تجهيز .env
def step_env(*, no_input: bool) -> int:
    head("2/4 ملف الإعدادات")

    plat = platform_name()

    if plat in {"wispbyte"}:
        info("وضع Wispbyte: الإعدادات تُقرأ من متغيّرات اللوحة (Variables)، لا .env.")
        if ENV_FILE.is_file():
            info("يوجد .env أيضًا — تُقرأ، لكن متغيرات اللوحة تفوز عليها.")

    if not ENV_FILE.exists():
        if ENV_EXAMPLE.is_file():
            shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
            try:
                ENV_FILE.chmod(0o600)
            except OSError:
                pass
            ok("أُنشئ .env من .env.example")
        else:
            ENV_FILE.write_text(
                "BOT_TOKEN=\nOWNER_ID=\nSTORAGE_CHANNEL=\n", encoding="utf-8"
            )
            warn("لا يوجد .env.example — أنشأت .env بسيطًا")
    else:
        ok(".env موجود — لن ألمسه (مفاتيحك آمنة)")

    if no_input and plat == "wispbyte":
        info("الأسرار تُقرأ من متغيرات اللوحة — لن أكتبها في .env")

    # المجلدات التي يكتب فيها البوت وقت التشغيل
    for d in (DATA_DIR, ROOT / "backups"):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warn(f"تعذّر إنشاء {d.name}: {exc}")

    load_env_file()
    return 0


def step_config_check() -> int:
    head("3/4 التحقق من الإعدادات")

    missing = [k for k in REQUIRED_VARS if not env_value(k)]
    for key in REQUIRED_VARS:
        val = env_value(key)
        if key == "BOT_TOKEN":
            info(f"{key}: {masked(val)}")   # لا يُطبع كاملًا أبدًا
        else:
            info(f"{key}: {val or '(فاضي)'}")

    if missing:
        bad(f"قيم ناقصة: {', '.join(missing)}")
        if platform_name() == "wispbyte":
            info("أضفها في لوحة Wispbyte ← تبويب Startup ← Variables، ثم أعد التشغيل.")
            info("BOT_TOKEN من @BotFather · OWNER_ID من @userinfobot · STORAGE_CHANNEL آيدي قناتك")
        else:
            info(f"املأ الأسطر في: {ENV_FILE}")
        return 1

    # ⚠️ الأهم: كشف قيم .env.example المثالّية — لو ظنّها صحيحة سيفشل البوت
    #    عند الاتصال بتيليجرام برسالة غامضة على السيرفر بلا واجهة.
    # القيم المثالّية معرَّفة في setup_common.PLACEHOLDERS (مصدر واحد
    # يشترك فيه setup.sh وsetup.py) — لا نكرّرها هنا.
    placeholder_hit = [k for k in REQUIRED_VARS if is_placeholder(k, env_value(k))]
    if placeholder_hit:
        bad(f"هذه قيم مثالّية من .env.example ولم تُغيَّر: {', '.join(placeholder_hit)}")
        if platform_name() == "wispbyte":
            info("املأها في لوحة Wispbyte ← Startup ← Variables (لا .env).")
        else:
            info(f"عدّل الأسطر في: {ENV_FILE}")
        info("BOT_TOKEN من @BotFather · OWNER_ID من @userinfobot · STORAGE_CHANNEL آيدي قناتك")
        return 1

    # تحقق من شكل القيم (أخطاء النسخ الشائعة)
    token = env_value("BOT_TOKEN")
    if ":" not in token or len(token) < 20:
        bad("BOT_TOKEN لا يشبه توكن تيليجرام (الصيغة: أرقام:أحرف).")
        info("انسخه كاملًا من @BotFather بلا مسافات.")
        return 1
    owner = env_value("OWNER_ID")
    if not owner.isdigit() or owner == "0":
        bad("OWNER_ID يجب أن يكون رقمًا (آيدي حسابك).")
        return 1
    chan = env_value("STORAGE_CHANNEL")
    if not (chan.startswith("-100") or (chan.startswith("@") and len(chan) > 4)):
        bad("STORAGE_CHANNEL يجب أن يبدأ بـ -100 (قناة) أو @اسم_قناة.")
        return 1

    ok("الإعدادات سليمة")
    return 0


# ─────────────────────────────── التحقّق من ربط الميزات الأساسية
def step_features() -> int:
    """يتأكد أن الميزات الجديدة **مربوطة فعلًا** قبل تشغيل البوت.

    **لماذا خطوة مستقلة؟** لأن عطلًا في الربط (زر يشير إلى callback غير
    مسجّل، أو موجّه غير مضاف إلى `ROUTER_ORDER`) لا يظهر وقت التثبيت —
    يظهر للمستخدم كزر لا يستجيب. الفحص هنا يحوّل عطلًا صامتًا إلى رسالة
    واضحة قبل التشغيل.
    """
    head("التحقّق من ربط الميزات")

    sys.path.insert(0, str(ROOT))
    problems: list[str] = []

    # ① الخدمات الجديدة موجودة وتستورد
    for module, attrs in (
        ("bot.services.verbatim", ("protect", "restore", "verbatim_report")),
        ("bot.services.material", ("analyze", "explain_material", "render")),
        ("bot.services.files", ("can_edit", "update_file", "delete_file", "user_files")),
    ):
        try:
            imported = __import__(module, fromlist=["*"])
        except Exception as exc:                  # noqa: BLE001
            problems.append(f"{module}: تعذّر الاستيراد ({type(exc).__name__})")
            continue
        for attr in attrs:
            if not hasattr(imported, attr):
                problems.append(f"{module}.{attr} مفقود")

    # ② الموجّهات الجديدة مسجّلة في البوت
    try:
        from bot import main as bot_main
        routers = {getattr(r, "name", "") for r in bot_main.ROUTER_ORDER}
        for needed in ("materials", "explain_material"):
            if needed not in routers:
                problems.append(f"الموجّه «{needed}» غير مسجَّل في ROUTER_ORDER")
    except Exception as exc:                      # noqa: BLE001
        problems.append(f"bot.main: تعذّر الفحص ({type(exc).__name__})")

    # ③ حفظ الرموز يعمل فعليًا (فحص سلوكي، لا وجودي)
    try:
        from bot.services import verbatim as v
        sample = "V = I × R حيث R > 0"
        protected = v.protect(sample)
        if not protected.saved:
            problems.append("حماية الرموز لا تحفظ شيئًا")
        elif protected.restore(protected.text, for_html=False) != sample:
            problems.append("حماية الرموز لا تُعيد النص كما هو")
        else:
            ok(f"حفظ الرموز حرفيًا يعمل (اختُبر على {len(protected.saved)} رمزًا)")
    except Exception as exc:                      # noqa: BLE001
        problems.append(f"فحص حفظ الرموز فشل ({type(exc).__name__})")

    if problems:
        for item in problems:
            bad(item)
        return 1
    ok("الميزات مربوطة وسليمة")
    return 0


# ───────────────────────────────── قراءة الملفات المصوّرة
def step_ocr() -> int:
    """قراءة الملفات المصوّرة — لا شيء يُثبَّت.

    كانت هذه الدالة تنزّل tesseract وpoppler كملفات .deb وتستخرجها بلا
    root. أُزيل المسار كليًا: القراءة الآن عبر وكيل الرؤية الذي يختاره
    المستخدم بمفتاحه، وتحويل PDF→صورة داخل بايثون بـPyMuPDF.
    """
    head("قارئ الملفات المصوّرة")
    try:
        import importlib.util

        if importlib.util.find_spec("pymupdf") or importlib.util.find_spec("fitz"):
            ok("جاهز — تحويل PDF→صورة داخل بايثون (بلا poppler)")
        else:
            warn("PyMuPDF غير مثبَّت — الملفات المصوّرة لن تُقرأ")
            info("أصلحه بـ: pip install -r requirements.txt")
        info("القراءة تتم عبر وكيل الرؤية الذي يختاره كل مستخدم بمفتاحه.")
    except Exception as exc:        # noqa: BLE001 — فحص لا يُسقط التجهيز
        info(f"تعذّر الفحص: {type(exc).__name__}")
    return 0


# ─────────────────────────────────────────────────────────── التشغيل
def run_bot() -> int:
    head("4/4 تشغيل البوت")
    info("للايقاف: Ctrl+C")
    print(flush=True)

    # نقطة الدخول الموحّدة (تحتوي التجهيز الذاتي والتنظيف والمنفذ الصحي)
    cmd = [sys.executable, "-u", "main.py"]
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONPATH", str(ROOT))

    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
        return proc.returncode
    except KeyboardInterrupt:
        info("تم إيقاف البوت")
        return 0


# ────────────────────────────────────────────────────────────── الوضع
def _find_bash() -> str | None:
    """يبحث عن bash في PATH وفي المواضع الشائعة (Termux يحتاجه من prefix).

    **لماذا هذا مهم:** `setup.py` كان يعيد تنفيذ منطق `setup.sh` كاملًا
    بالبايثون. النتيجة أن أي إصلاح في `setup.sh` (مثل عِلّة `SC2015`
    التي كانت تطبع «تعذّر التثبيت» بعد النجاح) لا يصل إلى من يستخدم
    `python setup.py`. مصدران للحقيقة = انحراف مؤكّد.
    """
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr")) / "bin" / "bash",
        Path("/bin/bash"),
        Path("/usr/bin/bash"),
        Path("/usr/local/bin/bash"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def should_delegate(argv: list[str], bash: str | None) -> bool:
    """هل نُفوّض إلى setup.sh؟ **قرار صرف** قابل للاختبار بلا آثار جانبية.

    التفويض يحدث فقط لو: (أ) طُلب صراحةً، (ب) bash موجود فعلًا.
    غياب bash ليس فشلًا — بل يُكمل المسار الكامل ببايثون.
    """
    if "--python-only" in argv:
        return False
    requested = any(f in argv for f in ("--install", "--no-start", "--test", "--start"))
    return requested and bash is not None


def run_setup_sh(argv: list[str]) -> int:
    """يفوّض إلى `setup.sh` — مصدر واحد لمنطق الإعداد على كل المنصّات.

    الخيارات التي يفهمها `setup.py` تُترجم إلى نظيراتها في `setup.sh`:
      --install / --no-start  →  دائرة الإعداد فقط (بلا تشغيل)
      --check                 →  --check
      --wispbyte              →  --wispbyte
      --test                  →  --test
    """
    bash = _find_bash()
    if bash is None:
        # الأخطاء تُكتب في stderr لا stdout: من يوجّه المخرج أو يقرأ
        # السجلّات البرمجية يجب أن يرى الفشل في القناة الصحيحة.
        print(f"{C.err}✖{C.reset} bash غير مثبّت — وهو ضروري لإعداد المشروع.",
              file=sys.stderr, flush=True)
        print("  على أندرويد/Termux:  pkg install bash", file=sys.stderr, flush=True)
        print("  على دبيان/أوبنتو:   apt-get install bash", file=sys.stderr, flush=True)
        return 1

    script = ROOT / "setup.sh"
    if not script.is_file():
        bad(f"ملف الإعداد مفقود: {script}")
        return 1

    mapped: list[str] = []
    # الإعداد فقط (بلا تشغيل البوت)
    setup_only = any(flag in argv for flag in ("--install", "--no-start"))
    if setup_only:
        # setup.sh بلا --start = يثبّت ويجهّز ولا يشغّل
        mapped.append("--no-start")
    for flag in ("--check", "--wispbyte", "--test", "--start"):
        if flag in argv:
            mapped.append(flag)
    if "--no-input" in argv:
        mapped.append("--no-input")

    info(f"التفويض إلى setup.sh (bash): {bash}")
    try:
        return subprocess.call([bash, str(script), *mapped], cwd=str(ROOT))
    except KeyboardInterrupt:
        info("تم إيقاف الإعداد")
        return 130
    except OSError as exc:
        bad(f"تعذّر تشغيل setup.sh: {exc}")
        return 1


def main(argv: list[str]) -> int:
    global _FORCE_PANEL
    _FORCE_PANEL = "--wispbyte" in argv
    do_check = "--check" in argv
    no_start = "--no-start" in argv or "--install" in argv
    no_input = "--no-input" in argv or is_container() or "--wispbyte" in argv

    banner("بوت ذاكر — إعداد وتشغيل (بايثون)")
    info(f"المنصّة المكتشفة: {platform_name()}")
    info(f"مجلد المشروع: {ROOT}")
    info(f"بايثون: {sys.version.split()[0]}")

    # التفويض إلى setup.sh عند الطلب الصريح أو على المنصّات التي تعتمد عليه.
    # `--python-only` يسمح بتشغيل نسخة بايثون الصرفة (مفيد للاختبارات).
    # التفويض إلى bash **اختياري لا إجباري**: حاويات كثيرة (Alpine slim،
    # وبعض صور اللوحات) بلا bash إطلاقًا. لو غاب نكمل بمسار بايثون الكامل
    # بدل الفشل — وهو ما يجعل `python setup.py` يعمل على أي منصّة.
    bash = _find_bash()
    if should_delegate(argv, bash):
        return run_setup_sh(argv)
    if bash is None and any(f in argv for f in ("--install", "--no-start", "--test", "--start")):
        info("bash غير موجود — سأكمل بالمسار الكامل ببايثون (لا فرق في النتيجة).")

    if do_check:
        head("فحص البيئة (بلا تعديل)")
        for mod in ("aiogram", "sqlalchemy", "aiosqlite", "alembic",
                    "pypdf", "httpx", "openpyxl", "PIL", "pymupdf"):
            try:
                __import__(mod)
                info(f"✔ {mod}")
            except Exception:  # noqa: BLE001
                info(f"✖ {mod}")
        load_env_file()
        for key in REQUIRED_VARS:
            info(f"{key}: {masked(env_value(key)) if key == 'BOT_TOKEN' else (env_value(key) or '(فاضي)')}")
        return 0

    # التثبيت إجباري دائمًا — هذا هو طلب المستخدم: يعمل كل شيء بلا خطوة يدوية.
    # self_install.py هو المسار **الوحيد**: يثبّت حزم بايثون ويتحقّق منها
    # بالاستيراد الفعلي. لا حزم نظام إطلاقًا — وهو ما يجعل البوت يعمل على
    # Wispbyte وأي حاوية بلا صلاحيات (وهو أصل المشكلة التي حلّها الملف).
    rc = step_self_install()
    if rc != 0:
        return rc

    rc = step_env(no_input=no_input)
    if rc != 0:
        return rc

    rc = step_config_check()
    if rc != 0:
        bad("لن أشغّل البوت بقيم ناقصة — صلّح ما سبق ثم أعد المحاولة.")
        return rc

    rc = step_features()
    if rc != 0:
        bad("لن أشغّل البوت وربط الميزات مكسور — أصلح ما سبق أولًا.")
        return rc

    step_ocr()

    if no_start:
        ok("كل شيء جاهز. للتشغيل: python setup.py")
        return 0

    return run_bot()


USAGE = f"""{C.bold}الاستخدام:{C.reset}
  python setup.py              تثبيت ذاتي + تجهيز + فحص + تشغيل (أي منصّة)
  python main.py               نفس الشيء عبر main.py — نقطة الدخول الموحّدة
  python setup.py --install    إعداد كامل بلا تشغيل (يفوّض إلى setup.sh)
  python setup.py --test       إعداد ثم تشغيل الاختبارات (يفوّض إلى setup.sh)
  python setup.py --python-only نفّذ نسخة بايثون (بلا تفويض)
  python setup.py --no-start   جهّز كل شيء ولا تشغّل
  python setup.py --check      فحص سريع بلا تعديل
  python setup.py --wispbyte   وضع اللوحة (env vars تفوز على .env)
  python setup.py --no-input   بلا أي سؤال
  python setup.py --help

{C.bold}على Wispbyte:{C.reset} اترك Startup Command = `python setup.py`
وأضف BOT_TOKEN و OWNER_ID و STORAGE_CHANNEL في تبويب Variables.
"""


if __name__ == "__main__":
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(USAGE)
        sys.exit(0)
    try:
        sys.exit(main(args))
    except KeyboardInterrupt:
        info("تم إيقاف البوت")
        sys.exit(0)
