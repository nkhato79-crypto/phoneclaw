# Risk Assessment — 2022-Style Rate Shock Stress

**From:** Risk Manager (CRO) — independent function
**Date:** 2026-08-14
**Request:** "Stress the book through a 2022-style rate shock — tell me what breaks first."
**Status:** **FAIL (unmeasurable)** on the actual mandate. Framework demonstration below on an
explicitly hypothetical book, which itself returns **FAIL (3 hard limit breaches)**.
**Distribution:** requester · `cio` · `internal-auditor` (exception log) · `treasury-collateral-manager`

---

## 0. Headline — read this before anything else

**There is no book of record. There is no measured exposure. Nothing in this document is a
measurement of a real portfolio.**

I searched `/home/user/phoneclaw` for a position file, holdings blotter, portfolio state,
NAV record, or exposure extract. There is none. What exists is model and research plumbing:

| Artifact | What it is | Why it is not a book |
| --- | --- | --- |
| `/home/user/phoneclaw/tests/data/regression_input.csv`, `regression_output_{256,512}.csv` | Kronos regression fixtures | Deterministic test vectors for a model gate. No instrument, no quantity, no owner. |
| `/home/user/phoneclaw/finetune_csv/data/HK_ali_09988_kline_5min_all.csv` | 5-min OHLCV history for 9988.HK | Price history. Says nothing about whether the fund owns any. |
| `/home/user/phoneclaw/examples/yuce/*.json` | Per-ticker analysis reports (000021, 002354, 300207, 600580) + `market_analysis_report.json` | Research output. A view is not a position. |
| `/home/user/phoneclaw/gold_dashboard/app.py` | Flask service fetching XAU/USD and serving Kronos forecasts | A forecast pipeline. No book behind it. |

The only files in the repo matching `position|holding|portfolio|exposure` are the agent
definition files in `.claude/agents/` — i.e. descriptions of roles, not records of risk.

Per my own guardrail, which I will not soften: **never approve or clear a position I cannot
measure. A book I cannot measure is a fail, not a pass.** I cannot tell you what breaks first
in your portfolio because I have not been shown your portfolio. Section 6 lists precisely what
I need to convert this into a real number, and what each input unlocks.

What follows in Sections 1–5 is a **HYPOTHETICAL** book, constructed by me, run end to end
through the framework so you can see the machinery work and judge whether it is the machinery
you want pointed at the real thing. Every position in it is invented. Do not cite any number
below as a fund exposure, a fund loss, or a fund limit utilisation.

---

## 1. The HYPOTHETICAL book — every line is an assumption

Built from this repo's actual subject matter: a gold-and-macro book of the kind the Kronos
gold dashboard (`gold_dashboard/app.py`, XAU/USD) would inform, with the China/HK research
sleeve implied by `examples/yuce/` and `finetune_csv/`. This is the natural fit, not a generic
equity portfolio — but it is still fiction.

### 1.1 Fund-level assumptions — ALL ASSUMED

| Parameter | Assumed value | Note |
| --- | --- | --- |
| NAV | USD 250,000,000 | Assumed |
| Base currency | USD | Assumed |
| Strategy | Discretionary macro, gold-centric, Kronos-informed | Assumed |
| Gross exposure | USD 390m = **156% of NAV** | Assumed |
| Net exposure | USD 290m = **116% of NAV** | Assumed |
| Long / short | USD 340m long / USD 50m short | Assumed |
| Balance-sheet leverage (assets/NAV) | **1.36x** | Assumed |
| Borrowings | USD 125m | Assumed |
| Unencumbered cash | USD 35m = **14.0% of NAV** | Assumed. This is the buffer that decides the outcome. |
| Initial margin posted (t0) | USD 47.2m | Assumed, built up in §4.2 |
| Financing rate at inception | SOFR + 65bp, SOFR = 0.05% | Assumed |
| Gold cost of carry at inception | ~0.3% p.a. (GOFO ≈ 0) | Assumed |
| Redemption terms | Quarterly, 45 days' notice, 25% investor-level gate | Assumed |
| Prime brokers | Two (PB1 metals/equity, PB2 futures FCM), **no cross-margining between them** | Assumed — and load-bearing, see §4.3 |

### 1.2 Positions — ALL ASSUMED

| Leg | Instrument | Notional (USDm) | Direction | Financing / margin basis | Assumed IM t0 (USDm) |
| --- | --- | --- | --- | --- | --- |
| A | XAU/USD unallocated spot + GLD | 110 | Long | PB1 financed, 20% haircut | 22.00 |
| B | COMEX gold futures (GC, front Dec) | 60 | Long | Exchange SPAN, 5.5% IM | 3.30 |
| C | Gold miners basket (GDX / GDXJ) | 45 | Long | PB1 Reg-T style, 25% | 11.25 |
| D | 10y TIPS, on-the-run, duration 8.5 | 50 | Long | Repo, 3% haircut | 1.50 |
| E | Short USD vs EUR/JPY 50/50, 3m fwds | 40 | Short USD | CSA IM 4% | 1.60 |
| F | China A sleeve: 000021, 002354, 300207, 600580 | 20 | Long | Cash / Stock Connect, unfinanced | 0.00 |
| G | 9988.HK (Alibaba) | 15 | Long | PB1, 30% | 4.50 |
| H | S&P 500 futures (ES) — macro hedge | 50 | **Short** | Exchange SPAN, 6% IM | 3.00 |
| | **Gross 390 · Net 290** | | | | **47.15** |

