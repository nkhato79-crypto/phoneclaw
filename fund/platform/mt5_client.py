#!/usr/bin/env python3
"""MetaTrader 5 connection layer.

Thin wrapper over the `MetaTrader5` package. Two jobs: read the account (which
gives the fund the book of record it otherwise lacks) and send orders (which only
`autotrade.py` does, and only after `risk_gate.py` has cleared them).

Nothing in this module decides *whether* to trade. It reports state and executes
instructions. Keeping the decision out of the connection layer is what makes the
gate impossible to bypass by accident.

PLATFORM: the MetaTrader5 package is Windows-only and requires a running MT5
terminal on the same machine. It will not import on Linux or macOS. See
fund/platform/README.md for the deployment shape.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:  # not Windows, or terminal not installed
    mt5 = None
    MT5_AVAILABLE = False

# Tags every order this system sends, so its fills are distinguishable from
# anything placed by hand in the terminal. Never reuse this for manual trades.
MAGIC = 770126

RETCODE_DONE = 10009


class MT5Error(RuntimeError):
    """Raised when the terminal refuses a call or is not connected."""


@dataclass
class Account:
    login: int
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    leverage: int
    is_live: bool          # trade_mode == REAL. The whole system keys off this.
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    ticket: int
    symbol: str
    direction: str         # "long" | "short"
    volume: float          # lots
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float          # unrealised, account currency
    swap: float
    contract_size: float
    notional: float        # volume * contract_size * price_current
    magic: int
    opened_at: str
    ours: bool             # magic == MAGIC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    comment: str
    ticket: int | None = None
    price: float | None = None
    volume: float | None = None
    request: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class MT5Client:
    """Connected session against one MT5 account.

    Use as a context manager so the terminal link is always shut down:

        with MT5Client(login=..., password=..., server=...) as c:
            acct = c.account()
    """

    def __init__(self, login: int | None = None, password: str | None = None,
                 server: str | None = None, terminal_path: str | None = None,
                 timeout_ms: int = 30_000):
        if not MT5_AVAILABLE:
            raise MT5Error(
                "the MetaTrader5 package is not importable. It is Windows-only and "
                "needs a running MT5 terminal on the same machine — see "
                "fund/platform/README.md"
            )
        self._login = login
        self._password = password
        self._server = server
        self._path = terminal_path
        self._timeout = timeout_ms
        self._connected = False

    # ---------------------------------------------------------------- session

    def __enter__(self) -> "MT5Client":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    def connect(self) -> None:
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._path:
            kwargs["path"] = self._path
        if self._login:
            kwargs.update(login=int(self._login), password=self._password,
                          server=self._server)

        if not mt5.initialize(**kwargs):
            raise MT5Error(f"initialize failed: {mt5.last_error()}")

        # initialize() with credentials already logs in; the explicit login is for
        # the case where the terminal was already up on a different account.
        if self._login and not mt5.login(int(self._login), password=self._password,
                                         server=self._server):
            mt5.shutdown()
            raise MT5Error(f"login failed for {self._login}@{self._server}: {mt5.last_error()}")

        self._connected = True

    def shutdown(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False

    def _require(self) -> None:
        if not self._connected:
            raise MT5Error("not connected — call connect() or use the context manager")

    # ------------------------------------------------------------------- read

    def account(self) -> Account:
        self._require()
        a = mt5.account_info()
        if a is None:
            raise MT5Error(f"account_info failed: {mt5.last_error()}")
        return Account(
            login=a.login, server=a.server, currency=a.currency,
            balance=a.balance, equity=a.equity, margin=a.margin,
            margin_free=a.margin_free, margin_level=a.margin_level,
            leverage=a.leverage,
            is_live=(a.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL),
            as_of=_now(),
        )

    def positions(self, symbol: str | None = None) -> list[Position]:
        self._require()
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if raw is None:
            raise MT5Error(f"positions_get failed: {mt5.last_error()}")

        out: list[Position] = []
        for p in raw:
            info = mt5.symbol_info(p.symbol)
            contract = info.trade_contract_size if info else 1.0
            out.append(Position(
                ticket=p.ticket, symbol=p.symbol,
                direction="long" if p.type == mt5.POSITION_TYPE_BUY else "short",
                volume=p.volume, price_open=p.price_open, price_current=p.price_current,
                sl=p.sl, tp=p.tp, profit=p.profit, swap=p.swap,
                contract_size=contract,
                notional=p.volume * contract * p.price_current,
                magic=p.magic,
                opened_at=dt.datetime.fromtimestamp(p.time, dt.timezone.utc).isoformat(timespec="seconds"),
                ours=(p.magic == MAGIC),
            ))
        return out

    def tick(self, symbol: str) -> tuple[float, float]:
        """(bid, ask) — raises rather than returning stale or empty quotes."""
        self._require()
        if not mt5.symbol_select(symbol, True):
            raise MT5Error(f"symbol {symbol} not selectable: {mt5.last_error()}")
        t = mt5.symbol_info_tick(symbol)
        if t is None or (t.bid == 0 and t.ask == 0):
            raise MT5Error(f"no tick for {symbol}: {mt5.last_error()}")
        return t.bid, t.ask

    def symbol(self, name: str):
        self._require()
        info = mt5.symbol_info(name)
        if info is None:
            raise MT5Error(f"unknown symbol {name}")
        return info

    def realised_pnl(self, since: dt.datetime) -> float:
        """Closed-trade P&L since `since`, this system's deals only.

        The daily loss limit reads this, so it must count swap and commission —
        a limit that ignores costs is a limit that lets you bleed out slowly.
        """
        self._require()
        deals = mt5.history_deals_get(since, dt.datetime.now(dt.timezone.utc))
        if deals is None:
            return 0.0
        return sum(d.profit + d.swap + d.commission for d in deals if d.magic == MAGIC)

    # ------------------------------------------------------------------ write

    def market_order(self, symbol: str, direction: str, volume: float,
                     sl: float, tp: float | None = None,
                     deviation_points: int = 20, comment: str = "fund-desk") -> OrderResult:
        """Send a market order. Caller must have cleared it through risk_gate.

        `sl` is required, not optional. An autonomous system without a stop on
        every position has no bounded loss per trade, and the daily loss limit
        then becomes the only thing between it and the account.
        """
        self._require()
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be long|short, got {direction!r}")
        if not sl or sl <= 0:
            raise ValueError("a stop-loss is mandatory on every order")

        info = self.symbol(symbol)
        bid, ask = self.tick(symbol)
        price = ask if direction == "long" else bid

        # Broker-side minimum stop distance; a closer stop is rejected outright.
        min_stop = info.trade_stops_level * info.point
        if min_stop and abs(price - sl) < min_stop:
            return OrderResult(False, -1,
                               f"stop {sl} inside broker minimum distance {min_stop:.5f} from {price}",
                               request={"symbol": symbol, "sl": sl, "price": price})

        volume = self._round_volume(info, volume)
        if volume <= 0:
            return OrderResult(False, -1, "volume rounds to zero at broker step",
                               request={"symbol": symbol, "volume": volume})

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "deviation": deviation_points,
            "magic": MAGIC,
            "comment": comment[:31],          # MT5 truncates past 31 chars
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        if tp:
            request["tp"] = tp

        r = mt5.order_send(request)
        if r is None:
            return OrderResult(False, -1, f"order_send returned None: {mt5.last_error()}",
                               request=request)
        return OrderResult(
            ok=(r.retcode == RETCODE_DONE), retcode=r.retcode, comment=r.comment,
            ticket=getattr(r, "order", None), price=getattr(r, "price", None),
            volume=getattr(r, "volume", None), request=request,
        )

    def close_position(self, ticket: int, deviation_points: int = 20,
                       comment: str = "fund-desk close") -> OrderResult:
        """Close one position by ticket, whoever opened it.

        Deliberately not restricted to our own magic: the kill switch and the
        daily-loss flatten must be able to close everything on the account.
        """
        self._require()
        found = [p for p in mt5.positions_get() or [] if p.ticket == ticket]
        if not found:
            return OrderResult(False, -1, f"position {ticket} not found")
        p = found[0]

        info = self.symbol(p.symbol)
        bid, ask = self.tick(p.symbol)
        closing_long = p.type == mt5.POSITION_TYPE_BUY

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL if closing_long else mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": bid if closing_long else ask,
            "deviation": deviation_points,
            "magic": MAGIC,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        r = mt5.order_send(request)
        if r is None:
            return OrderResult(False, -1, f"order_send returned None: {mt5.last_error()}",
                               request=request)
        return OrderResult(ok=(r.retcode == RETCODE_DONE), retcode=r.retcode,
                           comment=r.comment, ticket=ticket,
                           price=getattr(r, "price", None), request=request)

    def close_all(self, reason: str) -> list[OrderResult]:
        """Flatten the account. Used by the kill switch and the loss limit."""
        return [self.close_position(p.ticket, comment=f"flat: {reason}")
                for p in self.positions()]

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _round_volume(info, volume: float) -> float:
        """Snap to the broker's lot step and clamp to its min/max."""
        step = info.volume_step or 0.01
        v = round(round(volume / step) * step, 8)
        if v < info.volume_min:
            return 0.0
        return min(v, info.volume_max)

    @staticmethod
    def _filling_mode(info) -> int:
        """Pick a filling mode the symbol actually supports.

        Brokers differ here and a wrong mode is rejected with 10030, which reads
        like a connectivity problem and isn't.
        """
        modes = info.filling_mode
        if modes & 1:            # SYMBOL_FILLING_FOK
            return mt5.ORDER_FILLING_FOK
        if modes & 2:            # SYMBOL_FILLING_IOC
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN
