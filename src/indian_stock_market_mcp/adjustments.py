"""Split and bonus adjustment of bhavcopy prices.

Exchange bhavcopy prices are never adjusted: on the day a stock splits or
issues bonus shares its price drops overnight and nothing in the file says why.
Left alone, that looks like a crash, a fake 52-week high or a huge drawdown.

The method, run once when data is imported and saved beside the dataset:

1. Find candidate days: a close at least 15% below the previous close. Every
   split and every bonus down to 1:5 shows up this way.
2. Ask Yahoo (through yfinance) for its closes on the day before and the day
   of each candidate. Yahoo's closes are adjusted for splits and bonuses, so
   (our close / Yahoo's close) is a constant before the event and a different
   constant after it. The ratio of the two constants is the adjustment factor:
   Bajaj Finance, 16 June 2025: 9331 / 933.1 = 10 before, 938 / 938 = 1 after,
   so factor 10. Yahoo's own list of splits is not used because it misses
   events (it lists Bajaj Finance's 4:1 bonus and 1:2 split as a single 2).
3. Accept the factor only if it is a clean ratio (2, 5, 10, 1.5 for a 1:2
   bonus, ...), otherwise record the day as unverified. A fall that is a real
   crash gives a factor of 1 and is recorded as needing no adjustment.

Answers are saved in `<dataset>.adjustments.json`, so Yahoo is asked about each
day once and the server itself never needs the internet. Anything Yahoo cannot
answer is saved as unverified and retried on the next refresh.
"""

import json
import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .atomic import write_json_atomically
from .envelope import ResponseWarning, WarningCode, make_warning

# Set by import-bhavcopy on the Parquet files it writes. A dataset that carries
# it holds exchange prices as published, which are known to be unadjusted.
EXCHANGE_METADATA_KEY = b"indian_stock_market_mcp.exchange"

SIDECAR_SUFFIX = ".adjustments.json"
SIDECAR_VERSION = 1
DROP_THRESHOLD_PERCENT = 15.0
SNAP_TOLERANCE = 0.01
YAHOO_PADDING_DAYS = 5  # calendar days of Yahoo history fetched around each event
YAHOO_TIMEOUT_SECONDS = 15
YAHOO_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}
PRICE_COLUMNS = ["open", "high", "low", "close"]
MAX_LISTED = 10

# Status of one candidate day.
ADJUSTED = "adjusted"
NO_ADJUSTMENT = "none"
UNVERIFIED = "unknown"

# Status of a whole dataset.
YAHOO_CHECKED = "yahoo_checked"  # a sidecar exists: candidates were checked
RAW_UNCHECKED = "raw_unchecked"  # exchange prices as published, never checked
UNKNOWN = "unknown"  # no way to tell (for example a user's own CSV)


class AdjustmentsError(ValueError):
    """Raised when adjustments cannot be read, written or refreshed."""


# --- Clean ratios --------------------------------------------------------------


def build_clean_ratios() -> list[float]:
    """Factors that real corporate actions produce: splits are whole ratios
    (2 to 100) and an a:b bonus gives (a + b) / b, with a and b up to 5.

    The set is kept small on purpose. A bigger one would put some entry within
    1% of almost any number, and the clean-ratio check would stop catching
    wrong factors.
    """
    ratios = {float(n) for n in range(2, 101)}
    ratios |= {(a + b) / b for a in range(1, 6) for b in range(1, 6)}
    return sorted(ratios)


CLEAN_RATIOS = build_clean_ratios()


def snap_ratio(value: float) -> float | None:
    """The clean ratio within 1% of `value`, or None when there is none."""
    nearest = min(CLEAN_RATIOS, key=lambda ratio: abs(ratio / value - 1))
    return nearest if abs(nearest / value - 1) <= SNAP_TOLERANCE else None


def compute_factor(
    raw_before: float, raw_after: float, yahoo_before: float, yahoo_after: float
) -> tuple[str, float | None]:
    """Size one event from our two closes and Yahoo's two closes.

    Returns (status, factor): "adjusted" with the clean factor, "none" when
    prices were not adjusted by the exchange or Yahoo (factor 1), or "unknown"
    when the numbers are unusable or do not form a clean ratio.
    """
    values = (raw_before, raw_after, yahoo_before, yahoo_after)
    if not all(math.isfinite(v) and v > 0 for v in values):
        return UNVERIFIED, None
    factor = (raw_before / yahoo_before) / (raw_after / yahoo_after)
    if abs(factor - 1) <= SNAP_TOLERANCE:
        return NO_ADJUSTMENT, 1.0
    snapped = snap_ratio(factor)
    return (ADJUSTED, snapped) if snapped else (UNVERIFIED, None)


