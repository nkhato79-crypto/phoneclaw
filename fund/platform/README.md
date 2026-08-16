# Trading platform layer — MetaTrader 5

Connects the fund agents to a MetaTrader 5 account: reads the account into a book
of record, and executes agent intents autonomously through a risk gate.

## The one hard constraint

**The `MetaTrader5` Python package is Windows-only and needs a running MT5
terminal on the same machine.** It does not import on Linux or macOS.

This repo's Claude Code sessions run on ephemeral Linux containers that are
reclaimed after inactivity, so **the trader cannot run there** — not as a
limitation to work around, but as a fact to design for. It runs on your Windows
machine, or a Windows VPS, next to the terminal.

What does run anywhere: `risk_gate.py` and its tests, which is deliberate. The
component that decides whether money moves is testable without a broker.

```
python3 fund/platform/test_risk_gate.py     # 23 tests, no MT5 required
```

## Shape

```
  agents  ──writes──▶  fund/signals/intents.json
                              │
                              ▼
                       autotrade.py  ──asks──▶  risk_gate.py
                              │                      │
                              │              allow / deny + FLATTEN
                              ▼
                        mt5_client.py  ──▶  MT5 terminal  ──▶  broker
                              │
                              ├──▶  fund/book/latest.json     (book of record)
                              └──▶  fund/platform/audit.jsonl (every decision)
```

**Agents never call the broker.** A position agent writes an intent — instrument,
direction, stop, reason — and the loop sizes it, gates it, and executes it. The
intent records what the fund wanted; the audit log records what the gate decided
and what the broker did. Two separate records that disagree loudly when something
is wrong.

## Files

| File | Job |
| --- | --- |
| `mt5_client.py` | Terminal connection, account and position reads, order send. Decides nothing. |
| `risk_gate.py` | Every limit. No MT5 dependency, so it cannot be bypassed by mocking the broker. |
| `book_of_record.py` | Account + positions → `fund/book/latest.json`, the file every agent reads. |
| `autotrade.py` | The loop: read intents → size → gate → execute → log. |
| `settings.py` | Config loading. Credentials come from the environment, never the file. |
| `test_risk_gate.py` | 23 tests. Each one is a loss the system should refuse to take. |

## Setup

On the Windows machine running MT5:

```bat
pip install MetaTrader5 pyyaml
copy fund\platform\config.example.yaml fund\platform\config.yaml
set MT5_PASSWORD=your-password
```

Edit `config.yaml`: set `server`, `login` (or leave null to attach to the
account the terminal is already signed into), and the `symbols` allowlist.
In the terminal: **Tools → Options → Expert Advisors → Allow algorithmic trading.**

Then read the account without trading anything:

```bat
python fund\platform\book_of_record.py --print
```

## Arming, in order

The system ships disarmed. `allow_live` is `false` and a `KILL` file is present,
so a fresh clone cannot trade even if you run it by accident.

1. **Read-only.** `book_of_record.py` against your live account. This alone gives
   `risk-manager`, `fund-accountant`, `valuation-officer`,
   `treasury-collateral-manager` and `performance-analyst` something to measure —
   the first stress assessment failed purely for want of it.
2. **Demo, dry run.** Point `config.yaml` at a demo account, `del fund\platform\KILL`,
   then `python fund\platform\autotrade.py --dry-run`. It decides and logs but
   sends nothing. Read `audit.jsonl` and check the decisions are the ones you'd
   have made.
3. **Demo, live orders.** Drop `--dry-run`. Real fills, fake money. Leave it here
   long enough to see a losing day and confirm the daily loss limit flattens.
4. **Live.** Set `allow_live: true`. Until you do, the gate halts on any real
   account regardless of every other setting.

Do not skip step 3. The limits are only proven by watching them fire.

## The limits

All configured in `config.yaml`, all enforced in `risk_gate.py`.

| Limit | Default | What it stops |
| --- | --- | --- |
| `allow_live` | `false` | Any real-money account, until explicitly armed |
| Kill switch | `fund/platform/KILL` | Everything — checked before every order and every cycle |
| `symbols` | allowlist | Trading anything not named |
| `max_risk_per_trade` | 1% of equity | A single trade sizing beyond its stop |
| `daily_loss_limit` | 3% of equity | A bad day compounding — flattens and stops |
| `max_gross_exposure` | 2.0x equity | Leverage creeping up across positions |
| `max_symbol_exposure` | 1.0x equity | Concentration in one instrument |
| `max_order_exposure` | 0.5x equity | One oversized order |
| `max_open_positions` | 4 | Position sprawl |
| `max_consecutive_losses` | 4 | A strategy that has stopped working |
| `min_margin_level` | 300% | Drifting toward a margin call |
| `min_free_margin_fraction` | 30% | No headroom for a gap |
| `trading_hours_utc` | 06:00–20:00 | The thin hours around rollover |

Two behaviours worth knowing:

- **Sizing comes from the stop, not from a lot size.** `size_for_risk` solves for
  lots such that a stop-out costs exactly `max_risk_per_trade`. A wider stop
  produces a smaller position, never a larger loss. If the implied size is below
  the broker minimum the trade is skipped rather than rounded up.
- **Every order requires a stop.** `mt5_client.market_order` raises without one.
  An autonomous system with no stop has no bounded loss per trade, leaving the
  daily limit as the only thing between it and the account.

## Stopping it

```bat
type nul > fund\platform\KILL
```

A file on disk. No credentials, no restart, no code change — anyone with
filesystem access can stop the system. On the next check (before any order, and
at the top of every cycle) the loop flattens the account and exits. It also
refuses to start while the file exists.

## What it does not do

Stated plainly, because the gaps matter more than the features:

- **One broker.** Positions held elsewhere are invisible to it. The book of
  record names this in its `not_covered` field.
- **No fund accounting.** No subscriptions, redemptions, capital accounts,
  accruals or fees. `fund-accountant` still has no NAV.
- **No strategy.** Intents come from the agents; this layer sizes, gates and
  executes. A bad intent that passes the gate becomes a real losing trade.
- **No slippage or gap protection.** Stops are broker-side, so a weekend gap can
  fill well through them. The daily loss limit reacts after the fact, not during.
- **No tax or regulatory reporting.**

## Daily check

1. `fund/book/latest.json` — exposure, margin, day P&L.
2. `tail fund/platform/audit.jsonl` — every decision, including refusals. A run of
   `order_rejected` with the same reason usually means the limits and the strategy
   disagree; fix one of them rather than loosening the limit reflexively.
3. Any `FLATTEN` in the log means a limit fired. Find out why before restarting.