### 1.3 Model assumptions — ALL ASSUMED

| Assumption | Value | Basis |
| --- | --- | --- |
| Gold beta to 10y real yield | **-4.5% per +100bp** (stress-widened from -4.0% baseline) | Calibrated to 2022 realised: real +262bp, gold -11.8% peak-to-trough from Jan |
| Miner beta to gold | **2.2x**, plus 0.9 equity beta | GDX -46% peak-to-trough vs gold -22% = 2.1x realised |
| TIPS | Duration 8.5, convexity 85, CPI accretion +6.5% over horizon | Standard |
| EUR/JPY basket beta to real yields | -7.4% per +100bp | DXY +20% on +270bp |
| China/HK beta to global real yields | -2% per +100bp, plus large idiosyncratic | Idiosyncratic dominates (see §4.1) |
| Kronos forecasts | **Not used as a risk input** | See §5.3 — model risk |

---

## 2. The shock, defined concretely

I am replaying calendar 2022, compressed into a 9-month horizon, using actual realised
magnitudes as the reference. Two phases, because the path matters more than the endpoint.

### 2.1 Reference magnitudes — what 2022 actually did

| Variable | Start 2022 | Extreme | End 2022 |
| --- | --- | --- | --- |
| Fed funds target (upper) | 0.25% | 4.50% | 4.50% (**+425bp**, 7 hikes, four consecutive +75bp) |
| 10y nominal UST | 1.51% | 4.34% (21 Oct) | 3.88% |
| **10y TIPS real yield** | **-1.04%** | **+1.74%** (Oct/Nov) | **+1.58%** |
| DXY | 95.6 | **114.8 (28 Sep, +20%)** | 103.5 (+8%) |
| EURUSD | 1.137 | 0.9536 (Sep, **-16%**) | 1.0705 |
| USDJPY | 115.1 | 151.9 (Oct, **+32%**) | 131.1 |
| **Gold (XAU/USD)** | **1,829** | **2,070 (8 Mar, invasion) → 1,614 (26 Sep)** | **1,824 (flat on the year)** |
| GDX (miners) | ~31.1 | 40.9 (18 Apr) → 21.9 (26 Sep), **-46% peak-to-trough** | 30.5 |
| S&P 500 | 4,796 | 3,577 (12 Oct, **-25.4%**) | 3,839 (-18.1% TR) |
| US Agg bonds | — | — | **-13.0% — down alongside equities** |
| Stock/bond correlation | ~-0.3 | **flipped to ~+0.6** | The classic 60/40 hedge stopped working; 60/40 ≈ -17% |
| MOVE (rate vol) | ~75 | ~160 (Oct) | ~120 |
| CSI300 / 9988.HK | 4,918 / HK$118.9 | 3,495 (Oct) / **HK$60.05 (24 Oct, -49.5%)** | 3,872 / HK$85.5 |
| Gilt / LDI event | — | **30y gilt +130bp in 3 sessions, 23–28 Sep** — forced collateral liquidation | — |

### 2.2 What I am shocking, and by how much

| Factor | Shock applied | Phase |
| --- | --- | --- |
| **10y real yield** | **-1.00% → +1.74%, i.e. +274bp** | Primary driver. -20bp in Phase 1, +294bp in Phase 2. |
| Policy rate / financing base | **+425bp** over the horizon | Slow burn, hits carry not marks |
| DXY | **+20% to peak**, +8% at horizon end | Phase 2 |
| Gold | **+13% then -22%**: 1,829 → 2,070 → 1,614 → 1,824 | Both phases |
| Equity index | -25.4% to trough, with **two counter-trend rallies: +17% (16 Jun–16 Aug) and +14% (12 Oct–1 Dec)** | Both. The rallies are the point. |
| Equity/bond correlation | **-0.3 → +0.6** | Phase 2 |
| Rate vol (MOVE) → margin models | **75 → 160**, driving IM multipliers of 1.5–1.6x | Phase 2, T+1 to T+10 |
| China/HK idiosyncratic | -32% A-shares, -49.5% 9988 | Phase 2, Oct cluster |
| Traded volume in stress | **ADV halved** in single names; limit-down days assumed possible in the A sleeve | Applied throughout §4.4 |

I am **not** shocking: credit spreads (no credit in the hypothetical book), commodity ex-gold,
or counterparty default. Those are separate scenarios and their absence is a limitation, not
a comfort.

---

## 3. Mark-to-market result

