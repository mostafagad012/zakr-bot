#!/usr/bin/env python3
"""تنظيف المشروع وتقليل حجمه — آمن، قابل للتكرار، ومُختبَر.

**المشكلة:** الحجم الذي يهمّ فعلًا هو **الحجم المرسَل إلى المنصّة**، لا حجم
مجلد على جهازك. مخلفات مسار القراءة القديم (`.ocr/` + `models/`)
١٩٠ ميجا في النسخ القديمة، ولوحات مثل Wispbyte تحدّ المساحة والذاكرة،
والرفع البطيء يفشل في منتصفه.

**القانون الذي يتبعه هذا الملف:** لا يُحذف شيء يحتاجه التشغيل.
كل ما يُحذف إمّا (أ) يُعاد بناؤه تلقائيًا، أو (ب) للتطوير فقط.

    python scripts/clean.py            # ينظّف ويطبع ما وفّره
    python scripts/clean.py --dry-run  # يعرض فقط، لا يحذف
    python scripts/clean.py --deep     # + حذف مخلفات OCR القديمة
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (المسار, السبب, هل يُعاد بناؤه تلقائيًا؟)
TARGETS: tuple[tuple[str, str], ...] = (
    ("**/__pycache__", "مترجم بايثون — يُعاد إنشاؤه"),
    (".pytest_cache", "مخرجات الاختبارات"),
    (".ruff_cache", "مخرجات الفحص"),
    (".ocr-debs", "ملفات .deb مؤقتة من التثبيت القديم"),
    (".get-pip.py", "سكربت تنزيل مؤقت"),
    (".requirements-android.txt", "قائمة مؤقتة على أندرويد"),
    (".requirements-android-optional.txt", "قائمة مؤقتة على أندرويد"),
    ("*.deb", "حزم ديبيان — تُنزَّل عند الحاجة"),
    ("*.pyc", "بايت كود"),
    (".setup.lock", "قفل إعداد — يُنشأ من جديد"),
    ("data/.heartbeat", "نبضة حياة — تُكتب كل دقيقة"),
    ("data/*.db-wal", "ملف مؤقت لقاعدة SQLite"),
    ("data/*.db-shm", "ملف مؤقت لقاعدة SQLite"),
)

# للتطوير فقط — تُحذف بـ--deep وحدها
DEEP_TARGETS: tuple[tuple[str, str], ...] = (
    # `.ocr/` و`models/*.onnx` بقيتا هنا لأن نسخًا قديمة من المشروع قد
    # تحمل ١٩٠ ميجا من مخلفات مسار tesseract المُزال. حذفها آمن تمامًا
    # الآن — لا شيء يقرأها.
    (".ocr", "مخلفات التثبيت القديم (~160MB) — لا شيء يقرأها بعد إزالة OCR"),
    ("models/*.onnx", "نموذج فكّ الالتواء القديم (~32MB) — لم يبقَ له مستخدم"),
    ("models/*.onnx.tmp", "بقايا تنزيل ناقص"),
)

# ما **لا** يُلمس أبدًا مهما حدث
PROTECTED = (
    ".env",
    "data/library_bot.db",
    "backups",
    "uploads",
)


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def collect(patterns) -> list[Path]:
    found: list[Path] = []
    for pattern, _reason in patterns:
        found += [p for p in ROOT.glob(pattern) if p.exists()]
    # لا نحذف ما هو داخل شيء محمي
    safe: list[Path] = []
    for path in found:
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == keep or rel.startswith(keep + "/") for keep in PROTECTED):
            continue
        safe.append(path)
    return sorted(set(safe))


def remove(path: Path, *, dry: bool) -> int:
    freed = size_of(path)
    if dry:
        return freed
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    return freed


USAGE = """الاستخدام:
    python scripts/clean.py              ينظّف ويطبع ما وفّره
    python scripts/clean.py --dry-run    يعرض ما سيُحذف فقط
    python scripts/clean.py --deep       + حذف مخلفات OCR القديمة (.ocr/models)
    python scripts/clean.py --help       هذه المساعدة

ما يُحذف: مخبأ بايثون، مخبأ الاختبارات/الفحص، ملفات .deb المؤقتة،
نبضة الحياة، ملفات SQLite المؤقتة. **لا** يُحذف شيء يحتاجه التشغيل:
قاعدة البيانات والنسخ الاحتياطية و.env تبقى كما هي.
"""


def main(argv: list[str]) -> int:
    # `--help` كان يُهمَل فيبدأ الحذف فعليًا عند طلب المساعدة.
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        return 0

    dry = "--dry-run" in argv
    deep = "--deep" in argv

    targets = list(TARGETS) + (list(DEEP_TARGETS) if deep else [])
    paths = collect(targets)

    before = size_of(ROOT)
    print(f"• حجم المشروع قبل: {human(before)}")
    if not paths:
        print("✔ لا شيء لتنظيفه — المشروع نظيف بالفعل.")
        return 0

    freed = 0
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        freed += remove(path, dry=dry)
        print(f"  {'(سيُحذف)' if dry else 'حُذف'} {rel}")

    if deep:
        # المجلدات الفارغة تبقى بعد الحذف — ننظّفها
        for folder in ROOT.rglob("*"):
            if folder.is_dir() and not any(folder.iterdir()) and folder.name != ".git":
                if not dry:
                    folder.rmdir()
    after = size_of(ROOT) if not dry else before - freed
    label = "سيُوفَّر" if dry else "وُفِّر"
    print(f"\n✔ {label}: {human(freed)}  →  الحجم بعد: {human(after)}")
    if not deep:
        print("• لتقليل أكبر (وحذف مخلفات تثبيت tesseract القديم): أضف --deep")
        print("  لا شيء يقرأها الآن — القراءة عبر وكيل الذكاء الاصطناعي.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
