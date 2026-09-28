"""Domain model: products as they appear on a store listing, and device-name matching."""

import re

from pydantic import BaseModel, ConfigDict


class Product(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    brand: str
    category: str
    description: str
    key_features: list[str]
    compatible_devices: list[str]
    box_contents: list[str]
    # Label → value exactly as listed ("Wattage": "68 W"); listings vary too much to fix a schema.
    specs: dict[str, str]
    warranty: str
    return_policy: str
    customer_care: dict[str, str]
    manufacturer: str
    marketer: str
    country_of_origin: str
    disclaimer: str


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
# Words that never distinguish one model from another.
_NOISE = {"motorola", "moto", "phone", "mobile", "my", "the", "a", "an"}


def normalize_device(text: str) -> str:
    """Canonical key for a device name, robust to how STT writes it.

    "Moto G84 5G", "moto g 84", "Motorola G eighty four" → "g84"
    "Edge 50 Pro", "edge fifty pro"                      → "edge50pro"
    """
    text = re.sub(r"\b5\s?g\b", " ", text.lower())  # network generation, not model identity
    tokens = re.findall(r"[a-z]+|\d+", text)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _TENS:
            value = _TENS[token]
            if i + 1 < len(tokens) and tokens[i + 1] in _UNITS and 0 < _UNITS[tokens[i + 1]] < 10:
                value += _UNITS[tokens[i + 1]]
                i += 1
            out.append(str(value))
        elif token in _UNITS:
            out.append(str(_UNITS[token]))
        elif token not in _NOISE:
            out.append(token)
        i += 1
    return "".join(out)
