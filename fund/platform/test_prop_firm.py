#!/usr/bin/env python3
"""Tests for the prop-firm rules.

    python3 fund/platform/test_prop_firm.py

Each test is an account death the system should refuse to walk into. Prop-firm
breaches are not recoverable losses — the account ends — so these matter more
than the house limits they sit on top of.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from prop_firm import PropFirmRules, day_start, load_prop_config, PropFirmError  # noqa: E402
from risk_gate import RiskGate  # noqa: E402

INITIAL = 100_000.0

FTMO = {
    "enabled": True,
    "initial_balance": INITIAL,
    "max_daily_loss_pct": 0.05,     # 5,000
    "max_total_loss_pct": 0.10,     # 10,000
    "buffer_fraction": 0.20,        # stop at 4,000 daily / 8,000 total
    "hold_over_weekend": False,
    "weekend_flat_from_utc": "19:00",
}

WEDNESDAY = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.timezone.utc)
FRIDAY_LATE = dt.datetime(2026, 8, 21, 19, 30, tzinfo=dt.timezone.utc)
FRIDAY_EARLY = dt.datetime(2026, 8, 21, 12, 0, tzinfo=dt.timezone.utc)
SATURDAY = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.timezone.utc)


def rules(**over) -> PropFirmRules:
    cfg = dict(FTMO)
    cfg.update(over)
    return PropFirmRules(cfg)


# ------------------------------------------------------------- the day boundary

def test_day_resets_on_prague_midnight_not_utc():
    """23:30 UTC in summer is already tomorrow in Prague.

    A system resetting on UTC midnight would still be counting the old day's
    losses here, or worse, hand itself a fresh allowance two hours early.
    """
    late = dt.datetime(2026, 8, 19, 23, 30, tzinfo=dt.timezone.utc)   # 01:30 Prague, 20 Aug
    boundary = day_start(late, "Europe/Prague")
    assert boundary == dt.datetime(2026, 8, 19, 22, 0, tzinfo=dt.timezone.utc)
    assert boundary.astimezone(dt.timezone.utc).day == 19

    utc_naive = late.replace(hour=0, minute=0)
    assert boundary != utc_naive, "Prague midnight must not equal UTC midnight in summer"


def test_day_boundary_is_two_hours_off_in_summer_one_in_winter():
    summer = day_start(dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc), "Europe/Prague")
    winter = day_start(dt.datetime(2026, 1, 19, 12, tzinfo=dt.timezone.utc), "Europe/Prague")
    assert summer.hour == 22          # previous day 22:00 UTC = 00:00 CEST
    assert winter.hour == 23          # previous day 23:00 UTC = 00:00 CET


# ------------------------------------------------------------------ daily loss

def test_daily_loss_measured_from_day_start_balance():
    """Not from current equity, and not from the initial balance."""
    r = rules()
    # Started the day at 98,000 after an earlier drawdown; now 95,000.
    h = r.headroom(equity=95_000, day_start_balance=98_000, now=WEDNESDAY)
    assert h.daily_used == 3_000
    assert h.daily_allowance == 5_000            # still 5% of the INITIAL 100k
    assert h.daily_left == 2_000


def test_daily_allowance_does_not_shrink_with_equity():
    r = rules()
    h = r.headroom(equity=92_000, day_start_balance=92_000, now=WEDNESDAY)
    assert h.daily_allowance == 5_000, "allowance is 5% of initial, not of current equity"


def test_open_loss_counts_toward_the_daily_rule():
    """Equity includes floating P&L, so an open loser breaches just as surely."""
    r = rules()
    h = r.headroom(equity=94_800, day_start_balance=100_000, now=WEDNESDAY)
    assert h.daily_used == 5_200
    assert h.daily_left < 0
    assert any("DAILY LOSS BREACHED" in b for b in h.breached)


def test_buffer_halts_before_the_firms_line():
    r = rules()
    # 4,200 down: inside our 4,000 buffer, still 800 clear of the firm's 5,000.
    ok, reasons, actions, h = r.evaluate_account(95_800, 100_000, WEDNESDAY)
    assert not ok
    assert "FLATTEN" in actions
    assert h.daily_left == 800 and h.daily_left_buffered < 0
    assert any("inside the buffer" in x for x in reasons)


def test_just_inside_the_buffer_still_trades():
    r = rules()
    ok, _, _, h = r.evaluate_account(96_500, 100_000, WEDNESDAY)   # 3,500 down
    assert ok and h.daily_left_buffered == 500


# ------------------------------------------------------------------ total loss

def test_total_loss_floor_is_static_from_initial_balance():
    r = rules()
    h = r.headroom(equity=89_500, day_start_balance=90_000, now=WEDNESDAY)
    assert h.total_used == 10_500
    assert any("MAX LOSS BREACHED" in b for b in h.breached)


def test_total_loss_breach_flattens_and_halts():
    ok, reasons, actions, _ = rules().evaluate_account(89_000, 90_000, WEDNESDAY)
    assert not ok and "FLATTEN" in actions and "HALT" in actions


def test_profitable_account_has_full_headroom():
    r = rules()
    h = r.headroom(equity=108_000, day_start_balance=107_000, now=WEDNESDAY)
    assert h.daily_used == -1_000 and h.total_used == -8_000
    assert h.daily_left > h.daily_allowance      # a gain adds headroom
    assert not h.breached and not h.warnings


# ----------------------------------------------------------------- order sizing

def test_order_refused_when_its_stop_would_breach_the_day():
    r = rules()
    h = r.headroom(equity=96_500, day_start_balance=100_000, now=WEDNESDAY)  # 500 buffered left
    ok, reasons = r.evaluate_order(risk_cash=800, now=WEDNESDAY, headroom=h)
    assert not ok and any("buffered daily allowance" in x for x in reasons)


def test_smaller_order_still_allowed_with_headroom_left():
    r = rules()
    h = r.headroom(equity=96_500, day_start_balance=100_000, now=WEDNESDAY)
    ok, _ = r.evaluate_order(risk_cash=400, now=WEDNESDAY, headroom=h)
    assert ok


def test_max_risk_cash_shrinks_as_the_day_is_used():
    r = rules()
    fresh = r.headroom(100_000, 100_000, WEDNESDAY)
    spent = r.headroom(97_500, 100_000, WEDNESDAY)
    assert r.max_risk_cash(fresh) == 4_000       # 80% of 5,000
    assert r.max_risk_cash(spent) == 1_500
    assert r.max_risk_cash(r.headroom(95_000, 100_000, WEDNESDAY)) == 0.0


# -------------------------------------------------------------------- weekend

def test_no_new_trades_and_flat_after_friday_cutoff():
    r = rules()
    assert r.must_be_flat_for_weekend(FRIDAY_LATE)
    assert not r.must_be_flat_for_weekend(FRIDAY_EARLY)
    assert r.must_be_flat_for_weekend(SATURDAY)

    ok, reasons, actions, _ = r.evaluate_account(100_000, 100_000, FRIDAY_LATE)
    assert not ok and "FLATTEN" in actions and any("weekend" in x for x in reasons)


def test_swing_account_may_hold_over_the_weekend():
    r = rules(hold_over_weekend=True)
    assert not r.must_be_flat_for_weekend(SATURDAY)
    ok, _, _, _ = r.evaluate_account(100_000, 100_000, FRIDAY_LATE)
    assert ok


# ----------------------------------------------------------------- news window

def test_news_blackout_blocks_orders_inside_the_window():
    event = "2026-08-19T12:30:00+00:00"
    r = rules(news_blackout_minutes=2, news_windows=[event])
    inside = dt.datetime(2026, 8, 19, 12, 31, tzinfo=dt.timezone.utc)
    outside = dt.datetime(2026, 8, 19, 12, 40, tzinfo=dt.timezone.utc)

    ok, reasons = r.evaluate_order(risk_cash=100, now=inside)
    assert not ok and any("news blackout" in x for x in reasons)
    assert r.evaluate_order(risk_cash=100, now=outside)[0]


def test_empty_news_list_is_a_silent_no_op():
    """Documented, because it looks like protection and is not."""
    r = rules(news_blackout_minutes=2, news_windows=[])
    assert r.in_news_blackout(WEDNESDAY) is None


# --------------------------------------------------------------------- config

def test_initial_balance_is_required_when_enabled():
    try:
        load_prop_config({"enabled": True})
    except PropFirmError as e:
        assert "initial_balance" in str(e)
    else:
        raise AssertionError("enabling prop rules without an initial balance must fail")


def test_unknown_key_rejected():
    try:
        load_prop_config({"enabled": True, "initial_balance": 1, "max_dailly_loss_pct": 0.05})
    except PropFirmError as e:
        assert "unknown prop_firm config keys" in str(e)
    else:
        raise AssertionError("a misspelled prop rule must not be ignored")


def test_disabled_by_default():
    assert PropFirmRules(None).enabled is False


# ------------------------------------------------------- integration with gate

def test_gate_fails_closed_without_day_start_balance(tmp_root=None):
    g = RiskGate({"symbols": ["XAUUSD"], "prop_firm": FTMO}, repo_root="/nonexistent")
    acct = {"login": 1, "equity": 100_000, "balance": 100_000, "margin": 0,
            "margin_free": 100_000, "margin_level": 0, "is_live": False}
    d = g.evaluate_account(acct, [], realised_today=0.0, now=WEDNESDAY)
    assert not d.allow and any("starting balance is unknown" in r for r in d.reasons)


def test_gate_applies_prop_rules_on_top_of_house_limits():
    """The FTMO buffer stops a day the house limits are happy with.

    The house daily limit is deliberately loosened here so the only thing that
    can trip is the prop rule — otherwise the house check fires first and the
    test proves nothing about the integration.
    """
    g = RiskGate({"symbols": ["XAUUSD"], "daily_loss_limit": 0.10, "prop_firm": FTMO},
                 repo_root="/nonexistent")
    acct = {"login": 1, "equity": 95_800, "balance": 95_800, "margin": 0,
            "margin_free": 95_800, "margin_level": 0, "is_live": False}
    # 4,200 down: inside the house 10%-of-equity limit, past the FTMO 4,000 buffer.
    d = g.evaluate_account(acct, [], realised_today=-4_200, now=WEDNESDAY,
                           day_start_balance=100_000)
    assert not d.allow and "FLATTEN" in d.actions
    assert any("buffer" in r for r in d.reasons)
    assert any("FTMO" in r for r in d.reasons), "status line should carry the firm"


def test_house_limit_can_fire_before_the_prop_rule():
    """Both flatten, so the account is safe either way — but the reason differs,
    and the audit log should show which one bound."""
    g = RiskGate({"symbols": ["XAUUSD"], "prop_firm": FTMO}, repo_root="/nonexistent")
    acct = {"login": 1, "equity": 95_800, "balance": 95_800, "margin": 0,
            "margin_free": 95_800, "margin_level": 0, "is_live": False}
    d = g.evaluate_account(acct, [], realised_today=-4_200, now=WEDNESDAY,
                           day_start_balance=100_000)
    assert not d.allow and "FLATTEN" in d.actions
    assert any("daily loss" in r for r in d.reasons)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
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
