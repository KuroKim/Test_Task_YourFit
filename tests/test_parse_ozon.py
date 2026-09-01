import csv
import json
from pathlib import Path

import parse_ozon
from ozon_parser.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        cookies_path=tmp_path / "cookies.json",
        output_path=tmp_path / "products.csv",
        errors_path=tmp_path / "errors.csv",
        debug_dir=tmp_path / "debug",
        request_timeout=30,
        max_retries=3,
        retry_backoff=1,
        request_delay=1.5,
    )


def test_html_dir_is_an_explicit_diagnostic_option(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["parse_ozon.py", "123", "--html-dir", str(tmp_path / "saved")],
    )

    args = parse_ozon.parse_args(_settings(tmp_path))

    assert args.skus == ["123"]
    assert args.html_dir == tmp_path / "saved"


def test_default_skus_are_used_when_no_input_is_supplied(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["parse_ozon.py"])

    args = parse_ozon.parse_args(_settings(tmp_path))

    assert parse_ozon.collect_skus(args) == list(parse_ozon.DEFAULT_SKUS)


def test_html_dir_parses_saved_page_without_cookie_file(monkeypatch, tmp_path):
    html_dir = tmp_path / "saved"
    html_dir.mkdir()
    payload = {
        "@type": "Product",
        "name": "Сохранённый товар",
        "offers": {"price": 999},
    }
    (html_dir / "product_123.html").write_text(
        f"<script type='application/ld+json'>{json.dumps(payload, ensure_ascii=False)}</script>",
        encoding="utf-8",
    )
    products_path = tmp_path / "products.csv"
    errors_path = tmp_path / "errors.csv"
    monkeypatch.setattr(
        "sys.argv",
        [
            "parse_ozon.py",
            "123",
            "--html-dir",
            str(html_dir),
            "--output",
            str(products_path),
            "--errors",
            str(errors_path),
        ],
    )

    assert parse_ozon.main() == 0
    with products_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["sku"] == "123"
    assert rows[0]["title"] == "Сохранённый товар"
    assert rows[0]["price"] == "999"
    with errors_path.open(encoding="utf-8", newline="") as file:
        assert list(csv.DictReader(file)) == []
