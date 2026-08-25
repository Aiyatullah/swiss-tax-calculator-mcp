"""End-to-end tests against the live ESTV API (no key required)."""

from __future__ import annotations

import pytest

from estv_mcp.api import EstvError
from estv_mcp.server import (
    calculate_capital_payment_tax,
    calculate_company_tax,
    calculate_inheritance_tax,
    calculate_tax,
    calculate_tax_from_taxable_amounts,
    compare_locations,
    deduction_value,
    explain_tax_brackets,
    find_cheapest_nearby,
    find_location,
    get_tax_years,
    list_deductions,
    plan_capital_withdrawals,
)

YEAR = 2025
ZURICH = 800100000


def test_year_coverage():
    years = get_tax_years()
    assert years["calculators"]["income_wealth"]["min_year"] == 2010
    assert years["calculators"]["income_wealth"]["max_year"] >= 2025


def test_find_location_by_zip():
    r = find_location(query="8001", tax_year=YEAR)
    assert r["locations"][0]["tax_location_id"] == ZURICH
    assert r["locations"][0]["canton"] == "ZH"


def test_find_location_no_match():
    assert find_location(query="Atlantis", tax_year=YEAR)["count"] == 0


def test_rejects_year_outside_coverage():
    with pytest.raises(EstvError, match="out of range"):
        find_location(query="8001", tax_year=1999)


def test_single_earner_zurich():
    r = calculate_tax(location="8001", income1=120000, tax_year=YEAR, include_breakdown=False)
    assert r["location"]["canton"] == "ZH"
    lv = r["by_level"]
    assert lv["federal"] > 0 and lv["cantonal"] > 0 and lv["municipal"] > 0
    assert lv["church"] == 0  # confession defaults to none
    assert 10 < r["rates_percent"]["effective_on_gross_income"] < 20
    assert r["total_tax"] == pytest.approx(
        lv["federal"] + lv["cantonal"] + lv["municipal"] + lv["church"] + lv["personal_tax"] - lv["tax_credit"],
        abs=2,
    )


def test_location_id_accepted_directly():
    by_id = calculate_tax(location=ZURICH, income1=120000, tax_year=YEAR, include_breakdown=False)
    by_zip = calculate_tax(location="8001", income1=120000, tax_year=YEAR, include_breakdown=False)
    assert by_id["total_tax"] == by_zip["total_tax"]


def test_confession_adds_church_tax():
    without = calculate_tax(location="8001", income1=120000, tax_year=YEAR, include_breakdown=False)
    with_church = calculate_tax(
        location="8001",
        income1=120000,
        tax_year=YEAR,
        confession1="roman_catholic",
        include_breakdown=False,
    )
    assert with_church["by_level"]["church"] > 0
    assert with_church["total_tax"] > without["total_tax"]


def test_wealth_is_taxed():
    r = calculate_tax(location="8001", income1=120000, wealth=1_000_000, tax_year=YEAR, include_breakdown=False)
    assert r["taxable"]["wealth_cantonal"] == 1_000_000
    assert r["by_object"]["wealth_tax"] > 0


def test_children_reduce_tax():
    single = calculate_tax(location="8001", income1=150000, tax_year=YEAR, include_breakdown=False)
    parent = calculate_tax(
        location="8001", income1=150000, children_ages=[4, 9], tax_year=YEAR, include_breakdown=False
    )
    assert parent["total_tax"] < single["total_tax"]


def test_pillar_3a_reduces_tax():
    kw = {"location": "8001", "income1": 120000, "tax_year": YEAR, "include_breakdown": False}
    base = calculate_tax(**kw)
    with_3a = calculate_tax(**kw, deductions={"PRAEMIEN3A": 7258})
    assert with_3a["deductions_applied"] == {"PRAEMIEN3A": 7258}
    assert with_3a["taxable"]["income_cantonal"] < base["taxable"]["income_cantonal"]
    assert with_3a["total_tax"] < base["total_tax"]


def test_unknown_deduction_id_is_rejected():
    with pytest.raises(EstvError, match="unknown deduction id"):
        calculate_tax(location="8001", income1=100000, tax_year=YEAR, deductions={"BOGUS": 1})


def test_deduction_sheet_shape():
    lines = list_deductions(location="8001", income1=120000, tax_year=YEAR)["lines"]
    by_id = {line["id"]: line for line in lines}
    assert by_id["BRUTTOLOHN_P1"]["default_value"] == 120000
    assert by_id["BRUTTOLOHN_P1"]["editable"] is False
    assert by_id["PRAEMIEN3A"]["editable"] is True
    assert by_id["BEITRAG_AIO_P1"]["section"] == "income"
    assert by_id["PRAEMIEN3A"]["section"] == "deductions"