### 3.1 P&L by leg — and the two numbers that matter

| Leg | Notional | **At the stress trough (Sep/Oct analogue)** | | **At horizon end (Dec analogue)** | |
| --- | ---: | ---: | ---: | ---: | ---: |
| | USDm | Return | P&L USDm | Return | P&L USDm |
| A Gold spot/GLD | 110 | -11.8% | **-12.98** | -0.3% | -0.33 |
| B GC futures | 60 | -11.8% | **-7.08** | -0.3% | -0.18 |
| C Miners GDX/GDXJ | 45 | -29.6% | **-13.32** | -1.9% | -0.85 |
| D 10y TIPS | 50 | -13.4% | **-6.70** | -12.2% | -6.10 |
| E Short USD vs EUR/JPY | 40 | -18.3% | **-7.32** | -9.0% | -3.60 |
| F China A sleeve | 20 | -32.0% | **-6.40** | -25.0% | -5.00 |
| G 9988.HK | 15 | -49.5% | **-7.42** | -28.1% | -4.22 |
| H Short ES (hedge) | -50 | -23.8% | **+11.90** | -20.0% | +10.00 |
| Financing drag | | | **-2.33** | | -3.10 |
| **TOTAL** | | | **-51.65 (-20.7% of NAV)** | | **-13.38 (-5.4% of NAV)** |

TIPS note: price -19.9% (duration/convexity on +274bp) offset by +6.5% CPI accretion and
carry = -13.4%. Long TIPS lost double digits in a year CPI peaked at 9.1%. Real duration beat
inflation accrual by a factor of three. **The inflation hedge was not an inflation hedge.**

### 3.2 The single most important observation in this document

> **The book does not lose money over 2022. It loses 5.4%. But it passes through -20.7% on
> the way, and at -20.7% it is liquidated. The failure mode is not loss. It is being stopped
> out of a position that round-trips.**

Gold ended 2022 **flat** (1,829 → 1,824). Miners ended **-1.9%**. Together those are 42% of
the hypothetical gross and they cost 0.5% of NAV over the full year — and USD 26.3m, 10.5% of
NAV, at the September low. Every mechanism in §4 is a mechanism for converting the second
number into a realised one.

### 3.3 Factor decomposition — how much is the idea and how much is one macro variable

Real-yield DV01 by leg (P&L per +1bp in 10y real):

| Leg | DV01 (USD k/bp) |
| --- | ---: |
| A Gold spot/GLD | -49.5 |
| B GC futures | -27.0 |
| C Miners (2.2x gold) | -44.6 |
| D TIPS (duration) | -42.5 |
| E Short USD | -29.6 |
| F/G China + HK | -7.0 |
| **Gross negative DV01** | **-200.2** |
| H Short ES (offset) | +45.0 |
| **Net book DV01** | **-155.2 k/bp** |

- Net -155.2k/bp on 250m NAV = **6.2bp of NAV lost per 1bp rise in the 10y real yield.**
- +274bp × 155.2k = **-42.5m, which is 82% of the -51.65m stress loss.**
- **Only 18% of the loss is idea. 82% is one number on one screen.**

This is not a gold book, a miners book, a TIPS book and an FX book. It is **one position in
short 10y real yields, expressed eight ways**, of which five ways cost financing and three
cost nothing to hedge directly. The whole of the gold, miner, TIPS and short-dollar complex —
USD 305m, 78% of gross — loads the same sign on the same factor.

If the PM wants this exposure, the cheap, liquid, margin-efficient expression is a TIPS or
real-rate futures position sized to the intended DV01, not USD 305m of gross across four asset
classes each paying a financing spread and each posting its own initial margin. **The current
structure pays roughly USD 47m of initial margin and USD 125m of borrowings to hold a view
that a single instrument would express.** That is the decomposition finding and it stands
independent of the stress.

---

## 4. What breaks first — ordered by time-to-bite, not by size

The largest loss is the miners at -13.3m. **The miners are not what breaks first**, because an
unrealised mark is something you can sit on. What breaks first is whatever forces a cash
payment or a forced sale. In order:

### 4.1 T+0 to T+3 — Variation margin. This is the answer.

**Legs B (GC futures) and E (FX forwards) settle variation margin in cash, daily, same-day or
T+1, with no discretion.** Over the acute window they consume:

| | Cash out (USDm) |
| --- | ---: |
| B GC futures VM | -7.08 |
| E FX forward VM / CSA | -7.32 |
| **Total cash VM outflow** | **-14.40** |

That is **41% of the entire USD 35m cash buffer**, paid out in wires, while the book still
looks like it is merely down on paper. The worst 3-day cluster — the 21 Sep FOMC (+75bp) or
the 26–28 Sep gilt/LDI dislocation — is roughly **-4 to -5m in 72 hours**.

Note what is *not* here: leg C, the biggest loser at -13.3m, produces **zero cash outflow**
because it is a financed long, not a derivative. The order of pain and the order of loss are
different, and confusing them is how funds die.

