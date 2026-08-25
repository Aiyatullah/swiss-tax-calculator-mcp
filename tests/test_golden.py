"""Compare live ESTV output against recorded values.

A failure here is not necessarily a bug in this repo. It means the upstream
calculator changed, and the diff is the thing worth reading. Once the change
is understood, re-record with `uv run python scripts/update_golden.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import estv_mcp.server as server
from estv_mcp.api import EstvClient
from golden_cases import CASES, extract

GOLDEN = Path(__file__).parent / "golden.json"
RECORDED = json.loads(GOLDEN.read_text())


@pytest.fixture(autouse=True)
def _live_client(monkeypatch):
    """Force these tests past the cache.

    A cached answer proves nothing about what ESTV is serving today, and
    detecting upstream drift is the only reason this file exists.
    """
    monkeypatch.setattr(server, "client", EstvClient(cache=False))


def test_data_version_unchanged():
    assert server.client.tax_version() == RECORDED["data_version"], (
        "ESTV published a new API/data version. Review what moved, then re-record."
    )


@pytest.mark.parametrize("name,tool,kwargs,paths", CASES, ids=[c[0] for c in CASES])
def test_golden_case(name, tool, kwargs, paths):
    expected = RECORDED["cases"][name]["values"]
    payload = getattr(server, tool)(**kwargs)
    actual = {path: extract(payload, path) for path in paths}
    assert actual == expected


def test_every_case_is_recorded():
    assert {c[0] for c in CASES} == set(RECORDED["cases"]), (
        "golden.json is out of step with CASES; run scripts/update_golden.py"
    )
