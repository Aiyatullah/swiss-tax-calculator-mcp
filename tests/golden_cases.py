"""Cases pinned against recorded ESTV output.

Every case uses a closed tax year. Scales for a finished year do not change,
so any movement here means ESTV altered something upstream, which for a
reverse-engineered API is exactly what we want to hear about.

Regenerate the recorded values with `uv run python scripts/update_golden.py`,
and read the diff before committing it.
"""

from __future__ import annotations

from typing import Any

YEAR = 2024

ZURICH = {"location": "8001", "tax_year": YEAR}
ZUG = {"location": "6300", "tax_year": YEAR}


def _paths(*names: str) -> list[str]:
    return list(names)


# name -> (tool, kwargs, dotted paths whose values are pinned)
CASES: list[tuple[str, str, dict[str, Any], list[str]]] = [
    (
        "single_120k_zurich",
        "calculate_tax",
        ZURICH | {"income1": 120000, "include_breakdown": False},
        _paths(
            "total_tax",
            "by_level.federal",
            "by_level.cantonal",
            "by_level.municipal",
            "taxable.income_cantonal",
            "rates_percent.marginal_on_income",
        ),
    ),
    (
        "family_zug_with_3a_and_wealth",
        "calculate_tax",
        ZUG
        | {
            "income1": 140000,
            "income2": 60000,
            "relationship": "married",
            "children_ages": [4, 9],
            "confession1": "roman_catholic",
            "confession2": "roman_catholic",
            "wealth": 400000,
            "deductions": {"PRAEMIEN3A": 7056, "SCHULDZINSEN": 9000},
            "include_breakdown": False,
        },
        _paths("total_tax", "by_level.church", "by_object.wealth_tax", "taxable.income_cantonal"),
    ),
    (
        "pensioner_bern",
        "calculate_tax",
        {
            "location": "3011",
            "tax_year": YEAR,
            "income1": 90000,
            "income_type1": "pensioner",
            "age1": 70,
            "include_breakdown": False,
        },
        _paths("total_tax", "by_level.cantonal", "taxable.income_federal"),
    ),
    (
        "taxable_amounts_zurich",
        "calculate_tax_from_taxable_amounts",
        ZURICH | {"taxable_income_cantonal": 100000, "taxable_income_federal": 100000, "taxable_wealth": 200000},
        _paths("total_tax", "by_object.income_tax", "by_object.wealth_tax"),
    ),
    (
        "capitals_ranking_150k",
        "compare_locations",
        {"income1": 150000, "scope": "capitals", "tax_year": YEAR, "top": 3},
        _paths("count", "statistics.min", "statistics.median", "statistics.max", "cheapest.0.municipality"),
    ),
    (
        "canton_zg_ranking",
        "compare_locations",
        {"income1": 200000, "scope": "ZG", "tax_year": YEAR, "top": 1},
        _paths("count", "statistics.min", "cheapest.0.municipality", "most_expensive.0.municipality"),
    ),
    (
        "capital_payment_zurich",
        "calculate_capital_payment_tax",
        {"location": "8001", "capital": 500000, "age_at_payment": 65, "tax_year": YEAR},
        _paths("result.total_tax", "result.federal", "result.cantonal", "result.effective_rate_percent"),
    ),
    (
        "withdrawal_plan_600k",
        "plan_capital_withdrawals",
        {
            "location": "8001",
            "total_capital": 600000,
            "first_year": 2022,
            "age_at_first_withdrawal": 62,
            "max_tranches": 3,
        },
        _paths("plans.0.total_tax", "plans.1.total_tax", "plans.2.total_tax", "max_saving"),
    ),
    (
        "deduction_3a_value",
        "deduction_value",
        ZURICH | {"income1": 120000, "max_amount": 6000},
        _paths("steps.0.total_tax", "steps.4.total_tax", "total_saving_at_max"),
    ),
    (
        "inheritance_zurich_relationships",
        "calculate_inheritance_tax",
        {"location": "8001", "amount": 500000, "tax_year": YEAR},
        _paths("count", "results.0.total_tax", "results.37.total_tax"),
    ),
    (
        "inheritance_unrelated_capitals",
        "calculate_inheritance_tax",
        {"location": "capitals", "amount": 500000, "beneficiary": "unrelated", "tax_year": YEAR, "top": 1},
        _paths("count", "statistics.min", "statistics.median", "statistics.max"),
    ),
    (
        "company_zurich",
        "calculate_company_tax",
        {"location": "8001", "taxable_profit": 200000, "taxable_capital": 100000, "tax_year": YEAR},
        _paths(
            "result.total_tax", "result.profit_tax", "result.capital_tax", "result.effective_rate_on_profit_percent"
        ),
    ),
    (
        "company_capitals",
        "calculate_company_tax",
        {"location": "capitals", "taxable_profit": 1000000, "taxable_capital": 500000, "tax_year": YEAR, "top": 1},
        _paths("count", "statistics.min", "statistics.max", "cheapest.0.canton"),
    ),
    (
        "brackets_zurich_cantonal",
        "explain_tax_brackets",
        ZURICH | {"taxable_income": 100000},
        _paths(
            "simple_tax", "current_band.from", "current_band.to", "current_band.rate_percent", "amount_to_next_band"
        ),
    ),
    (
        "brackets_zurich_federal",
        "explain_tax_brackets",
        ZURICH | {"taxable_income": 100000, "target": "federal"},
        _paths("simple_tax", "current_band.from", "current_band.rate_percent"),
    ),
    (
        "nearby_zurich_hb",
        "find_cheapest_nearby",
        {"latitude": 47.3779, "longitude": 8.5403, "radius_km": 10, "income1": 150000, "tax_year": YEAR, "top": 1},
        _paths("municipalities_priced", "statistics.min", "statistics.max", "cheapest.0.municipality"),
    ),
]


def extract(payload: Any, path: str) -> Any:
    """Read a dotted path, treating all-digit segments as list indices."""
    node = payload
    for part in path.split("."):
        node = node[int(part)] if part.isdigit() else node[part]
    return node