**Verdict on "what breaks first": futures and FX variation margin, within 72 hours of the
first sharp real-yield leg, on legs that account for only 26% of gross and 28% of the loss.**

### 4.2 T+1 to T+10 — Procyclical margin re-set, arriving on top of the VM

Margin models are VaR-based. MOVE 75 → 160 means initial margin rises mechanically at exactly
the moment you can least afford it. CME raised gold IM during the March 2022 invasion vol.
Prime brokers re-cut haircuts on the worst-realised-vol positions first — which here are the
miners and the HK line.

| Leg | IM t0 (USDm) | Stressed notional | Stressed rate | IM stressed (USDm) | Δ |
| --- | ---: | ---: | ---: | ---: | ---: |
| A Gold spot/GLD | 22.00 | 97.0 | 20% → 28% | 27.16 | **+5.16** |
| B GC futures | 3.30 | 52.9 | 5.5% → 8.8% | 4.66 | +1.36 |
| C Miners | 11.25 | 31.7 | 25% → **40%** | 12.68 | +1.43 |
| D TIPS repo | 1.50 | 43.3 | 3% → 6% | 2.60 | +1.10 |
| E FX CSA | 1.60 | 40.0 | 4% → 6% | 2.40 | +0.80 |
| G 9988.HK | 4.50 | 7.6 | 30% → **50%** | 3.79 | -0.71 |
| H Short ES | 3.00 | 37.4 | 6% → 9.6% | 3.59 | +0.59 |
| **Total** | **47.15** | | | **56.88** | **+9.73** |

**+USD 9.73m of additional initial margin demanded while the book is down 51.65m.** Note the
perverse detail at leg G: the HK line's IM *falls* by 0.71m only because the position has lost
half its value. Margin relief by way of destruction is not relief.

### 4.3 T+3 to T+10 — the cash bridge, and why the broker structure decides survival

| | With hedge intact and cash fungible | Hedge cut, or offset trapped at PB2 |
| --- | ---: | ---: |
| Opening unencumbered cash | 35.00 | 35.00 |
| VM out, leg B | -7.08 | -7.08 |
| VM out, leg E | -7.32 | -7.32 |
| VM in, leg H (short ES) | **+11.90** | **0.00** |
| Additional IM | -9.73 | -9.73 |
| Financing accrual | -2.33 | -2.33 |
| **Closing cash** | **+20.44 (8.2% of NAV)** | **+8.54 (3.4% of NAV)** |

Two things break this bridge and neither requires a bigger market move:

1. **Timing and fungibility.** The gold trough (26 Sep) and the equity trough (12 Oct) are
   **16 days apart**. The hedge gain at leg H is not available on the day the gold VM call
   lands. With two brokers and no cross-margining (assumption §1.1), excess at PB2 requires a
   withdrawal request and settles T+1 at best, and FCMs slow-walk excess withdrawals in a vol
   event. **You are long a hedge that pays in the wrong account, in the wrong week.**
2. **Hedge abandonment.** See §4.4.

### 4.4 T+5 to T+20 — the correlated case: one factor, and a hedge under maximum pressure to cut

**This is the section that matters.** The naive correlated scenario — "everything falls
together" — is not quite what 2022 did to a book like this. What it did was worse, because it
is not visible in a correlation matrix.

**(a) The correlation is not between positions. It is between positions and one factor.**
Six of eight legs, 78% of gross, carry negative real-yield DV01. There is no diversification
to lose in a stress because there was none to begin with — the book is one bet. A conventional
pairwise correlation report on eight legs would show gold vs TIPS, gold vs FX, miners vs TIPS
all at moderate correlation in calm markets and would not flag this. The DV01 table in §3.3
flags it instantly. **Run the factor decomposition, not the correlation matrix.**

**(b) The one leg that works is the one the PM is most likely to cut.** Leg H, short ES, is the
only positive contributor — it earns +11.90m and it is the entire reason the cash bridge in
§4.3 holds. But look at what it does on the way:

- **16 Jun → 16 Aug 2022: S&P +17%.** The short gives back roughly **-8.5m** of open profit
  over nine weeks. Gold is simultaneously choppy-to-lower and real yields are grinding up, so
  the rest of the book is *also* bleeding. The hedge is not offsetting; it is adding to the
  drawdown for two months.
- **12 Oct → 1 Dec 2022: S&P +14%.** It does it again, immediately after the trough.

A macro hedge that loses money for two months during the drawdown it is supposed to hedge is
a hedge with a very short political life. The realistic path is that leg H is cut or halved in
August, on entirely reasonable-sounding grounds ("the hedge isn't working, it's costing us"),
**five weeks before the September event where it was the only thing standing between the fund
and a forced liquidation.** The right-hand column of §4.3 is not a tail scenario. It is the
modal outcome of ordinary human behaviour under drawdown.

**(c) The correlated failure, assembled.** Late September analogue, in sequence:

