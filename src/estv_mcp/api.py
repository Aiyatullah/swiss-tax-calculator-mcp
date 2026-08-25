"""Thin client for the undocumented ESTV swisstaxcalculator JSON API.

Reverse engineered from the public SPA at
https://swisstaxcalculator.estv.admin.ch/. Every operation is a POST with a
JSON body to ``{BASE}/operation/c3b67379_ESTV/{op}`` and answers with
``{"response": ...}``. No authentication.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "https://swisstaxcalculator.estv.admin.ch/delegate/ost-integration/v1/lg-proxy"
NAMESPACE = "operation/c3b67379_ESTV"

# --- enums lifted from the SPA bundle -------------------------------------

RELATIONSHIP = {
    "single": 1,
    "married": 2,
    "concubinage": 3,
    "registered_partnership": 4,
}

CONFESSION = {
    "reformed": 1,
    "roman_catholic": 2,
    "christ_catholic": 3,
    "none": 4,
    "other": 5,
}

INCOME_TYPE = {
    "employed": 1,  # gross salary, social deductions computed for you
    "self_employed": 2,  # net income from self-employment
    "pensioner": 3,  # retirement income
    "other": 4,  # other / no gainful employment
}

GENDER = {"male": 1, "female": 2}

LANGUAGE = {"de": 1, "fr": 2, "it": 3, "en": 4}

# TaxGroupID space for the multi-location comparison endpoints.
CANTON_GROUP = {
    "AG": 1,
    "AI": 2,
    "AR": 3,
    "BE": 4,
    "BL": 5,
    "BS": 6,
    "FR": 7,
    "GE": 8,
    "GL": 9,
    "GR": 10,
    "JU": 11,
    "LU": 12,
    "NE": 13,
    "NW": 14,
    "OW": 15,
    "SG": 16,
    "SH": 17,
    "SO": 18,
    "SZ": 19,
    "TG": 20,
    "TI": 21,
    "UR": 22,
    "VD": 23,
    "VS": 24,
    "ZG": 25,
    "ZH": 26,
    "LI": 27,
}
GROUP_CAPITALS = 88  # the 26 cantonal capitals
GROUP_SWITZERLAND = 99  # every municipality (~2100)

# Calculator ids for getTaxYearRange.
CALCULATOR = {
    "income_wealth": 1,
    "capital_payment": 2,
    "legal_entity": 3,
    "inheritance": 5,
}

# Beneficiary relationships for the inheritance/gift calculator, mapped to the
# (GroupID, PersonID) pair the API keys them by.
BENEFICIARY = {
    "spouse": (1, 1),
    "registered_partner": (2, 1),
    "cohabiting_partner": (2, 1),
    "fiance": (2, 2),
    "cohabiting_partner_with_child": (2, 4),
    "child": (3, 1),
    "grandchild": (3, 2),
    "orphan": (3, 4),
    "foster_child": (3, 8),
    "stepchild": (3, 32),
    "godchild": (3, 128),
    "parent": (4, 1),
    "foster_parent": (4, 2),
    "step_parent": (4, 4),
    "grandparent": (5, 1),
    "great_grandparent": (5, 8),
    "sibling": (6, 1),
    "step_sibling": (6, 2),
    "uncle_or_aunt": (7, 1),
    "nephew_or_niece": (7, 4),
    "cousin": (7, 32),
    "employee": (8, 2),
    "parent_in_law": (8, 4),
    "child_in_law": (8, 8),
    "foundation": (8, 512),
    "unrelated": (8, 1),
}

# Retried transparently; 500 is excluded because the backend uses it for
# validation failures, where retrying only wastes a round trip.
RETRY_STATUS = {429, 502, 503, 504}


class EstvError(RuntimeError):
    """The upstream API rejected the request."""


def _default_cache_dir() -> Path:
    override = os.environ.get("ESTV_MCP_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "estv-mcp"


class _DiskCache:
    """Tiny content-addressed cache.

    Tax scales for a closed year never change, and a single planning question
    can fan out into dozens of identical sub-queries, so caching is both a
    latency win and the polite thing to do to a federal server.
    """

    def __init__(self, directory: Path, ttl: float) -> None:
        self.directory = directory
        self.ttl = ttl
        self._memory: dict[str, Any] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(operation: str, payload: dict[str, Any]) -> str:
        blob = json.dumps([operation, payload], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> Any:
        # Always hand callers a private copy: server.py mutates budget lines
        # in place to apply deduction overrides, and a shared reference here
        # would let one call's edits leak into every later call that hits
        # the same cache entry.
        with self._lock:
            if key in self._memory:
                return copy.deepcopy(self._memory[key])
        path = self.directory / f"{key}.json"
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        with self._lock:
            self._memory[key] = value
        return copy.deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._memory[key] = value
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.directory / f"{key}.tmp"
            tmp.write_text(json.dumps(value))
            tmp.replace(self.directory / f"{key}.json")
        except OSError:
            pass  # cache is an optimisation; never fail a call over it


class EstvClient:
    def __init__(
        self,
        # Scale exports for large cantons return several MB and can take a
        # while, so the default has to be generous.
        timeout: float = 90.0,
        cache: bool | None = None,
        cache_ttl: float | None = None,
        min_interval: float = 0.05,
        max_attempts: int = 3,
    ) -> None:
        self._client = httpx.Client(
            base_url=f"{BASE}/{NAMESPACE}",
            timeout=timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "swiss-tax-calculator-mcp (+https://github.com/aiyatullah/swiss-tax-calculator-mcp)",
            },
        )
        if cache is None:
            cache = os.environ.get("ESTV_MCP_NO_CACHE", "") not in ("1", "true", "yes")
        if cache_ttl is None:
            cache_ttl = float(os.environ.get("ESTV_MCP_CACHE_TTL", 7 * 24 * 3600))
        self._cache = _DiskCache(_default_cache_dir(), cache_ttl) if cache else None
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._last_call = 0.0
        self._throttle = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def _throttled_post(self, operation: str, body: str) -> httpx.Response:
        with self._throttle:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
        return self._client.post(f"/{operation}", content=body)

    def call(self, operation: str, payload: dict[str, Any], *, cacheable: bool = True) -> Any:
        key = _DiskCache.key(operation, payload) if (self._cache and cacheable) else None
        if key is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return hit

        body = json.dumps(payload)
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._throttled_post(operation, body)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if resp.status_code not in RETRY_STATUS:
                    result = self._unwrap(operation, resp)
                    if key is not None:
                        self._cache.set(key, result)
                    return copy.deepcopy(result) if key is not None else result
                last_error = EstvError(f"{operation} returned HTTP {resp.status_code}")
            if attempt < self._max_attempts - 1:
                time.sleep(0.5 * 2**attempt)

        raise EstvError(f"could not reach the ESTV API after {self._max_attempts} attempts: {last_error}")

    @staticmethod
    def _unwrap(operation: str, resp: httpx.Response) -> Any:
        # The backend answers malformed input with an HTML error page, not JSON.
        try:
            body = resp.json()
        except ValueError:
            raise EstvError(
                f"{operation} rejected the request (HTTP {resp.status_code}). "
                "This usually means a required field is missing or a value is "
                "out of range (check tax_year against get_tax_years, and that "
                "the location id exists)."
            ) from None

        if resp.status_code >= 400:
            raise EstvError(f"{operation} failed (HTTP {resp.status_code}): {body}")
        if "response" not in body:
            raise EstvError(f"{operation} returned an unexpected payload: {body}")
        return body["response"]

    # --- operations -------------------------------------------------------

    def tax_version(self) -> str:
        # Not cached: this is the signal used to detect upstream data changes.
        return self.call("API_getTaxVersion", {}, cacheable=False)

    def tax_year_range(self, calculator: int) -> dict[str, Any]:
        return self.call("API_getTaxYearRange", {"Calculator": calculator})

    def search_location(self, search: str, tax_year: int, language: int = 1) -> list[dict]:
        return self.call(
            "API_searchLocation",
            {"Search": search, "Language": language, "TaxYear": tax_year},
        )

    def tax_budget(self, payload: dict[str, Any]) -> list[dict]:
        return self.call("API_calculateTaxBudget", payload)

    def detailed_taxes(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("API_calculateDetailedTaxes", payload)

    def simple_taxes(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("API_calculateSimpleTaxes", payload)

    def many_simple_taxes(self, payload: dict[str, Any]) -> list[dict]:
        return self.call("API_calculateManySimpleTaxes", payload)

    def many_capital_taxes(self, payload: dict[str, Any]) -> list[dict]:
        return self.call("API_calculateManyCapitalTaxes", payload)

    def search_location_geo(
        self, latitude: float, longitude: float, radius_km: float, tax_year: int, language: int = 1
    ) -> list[dict]:
        return self.call(
            "API_searchLocationGeo",
            {
                "Latitude": latitude,
                "Longitude": longitude,
                "RadiusKm": radius_km,
                "Language": language,
                "TaxYear": tax_year,
            },
        )

    def many_inheritance_taxes(self, payload: dict[str, Any]) -> list[dict]:
        return self.call("API_calculateManyInheritanceTaxes", payload)

    def many_legal_entity_taxes(self, payload: dict[str, Any]) -> list[dict]:
        return self.call("API_calculateManyLegalEntityTaxes", payload)

    def export_tax_scales(self, tax_year: int, tax_group_id: int, language: int = 1) -> list[dict]:
        return self.call(
            "API_exportManyTaxScales",
            {"TaxYear": tax_year, "TaxGroupID": tax_group_id, "Language": language},
        )
