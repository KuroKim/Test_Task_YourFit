from pathlib import Path
import subprocess

import pytest

from get_cookies import (
    detect_yandex_chromium_major,
    find_browser_binary,
    find_local_chromedriver,
)
from ozon_parser.errors import CookieFileError


def test_explicit_edge_binary_is_used(monkeypatch, tmp_path):
    binary = tmp_path / "msedge.exe"
    binary.write_bytes(b"test executable placeholder")
    monkeypatch.setenv("OZON_EDGE_BINARY", str(binary))

    assert find_browser_binary("edge") == binary


def test_missing_explicit_edge_binary_is_reported(monkeypatch, tmp_path):
    missing = tmp_path / "missing-msedge.exe"
    monkeypatch.setenv("OZON_EDGE_BINARY", str(missing))

    with pytest.raises(CookieFileError, match="missing file"):
        find_browser_binary("edge")


def test_explicit_yandex_binary_is_used(monkeypatch, tmp_path):
    binary = tmp_path / "browser.exe"
    binary.write_bytes(b"test executable placeholder")
    monkeypatch.setenv("OZON_YANDEX_BINARY", str(binary))

    assert find_browser_binary("yandex") == binary


def test_yandex_chromium_major_is_read_from_browser_dll(tmp_path):
    application = tmp_path / "Application"
    binary = application / "browser.exe"
    dll = application / "26.8.0.1788" / "browser.dll"
    dll.parent.mkdir(parents=True)
    binary.write_bytes(b"browser")
    dll.write_bytes(b"prefix Chrome/150.0.7871.1788 suffix")

    assert detect_yandex_chromium_major(binary) == "150"


def test_compatible_local_chromedriver_is_accepted(monkeypatch, tmp_path):
    driver = tmp_path / "chromedriver.exe"
    driver.write_bytes(b"placeholder")
    monkeypatch.setenv("OZON_CHROMEDRIVER_PATH", str(driver))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ChromeDriver 150.0.1.2\n", ""),
    )

    assert find_local_chromedriver("150") == driver


def test_incompatible_local_chromedriver_is_rejected(monkeypatch, tmp_path):
    driver = tmp_path / "chromedriver.exe"
    driver.write_bytes(b"placeholder")
    monkeypatch.setenv("OZON_CHROMEDRIVER_PATH", str(driver))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ChromeDriver 142.0.1.2\n", ""),
    )

    with pytest.raises(CookieFileError, match="expected 150"):
        find_local_chromedriver("150")