1. Real yields +100bp in six weeks. Legs A, B, C, D, E all mark down together — 47.4m of the
   51.7m loss.
2. Legs B and E convert their share into **14.4m of same-day cash outflow**.
3. Margin models re-price vol. **+9.7m of additional IM**, T+1.
4. The China/HK cluster fires on its own clock (Oct Party Congress; 9988 -49.5%) — **13.8m,
   uncorrelated with the rate driver, arriving in the same fortnight**. This is the piece that
   is genuinely independent, and it lands anyway. Two unrelated risks do not need to be
   correlated to be simultaneous.
5. The hedge that offsets is either cut (August) or trapped at the other broker (16-day
   timing gap).
6. Cash at 8.54m, 3.4% of NAV, against a 20.7% drawdown and a broker asking for more.
7. **You must raise cash in three days.** Section 4.5 says what you can actually sell.

**(d) Correlated-case loss: -51.65m, -20.7% of NAV.** Against a 10-day 99% VaR of 9.20m
(§5.2), that is **5.6x VaR**. Nothing in the VaR framework sees this coming, and §5.2 explains
why that is a property of the book, not a bug in the arithmetic.

### 4.5 T+10 to T+30 — liquidity asymmetry forces exactly the wrong sale

Days to exit, at 20% participation, with **ADV halved** in single names (ETF and futures volume
generally *rises* in stress — a genuine offset I am crediting):

| Leg | Notional | Normal ADV | Stressed assumption | Days to exit, normal | **Days to exit, stressed** |
| --- | ---: | --- | --- | ---: | ---: |
| A Gold spot/GLD | 110 | XAU OTC ~$130bn; GLD ~$1.6bn | Deepest market; volume rises | 0.5 | **1.0** |
| B GC futures | 60 | ~$40bn | Volume rises | 0.2 | **0.3** |
| C Miners GDX/GDXJ | 45 | GDX ~$700m, GDXJ ~$120m | ETF volume rises, GDXJ impact widens | 0.4 | **0.7** |
| D 10y TIPS | 50 | On-the-run liquid | **TIPS bid-ask widened 3–5x in 2022; Oct 2022 Treasury depth at post-2020 lows** | 1.0 | **3.0** |
| E FX fwds EUR/JPY | 40 | Deepest market on earth | Minimal impairment | 0.2 | **0.5** |
| F **China A sleeve** | 20 | ~$30–80m/name, 4 mid/small caps across SZ/ChiNext/SH | **ADV halves; ±10% (±20% ChiNext) daily limit boards; limit-down = zero exitable volume** | 3.0 | **8–15, unbounded on limit-down** |
| G 9988.HK | 15 | ~US$600m | Liquid; the risk is gap price, not volume | 0.2 | **0.5** |
| H Short ES | 50 | Enormous | Volume rises | 0.2 | **0.3** |

**Weighted: ~92% of the book is 1-day liquid under stress. That does not save you.** Two reasons:

1. **Redemption liquidity is not the binding constraint. Intraday margin liquidity is.**
   Quarterly/45-day redemption terms against a 1-day-liquid book looks immaculate on a DDQ.
   Margin calls settle same-day. A book can be 100% one-day-liquid and still fail a 9am wire,
   because settlement of the sale lands T+1/T+2 and the call is due now. **Liquidity ladders
   built against redemption terms systematically miss the risk that actually kills funds.**
2. **You sell what trades, not what you want to sell.** To raise cash in 72 hours you cannot
   touch leg F (8–15 days, possibly limit-locked) and you will not dump leg G into a -49.5%
   gap. So you sell **gold and GLD** — the most liquid, best-quality, highest-conviction leg —
   at the 26 September low of 1,614.

   **Selling USD 60m of gold at the low to fund a margin call converts leg A's -0.3% year into
   a locked -11.8%, permanently, on the leg that was about to round-trip to flat.** Forced
   deleveraging is path-dependent and it always eats the good collateral first, because good
   collateral is the only collateral that trades. **This is the true cost of the shock, and it
   appears nowhere in the mark-to-market table.**

### 4.6 T+30 to T+180 — financing. Last to bite, first to be forgotten.

| | At inception | At horizon end |
| --- | ---: | ---: |
| Borrowings USD 125m at SOFR+65bp | 0.88% → ~1.1m/yr | 4.95% → ~6.2m/yr |
| Gold cost of carry on 110m (GOFO ~0 → +4%) | ~0.3m/yr | ~4.7m/yr |
| **Total run-rate** | **~1.4m/yr (0.6% of NAV)** | **~10.9m/yr (4.4% of NAV)** |

A **+3.8% of NAV per annum** swing in carry, which **continues after the marks recover**. The
+425bp policy move does not just reprice the assets — it reprices the right to hold them. A
book that breaks even on marks now needs +4.4% a year just to stand still. Over 2023 that is
the difference between a flat fund and a fund with an 8% two-year hole.

### 4.7 T+60 to T+270 — drawdown gate and LP behaviour