# --- Candidate days ------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A close at least the threshold below the previous session's close."""

    symbol: str
    before_date: date
    ex_date: date
    before_close: float
    close: float

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.ex_date.isoformat()}"


def find_candidates(
    frame: pd.DataFrame, threshold_percent: float = DROP_THRESHOLD_PERCENT
) -> list[Candidate]:
    """Every symbol-day whose close is at least `threshold_percent` below the
    symbol's previous close in the data."""
    data = frame[["date", "symbol", "close"]].sort_values(["symbol", "date"])
    grouped = data.groupby("symbol")
    previous_close = grouped["close"].shift(1)
    previous_date = grouped["date"].shift(1)
    dropped = data["close"] / previous_close - 1 <= -threshold_percent / 100
    return [
        Candidate(
            symbol=str(row.symbol),
            before_date=pd.Timestamp(previous_date[index]).date(),
            ex_date=pd.Timestamp(row.date).date(),
            before_close=float(previous_close[index]),
            close=float(row.close),
        )
        for index, row in data[dropped].iterrows()
    ]


# --- Yahoo ---------------------------------------------------------------------

YahooCloses = dict[date, float]
Fetcher = Callable[[str, date, date], YahooCloses]


def yahoo_symbol(symbol: str, exchange: str) -> str:
    """The ticker Yahoo uses: RELIANCE.NS on NSE, RELIANCE.BO on BSE."""
    return f"{symbol}{YAHOO_SUFFIX[exchange]}"


def fetch_yahoo_closes(ticker: str, start: date, end: date) -> YahooCloses:
    """Yahoo's split-adjusted closes for a ticker from `start` to `end`.

    auto_adjust is off: Yahoo's Close column is adjusted for splits and
    bonuses but not for dividends, which is what a price comparison needs.
    """
    import yfinance  # imported here so the server starts even if it is broken

    # yfinance logs its own "possibly delisted" and HTTP 404 messages for
    # tickers Yahoo does not know. The result is reported as unverified, so the
    # extra lines only clutter the import summary.
    logger = logging.getLogger("yfinance")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        history = yfinance.Ticker(ticker).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            actions=False,
            timeout=YAHOO_TIMEOUT_SECONDS,
        )
    finally:
        logger.setLevel(previous_level)
    if history.empty:
        return {}
    closes = history["Close"].dropna()
    return {stamp.date(): float(value) for stamp, value in closes.items()}


def resolve_candidates(
    candidates: Iterable[Candidate], exchange: str, fetcher: Fetcher
) -> dict[str, dict[str, Any]]:
    """Check candidates against Yahoo, one request per symbol.

    Never raises on Yahoo trouble: a failed request or a missing price marks
    the affected days unverified, with the reason, so a later refresh retries.
    """
    by_symbol: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_symbol.setdefault(candidate.symbol, []).append(candidate)

    resolved: dict[str, dict[str, Any]] = {}
    padding = timedelta(days=YAHOO_PADDING_DAYS)
    for symbol, items in by_symbol.items():
        start = min(c.before_date for c in items) - padding
        end = max(c.ex_date for c in items) + padding
        try:
            closes = fetcher(yahoo_symbol(symbol, exchange), start, end)
        # Yahoo and its client can fail in many ways (network, rate limit,
        # parsing); none of them may stop an import.
        except Exception as error:  # noqa: BLE001
            for item in items:
                resolved[item.key] = {
                    "status": UNVERIFIED,
                    "factor": None,
                    "reason": f"Yahoo request failed ({type(error).__name__})",
                }
            continue
        for item in items:
            before, after = closes.get(item.before_date), closes.get(item.ex_date)
            if before is None or after is None:
                resolved[item.key] = {
                    "status": UNVERIFIED,
                    "factor": None,
                    "reason": "Yahoo has no price for one of the two days",
                }
                continue
            status, factor = compute_factor(
                item.before_close, item.close, before, after
            )
            entry: dict[str, Any] = {"status": status, "factor": factor}
            if status == UNVERIFIED:
                entry["reason"] = "Yahoo's prices do not give a clean split ratio"
            resolved[item.key] = entry
    return resolved


# --- The saved answers -----------------------------------------------------------


