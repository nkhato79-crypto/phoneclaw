#!/usr/bin/env python3
"""Pre-trade and account-level risk gate.

This is the file that stands between an autonomous agent and the account. It has
no MT5 dependency on purpose: it operates on plain data, so it is testable
without a terminal and cannot be bypassed by mocking the broker.

Two entry points:

    evaluate_account(...)  halt conditions that hold regardless of any order —
                           kill switch, daily loss limit, equity floor, margin
                           floor. Returns actions, including FLATTEN.

    evaluate_order(...)    per-order checks — allowlist, sizing, exposure caps,
                           stop discipline, position counts, trading window.

Both return a Decision. `allow` is only ever True when every check passed; a
check that cannot be evaluated (missing input, bad data) fails closed. In an
autonomous system, "I could not tell" must mean "no".

The limits themselves live in config.yaml, not here, so tightening them never
requires a code change — but the *checks* live here, so removing one is a visible
diff rather than a quiet config edit.
"""
from __future__ import annotations

import datetime as dt
import pathlib
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class Decision:
    allow: bool
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)   # e.g. ["FLATTEN", "HALT"]

    def deny(self, reason: str, action: str | None = None) -> "Decision":
        self.allow = False
        self.reasons.append(reason)
        if action and action not in self.actions:
            self.actions.append(action)
        return self

    def note(self, reason: str) -> "Decision":
        self.reasons.append(reason)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"allow": self.allow, "reasons": self.reasons, "actions": self.actions}


DEFAULTS: dict[str, Any] = {
    # Arming
    "allow_live": False,           # must be explicitly True to touch a real account
    "kill_switch_file": "fund/platform/KILL",

    # Universe and timing
    "symbols": [],                 # empty = nothing is tradable; allowlist only
    "trading_days": [0, 1, 2, 3, 4],
    "trading_hours_utc": [0, 24],

    # Exposure, all as a fraction of equity unless noted
    "max_gross_exposure": 3.0,
    "max_symbol_exposure": 1.0,
    "max_order_exposure": 0.5,
    "max_open_positions": 8,
    "max_positions_per_symbol": 2,

    # Loss control
    "max_risk_per_trade": 0.01,    # distance-to-stop loss as a fraction of equity
    "daily_loss_limit": 0.03,      # realised + unrealised, fraction of equity
    "equity_floor": 0.0,           # absolute; 0 disables
    "max_consecutive_losses": 4,

    # Margin
    "min_margin_level": 300.0,     # percent; MT5 margin call territory is ~100
    "min_free_margin_fraction": 0.30,
}