-20.7% at the trough breaches the assumed -15% soft drawdown gate. With 45 days' notice and
quarterly dealing, redemptions noticed in October settle **31 December 2022 / 31 March 2023** —
i.e. **after gold has recovered to 1,824 and while it is on its way to 2,050**. You sell the
recovery to pay for the panic. Every structural feature of the fund is procyclical in the same
direction.

### 4.8 Summary: the ordering

| # | What breaks | When | Trigger | Size |
| --- | --- | --- | --- | ---: |
| **1** | **Futures + FX variation margin (legs B, E)** | **T+0 to T+3** | **Cash, same-day, no discretion** | **-14.4m cash** |
| 2 | Procyclical IM / haircut re-set | T+1 to T+10 | MOVE 75 → 160; PB haircut re-cut | -9.7m cash |
| 3 | Hedge abandonment / cross-broker timing gap | T+5 to T+20 (decision made ~5 weeks earlier) | Two counter-trend equity rallies | -11.9m of offset |
| 4 | Forced sale of the best asset at the low | T+10 to T+30 | Only gold trades in size | Permanent, ~-7m of foregone round-trip |
| 5 | Financing / carry repricing | T+30 to T+180 | +425bp policy, GOFO 0 → 4% | -3.8% NAV p.a. run-rate |
| 6 | Drawdown gate → redemptions settling into the recovery | T+60 to T+270 | -20.7% breach of -15% soft gate | Structural |
| — | *Largest single mark-to-market loss (miners, -13.3m)* | *never forces anything* | *unrealised, financed long* | *-13.3m* |

---

## 5. Limits, VaR, and verdict

### 5.1 Limit utilisation — limits ALSO ASSUMED (no limit framework exists in this repo either)

| Limit | Assumed limit | Utilisation | Status |
| --- | --- | --- | --- |
| Gross exposure | ≤ 200% NAV | 156% | 78% — pass |
| Net exposure | ≤ 150% NAV | 116% | 77% — pass |
| Balance-sheet leverage | ≤ 2.0x | 1.36x | 68% — pass |
| **Single-factor risk share** | **≤ 40% of book risk** | **82% (10y real yield)** | **205% — HARD BREACH** |
| **Asset-class concentration (gold complex A+B+C)** | **≤ 60% NAV** | **86% NAV (215m)** | **143% — HARD BREACH** |
| Single equity name | ≤ 8% NAV | 6.0% (9988) | 75% — pass |
| Country: China + HK | ≤ 15% NAV | 14.0% | 93% — **AMBER** |
| Illiquid (>5d stressed exit) | ≤ 10% NAV | 8.0% (China A) | 80% — **AMBER** |
| Unencumbered cash | ≥ 10% NAV | 14.0% | pass, but only **1.4x** covered vs a 24.1m stress call |
| 10-day 99% VaR | ≤ 5% NAV | 3.68% | 74% — pass *(see 5.2)* |
| **Prescribed stress loss** | **≤ 15% NAV** | **20.7%** | **138% — HARD BREACH** |
| Drawdown soft gate | -15% | -20.7% at trough | **BREACHED** |
| Drawdown hard gate | -25% | -20.7% at trough | 83% — not breached, but inside the margin of error |

### 5.2 VaR and expected shortfall — and why they are misleading here

| Measure | Value | % NAV |
| --- | ---: | ---: |
| Daily P&L volatility | 1.25m | 0.50% |
| Annualised volatility | | 7.9% |
| 1-day 99% VaR | 2.91m | 1.16% |
| 10-day 99% VaR | 9.20m | 3.68% |
| 10-day 99% ES (empirical 1.6x tail multiple) | 14.72m | 5.89% |
| **Realised stress loss** | **51.65m** | **20.7%** |
| **Stress / 10-day VaR** | | **5.6x** |

**The 10-day 99% VaR understates this book's actual stress loss by a factor of 5.6, and this
is not a calibration error — it is structural.** VaR measures a shock. The 2022 rate move was
not a shock; it was a **nine-month trend with autocorrelated daily moves in one direction**.
Square-root-of-time scaling assumes i.i.d. increments and is simply the wrong scaling law for
a regime change. Any book whose dominant risk is a slow directional regime shift will pass VaR
every single day on the way to a 20% drawdown.

**Do not run this book on VaR.** Run it on factor DV01 (§3.3) and prescribed stress (§3.1),
with VaR as a secondary tripwire only.

### 5.3 Model risk — Kronos

`gold_dashboard/app.py` serves Kronos forecasts on XAU/USD (Kronos-mini / small / base). Per
my guardrail: **no model reaches production risk use without a documented model card, known
failure regimes from `quant-researcher`, and a monitoring plan.** None of the three exists in
this repo. Additionally:

