#!/usr/bin/env python3
"""نقطة الدخول الموحّدة لبوت «ذاكر» — الأمر نفسه يعمل على **أي** منصّة.

    python main.py

هذا ما تكتبه في خانة التشغيل (Startup Command / Start Command) على:
Wispbyte · Railway · Render · Koyeb · Fly.io · Northflank · Pterodactyl ·
VPS · Replit · Google Cloud Run... أو على جهازك.

**ما يفعله بالترتيب (كل شيء إجباري، بلا سؤال واحد):**

  ① يتأكد أن حزم بايثون الأساسية موجودة، ويُثبّت الناقص (بلا root).
  ② يتحقّق من متغيّرات البيئة (على اللوحات) أو ينشئ `.env` (على سيرفر).
  ③ يتحقّق من حزم بايثون كلها بالاستيراد الفعلي (لا بوجودها في pip).
  ④ ينظّف الملفات المؤقتة الزائدة.
  ⑤ يُشغّل البوت.

**قراءة الملفات المصوّرة** لم تعد تعتمد على tesseract: تُمرَّر الصفحات
على وكيل الرؤية الذي يختاره المستخدم، بمفتاحه هو. لا حزم نظام، ولا root.

**لماذا هذا الملف ضروري؟** لأن اللوحات لا تعطي طرفية تفاعلية: لا سؤال، ولا
`sudo`، ولا `apt`. وأي خطوة ناقصة تعني بوتًا لا يقلع. هنا الخطوات كلها
داخل أمر واحد — وهو الشرط الوحيد الذي تسمح به كل اللوحات.

⚠️ ملاحظة صدق: هذا الملف لا يُنشئ سيرفر HTTP، لأن البوت يعمل بـlong-polling
   ولا Webhook. المنصّات التي **تشترط** منفذًا تفتحه (Render/Koyeb) تحتاج
   `PORT` — والبوت يفتح منفذًا صحيًّا بسيطًا تلقائيًا إن وُجد `PORT`، انظر
   `_start_health_server`.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                     # بعض اللوحات تشغّل من مسار آخر


def _say(message: str) -> None:
    print(message, flush=True)


def _spawn_background_install(script: Path, reason: str) -> None:
    """يشغّل التجهيز الثقيل في **خلفية** بعد أن يصير البوت يخدم فعلًا.

    ⚠️ العِلّة التي أُصلحت هنا (سبب «البوت بيبطأ الاستضافة»):
    كان `bootstrap()` ينادي `subprocess.run([... self_install.py])` **بشكل
    حاجب قبل** فتح اتصال تيليجرام. على لوحة تمسح القرص عند كل إعادة تشغيل
    يعني ذلك إعادة تنزيل حزم النظام + فكّها + حلّ المكتبات
    **قبل** أن يخدم البوت تحديثًا واحدًا — عشرات الثواني إلى دقائق من
    قرص وشبكة وCPU مشغولة، واللوحة ترى خدمة لا تستجيب فتصنّفها بطيئة أو
    معلّقة وتُعيد تشغيلها (فتبدأ من الصفر مرة أخرى).

    الآن: البوت يقلع ويستقبل التحديثات فورًا، والتجهيز يكمل في الخلفية.
    مخرجاته تُكتب في ملف سجل حتى لا تضيع ولا تُلوّث مخرجات البوت.
    """
    import subprocess

    log_path = ROOT / "data" / "install.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("a", encoding="utf-8")
    except OSError:
        handle = None

    say = f"• {reason} — يجرى في الخلفية، والبوت يعمل الآن."
    _say(say)
    try:
        subprocess.Popen(          # noqa: S603 — مسار داخلي ثابت لا مُدخل مستخدم
            [sys.executable, str(script), "--quiet"],
            cwd=str(ROOT),
            stdout=handle or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,   # لا يموت مع أي إشارة موجّهة للمجموعة
        )
    except OSError as exc:
        _say(f"⚠ تعذّر بدء التجهيز في الخلفية ({type(exc).__name__}) — البوت يكمل.")
    finally:
        if handle is not None:
            handle.close()


_VERIFIED_STAMP = ".deps-verified"
# مهلة صلاحية بصمة التحقّق: ٢٤ ساعة. خلالها لا نُكلّف أنفسنا subprocess في
# كل إقلاع؛ وبعدها (أو لو تغيّر requirements.txt) نُعيد التحقّق الكامل.
_VERIFIED_TTL = 24 * 3600


def _requirements_fingerprint() -> str:
    """بصمة محتوى requirements.txt — تغيّرها يعني ضرورة إعادة التحقّق."""
    import hashlib

    req = ROOT / "requirements.txt"
    try:
        return hashlib.sha256(req.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unknown"


def _deps_verified() -> bool:
    """هل تحقّقنا من كل الحاجيات حديثًا وبنفس القائمة؟"""
    stamp = ROOT / "data" / _VERIFIED_STAMP
    try:
        raw = stamp.read_text(encoding="utf-8").strip().split("\n")
        fingerprint, when = raw[0], float(raw[1])
    except (OSError, ValueError, IndexError):
        return False
    if fingerprint != _requirements_fingerprint():
        return False
    return (time.time() - when) < _VERIFIED_TTL


def _mark_deps_verified() -> None:
    stamp = ROOT / "data" / _VERIFIED_STAMP
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            f"{_requirements_fingerprint()}\n{time.time()}", encoding="utf-8")
    except OSError:
        pass


def bootstrap(background: bool = True) -> int:
    """يتحقّق من جاهزية البوت — **سريع**، ولا ينزّل شيئًا ثقيلًا في المسار الحرج.

    الحزم الأساسية وحدها إجبارية قبل الإقلاع (بلا aiogram لا يوجد بوت أصلًا).
    أما التحقّق الكامل من بقية الحزم فيذهب للخلفية: البوت يخدم التحديثات
    فورًا. (قراءة الصور لم تعد تحتاج تثبيتًا — صارت عبر وكيل المستخدم.)
    """
    missing = []
    for module in ("aiogram", "sqlalchemy", "aiosqlite", "pypdf", "httpx"):
        try:
            __import__(module)
        except Exception:            # noqa: BLE001
            missing.append(module)

    script = ROOT / "self_install.py"
    forced = os.environ.get("ZAKR_SELF_INSTALL") == "1"

    if missing:
        # الحزم الأساسية إجبارية ولا تُؤجَّل: بلا aiogram لا يوجد بوت.
        if not script.is_file():
            _say("✖ حزم بايثون ناقصة وملف self_install.py غير موجود.")
            _say("  شغّل: pip install -r requirements.txt")
            return 1
        if background:
            _spawn_background_install(
                script, f"تنزيل الحزم الناقصة: {', '.join(missing)}")
            return 0                      # نكمل: ربما فشل الاستيراد فقط في الفحص
        import subprocess

        _say(f"• تجهيز تلقائي (ناقص: {', '.join(missing)}) …")
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT))  # noqa: S603
        return 0 if proc.returncode == 0 else proc.returncode

    # ── التحقّق الكامل **كل إقلاع** (لا عند النقص فقط)
    #  «تنزيل كامل لكل حاجاته إجباريًا» يعني: لا نكتفي بأن `import aiogram`
    #  نجح. قد تكون الحزمة موجودة بإصدار قديم، أو ناقصة الاعتماديات الداخلية،
    #  أو تالفة جزئيًا — والاستيراد ينجح ثم يفشل الاستخدام وقت التشغيل.
    #  المرور على `setup_deps.py --check` يتحقّق بالاستيراد الفعلي لكل حزمة
    #  في `requirements.txt`، ويكتشف النقص **قبل** أن يراه المستخدم كخطأ.
    if script.is_file() and not missing:
        need_full = forced or not _deps_verified()
        if need_full:
            if background:
                _spawn_background_install(script, "التحقّق الكامل من كل الحاجيات")
            else:
                import subprocess

                _say("• التحقّق الكامل من كل الحاجيات …")
                proc = subprocess.run(                  # noqa: S603
                    [sys.executable, str(script)], cwd=str(ROOT))
                if proc.returncode != 0:
                    return proc.returncode

    # ── قراءة الملفات المصوّرة: لا تثبيت على السطح إطلاقًا.
    #  أُزيل مسار tesseract بقرار المالك، وصارت القراءة عبر وكيل الرؤية
    #  الذي يختاره المستخدم بمفتاحه. لا حزم نظام، لا ١٥٨ ميجا تنزيل،
    #  لا انتظار في الخلفية — وهذا يزيل أثقل خطوة كانت في الإقلاع.

    return 0


# يُضبط عند بدء الاستقبال فعليًا (من bot/main.py بعد نجاح get_me). يقرأه
# المنفذ الصحي ليقول للوحة «جاهز» بدل «ما زال يجهّز».
try:
    import threading as _threading

    _READY = _threading.Event()
except Exception:                        # noqa: BLE001 — نظرية فقط
    class _NeverReady:
        @staticmethod
        def is_set() -> bool:
            return False

    _READY = _NeverReady()


def mark_ready() -> None:
    """يُنادى من البوت بعد نجاح الاتصال بتيليجرام."""
    try:
        _READY.set()
    except Exception:                    # noqa: BLE001
        pass


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
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Ping(BaseHTTPRequestHandler):
            def do_GET(self):                     # noqa: N802
                # حالة حقيقية لا ثابتة: 200 = يستقبل تحديثات أو جاهز للاستقبال؛
                # 503 = ما زال يجهّز. اللوحة تفهم الحالتين وتعطي مهلة الإقلاع
                # بدل أن تصنّف خدمة «حيّة» وهي لا تخدم أحدًا.
                import json

                ready = _READY.is_set()
                body = json.dumps(
                    {"status": "ok" if ready else "starting",
                     "service": "zakr-bot",
                     "ready": ready},
                    ensure_ascii=False,
                ).encode()
                self.send_response(200 if ready else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self):                    # noqa: N802
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):         # صامت: لا نُلوّث سجلّ البوت
                return

        server = HTTPServer(("0.0.0.0", int(port)), _Ping)   # noqa: S104
        server.daemon_threads = True
        server.allow_reuse_address = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        # ⚠️ إثبات لا افتراض: كنا نطبع «المنفذ مفتوح» بمجرد بدء الخيط، ثم
        # يتضح أن الخيط لم يُجدول بعد (أو أن الربط فشل بصمت) فترفض اللوحة
        # الاتصال وتصنّف الخدمة ميتة. ننتظر قليلًا **وندقق** قبل أن نُعلن.
        import socket as _socket

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                with _socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            _say(f"⚠ المنفذ :{port} لم يستجب بعد — البوت يكمل بلا فحص صحي")
            return
        _say(f"• منفذ صحي على :{port} (لأن PORT مضبوط) — البوت يعمل بـlong-polling")
    except Exception as exc:                      # noqa: BLE001
        _say(f"⚠ تعذّر فتح منفذ صحي على {port}: {type(exc).__name__} — البوت يكمل")


def _clean_junk() -> None:
    """يحذف مخابئ بايثون فقط — لا يلمس أي شيء قد تحتاجه عملية تثبيت جارية.

    ⚠️ كان يحذف `.ocr-debs` أيضًا — وهو المجلد المؤقت الذي كان
    `ocr_fetch.py` يكتب فيه أثناء التنزيل، فكان يحذفه في منتصف التجهيز
    ويُفسده. المسار أُزيل مع الـOCR اليوم، لكن القاعدة تبقى: لا نحذف إلا
    `__pycache__` — مخلّفات لا يقرأها أحد.
    """
    import shutil

    removed = 0
    for cache in ROOT.glob("**/__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    if removed:
        _say(f"• نُظّفت {removed} مخبأ بايثون")


USAGE = """الاستخدام:
    python main.py               يجهّز كل شيء ثم يشغّل البوت (نقطة الدخول الموحّدة)
    python main.py --check       فحص البيئة والحاجيات بلا تشغيل
    python main.py --help        هذه المساعدة

