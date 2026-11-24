"""Lightweight deployment smoke tests; no Telegram credentials required."""
from __future__ import annotations

from bot.config import _normalize_database_url
from setup_deps import pkg_name


def test_postgres_urls_from_hosting_panels_are_async_driver_urls() -> None:
    assert _normalize_database_url("postgres://user:pass@host/db") == (
        "postgresql+asyncpg://user:pass@host/db"
    )
    assert _normalize_database_url("postgresql://user:pass@host/db") == (
        "postgresql+asyncpg://user:pass@host/db"
    )


def test_database_url_already_using_async_driver_is_unchanged() -> None:
    url = "postgresql+asyncpg://user:pass@host/db"
    assert _normalize_database_url(url) == url


def test_sqlite_url_is_unchanged() -> None:
    url = "sqlite+aiosqlite:///./data/library_bot.db"
    assert _normalize_database_url(url) == url


def test_package_parser_handles_extras_and_version_ranges() -> None:
    assert pkg_name("SQLAlchemy[asyncio]>=2.0") == "sqlalchemy"
    assert pkg_name("aiogram>=3.13,<4") == "aiogram"


def test_wispbyte_requirements_include_all_feature_dependencies() -> None:
    from pathlib import Path

    requirements = Path(__file__).resolve().parents[1] / "requirements-wispbyte.txt"
    content = requirements.read_text(encoding="utf-8").lower()
    for package in ("alembic", "asyncpg", "pillow", "numpy", "pytesseract",
                    "pdf2image", "openpyxl", "onnxruntime", "fastembed"):
        assert package in content


def test_setup_deps_detects_wispbyte_from_environment(monkeypatch) -> None:
    import setup_deps

    monkeypatch.setenv("WISPBYTE", "1")
    assert setup_deps.is_wispbyte() is True


def test_setup_deps_wispbyte_delegates_to_full_installer(monkeypatch) -> None:
    import setup_deps

    called = {}
    monkeypatch.setattr(setup_deps, "is_wispbyte", lambda: True)
    monkeypatch.setattr(setup_deps, "run_wispbyte_installer", lambda: called.setdefault("rc", 0))
    assert setup_deps.main([]) == 0
    assert called["rc"] == 0


def test_ocr_fetch_maps_liblept_soname_to_supported_debian_packages() -> None:
    from ocr_fetch import PACKAGE_ALIASES

    assert "libleptonica5" in PACKAGE_ALIASES["liblept5"]
    assert "libleptonica6" in PACKAGE_ALIASES["liblept5"]


def test_ocr_fetch_includes_leptonica_runtime_package() -> None:
    from ocr_fetch import PACKAGES

    assert "libleptonica5" in PACKAGES


def test_dotenv_supports_export_quotes_comments_and_bom(tmp_path, monkeypatch) -> None:
    from bot.config import _load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text(
        '\ufeffexport ZAKR_TEST_QUOTED="value with spaces"\n'
        "ZAKR_TEST_PLAIN=plain-value # trailing comment\n"
        "# ignored\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ZAKR_TEST_QUOTED", raising=False)
    monkeypatch.delenv("ZAKR_TEST_PLAIN", raising=False)

    _load_dotenv(env_file)

    assert __import__("os").environ["ZAKR_TEST_QUOTED"] == "value with spaces"
    assert __import__("os").environ["ZAKR_TEST_PLAIN"] == "plain-value"


def test_dotenv_does_not_override_host_environment(tmp_path, monkeypatch) -> None:
    from bot.config import _load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("ZAKR_TEST_PRECEDENCE=file-value\n", encoding="utf-8")
    monkeypatch.setenv("ZAKR_TEST_PRECEDENCE", "host-value")

    _load_dotenv(env_file)

    assert __import__("os").environ["ZAKR_TEST_PRECEDENCE"] == "host-value"


def test_env_example_contains_all_required_placeholder_keys() -> None:
    from pathlib import Path
    from setup_common import is_placeholder

    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert example.is_file()
    values = {}
    for line in example.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    assert {"BOT_TOKEN", "OWNER_ID", "STORAGE_CHANNEL"} <= values.keys()
    assert all(is_placeholder(key, values[key]) for key in (
        "BOT_TOKEN", "OWNER_ID", "STORAGE_CHANNEL"
    ))


def test_runtime_setup_accepts_zakr_prefixed_environment(monkeypatch):
    from runtime_setup import load_configuration

    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("ZAKR_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyz")
    load_configuration()
    assert __import__("os").environ["BOT_TOKEN"] == "123456:abcdefghijklmnopqrstuvwxyz"


def test_runtime_setup_validates_all_required_values():
    from runtime_setup import _valid

    assert _valid("BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyz")
    assert not _valid("BOT_TOKEN", "123456:AAExampleTokenReplaceThisValue")
    assert _valid("OWNER_ID", "987654321")
    assert not _valid("OWNER_ID", "123456789")
    assert _valid("STORAGE_CHANNEL", "-100987654321")
    assert _valid("STORAGE_CHANNEL", "@my_storage_channel")


def test_runtime_setup_prompts_and_persists_missing_values(tmp_path, monkeypatch):
    import builtins
    import os
    import runtime_setup

    env_file = tmp_path / ".env"
    monkeypatch.setattr(runtime_setup, "ENV_FILE", env_file)
    for key in runtime_setup.REQUIRED:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("ZAKR_" + key, raising=False)
    answers = iter(("123456:abcdefghijklmnopqrstuvwxyz", "987654321", "-100987654321"))
    monkeypatch.setattr(runtime_setup, "_can_prompt", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))

    assert runtime_setup.prompt_missing() == []
    assert os.environ["BOT_TOKEN"] == "123456:abcdefghijklmnopqrstuvwxyz"
    saved = env_file.read_text(encoding="utf-8")
    assert "OWNER_ID=987654321" in saved
    assert "STORAGE_CHANNEL=-100987654321" in saved


def test_runtime_setup_valid_dotenv_replaces_invalid_panel_value(tmp_path, monkeypatch):
    import os
    import runtime_setup

    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_TOKEN=123456:abcdefghijklmnopqrstuvwxyz\n"
        "OWNER_ID=987654321\nSTORAGE_CHANNEL=-100987654321\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_setup, "ENV_FILE", env_file)
    monkeypatch.setenv("BOT_TOKEN", "not-a-valid-token")
    monkeypatch.setenv("OWNER_ID", "987654321")
    monkeypatch.setenv("STORAGE_CHANNEL", "-100987654321")

    runtime_setup.load_configuration()
    assert os.environ["BOT_TOKEN"] == "123456:abcdefghijklmnopqrstuvwxyz"