- Kronos is trained on historical market data. The 2021→2022 real-rate regime change is, by
  construction, the class of event on which an autoregressive sequence model trained on a
  low-rate sample will be least reliable. `app.py` has a `MODEL_AVAILABLE = False` fallback
  that silently degrades to "simulation mode" — **a risk system that silently substitutes
  simulated output for model output is a control failure in its own right**, independent of
  the model's accuracy.
- Kronos output is a distribution over sampled paths. Any risk use must report model variant,
  context length, sampling parameters and path count with every number.

**Kronos is excluded from this assessment as a risk input.** It may inform the view; it may
not size the position or set the limit until the model card exists.

### 5.4 Verdict

**On the actual mandate: FAIL — unmeasurable.**
No book of record. No exposure data. No limit framework. I decline to certify, clear, or
opine on the risk of a portfolio that has not been presented to me. This is not a procedural
objection; it is the guardrail. **"Model not available" is a fail, not a pass**, and so is
"book not available."

**On the HYPOTHETICAL book: FAIL — three hard limit breaches.**

| Breach | Limit | Actual |
| --- | --- | --- |
| Single-factor risk concentration | ≤ 40% | 82% |
| Gold-complex asset-class concentration | ≤ 60% NAV | 86% NAV |
| Prescribed 2022 stress loss | ≤ 15% NAV | 20.7% NAV |

**Conditions on which the hypothetical book would be re-presented for sign-off:**

| # | Condition | Rationale |
| --- | --- | --- |
| C1 | **Cut net real-yield DV01 from -155k/bp to a hard cap of -90k/bp** (≤ 3.6bp of NAV per bp). Express the residual through the single most margin-efficient instrument, not four asset classes. | §3.3 — 82% one factor |
| C2 | **Gold complex (A+B+C) capped at 60% of NAV**, i.e. reduce from 215m to 150m. Miners capped at 25% of the gold complex given 2.2x stress beta. | Concentration breach |
| C3 | **Unencumbered cash floor raised from 10% to 20% of NAV (50m)**, sized to cover 2.0x the modelled stress call of 24.1m. Non-negotiable and tested monthly. | §4.3 — the bridge |
| C4 | **The equity hedge (leg H) is a risk-mandated position, not a PM discretionary one.** It may not be cut, reduced, or rolled down without written CRO sign-off. Cutting it moves the book straight to the right-hand column of §4.3. | §4.4(b) — modal failure |
| C5 | **Cross-margining or a committed intraday liquidity line across PB1/PB2 before the futures book exceeds 40m notional.** The 16-day timing gap between the gold and equity troughs is otherwise uncollateralisable. | §4.3 |
| C6 | **China A sleeve capped at 5% of NAV** (from 8%) until a limit-down-day liquidity test is documented. Model exit at zero volume, not at halved volume. | §4.5 |
| C7 | **Financing sensitivity reported monthly**: P&L impact of +100bp on financing and on GOFO, as a standing line in the risk pack. | §4.6 |
| C8 | **Kronos excluded from risk and sizing** pending model card, failure regimes from `quant-researcher`, and a monitoring plan. Fix the silent `MODEL_AVAILABLE = False` degradation regardless. | §5.3 |
| C9 | **Retire VaR as the primary risk metric for this book.** Primary: factor DV01 and prescribed stress. Review date: 30 days. | §5.2 |

Any relaxation of C1–C6 requires a **written, time-limited override from `cio`, copied to
`internal-auditor`, recorded as an exception and not as a new limit.**

### 5.5 Reverse stress — what actually breaks the fund, rather than merely hurting it

Define "break" as either (a) unencumbered cash ≤ 0, forcing broker-directed liquidation, or
(b) NAV drawdown ≥ -25%, triggering the hard gate and the redemption cascade.

At the full 2022 replay (+274bp real), the hypothetical book is at **-20.7% and 8.5m of cash**
in the hedge-cut case. It hurts. It does not quite break. Working backwards:

| Path to break | Required |
| --- | --- |
| **Rates alone** | **10y real from -1.0% to ~+2.8%, i.e. +380bp in under 9 months** — roughly 1.4x what 2022 delivered |
| **Leverage alone** | Same 2022 shock at **gross ≥ 2.2x** instead of 1.56x (loss scales to -29%) |
| **Hedge alone** | Same 2022 shock with **leg H cut in August** → cash 8.5m, one further -3% week is terminal |
| **Margin model alone** | Same 2022 shock with **broker IM multipliers at 2.0x instead of 1.6x** → additional IM ~16m, cash ~2m |
| **Concentration alone** | Same 2022 shock with **China sleeve > 12% of NAV** → illiquid loss + zero exitable volume |

**The finding, stated plainly:**

> The fund is not 380bp of real yield away from ruin. It is **one PM decision** away
> (cut the hedge in August), **one broker email** away (routine haircut re-cut in a vol
> event), or **one prior sizing choice** away (gross at 2.2x, which many macro funds run).
> Each of those three is individually unremarkable, individually defensible, and requires
> **no additional market move whatsoever**.
>
> **Reverse stress that concludes "we need a 1.4x-2022 event to fail" is the wrong answer.
> The correct answer is that at a 1.0x-2022 event the fund fails if any one of three ordinary
> things is also true.** The distance to ruin is not measured in basis points. It is measured
> in decisions.

