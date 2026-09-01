from __future__ import annotations

import ast
import html as html_module
import json
import re
from collections.abc import Iterable, Iterator
from typing import Any, Callable, Optional

from bs4 import BeautifulSoup

from .errors import PageExtractionError
from .models import Number, ProductRecord

_KEY_CLEANUP = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"[-+]?\d[\d\s\u00a0\u202f]*(?:[.,]\d+)?")
_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


def _normal_key(value: object) -> str:
    return _KEY_CLEANUP.sub("", str(value).casefold())


def _compact_text(value: str) -> str:
    return " ".join(value.split())


def normalize_number(value: object) -> Optional[Number]:
    """Return the first human-formatted number as int or float."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "text"):
            if key in value:
                result = normalize_number(value[key])
                if result is not None:
                    return result
        return None
    if not isinstance(value, str):
        return None

    match = _NUMBER_PATTERN.search(value)
    if not match:
        return None
    normalized = re.sub(r"[\s\u00a0\u202f]", "", match.group(0)).replace(",", ".")
    try:
        number = float(normalized)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def normalize_integer(value: object) -> Optional[int]:
    number = normalize_number(value)
    if number is None:
        return None
    multiplier = 1
    if isinstance(value, str):
        lowered = value.casefold()
        if "млн" in lowered or re.search(r"\bm\b", lowered):
            multiplier = 1_000_000
        elif "тыс" in lowered or re.search(r"\bk\b", lowered):
            multiplier = 1_000
    return int(round(float(number) * multiplier))


def _decode_script_json(raw: str) -> list[Any]:
    text = html_module.unescape(raw).strip()
    if not text:
        return []

    marker = "window.__NUXT__.state="
    marker_position = text.find(marker)
    if marker_position >= 0:
        literal_start = marker_position + len(marker)
        while literal_start < len(text) and text[literal_start].isspace():
            literal_start += 1
        if literal_start < len(text) and text[literal_start] in {"'", '"'}:
            quote = text[literal_start]
            escaped = False
            literal_end = literal_start + 1
            while literal_end < len(text):
                character = text[literal_end]
                if character == quote and not escaped:
                    break
                if character == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                literal_end += 1
            if literal_end < len(text):
                try:
                    decoded_string = ast.literal_eval(text[literal_start : literal_end + 1])
                    return [json.loads(decoded_string)]
                except (SyntaxError, ValueError, json.JSONDecodeError):
                    pass
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        pass

    # Handles common assignments such as window.__INITIAL_STATE__ = {...};
    decoder = json.JSONDecoder()
    candidates: list[Any] = []
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        candidates.append(value)
        break
    return candidates


def _deep_decode(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return value
    if isinstance(value, dict):
        return {key: _deep_decode(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_decode(item, depth + 1) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if 1 < len(stripped) <= 2_000_000 and stripped[0] in "[{":
            try:
                return _deep_decode(json.loads(stripped), depth + 1)
            except json.JSONDecodeError:
                return value
    return value


def extract_json_documents(page_html: str) -> list[Any]:
    soup = BeautifulSoup(page_html, "html.parser")
    documents: list[Any] = []
    for script in soup.find_all("script"):
        raw = script.string if script.string is not None else script.get_text()
        script_type = str(script.get("type", "")).casefold()
        script_id = str(script.get("id", "")).casefold()
        marker = (raw or "")[:500]
        should_try = (
            "json" in script_type
            or "data" in script_id
            or "state" in script_id
            or "__next_data__" in script_id
            or "__initial_state__" in marker.casefold()
            or "__next_data__" in marker.casefold()
            or "__nuxt__" in marker.casefold()
        )
        if should_try and raw:
            documents.extend(_decode_script_json(raw))
    for tag in soup.find_all(attrs={"data-state": True}):
        raw_state = tag.get("data-state")
        if not isinstance(raw_state, str) or not raw_state.strip():
            continue
        try:
            documents.append(json.loads(raw_state))
        except json.JSONDecodeError:
            continue
    return [_deep_decode(document) for document in documents]


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def _direct_value(node: dict[str, Any], aliases: Iterable[str]) -> Any:
    normalized = {_normal_key(key): value for key, value in node.items()}
    for alias in aliases:
        if _normal_key(alias) in normalized:
            return normalized[_normal_key(alias)]
    return None


def _product_score(node: dict[str, Any], sku: str) -> int:
    keys = {_normal_key(key): value for key, value in node.items()}
    score = 0
    for key in ("sku", "productid", "product_id", "id", "offerid"):
        value = keys.get(_normal_key(key))
        if value is not None and str(value).strip() == sku:
            score += 100
            break
    type_value = str(keys.get("type", keys.get("@type", ""))).casefold()
    if type_value == "product":
        score += 60
    if any(key in keys for key in ("name", "title", "productname")):
        score += 12
    if any(key in keys for key in ("price", "currentprice", "finalprice", "saleprice")):
        score += 10
    if any(key in keys for key in ("image", "images", "gallery", "mediagallery")):
        score += 5
    if any(key in keys for key in ("characteristics", "attributes", "features")):
        score += 5
    return score


def _select_product_context(documents: list[Any], sku: str) -> dict[str, Any]:
    scored = [(_product_score(node, sku), node) for document in documents for node in _walk_dicts(document)]
    if not scored:
        raise PageExtractionError("Embedded JSON contains no objects")
    score, node = max(scored, key=lambda item: item[0])
    if score < 20:
        raise PageExtractionError("Embedded JSON was found, but expected product data is absent")
    return node


def _relevant_product_nodes(
    documents: list[Any], sku: str, primary: dict[str, Any]
) -> list[dict[str, Any]]:
    """Collect product widgets tied to this SKU without using recommendations."""
    relevant = [primary]
    seen = {id(primary)}
    for document in documents:
        for node in _walk_dicts(document):
            if id(node) in seen:
                continue
            normalized = {_normal_key(key): value for key, value in node.items()}
            has_matching_id = any(
                str(normalized.get(_normal_key(key), "")).strip() == sku
                for key in ("sku", "productId", "product_id", "id", "offerId")
            )
            type_value = str(normalized.get("type", normalized.get("@type", ""))).casefold()
            if has_matching_id or type_value == "product":
                relevant.append(node)
                seen.add(id(node))
    return relevant


def _find_alias_value(
    node: Any,
    aliases: Iterable[str],
    blocked_containers: Iterable[str] = (),
) -> Any:
    alias_keys = [_normal_key(alias) for alias in aliases]
    blocked = {_normal_key(key) for key in blocked_containers}

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            normalized = {_normal_key(key): item for key, item in value.items()}
            for alias in alias_keys:
                if alias in normalized:
                    return normalized[alias]
            for key, item in value.items():
                if _normal_key(key) not in blocked:
                    result = visit(item)
                    if result is not None:
                        return result
        elif isinstance(value, list):
            for item in value:
                result = visit(item)
                if result is not None:
                    return result
        return None

    return visit(node)


def _unwrap_text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        compact = _compact_text(BeautifulSoup(value, "html.parser").get_text(" "))
        return compact or None
    if isinstance(value, list):
        parts = [_unwrap_text(item) for item in value]
        cleaned = [part.strip(" ,;") for part in parts if part and part.strip(" ,;")]
        return ", ".join(cleaned) or None
    if isinstance(value, dict):
        for key in ("value", "text", "content", "textRs", "label", "name", "amount"):
            if key in value:
                result = _unwrap_text(value[key])
                if result:
                    return result
    return None


def _normalize_url(value: Any) -> Optional[str]:
    if isinstance(value, list):
        for item in value:
            result = _normalize_url(item)
            if result:
                return result
        return None
    if isinstance(value, dict):
        for key in ("url", "src", "link", "imageUrl", "image", "original"):
            if key in value:
                result = _normalize_url(value[key])
                if result:
                    return result
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("//"):
            return "https:" + candidate
        if _URL_PATTERN.match(candidate):
            return candidate
    return None


def _find_normalized(
    contexts: Iterable[Any],
    aliases: Iterable[str],
    normalizer: Callable[[Any], Any],
    blocked_containers: Iterable[str] = (),
) -> Any:
    context_list = list(contexts)
    alias_list = list(aliases)

    # Product-level fields must win over nested objects. Otherwise a generic
    # key such as "name" may resolve to the name of a characteristic.
    for context in context_list:
        if not isinstance(context, dict):
            continue
        normalized_keys = {_normal_key(key): value for key, value in context.items()}
        for alias in alias_list:
            value = normalized_keys.get(_normal_key(alias))
            if value is not None:
                normalized = normalizer(value)
                if normalized is not None:
                    return normalized

    for context in context_list:
        for alias in alias_list:
            value = _find_alias_value(context, (alias,), blocked_containers)
            if value is not None:
                normalized = normalizer(value)
                if normalized is not None:
                    return normalized
    return None


def extract_characteristic(
    node: Any,
    labels: Iterable[str],
    identifier_prefixes: Iterable[str] = (),
) -> Optional[str]:
    wanted = {_normal_key(label) for label in labels}
    wanted_identifiers = {_normal_key(prefix) for prefix in identifier_prefixes}

    # Ozon's characteristic widgets use stable identifiers such as Color_1 and
    # Material_3. Prefer the complete characteristic object so that nested UI
    # properties such as ``color: textPrimary`` cannot be mistaken for values.
    for current in _walk_dicts(node):
        normalized = {_normal_key(key): value for key, value in current.items()}
        name = None
        for key in ("name", "title", "label", "key"):
            if key in normalized:
                name = normalized[key]
                break
        label_matches = (
            name is not None and _normal_key(_unwrap_text(name) or "") in wanted
        )

        raw_identifier = normalized.get("id")
        identifier = re.sub(r"\d+$", "", _normal_key(raw_identifier or ""))
        identifier_matches = bool(
            identifier and identifier in wanted_identifiers
        )
        if not label_matches and not identifier_matches:
            continue
        for value_key in ("value", "values", "text", "valueText"):
            normalized_value_key = _normal_key(value_key)
            if normalized_value_key in normalized:
                result = _unwrap_text(normalized[normalized_value_key])
                if result:
                    return result

    # Some embedded product documents expose characteristics as a plain map.
    # Keep that representation as a lower-priority fallback.
    for current in _walk_dicts(node):
        normalized = {_normal_key(key): value for key, value in current.items()}
        for label in wanted:
            if label in normalized:
                result = _unwrap_text(normalized[label])
                if result:
                    return result
    return None


def _extract_dom_characteristic(
    page_html: str, labels: Iterable[str]
) -> Optional[str]:
    """Read an exact label/value pair from Ozon's semantic characteristics widget."""
    wanted = {_normal_key(label) for label in labels}
    soup = BeautifulSoup(page_html, "html.parser")
    widget = soup.find(attrs={"data-widget": "webCharacteristics"})
    if widget is None:
        return None
    for row in widget.find_all("dl"):
        term = row.find("dt")
        definition = row.find("dd")
        if term is None or definition is None:
            continue
        label = _normal_key(_compact_text(term.get_text(" ", strip=True)))
        if label not in wanted:
            continue
        value = _compact_text(definition.get_text(" ", strip=True))
        if value:
            return value
    return None


