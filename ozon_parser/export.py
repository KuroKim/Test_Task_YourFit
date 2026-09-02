"""Запись успешных товаров и ошибок отдельных SKU в CSV."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .models import PRODUCT_COLUMNS, ProductRecord


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def write_products(path: Path, products: Iterable[ProductRecord]) -> None:
    """Записывает товары в UTF-8 CSV со стабильным порядком колонок."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PRODUCT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for product in products:
            writer.writerow({key: _csv_value(value) for key, value in product.to_row().items()})


def write_errors(path: Path, errors: Iterable[dict[str, str]]) -> None:
    """Записывает ошибки отдельно от основного CSV с товарами."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["sku", "error_type", "message"]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(errors)