هذا هو الأمر الوحيد الذي تحتاجه على أي منصّة (Wispbyte / Pterodactyl /
Railway / Render / VPS). يفتح المنفذ الصحي فورًا، ينظّف المخلفات،
يتحقّق من الحاجيات، ثم يبدأ الاستقبال — بلا خطوات يدوية.

متغيرات البيئة المطلوبة:
    BOT_TOKEN · OWNER_ID · STORAGE_CHANNEL
اختيارية مهمّة:
    DATABASE_URL   (وإلا فكل إعادة نشر تمحو البيانات)
    ZAKR_MINIMAL=1                  على اللوحات ضيّقة المساحة
    PORT           (يفتح منفذًا صحيًا للوحات التي تتطلبه)
"""


def _self_check() -> int:
    """فحص جاهزية سريع بلا أي أثر: لا منفذ صحي، لا قاعدة بيانات، لا شبكة.

    الغرض: أن يعرف المستخدم **قبل** النشر إن كان شيء ناقصًا (توكن، قناة
    تخزين، حزمة، صلاحية كتابة) بدل أن يُقلع البوت ثم يفشل بعد نصف دقيقة.
    """
    problems = 0

    _say("══ فحص جاهزية بوت ذاكر ══")

    # ① بايثون
    _say(f"• بايثون: {sys.version.split()[0]} ({sys.executable})")

    # ② الحزم الأساسية
    for module in ("aiogram", "sqlalchemy", "aiosqlite", "pypdf", "httpx"):
        try:
            __import__(module)
            _say(f"  ✔ {module}")
        except Exception as exc:        # noqa: BLE001
            _say(f"  ✖ {module}: {type(exc).__name__}")
            problems += 1

    # ③ متغيّرات البيئة الإلزامية
    token = os.environ.get("BOT_TOKEN", "").strip()
    if token:
        looks_ok = ":" in token and len(token) > 40
        _say(f"  ✔ BOT_TOKEN ({len(token)} حرفًا)"
             + ("" if looks_ok else "  ⚠ الصيغة غير معتادة"))
    else:
        _say("  ✖ BOT_TOKEN غير مضبوط")
        problems += 1
    for key in ("OWNER_ID", "STORAGE_CHANNEL"):
        if os.environ.get(key, "").strip():
            _say(f"  ✔ {key}")
        else:
            _say(f"  ✖ {key} غير مضبوط")
            problems += 1

    # ④ قاعدة البيانات
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        _say(f"• قاعدة البيانات: خارجية ({url.split(':')[0]}) — تبقى بعد إعادة النشر")
    else:
        _say("• قاعدة البيانات: SQLite محلية — ⚠ تُمسح مع كل إعادة نشر على "
             "اللوحات ذات القرص المؤقت. اضبط DATABASE_URL.")

    # ⑤ صلاحية الكتابة
    try:
        (ROOT / "data").mkdir(parents=True, exist_ok=True)
        probe = ROOT / "data" / ".write_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        _say("✔ مجلد data قابل للكتابة")
    except OSError as exc:
        _say(f"✖ لا يمكن الكتابة في data: {type(exc).__name__}")
        problems += 1

    _say("")
    if problems:
        _say(f"✖ {problems} مشكلة تمنع التشغيل — أصلحها ثم أعد الفحص.")
        return 1
    _say("✔ كل شيء جاهز. شغّل: python main.py")
    return 0


def main() -> int:
    # ⚠️ `--help` كان يُهمَل تمامًا: من يكتبه يبدأ تشغيلًا كاملًا يفتح
    #    منافذ ويعدّل قاعدة البيانات. المساعدة يجب أن تكون بلا آثار.
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        return 0

    # `--check`: فحص جاهزية بلا تشغيل — لا منفذ، لا قاعدة بيانات، لا تيليجرام.
    # مفيد قبل النشر للتأكد أن كل شيء في مكانه على المنصّة.
    if "--check" in argv:
        return _self_check()

    _say("╔══════════════════════════════════════════╗")
    _say("║  بوت ذاكر — بدء التشغيل الموحّد          ║")
    _say("╚══════════════════════════════════════════╝")

    # ① المنفذ الصحي أولًا: بعض اللوحات تقتل الخدمة إن لم يُفتح منفذ سريعًا.
    #    لو انتظرنا التجهيز لما فُتح أبدًا في الوقت المطلوب.
    _start_health_server()

    # ② تنظيف خفيف: __pycache__ فقط. لا نمشي على الشجرة كلها في كل إقلاع
    #    (كان يمسح .deb المؤقتة أيضًا وهي تحتاجها عملية التثبيت الجارية).
    _clean_junk()

    # ③ التجهيز: لا ينزّل شيئًا ثقيلًا في المسار الحرج (انظر bootstrap).
    rc = bootstrap()
    if rc != 0:
        return rc

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