def sidecar_path(dataset: Path) -> Path:
    return dataset.with_name(dataset.name + SIDECAR_SUFFIX)


def dataset_exchange(dataset: Path) -> str | None:
    """The exchange an import-bhavcopy Parquet file holds, else None."""
    if dataset.suffix.lower() != ".parquet":
        return None
    try:
        metadata = pq.read_schema(dataset).metadata or {}
    except (OSError, ValueError, pa.ArrowException):
        return None
    exchange = metadata.get(EXCHANGE_METADATA_KEY, b"").decode()
    return exchange if exchange in YAHOO_SUFFIX else None


def read_sidecar(dataset: Path) -> dict[str, dict[str, Any]] | None:
    """The saved answers keyed "SYMBOL|YYYY-MM-DD", or None when none exist."""
    path = sidecar_path(dataset)
    if not path.exists():
        return None
    try:
        checked = json.loads(path.read_text())["checked"]
        for key, entry in checked.items():
            symbol, _, day = key.partition("|")
            date.fromisoformat(day)
            if not symbol or entry["status"] not in (
                ADJUSTED,
                NO_ADJUSTMENT,
                UNVERIFIED,
            ):
                raise ValueError(key)
            if entry["status"] == ADJUSTED and not entry["factor"] > 1:
                raise ValueError(key)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise AdjustmentsError(
            f"{path.name} could not be read ({type(error).__name__}). "
            "Fix or delete it; it was not changed."
        ) from error
    return checked


def write_sidecar(
    dataset: Path, exchange: str, checked: dict[str, dict[str, Any]]
) -> None:
    """Write the answers in one step so a failure cannot leave half a file."""
    write_json_atomically(
        sidecar_path(dataset),
        {
            "version": SIDECAR_VERSION,
            "exchange": exchange,
            "checked": dict(sorted(checked.items())),
        },
    )


@dataclass(frozen=True)
class AdjustmentEvent:
    symbol: str
    ex_date: date
    factor: float


@dataclass(frozen=True)
class AdjustmentInfo:
    """What is known about a dataset's price adjustment."""

    status: str
    events: tuple[AdjustmentEvent, ...] = ()
    unverified: tuple[tuple[str, date], ...] = ()
    problem: str | None = None


def load_adjustment_info(dataset: Path) -> AdjustmentInfo:
    """Read a dataset's adjustment status. Never raises: a damaged answers file
    is reported in `problem` and treated as unknown."""
    try:
        checked = read_sidecar(dataset)
    except AdjustmentsError as error:
        return AdjustmentInfo(UNKNOWN, problem=str(error))
    if checked is None:
        return AdjustmentInfo(RAW_UNCHECKED if dataset_exchange(dataset) else UNKNOWN)
    events, unverified = [], []
    for key, entry in checked.items():
        symbol, _, day = key.partition("|")
        if entry["status"] == ADJUSTED:
            events.append(
                AdjustmentEvent(symbol, date.fromisoformat(day), entry["factor"])
            )
        elif entry["status"] == UNVERIFIED:
            unverified.append((symbol, date.fromisoformat(day)))
    return AdjustmentInfo(YAHOO_CHECKED, tuple(events), tuple(unverified))


def apply_adjustments(
    frame: pd.DataFrame, events: Iterable[AdjustmentEvent]
) -> pd.DataFrame:
    """Divide prices before each event by its factor and multiply volume by it,
    so a stock's history reads as if the split had always been in place."""
    events = list(events)
    if not events:
        return frame
    adjusted = frame.copy()
    # Volume is a whole number in imported files, but a fractional factor (a
    # 1:5 bonus is 1.2) gives share counts with decimals.
    if "volume" in adjusted.columns:
        adjusted["volume"] = pd.to_numeric(adjusted["volume"], errors="coerce").astype(
            "float64"
        )
    # The quality report passes unvalidated rows, so compare on cleaned copies
    # of the symbol and date and leave the frame's own values untouched.
    symbols = adjusted["symbol"].astype("string").str.strip().str.upper()
    days = pd.to_datetime(adjusted["date"], errors="coerce")
    columns = [
        column
        for column in PRICE_COLUMNS
        if column in adjusted.columns
        and pd.api.types.is_numeric_dtype(adjusted[column])
    ]
    for event in events:
        before = (
            ((symbols == event.symbol) & (days < pd.Timestamp(event.ex_date)))
            .fillna(False)
            .to_numpy(dtype=bool)
        )
        if not before.any():
            continue
        adjusted.loc[before, columns] = adjusted.loc[before, columns] / event.factor
        if "volume" in adjusted.columns:
            adjusted.loc[before, "volume"] = (
                adjusted.loc[before, "volume"] * event.factor
            )
    return adjusted


