import json

import pytest

from ozon_parser.errors import PageExtractionError
from ozon_parser.extraction import (
    count_seller_media,
    extract_characteristic,
    has_rich_content,
    normalize_integer,
    normalize_number,
    parse_product_html,
)


def _html_with_json(payload, script_type="application/json"):
    return f"<html><head><script id='__NEXT_DATA__' type='{script_type}'>{json.dumps(payload, ensure_ascii=False)}</script></head></html>"


def _full_product():
    return {
        "props": {
            "pageProps": {
                "product": {
                    "sku": "2359066702",
                    "title": "Тестовый органайзер",
                    "currentPrice": "1 299 ₽",
                    "oldPrice": "2 500 ₽",
                    "rating": "4,8",
                    "reviewCount": "1,2 тыс. отзывов",
                    "coverImage": "https://cdn.example.test/cover.jpg",
                    "gallery": [
                        {"type": "image", "url": "https://cdn.example.test/cover.jpg"},
                        {"type": "image", "url": "https://cdn.example.test/second.jpg"},
                        {"type": "video", "url": "https://cdn.example.test/demo.mp4"},
                    ],
                    "characteristics": [
                        {"name": "Цвет товара", "values": ["синий"]},
                        {"name": "Материал", "value": "хлопок"},
                        {"name": "Артикул производителя", "value": "ABC-42"},
                        {"name": "Комплектация", "value": "1 шт."},
                    ],
                    "richContent": "<p>Описание</p><ul><li>Пункт</li></ul><img src='https://cdn.example.test/rich.jpg'>",
                }
            }
        }
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7 990 ₽", 7990),
        ("4,8 из 5", 4.8),
        ("12\u00a0345.50", 12345.5),
        (None, None),
        ("нет данных", None),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,2 тыс. отзывов", 1200), ("2 млн", 2_000_000), ("987", 987)],
)
def test_normalize_integer_with_suffix(raw, expected):
    assert normalize_integer(raw) == expected


def test_extracts_all_requested_fields_from_embedded_json():
    product = parse_product_html("2359066702", _html_with_json(_full_product()))

    assert product.title == "Тестовый органайзер"
    assert product.price == 1299
    assert product.rating == 4.8
    assert product.reviews_total == 1200
    assert product.cover_image == "https://cdn.example.test/cover.jpg"
    assert product.photos_seller == 2
    assert product.videos_seller == 1
    assert product.color == "синий"
    assert product.material == "хлопок"
    assert product.art_set == "ABC-42"
    assert product.has_rich_content is True


def test_characteristic_falls_back_to_equipment():
    node = {
        "attributes": [
            {"title": "Цвет", "value": "красный"},
            {"title": "Комплектация", "value": ["чехол", "ремень"]},
        ]
    }
    assert extract_characteristic(node, ("Цвет", "Цвет товара")) == "красный"
    assert extract_characteristic(node, ("Комплектация",)) == "чехол, ремень"


def test_gallery_count_does_not_include_rich_content_images():
    node = {
        "images": ["https://cdn.example.test/a.jpg", "https://cdn.example.test/b.jpg"],
        "videos": ["https://cdn.example.test/a.mp4"],
        "description": "<img src='https://cdn.example.test/not-gallery.jpg'>",
    }
    assert count_seller_media(node) == (2, 1)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("<p>Only plain text</p>", False),
        ("<table><tr><td>Size</td></tr></table>", True),
        ({"blocks": [{"type": "image", "url": "https://example.test/x.jpg"}]}, True),
        ("<ol><li>One</li></ol>", True),
    ],
)
def test_rich_content_detection(description, expected):
    assert has_rich_content({"description": description, "gallery": ["https://example.test/a.jpg"]}) is expected


def test_missing_optional_values_remain_none():
    payload = {"product": {"sku": "2829800382", "name": "Товар без деталей", "price": 500}}
    product = parse_product_html("2829800382", _html_with_json(payload))

    assert product.sku == "2829800382"
    assert product.title == "Товар без деталей"
    assert product.price == 500
    assert product.rating is None
    assert product.reviews_total is None
    assert product.cover_image is None
    assert product.photos_seller is None
    assert product.videos_seller is None
    assert product.color is None
    assert product.material is None
    assert product.art_set is None
    assert product.has_rich_content is False


def test_separate_widgets_for_same_sku_are_combined_without_recommendations():
    payload = {
        "widgets": [
            {"sku": "123", "name": "Основной товар", "price": 700},
            {
                "productId": "123",
                "attributes": [{"name": "Материал", "value": "сталь"}],
                "images": ["https://cdn.example.test/main.jpg"],
            },
            {
                "sku": "999",
                "name": "Рекомендованный товар",
                "images": [
                    "https://cdn.example.test/other-1.jpg",
                    "https://cdn.example.test/other-2.jpg",
                ],
                "attributes": [{"name": "Цвет", "value": "не брать"}],
            },
        ]
    }
    product = parse_product_html("123", _html_with_json(payload))
    assert product.title == "Основной товар"
    assert product.material == "сталь"
    assert product.color is None
    assert product.photos_seller == 1


def test_json_ld_is_used_as_fallback():
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "JSON-LD товар",
        "image": ["https://cdn.example.test/product.jpg"],
        "offers": {"price": "999.90"},
        "aggregateRating": {"ratingValue": "4.5", "reviewCount": "12"},
    }
    html = _html_with_json(payload, "application/ld+json")
    product = parse_product_html("999", html)
    assert product.title == "JSON-LD товар"
    assert product.price == 999.9
    assert product.rating == 4.5
    assert product.reviews_total == 12
    assert product.cover_image == "https://cdn.example.test/product.jpg"


