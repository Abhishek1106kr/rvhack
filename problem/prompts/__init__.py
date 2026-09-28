from pathlib import Path

from problem.knowledge import Catalog


def system_prompt(catalog: Catalog) -> str:
    template = Path(__file__).with_name("system.md").read_text()
    listing = "\n".join(f"- {p.id}: {p.name}" for p in catalog.products)
    return template.format(catalog=listing)
