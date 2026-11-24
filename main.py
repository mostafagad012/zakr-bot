#!/usr/bin/env python3
"""نقطة الدخول الموحّدة لبوت «ذاكر» — الأمر نفسه يعمل على **أي** منصّة.

    python main.py

هذا ما تكتبه في خانة التشغيل (Startup Command / Start Command) على:
Wispbyte · Railway · Render · Koyeb · Fly.io · Northflank · Pterodactyl ·
VPS · Replit · Google Cloud Run... أو على جهازك.

**ما يفعله بالترتيب:**
  ⓪ يفحص الإعدادات الأساسية ويطلب القيم الناقصة من Console إذا كانت تفاعلية.

  ① يتأكد أن حزم بايثون الأساسية موجودة، ويُثبّت الناقص (بلا root).
  ② يتحقّق من متغيّرات البيئة (على اللوحات) أو ينشئ `.env` (على سيرفر).
  ③ يوصّل tesseract/poppler لقراءة الملفات المصوّرة — بلا root:
     تنزيل .deb + استخراج في `.ocr/` (أو pkg على أندرويد).
  ④ ينظّف الملفات المؤقتة الزائدة.
  ⑤ يُشغّل البوت.

**لماذا هذا الملف ضروري؟** لأن اللوحات لا تعطي طرفية تفاعلية: لا سؤال، ولا
`sudo`، ولا `apt`. وأي خطوة ناقصة تعني بوتًا لا يقلع. هنا الخطوات كلها
داخل أمر واحد — وهو الشرط الوحيد الذي تسمح به كل اللوحات.

⚠️ ملاحظة: إذا كانت لوحة الاستضافة لا تمرر stdin، فعّل ZAKR_INTERACTIVE_SETUP=1
   إن كانت Console تدعم الإدخال، أو أدخل القيم في Variables.

⚠️ ملاحظة صدق: هذا الملف لا يُنشئ سيرفر HTTP، لأن البوت يعمل بـlong-polling
   ولا Webhook. المنصّات التي **تشترط** منفذًا تفتحه (Render/Koyeb) تحتاج
   `PORT` — والبوت يفتح منفذًا صحيًّا بسيطًا تلقائيًا إن وُجد `PORT`، انظر
   `_start_health_server`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                     # بعض اللوحات تشغّل من مسار آخر


def _say(message: str) -> None:
    print(message, flush=True)


def bootstrap() -> int:
    """تهيئة صارمة على Wispbyte؛ لا تشغيل قبل نجاح كل متطلبات المنصة."""
    # setup.sh/setup.py قد أنهيا التثبيت والفحص بالفعل؛ لا نكرر تنزيل الحزم.
    if os.environ.get("ZAKR_SETUP_COMPLETE") == "1":
        _say("• تم تجهيز المنصة وفحصها بواسطة setup script — تخطي التثبيت المكرر.")
        return 0

    is_wispbyte = (
        os.environ.get("ZAKR_WISPBYTE_MODE") == "1"
        or os.environ.get("WISPBYTE") == "1"
        or Path("/home/container").is_dir()
    )
    if is_wispbyte:
        os.environ["ZAKR_WISPBYTE_MODE"] = "1"
        os.environ["ZAKR_INSTALL_OCR"] = "1"
        os.environ.pop("ZAKR_SKIP_OCR", None)
        script = ROOT / "self_install.py"
        if not script.is_file():
            _say("✖ self_install.py غير موجود؛ لا يمكن تجهيز Wispbyte.")
            return 1
        import subprocess
        _say("• Wispbyte: تثبيت وفحص جميع المكتبات وأدوات OCR قبل التشغيل …")
        proc = subprocess.run([sys.executable, "-u", str(script)], cwd=str(ROOT))
        if proc.returncode != 0:
            _say("✖ فشل التجهيز الإجباري. تم إيقاف التشغيل لمنع تشغيل نسخة ناقصة.")
            return proc.returncode or 1
        return 0

    required_modules = ["aiogram", "sqlalchemy", "aiosqlite", "pypdf", "httpx"]
    database_url = os.environ.get("DATABASE_URL", "").lower()
    if database_url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        required_modules.append("asyncpg")
    if os.environ.get("ZAKR_INSTALL_OCR") == "1":
        required_modules.extend(["PIL", "numpy", "pytesseract", "pdf2image"])
    if os.environ.get("ZAKR_INSTALL_OPTIONAL") == "1":
        required_modules.extend(["PIL", "numpy", "pytesseract", "pdf2image", "openpyxl"])

    missing = []
    for module in required_modules:
        try:
            __import__(module)
        except Exception:            # noqa: BLE001
            missing.append(module)

    script = ROOT / "self_install.py"
    forced = os.environ.get("ZAKR_SELF_INSTALL") == "1"

    # ── OCR أيضًا يُفحص هنا، لا وقت حاجته
    # سبب حقيقي: صورة فيها حزم بايثون (pip install -r) بلا tesseract ⇒
    # كان البوت يقلع ويقول «OCR معطّل» عند أول صورة، بلا محاولة إصلاح.
    # الآن نحاول التوفير عند الإقلاع — مرة واحدة فقط (علامة `.ocr/.attempted`)
    # كي لا نُعيد تنزيل ١٦٠ ميجا عند كل إعادة تشغيل على لوحة تمنعه.
    ocr_needed = False
    if os.environ.get("ZAKR_INSTALL_OCR") == "1" and os.environ.get("ZAKR_SKIP_OCR") != "1":
        try:
            from bot.services import ocr as _ocr

            if not _ocr.is_available():
                marker = ROOT / ".ocr" / ".attempted"
                if forced or os.environ.get("ZAKR_FORCE_OCR") == "1" or not marker.exists():
                    ocr_needed = True
                    _say("• قارئ الملفات المصوّرة غير متوفّر — محاولة توفيره بلا root …")
        except Exception:            # noqa: BLE001 — لا نُسقط الإقلاع لفحص OCR
            pass

    if not missing and not forced and not ocr_needed:
        return 0                  # كل شيء موجود — لا نُضيّع ثانية واحدة

    if not script.is_file():
        _say("✖ حزم بايثون ناقصة وملف self_install.py غير موجود.")
        _say("  شغّل: pip install -r requirements.txt")
        return 1

    _say(f"• تجهيز تلقائي (ناقص: {', '.join(missing) or 'كل شيء موجود'}) …")
    import subprocess

    proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT))

    # علامة المحاولة: تمنع إعادة التنزيل الثقيل عند كل إعادة تشغيل على منصّة
    # تمنع الحزم. تُحذف تلقائيًا لو نجح التوفير (المجلد يُنشأ مع tesseract).
    if ocr_needed:
        try:
            marker = ROOT / ".ocr" / ".attempted"
            from bot.services import ocr as _ocr

            if not _ocr.is_available():
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("تعذّر توفير كل مكوّنات OCR", encoding="utf-8")
        except Exception:        # noqa: BLE001
            pass

    if proc.returncode != 0:
        _say("⚠ تعذّر إكمال التجهيز العام — سأتحقق من حزم الإقلاع الأساسية مباشرة.")

    # بعض لوحات الاستضافة تشغّل pip داخل بيئة مختلفة أو يتعطّل فيها
    # المُثبّت الموحّد. نستخدم نفس مفسّر Python الذي سيشغّل البوت، ونثبت
    # aiogram صراحةً لأنه إطار البوت الأساسي وليس إضافة اختيارية.
    import importlib.util

    fallbacks = {
        "aiogram": "aiogram>=3.13,<4",
        "sqlalchemy": "SQLAlchemy[asyncio]>=2.0",
        "aiosqlite": "aiosqlite>=0.20",
        "pypdf": "pypdf>=5.0",
        "httpx": "httpx>=0.27",
    }
    for module, requirement in fallbacks.items():
        try:
            present = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            present = False
        if present:
            continue
        _say(f"• تثبيت الاعتمادية الأساسية الناقصة: {module} …")
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", requirement],
            cwd=str(ROOT),
        )
        if install.returncode != 0:
            _say(f"✖ فشل تثبيت {module}. تأكد أن الاستضافة تسمح بـpip والاتصال بمستودع PyPI.")
            return install.returncode or 1

    try:
        import aiogram  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        _say(f"✖ aiogram ما زال غير قابل للاستيراد بعد التثبيت: {exc}")
        return 1
    return 0


def _start_health_server() -> None:
    """يفتح منفذًا صحيًّا لو المنصّة تشترطه (PORT) — لا يؤثّر على البوت.

    بعض المنصّات (Render, Koyeb, Cloud Run) تُسقط الخدمة إن لم تفتح منفذًا
    خلال مهلة. البوت لا يحتاج HTTP، لكنه يفتح واحدًا **يقرأ فقط** ليقول
    «أنا حيّ» بدل أن يُقتل. يُشغَّل في خيط خفي ولا يتعارض مع long-polling.
    """
    port = os.environ.get("PORT", "").strip()
    if not port.isdigit():
        return
    try:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _Ping(BaseHTTPRequestHandler):
            def do_GET(self):                     # noqa: N802
                body = b'{"status":"ok","service":"zakr-bot"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self):                    # noqa: N802
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):         # صامت: لا نُلوّث سجلّ البوت
                return

        server = ThreadingHTTPServer(("0.0.0.0", int(port)), _Ping)   # noqa: S104
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _say(f"• منفذ صحي على :{port} (لأن PORT مضبوط) — البوت يعمل بـlong-polling")
    except Exception as exc:                      # noqa: BLE001
        _say(f"⚠ تعذّر فتح منفذ صحي على {port}: {type(exc).__name__} — البوت يكمل")


def _clean_junk() -> None:
    """يحذف مخلّفات لا يحتاجها البوت ويستهلك مساحة الحاوية (حدود اللوحات ضيّقة)."""
    import shutil

    removed = 0
    for pattern in ("**/__pycache__", ".ocr-debs", ".get-pip.py"):
        for path in ROOT.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed += 1
    if removed:
        _say(f"• نُظّفت {removed} مخلّفات (__pycache__/deb مؤقتة)")


def main() -> int:
    # اسأل قبل bootstrap/import bot.config حتى لا تُحمّل Settings بقيم قديمة.
    try:
        from runtime_setup import prompt_missing, diagnose
        unresolved = prompt_missing()
        if unresolved:
            diagnose()
            _say("✖ لا يمكن تشغيل البوت قبل استكمال الإعدادات: " + ", ".join(unresolved))
            return 1
    except Exception as exc:  # لا نخفي سبب فشل التهيئة
        _say(f"✖ تعذّر فحص الإعدادات: {type(exc).__name__}: {exc}")
        return 1

    _say("╔══════════════════════════════════════════╗")
    _say("║  بوت ذاكر — بدء التشغيل الموحّد          ║")
    _say("╚══════════════════════════════════════════╝")

    rc = bootstrap()
    if rc != 0:
        return rc

    _clean_junk()
    _start_health_server()

    # نستورد بعد التجهيز فقط — أي حزمة ناقصة تعني رسالة واضحة هنا لا انهيارًا
    try:
        import asyncio

        from bot.main import main as bot_main
    except ImportError as exc:
        _say(f"✖ تعذّر تحميل البوت: {exc}")
        _say("  شغّل: python self_install.py   ثم أعد المحاولة.")
        return 1

    try:
        return asyncio.run(bot_main())
    except KeyboardInterrupt:
        _say("• تم إيقاف البوت")
        return 0


if __name__ == "__main__":
    sys.exit(main())
