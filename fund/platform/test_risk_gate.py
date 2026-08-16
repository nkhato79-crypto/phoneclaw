#!/usr/bin/env python3
"""Tests for the risk gate.

The gate is the only thing standing between an autonomous agent and the account,
so it is tested independently of MT5 — no terminal, no Windows, no broker. Run it
anywhere:

    python3 fund/platform/test_risk_gate.py

Every test here is a loss the system should refuse to take.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from risk_gate import RiskGate, size_for_risk, load_config  # noqa: E402

BASE = {
    "allow_live": False,
    "symbols": ["XAUUSD"],
    "trading_days": [0, 1, 2, 3, 4],
    "trading_hours_utc": [0, 24],
    "max_gross_exposure": 2.0,
    "max_symbol_exposure": 1.0,
    "max_order_exposure": 0.5,
    "max_open_positions": 4,
    "max_positions_per_symbol": 2,
    "max_risk_per_trade": 0.01,
    "daily_loss_limit": 0.03,
    "max_consecutive_losses": 4,
    "min_margin_level": 300.0,
    "min_free_margin_fraction": 0.30,
}

DEMO = {"login": 1, "server": "X", "currency": "USD", "balance": 100_000,
        "equity": 100_000, "margin": 0, "margin_free": 100_000,
        "margin_level": 0, "leverage": 100, "is_live": False}

# 0.1 lots of gold at 4400 = 0.1 * 100oz * 4400 = 44,000 notional; stop 40 away
# = 400 of risk, which is 0.4% of a 100k account.
ORDER = {"symbol": "XAUUSD", "direction": "long", "volume": 0.1, "price": 4400.0,
         "sl": 4360.0, "contract_size": 100.0}

# Order checks are pinned to a fixed Monday. Left to wall-clock time these tests
# pass or fail depending on the day they are run — on a weekend the trading-day
# check refuses everything, which is correct behaviour and a useless test.
MONDAY = dt.datetime(2026, 8, 17, 10, 0, tzinfo=dt.timezone.utc)


def gate(tmp: pathlib.Path, **overrides) -> RiskGate:
    cfg = dict(BASE)
    cfg.update(overrides)
    cfg["kill_switch_file"] = "KILL"
    return RiskGate(cfg, repo_root=tmp)


def pos(symbol="XAUUSD", direction="long", notional=44_000.0, profit=0.0, ticket=1):
    return {"ticket": ticket, "symbol": symbol, "direction": direction,
            "notional": notional, "profit": profit}


# --------------------------------------------------------------- arming

def test_kill_switch_blocks_orders(tmp):
    g = gate(tmp)
    (tmp / "KILL").write_text("stop")
    d = g.evaluate_order(ORDER, DEMO, [], now=MONDAY)
    assert not d.allow and "kill switch" in d.reasons[0]


def test_kill_switch_flattens_account(tmp):
    g = gate(tmp)
    (tmp / "KILL").write_text("stop")
    d = g.evaluate_account(DEMO, [pos()], realised_today=0.0)
    assert not d.allow and "FLATTEN" in d.actions


def test_live_account_refused_unless_armed(tmp):
    live = dict(DEMO, is_live=True)
    d = gate(tmp).evaluate_account(live, [], realised_today=0.0)
    assert not d.allow and "LIVE" in d.reasons[0] and "HALT" in d.actions

    d2 = gate(tmp, allow_live=True).evaluate_account(live, [], realised_today=0.0)
    assert d2.allow


# ---------------------------------------------------------- loss control

def test_daily_loss_limit_flattens(tmp):
    g = gate(tmp)
    # -2,000 realised and -1,500 open = -3.5% of 100k, past the 3% limit.
    d = g.evaluate_account(DEMO, [pos(profit=-1_500)], realised_today=-2_000)
    assert not d.allow and "FLATTEN" in d.actions and "daily loss" in d.reasons[0]


def test_daily_loss_limit_counts_open_pnl(tmp):
    """Realised alone is inside the limit; the open loss is what breaches it."""
    g = gate(tmp)
    assert g.evaluate_account(DEMO, [], realised_today=-2_000).allow
    assert not g.evaluate_account(DEMO, [pos(profit=-1_500)], realised_today=-2_000).allow


def test_equity_floor(tmp):
    g = gate(tmp, equity_floor=95_000)
    d = g.evaluate_account(dict(DEMO, equity=94_000), [], realised_today=0.0)
    assert not d.allow and "FLATTEN" in d.actions


def test_consecutive_losses_halt(tmp):
    d = gate(tmp).evaluate_account(DEMO, [], realised_today=0.0, consecutive_losses=4)
    assert not d.allow and "HALT" in d.actions


def test_margin_floors(tmp):
    g = gate(tmp)
    thin = dict(DEMO, margin=30_000, margin_level=250.0, margin_free=20_000)
    d = g.evaluate_account(thin, [], realised_today=0.0)
    assert not d.allow
    assert any("margin level" in r for r in d.reasons)
    assert any("free margin" in r for r in d.reasons)


def test_non_positive_equity_fails_closed(tmp):
    d = gate(tmp).evaluate_account(dict(DEMO, equity=0), [], realised_today=0.0)
    assert not d.allow and "HALT" in d.actions


# ----------------------------------------------------------- order checks

def test_allowlist(tmp):
    d = gate(tmp).evaluate_order(dict(ORDER, symbol="EURUSD"), DEMO, [], now=MONDAY)
    assert not d.allow and "allowlist" in d.reasons[0]


def test_missing_fields_fail_closed(tmp):
    d = gate(tmp).evaluate_order(dict(ORDER, sl=None), DEMO, [], now=MONDAY)
    assert not d.allow and "missing required fields" in d.reasons[0]


def test_stop_must_be_on_the_losing_side(tmp):
    g = gate(tmp)
    assert not g.evaluate_order(dict(ORDER, sl=4450.0), DEMO, [], now=MONDAY).allow
    short = dict(ORDER, direction="short", sl=4360.0)
    assert not g.evaluate_order(short, DEMO, [], now=MONDAY).allow
    assert g.evaluate_order(dict(short, sl=4450.0), DEMO, [], now=MONDAY).allow


def test_risk_per_trade_cap(tmp):
    g = gate(tmp)
    assert g.evaluate_order(ORDER, DEMO, [], now=MONDAY).allow           # 0.4% of equity
    wide = dict(ORDER, sl=4200.0)                            # 2,000 = 2%
    d = g.evaluate_order(wide, DEMO, [], now=MONDAY)
    assert not d.allow and any("risk to stop" in r for r in d.reasons)


def test_order_notional_cap(tmp):
    big = dict(ORDER, volume=2.0, sl=4399.0)   # 880k notional on 100k equity
    d = gate(tmp).evaluate_order(big, DEMO, [], now=MONDAY)
    assert not d.allow and any("order notional" in r for r in d.reasons)


def test_symbol_exposure_cap(tmp):
    g = gate(tmp, max_positions_per_symbol=9, max_open_positions=9)
    held = [pos(notional=90_000, ticket=1)]      # 0.9x equity already
    d = g.evaluate_order(ORDER, DEMO, held, now=MONDAY)      # + 0.44x -> 1.34x, cap 1.0x
    assert not d.allow and any("exposure would reach" in r for r in d.reasons)


def test_gross_exposure_cap(tmp):
    g = gate(tmp, max_symbol_exposure=9.0, max_positions_per_symbol=9, max_open_positions=9)
    held = [pos(symbol="XAUUSD", notional=100_000, ticket=1),
            pos(symbol="XAUUSD", direction="short", notional=90_000, ticket=2)]
    d = g.evaluate_order(ORDER, DEMO, held, now=MONDAY)      # 190k + 44k = 2.34x, cap 2.0x
    assert not d.allow and any("gross exposure" in r for r in d.reasons)


def test_position_count_caps(tmp):
    g = gate(tmp, max_open_positions=2)
    d = g.evaluate_order(ORDER, DEMO, [pos(ticket=1), pos(ticket=2)], now=MONDAY)
    assert not d.allow and any("positions open" in r for r in d.reasons)

    g2 = gate(tmp, max_positions_per_symbol=1, max_open_positions=9,
              max_symbol_exposure=9.0)
    d2 = g2.evaluate_order(ORDER, DEMO, [pos(ticket=1)], now=MONDAY)
    assert not d2.allow and any("already in XAUUSD" in r for r in d2.reasons)


def test_trading_window(tmp):
    g = gate(tmp, trading_hours_utc=[6, 20])
    sat = dt.datetime(2026, 8, 15, 10, tzinfo=dt.timezone.utc)   # Saturday
    assert not g.evaluate_order(ORDER, DEMO, [], now=sat).allow

    night = dt.datetime(2026, 8, 17, 3, tzinfo=dt.timezone.utc)  # Monday 03:00
    d = g.evaluate_order(ORDER, DEMO, [], now=night)
    assert not d.allow and any("outside window" in r for r in d.reasons)

    ok = dt.datetime(2026, 8, 17, 10, tzinfo=dt.timezone.utc)
    assert g.evaluate_order(ORDER, DEMO, [], now=ok).allow


# ------------------------------------------------------------------ sizing

def test_size_for_risk_matches_the_stop():
    # 1% of 100k = 1,000 risk; stop 40 wide; gold contract 100oz
    # -> 1000 / (40 * 100) = 0.25 lots
    v = size_for_risk(100_000, 0.01, 4400.0, 4360.0, 100.0)
    assert abs(v - 0.25) < 1e-9
    assert abs((4400.0 - 4360.0) * v * 100.0 - 1_000) < 1e-6


def test_size_below_broker_minimum_returns_zero():
    # A very wide stop on a small account implies less than the minimum lot.
    assert size_for_risk(1_000, 0.01, 4400.0, 3400.0, 100.0, volume_min=0.01) == 0.0


def test_size_is_zero_without_a_stop():
    assert size_for_risk(100_000, 0.01, 4400.0, 4400.0, 100.0) == 0.0


# ------------------------------------------------------------------ config

def test_unknown_config_key_is_an_error():
    try:
        load_config({"max_risk_per_trad": 0.5})    # typo
    except ValueError as e:
        assert "unknown risk config keys" in str(e)
    else:
        raise AssertionError("a misspelled limit must not silently use the default")


def test_defaults_are_disarmed():
    cfg = load_config(None)
    assert cfg["allow_live"] is False
    assert cfg["symbols"] == []          # nothing tradable until listed


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for name, fn in tests:
            work = tmp / name
            work.mkdir()
            try:
                fn(work) if fn.__code__.co_argcount else fn()
                print(f"  ok    {name}")
            except AssertionError as e:
                failed.append(name)
                print(f"  FAIL  {name}  {e}")
            except Exception as e:  # noqa: BLE001
                failed.append(name)
                print(f"  ERROR {name}  {type(e).__name__}: {e}")

    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
