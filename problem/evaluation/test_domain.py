import pytest

from problem.domain import normalize_device
from problem.knowledge import Catalog, ProductNotFound

CHARGER = "moto-68w-turbopower-charger"


@pytest.mark.parametrize(
    ("spoken", "key"),
    [
        ("Edge 50 Pro", "edge50pro"),
        ("edge fifty pro", "edge50pro"),
        ("Motorola Edge 50 Pro", "edge50pro"),
        ("Moto G84 5G", "g84"),
        ("moto g eighty four", "g84"),
        ("my moto G 84 5 g phone", "g84"),
        ("Razr forty", "razr40"),
    ],
)
def test_normalize_device(spoken: str, key: str) -> None:
    assert normalize_device(spoken) == key


def test_catalog_loads_listing() -> None:
    product = Catalog.load().get(CHARGER)
    assert product.specs["Wattage"] == "68 W"
    assert len(product.compatible_devices) == 13


def test_unknown_product() -> None:
    with pytest.raises(ProductNotFound, match="known ids"):
        Catalog.load().get("iphone-charger")


def test_search_matches_keywords_only() -> None:
    catalog = Catalog.load()
    assert [p.id for p in catalog.search("fast charger for motorola")] == [CHARGER]
    assert catalog.search("laptop bag") == []


def test_stt_vocabulary_lists_devices() -> None:
    vocabulary = Catalog.load().stt_vocabulary()
    assert "Razr 50 Ultra" in vocabulary and "Motorola 68W TurboPower Charger" in vocabulary
