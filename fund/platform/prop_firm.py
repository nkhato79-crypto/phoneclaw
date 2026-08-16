#!/usr/bin/env python3
"""Prop-firm evaluation rules, defaulted to FTMO.

A prop-firm account is not an ordinary account with tighter limits. Breaching a
rule does not cost you the loss — it ends the account, along with the fee paid
for it and any unpaid profit split. So these checks are deliberately harsher than
the house limits in `risk_gate.py`: they stop at a buffer *before* the firm's
line, because the firm measures continuously and a spike through the level while
you are between polls is still a breach.

Three structural differences from the house limits, and each one matters:

1. **Percentages are of the INITIAL balance, not current equity.** Draw down 5%
   and the allowance does not shrink with the account — it stays 5% of what you
   started with. Sizing off current equity, as `max_risk_per_trade` does, is a
   different measure and will not keep you compliant on its own.

2. **The daily loss is measured from the day's STARTING BALANCE against current
   EQUITY, floating P&L included.** An open loser breaches the daily rule just as
   surely as a closed one. There is no "it will come back" — the rule is checked
   on equity, tick by tick.

3. **The day rolls at the firm's server midnight, CE(S)T for FTMO, not UTC.**
   Two hours' difference in summer. A loss booked at 23:30 UTC belongs to the
   *next* FTMO day, and a system that resets at UTC midnight will believe it has
   a fresh allowance when it does not.

RULE ACCURACY: these are the parameters as commonly published, and they are all
configurable because prop firms revise them and they vary by account type
(Normal vs Swing) and phase (Challenge, Verification, Funded). FTMO has changed
its terms more than once — notably around minimum trading days. **Check the
numbers against your own dashboard and account agreement before arming this.**
The defaults here are a starting point, not a legal statement of your terms.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

FTMO_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "firm": "FTMO",
    "account_type": "normal",          # normal | swing
    "phase": "challenge",              # challenge | verification | funded
    "initial_balance": 0.0,            # REQUIRED when enabled; every % is of this
    "max_daily_loss_pct": 0.05,        # 5% of initial balance
    "max_total_loss_pct": 0.10,        # 10% of initial balance, static floor
    "profit_target_pct": 0.10,         # informational; 0.05 on verification
    "day_reset_tz": "Europe/Prague",   # FTMO server day is CE(S)T
    # Stop at 80% of each allowance. The remaining 20% absorbs slippage, swap,
    # commission and the gap between one poll and the next.
    "buffer_fraction": 0.20,
    # Normal accounts are typically flat-by-weekend; Swing accounts may hold.
    "hold_over_weekend": False,
    "weekend_flat_from_utc": "19:00",  # Friday: no new trades, flatten
    # Normal accounts typically restrict trading around high-impact releases.
    "news_blackout_minutes": 2,
    "news_windows": [],                # ISO timestamps of high-impact events
}


class PropFirmError(ValueError):
    pass


def load_prop_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(FTMO_DEFAULTS)
    if not raw:
        return cfg
    unknown = set(raw) - set(FTMO_DEFAULTS)
    if unknown:
        raise PropFirmError(f"unknown prop_firm config keys: {sorted(unknown)}")
    cfg.update(raw)
    if cfg["enabled"] and not cfg["initial_balance"]:
        raise PropFirmError(
            "prop_firm.initial_balance must be set when enabled — every FTMO "
            "allowance is a percentage of the starting balance, not of current equity"
        )
    if not 0 <= cfg["buffer_fraction"] < 1:
        raise PropFirmError("prop_firm.buffer_fraction must be in [0, 1)")
    return cfg


@dataclass
class Headroom:
    """What is left before the firm's lines, in account currency."""
    daily_allowance: float          # full allowance for the day
    daily_used: float               # loss taken so far today (positive = loss)
    daily_left: float               # to the firm's line
    daily_left_buffered: float      # to our earlier stop
    total_allowance: float
    total_used: float
    total_left: float
    total_left_buffered: float
    day_start_balance: float
    day_started_at: str
    breached: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def day_start(now: dt.datetime, tz_name: str) -> dt.datetime:
    """Most recent firm-server midnight, returned in UTC.

    This is the boundary the daily loss resets on. Getting it wrong by two hours
    is the difference between a fresh allowance and a breach.
    """
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(dt.timezone.utc)


