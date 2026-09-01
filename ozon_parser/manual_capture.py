from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import websocket

from .errors import CookieFileError


@dataclass
class ManualBrowser:
    process: subprocess.Popen[Any]
    profile_dir: Path
    port: int


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _local_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _wait_for_debug_endpoint(port: int, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    with _local_session() as session:
        while time.monotonic() < deadline:
            try:
                response = session.get(url, timeout=1)
                response.raise_for_status()
                payload = response.json()
                if payload.get("webSocketDebuggerUrl"):
                    return payload
            except (requests.RequestException, ValueError):
                time.sleep(0.25)
    raise CookieFileError("The manually opened browser did not expose its local debug endpoint")


def launch_manual_browser(
    browser_binary: Path,
    start_url: str,
    profiles_root: Path,
) -> ManualBrowser:
    profiles_root.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="ozon_", dir=profiles_root))
    port = _free_local_port()
    origin = f"http://127.0.0.1:{port}"
    command = [
        str(browser_binary),
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins={origin}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        start_url,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for_debug_endpoint(port)
    except Exception:
        if "process" in locals() and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    return ManualBrowser(process=process, profile_dir=profile_dir, port=port)


def _cdp_command(
    connection: websocket.WebSocket,
    command_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    command: dict[str, Any] = {"id": command_id, "method": method}
    if params:
        command["params"] = params
    connection.send(json.dumps(command))
    while True:
        message = json.loads(connection.recv())
        if message.get("id") != command_id:
            continue
        if "error" in message:
            raise CookieFileError(f"Browser debug command {method} failed: {message['error']}")
        return message.get("result", {})


def _is_ozon_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").casefold()
    return host == "ozon.ru" or host.endswith(".ozon.ru")


def select_current_ozon_url(targets: list[dict[str, Any]]) -> str:
    for target in targets:
        url = str(target.get("url", ""))
        if target.get("type") == "page" and _is_ozon_url(url):
            return url
    raise CookieFileError("No open Ozon page was found in the manual browser")


def _current_ozon_target(browser: ManualBrowser) -> dict[str, Any]:
    try:
        with _local_session() as session:
            response = session.get(f"http://127.0.0.1:{browser.port}/json/list", timeout=5)
            response.raise_for_status()
            targets = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise CookieFileError(f"Cannot read the manual browser page list: {exc}") from exc
    if not isinstance(targets, list):
        raise CookieFileError("The browser returned an invalid target list")
    selected_url = select_current_ozon_url(targets)
    return next(
        target
        for target in targets
        if target.get("type") == "page" and target.get("url") == selected_url
    )


def normalize_cdp_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for cookie in cookies:
        item = {
            key: cookie[key]
            for key in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite")
            if key in cookie
        }
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            item["expiry"] = int(expires)
        normalized.append(item)
    return normalized


def capture_manual_session(browser: ManualBrowser) -> tuple[list[dict[str, Any]], str, str]:
    version = _wait_for_debug_endpoint(browser.port, timeout=5)
    current_url = str(_current_ozon_target(browser)["url"])

    websocket_url = str(version["webSocketDebuggerUrl"])
    origin = f"http://127.0.0.1:{browser.port}"
    try:
        connection = websocket.create_connection(websocket_url, timeout=10, origin=origin)
        try:
            browser_info = _cdp_command(connection, 1, "Browser.getVersion")
            storage = _cdp_command(connection, 2, "Storage.getCookies")
        finally:
            connection.close()
    except (OSError, ValueError, websocket.WebSocketException) as exc:
        raise CookieFileError(f"Cannot read the manual browser session: {exc}") from exc

    raw_cookies = storage.get("cookies")
    user_agent = browser_info.get("userAgent")
    if not isinstance(raw_cookies, list) or not isinstance(user_agent, str):
        raise CookieFileError("The manual browser returned incomplete session data")
    return normalize_cdp_cookies(raw_cookies), user_agent, current_url


def capture_current_page_html(browser: ManualBrowser) -> tuple[str, str]:
    target = _current_ozon_target(browser)
    websocket_url = target.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str):
        raise CookieFileError("The current Ozon page has no local debug endpoint")
    origin = f"http://127.0.0.1:{browser.port}"
    try:
        connection = websocket.create_connection(websocket_url, timeout=15, origin=origin)
        try:
            result = _cdp_command(
                connection,
                10,
                "Runtime.evaluate",
                {
                    "expression": "document.documentElement.outerHTML",
                    "returnByValue": True,
                },
            )
        finally:
            connection.close()
    except (OSError, ValueError, websocket.WebSocketException) as exc:
        raise CookieFileError(f"Cannot capture the current browser page: {exc}") from exc
    page_html = result.get("result", {}).get("value")
    if not isinstance(page_html, str) or not page_html.strip():
        raise CookieFileError("The current browser page returned empty HTML")
    return str(target["url"]), page_html


def close_manual_browser(browser: ManualBrowser) -> None:
    try:
        version = _wait_for_debug_endpoint(browser.port, timeout=2)
        connection = websocket.create_connection(
            str(version["webSocketDebuggerUrl"]),
            timeout=3,
            origin=f"http://127.0.0.1:{browser.port}",
        )
        try:
            _cdp_command(connection, 99, "Browser.close")
        except (CookieFileError, OSError, ValueError, websocket.WebSocketException):
            pass
        finally:
            connection.close()
    except (CookieFileError, OSError, ValueError, websocket.WebSocketException):
        if browser.process.poll() is None:
            browser.process.terminate()
    try:
        browser.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if browser.process.poll() is None:
            browser.process.terminate()
    time.sleep(0.5)
    shutil.rmtree(browser.profile_dir, ignore_errors=True)
    if browser.profile_dir.exists():
        logging.warning("Temporary browser profile could not be removed: %s", browser.profile_dir)