# --- Refreshing ----------------------------------------------------------------


@dataclass(frozen=True)
class RefreshSummary:
    exchange: str
    candidates: int
    already_checked: int
    adjusted: tuple[AdjustmentEvent, ...]
    no_adjustment: int
    unverified: tuple[tuple[str, date, str], ...]  # symbol, day, reason


def refresh_adjustments(
    dataset: Path, fetcher: Fetcher | None = None
) -> RefreshSummary:
    """Check every unchecked candidate day of an imported dataset and save the
    answers. Days already adjusted or confirmed unadjusted are not asked again;
    unverified days are retried."""
    exchange = dataset_exchange(dataset)
    if exchange is None:
        raise AdjustmentsError(
            f"{dataset.name} was not created by import-bhavcopy, so its prices "
            "cannot be adjusted automatically."
        )
    frame = pd.read_parquet(dataset, columns=["date", "symbol", "close"])
    frame["date"] = pd.to_datetime(frame["date"])
    candidates = find_candidates(frame)
    checked = read_sidecar(dataset) or {}

    settled = (ADJUSTED, NO_ADJUSTMENT)
    pending = [
        c for c in candidates if checked.get(c.key, {}).get("status") not in settled
    ]
    checked.update(resolve_candidates(pending, exchange, fetcher or fetch_yahoo_closes))
    write_sidecar(dataset, exchange, checked)

    adjusted, unverified, none = [], [], 0
    for candidate in candidates:
        entry = checked[candidate.key]
        if entry["status"] == ADJUSTED:
            adjusted.append(
                AdjustmentEvent(candidate.symbol, candidate.ex_date, entry["factor"])
            )
        elif entry["status"] == NO_ADJUSTMENT:
            none += 1
        else:
            unverified.append(
                (candidate.symbol, candidate.ex_date, entry.get("reason", ""))
            )
    return RefreshSummary(
        exchange=exchange,
        candidates=len(candidates),
        already_checked=len(candidates) - len(pending),
        adjusted=tuple(adjusted),
        no_adjustment=none,
        unverified=tuple(unverified),
    )


# --- Warnings ------------------------------------------------------------------


def adjustment_warnings(
    info: AdjustmentInfo,
    symbols: Iterable[str] | None = None,
    *,
    explain_unknown: bool = False,
) -> list[ResponseWarning]:
    """Warnings about price adjustment for the given symbols (None = all).

    A dataset whose status is simply unknown, such as a user's own CSV, is only
    explained when `explain_unknown` is set (dataset diagnostics). Tools stay
    quiet about it rather than repeat the same notice on every answer.
    """
    if info.problem:
        return [make_warning(WarningCode.PRICE_ADJUSTMENT_UNKNOWN, info.problem)]
    if info.status == RAW_UNCHECKED:
        return [
            make_warning(
                WarningCode.UNADJUSTED_PRICES,
                "Prices are exchange closes as published and are not adjusted for "
                "splits or bonuses. Run `indian-stock-market-mcp refresh-adjustments "
                "<dataset>` to check them.",
            )
        ]
    if info.status == YAHOO_CHECKED:
        wanted = None if symbols is None else set(symbols)
        affected = [
            (symbol, day)
            for symbol, day in info.unverified
            if wanted is None or symbol in wanted
        ]
        if not affected:
            return []
        names = sorted({symbol for symbol, _ in affected})
        detail = ", ".join(f"{s} {d.isoformat()}" for s, d in affected[:MAX_LISTED])
        more = (
            ""
            if len(affected) <= MAX_LISTED
            else f" and {len(affected) - MAX_LISTED} more"
        )
        return [
            make_warning(
                WarningCode.PRICE_ADJUSTMENT_UNKNOWN,
                f"{len(affected)} large price fall(s) could not be verified as "
                f"splits or bonuses and are not adjusted: {detail}{more}. "
                "Returns across those days may be distorted.",
                names[:50],
            )
        ]
    if explain_unknown:
        return [
            make_warning(
                WarningCode.PRICE_ADJUSTMENT_UNKNOWN,
                "Price-adjustment status for splits and dividends is unknown; "
                "verify it before relying on returns.",
            )
        ]
    return []
