from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Union

Number = Union[int, float]


@dataclass(frozen=True)
class ProductRecord:
    sku: str
    title: Optional[str] = None
    price: Optional[Number] = None
    rating: Optional[Number] = None
    reviews_total: Optional[int] = None
    cover_image: Optional[str] = None
    photos_seller: Optional[int] = None
    videos_seller: Optional[int] = None
    color: Optional[str] = None
    material: Optional[str] = None
    art_set: Optional[str] = None
    has_rich_content: bool = False

    def to_row(self) -> dict[str, object]:
        return asdict(self)


PRODUCT_COLUMNS = [
    "sku",
    "title",
    "price",
    "rating",
    "reviews_total",
    "cover_image",
    "photos_seller",
    "videos_seller",
    "color",
    "material",
    "art_set",
    "has_rich_content",
]

