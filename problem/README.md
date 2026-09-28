# problem/

Hackathon-specific behavior. Current problem: a **voice product assistant** for an online
store, answering shopper questions about a product listing by voice.
Problem Statement : 
We have build something related to Voice AI 
for now we can develop the whole template all we need to just deployment

May import `backend/app`. `backend/app` must never import from here.

| Path | What |
|---|---|
| `knowledge/catalog.json` | the catalog: the Motorola 68W TurboPower charger listing, as given |
| `domain.py` | `Product` model; `normalize_device` (handles STT output like "g eighty four") |
| `tools/` | read-only tools: `search_products`, `get_product_details`, `check_compatibility`, `get_return_policy` |
| `workflows/` | deterministic planner: routes compatibility / returns / spec questions to tools *before* the LLM, so a small model can't skip the lookup or invent a policy |
| `prompts/system.md` | voice-first system prompt |
| `app.py` | assembles the agent: runtime + real adapters + tools + prompt + planner |
| `evaluation/` | deterministic scenarios (`make check`) and `live_eval.py` (`make eval-live`) |

No prices, stock, orders, or other products: the listing has no data for them, so the
assistant says so instead of inventing them. To add products, append to `catalog.json`.