def _media_item_urls(value: Any, default_kind: str) -> tuple[set[str], set[str]]:
    images: set[str] = set()
    videos: set[str] = set()
    if isinstance(value, list):
        for item in value:
            item_images, item_videos = _media_item_urls(item, default_kind)
            images.update(item_images)
            videos.update(item_videos)
        return images, videos
    if isinstance(value, str):
        url = _normalize_url(value)
        if url:
            is_video = default_kind == "video" or re.search(r"\.(?:mp4|webm|mov)(?:\?|$)", url, re.I)
            (videos if is_video else images).add(url)
        return images, videos
    if not isinstance(value, dict):
        return images, videos

    item_type = _normal_key(value.get("type", value.get("mediaType", default_kind)))
    kind = "video" if "video" in item_type else "image" if "image" in item_type else default_kind
    url_keys = ("videoUrl", "url", "src", "link") if kind == "video" else (
        "imageUrl", "image", "url", "src", "link", "original"
    )
    for key in url_keys:
        if key in value:
            url = _normalize_url(value[key])
            if url:
                (videos if kind == "video" else images).add(url)
                break
    for key, item in value.items():
        normalized_key = _normal_key(key)
        if normalized_key in {"items", "media", "images", "photos", "videos"}:
            child_kind = "video" if normalized_key == "videos" else kind
            item_images, item_videos = _media_item_urls(item, child_kind)
            images.update(item_images)
            videos.update(item_videos)
    return images, videos


