import json

import pytest
import requests

from ozon_parser.cookies import apply_cookies, load_cookie_file, save_browser_cookies
from ozon_parser.errors import CookieFileError


def _valid_payload():
    return {
        "version": 1,
        "created_at": "2026-09-01T00:00:00+00:00",
        "source_url": "https://data.ozon.ru/analytics",
        "user_agent": "Test Browser/1.0",
        "cookies": [
            {
                "name": "session",
                "value": "secret-value",
                "domain": ".ozon.ru",
                "path": "/",
                "expiry": 1_900_000_000,
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        ],
    }


def test_load_and_apply_cookies_preserves_domain_and_path(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(_valid_payload()), encoding="utf-8")

    payload = load_cookie_file(path)
    session = requests.Session()
    apply_cookies(session, payload["cookies"])

    cookie = next(iter(session.cookies))
    assert payload["user_agent"] == "Test Browser/1.0"
    assert cookie.domain == ".ozon.ru"
    assert cookie.path == "/"
    assert cookie.secure is True
    assert cookie.expires == 1_900_000_000


def test_save_browser_cookies_keeps_only_declared_fields_and_ozon_domains(tmp_path):
    path = tmp_path / "data" / "cookies.json"
    count = save_browser_cookies(
        path,
        [
            {**_valid_payload()["cookies"][0], "unexpected": "not-saved"},
            {"name": "other", "value": "x", "domain": ".example.com", "path": "/"},
        ],
        "Test Browser/1.0",
        "https://seller.ozon.ru/app",
    )

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert count == 1
    assert "unexpected" not in stored["cookies"][0]
    assert stored["cookies"][0]["domain"] == ".ozon.ru"


@pytest.mark.parametrize(
    "contents",
    [
        "not-json",
        json.dumps({"version": 1, "user_agent": "UA", "cookies": []}),
        json.dumps(
            {
                "version": 1,
                "user_agent": "UA",
                "cookies": [{"name": "x", "value": "y", "domain": ".example.com", "path": "/"}],
            }
        ),
    ],
)
def test_invalid_cookie_files_are_rejected(tmp_path, contents):
    path = tmp_path / "cookies.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(CookieFileError):
        load_cookie_file(path)


def test_save_rejects_non_ozon_browser_url(tmp_path):
    with pytest.raises(CookieFileError, match="not on an Ozon domain"):
        save_browser_cookies(
            tmp_path / "cookies.json",
            _valid_payload()["cookies"],
            "UA",
            "https://example.com/",
        )