def test_married_sheet_has_second_person():
    lines = list_deductions(location="8001", income1=120000, income2=80000, relationship="married", tax_year=YEAR)[
        "lines"
    ]
    ids = {line["id"] for line in lines}
    assert {"BRUTTOLOHN_P2", "BEITRAG_BVG_P2"} <= ids


def test_breakdown_is_localized():
    r = calculate_tax(location="8001", income1=120000, tax_year=YEAR, language="fr")
    items = r["breakdown"]["federal_and_cantonal"]
    assert items and all(line["item"] for line in items)
    assert any("Revenu" in line["item"] or "revenu" in line["item"] for line in items)


def test_compare_capitals_ranks_zug_lowest():
    r = compare_locations(income1=150000, scope="capitals", tax_year=YEAR, top=3)
    assert r["count"] == 26
    assert r["cheapest"][0]["canton"] == "ZG"
    assert r["cheapest"][0]["total_tax"] < r["most_expensive"][0]["total_tax"]
    assert r["statistics"]["spread_max_minus_min"] > 0


def test_compare_canton_scope():
    r = compare_locations(income1=150000, scope="ZG", tax_year=YEAR, top=5)
    assert 5 < r["count"] < 20
    assert {e["canton"] for e in r["cheapest"]} == {"ZG"}


def test_compare_switzerland_filtered_by_canton():
    r = compare_locations(income1=150000, scope="switzerland", tax_year=YEAR, only_cantons=["ZH", "SZ"], top=3)
    assert r["count"] > 100
    assert {e["canton"] for e in r["cheapest"]} <= {"ZH", "SZ"}


def test_taxable_amounts_path():
    r = calculate_tax_from_taxable_amounts(
        location="8001",
        taxable_income_cantonal=100000,
        taxable_income_federal=100000,
        taxable_wealth=200000,
        tax_year=YEAR,
    )
    assert r["by_object"]["wealth_tax"] > 0
    assert r["by_level"]["federal"] > 0


def test_capital_payment_single_location():
    r = calculate_capital_payment_tax(location="8001", capital=500000, age_at_payment=65, tax_year=YEAR)
    assert r["count"] == 1
    res = r["result"]
    assert res["total_tax"] == res["federal"] + res["cantonal"] + res["municipal"] + res["church"]
    assert 3 < res["effective_rate_percent"] < 15


def test_capital_payment_ranked():
    r = calculate_capital_payment_tax(location="capitals", capital=500000, age_at_payment=65, tax_year=YEAR, top=3)
    assert r["count"] == 26
    assert r["cheapest"][0]["total_tax"] <= r["statistics"]["median"] <= r["most_expensive"][0]["total_tax"]


# --- relocation -----------------------------------------------------------


def test_nearby_ranks_within_radius():
    r = find_cheapest_nearby(latitude=47.3779, longitude=8.5403, radius_km=10, income1=150000, tax_year=YEAR, top=3)
    assert r["municipalities_priced"] > 10
    assert "ZH" in r["cantons_in_radius"]
    assert r["cheapest"][0]["total_tax"] < r["most_expensive"][0]["total_tax"]


def test_nearby_rejects_point_outside_switzerland():
    with pytest.raises(EstvError, match="no Swiss tax location"):
        find_cheapest_nearby(latitude=45.5, longitude=5.1, radius_km=1, income1=100000, tax_year=YEAR)


def test_nearby_warns_when_geo_search_is_clipped():
    r = find_cheapest_nearby(latitude=47.3779, longitude=8.5403, radius_km=40, income1=150000, tax_year=YEAR)
    assert "warning" in r


# --- withdrawal planning --------------------------------------------------


def test_staggering_beats_a_single_payout():
    r = plan_capital_withdrawals(
        location="8001", total_capital=600000, first_year=2022, age_at_first_withdrawal=62, max_tranches=3
    )
    taxes = [p["total_tax"] for p in r["plans"]]
    assert taxes == sorted(taxes, reverse=True)  # more tranches, less tax
    assert r["max_saving"] > 0
    assert r["best_plan"]["tranches"] == 3


def test_splitting_within_one_year_saves_nothing():
    """The aggregation rule is the whole reason this tool has to exist."""
    single = plan_capital_withdrawals(
        location="8001",
        total_capital=600000,
        first_year=2022,
        age_at_first_withdrawal=62,
        tranches=[{"year": 2022, "amount": 600000}],
    )
    split = plan_capital_withdrawals(
        location="8001",
        total_capital=600000,
        first_year=2022,
        age_at_first_withdrawal=62,
        tranches=[{"year": 2022, "amount": 300000}, {"year": 2022, "amount": 300000}],
    )
    assert split["plans"][0]["total_tax"] == single["plans"][0]["total_tax"]
    assert split["plans"][0]["saving_vs_single_payout"] == 0


