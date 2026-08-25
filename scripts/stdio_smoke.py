"""Drive the packaged server over real MCP stdio and assert it answers.

The unit tests call the tool functions directly; this checks the transport,
the entry point and the generated tool schemas actually work end to end.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "find_location",
    "list_deductions",
    "calculate_tax",
    "calculate_tax_from_taxable_amounts",
    "compare_locations",
    "calculate_capital_payment_tax",
    "get_tax_years",
    "find_cheapest_nearby",
    "plan_capital_withdrawals",
    "deduction_value",
    "calculate_inheritance_tax",
    "calculate_company_tax",
    "explain_tax_brackets",
}


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "estv_mcp.server"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        print(f"connected to {init.serverInfo.name}")

        names = {t.name for t in (await session.list_tools()).tools}
        missing = EXPECTED_TOOLS - names
        if missing:
            print(f"FAIL: missing tools {sorted(missing)}")
            return 1
        print(f"exposes {len(names)} tools")

        res = await session.call_tool(
            "calculate_tax",
            {"location": "8001", "income1": 120000, "tax_year": 2025, "include_breakdown": False},
        )
        if res.isError:
            print(f"FAIL: calculate_tax returned an error: {res.content}")
            return 1
        data = res.structuredContent or {}
        if data.get("location", {}).get("canton") != "ZH" or not data.get("total_tax"):
            print(f"FAIL: unexpected payload {data}")
            return 1
        print(f"calculate_tax -> CHF {data['total_tax']} in {data['location']['municipality']}")

    print("stdio smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
