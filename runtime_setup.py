"""Interactive first-run configuration shared by setup.py and main.py.

Environment variables from hosting panels take precedence over .env.  When a
required value is missing/invalid, ask in the attached console if interactive
(or when ZAKR_INTERACTIVE_SETUP=1 is explicitly enabled by the panel).
"""
from __future__ import annotations

import getpass
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
REQUIRED = ("BOT_TOKEN", "OWNER_ID", "STORAGE_CHANNEL")


def _placeholder(key: str, value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    if key == "BOT_TOKEN":
        return "aaexample" in v.lower() or "replaceme" in v.lower() or v.startswith("123456789:")
    if key == "OWNER_ID":
        return v == "123456789"
    if key == "STORAGE_CHANNEL":
        return v == "-1001234567890"
    return False


def _valid(key: str, value: str) -> bool:
    value = value.strip()
    if key == "BOT_TOKEN":
        return bool(re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", value)) and not _placeholder(key, value)
    if key == "OWNER_ID":
        return value.isdigit() and value != "0" and not _placeholder(key, value)
    if key == "STORAGE_CHANNEL":
        return bool(re.fullmatch(r"-100\d+|@[A-Za-z0-9_]{4,}", value)) and not _placeholder(key, value)
    return bool(value)


def _read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return values
    for raw in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        raw_value = raw_value.strip()
        try:
            if raw_value.startswith(("'", '"')):
                parts = shlex.split(raw_value, comments=True, posix=True)
                value = parts[0] if parts else ""
            else:
                value = raw_value.split(" #", 1)[0].strip()
        except ValueError:
            continue
        values[key] = value
    return values


def _canonicalize_environment() -> None:
    """Accept canonical names plus common ZAKR_* aliases, case-insensitively."""
    by_upper = {k.upper(): v for k, v in os.environ.items()}
    for key in REQUIRED:
        current = os.environ.get(key, "").strip()
        if current:
            os.environ[key] = current
            continue
        alias = by_upper.get("ZAKR_" + key) or by_upper.get(key)
        if alias and alias.strip():
            os.environ[key] = alias.strip().strip("\"'")


def load_configuration() -> None:
    """Load .env defaults; valid panel values win, invalid ones can fall back.

    This matters when a panel contains an old/placeholder value: it must not
    permanently mask a corrected value entered in the live console and saved
    to .env.
    """
    file_values = _read_dotenv()
    for key, value in file_values.items():
        current = os.environ.get(key, "").strip()
        if not current or (key in REQUIRED and not _valid(key, current) and _valid(key, value)):
            os.environ[key] = value
    _canonicalize_environment()


def _save(values: dict[str, str]) -> None:
    current = _read_dotenv()
    current.update(values)
    # Preserve comments and unrelated settings while replacing required keys.
    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines() if ENV_FILE.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in values:
                if key not in seen:
                    out.append(f"{key}={values[key]}")
                    seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass
    for key, value in values.items():
        os.environ[key] = value


def _can_prompt() -> bool:
    if os.environ.get("ZAKR_NO_PROMPT", "").lower() in {"1", "true", "yes"}:
        return False
    if os.environ.get("ZAKR_INTERACTIVE_SETUP", "").lower() in {"1", "true", "yes", "on"}:
        return not sys.stdin.closed
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def prompt_missing(*, force: bool = False) -> list[str]:
    """Prompt only for missing/invalid required values; returns unresolved keys."""
    load_configuration()
    unresolved = [k for k in REQUIRED if not _valid(k, os.environ.get(k, ""))]
    if not unresolved:
        return []
    if not (force or _can_prompt()):
        print("⚠ لا توجد طرفية إدخال تفاعلية. أعد التشغيل من Console تفاعلي، أو فعّل ZAKR_INTERACTIVE_SETUP=1.", flush=True)
        return unresolved

    print("\n⚙️ إعداد بوت ذاكر — أدخل القيم الناقصة أو غير الصحيحة فقط.", flush=True)
    print("اترك الحقل فارغًا لإعادة المحاولة لاحقًا. لن يتم عرض التوكن بعد إدخاله.\n", flush=True)
    labels = {
        "BOT_TOKEN": "BOT_TOKEN (توكن @BotFather)",
        "OWNER_ID": "OWNER_ID (رقم حسابك)",
        "STORAGE_CHANNEL": "STORAGE_CHANNEL (آيدي القناة -100... أو @username)",
    }
    values: dict[str, str] = {}
    for key in unresolved:
        while True:
            try:
                if key == "BOT_TOKEN" and sys.stdin.isatty():
                    answer = getpass.getpass(labels[key] + ": ").strip()
                else:
                    answer = input(labels[key] + ": ").strip()
            except (EOFError, OSError):
                answer = ""
            if not answer:
                break
            if _valid(key, answer):
                values[key] = answer
                break
            print(f"✖ صيغة {key} غير صحيحة؛ حاول مرة أخرى.", flush=True)
    if values:
        _save(values)
        print("✔ تم حفظ القيم المدخلة في .env وتحديث بيئة التشغيل.", flush=True)
    load_configuration()
    return [k for k in REQUIRED if not _valid(k, os.environ.get(k, ""))]


def diagnose() -> None:
    """Print safe diagnostics (presence/source/length only; never secrets)."""
    load_configuration()
    aliases = {k.upper(): k for k in os.environ}
    print("• فحص متغيرات الإعداد (دون طباعة الأسرار):", flush=True)
    for key in REQUIRED:
        value = os.environ.get(key, "").strip()
        source = "Environment/Panel" if value and key in os.environ else ".env/غير موجود"
        state = "صالح شكليًا" if _valid(key, value) else ("موجود لكن غير صالح" if value else "غير موجود")
        print(f"  {key}: {state} | المصدر: {source} | الطول: {len(value)}", flush=True)
