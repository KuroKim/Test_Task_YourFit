"""Настройка HTTP-сессии, повторов и проверка ответов Ozon."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cookies import apply_cookies, load_cookie_file
from .errors import PageRequestError

PRODUCT_URL = "https://www.ozon.ru/product/{sku}/"
_ANTIBOT_TEXT_MARKERS = (
    "капча",
    "проверка безопасности",
    "подтвердите, что вы не робот",
    "access denied",
    "robot check",
)


def create_session(cookie_path: Path, retries: int, backoff: float) -> requests.Session:
    """Создаёт браузероподобную Requests-сессию из сохранённой сессии."""
    payload = load_cookie_file(cookie_path)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": payload["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    apply_cookies(session, payload["cookies"])

    # urllib3 повторяет только временные ошибки; проблемы авторизации и парсинга
    # сразу возвращаются с отдельными типами предметных ошибок.
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=backoff,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def _looks_like_login(response: requests.Response) -> bool:
    parsed = urlparse(response.url)
    host = parsed.hostname or ""
    path = parsed.path.lower()
    if "id.ozon.ru" in host or any(part in path for part in ("/login", "/signin", "/auth")):
        return True
    lowered = response.text[:200_000].lower()
    return "войти в ozon" in lowered and "ozon id" in lowered


def validate_response(response: requests.Response) -> None:
    """Классифицирует HTTP-ошибки, авторизацию, пустые и антибот-страницы."""
    if response.status_code == 403:
        raise PageRequestError("http_403", "Ozon returned HTTP 403 (access forbidden)")
    if response.status_code == 404:
        raise PageRequestError("http_404", "Product page was not found (HTTP 404)")
    if response.status_code == 429:
        raise PageRequestError("http_429", "Ozon rate limit remained after retries (HTTP 429)")
    if response.status_code >= 500:
        raise PageRequestError("http_5xx", f"Ozon returned HTTP {response.status_code} after retries")
    if response.status_code >= 400:
        raise PageRequestError("http_error", f"Ozon returned HTTP {response.status_code}")
    if _looks_like_login(response):
        raise PageRequestError("authorization_redirect", "The request was redirected to Ozon sign-in")
    if not response.text.strip():
        raise PageRequestError("empty_page", "Ozon returned an empty page")

    lowered = response.text[:500_000].lower()
    is_small_captcha_page = "captcha" in lowered and len(response.text) < 200_000
    if is_small_captcha_page or any(marker in lowered for marker in _ANTIBOT_TEXT_MARKERS):
        raise PageRequestError("antibot", "Ozon returned a CAPTCHA or anti-bot page")


def fetch_product_page(session: requests.Session, sku: str, timeout: float) -> requests.Response:
    """Загружает и проверяет одну публичную карточку товара Ozon."""
    response = session.get(PRODUCT_URL.format(sku=sku), timeout=timeout)
    validate_response(response)
    return response


def save_debug_html(debug_dir: Path, sku: str, html: str) -> Path:
    """Сохраняет диагностический HTML под безопасным именем на основе SKU."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    safe_sku = re.sub(r"[^0-9A-Za-z_-]", "_", sku)
    path = debug_dir / f"product_{safe_sku}.html"
    path.write_text(html, encoding="utf-8")
    return path
