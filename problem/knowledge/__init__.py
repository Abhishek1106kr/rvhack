"""The product catalog: loaded from catalog.json, searched in memory."""

import json
import re
from pathlib import Path

from problem.domain import Product

CATALOG_PATH = Path(__file__).with_name("catalog.json")


class ProductNotFound(LookupError):
    pass


class Catalog:
    def __init__(self, products: list[Product]) -> None:
        self._by_id = {p.id: p for p in products}

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> "Catalog":
        return cls([Product.model_validate(item) for item in json.loads(path.read_text())])

    @property
    def products(self) -> list[Product]:
        return list(self._by_id.values())

    def get(self, product_id: str) -> Product:
        try:
            return self._by_id[product_id]
        except KeyError:
            known = ", ".join(self._by_id)
            raise ProductNotFound(f"no product {product_id!r}; known ids: {known}") from None

    def search(self, query: str, limit: int = 3) -> list[Product]:
        """Keyword overlap across name, brand, category and features. Zero overlap = no match."""
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for product in self._by_id.values():
            haystack = " ".join(
                [product.name, product.brand, product.category, *product.key_features]
            ).lower()
            score = len(words & set(re.findall(r"[a-z0-9]+", haystack)))
            if score:
                scored.append((score, product))
        scored.sort(key=lambda pair: -pair[0])
        return [product for _, product in scored[:limit]]

    def stt_vocabulary(self) -> str:
        """Names Whisper should expect to hear (passed as its initial prompt)."""
        names = {p.name for p in self.products} | {
            d for p in self.products for d in p.compatible_devices
        }
        return ", ".join(sorted(names)) + "."
