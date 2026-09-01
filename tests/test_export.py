import csv

from ozon_parser.export import write_errors, write_products
from ozon_parser.models import PRODUCT_COLUMNS, ProductRecord


def test_csv_has_stable_columns_and_empty_optional_cells(tmp_path):
    products_path = tmp_path / "output" / "products.csv"
    errors_path = tmp_path / "output" / "errors.csv"
    write_products(products_path, [ProductRecord(sku="123", title="Товар", has_rich_content=False)])
    write_errors(errors_path, [{"sku": "456", "error_type": "http_404", "message": "not found"}])

    with products_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert list(rows[0]) == PRODUCT_COLUMNS
    assert rows[0]["price"] == ""
    assert rows[0]["has_rich_content"] == "false"

    with errors_path.open(encoding="utf-8", newline="") as file:
        errors = list(csv.DictReader(file))
    assert errors == [{"sku": "456", "error_type": "http_404", "message": "not found"}]

