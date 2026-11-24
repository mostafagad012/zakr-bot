"""النسخ الاحتياطي لقاعدة البيانات.

SQLite: نسخة آمنة عبر sqlite3.backup (لا تُفسد قاعدة بيانات نشطة) + تدوير النسخ.
PostgreSQL: نفّذ pg_dump خارجيًا — نفس الواجهة ترجع المسار.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def sqlite_backup(db_path: str | Path, dest_dir: str | Path, keep: int = 14) -> Path:
    db_path = Path(db_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise FileNotFoundError(f"قاعدة البيانات غير موجودة: {db_path}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"{db_path.stem}-{stamp}.db"
    # ضمان تفرّد الاسم لو حدث أكثر من نسخ احتياطي في نفس الثانية
    suffix = 1
    while target.exists():
        target = dest_dir / f"{db_path.stem}-{stamp}-{suffix}.db"
        suffix += 1

    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    # نسخ الملفات المرافقة إن وُجدت (WAL/SHM) — لتكتمل الصورة
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, target.with_name(target.name + suffix))

    rotate(dest_dir, keep=keep, pattern=f"{db_path.stem}-*.db")
    log.info("نسخة احتياطية: %s", target)
    return target


def rotate(dest_dir: str | Path, keep: int = 14, pattern: str = "*.db") -> list[Path]:
    """يحذف أقدم النسخ بما يبقي العدد المحدد فقط."""
    dest_dir = Path(dest_dir)
    if keep <= 0:
        return []
    backups = sorted(dest_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for old in backups[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError as exc:
            log.warning("تعذّر حذف النسخة القديمة %s: %s", old, exc)
    return removed


def list_backups(dest_dir: str | Path, pattern: str = "*.db") -> list[Path]:
    return sorted(Path(dest_dir).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)


def restore(backup_path: str | Path, db_path: str | Path) -> Path:
    """يستعيد نسخة إلى مسار قاعدة البيانات (يوقف البوت أولًا!)."""
    backup_path, db_path = Path(backup_path), Path(db_path)
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, db_path)
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            side.unlink()
    return db_path