def count_seller_media(node: Any, cover_image: Optional[str] = None) -> tuple[Optional[int], Optional[int]]:
    image_keys = {"images", "photos", "gallery", "mediagallery", "productimages"}
    video_keys = {"videos", "productvideos", "videogallery"}
    blocked_keys = {"description", "richcontent", "richdescription"}
    images: set[str] = set()
    videos: set[str] = set()
    explicit_image_counts: list[int] = []
    explicit_video_counts: list[int] = []
    media_seen = cover_image is not None

    def visit(value: Any) -> None:
        nonlocal media_seen
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = _normal_key(key)
                if normalized_key in blocked_keys:
                    continue
                if normalized_key in image_keys:
                    media_seen = True
                    found_images, found_videos = _media_item_urls(item, "image")
                    images.update(found_images)
                    videos.update(found_videos)
                    continue
                if normalized_key in video_keys:
                    media_seen = True
                    found_images, found_videos = _media_item_urls(item, "video")
                    images.update(found_images)
                    videos.update(found_videos)
                    continue
                if normalized_key in {"imagescount", "photoscount", "photocount"}:
                    media_seen = True
                    count = normalize_integer(item)
                    if count is not None:
                        explicit_image_counts.append(count)
                elif normalized_key in {"videoscount", "videocount"}:
                    media_seen = True
                    count = normalize_integer(item)
                    if count is not None:
                        explicit_video_counts.append(count)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(node)
    if cover_image:
        images.add(cover_image)
    if not media_seen:
        return None, None
    image_count = max([len(images), *explicit_image_counts]) if images or explicit_image_counts else None
    video_count = max([len(videos), *explicit_video_counts]) if videos or explicit_video_counts else 0
    return image_count, video_count


