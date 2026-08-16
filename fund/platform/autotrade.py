#!/usr/bin/env python3
"""The autonomous trading loop.

The boundary this file enforces: **agents never touch the broker.** A position
agent writes an intent to fund/signals/intents.json — an instrument, a direction,
a stop, and a reason. This loop reads intents, sizes them from the stop, puts
every one through risk_gate, and only then sends an order. Everything, allowed or
denied, is appended to an audit log.

That split is what makes an autonomous system reviewable. The intent says what
the fund wanted; the audit log says what the gate decided and what the broker
did; the two are separate records and they disagree loudly when something is
wrong.

    python3 fund/platform/autotrade.py --once      # one cycle, then exit
    python3 fund/platform/autotrade.py             # run until stopped
    python3 fund/platform/autotrade.py --dry-run   # decide, never send

To stop it from anywhere, with no credentials and no restart:

    touch fund/platform/KILL

The kill switch is checked before every order and at the top of every cycle. When
it appears, the loop flattens the account and exits.

PLATFORM: requires Windows, a running MT5 terminal, and `pip install MetaTrader5`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import signal
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from book_of_record import build_book, write_book  # noqa: E402
from mt5_client import MT5Client, MT5Error  # noqa: E402
from risk_gate import RiskGate, size_for_risk  # noqa: E402
from settings import load_settings  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
_stop = False


def _handle_signal(signum, _frame):
    global _stop
    _stop = True
    print(f"\nsignal {signum} received — finishing this cycle, then stopping", flush=True)


class Audit:
    """Append-only JSONL. Never rewritten, never truncated by this process."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "kind": kind, **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        return rec

    def executed_intent_ids(self) -> set[str]:
        """Intents already acted on, so a re-read of the file cannot double-fill.

        Read fresh each cycle rather than cached in memory: if the process
        restarts, the log is still the source of truth about what was sent.
        """
        done: set[str] = set()
        if not self.path.exists():
            return done
        for line in self.path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") in ("order_sent", "order_rejected") and rec.get("intent_id"):
                done.add(rec["intent_id"])
        return done

    def consecutive_losses(self) -> int:
        closes = []
        if not self.path.exists():
            return 0
        for line in self.path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "position_closed" and rec.get("profit") is not None:
                closes.append(rec["profit"])
        run = 0
        for pnl in reversed(closes):
            if pnl < 0:
                run += 1
            else:
                break
        return run


