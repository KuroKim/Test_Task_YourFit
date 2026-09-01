from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from requests.cookies import create_cookie

from .errors import CookieFileError

COOKIE_FIELDS = ("name", "value", "domain", "path", "expiry", "secure", "httpOnly", "sameSite")


def _is_ozon_domain(domain: str) -> bool:
    normalized = domain.strip().lower().lstrip(".")
    return normalized == "ozon.ru" or normalized.endswith(".ozon.ru")


def _validate_cookie(cookie: Any, index: int) -> dict[str, Any]:
    if not isinstance(cookie, dict):
        raise CookieFileError(f"Cookie #{index} must be a JSON object")

    missing = [key for key in ("name", "value", "domain", "path") if key not in cookie]
    if missing:
        raise CookieFileError(f"Cookie #{index} is missing fields: {', '.join(missing)}")

    for key in ("name", "value", "domain", "path"):
        if not isinstance(cookie[key], str):
            raise CookieFileError(f"Cookie #{index} field {key!r} must be a string")

    if not cookie["name"] or not cookie["domain"] or not cookie["path"]:
        raise CookieFileError(f"Cookie #{index} has an empty name, domain, or path")
    if not _is_ozon_domain(cookie["domain"]):
        raise CookieFileError(f"Cookie #{index} has a non-Ozon domain")

    normalized = {key: cookie[key] for key in COOKIE_FIELDS if key in cookie}
    if "expiry" in normalized and not isinstance(normalized["expiry"], int):
        raise CookieFileError(f"Cookie #{index} field 'expiry' must be an integer")
    for key in ("secure", "httpOnly"):
        if key in normalized and not isinstance(normalized[key], bool):
            raise CookieFileError(f"Cookie #{index} field {key!r} must be boolean")
    return normalized


def save_browser_cookies(
    path: Path,
    cookies: Iterable[dict[str, Any]],
    user_agent: str,
    current_url: str,
) -> int:
    hostname = (urlparse(current_url).hostname or "").lower()
    if not _is_ozon_domain(hostname):
        raise CookieFileError("The browser is not on an Ozon domain")
    if not user_agent.strip():
        raise CookieFileError("The browser returned an empty User-Agent")

    safe_cookies = []
    for raw_cookie in cookies:
        if _is_ozon_domain(str(raw_cookie.get("domain", ""))):
            safe_cookies.append(
                {key: raw_cookie[key] for key in COOKIE_FIELDS if key in raw_cookie}
            )
    validated = [_validate_cookie(cookie, index) for index, cookie in enumerate(safe_cookies, 1)]
    if not validated:
        raise CookieFileError("No Ozon cookies were found after manual sign-in")

    payload = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": current_url,
        "user_agent": user_agent,
        "cookies": validated,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return len(validated)


def load_cookie_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CookieFileError(
            f"Cookie file not found: {path}. Run get_cookies.py first."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CookieFileError(f"Cannot read cookie file {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise CookieFileError("Cookie file root must be a JSON object")
    if payload.get("version") != 1:
        raise CookieFileError("Unsupported cookie file version")
    if not isinstance(payload.get("user_agent"), str) or not payload["user_agent"].strip():
        raise CookieFileError("Cookie file has no valid User-Agent")
    if not isinstance(payload.get("cookies"), list) or not payload["cookies"]:
        raise CookieFileError("Cookie file contains no cookies")

    payload["cookies"] = [
        _validate_cookie(cookie, index)
        for index, cookie in enumerate(payload["cookies"], 1)
    ]
    return payload


def apply_cookies(session: requests.Session, cookies: Iterable[dict[str, Any]]) -> None:
    for cookie in cookies:
        rest: dict[str, Any] = {}
        if cookie.get("httpOnly"):
            rest["HttpOnly"] = True
        if cookie.get("sameSite") is not None:
            rest["SameSite"] = cookie["sameSite"]

        prepared = create_cookie(
            name=cookie["name"],
            value=cookie["value"],
            domain=cookie["domain"],
            path=cookie["path"],
            secure=cookie.get("secure", False),
            expires=cookie.get("expiry"),
            rest=rest,
        )
        session.cookies.set_cookie(prepared)