class PropFirmRules:
    def __init__(self, config: dict[str, Any] | None = None):
        self.cfg = load_prop_config(config)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg["enabled"])

    # ---------------------------------------------------------------- headroom

    def headroom(self, equity: float, day_start_balance: float,
                 now: dt.datetime | None = None) -> Headroom:
        """Distance to both firm lines.

        `day_start_balance` is the account balance at the firm's server midnight.
        `autotrade` derives it as (current balance − realised P&L since that
        midnight), which is exact as long as no deposit or withdrawal landed
        mid-day.
        """
        now = now or dt.datetime.now(dt.timezone.utc)
        initial = float(self.cfg["initial_balance"])
        buffer = float(self.cfg["buffer_fraction"])

        daily_allowance = self.cfg["max_daily_loss_pct"] * initial
        total_allowance = self.cfg["max_total_loss_pct"] * initial

        # Both measured on EQUITY, so open losses count.
        daily_used = day_start_balance - equity
        total_used = initial - equity

        h = Headroom(
            daily_allowance=daily_allowance,
            daily_used=daily_used,
            daily_left=daily_allowance - daily_used,
            daily_left_buffered=daily_allowance * (1 - buffer) - daily_used,
            total_allowance=total_allowance,
            total_used=total_used,
            total_left=total_allowance - total_used,
            total_left_buffered=total_allowance * (1 - buffer) - total_used,
            day_start_balance=day_start_balance,
            day_started_at=day_start(now, self.cfg["day_reset_tz"]).isoformat(timespec="seconds"),
        )

        if h.daily_left <= 0:
            h.breached.append(
                f"DAILY LOSS BREACHED: down {daily_used:,.2f} against a "
                f"{daily_allowance:,.2f} allowance ({self.cfg['max_daily_loss_pct']:.0%} "
                f"of {initial:,.0f}). The account is gone — stop and contact the firm."
            )
        elif h.daily_left_buffered <= 0:
            h.warnings.append(
                f"daily loss {daily_used:,.2f} is inside the buffer — only "
                f"{h.daily_left:,.2f} left to the firm's line; trading halted here "
                f"deliberately, {buffer:.0%} short of it"
            )

        if h.total_left <= 0:
            h.breached.append(
                f"MAX LOSS BREACHED: equity {equity:,.2f} is below the floor "
                f"{initial * (1 - self.cfg['max_total_loss_pct']):,.2f} "
                f"({self.cfg['max_total_loss_pct']:.0%} of {initial:,.0f})."
            )
        elif h.total_left_buffered <= 0:
            h.warnings.append(
                f"total drawdown {total_used:,.2f} is inside the buffer — "
                f"{h.total_left:,.2f} left to the floor"
            )

        return h

    # ----------------------------------------------------------------- account

    def evaluate_account(self, equity: float, day_start_balance: float,
                         now: dt.datetime | None = None) -> tuple[bool, list[str], list[str], Headroom]:
        """(allow, reasons, actions, headroom) for the account as a whole."""
        now = now or dt.datetime.now(dt.timezone.utc)
        h = self.headroom(equity, day_start_balance, now)
        reasons: list[str] = []
        actions: list[str] = []

        if h.breached:
            # Flatten anyway: the account is finished, and leaving positions open
            # only adds loss to an account that can no longer earn anything back.
            return False, h.breached, ["FLATTEN", "HALT"], h

        if h.warnings:
            return False, h.warnings, ["FLATTEN"], h

        if self.must_be_flat_for_weekend(now):
            return False, [f"weekend flat rule: {self.cfg['account_type']} account, "
                           f"flat from {self.cfg['weekend_flat_from_utc']} UTC Friday"], ["FLATTEN"], h

        return True, reasons, actions, h

    # ------------------------------------------------------------------- order

    def evaluate_order(self, risk_cash: float, now: dt.datetime | None = None,
                       headroom: Headroom | None = None) -> tuple[bool, list[str]]:
        """Per-order checks. `risk_cash` is the loss if this trade stops out.

        The important one: a trade is refused when its own stop-out would push
        the day through the buffered allowance. This is what stops the "one more
        trade" that turns a bad day into a dead account.
        """
        now = now or dt.datetime.now(dt.timezone.utc)
        reasons: list[str] = []

        if self.no_new_trades_before_weekend(now):
            reasons.append(
                f"no new positions before the weekend on a "
                f"{self.cfg['account_type']} account "
                f"(from {self.cfg['weekend_flat_from_utc']} UTC Friday)"
            )

        window = self.in_news_blackout(now)
        if window:
            reasons.append(f"inside the {self.cfg['news_blackout_minutes']}-minute "
                           f"news blackout around {window}")

        if headroom is not None:
            if risk_cash >= headroom.daily_left_buffered:
                reasons.append(
                    f"a stop-out on this trade costs {risk_cash:,.2f}, against "
                    f"{headroom.daily_left_buffered:,.2f} of buffered daily allowance "
                    f"({headroom.daily_left:,.2f} to the firm's line)"
                )
            if risk_cash >= headroom.total_left_buffered:
                reasons.append(
                    f"a stop-out on this trade costs {risk_cash:,.2f}, against "
                    f"{headroom.total_left_buffered:,.2f} of buffered total allowance"
                )

        return (not reasons), reasons

    # ----------------------------------------------------------------- helpers

    def _weekend_cutoff(self, now: dt.datetime) -> dt.datetime:
        hh, mm = (int(x) for x in str(self.cfg["weekend_flat_from_utc"]).split(":"))
        return now.astimezone(dt.timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0)

    def must_be_flat_for_weekend(self, now: dt.datetime) -> bool:
        if self.cfg["hold_over_weekend"]:
            return False
        utc = now.astimezone(dt.timezone.utc)
        if utc.weekday() == 4 and utc >= self._weekend_cutoff(utc):   # Friday
            return True
        return utc.weekday() in (5, 6)                                # Sat, Sun

    def no_new_trades_before_weekend(self, now: dt.datetime) -> bool:
        return self.must_be_flat_for_weekend(now)

    def in_news_blackout(self, now: dt.datetime) -> str | None:
        """Return the event timestamp whose blackout window we are inside.

        `news_windows` is supplied by the caller — this module does not fetch a
        calendar. With an empty list there is no blackout, which is a silent
        no-op: on an account where the rule applies, an empty list means the
        check is not actually protecting you.
        """
        minutes = int(self.cfg["news_blackout_minutes"] or 0)
        if not minutes or not self.cfg["news_windows"]:
            return None
        utc = now.astimezone(dt.timezone.utc)
        for stamp in self.cfg["news_windows"]:
            try:
                event = dt.datetime.fromisoformat(stamp)
            except ValueError:
                continue
            if event.tzinfo is None:
                event = event.replace(tzinfo=dt.timezone.utc)
            if abs((utc - event).total_seconds()) <= minutes * 60:
                return stamp
        return None

    def max_risk_cash(self, headroom: Headroom) -> float:
        """Largest per-trade risk that keeps the buffered allowances intact.

        `autotrade` sizes against this as well as the house `max_risk_per_trade`,
        so position size shrinks automatically as the day's allowance is used up.
        """
        return max(0.0, min(headroom.daily_left_buffered, headroom.total_left_buffered))

    def status_line(self, h: Headroom) -> str:
        return (f"{self.cfg['firm']} {self.cfg['phase']}: daily "
                f"{h.daily_used:,.0f}/{h.daily_allowance:,.0f} used, "
                f"{h.daily_left:,.0f} to the line; total "
                f"{h.total_used:,.0f}/{h.total_allowance:,.0f} used, "
                f"{h.total_left:,.0f} to the floor")