def read_intents(path: pathlib.Path) -> list[dict[str, Any]]:
    """Load agent-written intents.

    A malformed intents file is not a reason to trade on stale data — it returns
    empty and the cycle does nothing.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"intents file is not valid JSON ({e}) — ignoring this cycle", flush=True)
        return []
    intents = data.get("intents", data if isinstance(data, list) else [])
    now = dt.datetime.now(dt.timezone.utc)

    live = []
    for i in intents:
        expiry = i.get("expires_at")
        if expiry:
            try:
                if dt.datetime.fromisoformat(expiry) < now:
                    continue          # stale intent; the view behind it has aged out
            except ValueError:
                continue
        live.append(i)
    return live


def cycle(client: MT5Client, gate: RiskGate, audit: Audit, settings, dry_run: bool) -> bool:
    """One pass. Returns False when the loop should stop."""
    account = client.account().to_dict()
    positions = [p.to_dict() for p in client.positions()]
    midnight = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    realised = client.realised_pnl(midnight)

    # Refresh the book every cycle: it is what the agents read, and a stale book
    # is worse than none because it looks current.
    write_book(build_book(account, positions, realised), REPO)

    verdict = gate.evaluate_account(account, positions, realised,
                                    consecutive_losses=audit.consecutive_losses())
    audit.write("account_check", account=account["login"], mode=account["mode"] if "mode" in account
                else ("LIVE" if account["is_live"] else "DEMO"),
                equity=account["equity"], decision=verdict.to_dict())

    if not verdict.allow:
        print(f"HALT: {'; '.join(verdict.reasons)}", flush=True)
        if "FLATTEN" in verdict.actions and positions:
            if dry_run:
                print(f"dry-run: would flatten {len(positions)} positions", flush=True)
            else:
                for res in client.close_all(reason=verdict.reasons[0][:24]):
                    audit.write("position_closed", result=res.to_dict(),
                                reason="risk gate flatten")
                print(f"flattened {len(positions)} positions", flush=True)
        return False

    intents = read_intents(REPO / settings.signals_file)
    already = audit.executed_intent_ids()

    for intent in intents:
        iid = intent.get("id")
        if not iid:
            audit.write("intent_malformed", intent=intent, reason="no id")
            continue
        if iid in already:
            continue

        if intent.get("action") == "close":
            ticket = intent.get("ticket")
            match = [p for p in positions if p["ticket"] == ticket]
            if not match:
                audit.write("order_rejected", intent_id=iid, reason=f"ticket {ticket} not open")
                continue
            if dry_run:
                audit.write("order_rejected", intent_id=iid, reason="dry-run")
                continue
            res = client.close_position(ticket, comment=f"intent {iid}"[:31])
            audit.write("position_closed", intent_id=iid, result=res.to_dict(),
                        reason=intent.get("rationale", ""))
            continue

        try:
            info = client.symbol(intent["symbol"])
            bid, ask = client.tick(intent["symbol"])
        except (MT5Error, KeyError) as e:
            audit.write("order_rejected", intent_id=iid, reason=f"symbol lookup failed: {e}")
            continue

        price = ask if intent.get("direction") == "long" else bid
        volume = size_for_risk(
            equity=account["equity"],
            risk_fraction=min(float(intent.get("risk_fraction", gate.cfg["max_risk_per_trade"])),
                              gate.cfg["max_risk_per_trade"]),
            price=price, sl=float(intent.get("sl", 0) or 0),
            contract_size=info.trade_contract_size,
            volume_step=info.volume_step, volume_min=info.volume_min,
            volume_max=info.volume_max,
        )

        order = {
            "symbol": intent.get("symbol"), "direction": intent.get("direction"),
            "volume": volume, "price": price, "sl": intent.get("sl"),
            "tp": intent.get("tp"), "contract_size": info.trade_contract_size,
        }
        decision = gate.evaluate_order(order, account, positions)

        if volume <= 0:
            decision.deny("sized below the broker minimum at this stop distance")

        if not decision.allow:
            audit.write("order_rejected", intent_id=iid, order=order,
                        decision=decision.to_dict(), rationale=intent.get("rationale", ""))
            print(f"rejected {iid}: {'; '.join(decision.reasons)}", flush=True)
            continue

        if dry_run:
            audit.write("order_rejected", intent_id=iid, order=order,
                        decision=decision.to_dict(), reason="dry-run")
            print(f"dry-run: would send {order['direction']} {volume} {order['symbol']}", flush=True)
            continue

        res = client.market_order(
            symbol=order["symbol"], direction=order["direction"], volume=volume,
            sl=float(order["sl"]), tp=float(order["tp"]) if order.get("tp") else None,
            comment=f"fd {iid}"[:31],
        )
        audit.write("order_sent", intent_id=iid, order=order, result=res.to_dict(),
                    decision=decision.to_dict(), rationale=intent.get("rationale", ""))
        print(("sent " if res.ok else "broker rejected ") +
              f"{order['direction']} {volume} {order['symbol']} — {res.comment}", flush=True)

        positions = [p.to_dict() for p in client.positions()]

    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="fund/platform/config.yaml")
    ap.add_argument("--once", action="store_true", help="run a single cycle and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="evaluate and log, but never send an order")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    settings = load_settings(REPO / args.config)
    gate = RiskGate(settings.risk, repo_root=REPO)
    audit = Audit(REPO / settings.audit_log)

    if gate.kill_switch_engaged():
        print(f"kill switch present at {gate.cfg['kill_switch_file']} — refusing to start. "
              f"Remove it to arm.", flush=True)
        return 3

    try:
        with MT5Client(**settings.mt5_kwargs()) as client:
            acct = client.account()
            mode = "LIVE" if acct.is_live else "DEMO"
            print(f"connected: {acct.login}@{acct.server} [{mode}] "
                  f"equity {acct.equity:,.2f} {acct.currency}"
                  + ("  (dry-run)" if args.dry_run else ""), flush=True)
            audit.write("session_start", account=acct.login, mode=mode,
                        equity=acct.equity, dry_run=args.dry_run, config=gate.cfg)

            while True:
                if not cycle(client, gate, audit, settings, args.dry_run):
                    break
                if args.once or _stop:
                    break
                time.sleep(settings.poll_seconds)

            audit.write("session_end", reason="kill switch or halt" if not _stop else "signal")
    except MT5Error as e:
        print(f"MT5: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
