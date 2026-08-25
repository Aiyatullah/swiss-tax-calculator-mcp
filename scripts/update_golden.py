"""Regenerate tests/golden.json from the live ESTV API.

Run this only when a diff has been understood and accepted. The whole point of
the fixture is that it fails when upstream numbers move, so silently rewriting
it defeats the check.

    uv run python scripts/update_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import estv_mcp.server as server  # noqa: E402
from golden_cases import CASES, YEAR, extract  # noqa: E402

OUT = ROOT / "tests" / "golden.json"


def main() -> int:
    record = {
        "tax_year": YEAR,
        "data_version": server.client.tax_version(),
        "cases": {},
    }
    for name, tool, kwargs, paths in CASES:
        payload = getattr(server, tool)(**kwargs)
        record["cases"][name] = {
            "tool": tool,
            "values": {path: extract(payload, path) for path in paths},
        }
        print(f"  recorded {name} ({tool})")

    previous = json.loads(OUT.read_text()) if OUT.exists() else None
    OUT.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")

    if previous is None:
        print(f"\nwrote {OUT} with {len(record['cases'])} cases")
        return 0

    changed = [
        f"  {name}.{path}: {old} -> {record['cases'][name]['values'][path]}"
        for name, case in previous["cases"].items()
        if name in record["cases"]
        for path, old in case["values"].items()
        if record["cases"][name]["values"].get(path) != old
    ]
    if previous.get("data_version") != record["data_version"]:
        changed.insert(0, f"  data_version: {previous.get('data_version')} -> {record['data_version']}")
    print(f"\nwrote {OUT}")
    print("changed values:" if changed else "no values changed")
    for line in changed:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
