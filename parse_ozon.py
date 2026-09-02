"""Загружает карточки Ozon и экспортирует нормализованные данные в CSV."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import requests

from ozon_parser.config import Settings
from ozon_parser.errors import CookieFileError, PageExtractionError, PageRequestError
from ozon_parser.export import write_errors, write_products
from ozon_parser.extraction import parse_product_html
from ozon_parser.http import create_session, fetch_product_page, save_debug_html
from ozon_parser.models import ProductRecord

DEFAULT_SKUS = ("2359066702", "2829800382")


def _read_sku_file(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read SKU file {path}: {exc}") from exc
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _valid_sku(value: str) -> str:
    candidate = value.strip()
    if not candidate.isdigit():
        raise argparse.ArgumentTypeError(f"SKU must contain digits only: {value!r}")
    return candidate


def parse_args(settings: Settings) -> argparse.Namespace:
    """Разбирает источники SKU, пути результатов и сетевые параметры."""
    parser = argparse.ArgumentParser(description="Parse Ozon product cards into CSV.")
    parser.add_argument("skus", nargs="*", type=_valid_sku, help="Product SKUs")
    parser.add_argument("--sku-file", type=Path, help="UTF-8 text file with one SKU per line")
    parser.add_argument("--cookies", type=Path, default=settings.cookies_path)
    parser.add_argument("--output", type=Path, default=settings.output_path)
    parser.add_argument("--errors", type=Path, default=settings.errors_path)
    parser.add_argument("--debug-dir", type=Path, default=settings.debug_dir)
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="Save successful response HTML to the ignored debug directory",
    )
    parser.add_argument(
        "--html-dir",
        type=Path,
        help="Diagnostic mode: parse existing product_<sku>.html files without HTTP",
    )
    parser.add_argument("--timeout", type=float, default=settings.request_timeout)
    parser.add_argument("--retries", type=int, default=settings.max_retries)
    parser.add_argument("--backoff", type=float, default=settings.retry_backoff)
    parser.add_argument("--delay", type=float, default=settings.request_delay)
    return parser.parse_args()


def collect_skus(args: argparse.Namespace) -> list[str]:
    """Объединяет SKU из аргументов и файла с сохранением порядка."""
    values = list(args.skus)
    if args.sku_file:
        try:
            values.extend(_valid_sku(value) for value in _read_sku_file(args.sku_file))
        except argparse.ArgumentTypeError as exc:
            raise ValueError(str(exc)) from exc
    if not values:
        values.extend(DEFAULT_SKUS)
    return list(dict.fromkeys(values))


def main() -> int:
    """Обрабатывает все SKU, не прерывая пакет из-за единичной ошибки."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    session: requests.Session | None = None
    try:
        settings = Settings.from_env()
        args = parse_args(settings)
        if args.timeout <= 0 or args.retries < 0 or args.backoff < 0 or args.delay < 0:
            raise ValueError("Timeout must be positive; retries, backoff, and delay cannot be negative")
        if args.html_dir is not None and args.save_html:
            raise ValueError("--save-html cannot be combined with --html-dir")
        skus = collect_skus(args)
        if args.html_dir is None:
            session = create_session(args.cookies, args.retries, args.backoff)
    except (CookieFileError, ValueError) as exc:
        logging.error("Startup failed: %s", exc)
        return 2

    products: list[ProductRecord] = []
    errors: list[dict[str, str]] = []
    try:
        for index, sku in enumerate(skus):
            if index and args.html_dir is None:
                time.sleep(args.delay)
            action = "Reading saved HTML for" if args.html_dir is not None else "Fetching"
            logging.info("%s SKU %s (%d/%d)", action, sku, index + 1, len(skus))
            html = ""
            try:
                if args.html_dir is not None:
                    html_path = args.html_dir / f"product_{sku}.html"
                    try:
                        html = html_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeError) as exc:
                        errors.append(
                            {
                                "sku": sku,
                                "error_type": "local_html_error",
                                "message": f"Cannot read diagnostic HTML {html_path}: {exc}",
                            }
                        )
                        logging.error("SKU %s: cannot read diagnostic HTML %s", sku, html_path)
                        continue
                    if not html.strip():
                        errors.append(
                            {
                                "sku": sku,
                                "error_type": "local_html_error",
                                "message": f"Diagnostic HTML is empty: {html_path}",
                            }
                        )
                        logging.error("SKU %s: diagnostic HTML is empty: %s", sku, html_path)
                        continue
                else:
                    assert session is not None
                    response = fetch_product_page(session, sku, args.timeout)
                    html = response.text
                    if args.save_html:
                        debug_path = save_debug_html(args.debug_dir, sku, html)
                        logging.info("Saved diagnostic HTML for SKU %s to %s", sku, debug_path)
                product = parse_product_html(sku, html)
                products.append(product)
                logging.info("Parsed SKU %s", sku)
            except PageRequestError as exc:
                errors.append({"sku": sku, "error_type": exc.error_type, "message": str(exc)})
                if html:
                    debug_path = save_debug_html(args.debug_dir, sku, html)
                    logging.warning("Saved diagnostic HTML for SKU %s to %s", sku, debug_path)
                logging.error("SKU %s: %s", sku, exc)
            except PageExtractionError as exc:
                if html:
                    debug_path = save_debug_html(args.debug_dir, sku, html)
                    message = f"{exc}. Diagnostic HTML: {debug_path}"
                else:
                    message = str(exc)
                errors.append({"sku": sku, "error_type": "extraction_error", "message": message})
                logging.error("SKU %s: %s", sku, message)
            except requests.RequestException as exc:
                errors.append({"sku": sku, "error_type": "network_error", "message": str(exc)})
                logging.error("SKU %s: network error: %s", sku, exc)
            except Exception as exc:  # Keep one unexpected product from aborting the batch.
                logging.exception("SKU %s: unexpected parser error", sku)
                errors.append({"sku": sku, "error_type": "unexpected_error", "message": str(exc)})
    finally:
        if session is not None:
            session.close()

    write_products(args.output, products)
    write_errors(args.errors, errors)
    logging.info("Wrote %d products to %s", len(products), args.output)
    logging.info("Wrote %d errors to %s", len(errors), args.errors)
    return 0 if products and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
