"""MCP server exposing the ESTV Swiss tax calculator."""

from __future__ import annotations

import statistics
from functools import lru_cache
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .api import (
    BENEFICIARY,
    CALCULATOR,
    CANTON_GROUP,
    CONFESSION,
    GENDER,
    GROUP_CAPITALS,
    GROUP_SWITZERLAND,
    INCOME_TYPE,
    LANGUAGE,
    RELATIONSHIP,
    EstvClient,
    EstvError,
)

mcp = FastMCP(
    "estv-tax",
    instructions=(
        "Swiss income, wealth and lump-sum tax figures from the official ESTV "
        "calculator (swisstaxcalculator.estv.admin.ch), covering every "
        "municipality and tax years 2010-2026.\n\n"
        "Typical flow: find_location -> calculate_tax. Use compare_locations to "
        "rank municipalities for the same household, and list_deductions before "
        "passing the `deductions` argument to calculate_tax.\n\n"
        "All amounts are CHF per year. Results are the official model figures, "
        "not a binding assessment."
    ),
)

client = EstvClient()

Relationship = Literal["single", "married", "concubinage", "registered_partnership"]
Confession = Literal["reformed", "roman_catholic", "christ_catholic", "none", "other"]
IncomeType = Literal["employed", "self_employed", "pensioner", "other"]
Lang = Literal["de", "fr", "it", "en"]

DEFAULT_YEAR = 2026


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _text(block: dict[str, Any] | None, lang: str) -> str:
    """Pick a localized label, falling back to German (EN is often blank)."""
    if not block:
        return ""
    return block.get(lang.upper()) or block.get("DE") or block.get("ID") or ""


@lru_cache(maxsize=8)
def _year_range(calculator: str) -> tuple[int, int]:
    rng = client.tax_year_range(CALCULATOR[calculator])
    return rng["MinYear"], rng["MaxYear"]


def _check_year(tax_year: int, calculator: str = "income_wealth") -> int:
    lo, hi = _year_range(calculator)
    if not lo <= tax_year <= hi:
        raise EstvError(f"tax_year {tax_year} is out of range; ESTV covers {lo}-{hi} for this calculator.")
    return tax_year


def _resolve_location(location: str | int, tax_year: int, lang: str) -> dict[str, Any]:
    raw = str(location).strip()
    # Tax location ids are 9 digits; anything shorter is a postal code or name.
    if raw.isdigit() and len(raw) >= 7:
        return {"TaxLocationID": int(raw)}
    hits = client.search_location(raw, tax_year, LANGUAGE[lang])
    if not hits:
        raise EstvError(
            f"no Swiss tax location matches {location!r} for {tax_year}. "
            "Try a postal code (e.g. '8001') or an exact municipality name."
        )
    return hits[0]


def _person_payload(
    *,
    tax_year: int,
    relationship: str,
    confession1: str,
    confession2: str | None,
    children_ages: list[int] | None,
    age1: int,
    income1: float,
    income_type1: str,
    age2: int | None,
    income2: float,
    income_type2: str,
    wealth: float,
) -> dict[str, Any]:
    joint = relationship in ("married", "registered_partnership")
    payload: dict[str, Any] = {
        "SimKey": None,
        "TaxYear": tax_year,
        "Relationship": RELATIONSHIP[relationship],
        "Confession1": CONFESSION[confession1],
        "Children": [{"Age": int(a)} for a in (children_ages or [])],
        "Age1": int(age1),
        "RevenueType1": INCOME_TYPE[income_type1],
        "Revenue1": round(income1),
        "Fortune": round(wealth),
    }
    if joint:
        payload |= {
            "Confession2": CONFESSION[confession2 or "none"],
            "Age2": int(age2 if age2 is not None else age1),
            "RevenueType2": INCOME_TYPE[income_type2],
            "Revenue2": round(income2),
        }
    else:
        payload |= {"Confession2": 0, "Age2": 0, "RevenueType2": 0, "Revenue2": 0}
    return payload


def _rate(tax: float, base: float) -> float | None:
    return round(100 * tax / base, 2) if base else None


def _breakdown(entries: list[dict], lang: str) -> list[dict]:
    # ESTV emits the full line structure in each section and zero-fills the
    # ones that do not apply, which triples the payload for no information.
    return [
        {
            "group": _text(e.get("Group"), lang),
            "item": _text(e.get("Entry"), lang),
            "federal": e.get("Fed"),
            "cantonal": e.get("Canton"),
        }
        for e in entries or []
        if e.get("Fed") or e.get("Canton")
    ]


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