def _contains_rich_element(value: Any) -> bool:
    if isinstance(value, str):
        soup = BeautifulSoup(value, "html.parser")
        return soup.find(("img", "picture", "table", "ul", "ol", "li")) is not None
    if isinstance(value, list):
        return any(_contains_rich_element(item) for item in value)
    if isinstance(value, dict):
        type_value = _normal_key(value.get("type", value.get("widgetType", "")))
        if any(marker in type_value for marker in ("image", "table", "list")):
            return True
        for key, item in value.items():
            normalized_key = _normal_key(key)
            if normalized_key in {"image", "images", "table", "tables", "list", "lists"}:
                if item:
                    return True
            if _contains_rich_element(item):
                return True
    return False


def has_rich_content(node: Any) -> bool:
    rich_keys = {"richcontent", "richdescription", "description", "productdescription"}
    for current in _walk_dicts(node):
        for key, value in current.items():
            if _normal_key(key) in rich_keys and _contains_rich_element(value):
                return True
    return False


def parse_product_html(sku: str, page_html: str) -> ProductRecord:
    documents = extract_json_documents(page_html)
    if not documents:
        raise PageExtractionError("No readable embedded JSON was found in the page")
    product = _select_product_context(documents, sku)
    relevant_nodes = _relevant_product_nodes(documents, sku, product)
    contexts = tuple(relevant_nodes)
    characteristic_contexts = [
        *relevant_nodes,
        *[
            document
            for document in documents
            if isinstance(document, dict)
            and any(_normal_key(key) == "characteristics" for key in document)
        ],
    ]

    title = _find_normalized(contexts, ("productName", "name", "title"), _unwrap_text)
    price_widgets = [
        document
        for document in documents
        if isinstance(document, dict)
        and any(_normal_key(key) == "cardprice" for key in document)
        and any(_normal_key(key) == "price" for key in document)
    ]
    price = _find_normalized(
        price_widgets,
        ("cardPrice", "price"),
        normalize_number,
    )
    if price is None:
        price = _find_normalized(
            contexts,
            ("currentPrice", "finalPrice", "salePrice", "price"),
            normalize_number,
            ("oldPrice", "originalPrice", "listPrice", "fullPrice", "previousPrice"),
        )
    rating = _find_normalized(contexts, ("ratingValue", "rating", "score"), normalize_number)
    reviews_total = _find_normalized(
        contexts,
        ("reviewCount", "reviewsCount", "reviewsTotal", "commentsCount"),
        normalize_integer,
    )
    cover_image = _find_normalized(
        contexts,
        ("coverImage", "primaryImage", "mainImage", "image"),
        _normalize_url,
    )
    if title is None:
        raise PageExtractionError("Product JSON was found, but the product title is missing")
    photos_seller, videos_seller = count_seller_media(relevant_nodes, cover_image)

    color_labels = ("Цвет", "Цвет товара")
    color = extract_characteristic(
        characteristic_contexts,
        color_labels,
        identifier_prefixes=("Color",),
    ) or _extract_dom_characteristic(page_html, color_labels)

    material_labels = ("Материал",)
    material = extract_characteristic(
        characteristic_contexts,
        material_labels,
        identifier_prefixes=("Material",),
    ) or _extract_dom_characteristic(page_html, material_labels)

    manufacturer_article_labels = ("Артикул производителя",)
    art_set = extract_characteristic(
        characteristic_contexts,
        manufacturer_article_labels,
        identifier_prefixes=("ManufacturerArticle", "ManufacturerSku", "VendorCode"),
    ) or _extract_dom_characteristic(page_html, manufacturer_article_labels)
    if art_set is None:
        equipment_labels = ("Комплектация", "Состав комплекта")
        art_set = extract_characteristic(
            characteristic_contexts,
            equipment_labels,
            identifier_prefixes=(
                "Equipment",
                "Complectation",
                "PackageContents",
                "SetContents",
            ),
        ) or _extract_dom_characteristic(page_html, equipment_labels)

    return ProductRecord(
        sku=sku,
        title=title,
        price=price,
        rating=rating,
        reviews_total=reviews_total,
        cover_image=cover_image,
        photos_seller=photos_seller,
        videos_seller=videos_seller,
        color=color,
        material=material,
        art_set=art_set,
        has_rich_content=has_rich_content([*relevant_nodes, *documents]),
    )
