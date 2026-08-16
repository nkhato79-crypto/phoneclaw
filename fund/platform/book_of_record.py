#!/usr/bin/env python3
"""Turn the live MT5 account into the fund's book of record.

The first risk assessment (fund/risk/2026-08-14-2022-rate-shock-stress.md) failed
as *unmeasurable* — no positions, no NAV, no exposure. Section 6 of it lists the
minimum viable inputs: a position-level file, margin methodology, and a cash
ladder. This module produces the first and most of the third from the broker.

Output is deliberately platform-neutral. The agents read this JSON, never the MT5
API, so swapping brokers later changes this file and nothing else.

    python3 fund/platform/book_of_record.py            # write today's snapshot
    python3 fund/platform/book_of_record.py --print    # stdout, no write

Writes fund/book/positions-YYYY-MM-DD.json and refreshes fund/book/latest.json,
which is the path every agent should read.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mt5_client import MT5Client, MT5Error  # noqa: E402
from settings import load_settings  # noqa: E402

SCHEMA_VERSION = 1


def build_book(account: dict[str, Any], positions: list[dict[str, Any]],
               realised_today: float) -> dict[str, Any]:
    """Assemble the snapshot. Pure function — testable without a terminal."""
    equity = account["equity"] or 0.0
    longs = [p for p in positions if p["direction"] == "long"]
    shorts = [p for p in positions if p["direction"] == "short"]

    long_notional = sum(p["notional"] for p in longs)
    short_notional = sum(p["notional"] for p in shorts)
    gross = long_notional + short_notional
    net = long_notional - short_notional
    unrealised = sum(p["profit"] for p in positions)

    by_symbol: dict[str, dict[str, Any]] = {}
    for p in positions:
        s = by_symbol.setdefault(p["symbol"], {
            "symbol": p["symbol"], "net_volume": 0.0, "gross_notional": 0.0,
            "net_notional": 0.0, "unrealised": 0.0, "positions": 0,
        })
        sign = 1 if p["direction"] == "long" else -1
        s["net_volume"] += p["volume"] * sign
        s["gross_notional"] += p["notional"]
        s["net_notional"] += p["notional"] * sign
        s["unrealised"] += p["profit"]
        s["positions"] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "MetaTrader 5",
        "account": {
            "login": account["login"],
            "server": account["server"],
            "currency": account["currency"],
            "mode": "LIVE" if account["is_live"] else "DEMO",
            "leverage": account["leverage"],
        },
        # The cash ladder the treasury position asked for, as far as one broker
        # can answer it. Multi-broker aggregation is a later problem.
        "capital": {
            "balance": account["balance"],
            "equity": equity,
            "margin_used": account["margin"],
            "margin_free": account["margin_free"],
            "margin_level_pct": account["margin_level"],
            "free_margin_fraction": (account["margin_free"] / equity) if equity else None,
        },
        "exposure": {
            "gross_notional": gross,
            "net_notional": net,
            "long_notional": long_notional,
            "short_notional": short_notional,
            "gross_x_equity": (gross / equity) if equity else None,
            "net_x_equity": (net / equity) if equity else None,
        },
        "pnl": {
            "unrealised": unrealised,
            "realised_today": realised_today,
            "day_total": unrealised + realised_today,
            "day_pct_of_equity": ((unrealised + realised_today) / equity) if equity else None,
        },
        "by_symbol": sorted(by_symbol.values(), key=lambda s: -s["gross_notional"]),
        "positions": positions,
        "position_count": len(positions),
        # Named so no downstream reader mistakes this for a complete fund book.
        "not_covered": [
            "instruments held away from this broker",
            "subscriptions, redemptions and capital accounts",
            "accruals, fees and fund-level expenses",
            "counterparty exposure beyond this single broker",
        ],
    }


def write_book(book: dict[str, Any], repo_root: pathlib.Path) -> pathlib.Path:
    out_dir = repo_root / "fund" / "book"
    out_dir.mkdir(parents=True, exist_ok=True)
    day = book["as_of"][:10]
    payload = json.dumps(book, indent=2)
    dated = out_dir / f"positions-{day}.json"
    dated.write_text(payload)
    (out_dir / "latest.json").write_text(payload)
    return dated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="to_stdout",
                    help="print the snapshot instead of writing it")
    ap.add_argument("--config", default="fund/platform/config.yaml")
    args = ap.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    st = load_settings(repo_root / args.config)

    try:
        with MT5Client(**st.mt5_kwargs()) as client:
            account = client.account().to_dict()
            positions = [p.to_dict() for p in client.positions()]
            midnight = dt.datetime.now(dt.timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0)
            realised = client.realised_pnl(midnight)
    except MT5Error as e:
        print(f"could not read the account: {e}", file=sys.stderr)
        return 2

    book = build_book(account, positions, realised)

    if args.to_stdout:
        print(json.dumps(book, indent=2))
        return 0

    path = write_book(book, repo_root)
    eq = book["capital"]["equity"]
    print(f"wrote {path.relative_to(repo_root)} — {book['account']['mode']} account, "
          f"equity {eq:,.2f} {book['account']['currency']}, "
          f"{book['position_count']} positions, "
          f"gross {book['exposure']['gross_x_equity']:.2f}x equity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
