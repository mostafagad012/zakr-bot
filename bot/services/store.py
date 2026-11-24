"""نقطة وصول واحدة لخدمة التخزين (تُهيَّأ مرة واحدة عند الإقلاع)."""
from __future__ import annotations

from aiogram import Bot

from .storage import TelegramStorage

_backend = None


def set_backend(backend) -> None:
    global _backend
    _backend = backend


def get_backend():
    if _backend is None:
        raise RuntimeError("لم يتم تهيئة التخزين — نادِ set_backend() أولًا")
    return _backend


def init_telegram_backend(bot: Bot, channel: str):
    backend = TelegramStorage(bot, channel)
    set_backend(backend)
    return backend