def load_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge user config over defaults. Unknown keys are an error, not a typo to
    ignore — a misspelled limit that silently falls back to the default is how a
    cap ends up looser than the person who wrote it believes."""
    cfg = dict(DEFAULTS)
    if not raw:
        return cfg
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown risk config keys: {sorted(unknown)}")
    cfg.update(raw)
    return cfg


class RiskGate:
    def __init__(self, config: dict[str, Any] | None = None, repo_root: str | pathlib.Path = "."):
        self.cfg = load_config(config)
        self.root = pathlib.Path(repo_root)

    # ------------------------------------------------------------------ state

    def kill_switch_engaged(self) -> bool:
        """A file on disk, not a config value or an env var.

        Anyone with filesystem access can stop the system — no credentials, no
        restart, no code. `touch fund/platform/KILL` is the whole procedure.
        """
        return (self.root / self.cfg["kill_switch_file"]).exists()

    # ---------------------------------------------------------------- account

    def evaluate_account(self, account: dict[str, Any], positions: Iterable[dict[str, Any]],
                         realised_today: float, consecutive_losses: int = 0,
                         now: dt.datetime | None = None) -> Decision:
        """Halt conditions. Run this before considering any order, every cycle."""
        d = Decision(allow=True)
        now = now or dt.datetime.now(dt.timezone.utc)
        positions = list(positions)

        if self.kill_switch_engaged():
            return d.deny(f"kill switch present at {self.cfg['kill_switch_file']}", "FLATTEN")

        equity = account.get("equity")
        if equity is None or equity <= 0:
            return d.deny("equity missing or non-positive — failing closed", "HALT")

        if account.get("is_live") and not self.cfg["allow_live"]:
            return d.deny(
                f"account {account.get('login')} is LIVE and allow_live is false",
                "HALT",
            )

        floor = self.cfg["equity_floor"]
        if floor and equity <= floor:
            return d.deny(f"equity {equity:,.2f} at or below floor {floor:,.2f}", "FLATTEN")

        unrealised = sum(p.get("profit", 0.0) for p in positions)
        day_pnl = realised_today + unrealised
        limit = self.cfg["daily_loss_limit"] * equity
        if limit and day_pnl <= -limit:
            return d.deny(
                f"daily loss {day_pnl:,.2f} breached limit {-limit:,.2f} "
                f"(realised {realised_today:,.2f} + open {unrealised:,.2f})",
                "FLATTEN",
            )

        if self.cfg["max_consecutive_losses"] and consecutive_losses >= self.cfg["max_consecutive_losses"]:
            d.deny(f"{consecutive_losses} consecutive losing trades", "HALT")

        margin_level = account.get("margin_level") or 0.0
        if account.get("margin", 0) > 0 and margin_level < self.cfg["min_margin_level"]:
            d.deny(f"margin level {margin_level:.0f}% below floor {self.cfg['min_margin_level']:.0f}%",
                   "HALT")

        free_frac = (account.get("margin_free", 0.0) / equity) if equity else 0.0
        if free_frac < self.cfg["min_free_margin_fraction"]:
            d.deny(f"free margin {free_frac:.0%} of equity below floor "
                   f"{self.cfg['min_free_margin_fraction']:.0%}", "HALT")

        if d.allow:
            d.note(f"account clear — equity {equity:,.2f}, day P&L {day_pnl:,.2f}, "
                   f"{len(positions)} open")
        return d

    # ------------------------------------------------------------------ order

    def evaluate_order(self, order: dict[str, Any], account: dict[str, Any],
                       positions: Iterable[dict[str, Any]],
                       now: dt.datetime | None = None) -> Decision:
        """Per-order checks.

        `order` needs: symbol, direction, volume, price, sl, contract_size.
        Anything missing is a denial — this function never guesses a stop.
        """
        d = Decision(allow=True)
        now = now or dt.datetime.now(dt.timezone.utc)
        positions = list(positions)

        if self.kill_switch_engaged():
            return d.deny("kill switch engaged", "FLATTEN")

        required = ("symbol", "direction", "volume", "price", "sl", "contract_size")
        missing = [k for k in required if order.get(k) in (None, "", 0)]
        if missing:
            return d.deny(f"order missing required fields: {missing}")

        symbol = order["symbol"]
        equity = account.get("equity") or 0.0
        if equity <= 0:
            return d.deny("equity non-positive — failing closed")

        if symbol not in self.cfg["symbols"]:
            return d.deny(f"{symbol} not in allowlist {self.cfg['symbols']}")

        if order["direction"] not in ("long", "short"):
            return d.deny(f"direction {order['direction']!r} is not long|short")

        if now.weekday() not in self.cfg["trading_days"]:
            d.deny(f"{now:%A} is outside trading days {self.cfg['trading_days']}")
        lo, hi = self.cfg["trading_hours_utc"]
        if not (lo <= now.hour < hi):
            d.deny(f"{now:%H:%M} UTC outside window {lo:02d}:00-{hi:02d}:00")

        # Stop discipline. The stop must be on the losing side of entry, and the
        # loss it implies is what sizes the trade — not the notional.
        price, sl = float(order["price"]), float(order["sl"])
        if order["direction"] == "long" and sl >= price:
            return d.deny(f"long stop {sl} is at or above entry {price}")
        if order["direction"] == "short" and sl <= price:
            return d.deny(f"short stop {sl} is at or below entry {price}")

        notional = float(order["volume"]) * float(order["contract_size"]) * price
        risk_cash = abs(price - sl) * float(order["volume"]) * float(order["contract_size"])
        risk_frac = risk_cash / equity

        if risk_frac > self.cfg["max_risk_per_trade"]:
            d.deny(f"risk to stop {risk_cash:,.2f} is {risk_frac:.2%} of equity, "
                   f"over cap {self.cfg['max_risk_per_trade']:.2%}")

        if notional / equity > self.cfg["max_order_exposure"]:
            d.deny(f"order notional {notional:,.2f} is {notional / equity:.2f}x equity, "
                   f"over cap {self.cfg['max_order_exposure']:.2f}x")

        # Exposure caps count existing positions plus this one. Same-symbol
        # opposing positions net; MT5 hedging accounts allow both at once.
        def signed(p: dict[str, Any]) -> float:
            return p.get("notional", 0.0) * (1 if p.get("direction") == "long" else -1)

        new_signed = notional * (1 if order["direction"] == "long" else -1)

        sym_positions = [p for p in positions if p.get("symbol") == symbol]
        sym_gross = abs(sum(signed(p) for p in sym_positions) + new_signed)
        if sym_gross / equity > self.cfg["max_symbol_exposure"]:
            d.deny(f"{symbol} exposure would reach {sym_gross / equity:.2f}x equity, "
                   f"over cap {self.cfg['max_symbol_exposure']:.2f}x")

        gross = sum(abs(signed(p)) for p in positions) + notional
        if gross / equity > self.cfg["max_gross_exposure"]:
            d.deny(f"gross exposure would reach {gross / equity:.2f}x equity, "
                   f"over cap {self.cfg['max_gross_exposure']:.2f}x")

        if len(positions) >= self.cfg["max_open_positions"]:
            d.deny(f"{len(positions)} positions open, cap {self.cfg['max_open_positions']}")

        if len(sym_positions) >= self.cfg["max_positions_per_symbol"]:
            d.deny(f"{len(sym_positions)} positions already in {symbol}, "
                   f"cap {self.cfg['max_positions_per_symbol']}")

        if d.allow:
            d.note(f"cleared {order['direction']} {order['volume']} {symbol} — "
                   f"risk {risk_frac:.2%} of equity, notional {notional / equity:.2f}x")
        return d


def size_for_risk(equity: float, risk_fraction: float, price: float, sl: float,
                  contract_size: float, volume_step: float = 0.01,
                  volume_min: float = 0.01, volume_max: float = 100.0) -> float:
    """Lots such that a stop-out costs `risk_fraction` of equity.

    Sizing from the stop rather than from a fixed lot size is what keeps a wide
    stop from quietly becoming a large position. Returns 0.0 when the implied
    size is below the broker's minimum — the correct answer there is to skip the
    trade, never to round up to the minimum.
    """
    stop_distance = abs(price - sl)
    if stop_distance <= 0 or contract_size <= 0 or equity <= 0:
        return 0.0
    raw = (equity * risk_fraction) / (stop_distance * contract_size)
    stepped = round(raw / volume_step) * volume_step
    if stepped < volume_min:
        return 0.0
    return round(min(stepped, volume_max), 8)