---

## 6. What I need to turn this into a real number

Every item below is a specific input. None of this exists in the repo today.

| # | Input required | Format | What it unlocks | Without it |
| --- | --- | --- | --- | --- |
| 1 | **Position-level book of record**: instrument identifier (ISIN/CUSIP/RIC/exchange+contract), quantity, direction, trade date, entry price, current mark, mark source, currency of denomination, currency of settlement, custodian, executing and clearing broker | One CSV/Parquet, one row per lot, daily snapshot | **Everything.** Gross, net, single-name, sector, country, and the entire §3 table | No exposure, no VaR, no stress. Hard stop. |
| 2 | **NAV and capital account history**, monthly, with subscriptions and redemptions | CSV, monthly | Denominator for every limit; drawdown history; return volatility | Every "% of NAV" number is undefined |
| 3 | **Financing terms per position**: rate basis and spread, reset frequency, term vs overnight, recall/rehypothecation rights, stock-borrow rate and availability for shorts | Per-lot, from PB statements | §4.6 carry, the +425bp policy transmission, short-squeeze risk | Financing drag is a guess; the slowest-biting risk stays invisible |
| 4 | **Margin methodology per broker**: current IM by position, the methodology (SPAN/VaR/Reg-T/house), historical IM multipliers observed in past vol events, add-on and concentration-charge rules, cross-margining agreements, intraday call mechanics and cut-off times | PB margin statements + the actual margin schedule in the PB agreement | **§4.2 and §4.3 — the thing that breaks first.** Converts "assumed 1.6x multiplier" into a measured one | The #1 finding of this report remains an assumption |
| 5 | **Cash and collateral ladder**: unencumbered cash by currency and entity, eligible collateral, haircut schedule, committed vs uncommitted credit lines with drawdown conditions | Daily treasury report | Whether the §4.3 bridge actually holds | Survival is untested |
| 6 | **Redemption terms and LP concentration**: notice period, dealing frequency, gates, lock-ups, side letters, top-10 LP share, key-man and drawdown triggers in the LPA | From `legal-counsel` / `investor-relations` | §4.7 — liability-side liquidity against asset-side days-to-exit | Liquidity risk is half-measured |
| 7 | **ADV and market-depth history per instrument**, including the 2020 and 2022 stress windows, plus realised bid-ask | Vendor time series, 5y | §4.5 days-to-exit calibrated on observed stress volume rather than an assumed 50% haircut | Liquidity horizon is a rule of thumb |
| 8 | **Factor model / risk-factor returns history**: 10y real yield, DXY and crosses, gold, gold vol, MOVE, equity indices, China/HK — daily, 15y | CSV or vendor feed | §3.3 DV01s from regression rather than assumption; §5.2 VaR/ES; the correlation flip | Betas are judgement calls |
| 9 | **Counterparty exposure**: net MTM by counterparty, CSA thresholds and minimum transfer amounts, collateral held vs posted, credit quality | Trade ops / `prime-broker-liaison` | Counterparty and wrong-way risk, entirely absent from this document | An unassessed risk class |
| 10 | **The house limit framework as actually approved**: the real limits, who approved them, when they were last reviewed, and the current exception log | Board/IC minutes | §5.1 becomes utilisation against real limits rather than my invented ones | I am marking homework I set myself |
| 11 | **Kronos model card**: training window, known failure regimes from `quant-researcher`, backtest including 2022, monitoring plan, and remediation of the silent `MODEL_AVAILABLE = False` fallback in `gold_dashboard/app.py` | Doc + code fix | §5.3 — permits Kronos as a risk or sizing input | Kronos stays excluded |

**Minimum viable set to produce a genuine "what breaks first" answer: items 1, 4, and 5.**
Position file, margin methodology, and cash ladder. With those three I can measure the actual
cash-out path under a prescribed shock in about a day, which is the question you asked. Items
2, 3, 6 and 7 make it a full assessment. The rest complete the framework.

---

**Handoffs:**
`portfolio-manager` — C1/C2/C6 resizing, and the §3.3 finding that the same view is available
at a fraction of the gross · `cio` — C4 (hedge becomes risk-mandated, removing PM discretion)
requires a decision, and any override of C1–C6 must be written, time-limited and copied to
`internal-auditor` · `treasury-collateral-manager` — C3 cash floor, C5 cross-margining, and
input items 4 and 5 · `prime-broker-liaison` — margin schedules and cross-margining
feasibility · `quant-researcher` — C8 Kronos failure regimes · `head-of-technology` — the
silent simulation-mode fallback in `gold_dashboard/app.py` · `internal-auditor` — this
document is logged as an exception: **assessment could not be performed for want of a book of
record.**

*No live execution. No orders. All analysis on a stated hypothetical book, labelled as such
throughout.*
