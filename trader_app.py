#!/usr/bin/env python3
"""
Trader App — interactive terminal backtester.

A menu-driven terminal application around the 20/50 SMA crossover backtest.
Change the strategy parameters, run backtests in/out of sample, view results
as tables and terminal charts, and drill into individual tickers and trades.

Run:  python3 trader_app.py
Deps: pip install yfinance backtesting pandas numpy rich plotext
"""

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, FloatPrompt, Confirm
from rich.table import Table

warnings.filterwarnings("ignore")

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "price_data"
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = APP_DIR / "trader_settings.json"

DEFAULT_SETTINGS = {
    "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "JPM", "JNJ", "PG", "XOM", "KO", "DIS"],
    "benchmark": "SPY",
    "sma_fast": 20,
    "sma_slow": 50,
    "commission_pct": 0.1,
    "cash": 10000,
    "start": "2010-01-01",
    "end": "2026-07-18",
    "in_sample_end": "2018-12-31",
    "out_of_sample_start": "2019-01-01",
    "risk_engine": False,
    "risk_pct_per_trade": 2.0,
    "momentum_top_n": 3,
    "momentum_lookback_m": 12,
    "ibkr_port": 7497,
    "ibkr_client_id": 7,
    "autotrade": {"enabled": False, "signal": "momentum"},
}

console = Console()


# ---------------------------------------------------------------- settings

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            merged = {**DEFAULT_SETTINGS, **saved}
            return merged
        except (json.JSONDecodeError, OSError):
            console.print("[yellow]Settings file unreadable; using defaults.[/yellow]")
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


# ---------------------------------------------------------------- data

def fetch(ticker: str, start: str, end: str, force: bool = False, cache_dir: Path = None) -> pd.DataFrame:
    """Download (or load cached) daily OHLCV for one ticker."""
    cache_path = (cache_dir or DATA_DIR) / f"{ticker}.csv"
    if cache_path.exists() and not force:
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
    else:
        raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if raw.empty:
            raise RuntimeError(f"No data returned for {ticker}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.to_csv(cache_path)
    df.index = pd.to_datetime(df.index)
    return df.dropna()


def load_all_data(settings: dict, force: bool = False) -> dict:
    tickers = settings["tickers"] + [settings["benchmark"]]
    data = {}
    with console.status("[bold cyan]Fetching price data..."):
        for t in tickers:
            try:
                data[t] = fetch(t, settings["start"], settings["end"], force=force)
            except Exception as e:
                console.print(f"[red]  {t}: failed ({e})[/red]")
    console.print(f"[green]Loaded {len(data)}/{len(tickers)} tickers.[/green]")
    return data


# ---------------------------------------------------------------- strategy

def _atr_values(high, low, close, n=14):
    h, l, c = pd.Series(high), pd.Series(low), pd.Series(close)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().values


def make_strategy(fast: int, slow: int, risk_engine: bool = False, risk_pct: float = 2.0):
    """Plain SMA crossover, or (risk_engine=True) the 'V4' version:
    200-day trend filter + 2*ATR trailing stop + fixed-fractional sizing
    so a stop-out loses ~risk_pct% of equity."""

    class SmaCross(Strategy):
        n1 = fast
        n2 = slow

        def init(self):
            close = self.data.Close
            self.sma1 = self.I(lambda x: pd.Series(x).rolling(self.n1).mean(), close, name=f"SMA{self.n1}")
            self.sma2 = self.I(lambda x: pd.Series(x).rolling(self.n2).mean(), close, name=f"SMA{self.n2}")
            if risk_engine:
                self.s200 = self.I(lambda x: pd.Series(x).rolling(200).mean(), close, name="SMA200")
                self.atr = self.I(lambda _: _atr_values(self.data.High, self.data.Low, self.data.Close),
                                  close, name="ATR14")
                self.trail = None

        def next(self):
            if not risk_engine:
                if crossover(self.sma1, self.sma2):
                    self.buy()
                elif crossover(self.sma2, self.sma1):
                    self.position.close()
                return

            price = self.data.Close[-1]
            atr = self.atr[-1]
            if self.position:
                if not np.isnan(atr):
                    self.trail = max(self.trail, price - 2 * atr)
                if (self.trail is not None and price < self.trail) or crossover(self.sma2, self.sma1):
                    self.position.close()
                    self.trail = None
            elif crossover(self.sma1, self.sma2):
                if np.isnan(self.s200[-1]) or price <= self.s200[-1]:
                    return  # trend filter: only buy above 200-day SMA
                stop_dist = 2 * atr
                if np.isnan(stop_dist) or stop_dist <= 0:
                    return
                units = int((self.equity * risk_pct / 100) / stop_dist)
                units = min(units, int(self.equity * 0.99 / price))
                if units >= 1:
                    self.buy(size=units)
                    self.trail = price - stop_dist

    return SmaCross


# ---------------------------------------------------------------- metrics

def buy_and_hold_stats(df: pd.DataFrame) -> dict:
    start_price, end_price = df["Close"].iloc[0], df["Close"].iloc[-1]
    years = (df.index[-1] - df.index[0]).days / 365.25
    total_return = end_price / start_price - 1
    cagr = (end_price / start_price) ** (1 / years) - 1 if years > 0 else float("nan")
    running_max = df["Close"].cummax()
    max_dd = ((df["Close"] - running_max) / running_max).min()
    daily = df["Close"].pct_change().dropna()
    sharpe = (daily.mean() / daily.std()) * np.sqrt(252) if daily.std() > 0 else float("nan")
    return {"return_pct": total_return * 100, "cagr_pct": cagr * 100,
            "max_dd_pct": max_dd * 100, "sharpe": sharpe}


def cagr_from_total(df: pd.DataFrame, total_return_pct: float) -> float:
    years = (df.index[-1] - df.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    return (((1 + total_return_pct / 100) ** (1 / years)) - 1) * 100


def run_backtest(df: pd.DataFrame, settings: dict):
    """Run the strategy on one dataframe. Returns (stats, backtest_object)."""
    strat = make_strategy(settings["sma_fast"], settings["sma_slow"],
                          risk_engine=settings.get("risk_engine", False),
                          risk_pct=settings.get("risk_pct_per_trade", 2.0))
    bt = Backtest(df, strat, cash=settings["cash"],
                  commission=settings["commission_pct"] / 100, exclusive_orders=True)
    stats = bt.run()
    return stats, bt


# ---------------------------------------------------------------- views

def fmt(x, suffix=""):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:,.2f}{suffix}"


def color_num(x, suffix="", invert=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    good = (x < 0) if invert else (x > 0)
    color = "green" if good else "red"
    return f"[{color}]{x:,.2f}{suffix}[/{color}]"


def results_table(rows: list, title: str) -> Table:
    t = Table(title=title, header_style="bold cyan")
    t.add_column("Ticker", style="bold")
    t.add_column("Strategy CAGR", justify="right")
    t.add_column("Buy&Hold CAGR", justify="right")
    t.add_column("SPY CAGR", justify="right")
    t.add_column("Sharpe", justify="right")
    t.add_column("Max DD", justify="right")
    t.add_column("Trades", justify="right")
    t.add_column("Win %", justify="right")
    t.add_column("Beats B&H?", justify="center")
    for r in rows:
        beats = r["strategy_cagr"] > r["bh_cagr"]
        t.add_row(
            r["ticker"],
            color_num(r["strategy_cagr"], "%"),
            fmt(r["bh_cagr"], "%"),
            fmt(r["spy_cagr"], "%"),
            fmt(r["sharpe"]),
            f"[red]{fmt(r['max_dd'], '%')}[/red]",
            str(r["trades"]),
            fmt(r["win_rate"], "%"),
            "[green]YES[/green]" if beats else "[red]no[/red]",
        )
    return t


def run_and_show(data: dict, settings: dict, period_name: str, p_start: str, p_end: str):
    spy = data.get(settings["benchmark"])
    if spy is None:
        console.print("[red]Benchmark data missing — refresh data first (menu 8).[/red]")
        return []
    spy_period = spy.loc[p_start:p_end]
    if len(spy_period) < 60:
        console.print(f"[red]Not enough benchmark data in {p_start}..{p_end}.[/red]")
        return []
    spy_bh = buy_and_hold_stats(spy_period)

    rows = []
    with console.status(f"[bold cyan]Backtesting {len(settings['tickers'])} tickers ({period_name})..."):
        for ticker in settings["tickers"]:
            if ticker not in data:
                continue
            sliced = data[ticker].loc[p_start:p_end]
            if len(sliced) < max(60, settings["sma_slow"] + 10):
                console.print(f"[yellow]  skip {ticker}: not enough data in period[/yellow]")
                continue
            try:
                stats, _ = run_backtest(sliced, settings)
            except Exception as e:
                console.print(f"[red]  {ticker} failed: {e}[/red]")
                continue
            bh = buy_and_hold_stats(sliced)
            rows.append({
                "ticker": ticker,
                "strategy_cagr": cagr_from_total(sliced, stats["Return [%]"]),
                "bh_cagr": bh["cagr_pct"],
                "spy_cagr": spy_bh["cagr_pct"],
                "sharpe": stats["Sharpe Ratio"],
                "max_dd": stats["Max. Drawdown [%]"],
                "trades": int(stats["# Trades"]),
                "win_rate": stats["Win Rate [%]"],
            })

    if not rows:
        console.print("[red]No results produced.[/red]")
        return rows

    risk_tag = (f"  [RISK ENGINE: trend filter + 2xATR stop + {settings.get('risk_pct_per_trade', 2.0)}% risk]"
                if settings.get("risk_engine") else "")
    console.print(results_table(
        rows,
        f"SMA({settings['sma_fast']}/{settings['sma_slow']}) — {period_name}  "
        f"[{p_start} → {p_end}]  cost {settings['commission_pct']}%/trade{risk_tag}",
    ))
    n = len(rows)
    beats_bh = sum(r["strategy_cagr"] > r["bh_cagr"] for r in rows)
    beats_spy = sum(r["strategy_cagr"] > r["spy_cagr"] for r in rows)
    avg_s = np.mean([r["strategy_cagr"] for r in rows])
    avg_b = np.mean([r["bh_cagr"] for r in rows])
    verdict_color = "green" if beats_spy > n / 2 else "red"
    console.print(Panel(
        f"Beat own buy-and-hold: [bold]{beats_bh}/{n}[/bold]   "
        f"Beat SPY: [bold]{beats_spy}/{n}[/bold]\n"
        f"Avg strategy CAGR: [bold]{avg_s:.2f}%[/bold]   "
        f"Avg buy-and-hold CAGR: [bold]{avg_b:.2f}%[/bold]   "
        f"SPY CAGR: [bold]{spy_bh['cagr_pct']:.2f}%[/bold]",
        title=f"[{verdict_color}]Verdict[/{verdict_color}]", border_style=verdict_color,
    ))
    return rows


def deep_dive(data: dict, settings: dict):
    ticker = Prompt.ask("Ticker", default=settings["tickers"][0]).upper().strip()
    if ticker not in data:
        console.print(f"[red]{ticker} not loaded. Add it in settings or refresh data.[/red]")
        return
    p_start = Prompt.ask("Start date", default=settings["out_of_sample_start"])
    p_end = Prompt.ask("End date", default=settings["end"])
    sliced = data[ticker].loc[p_start:p_end]
    if len(sliced) < max(60, settings["sma_slow"] + 10):
        console.print("[red]Not enough data in that window.[/red]")
        return
    stats, _ = run_backtest(sliced, settings)
    bh = buy_and_hold_stats(sliced)

    t = Table(title=f"{ticker} deep dive [{p_start} → {p_end}]", header_style="bold cyan")
    t.add_column("Metric"); t.add_column("Strategy", justify="right"); t.add_column("Buy & Hold", justify="right")
    t.add_row("Total return", color_num(stats["Return [%]"], "%"), color_num(bh["return_pct"], "%"))
    t.add_row("CAGR", color_num(cagr_from_total(sliced, stats["Return [%]"]), "%"), color_num(bh["cagr_pct"], "%"))
    t.add_row("Sharpe", fmt(stats["Sharpe Ratio"]), fmt(bh["sharpe"]))
    t.add_row("Max drawdown", f"[red]{fmt(stats['Max. Drawdown [%]'], '%')}[/red]", f"[red]{fmt(bh['max_dd_pct'], '%')}[/red]")
    t.add_row("Trades", str(int(stats["# Trades"])), "1")
    t.add_row("Win rate", fmt(stats["Win Rate [%]"], "%"), "-")
    t.add_row("Best trade", fmt(stats["Best Trade [%]"], "%"), "-")
    t.add_row("Worst trade", fmt(stats["Worst Trade [%]"], "%"), "-")
    t.add_row("Commissions paid", f"${fmt(stats['Commissions [$]'])}", "$0")
    console.print(t)

    trades = stats["_trades"]
    if len(trades) and Confirm.ask("Show individual trades?", default=False):
        tt = Table(title=f"{ticker} trades", header_style="bold cyan")
        for col in ["Entry", "Exit", "Days", "Return"]:
            tt.add_column(col, justify="right")
        for _, tr in trades.iterrows():
            ret = tr["ReturnPct"] * 100
            tt.add_row(str(tr["EntryTime"].date()), str(tr["ExitTime"].date()),
                       str((tr["ExitTime"] - tr["EntryTime"]).days), color_num(ret, "%"))
        console.print(tt)

    if Confirm.ask("Plot equity curve in terminal?", default=True):
        plot_equity(ticker, sliced, stats, settings)


def plot_equity(ticker: str, sliced: pd.DataFrame, stats, settings: dict):
    try:
        import plotext as plt
    except ImportError:
        console.print("[red]plotext not installed: pip install plotext[/red]")
        return
    eq = stats["_equity_curve"]["Equity"]
    bh_norm = sliced["Close"] / sliced["Close"].iloc[0] * settings["cash"]
    dates = [d.strftime("%d/%m/%Y") for d in eq.index]
    plt.clear_figure()
    plt.date_form("d/m/Y")
    plt.plot(dates, eq.values.tolist(), label="Strategy")
    plt.plot(dates, bh_norm.reindex(eq.index).ffill().values.tolist(), label="Buy & Hold")
    plt.title(f"{ticker}: strategy vs buy & hold (equity, ${settings['cash']:,} start)")
    plt.theme("dark")
    plt.plotsize(min(120, plt.terminal_width() or 100), 25)
    plt.show()


def simulate_rotation(daily_ret: pd.DataFrame, month_ends: list, tops_by_date: dict,
                      cost: float, top_n: int) -> pd.Series:
    """Equal-weight monthly rotation simulator: shared by momentum_backtest and
    any other per-date ranking (e.g. Kronos's forecast, see KronosAI/kronos_backtest.py)
    so a strategy comparison is apples-to-apples on identical cost/turnover mechanics.

    `tops_by_date[month_ends[i]]` is the list of tickers to hold from
    month_ends[i] to month_ends[i+1]; turnover cost is charged on the
    fraction of the top-N that changed since the prior rebalance."""
    equity, curve, prev = 1.0, [], set()
    for i in range(len(month_ends) - 1):
        m_start, m_end = month_ends[i], month_ends[i + 1]
        top = tops_by_date.get(m_start, [])
        if top:
            seg = daily_ret.loc[m_start:m_end, top].iloc[1:]
            seg_ret = (1 + seg.mean(axis=1)).prod() - 1
        else:
            seg_ret = 0.0  # fully in cash
        turnover = len(set(top) ^ prev) / max(top_n, 1)
        equity *= (1 + seg_ret) * (1 - cost * turnover)
        prev = set(top)
        curve.append((m_end, equity))
    return pd.Series(dict(curve))


def momentum_backtest(data: dict, settings: dict):
    """Portfolio-level momentum rotation: each month, rank tickers by trailing
    N-month return and hold the top-K equal-weight. If the risk engine is on,
    apply an absolute-momentum filter: any selected ticker with a negative
    trailing return is replaced by cash (dual momentum — cuts bear-market DD)."""
    top_n = settings.get("momentum_top_n", 3)
    lookback = settings.get("momentum_lookback_m", 12)
    dual = settings.get("risk_engine", False)
    cost = settings["commission_pct"] / 100
    bench = settings["benchmark"]

    p_start = Prompt.ask("Start date", default=settings["out_of_sample_start"])
    p_end = Prompt.ask("End date", default=settings["end"])

    tickers = [t for t in settings["tickers"] if t in data]
    if len(tickers) < top_n + 1:
        console.print(f"[red]Need at least {top_n + 1} loaded tickers.[/red]")
        return
    # extend lookback so the first month already has N months of history
    warmup = pd.Timestamp(p_start) - pd.DateOffset(months=lookback + 2)
    closes = pd.DataFrame({t: data[t]["Close"] for t in tickers}).loc[warmup:p_end].dropna(how="all")

    monthly = closes.resample("ME").last()
    mom = monthly.pct_change(lookback)
    daily_ret = closes.pct_change()

    first_i = next((i for i in range(len(monthly)) if monthly.index[i] >= pd.Timestamp(p_start)), None)
    if first_i is None or first_i >= len(monthly) - 1:
        console.print("[red]Not enough data in that window.[/red]")
        return

    tops_by_date = {}
    for i in range(max(first_i, lookback), len(monthly) - 1):
        ranked = mom.iloc[i].dropna().sort_values(ascending=False)
        top = list(ranked.index[:top_n])
        if dual:
            top = [t for t in top if ranked[t] > 0]  # negative momentum -> cash
        tops_by_date[monthly.index[i]] = top

    month_ends = list(monthly.index[max(first_i, lookback):])
    curve = simulate_rotation(daily_ret, month_ends, tops_by_date, cost, top_n)
    holds = [(d.date(), tops_by_date[d] if tops_by_date[d] else ["CASH"]) for d in month_ends[:-1]]
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    cagr = ((curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1) * 100
    dd = ((curve - curve.cummax()) / curve.cummax()).min() * 100
    mret = curve.pct_change().dropna()
    sharpe = mret.mean() / mret.std() * np.sqrt(12) if mret.std() > 0 else float("nan")

    spy = data.get(bench)
    spy_line = ""
    if spy is not None:
        sb = buy_and_hold_stats(spy.loc[curve.index[0]:p_end])
        spy_line = (f"\n{bench} buy & hold:  CAGR [bold]{sb['cagr_pct']:.2f}%[/bold]   "
                    f"max DD [bold]{sb['max_dd_pct']:.2f}%[/bold]   Sharpe [bold]{sb['sharpe']:.2f}[/bold]")

    mode = f"top-{top_n}, {lookback}-mo lookback" + (", dual momentum (cash filter) ON" if dual else "")
    verdict_color = "green" if spy is not None and cagr > sb["cagr_pct"] else "yellow"
    console.print(Panel(
        f"Momentum rotation ({mode})  [{curve.index[0].date()} → {curve.index[-1].date()}]\n"
        f"CAGR [bold]{cagr:.2f}%[/bold]   max DD [bold]{dd:.2f}%[/bold]   "
        f"Sharpe [bold]{sharpe:.2f}[/bold]   months in cash: "
        f"[bold]{sum(1 for _, h in holds if h == ['CASH'])}[/bold]/{len(holds)}{spy_line}",
        title=f"[{verdict_color}]Momentum result[/{verdict_color}]", border_style=verdict_color))

    if Confirm.ask("Show holdings by month?", default=False):
        ht = Table(title="Holdings", header_style="bold cyan")
        ht.add_column("Month end"); ht.add_column("Held")
        for d, h in holds:
            ht.add_row(str(d), ", ".join(h))
        console.print(ht)

    if Confirm.ask("Plot equity curve vs benchmark?", default=True):
        try:
            import plotext as plt
        except ImportError:
            console.print("[red]plotext not installed.[/red]")
            return
        plt.clear_figure()
        plt.date_form("d/m/Y")
        dates = [d.strftime("%d/%m/%Y") for d in curve.index]
        plt.plot(dates, (curve * settings["cash"]).tolist(), label="Momentum rotation")
        if spy is not None:
            spy_c = spy["Close"].reindex(curve.index, method="ffill")
            plt.plot(dates, (spy_c / spy_c.iloc[0] * settings["cash"]).tolist(), label=f"{bench} B&H")
        plt.title(f"Momentum rotation vs {bench} (${settings['cash']:,} start)")
        plt.theme("dark")
        plt.plotsize(min(120, plt.terminal_width() or 100), 25)
        plt.show()


def kronos_menu(settings: dict):
    """Run KronosAI's forecast agent (kronos_agent.py) against the watchlist
    and show a ranked table. Analysis only — this app places no orders;
    paper_trader.py --signal kronos trades off this same signal (still
    paper, still human-approved). Backtested 2026-07-23 (see CLAUDE.md
    empirical findings): near-zero information coefficient, 50% hit rate —
    no measurable forecasting skill found in the one honest post-cutoff
    window available. Kept for reference/re-testing, not because it's
    shown edge — see CLAUDE.md rule 5."""
    try:
        sys.path.insert(0, str(Path(__file__).parent / "KronosAI"))
        import kronos_agent as ka
    except ImportError as e:
        console.print(f"[red]Kronos dependencies not installed: {e}[/red]")
        console.print("[dim]pip install torch einops huggingface_hub safetensors matplotlib tqdm "
                      "(see KronosAI/requirements.txt)[/dim]")
        return

    raw = Prompt.ask("Tickers (comma-separated, blank = full watchlist)", default="")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()] or settings["tickers"]
    sample_count = IntPrompt.ask("Sample count (forecast paths averaged per ticker)",
                                 default=ka.DEFAULT_SAMPLE_COUNT)

    console.print(Panel(
        f"Forecasting {len(tickers)} ticker(s), {ka.PRED_LEN} trading days ahead, "
        f"sample_count={sample_count}.\n"
        "[dim]Backtested — no measurable edge found (IC 0.036, 50% hit rate). "
        "Analysis only, no orders placed.[/dim]",
        title="Kronos forecast", border_style="cyan"))

    with console.status("[bold cyan]Loading model + forecasting (first run downloads from Hugging Face)..."):
        try:
            ok_tickers, hist_data, pred_dfs = ka.forecast_tickers(
                tickers, pred_len=ka.PRED_LEN, sample_count=sample_count, verbose=False)
        except Exception as e:
            console.print(f"[red]Forecast failed: {e}[/red]")
            return

    rows = []
    for tk in ok_tickers:
        last_close = hist_data[tk]["Close"].iloc[-1]
        pred_end = pred_dfs[tk]["close"].iloc[-1]
        chg = (pred_end / last_close - 1) * 100
        rows.append((tk, last_close, pred_end, chg))
    rows.sort(key=lambda r: r[3], reverse=True)

    kt = Table(title=f"Kronos forecast — {ka.PRED_LEN} trading days ahead", header_style="bold cyan")
    kt.add_column("Ticker", style="bold")
    kt.add_column("Last Close", justify="right")
    kt.add_column("Pred End Close", justify="right")
    kt.add_column("Chg %", justify="right")
    for tk, last_close, pred_end, chg in rows:
        kt.add_row(tk, fmt(last_close), fmt(pred_end), color_num(chg, "%"))
    console.print(kt)


def chart_view(data: dict, settings: dict):
    """Candlestick chart with indicator overlays + panels, terminal-rendered.
    All math comes from indicators.py — the same module the research agent
    uses, so human and AI look at identical numbers."""
    import indicators as ta
    try:
        import plotext as plt
    except ImportError:
        console.print("[red]plotext not installed: pip install plotext[/red]")
        return

    ticker = Prompt.ask("Ticker", default=settings["tickers"][0]).upper().strip()
    tf = Prompt.ask("Timeframe", choices=["daily", "15m"], default="daily")

    if tf == "daily":
        if ticker in data:
            df = data[ticker].tail(120)
        else:
            try:
                df = fetch(ticker, settings["start"], settings["end"]).tail(120)
            except Exception as e:
                console.print(f"[red]No data for {ticker}: {e}[/red]")
                return
        date_fmt, dform = "%d/%m/%Y", "d/m/Y"
    else:
        raw = yf.download(ticker, period="5d", interval="15m", progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.dropna()
        if len(df) < 30:
            console.print(f"[red]Not enough 15-min data for {ticker}.[/red]")
            return
        date_fmt, dform = "%d/%m/%Y %H:%M", "d/m/Y H:M"

    sets = Prompt.ask("Indicator sets (comma-sep: trend,vol,volume,structure or 'all')",
                      default="all").lower()
    want = {"trend", "vol", "volume", "structure"} if "all" in sets else \
           {s.strip() for s in sets.split(",")}

    c = df["Close"]
    dates = [d.strftime(date_fmt) for d in df.index]
    width = min(140, plt.terminal_width() or 110)

    # --- main panel: candles + overlays
    plt.clear_figure()
    plt.date_form(dform)
    plt.candlestick(dates, {"Open": df["Open"].tolist(), "Close": df["Close"].tolist(),
                            "High": df["High"].tolist(), "Low": df["Low"].tolist()})
    title_bits = [f"{ticker} ({tf})"]
    if "trend" in want:
        n1, n2 = (settings["sma_fast"], settings["sma_slow"]) if tf == "daily" else (20, 50)
        plt.plot(dates, ta.sma(c, n1).tolist(), label=f"SMA{n1}")
        plt.plot(dates, ta.sma(c, n2).tolist(), label=f"SMA{n2}")
        title_bits.append(f"SMA{n1}/{n2}")
    if "vol" in want:
        bb_mid, bb_up, bb_lo = ta.bollinger(c)
        plt.plot(dates, bb_up.tolist(), label="BB up")
        plt.plot(dates, bb_lo.tolist(), label="BB low")
        title_bits.append("Bollinger(20,2)")
    if "structure" in want:
        sup, res = ta.swing_levels(df)
        for lv in sup[:2]:
            plt.horizontal_line(lv, color="green")
        for lv in res[:2]:
            plt.horizontal_line(lv, color="red")
        if sup or res:
            title_bits.append("S/R levels")
    plt.title("  ".join(title_bits))
    plt.theme("dark")
    plt.plotsize(width, 22)
    plt.show()

    # --- panels
    if "volume" in want:
        plt.clear_figure()
        plt.date_form(dform)
        plt.bar(dates, df["Volume"].tolist())
        if tf == "15m":
            v = ta.vwap(df)
            console.print(f"[dim]Session VWAP: {float(v.iloc[-1]):.2f} "
                          f"(price {'above' if float(c.iloc[-1]) > float(v.iloc[-1]) else 'below'})[/dim]")
        plt.title("Volume")
        plt.theme("dark")
        plt.plotsize(width, 8)
        plt.show()

    if "trend" in want:
        m_line, m_sig, m_hist = ta.macd(c)
        plt.clear_figure()
        plt.date_form(dform)
        plt.plot(dates, m_line.tolist(), label="MACD")
        plt.plot(dates, m_sig.tolist(), label="signal")
        plt.bar(dates, m_hist.tolist(), label="hist")
        plt.horizontal_line(0)
        plt.title("MACD (12,26,9)")
        plt.theme("dark")
        plt.plotsize(width, 10)
        plt.show()

        r = ta.rsi(c)
        plt.clear_figure()
        plt.date_form(dform)
        plt.plot(dates, r.tolist(), label="RSI14")
        plt.horizontal_line(70, color="red")
        plt.horizontal_line(30, color="green")
        plt.title("RSI (14)")
        plt.theme("dark")
        plt.plotsize(width, 8)
        plt.show()

    # --- numeric readout (identical to what the research agent sees)
    try:
        lines = ta.summarize_daily(df) if tf == "daily" else ta.summarize_intraday(df)
        console.print(Panel("\n".join(lines),
                            title="Indicator readout (same numbers the AI sees)",
                            border_style="cyan"))
    except Exception as e:
        console.print(f"[yellow]Readout unavailable: {e}[/yellow]")


def ibkr_menu(settings: dict):
    """Connect to the IBKR PAPER account via ibkr_service and inspect it.
    Read-only by design: no orders can be placed from this app until a
    strategy + approval loop exist."""
    try:
        import ibkr_service as ibs
    except ImportError:
        console.print("[red]ib_async not installed — run: pip install ib_async[/red]")
        return

    port = settings.get("ibkr_port", 7497)
    console.print(Panel(
        f"Connecting to IBKR on 127.0.0.1:[bold]{port}[/bold] (paper trading port).\n"
        "[dim]Requires TWS or IB Gateway running locally with the API enabled\n"
        "(Edit → Global Configuration → API → Settings).[/dim]",
        title="IBKR", border_style="cyan"))
    try:
        ib = ibs.connect(port=port, client_id=settings.get("ibkr_client_id", 7))
    except Exception as e:
        console.print(f"[red]Could not connect: {e}[/red]")
        console.print("[dim]Is TWS/IB Gateway open and logged into the PAPER account? "
                      f"Is the API enabled and the socket port set to {port}?[/dim]")
        return
    console.print("[green]Connected (paper).[/green]")

    try:
        while True:
            sub = Prompt.ask(
                "IBKR: [bold]1[/bold]=account summary  [bold]2[/bold]=positions  "
                "[bold]3[/bold]=live 15-min bars  [bold]4[/bold]=disconnect & back",
                choices=["1", "2", "3", "4"], default="4")
            if sub == "1":
                acct = {v.tag: v.value for v in ib.accountSummary()
                        if v.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower",
                                     "GrossPositionValue", "AvailableFunds")}
                t = Table(title="Account summary (paper)", header_style="bold cyan")
                t.add_column("Metric"); t.add_column("Value", justify="right")
                for k, v in acct.items():
                    t.add_row(k, f"${float(v):,.2f}")
                console.print(t)
            elif sub == "2":
                poss = ib.positions()
                if not poss:
                    console.print("[dim]No open positions.[/dim]")
                else:
                    t = Table(title="Open positions", header_style="bold cyan")
                    for col in ("Symbol", "Type", "Position", "Avg cost"):
                        t.add_column(col, justify="right")
                    for p in poss:
                        t.add_row(p.contract.symbol, p.contract.secType,
                                  f"{p.position:,.0f}", f"{p.avgCost:,.2f}")
                    console.print(t)
            elif sub == "3":
                kind = Prompt.ask("Asset class", choices=["stock", "forex", "future", "crypto"],
                                  default="stock")
                sym = Prompt.ask("Symbol", default={"stock": "AAPL", "forex": "EURUSD",
                                                    "future": "MGC", "crypto": "BTC"}[kind]).upper()
                if kind == "stock":
                    contract = ibs.stock(sym)
                elif kind == "forex":
                    contract = ibs.forex_pair(sym)
                elif kind == "crypto":
                    contract = ibs.crypto(sym)
                else:
                    expiry = Prompt.ask("Expiry (YYYYMM)", default="202612")
                    exchange = Prompt.ask("Exchange", default="COMEX")
                    contract = ibs.future(sym, expiry, exchange)
                try:
                    df = ibs.get_15min_bars(ib, contract, duration="1 D")
                except Exception as e:
                    console.print(f"[red]Data request failed: {e}[/red]")
                    continue
                if df is None or len(df) == 0:
                    console.print("[yellow]No bars returned (market data subscription "
                                  "may be needed for this asset).[/yellow]")
                    continue
                t = Table(title=f"{sym} — last 10 bars (15 min)", header_style="bold cyan")
                for col in ("time", "open", "high", "low", "close"):
                    t.add_column(col, justify="right")
                for _, r in df.tail(10).iterrows():
                    t.add_row(str(r["date"]), f"{r['open']:.4f}", f"{r['high']:.4f}",
                              f"{r['low']:.4f}", f"{r['close']:.4f}")
                console.print(t)
                if Confirm.ask("Plot session chart?", default=True):
                    try:
                        import plotext as plt
                        plt.clear_figure()
                        plt.plot(df["close"].tolist())
                        plt.title(f"{sym} 15-min close (today)")
                        plt.theme("dark")
                        plt.plotsize(min(120, plt.terminal_width() or 100), 20)
                        plt.show()
                    except ImportError:
                        console.print("[red]plotext not installed.[/red]")
            else:
                break
    finally:
        ib.disconnect()
        console.print("[dim]Disconnected from IBKR.[/dim]")


def edit_settings(settings: dict):
    while True:
        console.print(Panel(
            f"1. Tickers: [bold]{', '.join(settings['tickers'])}[/bold]\n"
            f"2. SMA windows: [bold]{settings['sma_fast']}/{settings['sma_slow']}[/bold]\n"
            f"3. Cost per trade: [bold]{settings['commission_pct']}%[/bold]\n"
            f"4. Starting cash: [bold]${settings['cash']:,}[/bold]\n"
            f"5. Date range: [bold]{settings['start']} → {settings['end']}[/bold] "
            f"(in-sample ends {settings['in_sample_end']})\n"
            f"6. Risk engine: [bold]{'ON' if settings.get('risk_engine') else 'off'}[/bold] "
            f"(SMA: trend filter + 2xATR stop + {settings.get('risk_pct_per_trade', 2.0)}% risk/trade; "
            f"momentum: cash filter)\n"
            f"7. Momentum: [bold]top {settings.get('momentum_top_n', 3)}[/bold] of watchlist, "
            f"[bold]{settings.get('momentum_lookback_m', 12)}-month[/bold] lookback\n"
            f"8. IBKR: port [bold]{settings.get('ibkr_port', 7497)}[/bold], "
            f"client id [bold]{settings.get('ibkr_client_id', 7)}[/bold] "
            f"[dim](7497=TWS paper, 4002=Gateway paper)[/dim]\n"
            f"9. Back to main menu",
            title="Settings", border_style="cyan"))
        choice = Prompt.ask("Change which", choices=[str(i) for i in range(1, 10)], default="9")
        if choice == "1":
            raw = Prompt.ask("Tickers (comma-separated)", default=",".join(settings["tickers"]))
            tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
            if tickers:
                settings["tickers"] = tickers
        elif choice == "2":
            fast = IntPrompt.ask("Fast SMA", default=settings["sma_fast"])
            slow = IntPrompt.ask("Slow SMA", default=settings["sma_slow"])
            if fast >= slow:
                console.print("[red]Fast window must be smaller than slow window.[/red]")
            elif fast < 2:
                console.print("[red]Fast window must be at least 2.[/red]")
            else:
                settings["sma_fast"], settings["sma_slow"] = fast, slow
        elif choice == "3":
            c = FloatPrompt.ask("Cost per trade in % (0.1 = 0.1%)", default=settings["commission_pct"])
            if 0 <= c < 5:
                settings["commission_pct"] = c
            else:
                console.print("[red]Enter a value between 0 and 5.[/red]")
        elif choice == "4":
            cash = IntPrompt.ask("Starting cash ($)", default=settings["cash"])
            if cash >= 1000:
                settings["cash"] = cash
            else:
                console.print("[red]Minimum $1,000.[/red]")
        elif choice == "5":
            settings["start"] = Prompt.ask("History start (YYYY-MM-DD)", default=settings["start"])
            settings["end"] = Prompt.ask("History end (YYYY-MM-DD)", default=settings["end"])
            settings["in_sample_end"] = Prompt.ask("In-sample ends (YYYY-MM-DD)", default=settings["in_sample_end"])
            settings["out_of_sample_start"] = Prompt.ask("Out-of-sample starts (YYYY-MM-DD)",
                                                         default=settings["out_of_sample_start"])
            console.print("[yellow]Date range changed — refresh data (menu 8) to re-download.[/yellow]")
        elif choice == "6":
            settings["risk_engine"] = not settings.get("risk_engine", False)
            if settings["risk_engine"]:
                r = FloatPrompt.ask("Risk per trade in % of equity",
                                    default=settings.get("risk_pct_per_trade", 2.0))
                if 0.1 <= r <= 10:
                    settings["risk_pct_per_trade"] = r
                console.print("[green]Risk engine ON.[/green] [dim]Expect smaller drawdowns AND smaller returns.[/dim]")
            else:
                console.print("[yellow]Risk engine off — back to all-in baseline.[/yellow]")
        elif choice == "7":
            n = IntPrompt.ask("Hold top N tickers", default=settings.get("momentum_top_n", 3))
            lb = IntPrompt.ask("Lookback months", default=settings.get("momentum_lookback_m", 12))
            if 1 <= n <= len(settings["tickers"]) - 1 and 1 <= lb <= 24:
                settings["momentum_top_n"], settings["momentum_lookback_m"] = n, lb
            else:
                console.print("[red]N must fit the watchlist and lookback must be 1-24 months.[/red]")
        elif choice == "8":
            p = IntPrompt.ask("IBKR socket port (7497 TWS paper / 4002 Gateway paper)",
                              default=settings.get("ibkr_port", 7497))
            if p in (7496, 4001):
                console.print("[red]That's a LIVE port. This app only connects to paper "
                              "accounts — port unchanged.[/red]")
            elif p in (7497, 4002):
                settings["ibkr_port"] = p
            elif 1024 <= p <= 65535 and Confirm.ask(
                    f"[yellow]{p} is not a standard IBKR paper port (7497/4002). "
                    f"Use it anyway?[/yellow]", default=False):
                settings["ibkr_port"] = p
            else:
                console.print(f"[red]{p} rejected — port unchanged "
                              f"({settings.get('ibkr_port', 7497)}).[/red]")
            settings["ibkr_client_id"] = IntPrompt.ask(
                "Client id (any small integer, unique per connected app)",
                default=settings.get("ibkr_client_id", 7))
        else:
            save_settings(settings)
            console.print("[green]Settings saved.[/green]")
            return


# ---------------------------------------------------------------- main loop

MENU = """[bold cyan]1[/bold cyan]. SMA backtest — out-of-sample (2019 → now)   [dim]the number that matters[/dim]
[bold cyan]2[/bold cyan]. SMA backtest — in-sample (2010 → 2018)
[bold cyan]3[/bold cyan]. SMA backtest — full history
[bold cyan]4[/bold cyan]. Ticker deep dive (stats, trades, equity chart)
[bold cyan]5[/bold cyan]. Chart view (candlesticks + indicators: trend, volatility, volume, S/R)
[bold cyan]6[/bold cyan]. Momentum rotation backtest (portfolio)   [dim]the strategy that earned it[/dim]
[bold cyan]7[/bold cyan]. Kronos forecast (research agent)   [dim]backtested, no edge found — analysis only[/dim]
[bold cyan]8[/bold cyan]. Autotrade toggle   [dim yellow]EXPERIMENTAL — unattended, no edge shown[/dim yellow]
[bold cyan]9[/bold cyan]. Settings (tickers, SMA windows, costs, risk engine, momentum, IBKR)
[bold cyan]10[/bold cyan]. Refresh price data (force re-download)
[bold cyan]11[/bold cyan]. IBKR paper account (connect, positions, live 15-min bars)
[bold cyan]12[/bold cyan]. Quit"""


def autotrade_menu(settings: dict):
    """View/toggle autotrade_runner.py's unattended-hourly-rebalance
    switch. This app never places an order itself — flipping this on/off
    just writes trader_settings.json's "autotrade" block, which a separate
    launchd job (com.tradingbotapp.autotrade.plist, hourly during NYSE
    hours) reads before deciding whether to trade. See
    autotrade_runner.py's own docstring for the full picture.

    EXPERIMENTAL: both signals offered here were screened at this exact
    hourly cadence (KronosAI/kronos_ic_hourly.py, 2026-07-24) and showed
    NO measurable edge — momentum-hourly IC -0.037/48.5% hit rate,
    Kronos-hourly IC -0.081/46.4% hit rate (336 pooled pairs, both
    indistinguishable from noise). Turning this on runs either signal
    live on the PAPER account as a deliberate experiment, per the owner's
    explicit choice — not because either is validated. See CLAUDE.md."""
    autotrade = settings.get("autotrade", {"enabled": False, "signal": "momentum"})
    state = "[green]ON[/green]" if autotrade.get("enabled") else "[red]OFF[/red]"
    console.print(Panel(
        f"Current state: {state}   Signal: [bold]{autotrade.get('signal', 'momentum')}[/bold]\n\n"
        "[dim]Unattended hourly rebalancing via a separate launchd job "
        "(autotrade_runner.py) — no y/n prompt when on, RiskGuard still\n"
        "fully enforced. Both signals (momentum-hourly, kronos-hourly) "
        "showed NO measurable edge in a 2026-07-24 IC screen (see\n"
        "CLAUDE.md) — this is a deliberate live paper experiment, not a "
        "validated strategy. Paper account only.[/dim]",
        title="Autotrade [yellow]EXPERIMENTAL[/yellow]", border_style="yellow"))

    action = Prompt.ask("1=toggle on/off  2=change signal  3=back",
                        choices=["1", "2", "3"], default="3")
    if action == "1":
        autotrade["enabled"] = not autotrade.get("enabled", False)
        settings["autotrade"] = autotrade
        save_settings(settings)
        new_state = "ON" if autotrade["enabled"] else "OFF"
        console.print(f"[bold]Autotrade is now {new_state}.[/bold]")
        if autotrade["enabled"]:
            console.print("[yellow]Reminder: unattended, no edge shown in testing, "
                          "paper account only.[/yellow]")
    elif action == "2":
        sig = Prompt.ask("Signal", choices=["momentum", "kronos"],
                         default=autotrade.get("signal", "momentum"))
        autotrade["signal"] = sig
        settings["autotrade"] = autotrade
        save_settings(settings)
        console.print(f"[bold]Autotrade signal set to {sig}.[/bold]")


def main():
    console.print(Panel.fit(
        "[bold]Trader App[/bold] — SMA crossover backtester\n"
        "[dim]This app places no orders itself. Item 8 (Autotrade) toggles a separate\n"
        "unattended background job that does — see that menu before enabling it.[/dim]",
        border_style="cyan"))
    settings = load_settings()
    data = load_all_data(settings)

    while True:
        console.print(Panel(MENU, title="Menu", border_style="blue"))
        try:
            choice = Prompt.ask("Choose", choices=[str(i) for i in range(1, 13)], default="1")
        except (EOFError, KeyboardInterrupt):
            break
        try:
            if choice == "1":
                run_and_show(data, settings, "OUT-OF-SAMPLE",
                             settings["out_of_sample_start"], settings["end"])
            elif choice == "2":
                run_and_show(data, settings, "IN-SAMPLE",
                             settings["start"], settings["in_sample_end"])
            elif choice == "3":
                run_and_show(data, settings, "FULL HISTORY", settings["start"], settings["end"])
            elif choice == "4":
                deep_dive(data, settings)
            elif choice == "5":
                chart_view(data, settings)
            elif choice == "6":
                momentum_backtest(data, settings)
            elif choice == "7":
                kronos_menu(settings)
            elif choice == "8":
                autotrade_menu(settings)
            elif choice == "9":
                edit_settings(settings)
                data = load_all_data(settings)  # in case tickers changed
            elif choice == "10":
                data = load_all_data(settings, force=True)
            elif choice == "11":
                ibkr_menu(settings)
            elif choice == "12":
                break
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    console.print("[dim]Bye.[/dim]")


if __name__ == "__main__":
    main()