def test_future_years_are_flagged_as_proxy_priced():
    r = plan_capital_withdrawals(
        location="8001", total_capital=400000, first_year=2035, age_at_first_withdrawal=65, max_tranches=2
    )
    assert any("indication, not a forecast" in c for c in r["caveats"])
    assert r["plans"][1]["schedule"][0]["priced_with_tax_year"] <= 2026


def test_spousal_aggregation_is_disclosed():
    r = plan_capital_withdrawals(
        location="8001", total_capital=300000, first_year=2022, age_at_first_withdrawal=62, max_tranches=1
    )
    assert any("spouse" in c for c in r["caveats"])


# --- deduction value ------------------------------------------------------


def test_deduction_value_is_monotonic():
    r = deduction_value(location="8001", income1=120000, tax_year=YEAR, max_amount=6000)
    taxes = [s["total_tax"] for s in r["steps"]]
    assert taxes == sorted(taxes, reverse=True)
    assert r["total_saving_at_max"] > 0
    rates = [s["saving_per_franc_percent"] for s in r["steps"][1:]]
    assert all(0 < x < 60 for x in rates)


# --- inheritance ----------------------------------------------------------


def test_inheritance_lists_every_relationship():
    r = calculate_inheritance_tax(location="8001", amount=500000, tax_year=YEAR)
    assert r["count"] == 38
    assert r["results"][0]["total_tax"] == 0  # spouse and children are exempt in ZH


def test_unrelated_beneficiary_pays_far_more_than_a_child():
    child = calculate_inheritance_tax(location="8001", amount=500000, beneficiary="child", tax_year=YEAR)
    other = calculate_inheritance_tax(location="8001", amount=500000, beneficiary="unrelated", tax_year=YEAR)
    assert child["result"]["total_tax"] == 0
    assert other["result"]["total_tax"] > 100000


def test_inheritance_ranks_locations():
    r = calculate_inheritance_tax(location="capitals", amount=500000, beneficiary="sibling", tax_year=YEAR, top=2)
    assert r["count"] == 26
    assert r["statistics"]["min"] <= r["statistics"]["max"]


def test_unknown_beneficiary_is_rejected():
    with pytest.raises(EstvError, match="unknown beneficiary"):
        calculate_inheritance_tax(location="8001", amount=1000, beneficiary="pet_cat", tax_year=YEAR)


# --- company tax ----------------------------------------------------------


def test_company_tax_single_location():
    r = calculate_company_tax(location="8001", taxable_profit=200000, taxable_capital=100000, tax_year=YEAR)
    res = r["result"]
    assert res["profit_tax"] > 0 and res["capital_tax"] > 0
    assert res["total_tax"] == res["profit_tax"] + res["capital_tax"]
    assert 10 < res["effective_rate_on_profit_percent"] < 30


def test_company_tax_ranks_cantons():
    r = calculate_company_tax(location="capitals", taxable_profit=1000000, taxable_capital=500000, tax_year=YEAR, top=3)
    assert r["count"] == 26
    assert r["statistics"]["min"] < r["statistics"]["max"]


# --- bracket explanation --------------------------------------------------


def test_brackets_match_the_api_simple_tax():
    r = explain_tax_brackets(location="8001", taxable_income=100000, tax_year=YEAR)
    api = calculate_tax_from_taxable_amounts(
        location="8001", taxable_income_cantonal=100000, taxable_income_federal=100000, tax_year=YEAR
    )
    # The simple tax is what the multipliers are applied to, so it must be
    # below the payable cantonal + municipal amount.
    assert r["simple_tax"] < api["by_level"]["cantonal"] + api["by_level"]["municipal"]
    assert r["current_band"]["from"] < 100000 <= r["current_band"]["to"]
    assert r["amount_to_next_band"] == r["current_band"]["to"] - 100000


def test_brackets_ladder_is_ordered_and_progressive():
    r = explain_tax_brackets(location="8001", taxable_income=100000, tax_year=YEAR)
    rates = [b["rate_percent"] for b in r["ladder"]]
    assert rates == sorted(rates)
    edges = [b["from"] for b in r["ladder"]]
    assert edges == sorted(edges)


def test_brackets_federal_scale():
    r = explain_tax_brackets(location="8001", taxable_income=100000, tax_year=YEAR, target="federal")
    assert r["simple_tax"] > 0
    assert "no multiplier" in r["note"]


def test_formula_cantons_still_return_the_amount():
    """Basel-Landschaft publishes a formula, so the ladder is unavailable."""
    r = explain_tax_brackets(location="4410", taxable_income=100000, tax_year=YEAR)
    assert r["simple_tax"] > 0
    assert "ladder" not in r
    assert "ladder_unavailable" in r
