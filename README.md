# Swiss Tax Calculator — MCP Server & Python API

>Licensed under MIT by [Aiyatullah Saiyed](https://github.com/aiyatullah).

A dual-purpose Swiss tax toolkit: use it as a **Python API client** in your applications, or run it as an **MCP server** for AI assistants like Claude. Wraps the official [ESTV Swiss Federal Tax Calculator](https://swisstaxcalculator.estv.admin.ch/) — no API key or authentication required.

Covers income, wealth, lump-sum capital, inheritance/gift, and corporate taxes for every Swiss municipality across tax years 2010–2026.

---

## Features

| Category | Tools |
|---|---|
| **Household tax** | `find_location`, `calculate_tax`, `list_deductions`, `calculate_tax_from_taxable_amounts` |
| **Compare places** | `compare_locations`, `find_cheapest_nearby` |
| **Tax planning** | `plan_capital_withdrawals`, `deduction_value` |
| **Other taxes** | `calculate_capital_payment_tax`, `calculate_inheritance_tax`, `calculate_company_tax` |
| **Reference** | `explain_tax_brackets`, `get_tax_years` |

---

## Quick Start

### Install

```bash
# Clone the repository
git clone https://github.com/aiyatullah/swiss-tax-calculator-mcp.git
cd swiss-tax-calculator-mcp

# Install dependencies
uv sync
```

---

## Usage as Python API

Import `EstvClient` directly in your Python code to query the ESTV tax calculator programmatically.

### Basic Example — Find a Location

```python
from estv_mcp import EstvClient

client = EstvClient()

# Search for a municipality by name or postal code
locations = client.search_location("Zug", tax_year=2026)
for loc in locations[:3]:
    print(f"{loc['City']} ({loc['Canton']}) — ID: {loc['TaxLocationID']}")
```

### Calculate Income Tax

```python
from estv_mcp.api import EstvClient, RELATIONSHIP, CONFESSION, INCOME_TYPE

client = EstvClient()

# Step 1: Find the tax location
locations = client.search_location("8001", tax_year=2026)
location_id = locations[0]["TaxLocationID"]

# Step 2: Get the tax budget (deduction sheet)
budget = client.tax_budget({
    "SimKey": None,
    "TaxYear": 2026,
    "TaxLocationID": location_id,
    "Relationship": RELATIONSHIP["single"],
    "Confession1": CONFESSION["none"],
    "Confession2": 0,
    "Children": [],
    "Age1": 35,
    "RevenueType1": INCOME_TYPE["employed"],
    "Revenue1": 120_000,
    "Age2": 0,
    "RevenueType2": 0,
    "Revenue2": 0,
    "Fortune": 50_000,
})

# Step 3: Calculate detailed taxes
result = client.detailed_taxes({
    "SimKey": None,
    "TaxYear": 2026,
    "TaxLocationID": location_id,
    "Relationship": RELATIONSHIP["single"],
    "Confession1": CONFESSION["none"],
    "Confession2": 0,
    "Children": [],
    "Age1": 35,
    "RevenueType1": INCOME_TYPE["employed"],
    "Revenue1": 120_000,
    "Age2": 0,
    "RevenueType2": 0,
    "Revenue2": 0,
    "Fortune": 0,
    "Budget": budget,
})

print(f"Total tax: CHF {result['TotalTax']:,.0f}")
print(f"Federal:   CHF {result.get('IncomeTaxFed', 0):,.0f}")
print(f"Cantonal:  CHF {result.get('IncomeTaxCanton', 0):,.0f}")
print(f"Municipal: CHF {result.get('IncomeTaxCity', 0):,.0f}")
```

### Compare Municipalities

```python
from estv_mcp.api import EstvClient, RELATIONSHIP, CONFESSION, INCOME_TYPE, GROUP_CAPITALS

client = EstvClient()

# Compare tax burden across all 26 cantonal capitals
rows = client.many_simple_taxes({
    "SimKey": None,
    "TaxYear": 2026,
    "TaxGroupID": GROUP_CAPITALS,
    "Relationship": RELATIONSHIP["single"],
    "Confession1": CONFESSION["none"],
    "Confession2": 0,
    "Children": [],
    "Age1": 35,
    "RevenueType1": INCOME_TYPE["employed"],
    "Revenue1": 100_000,
    "Age2": 0,
    "RevenueType2": 0,
    "Revenue2": 0,
    "Fortune": 0,
})

ranked = sorted(rows, key=lambda r: r["TotalTax"])
print("Top 5 cheapest cantonal capitals:")
for r in ranked[:5]:
    loc = r["Location"]
    print(f"  {loc['City']} ({loc['Canton']}): CHF {r['TotalTax']:,.0f}")
```

### Inheritance Tax

```python
from estv_mcp.api import EstvClient, BENEFICIARY, GROUP_CAPITALS

client = EstvClient()

# Inheritance tax for a sibling across cantonal capitals
rows = client.many_inheritance_taxes({
    "SimKey": None,
    "TaxYear": 2026,
    "TaxGroupID": GROUP_CAPITALS,
    "OnlyGroupID": BENEFICIARY["sibling"][0],
    "OnlyPersonID": BENEFICIARY["sibling"][1],
    "Donation": False,
    "Amount": 500_000,
})

ranked = sorted(rows, key=lambda r: r["TaxTotal"])
print(f"Cheapest: {ranked[0]['Location']['City']} — CHF {ranked[0]['TaxTotal']:,.0f}")
print(f"Most expensive: {ranked[-1]['Location']['City']} — CHF {ranked[-1]['TaxTotal']:,.0f}")
```

### Available API Operations

| Method | Description |
|---|---|
| `search_location(query, tax_year)` | Find municipalities by name or postal code |
| `search_location_geo(lat, lon, radius_km, tax_year)` | Find municipalities within a radius |
| `tax_budget(payload)` | Get the deduction/budget sheet for a household |
| `detailed_taxes(payload)` | Full tax calculation with breakdown |
| `simple_taxes(payload)` | Tax from pre-computed taxable amounts |
| `many_simple_taxes(payload)` | Compare tax across many municipalities |
| `many_capital_taxes(payload)` | Lump-sum capital withdrawal tax across municipalities |
| `many_inheritance_taxes(payload)` | Inheritance/gift tax across municipalities |
| `many_legal_entity_taxes(payload)` | Corporate tax across municipalities |
| `export_tax_scales(tax_year, tax_group_id)` | Raw tax bracket tables |
| `tax_year_range(calculator)` | Supported year range per calculator |
| `tax_version()` | Current ESTV data version |

### Enum Reference

```python
from estv_mcp.api import (
    RELATIONSHIP,      # single=1, married=2, concubinage=3, registered_partnership=4
    CONFESSION,        # reformed=1, roman_catholic=2, christ_catholic=3, none=4, other=5
    INCOME_TYPE,       # employed=1, self_employed=2, pensioner=3, other=4
    GENDER,            # male=1, female=2
    LANGUAGE,          # de=1, fr=2, it=3, en=4
    CANTON_GROUP,      # AG=1 ... ZH=26
    GROUP_CAPITALS,    # 88 — all 26 cantonal capitals
    GROUP_SWITZERLAND, # 99 — every municipality (~2100)
    CALCULATOR,        # income_wealth=1, capital_payment=2, legal_entity=3, inheritance=5
    BENEFICIARY,       # spouse, child, sibling, unrelated, etc. → (GroupID, PersonID)
)
```

### Caching

Responses are cached under `~/.cache/estv-mcp` for one week. Control via environment variables:

| Variable | Effect |
|---|---|
| `ESTV_MCP_NO_CACHE=1` | Disable caching entirely |
| `ESTV_MCP_CACHE_DIR=/path` | Custom cache directory |
| `ESTV_MCP_CACHE_TTL=3600` | Cache lifetime in seconds |

---

## Usage as MCP Server

Run this project as an MCP server to give AI assistants (Claude, etc.) access to all Swiss tax tools.

### Register with Claude Code

```bash
# Project-local (this repo only)
claude mcp add estv-tax -- uv run --directory "$PWD" estv-mcp

# User-wide (available in every project)
claude mcp add -s user estv-tax -- uv run --directory "$PWD" estv-mcp
```

### MCP Client Config (JSON)

For any MCP-compatible client, add to your config:

```json
{
  "mcpServers": {
    "estv-tax": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/swiss-tax-calculator-mcp", "estv-mcp"]
    }
  }
}
```

### What the AI Can Do

Once connected, the AI assistant can:

- **"How much tax would I pay in Zurich on CHF 150,000?"** — calls `find_location` + `calculate_tax`
- **"Compare Zug vs Schwyz for a married couple with 2 kids"** — calls `compare_locations`
- **"Where's the cheapest place within 30km of Zurich HB?"** — calls `find_cheapest_nearby`
- **"Should I split my pillar 2 withdrawal over multiple years?"** — calls `plan_capital_withdrawals`
- **"What's the inheritance tax for a sibling in Geneva?"** — calls `calculate_inheritance_tax`
- **"How much would my company pay in Zug vs Lucerne?"** — calls `calculate_company_tax`

---

## Development

```bash
uv sync                                          # Install dependencies (including dev)
uv run pytest -q                                  # Run tests (hits live ESTV API)
uv run python scripts/stdio_smoke.py              # MCP stdio handshake test
uv run ruff check . && uv run ruff format --check .  # Lint & format check
```

### Golden Values

`tests/golden.json` pins known tax figures for closed tax years. The weekly CI run re-checks them against the live API to detect upstream changes.

```bash
uv run python scripts/update_golden.py   # Update golden values (prints diffs)
```

### CI

Tests run on Python 3.11/3.12/3.13 on every push, plus a weekly schedule to catch ESTV data changes.

---

## Important Notes

- All amounts are **CHF per year**
- `income_type='employed'` means gross salary — social contributions (AHV/IV/EO, ALV, NBU, BVG) are derived automatically
- Override deductions via `deductions={id: value}` using IDs from `list_deductions` (e.g. `PRAEMIEN3A` for pillar 3a, `SCHULDZINSEN` for mortgage interest)
- `compare_locations(scope='switzerland')` covers ~2,100 municipalities; results are summarized to stay readable
- Church tax only applies when `confession` is not `none`
- Figures are from the official ESTV model — **not a binding tax assessment**

---

## License

MIT License — see [LICENSE](LICENSE).

Inspired by and built upon [noaahh/estv-mcp](https://github.com/noaahh/estv-mcp).
