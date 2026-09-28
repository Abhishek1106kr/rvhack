"""Store tools. All read-only: the listing gives no data for orders, stock, or price."""

from typing import Literal

from pydantic import BaseModel, Field

from app.tools.registry import ToolRisk, ToolSpec
from problem.domain import normalize_device
from problem.knowledge import Catalog

DetailField = Literal[
    "description",
    "key_features",
    "box_contents",
    "specs",
    "warranty",
    "country_of_origin",
    "manufacturer",
    "compatible_devices",
    "disclaimer",
]


class SearchInput(BaseModel):
    query: str = Field(description="What the shopper is looking for, e.g. 'fast charger'.")


class DetailsInput(BaseModel):
    product_id: str
    fields: list[DetailField] = Field(
        default_factory=list,
        description="Which details to return. Empty returns a short summary.",
    )


class CompatibilityInput(BaseModel):
    product_id: str
    device: str = Field(description="The shopper's device as they said it, e.g. 'Edge 50 Pro'.")


class ProductInput(BaseModel):
    product_id: str


class CompatibilityResult(BaseModel):
    device_asked: str
    status: Literal["listed", "ambiguous", "not_listed"]
    matches: list[str]
    # One plain instruction per outcome: small models follow this better than prose.
    note: str


_COMPATIBILITY_NOTES = {
    "listed": "On the official compatibility list.",
    "ambiguous": "Several listed models match. Ask the shopper which one they have.",
    "not_listed": "Not on the official list. The listing only says it works with "
    "smartphones, tablets and other USB-C devices in general.",
}


def build_tools(catalog: Catalog) -> list[ToolSpec]:
    async def search_products(args: SearchInput) -> list[dict]:
        return [
            {"product_id": p.id, "name": p.name, "category": p.category}
            for p in catalog.search(args.query)
        ]

    async def get_product_details(args: DetailsInput) -> dict:
        product = catalog.get(args.product_id)
        if not args.fields:
            return {
                "name": product.name,
                "key_features": product.key_features,
                "wattage": product.specs.get("Wattage"),
                "box_contents": product.box_contents,
            }
        return {"name": product.name} | {f: getattr(product, f) for f in args.fields}

    async def check_compatibility(args: CompatibilityInput) -> CompatibilityResult:
        product = catalog.get(args.product_id)
        asked = normalize_device(args.device)
        listed = {normalize_device(d): d for d in product.compatible_devices}
        if asked in listed:
            status, matches = "listed", [listed[asked]]
        else:
            # "Edge 50" is a prefix of Edge 50 Pro / Ultra / Fusion: ask which one.
            matches = [name for key, name in listed.items() if asked and key.startswith(asked)]
            status = "ambiguous" if matches else "not_listed"
        return CompatibilityResult(
            device_asked=args.device,
            status=status,
            matches=matches,
            note=_COMPATIBILITY_NOTES[status],
        )

    async def get_return_policy(args: ProductInput) -> dict:
        product = catalog.get(args.product_id)
        return {
            "name": product.name,
            "return_policy": product.return_policy,
            "warranty": product.warranty,
            "customer_care": product.customer_care,
        }

    def spec(name, description, input_model, handler, output_model=None) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            input_model=input_model,
            handler=handler,
            timeout_s=1.0,
            risk=ToolRisk.READ_ONLY,
            output_model=output_model,
        )

    return [
        spec(
            "search_products",
            "Find products in the store catalog by keywords.",
            SearchInput,
            search_products,
        ),
        spec(
            "get_product_details",
            "Get facts about one product: features, specs, box contents, warranty, origin.",
            DetailsInput,
            get_product_details,
        ),
        spec(
            "check_compatibility",
            "Check whether a product is listed as compatible with the shopper's device.",
            CompatibilityInput,
            check_compatibility,
            CompatibilityResult,
        ),
        spec(
            "get_return_policy",
            "Return policy, warranty and customer care contacts for a product.",
            ProductInput,
            get_return_policy,
        ),
    ]
