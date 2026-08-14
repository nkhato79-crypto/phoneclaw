---
name: investment-fund-desk
description: Run work through a full investment fund staffed as agents — every position from CIO and portfolio manager to risk, compliance, valuation, trade operations, fund accounting, treasury, prime brokerage, investor relations and performance. Use when a request involves investment research, position sizing, execution planning, risk or compliance review, NAV and fund accounting, LP reporting, or when the user asks who at a fund would handle something.
---

# Investment Fund Desk

This repo carries a complete investment-fund org as Claude Code subagents in
`.claude/agents/`. This skill is the routing layer: it maps a request to the positions
that own it and runs them in the order a real fund would.

## How to use it

1. Read `.claude/agents/README.md` for the roster, org chart, and shared house rules.
2. Identify the owning position and any supporting ones.
3. For anything crossing more than one desk, invoke the `fund-desk` orchestrator rather
   than stitching positions together by hand.
4. For a single, clearly-owned question, invoke that position's agent directly.

## Routing table

| Request looks like | Position |
| --- | --- |
| "Is this stock a buy?" / model a company | `equity-research-analyst` |
| Bond, loan, covenant, default, recovery | `credit-analyst` |
| Rates, inflation, FX, gold, regime | `macro-strategist` |
| Signal, backtest, Kronos forecast research | `quant-researcher` |
| Build/productionise research, pipelines, model code | `quant-developer` |
| New dataset, data quality, scraping legality | `alt-data-analyst` |
| How big should this position be / rebalance / hedge | `portfolio-manager` |
| Allocate capital across strategies, final sign-off | `cio` |
| How do we get in or out, cost of trading, TCA | `execution-trader` |
| Limits, VaR, stress, drawdown response | `risk-manager` |
| Marks, illiquid pricing, Level 3 | `valuation-officer` |
| Are we allowed to, restricted list, MNPI, filings | `compliance-officer` |
| LPA, ISDA, side letters, contracts, licences | `legal-counsel` |
| Control testing, breach and exception tracking | `internal-auditor` |
| Process, vendors, incident, business continuity | `coo` |
| Booking, settlement, reconciliation, breaks | `trade-operations` |
| Systems, deployment, access, monitoring | `head-of-technology` |
| Budget, fees, expense allocation, audit | `cfo` |
| NAV, capital accounts, month-end close | `fund-accountant` |
| Cash, margin calls, collateral, financing | `treasury-collateral-manager` |
| Prime broker terms, borrow, counterparty exposure | `prime-broker-liaison` |
| LP letter, DDQ, fundraising, redemption query | `investor-relations` |
| Returns, attribution, benchmark, drawdown stats | `performance-analyst` |

## Control gates that cannot be skipped

- Sizing requires a thesis. Execution requires sizing plus `risk-manager` and
  `compliance-officer` clearance. Booking requires execution. NAV requires independent
  marks. Investor-facing output requires `compliance-officer` review.
- Front office never marks its own book, sets its own limits, or clears its own
  compliance.
- A `cio` override of a risk limit is written, time-limited, and copied to
  `internal-auditor`. `compliance-officer` cannot be overridden on a legal point.

## Standing limits

Everything produced here is decision support for a professional team, not investment
advice. No agent places live orders, moves real cash, or touches production brokerage,
custody, or payment systems — simulated flows only, labelled as such. Requests that seek
or act on material non-public information, or that describe market manipulation, stop and
go to `compliance-officer`.

## Repo tooling available to the quantitative positions

Kronos (financial-markets foundation model) lives here: `model/` for inference,
`examples/prediction_example.py` and `examples/run_backtest_kronos.py` for forecasting
and backtests, `finetune/` for adaptation, `gold_dashboard/app.py` for serving, and
`tests/test_kronos_regression.py` as the regression gate. Kronos output is a
distribution over sampled paths — report the model variant, context length, sampling
parameters, and path count with every result.