def test_old_nested_price_is_never_used_as_current_price():
    payload = {
        "product": {
            "sku": "123",
            "name": "Товар без актуальной цены",
            "oldPrice": {"price": "2 999 ₽"},
        }
    }
    product = parse_product_html("123", _html_with_json(payload))
    assert product.price is None


def test_nuxt_state_wrapped_in_javascript_string_is_decoded():
    payload = {
        "product": {
            "sku": "123",
            "name": "Товар из NUXT",
            "price": "1 250 ₽",
            "attributes": [{"name": "Материал", "value": "бумага"}],
        }
    }
    state = json.dumps(payload, ensure_ascii=False)
    page_html = f"<script>window.__NUXT__={{}};window.__NUXT__.state={state!r};</script>"

    product = parse_product_html("123", page_html)
    assert product.title == "Товар из NUXT"
    assert product.price == 1250
    assert product.material == "бумага"


def test_product_widget_data_state_is_combined_with_json_ld():
    ld_product = {
        "@type": "Product",
        "name": "Товар из JSON-LD",
        "offers": {"price": 900},
    }
    gallery_state = {
        "sku": "123",
        "coverImage": "https://cdn.example.test/cover.jpg",
        "images": [
            "https://cdn.example.test/cover.jpg",
            "https://cdn.example.test/second.jpg",
        ],
        "videos": [{"url": "https://cdn.example.test/video.mp4"}],
    }
    characteristics_state = {
        "characteristics": [
            {"name": "Цвет товара", "value": "зелёный"},
            {"name": "Материал", "value": "бумага"},
        ]
    }
    page_html = (
        f"<script type='application/ld+json'>{json.dumps(ld_product, ensure_ascii=False)}</script>"
        f"<div data-state='{json.dumps(gallery_state, ensure_ascii=False)}'></div>"
        f"<div data-state='{json.dumps(characteristics_state, ensure_ascii=False)}'></div>"
    )

    product = parse_product_html("123", page_html)
    assert product.photos_seller == 2
    assert product.videos_seller == 1
    assert product.color == "зелёный"
    assert product.material == "бумага"


def test_real_ozon_characteristic_ids_win_over_ui_color_tokens():
    ld_product = {
        "@type": "Product",
        "name": "Товар из JSON-LD",
        "offers": {"price": 900},
    }
    characteristics_state = {
        "characteristics": [
            {
                "id": "Color_1",
                "title": {
                    "textRs": [
                        {"content": "Цвет", "color": "textSecondary"}
                    ]
                },
                "values": [{"id": "Color_0", "text": "Темно-розовый", "textColor": "textPrimary"}],
            },
            {
                "id": "Material_3",
                "title": {
                    "textRs": [
                        {"content": "Материал", "color": "textSecondary"}
                    ]
                },
                "values": [{"id": "Material_0", "text": "Бумага"}],
            },
        ]
    }
    page_html = (
        f"<script type='application/ld+json'>{json.dumps(ld_product, ensure_ascii=False)}</script>"
        f"<div data-state='{json.dumps(characteristics_state, ensure_ascii=False)}'></div>"
    )

    product = parse_product_html("123", page_html)
    assert product.color == "Темно-розовый"
    assert product.material == "Бумага"


def test_characteristic_value_list_does_not_duplicate_source_commas():
    node = {
        "characteristics": [
            {
                "id": "Color_1",
                "values": [
                    {"text": "Бежевый, "},
                    {"text": "желтый, "},
                    {"text": "красный"},
                ],
            }
        ]
    }

    assert (
        extract_characteristic(node, ("Цвет",), identifier_prefixes=("Color",))
        == "Бежевый, желтый, красный"
    )


def test_dom_characteristic_fallback_uses_exact_semantic_rows():
    ld_product = {
        "@type": "Product",
        "name": "Товар из JSON-LD",
        "offers": {"price": 900},
    }
    page_html = (
        f"<script type='application/ld+json'>{json.dumps(ld_product, ensure_ascii=False)}</script>"
        "<div data-widget='webCharacteristics'>"
        "<dl><dt>Артикул</dt><dd>123</dd></dl>"
        "<dl><dt>Состав комплекта</dt><dd>Раскраска</dd></dl>"
        "</div>"
    )

    product = parse_product_html("123", page_html)
    assert product.art_set == "Раскраска"


def test_ozon_card_price_is_the_current_main_price():
    ld_product = {
        "@type": "Product",
        "name": "Товар из JSON-LD",
        "offers": {"price": 2040},
    }
    price_state = {
        "cardPrice": "1\u2009836\u2009₽",
        "price": "2\u2009040\u2009₽",
        "showOriginalPrice": True,
    }
    page_html = (
        f"<script type='application/ld+json'>{json.dumps(ld_product, ensure_ascii=False)}</script>"
        f"<div data-state='{json.dumps(price_state, ensure_ascii=False)}'></div>"
    )

    product = parse_product_html("123", page_html)
    assert product.price == 1836


@pytest.mark.parametrize(
    "page_html",
    [
        "<html><script type='application/json'>{broken</script></html>",
        _html_with_json({"unrelated": {"foo": "bar"}}),
        "<html><body>No scripts here</body></html>",
    ],
)
def test_corrupt_or_unexpected_json_is_reported(page_html):
    with pytest.raises(PageExtractionError):
        parse_product_html("123", page_html)
