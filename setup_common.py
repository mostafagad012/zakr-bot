"""أدوات مشتركة لسكربتات الإعداد — مصدر واحد للحقيقة.

قبل هذا الملف كانت دوال الطباعة الملوّنة (`ok`/`warn`/`bad`/`info`) وكشف
المنصّة (`is_termux`) **منسوخة حرفيًا** في ثلاثة ملفات: `setup.py`،
`setup_deps.py`، وسواها. أي تعديل على شكل رسالة كان يجب أن يُكرّر
ثلاث مرات — ونسيان واحدة يعني رسائل غير متسقة للمستخدم.

الآن الثلاثة تستورد من هنا.

⚠️ قيد مهم: هذا الملف لا يستورد أي حزمة خارجية، لأن `setup_deps.py`
   يعمل **قبل** تثبيت الحاجيات. بايثون القياسي فقط.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ─────────────────────────────────────────────────────── الطباعة
class C:
    """ألوان ANSI تُلغى تلقائيًا لو المخرج ليس طرفية (سجلّات اللوحة)."""

    _on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    ok = "\033[1;32m" if _on else ""
    warn = "\033[1;33m" if _on else ""
    err = "\033[1;31m" if _on else ""
    dim = "\033[2m" if _on else ""
    bold = "\033[1m" if _on else ""
    reset = "\033[0m" if _on else ""


def say(message: str = "") -> None:
    """يطبع سطرًا ويُفرّغ المخرج فورًا (مهم لسجلّات اللوحة الحيّة)."""
    print(message, flush=True)


def ok(message: str) -> None:
    say(f"{C.ok}✔{C.reset} {message}")


def warn(message: str) -> None:
    say(f"{C.warn}⚠{C.reset} {message}")


def bad(message: str) -> None:
    say(f"{C.err}✖{C.reset} {message}")


def info(message: str) -> None:
    say(f"{C.dim}•{C.reset} {message}")


def head(message: str) -> None:
    say(f"\n{C.bold}══ {message} ══{C.reset}")


def banner(title: str) -> None:
    line = "═" * 42
    say(f"{C.bold}╔{line}╗{C.reset}")
    say(f"{C.bold}║ {title}{C.reset}")
    say(f"{C.bold}╚{line}╝{C.reset}")


# ─────────────────────────────────────────────────── كشف المنصّة
def is_termux() -> bool:
    """هل نحن داخل Termux (أندرويد)؟"""
    return bool(os.environ.get("TERMUX_VERSION")) or "com.termux" in os.environ.get("PREFIX", "")


def is_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


def is_container() -> bool:
    """Wispbyte / Pterodactyl: المشروع في /home/container."""
    target = Path("/home/container")
    return target.is_dir() and not target.is_symlink()


def platform_name(*, force_panel: bool = False) -> str:
    """يسمّي المنصّة — يُستخدم في الرسائل وفي اختيار المسار."""
    if force_panel or os.environ.get("WISPBYTE"):
        return "wispbyte"
    if is_vercel():
        return "vercel"
    if is_termux():
        return "termux"
    if is_container():
        return "wispbyte"
    return "server"


# ─────────────────────────────────────────── أسرار لا تُطبع كاملة
def masked(value: str, *, keep: int = 6) -> str:
    """يقنّع قيمة حسّاسة (توكن/مفتاح) — لا تُطبع كاملة أبدًا."""
    if not value:
        return "(فاضي)"
    if len(value) <= keep + 4:
        return value[:2] + "…" + f" ({len(value)} حرفًا)"
    return f"{value[:keep]}…{value[-4:]} ({len(value)} حرفًا)"


# ──────────────────────────────────── متغيّرات .env.example المثالّية
#  سبب وجودها: لو ظنّها البوت صحيحة، يبدأ ثم يفشل عند الاتصال بتيليجرام
#  برسالة غامضة — على سيرفر بلا واجهة هذا أسوأ سيناريو ممكن.
PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "BOT_TOKEN": ("AAExample", "ReplaceMe"),
    "OWNER_ID": ("123456789",),
    "STORAGE_CHANNEL": ("-1001234567890",),
}


def is_placeholder(key: str, value: str) -> bool:
    """هل القيمة لا تزال مثالًا من .env.example؟"""
    return any(marker in (value or "") for marker in PLACEHOLDERS.get(key, ()))
