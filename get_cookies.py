from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service as ChromeService

from ozon_parser.cookies import save_browser_cookies
from ozon_parser.errors import CookieFileError
from ozon_parser.http import save_debug_html
from ozon_parser.manual_capture import (
    ManualBrowser,
    capture_current_page_html,
    capture_manual_session,
    close_manual_browser,
    launch_manual_browser,
)

START_URL = "https://data.ozon.ru/"


def find_browser_binary(browser: str) -> Path | None:
    env_names = {
        "chrome": "OZON_CHROME_BINARY",
        "edge": "OZON_EDGE_BINARY",
        "yandex": "OZON_YANDEX_BINARY",
    }
    env_name = env_names[browser]
    configured = os.getenv(env_name)
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise CookieFileError(f"{env_name} points to a missing file: {path}")
        return path

    if browser == "yandex":
        candidates = (
            Path(os.getenv("LOCALAPPDATA", "")) / "Yandex/YandexBrowser/Application/browser.exe",
            Path(os.getenv("PROGRAMFILES", r"C:\Program Files"))
            / "Yandex/YandexBrowser/Application/browser.exe",
            Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Yandex/YandexBrowser/Application/browser.exe",
        )
    elif browser == "chrome":
        candidates = (
            Path(os.getenv("PROGRAMFILES", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        )
    else:
        candidates = (
            Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.getenv("PROGRAMFILES", r"C:\Program Files"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
        )
    return next((path for path in candidates if path.is_file()), None)


def detect_yandex_chromium_major(browser_binary: Path) -> str | None:
    pattern = re.compile(rb"Chrome/(\d+)\.\d+\.\d+\.\d+")
    version_dlls = sorted(
        browser_binary.parent.glob("*/browser.dll"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for dll_path in version_dlls:
        try:
            with dll_path.open("rb") as file:
                tail = b""
                while chunk := file.read(4 * 1024 * 1024):
                    match = pattern.search(tail + chunk)
                    if match:
                        return match.group(1).decode("ascii")
                    tail = chunk[-64:]
        except OSError:
            continue
    return None


def find_local_chromedriver(required_major: str) -> Path | None:
    configured = os.getenv("OZON_CHROMEDRIVER_PATH")
    path = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parent / "data/webdriver/chromedriver.exe"
    )
    if not path.is_file():
        if configured:
            raise CookieFileError(f"OZON_CHROMEDRIVER_PATH points to a missing file: {path}")
        return None
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CookieFileError(f"Cannot validate local ChromeDriver {path}: {exc}") from exc
    match = re.search(r"ChromeDriver\s+(\d+)", result.stdout)
    if not match or match.group(1) != required_major:
        actual = match.group(1) if match else "unknown"
        raise CookieFileError(
            f"Local ChromeDriver major version is {actual}, expected {required_major}"
        )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open Ozon Analytics for manual sign-in and save browser cookies."
    )
    parser.add_argument(
        "--browser",
        choices=("edge", "chrome", "yandex"),
        default=os.getenv("OZON_BROWSER", "edge").lower(),
        help="Visible browser to open (default: OZON_BROWSER or edge)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("OZON_COOKIES_PATH", "data/cookies.json")),
        help="Cookie JSON path",
    )
    parser.add_argument(
        "--manual-capture",
        action="store_true",
        help="Use a normal isolated browser and export cookies after manual sign-in",
    )
    parser.add_argument(
        "--capture-product-html",
        metavar="SKU",
        help="In manual mode, save the currently open product page HTML for this SKU",
    )
    return parser.parse_args()


def create_driver(browser: str, browser_version: str | None = None):
    binary_path = find_browser_binary(browser)
    if browser in {"chrome", "yandex"}:
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        if binary_path:
            options.binary_location = str(binary_path)
        local_driver = None
        if browser == "yandex" and browser_version:
            local_driver = find_local_chromedriver(browser_version)
        if browser_version and local_driver is None:
            options.browser_version = browser_version
        if local_driver:
            return webdriver.Chrome(
                service=ChromeService(executable_path=str(local_driver)),
                options=options,
            )
        return webdriver.Chrome(options=options)
    options = webdriver.EdgeOptions()
    options.add_argument("--start-maximized")
    if binary_path:
        options.binary_location = str(binary_path)
    return webdriver.Edge(options=options)


def print_login_instructions() -> None:
    print(
        "\nComplete these steps in the opened browser:\n"
        "  1. Pass Ozon's anti-bot check if it appears.\n"
        "  2. Click 'Перейти к аналитике'.\n"
        "  3. Sign in to Ozon ID manually.\n"
        "  4. Confirm the sign-in in the mobile application.\n"
        "  5. Wait until the analytics page is fully open.\n\n"
        "Do not close the browser. Return here only when the page is ready."
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    driver = None
    manual_browser: ManualBrowser | None = None
    try:
        if args.capture_product_html and not args.manual_capture:
            raise CookieFileError("--capture-product-html requires --manual-capture")
        binary_path = find_browser_binary(args.browser)
        if binary_path:
            logging.info("Using browser installation: %s", binary_path)
        if args.manual_capture:
            if binary_path is None:
                raise CookieFileError(f"Cannot find the {args.browser.title()} browser executable")
            logging.info("Starting visible %s in isolated manual-capture mode...", args.browser.title())
            manual_browser = launch_manual_browser(
                binary_path,
                START_URL,
                Path(__file__).resolve().parent / "data/browser-profiles",
            )
            print_login_instructions()
            if args.capture_product_html:
                print(
                    "\nFor diagnostics, after sign-in open this product page in the same tab:\n"
                    f"  https://www.ozon.ru/product/{args.capture_product_html}/\n"
                    "Wait until the product card is fully loaded, then return here."
                )
            input("Press Enter to save the authenticated session... ")
            cookies, user_agent, current_url = capture_manual_session(manual_browser)
            cookie_count = save_browser_cookies(
                path=args.output,
                cookies=cookies,
                user_agent=user_agent,
                current_url=current_url,
            )
            logging.info("Saved %d Ozon cookies to %s", cookie_count, args.output)
            if args.capture_product_html:
                page_url, page_html = capture_current_page_html(manual_browser)
                if args.capture_product_html not in page_url:
                    raise CookieFileError(
                        f"The open page URL does not contain SKU {args.capture_product_html}: {page_url}"
                    )
                debug_path = save_debug_html(
                    Path(__file__).resolve().parent / "debug",
                    args.capture_product_html,
                    page_html,
                )
                logging.info("Saved diagnostic product HTML to %s", debug_path)
            logging.info("Cookie values were not printed. Keep this file private.")
            return 0

        logging.info("Starting visible %s via Selenium Manager...", args.browser.title())
        browser_version = None
        if args.browser == "yandex" and binary_path:
            browser_version = detect_yandex_chromium_major(binary_path)
            if browser_version is None:
                raise CookieFileError(
                    "Cannot detect the Chromium version used by Yandex Browser"
                )
            logging.info("Detected Yandex Chromium major version: %s", browser_version)
            local_driver = find_local_chromedriver(browser_version)
            if local_driver:
                logging.info("Using compatible local ChromeDriver: %s", local_driver)
        driver = create_driver(args.browser, browser_version)
        driver.get(START_URL)
        print_login_instructions()
        input("Press Enter to save the authenticated session... ")
        current_url = driver.current_url
        user_agent = driver.execute_script("return navigator.userAgent")
        cookie_count = save_browser_cookies(
            path=args.output,
            cookies=driver.get_cookies(),
            user_agent=str(user_agent),
            current_url=current_url,
        )
        logging.info("Saved %d Ozon cookies to %s", cookie_count, args.output)
        logging.info("Cookie values were not printed. Keep this file private.")
        return 0
    except (CookieFileError, WebDriverException) as exc:
        logging.error("Cannot save the browser session: %s", exc)
        return 1
    except (EOFError, KeyboardInterrupt):
        logging.warning("Cancelled; no cookie file was saved.")
        return 130
    finally:
        if manual_browser is not None:
            close_manual_browser(manual_browser)
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                logging.warning("The browser did not close cleanly.")


if __name__ == "__main__":
    sys.exit(main())