@mcp.tool()
def find_location(
    query: Annotated[str, Field(description="Postal code, municipality or city name, e.g. '8001', 'Zug', 'Lausanne'.")],
    tax_year: Annotated[int, Field(description="Tax year the location must exist in.")] = DEFAULT_YEAR,
    language: Lang = "de",
    limit: Annotated[int, Field(description="Max results.", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Resolve a place name or postal code to an ESTV tax location id.

    Municipal tax multipliers differ inside a canton and sometimes inside a
    postal code, so every calculation is anchored on a `tax_location_id`.
    """
    _check_year(tax_year)
    hits = client.search_location(query, tax_year, LANGUAGE[language])
    return {
        "query": query,
        "tax_year": tax_year,
        "count": len(hits),
        "locations": [
            {
                "tax_location_id": h["TaxLocationID"],
                "zip": h["ZipCode"],
                "city": h["City"],
                "municipality": h["BfsName"] or h["City"],
                "canton": h["Canton"],
                "bfs_id": h["BfsID"],
            }
            for h in hits[:limit]
        ],
    }


@mcp.tool()
def list_deductions(
    location: Annotated[str | int, Field(description="Tax location id, postal code or municipality name.")],
    income1: Annotated[float, Field(description="Annual income of person 1, CHF.", ge=0)],
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    income_type1: IncomeType = "employed",
    income2: Annotated[float, Field(description="Annual income of the spouse, CHF.", ge=0)] = 0,
    income_type2: IncomeType = "employed",
    age1: int = 40,
    age2: int | None = None,
    children_ages: list[int] | None = None,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    wealth: Annotated[float, Field(description="Net wealth at year end, CHF.", ge=0)] = 0,
    language: Lang = "de",
) -> dict[str, Any]:
    """Show the deduction/budget sheet ESTV derives for a household.

    Returns every line item with the value ESTV assumes by default. Lines
    marked `editable` can be overridden through the `deductions` argument of
    `calculate_tax` (keyed by `id`), e.g. `PRAEMIEN3A` for pillar 3a
    contributions or `SCHULDZINSEN` for mortgage interest. Non-editable lines
    (gross salary, AHV/ALV/BVG contributions) are computed by the model.
    """
    _check_year(tax_year)
    loc = _resolve_location(location, tax_year, language)
    payload = _person_payload(
        tax_year=tax_year,
        relationship=relationship,
        confession1=confession1,
        confession2=confession2,
        children_ages=children_ages,
        age1=age1,
        income1=income1,
        income_type1=income_type1,
        age2=age2,
        income2=income2,
        income_type2=income_type2,
        wealth=wealth,
    ) | {"TaxLocationID": loc["TaxLocationID"]}

    section = {1: "income", 2: "deductions", 3: "wealth"}
    lines = client.tax_budget(payload)
    return {
        "tax_year": tax_year,
        "note": "Values are CHF per year. Override editable lines via calculate_tax(deductions={id: value}).",
        "lines": [
            {
                "id": b["Ident"],
                "label": _text(b.get("Name"), language),
                "section": section.get(b.get("Main"), "other"),
                "default_value": b["Value"],
                "editable": bool(b.get("Show")),
            }
            for b in lines
        ],
    }


@mcp.tool()
def calculate_tax(
    location: Annotated[str | int, Field(description="Tax location id, postal code or municipality name.")],
    income1: Annotated[
        float, Field(description="Annual income of person 1, CHF. Gross salary when income_type1='employed'.", ge=0)
    ],
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    income_type1: IncomeType = "employed",
    income2: Annotated[
        float, Field(description="Annual income of the spouse, CHF. Only used for married/registered partners.", ge=0)
    ] = 0,
    income_type2: IncomeType = "employed",
    age1: int = 40,
    age2: int | None = None,
    children_ages: Annotated[list[int] | None, Field(description="Age of each dependent child, e.g. [4, 9].")] = None,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    wealth: Annotated[float, Field(description="Net taxable wealth at year end, CHF.", ge=0)] = 0,
    deductions: Annotated[
        dict[str, float] | None,
        Field(
            description="Overrides for the ESTV budget sheet, keyed by the ids from list_deductions, e.g. {'PRAEMIEN3A': 7258}."
        ),
    ] = None,
    include_breakdown: Annotated[
        bool, Field(description="Include the line-by-line derivation of taxable income.")
    ] = True,
    language: Lang = "de",
) -> dict[str, Any]:
    """Compute federal, cantonal, municipal and church tax for a household.

    Uses ESTV's detailed model: it derives social-insurance contributions and
    standard deductions from the gross figures, so you only need income,
    wealth and family situation. Call `list_deductions` first if you want to
    supply pillar 3a, mortgage interest or other real deductions.
    """
    _check_year(tax_year)
    loc = _resolve_location(location, tax_year, language)
    base = _person_payload(
        tax_year=tax_year,
        relationship=relationship,
        confession1=confession1,
        confession2=confession2,
        children_ages=children_ages,
        age1=age1,
        income1=income1,
        income_type1=income_type1,
        age2=age2,
        income2=income2,
        income_type2=income_type2,
        wealth=wealth,
    ) | {"TaxLocationID": loc["TaxLocationID"]}

    budget = client.tax_budget(base)
    applied: dict[str, float] = {}
    if deductions:
        known = {b["Ident"] for b in budget}
        unknown = sorted(set(deductions) - known)
        if unknown:
            raise EstvError(
                f"unknown deduction id(s): {', '.join(unknown)}. "
                "Call list_deductions for the ids valid for this household."
            )
        for line in budget:
            if line["Ident"] in deductions:
                line["Value"] = round(deductions[line["Ident"]])
                applied[line["Ident"]] = line["Value"]

    # Detailed mode carries net wealth inside the budget (NETTO_VM), not Fortune.
    r = client.detailed_taxes(base | {"Fortune": 0, "Budget": budget})

    total_income = income1 + (income2 if relationship in ("married", "registered_partnership") else 0)
    rl = r.get("Location", {})
    income_tax = sum(r.get(k, 0) for k in ("IncomeTaxFed", "IncomeTaxCanton", "IncomeTaxCity", "IncomeTaxChurch"))
    wealth_tax = sum(r.get(k, 0) for k in ("FortuneTaxCanton", "FortuneTaxCity", "FortuneTaxChurch"))

    out: dict[str, Any] = {
        "location": {
            "tax_location_id": rl.get("TaxLocationID"),
            "zip": rl.get("ZipCode"),
            "municipality": rl.get("BfsName") or rl.get("City"),
            "canton": rl.get("Canton"),
        },
        "tax_year": tax_year,
        "total_tax": r.get("TotalTax"),
        "by_level": {
            "federal": r.get("IncomeTaxFed"),
            "cantonal": r.get("IncomeTaxCanton", 0) + r.get("FortuneTaxCanton", 0),
            "municipal": r.get("IncomeTaxCity", 0) + r.get("FortuneTaxCity", 0),
            "church": r.get("IncomeTaxChurch", 0) + r.get("FortuneTaxChurch", 0),
            "personal_tax": r.get("PersonalTax"),
            "tax_credit": r.get("TaxCredit"),
        },
        "by_object": {"income_tax": income_tax, "wealth_tax": wealth_tax},
        "taxable": {
            "income_federal": r.get("TaxableIncomeFed"),
            "income_cantonal": r.get("TaxableIncomeCanton"),
            "wealth_cantonal": r.get("TaxableFortuneCanton"),
        },
        "rates_percent": {
            "effective_on_gross_income": _rate(r.get("TotalTax", 0), total_income),
            "marginal_on_income": r.get("MarginalTaxRate"),
            "marginal_on_wealth": r.get("MarginalTaxRateVM"),
        },
        "tax_freedom_day_offset_days": r.get("TaxFreedomDay"),
        "deductions_applied": applied,
    }
    if r.get("Diagnosis"):
        out["diagnosis"] = r["Diagnosis"]
    if include_breakdown:
        out["breakdown"] = {
            "federal_and_cantonal": _breakdown(r.get("InfoBoth", []), language),
            "cantonal_only": _breakdown(r.get("InfoCanton", []), language),
            "federal_only": _breakdown(r.get("InfoFed", []), language),
        }
    return out


@mcp.tool()
def calculate_tax_from_taxable_amounts(
    location: Annotated[str | int, Field(description="Tax location id, postal code or municipality name.")],
    taxable_income_cantonal: Annotated[
        float, Field(description="Taxable income per the cantonal assessment, CHF.", ge=0)
    ],
    taxable_income_federal: Annotated[
        float, Field(description="Taxable income per the federal assessment, CHF.", ge=0)
    ],
    taxable_wealth: Annotated[
        float, Field(description="Taxable net wealth per the cantonal assessment, CHF.", ge=0)
    ] = 0,
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    children_ages: list[int] | None = None,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    language: Lang = "de",
) -> dict[str, Any]:
    """Apply the tax scales to amounts already known to be taxable.

    Use this when the user reads figures off a tax assessment or return
    (`steuerbares Einkommen` / `revenu imposable`) instead of a gross salary.
    Note that cantonal and federal taxable income usually differ.
    """
    _check_year(tax_year)
    loc = _resolve_location(location, tax_year, language)
    joint = relationship in ("married", "registered_partnership")
    r = client.simple_taxes(
        {
            "SimKey": None,
            "TaxYear": tax_year,
            "TaxLocationID": loc["TaxLocationID"],
            "Relationship": RELATIONSHIP[relationship],
            "Confession1": CONFESSION[confession1],
            "Confession2": CONFESSION[confession2 or "none"] if joint else 0,
            "Children": [{"Age": int(a)} for a in (children_ages or [])],
            "TaxableIncomeCanton": round(taxable_income_cantonal),
            "TaxableIncomeFed": round(taxable_income_federal),
            "TaxableFortune": round(taxable_wealth),
        }
    )
    rl = r.get("Location", {})
    return {
        "location": {
            "tax_location_id": rl.get("TaxLocationID"),
            "zip": rl.get("ZipCode"),
            "municipality": rl.get("BfsName") or rl.get("City"),
            "canton": rl.get("Canton"),
        },
        "tax_year": tax_year,
        "total_tax": r.get("TotalTax"),
        "by_level": {
            "federal": r.get("IncomeTaxFed"),
            "cantonal": r.get("IncomeTaxCanton", 0) + r.get("FortuneTaxCanton", 0),
            "municipal": r.get("IncomeTaxCity", 0) + r.get("FortuneTaxCity", 0),
            "church": r.get("IncomeTaxChurch", 0) + r.get("FortuneTaxChurch", 0),
            "personal_tax": r.get("PersonalTax"),
        },
        "by_object": {
            "income_tax": sum(
                r.get(k, 0) for k in ("IncomeTaxFed", "IncomeTaxCanton", "IncomeTaxCity", "IncomeTaxChurch")
            ),
            "wealth_tax": sum(r.get(k, 0) for k in ("FortuneTaxCanton", "FortuneTaxCity", "FortuneTaxChurch")),
        },
        "rates_percent": {
            "effective_on_cantonal_taxable_income": _rate(r.get("TotalTax", 0), taxable_income_cantonal),
        },
    }


def _resolve_group(scope: str, tax_year: int, language: str) -> tuple[int, str]:
    key = scope.strip().upper()
    if key in ("CH", "SWITZERLAND", "ALL"):
        return GROUP_SWITZERLAND, "all Swiss municipalities"
    if key in ("CAPITALS", "CANTONAL_CAPITALS"):
        return GROUP_CAPITALS, "the 26 cantonal capitals"
    if key in CANTON_GROUP:
        return CANTON_GROUP[key], f"all municipalities in canton {key}"
    loc = _resolve_location(scope, tax_year, language)
    return loc["TaxLocationID"], f"municipality {scope}"


def _rank(rows: list[dict], top: int, cantons: list[str] | None) -> dict[str, Any]:
    if cantons:
        wanted = {c.upper() for c in cantons}
        rows = [r for r in rows if r["Location"]["Canton"] in wanted]
    if not rows:
        return {"count": 0, "cheapest": [], "most_expensive": []}

    def entry(r: dict) -> dict:
        loc = r["Location"]
        return {
            "tax_location_id": loc["TaxLocationID"],
            "municipality": loc["BfsName"] or loc["City"],
            "canton": loc["Canton"],
            "zip": loc["ZipCode"],
            "total_tax": r["TotalTax"],
        }

    ordered = sorted(rows, key=lambda r: r["TotalTax"])
    taxes = [r["TotalTax"] for r in ordered]
    return {
        "count": len(ordered),
        "statistics": {
            "min": taxes[0],
            "median": round(statistics.median(taxes)),
            "max": taxes[-1],
            "spread_max_minus_min": taxes[-1] - taxes[0],
        },
        "cheapest": [entry(r) for r in ordered[:top]],
        "most_expensive": [entry(r) for r in ordered[-top:][::-1]],
    }


@mcp.tool()
def compare_locations(
    income1: Annotated[float, Field(description="Annual income of person 1, CHF.", ge=0)],
    scope: Annotated[
        str,
        Field(
            description="'switzerland' for every municipality, 'capitals' for the 26 cantonal capitals, or a canton code such as 'ZG'."
        ),
    ] = "capitals",
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    income_type1: IncomeType = "employed",
    income2: Annotated[float, Field(description="Annual income of the spouse, CHF.", ge=0)] = 0,
    income_type2: IncomeType = "employed",
    age1: int = 40,
    age2: int | None = None,
    children_ages: list[int] | None = None,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    wealth: Annotated[float, Field(description="Net taxable wealth, CHF.", ge=0)] = 0,
    only_cantons: Annotated[
        list[str] | None, Field(description="Restrict the ranking to these canton codes, e.g. ['ZH','ZG','SZ'].")
    ] = None,
    top: Annotated[
        int, Field(description="How many cheapest and most expensive municipalities to return.", ge=1, le=100)
    ] = 10,
    language: Lang = "de",
) -> dict[str, Any]:
    """Rank municipalities by total tax burden for one and the same household.

    Answers "where would I pay the least?". Scanning all of Switzerland covers
    ~2100 municipalities, so only the extremes plus summary statistics are
    returned; narrow with `scope` or `only_cantons` to see a specific region.
    """
    _check_year(tax_year)
    group_id, scope_label = _resolve_group(scope, tax_year, language)
    payload = _person_payload(
        tax_year=tax_year,
        relationship=relationship,
        confession1=confession1,
        confession2=confession2,
        children_ages=children_ages,
        age1=age1,
        income1=income1,
        income_type1=income_type1,
        age2=age2,
        income2=income2,
        income_type2=income_type2,
        wealth=wealth,
    ) | {"TaxGroupID": group_id}

    rows = client.many_simple_taxes(payload)
    result = _rank(rows, top, only_cantons)
    return {
        "scope": scope_label,
        "tax_year": tax_year,
        "note": "Total annual tax in CHF (federal + cantonal + municipal + church) for the identical household in each municipality.",
        **result,
    }


@mcp.tool()
def calculate_capital_payment_tax(
    location: Annotated[
        str | int,
        Field(description="Tax location id, postal code, municipality name, canton code, 'capitals' or 'switzerland'."),
    ],
    capital: Annotated[float, Field(description="Lump sum paid out, CHF.", ge=0)],
    age_at_payment: Annotated[
        int, Field(description="Age of the beneficiary when the capital is paid out.", ge=0, le=120)
    ],
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    gender: Literal["male", "female"] = "male",
    number_of_children: int = 0,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    top: Annotated[
        int,
        Field(description="When comparing many locations, how many to return per end of the ranking.", ge=1, le=100),
    ] = 10,
    language: Lang = "de",
) -> dict[str, Any]:
    """Tax on a lump-sum payout from pillar 2 or pillar 3a.

    Capital withdrawals are taxed separately from ordinary income at a reduced
    rate, and the rate varies a lot between cantons. Pass a canton code,
    'capitals' or 'switzerland' as `location` to rank places instead of
    computing a single figure.
    """
    _check_year(tax_year, "capital_payment")
    group_id, scope_label = _resolve_group(str(location), tax_year, language)
    joint = relationship in ("married", "registered_partnership")
    rows = client.many_capital_taxes(
        {
            "SimKey": None,
            "TaxYear": tax_year,
            "TaxGroupID": group_id,
            "Relationship": RELATIONSHIP[relationship],
            "Confession1": CONFESSION[confession1],
            "Confession2": CONFESSION[confession2 or "none"] if joint else 0,
            "NumberOfChildren": int(number_of_children),
            "Gender": GENDER[gender],
            "AgeAtPayment": int(age_at_payment),
            "Capital": round(capital),
        }
    )

    def entry(r: dict) -> dict:
        loc = r["Location"]
        total = r.get("TaxFed", 0) + r.get("TaxCanton", 0) + r.get("TaxCity", 0) + r.get("TaxChurch", 0)
        return {
            "tax_location_id": loc["TaxLocationID"],
            "municipality": loc["BfsName"] or loc["City"],
            "canton": loc["Canton"],
            "zip": loc["ZipCode"],
            "total_tax": total,
            "federal": r.get("TaxFed"),
            "cantonal": r.get("TaxCanton"),
            "municipal": r.get("TaxCity"),
            "church": r.get("TaxChurch"),
            "effective_rate_percent": _rate(total, capital),
        }

    entries = sorted((entry(r) for r in rows), key=lambda e: e["total_tax"])
    base = {"scope": scope_label, "tax_year": tax_year, "capital": round(capital), "count": len(entries)}
    if len(entries) == 1:
        return base | {"result": entries[0]}
    taxes = [e["total_tax"] for e in entries]
    return base | {
        "statistics": {"min": taxes[0], "median": round(statistics.median(taxes)), "max": taxes[-1]},
        "cheapest": entries[:top],
        "most_expensive": entries[-top:][::-1],
    }


@mcp.tool()
def get_tax_years() -> dict[str, Any]:
    """Report which tax years each ESTV calculator currently covers."""
    return {
        "data_version": client.tax_version(),
        "calculators": {
            name: {
                "min_year": (rng := client.tax_year_range(cid))["MinYear"],
                "max_year": rng["MaxYear"],
            }
            for name, cid in CALCULATOR.items()
        },
        "source": "https://swisstaxcalculator.estv.admin.ch/",
    }


# --------------------------------------------------------------------------
# relocation
# --------------------------------------------------------------------------


# The geo endpoint caps its answer; hitting the cap means the radius clipped.
GEO_RESULT_CAP = 200


@mcp.tool()
def find_cheapest_nearby(
    latitude: Annotated[
        float, Field(description="WGS84 latitude of the reference point, e.g. the office.", ge=45.0, le=48.0)
    ],
    longitude: Annotated[float, Field(description="WGS84 longitude of the reference point.", ge=5.0, le=11.0)],
    radius_km: Annotated[float, Field(description="Search radius in kilometres.", gt=0, le=100)],
    income1: Annotated[float, Field(description="Annual income of person 1, CHF.", ge=0)],
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    income_type1: IncomeType = "employed",
    income2: Annotated[float, Field(description="Annual income of the spouse, CHF.", ge=0)] = 0,
    income_type2: IncomeType = "employed",
    age1: int = 40,
    age2: int | None = None,
    children_ages: list[int] | None = None,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    wealth: Annotated[float, Field(description="Net taxable wealth, CHF.", ge=0)] = 0,
    top: Annotated[
        int, Field(description="How many municipalities to return per end of the ranking.", ge=1, le=100)
    ] = 10,
    language: Lang = "de",
) -> dict[str, Any]:
    """Rank municipalities within a radius of a point by tax burden.

    The relocation question people actually ask: given that I have to stay
    within commuting distance of somewhere, where is the cheapest place to
    live? Pass the coordinates of the office or station and a radius.
    """
    _check_year(tax_year)
    nearby = client.search_location_geo(latitude, longitude, radius_km, tax_year, LANGUAGE[language])
    if not nearby:
        raise EstvError(
            f"no Swiss tax location found within {radius_km} km of ({latitude}, {longitude}). "
            "Check the coordinates are in Switzerland and widen the radius."
        )

    # Geo answers at postal-code granularity but the comparison endpoint works
    # per canton at municipality granularity, so join the two on BfsID.
    wanted_bfs = {n["BfsID"] for n in nearby}
    cantons = sorted({n["Canton"] for n in nearby if n["Canton"] in CANTON_GROUP})
    payload = _person_payload(
        tax_year=tax_year,
        relationship=relationship,
        confession1=confession1,
        confession2=confession2,
        children_ages=children_ages,
        age1=age1,
        income1=income1,
        income_type1=income_type1,
        age2=age2,
        income2=income2,
        income_type2=income_type2,
        wealth=wealth,
    )

    rows: list[dict] = []
    for canton in cantons:
        rows.extend(client.many_simple_taxes(payload | {"TaxGroupID": CANTON_GROUP[canton]}))

    seen: set[int] = set()
    in_radius: list[dict] = []
    for row in sorted(rows, key=lambda r: r["TotalTax"]):
        bfs = row["Location"]["BfsID"]
        if bfs in wanted_bfs and bfs not in seen:
            seen.add(bfs)
            in_radius.append(row)

    result = _rank(in_radius, top, None)
    out = {
        "centre": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km},
        "tax_year": tax_year,
        "cantons_in_radius": cantons,
        "municipalities_priced": len(in_radius),
        **result,
    }
    if len(nearby) >= GEO_RESULT_CAP:
        out["warning"] = (
            f"the location search returned its maximum of {GEO_RESULT_CAP} results, so the "
            "radius was clipped and some municipalities near the edge are missing. "
            "Use a smaller radius for complete coverage."
        )
    return out


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------


def _capital_tax(
    *,
    location_id: int,
    capital: float,
    tax_year: int,
    age: int,
    relationship: str,
    confession1: str,
    confession2: str | None,
    gender: str,
    number_of_children: int,
) -> int:
    joint = relationship in ("married", "registered_partnership")
    rows = client.many_capital_taxes(
        {
            "SimKey": None,
            "TaxYear": tax_year,
            "TaxGroupID": location_id,
            "Relationship": RELATIONSHIP[relationship],
            "Confession1": CONFESSION[confession1],
            "Confession2": CONFESSION[confession2 or "none"] if joint else 0,
            "NumberOfChildren": int(number_of_children),
            "Gender": GENDER[gender],
            "AgeAtPayment": int(age),
            "Capital": round(capital),
        }
    )
    r = rows[0]
    return r.get("TaxFed", 0) + r.get("TaxCanton", 0) + r.get("TaxCity", 0) + r.get("TaxChurch", 0)


@mcp.tool()
def plan_capital_withdrawals(
    location: Annotated[str | int, Field(description="Tax location id, postal code or municipality name.")],
    total_capital: Annotated[float, Field(description="Total pillar 2 / pillar 3a capital to withdraw, CHF.", gt=0)],
    first_year: Annotated[int, Field(description="Calendar year of the first withdrawal.")],
    age_at_first_withdrawal: Annotated[
        int, Field(description="Age of the beneficiary in the first withdrawal year.", ge=0, le=120)
    ],
    max_tranches: Annotated[int, Field(description="Largest number of tranches to evaluate.", ge=1, le=10)] = 5,
    years_between: Annotated[int, Field(description="Calendar years between consecutive tranches.", ge=1, le=10)] = 1,
    tranches: Annotated[
        list[dict[str, float]] | None,
        Field(description="Price a specific plan instead of searching, e.g. [{'year': 2030, 'amount': 200000}, ...]."),
    ] = None,
    relationship: Relationship = "single",
    gender: Literal["male", "female"] = "male",
    number_of_children: int = 0,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    language: Lang = "de",
) -> dict[str, Any]:
    """Find the cheapest way to split a lump-sum withdrawal across tax years.

    Capital payouts are taxed on a steeply progressive separate scale, so
    spreading a pension pot over several calendar years can save a large
    amount. Splitting within one year saves nothing: all payouts received in
    the same calendar year are added together before the rate is applied, and
    this tool models that. Read the `caveats` in the result before acting.
    """
    loc = _resolve_location(location, min(first_year, _year_range("capital_payment")[1]), language)
    location_id = loc["TaxLocationID"]
    lo, hi = _year_range("capital_payment")

    person = {
        "location_id": location_id,
        "relationship": relationship,
        "confession1": confession1,
        "confession2": confession2,
        "gender": gender,
        "number_of_children": number_of_children,
    }
    years_priced_with_proxy: set[int] = set()

    def price(plan: list[tuple[int, float]]) -> dict[str, Any]:
        # Same-year payouts aggregate before the rate applies; that is the rule
        # that makes intra-year splitting pointless, so apply it first.
        merged: dict[int, float] = {}
        for year, amount in plan:
            merged[int(year)] = merged.get(int(year), 0.0) + float(amount)
        legs = []
        total = 0
        for year in sorted(merged):
            priced_year = min(max(year, lo), hi)
            if priced_year != year:
                years_priced_with_proxy.add(year)
            age = age_at_first_withdrawal + (year - first_year)
            tax = _capital_tax(capital=merged[year], tax_year=priced_year, age=age, **person)
            total += tax
            legs.append(
                {
                    "year": year,
                    "amount": round(merged[year]),
                    "age": age,
                    "tax": tax,
                    "priced_with_tax_year": priced_year,
                }
            )
        return {
            "tranches": len(legs),
            "total_tax": total,
            "effective_rate_percent": _rate(total, sum(merged.values())),
            "schedule": legs,
        }

    if tranches:
        plans = [price([(int(t["year"]), float(t["amount"])) for t in tranches])]
        baseline = price([(first_year, total_capital)])
        considered = "the supplied schedule, against a single payout"
    else:
        plans = [
            price([(first_year + i * years_between, total_capital / n) for i in range(n)])
            for n in range(1, max_tranches + 1)
        ]
        baseline = plans[0]
        considered = f"1 to {max_tranches} equal tranches, {years_between} year(s) apart"

    best = min(plans, key=lambda p: p["total_tax"])
    for p in plans:
        p["saving_vs_single_payout"] = baseline["total_tax"] - p["total_tax"]

    caveats = [
        "All payouts in the same calendar year are aggregated before the rate is "
        "applied. This tool applies that rule, so splitting within a year shows no saving.",
        "At federal level a spouse's payouts in the same year are aggregated with "
        "yours. This tool prices one person only, so a couple withdrawing in the "
        "same year will pay more than shown.",
        "Pension fund regulations, and the rules on how many partial withdrawals "
        "they permit, are not modelled. Check what your scheme actually allows.",
    ]
    if years_priced_with_proxy:
        caveats.append(
            f"ESTV only publishes scales for {lo}-{hi}. Year(s) "
            f"{sorted(years_priced_with_proxy)} were priced using the {hi} scales, so "
            "those figures are an indication, not a forecast."
        )

    return {
        "location": {
            "tax_location_id": location_id,
            "municipality": loc.get("BfsName") or loc.get("City"),
            "canton": loc.get("Canton"),
        },
        "total_capital": round(total_capital),
        "considered": considered,
        "best_plan": best,
        "max_saving": baseline["total_tax"] - best["total_tax"],
        "plans": plans,
        "caveats": caveats,
    }


@mcp.tool()
def deduction_value(
    location: Annotated[str | int, Field(description="Tax location id, postal code or municipality name.")],
    income1: Annotated[float, Field(description="Annual income of person 1, CHF.", ge=0)],
    deduction_id: Annotated[str, Field(description="Which budget line to sweep, from list_deductions.")] = "PRAEMIEN3A",
    amounts: Annotated[
        list[float] | None,
        Field(description="Deduction amounts to price. Defaults to five steps from 0 to max_amount."),
    ] = None,
    max_amount: Annotated[float, Field(description="Upper end of the default sweep, CHF.", gt=0)] = 10000,
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    income_type1: IncomeType = "employed",
    income2: Annotated[float, Field(description="Annual income of the spouse, CHF.", ge=0)] = 0,
    income_type2: IncomeType = "employed",
    age1: int = 40,
    age2: int | None = None,
    children_ages: list[int] | None = None,
    confession1: Confession = "none",
    confession2: Confession | None = None,
    wealth: Annotated[float, Field(description="Net taxable wealth, CHF.", ge=0)] = 0,
    language: Lang = "de",
) -> dict[str, Any]:
    """Measure what a deduction is actually worth to this household.

    Sweeps a budget line (pillar 3a by default) and reports the tax saved at
    each level, plus the saving on each additional franc. Because rates are
    progressive the last franc of a deduction is worth more than the first,
    and the return flattens once a bracket boundary is crossed.
    """
    _check_year(tax_year)
    steps = sorted({round(a) for a in (amounts if amounts else [max_amount * i / 4 for i in range(5)])})
    if steps[0] != 0:
        steps.insert(0, 0)

    common = {
        "location": location,
        "tax_year": tax_year,
        "relationship": relationship,
        "income_type1": income_type1,
        "income2": income2,
        "income_type2": income_type2,
        "age1": age1,
        "age2": age2,
        "children_ages": children_ages,
        "confession1": confession1,
        "confession2": confession2,
        "wealth": wealth,
        "language": language,
        "include_breakdown": False,
    }

    rows = []
    previous: dict[str, Any] | None = None
    for amount in steps:
        r = calculate_tax(income1=income1, deductions={deduction_id: amount}, **common)
        entry = {
            "amount": amount,
            "total_tax": r["total_tax"],
            "saved_vs_zero": rows[0]["total_tax"] - r["total_tax"] if rows else 0,
        }
        if previous is not None:
            d_amount = amount - previous["amount"]
            entry["saving_on_this_step"] = previous["total_tax"] - r["total_tax"]
            entry["saving_per_franc_percent"] = (
                round(100 * entry["saving_on_this_step"] / d_amount, 2) if d_amount else None
            )
        rows.append(entry)
        previous = {"amount": amount, "total_tax": r["total_tax"]}

    return {
        "location": location,
        "tax_year": tax_year,
        "deduction_id": deduction_id,
        "note": (
            "saving_per_franc_percent is the share of each additional franc of deduction that comes back as tax saved."
        ),
        "steps": rows,
        "total_saving_at_max": rows[-1]["saved_vs_zero"],
    }


# --------------------------------------------------------------------------
# inheritance, companies, scales
# --------------------------------------------------------------------------


@mcp.tool()
def calculate_inheritance_tax(
    location: Annotated[
        str | int,
        Field(description="Tax location id, postal code, municipality name, canton code, 'capitals' or 'switzerland'."),
    ],
    amount: Annotated[float, Field(description="Value of the estate share or gift, CHF.", ge=0)],
    beneficiary: Annotated[
        str | None,
        Field(
            description="Who receives it, e.g. 'child', 'spouse', 'sibling', 'cohabiting_partner', 'unrelated'. Omit to list every relationship."
        ),
    ] = None,
    is_gift: Annotated[bool, Field(description="True for a lifetime gift, False for an inheritance.")] = False,
    tax_year: int = DEFAULT_YEAR,
    top: Annotated[int, Field(description="When ranking locations, how many to return per end.", ge=1, le=100)] = 10,
    language: Lang = "de",
) -> dict[str, Any]:
    """Inheritance and gift tax, which is cantonal and varies enormously.

    Spouses and direct descendants are exempt in most cantons while unrelated
    beneficiaries and unmarried partners can pay a quarter of the estate, so
    the beneficiary relationship matters more than the amount. Omit
    `beneficiary` to see every relationship for one place, or pass a canton
    code, 'capitals' or 'switzerland' to rank places for one relationship.
    """
    _check_year(tax_year, "inheritance")
    group_id, scope_label = _resolve_group(str(location), tax_year, language)

    group_pid = (0, 0)
    if beneficiary is not None:
        key = beneficiary.strip().lower()
        if key not in BENEFICIARY:
            raise EstvError(f"unknown beneficiary {beneficiary!r}; expected one of: {', '.join(sorted(BENEFICIARY))}")
        group_pid = BENEFICIARY[key]

    rows = client.many_inheritance_taxes(
        {
            "SimKey": None,
            "TaxYear": tax_year,
            "TaxGroupID": group_id,
            "OnlyGroupID": group_pid[0],
            "OnlyPersonID": group_pid[1],
            "Donation": bool(is_gift),
            "Amount": round(amount),
        }
    )

    def entry(r: dict) -> dict:
        loc = r["Location"]
        return {
            "beneficiary_code": r["Person"],
            "municipality": loc["BfsName"] or loc["City"],
            "canton": loc["Canton"],
            "tax_location_id": loc["TaxLocationID"],
            "total_tax": r["TaxTotal"],
            "cantonal": r["TaxCanton"],
            "municipal": r["TaxCity"],
            "allowance": r["Deduction"],
            "minimum_years_of_relationship": r["MinYears"],
            "effective_rate_percent": _rate(r["TaxTotal"], amount),
        }

    base = {
        "scope": scope_label,
        "tax_year": tax_year,
        "amount": round(amount),
        "kind": "gift" if is_gift else "inheritance",
        "count": len(rows),
    }

    if beneficiary is None:
        return base | {
            "beneficiary": "all relationships",
            "note": "One row per beneficiary relationship for this location, cheapest first.",
            "results": sorted((entry(r) for r in rows), key=lambda e: e["total_tax"]),
        }

    entries = sorted((entry(r) for r in rows), key=lambda e: e["total_tax"])
    if len(entries) == 1:
        return base | {"beneficiary": beneficiary, "result": entries[0]}
    taxes = [e["total_tax"] for e in entries]
    return base | {
        "beneficiary": beneficiary,
        "statistics": {"min": taxes[0], "median": round(statistics.median(taxes)), "max": taxes[-1]},
        "cheapest": entries[:top],
        "most_expensive": entries[-top:][::-1],
    }


@mcp.tool()
def calculate_company_tax(
    location: Annotated[
        str | int,
        Field(description="Tax location id, postal code, municipality name, canton code, 'capitals' or 'switzerland'."),
    ],
    taxable_profit: Annotated[float, Field(description="Taxable profit, CHF.", ge=0)],
    taxable_capital: Annotated[float, Field(description="Taxable capital (equity), CHF.", ge=0)],
    share_capital: Annotated[
        float | None, Field(description="Nominal share capital, CHF. Defaults to taxable_capital.")
    ] = None,
    profit_before_taxes: Annotated[
        bool, Field(description="True if taxable_profit is stated before tax is deducted.")
    ] = True,
    taxable_profit_federal: Annotated[
        float | None, Field(description="Federal taxable profit if it differs from the cantonal figure.")
    ] = None,
    participation_net_profit: Annotated[
        float, Field(description="Net profit from qualifying participations, for participation relief.", ge=0)
    ] = 0,
    patent_box_relief: Annotated[
        float, Field(description="Combined patent box, R&D and equity-interest relief, CHF.", ge=0)
    ] = 0,
    total_assets: Annotated[
        float, Field(description="Balance sheet total, CHF. Some cantons need it for the capital tax.", ge=0)
    ] = 0,
    tax_year: int = DEFAULT_YEAR,
    top: Annotated[int, Field(description="When ranking locations, how many to return per end.", ge=1, le=100)] = 10,
    language: Lang = "de",
) -> dict[str, Any]:
    """Profit and capital tax for a company (GmbH, AG or similar).

    Pass a canton code, 'capitals' or 'switzerland' as `location` to rank
    places, which is the usual reason to ask: cantonal profit tax rates for
    legal entities differ by a factor of roughly two across Switzerland.
    """
    _check_year(tax_year, "legal_entity")
    group_id, scope_label = _resolve_group(str(location), tax_year, language)
    capital = share_capital if share_capital is not None else taxable_capital

    rows = client.many_legal_entity_taxes(
        {
            "SimKey": None,
            "TaxYear": tax_year,
            "TaxGroupID": group_id,
            "ProfitBeforeTaxes": bool(profit_before_taxes),
            "TaxableProfitConf": round(taxable_profit if taxable_profit_federal is None else taxable_profit_federal),
            "TaxableProfitCanton": round(taxable_profit),
            "TaxableCapital": round(taxable_capital),
            "ShareCapital": round(capital),
            "NetProfitParticipationConf": round(participation_net_profit),
            "NetProfitParticipationCanton": round(participation_net_profit),
            "TotalPatentBox": round(patent_box_relief),
            "TotalAssets": round(total_assets),
            # The backend accepts both spellings; the misspelling is theirs.
            "TotalBeneficiaryParticipation": 0,
            "TotalBenificiaryParticipation": 0,
        }
    )

    def entry(r: dict) -> dict:
        loc = r["Location"]
        profit_tax = sum(r.get(k, 0) for k in ("ProfitTaxFed", "ProfitTaxCanton", "ProfitTaxCity", "ProfitTaxChurch"))
        capital_tax = sum(r.get(k, 0) for k in ("CapitalTaxCanton", "CapitalTaxCity", "CapitalTaxChurch"))
        total = profit_tax + capital_tax + r.get("MinimalTaxesCanton", 0)
        return {
            "municipality": loc["BfsName"] or loc["City"],
            "canton": loc["Canton"],
            "tax_location_id": loc["TaxLocationID"],
            "total_tax": total,
            "profit_tax": profit_tax,
            "capital_tax": capital_tax,
            "by_level": {
                "federal": r.get("ProfitTaxFed"),
                "cantonal": r.get("ProfitTaxCanton", 0) + r.get("CapitalTaxCanton", 0),
                "municipal": r.get("ProfitTaxCity", 0) + r.get("CapitalTaxCity", 0),
            },
            "effective_rate_on_profit_percent": _rate(total, taxable_profit),
        }

    entries = sorted((entry(r) for r in rows), key=lambda e: e["total_tax"])
    base = {
        "scope": scope_label,
        "tax_year": tax_year,
        "taxable_profit": round(taxable_profit),
        "taxable_capital": round(taxable_capital),
        "count": len(entries),
    }
    if len(entries) == 1:
        return base | {"result": entries[0]}
    taxes = [e["total_tax"] for e in entries]
    return base | {
        "statistics": {"min": taxes[0], "median": round(statistics.median(taxes)), "max": taxes[-1]},
        "cheapest": entries[:top],
        "most_expensive": entries[-top:][::-1],
    }


SENTINEL_WIDTH = 99_999_999

# ZUERICH and BUND are bracket tables we can reproduce exactly. FORMEL and
# FREIBURG encode the scale as an expression; guessing at them produced
# numbers that were wrong by nearly half, so they are refused outright.
SUPPORTED_TABLE_TYPES = {"ZUERICH", "BUND", "FLATTAX"}


def _walk_scale(table: list[dict], income: float, table_type: str) -> dict[str, Any]:
    """Reconstruct the simple tax from a published bracket table.

    The two layouts share a field set but not their meaning, which is what
    `TableType` distinguishes:

    - ZUERICH style: `Amount` is the width of a band, `Percent` its marginal
      rate, and tax accumulates across bands from zero.
    - BUND style: `Amount` is the lower threshold of a band, `Taxes` the tax
      already due at that threshold, and `Percent` the rate on income above it.

    Both are verified to reproduce the API's own simple-tax figures exactly.
    """
    if table_type not in SUPPORTED_TABLE_TYPES or any(row.get("Formula") for row in table):
        raise EstvError(
            f"this canton publishes its scale in the {table_type!r} layout, which is a "
            "formula rather than a reproducible bracket table, so it cannot be shown as "
            "a ladder. Use calculate_tax for the amount payable."
        )

    ladder: list[dict] = []
    current: dict | None = None

    if table_type == "FLATTAX" or len(table) == 1:
        rate = table[-1]["Percent"]
        band = {"from": 0, "to": None, "rate_percent": rate, "income_in_band": round(income)}
        return {"simple_tax": round(income * rate / 100, 2), "current_band": band, "ladder": [band], "flat": True}

    if table_type == "BUND":
        tax = 0.0
        for i, row in enumerate(table):
            lower = row["Amount"]
            upper = table[i + 1]["Amount"] if i + 1 < len(table) else None
            band = {
                "from": round(lower),
                "to": None if upper is None else round(upper),
                "rate_percent": row["Percent"],
                "income_in_band": 0,
            }
            if income >= lower and (upper is None or income < upper):
                tax = row["Taxes"] + (income - lower) * row["Percent"] / 100
                band["income_in_band"] = round(income - lower)
                current = band
            ladder.append(band)
        if current is None:  # income below the first threshold
            current = ladder[0]
        return {"simple_tax": round(tax, 2), "current_band": current, "ladder": ladder}

    tax = 0.0
    lower = 0.0
    for row in table:
        width = row["Amount"]
        upper = lower + width
        taken = min(max(0.0, income - lower), width)
        tax += taken * row["Percent"] / 100
        band = {
            "from": round(lower),
            "to": None if width >= SENTINEL_WIDTH else round(upper),
            "rate_percent": row["Percent"],
            "income_in_band": round(taken),
        }
        ladder.append(band)
        if current is None and (income <= upper or width >= SENTINEL_WIDTH):
            current = band
        lower = upper
    return {"simple_tax": round(tax, 2), "current_band": current or ladder[-1], "ladder": ladder}


@mcp.tool()
def explain_tax_brackets(
    location: Annotated[str | int, Field(description="Tax location id, postal code or municipality name.")],
    taxable_income: Annotated[float, Field(description="Taxable income to locate in the scale, CHF.", ge=0)],
    tax_year: int = DEFAULT_YEAR,
    relationship: Relationship = "single",
    has_children: Annotated[
        bool,
        Field(
            description="Whether the household has dependent children, which selects the married/family scale in most cantons."
        ),
    ] = False,
    target: Annotated[Literal["cantonal", "federal"], Field(description="Which scale to explain.")] = "cantonal",
    language: Lang = "de",
) -> dict[str, Any]:
    """Show the statutory rate ladder and where an income sits in it.

    Explains the number rather than just producing it: which bracket the
    household is in, the marginal rate there, and how far the next threshold
    is. The cantonal figure is the simple tax (einfache Staatssteuer), which
    the canton and municipality then multiply by their own rates.

    The tax amount always comes from ESTV itself. The ladder is rebuilt from
    the published scale, which a few cantons express as a formula rather than
    a table; there the amount is still exact and `ladder` is simply absent.
    """
    _check_year(tax_year)
    loc = _resolve_location(location, tax_year, language)
    if "BfsID" not in loc:
        # A raw tax location id was supplied; resolve it to get canton and BFS.
        found = client.search_location(str(loc["TaxLocationID"])[:4], tax_year, LANGUAGE[language])
        match = next((f for f in found if f["TaxLocationID"] == loc["TaxLocationID"]), None)
        if match is None:
            raise EstvError("pass a postal code or municipality name so the canton can be determined.")
        loc = match

    canton = loc["Canton"]
    wants_family_scale = relationship in ("married", "registered_partnership") or has_children
    want_target = "KANTON" if target == "cantonal" else "BUND"

    # The authoritative amount, straight from ESTV.
    authoritative = client.simple_taxes(
        {
            "SimKey": None,
            "TaxYear": tax_year,
            "TaxLocationID": loc["TaxLocationID"],
            "Relationship": RELATIONSHIP["married"] if wants_family_scale else RELATIONSHIP["single"],
            "Confession1": CONFESSION["none"],
            "Confession2": CONFESSION["none"] if wants_family_scale else 0,
            "Children": [],
            "TaxableIncomeCanton": round(taxable_income),
            "TaxableIncomeFed": round(taxable_income),
            "TaxableFortune": 0,
        }
    )
    simple_tax = authoritative["IncomeSimpleTaxCanton" if target == "cantonal" else "IncomeSimpleTaxFed"]

    out: dict[str, Any] = {
        "location": {
            "municipality": loc.get("BfsName") or loc.get("City"),
            "canton": canton,
            "bfs_id": loc["BfsID"],
        },
        "tax_year": tax_year,
        "target": target,
        "taxable_income": round(taxable_income),
        "simple_tax": simple_tax,
        "note": (
            "This is the simple tax; the canton and municipality each apply their own "
            "multiplier on top, so the amount actually payable is higher. Use "
            "calculate_tax for the payable figure."
            if target == "cantonal"
            else "The federal scale is applied directly, with no multiplier."
        ),
    }

    try:
        scales = client.export_tax_scales(tax_year, CANTON_GROUP[canton], LANGUAGE[language])
        published = [
            s
            for s in scales
            if s["Location"]["BfsID"] == loc["BfsID"]
            and s["TaxType"] == "EINKOMMENSSTEUER"
            and s["Target"] == want_target
        ]
        if not published:
            raise EstvError(f"no {target} income tax scale published for this municipality in {tax_year}.")

        # Cantons split into two camps: separate single/family tables, or one
        # shared table plus a splitting divisor applied to family income.
        by_group = [s for s in published if ("VERHEIRATET" in s["Group"]) == wants_family_scale]
        scale = (by_group or published)[0]
        splitting = float(scale.get("Splitting") or 0)
        divisor = splitting if (splitting > 0 and wants_family_scale) else 1.0

        walk = _walk_scale(scale["Table"], taxable_income / divisor, scale["TableType"])
        band = walk["current_band"]
        out |= {
            "scale": {
                "type": scale["TableType"],
                "applies_to": scale["Group"],
                "splitting_divisor": splitting or None,
                "splitting_applied": divisor != 1.0,
            },
            "income_taxed_at_scale": round(taxable_income / divisor),
            "current_band": band,
            "amount_to_next_band": (
                None if band["to"] is None else round(band["to"] * divisor) - round(taxable_income)
            ),
            "ladder": walk["ladder"],
        }
        # Splitting cantons apply their own rounding on top of the published
        # table, so the rebuilt figure can drift by a franc or two. Say so
        # rather than let the ladder imply a precision it does not have.
        rebuilt = round(walk["simple_tax"] * divisor, 2)
        if abs(rebuilt - simple_tax) > 1:
            out["ladder_note"] = (
                f"Rebuilding the scale gives CHF {rebuilt}, against ESTV's CHF {simple_tax}. "
                "This canton applies rounding the published table does not capture, so treat "
                "the ladder as the shape of the scale and simple_tax as the amount."
            )
    except EstvError as exc:
        out["ladder_unavailable"] = str(exc)

    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
