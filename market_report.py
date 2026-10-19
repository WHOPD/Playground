#!/usr/bin/env python3
"""Market and economic summary report.

Pulls rates, credit, economic, market, relative-value, calendar and headline
data, then writes one self-contained HTML file (charts are embedded).

API keys come from environment variables:
  FRED_API_KEY   needed for the rates, credit and economy sections
                 (free: https://fred.stlouisfed.org/docs/api/api_key.html)
  FMP_API_KEY    needed for the economic and earnings calendars
                 (https://site.financialmodelingprep.com/developer/docs)
  EARNINGS_UNIVERSE_FILE   optional .xlsx/.csv with a 'symbol' column to filter
                 the earnings calendar; defaults to the S&P 1500 from Wikipedia

Any section whose data can't be fetched is skipped with a note; the rest of
the report still builds.

Usage:
  python market_report.py
  python market_report.py --years 5 --out reports/today.html
"""

import argparse
import base64
import html
import io
import os
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
FRED_SERIES = {
    "FEDFUNDS": "Fed Funds",
    "SOFR": "SOFR",
    "DGS2": "2Y UST",
    "DGS3": "3Y UST",
    "DGS5": "5Y UST",
    "DGS7": "7Y UST",
    "DGS10": "10Y UST",
    "DGS20": "20Y UST",
    "DGS30": "30Y UST",
    "T10Y2Y": "2s10s",
    "DFII5": "5Y TIPS",
    "DFII10": "10Y TIPS",
    "DFII20": "20Y TIPS",
    "DFII30": "30Y TIPS",
    "BAMLH0A0HYM2EY": "HY Yield",
    "BAMLC0A4CBBBEY": "BBB Corp Yield",
    "BAMLC0A3CAEY": "A Corp Yield",
    "BAMLC0A2CAAEY": "AA Corp Yield",
    "BAMLC0A1CAAAEY": "AAA Corp Yield",
    "BAMLH0A0HYM2": "HY OAS",
    "BAMLC0A4CBBB": "BBB OAS",
    "BAMLC0A3CA": "A OAS",
    "BAMLC0A2CAA": "AA OAS",
    "BAMLC0A1CAAA": "AAA OAS",
    "BAMLC0A0CM": "IG OAS",
    "VIXCLS": "VIX",
    "PAYEMS": "Nonfarm Payrolls (k)",
    "UNRATE": "Unemployment Rate",
    "CPIAUCSL": "CPI Index",
    "CPILFESL": "Core CPI Index",
    "PCEPILFE": "Core PCE Index",
    "T5YIE": "5Y Breakeven",
    "GDP": "Nominal GDP ($B)",
    "GDPC1": "Real GDP ($B, chained 2017)",
    "ICSA": "Initial Jobless Claims",
    "RSAFS": "Retail Sales ($M)",
}

UST = {"2Y": "DGS2", "3Y": "DGS3", "5Y": "DGS5", "7Y": "DGS7",
       "10Y": "DGS10", "20Y": "DGS20", "30Y": "DGS30"}
TIPS = {"5Y": "DFII5", "10Y": "DFII10", "20Y": "DFII20", "30Y": "DFII30"}
OAS = {"BBB": "BAMLC0A4CBBB", "A": "BAMLC0A3CA", "AA": "BAMLC0A2CAA",
       "AAA": "BAMLC0A1CAAA", "IG": "BAMLC0A0CM", "HY": "BAMLH0A0HYM2"}

# Yield-curve comparisons, in calendar days
CURVE_LOOKBACKS = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}

MARKET_TICKERS = {
    "^GSPC": "S&P 500",
    "^FTSE": "FTSE 100",
    "^RUT": "Russell 2000",
    "^VIX": "CBOE Volatility Index",
    "TLT": "iShares 20+ Treasury Bond ETF",
    "AGG": "iShares Core US Aggregate Bond ETF",
    "LQD": "iShares IG Corp Bond ETF",
    "EURUSD=X": "EUR:USD",
    "GBPUSD=X": "GBP:USD",
    "JPY=X": "USD:JPY",
    "CL=F": "Crude Oil",
    "GC=F": "Gold",
}

REL_VALUE_ASSETS = ["SPY", "QQQ", "MDY", "IWM", "EFA", "EEM", "LQD", "TLT",
                    "TIP", "JNK", "GLD", "VNQ", "DBC", "USO"]
REL_VALUE_SECTORS = ["XLK", "XLV", "XLF", "XLY", "XLC", "XLI", "XLP", "XLE",
                     "XLU", "XLB", "XLRE"]

NEWS_TICKERS = ["^GSPC", "^DJI", "^IXIC"]

FMP_URL = "https://financialmodelingprep.com/stable"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def zstats(s):
    """Latest value, mean, and z-score of a series (the notebook's curr/avg/z block)."""
    s = s.dropna()
    return {"asof": s.index[-1].date(), "current": s.iloc[-1],
            "mean": s.mean(), "z": (s.iloc[-1] - s.mean()) / s.std()}


def as_of_or_before(df, when):
    """Last row on or before `when` (handles weekends and holidays)."""
    return df.loc[:when].iloc[-1]


def img(fig):
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return f'<img src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}" alt="">'


def style(ax, title, ylabel):
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)


def line_chart(data, title, ylabel, mean_line=True):
    fig, ax = plt.subplots(figsize=(8, 4))
    if isinstance(data, pd.DataFrame):
        for col in data.columns:
            ax.plot(data.index, data[col], label=col)
        ax.legend()
    else:
        ax.plot(data.index, data.values)
        if mean_line:
            ax.axhline(data.mean(), linestyle="--", alpha=0.7)
    style(ax, title, ylabel)
    fig.autofmt_xdate()
    return img(fig)


def bar_chart(s, title, ylabel, width):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(s.index, s.values, width=width, color="navy")
    style(ax, title, ylabel)
    return img(fig)


def card(label, s, unit="", fmt="{:,.1f}"):
    st = zstats(s)
    return (f'<div class="card"><div class="label">{label}</div>'
            f'<div class="value">{fmt.format(st["current"])}{unit}</div>'
            f'<div class="sub">mean {fmt.format(st["mean"])}{unit} · z {st["z"]:+.1f}'
            f' · as of {st["asof"]}</div></div>')


def metric(label, s, unit="", fmt="{:,.1f}", chart=None):
    """Stat card plus chart for one series."""
    chart = chart or line_chart(s, label, unit.strip() or "Level")
    return f'<div class="metric">{card(label, s, unit, fmt)}{chart}</div>'


def table(df, formats=None, index=True):
    df = df.copy()
    for col, fmt in (formats or {}).items():
        df[col] = df[col].map(lambda x: "" if pd.isna(x) else fmt.format(x))
    return df.to_html(classes="tbl", border=0, na_rep="", index=index, escape=True)


# --------------------------------------------------------------------------
# Data fetching
# --------------------------------------------------------------------------
def fetch_fred(api_key, start):
    from fredapi import Fred

    fred = Fred(api_key=api_key)
    out, failed = {}, []
    for sid in FRED_SERIES:
        try:
            out[sid] = fred.get_series(sid, observation_start=start).dropna()
        except Exception:
            failed.append(sid)
    if not out:
        raise RuntimeError("no FRED series could be downloaded")
    return out, failed


def fmp(endpoint, key, **params):
    r = requests.get(f"{FMP_URL}/{endpoint}", params={**params, "apikey": key}, timeout=30)
    r.raise_for_status()
    return pd.DataFrame(r.json())


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
def section_snapshot(f, failed):
    rows = [(FRED_SERIES[sid], s.iloc[-1], s.index[-1].date()) for sid, s in f.items() if len(s)]
    df = pd.DataFrame(rows, columns=["Series", "Latest", "As of"])
    note = f'<p class="note">Not downloaded: {", ".join(failed)}</p>' if failed else ""
    return note + table(df, {"Latest": "{:,.2f}"}, index=False)


def section_rates(f, start):
    ust = pd.concat({k: f[v] for k, v in UST.items()}, axis=1).dropna()
    tips = pd.concat({k: f[v] for k, v in TIPS.items()}, axis=1).dropna()
    recent_ust = ust.loc[start:]

    out = ['<div class="grid">',
           metric("2s10s curve", (recent_ust["10Y"] - recent_ust["2Y"]) * 100, " bps"),
           metric("10s30s curve", (recent_ust["30Y"] - recent_ust["10Y"]) * 100, " bps"),
           metric("Fed Funds", f["FEDFUNDS"].loc[start:], "%", "{:.2f}"),
           metric("SOFR", f["SOFR"].loc[start:], "%", "{:.2f}"),
           "</div>"]

    for name, df in (("US Treasury yields", ust), ("US TIPS yields", tips)):
        last = df.index[-1]
        prev = df.loc[:last - timedelta(days=7)]
        wow = pd.DataFrame({"Current": df.iloc[-1], "1W ago": prev.iloc[-1]})
        wow["Change"] = wow["Current"] - wow["1W ago"]
        cols = [c for c in df.columns if c in ("2Y", "5Y", "10Y", "20Y", "30Y")]
        out.append(f"<h3>{name}</h3>")
        out.append(line_chart(df.loc[start:, cols], name, "Yield %"))
        out.append(f'<p class="note">{last.date()} vs {prev.index[-1].date()}</p>')
        out.append(table(wow, {"Current": "{:.2f}", "1W ago": "{:.2f}", "Change": "{:+.2f}"}))

    # Yield curve today vs calendar-day lookbacks
    last = ust.index[-1]
    mats = [int(m[:-1]) for m in ust.columns]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(mats, ust.iloc[-1], marker="o", linewidth=2.5, label=f"Current ({last.date()})")
    curve = pd.DataFrame({"Current": ust.iloc[-1].values}, index=ust.columns)
    for label, days in CURVE_LOOKBACKS.items():
        then = as_of_or_before(ust, last - timedelta(days=days))
        ax.plot(mats, then, marker=".", alpha=0.8, label=f"{label} ago ({then.name.date()})")
        curve[f"Chg vs {label}"] = (ust.iloc[-1] - then).values
    style(ax, "US Treasury curve", "Yield %")
    ax.set_xlabel("Maturity (years)")
    ax.legend(fontsize=8)
    out.append("<h3>Treasury curve changes</h3>")
    out.append(img(fig))
    out.append(table(curve, {c: ("{:.2f}" if c == "Current" else "{:+.2f}") for c in curve.columns}))
    return "\n".join(out)


def section_credit(f, start):
    oas = pd.concat({k: f[v] for k, v in OAS.items()}, axis=1).dropna().loc[start:] * 100
    by_rating = oas[["BBB", "A", "AA", "AAA"]]
    latest = by_rating.tail(1).copy()
    latest.index = [latest.index[0].date()]
    return "\n".join([
        line_chart(by_rating, "IG credit spreads by rating (OAS)", "Spread (bps)"),
        table(latest, {c: "{:.0f}" for c in latest.columns}),
        '<div class="grid">',
        metric("BBB − AAA OAS", oas["BBB"] - oas["AAA"], " bps"),
        metric("HY − IG OAS", oas["HY"] - oas["IG"], " bps"),
        metric("IG OAS", oas["IG"], " bps"),
        metric("High yield OAS", oas["HY"], " bps"),
        "</div>",
    ])


def section_economy(f, start):
    w = lambda s: s.loc[start:]  # noqa: E731
    gdp_qoq = w(((f["GDPC1"].pct_change() + 1) ** 4 - 1) * 100)
    nfp_chg = w(f["PAYEMS"].diff())
    return "\n".join([
        '<div class="grid">',
        metric("Real GDP growth (QoQ annualized)", gdp_qoq, "%",
               chart=bar_chart(gdp_qoq, "Real GDP growth (QoQ annualized)", "%", 60)),
        metric("Change in nonfarm payrolls", nfp_chg, "k",
               chart=bar_chart(nfp_chg, "Change in nonfarm payrolls", "Thousands", 20)),
        metric("Unemployment rate", w(f["UNRATE"]), "%"),
        metric("Initial jobless claims", w(f["ICSA"]), "", "{:,.0f}"),
        metric("CPI (YoY)", w(f["CPIAUCSL"].pct_change(12) * 100), "%"),
        metric("Core CPI (YoY)", w(f["CPILFESL"].pct_change(12) * 100), "%"),
        metric("Core PCE (YoY)", w(f["PCEPILFE"].pct_change(12) * 100), "%"),
        metric("Retail sales (MoM)", w(f["RSAFS"].pct_change() * 100), "%"),
        metric("5Y breakeven inflation", w(f["T5YIE"]), "%", "{:.2f}"),
        metric("VIX", w(f["VIXCLS"]), "", "{:.2f}"),
        "</div>",
    ])


def add_indicators(df):
    c = df["Close"]
    df["MA50"] = c.rolling(50).mean()
    df["MA200"] = c.rolling(200).mean()
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["BB_mid"], df["BB_up"], df["BB_lo"] = mid, mid + 2 * sd, mid - 2 * sd
    # RSI(14) with Wilder's smoothing
    chg = c.diff().dropna()
    gain = chg.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    loss = (-chg.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + gain / loss)
    return df


def ohlc_chart(df, title):
    x = np.arange(len(df))
    colors = np.where(df["Close"] > df["Open"], "green",
                      np.where(df["Close"] < df["Open"], "red", "gray"))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.vlines(x, df["Low"], df["High"], color=colors, linewidth=1)
    ax1.hlines(df["Open"], x - 0.5, x, color="black", linewidth=1)
    ax1.hlines(df["Close"], x, x + 0.5, color="black", linewidth=1)
    ax1.plot(x, df["MA50"], color="magenta", linewidth=1, label="50-day MA")
    ax1.plot(x, df["MA200"], color="blue", linewidth=1, label="200-day MA")
    ax1.plot(x, df["BB_up"], color="grey", linewidth=1, label="Bollinger 20d ±2σ")
    ax1.plot(x, df["BB_mid"], color="grey", linewidth=1, linestyle=":")
    ax1.plot(x, df["BB_lo"], color="grey", linewidth=1)
    ax1.set_title(title)
    ax1.set_ylabel("Price")
    ax1.grid(True, alpha=0.4)
    ax1.legend(fontsize=8)
    ax2.plot(x, df["RSI"], color="darkorange", linewidth=1.5)
    ax2.axhline(30, color="green", linewidth=1)
    ax2.axhline(70, color="red", linewidth=1)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI (14)")
    step = max(1, len(df) // 10)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels(df.index[::step].strftime("%Y-%m-%d"), rotation=45, ha="right")
    return img(fig)


def section_markets():
    raw = yf.download(list(MARKET_TICKERS), period="2y", interval="1d",
                      group_by="ticker", progress=False)
    rows, charts = [], []
    for ticker, name in MARKET_TICKERS.items():
        try:
            df = add_indicators(raw[ticker].dropna(subset=["Close"]).copy())
        except KeyError:
            continue
        df = df[df.index > df.index[-1] - timedelta(days=365)]
        px, prev, last = df["Close"].iloc[-1], df["Close"].iloc[-2], df.iloc[-1]
        rows.append({
            "Name": name, "Ticker": ticker, "As of": df.index[-1].date(),
            "Price": px, "Chg": px - prev, "Chg %": px / prev - 1,
            "1Y low": min(df["Low"].min(), px), "1Y high": max(df["High"].max(), px),
            "50d MA": last["MA50"], "200d MA": last["MA200"], "RSI": last["RSI"],
            "vs BB upper": px / last["BB_up"] - 1, "vs BB mid": px / last["BB_mid"] - 1,
            "vs BB lower": px / last["BB_lo"] - 1,
        })
        charts.append(ohlc_chart(df, f"{name} ({ticker})"))
    if not rows:
        raise RuntimeError("no market data returned")
    num = "{:,.2f}"
    pct = "{:+.2%}"
    summary = table(pd.DataFrame(rows), {
        "Price": num, "Chg": "{:+,.2f}", "Chg %": pct, "1Y low": num, "1Y high": num,
        "50d MA": num, "200d MA": num, "RSI": "{:.1f}",
        "vs BB upper": pct, "vs BB mid": pct, "vs BB lower": pct}, index=False)
    return summary + '<div class="charts">' + "\n".join(charts) + "</div>"


def long_name(ticker):
    try:
        return yf.Ticker(ticker).info.get("longName") or ticker
    except Exception:
        return ticker


def relative_value(tickers):
    """For each ticker, z-score of its 1Y cumulative return gap vs every other
    ticker, averaged across pairs (same method as the original notebook)."""
    start = datetime.today() - timedelta(days=365)
    data = yf.download(tickers, period="2y", progress=False)
    close = data["Close"][tickers].loc[start:].ffill()
    high, low = data["High"][tickers].loc[start:], data["Low"][tickers].loc[start:]
    cum = (close.pct_change().fillna(0) + 1).cumprod() - 1

    rows = {}
    for lt in tickers:
        zs = []
        for st in tickers:
            if st != lt:
                delta = (cum[lt] - cum[st]).dropna()
                zs.append((delta.iloc[-1] - delta.mean()) / delta.std())
        zs = pd.Series(zs)
        px, lo = close[lt].iloc[-1], low[lt].min()
        rows[lt] = {"Name": long_name(lt), "Price": px, "52w high": high[lt].max(),
                    "52w low": lo, "% off 52w low": px / lo - 1,
                    "Z-score": zs.mean(), "Z st.dev": zs.std()}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_values("Z-score")
    df.index.name = "Ticker"
    return table(df, {"Price": "{:,.2f}", "52w high": "{:,.2f}", "52w low": "{:,.2f}",
                      "% off 52w low": "{:.0%}", "Z-score": "{:+.2f}", "Z st.dev": "{:.2f}"})


def section_rel_value():
    return ("<h3>Broad asset classes</h3>" + relative_value(REL_VALUE_ASSETS)
            + "<h3>S&amp;P 500 sectors</h3>" + relative_value(REL_VALUE_SECTORS))


def earnings_universe():
    path = os.environ.get("EARNINGS_UNIVERSE_FILE")
    if path:
        df = pd.read_excel(path) if path.lower().endswith((".xlsx", ".xls")) else pd.read_csv(path)
        return set(df["symbol"].astype(str).str.upper()), os.path.basename(path)
    symbols = set()
    for n in (500, 400, 600):
        page = requests.get(f"https://en.wikipedia.org/wiki/List_of_S%26P_{n}_companies",
                            headers={"User-Agent": "market_report.py"}, timeout=30)
        page.raise_for_status()
        t = next(t for t in pd.read_html(io.StringIO(page.text)) if "Symbol" in t.columns)
        symbols |= set(t["Symbol"].astype(str).str.replace(".", "-", regex=False).str.upper())
    return symbols, "S&P 1500 (Wikipedia)"


def section_calendars(key):
    today = datetime.today()
    window = {"from": (today - timedelta(days=7)).strftime("%Y-%m-%d"),
              "to": (today + timedelta(days=7)).strftime("%Y-%m-%d")}

    econ = fmp("economic-calendar", key, **window)
    if "country" in econ.columns:
        econ = econ[econ["country"] == "US"]
    econ = econ[econ["impact"] == "High"].sort_values("date")
    econ = econ[[c for c in ["date", "event", "previous", "estimate", "actual", "change",
                             "changePercentage", "unit"] if c in econ.columns]]

    earn = fmp("earnings-calendar", key, **window)
    universe, source = earnings_universe()
    earn = earn[earn["symbol"].isin(universe)].sort_values(["date", "symbol"])
    for col in ("revenueActual", "revenueEstimated"):
        earn[col] = earn[col] / 1e9
    earn = earn.rename(columns={"revenueActual": "revenueActual ($B)",
                                "revenueEstimated": "revenueEstimated ($B)"})
    earn = earn[[c for c in ["symbol", "date", "epsActual", "epsEstimated", "revenueActual ($B)",
                             "revenueEstimated ($B)"] if c in earn.columns]]

    return ("<h3>Economic calendar (US, high impact)</h3>"
            + table(econ, {"previous": "{:,.2f}", "estimate": "{:,.2f}", "actual": "{:,.2f}",
                           "change": "{:+,.2f}", "changePercentage": "{:+.1f}%"}, index=False)
            + f"<h3>Earnings calendar ({html.escape(source)})</h3>"
            + table(earn, {"epsActual": "{:.2f}", "epsEstimated": "{:.2f}",
                           "revenueActual ($B)": "{:,.2f}", "revenueEstimated ($B)": "{:,.2f}"},
                    index=False))


def section_headlines(limit=15):
    items = {}
    for t in NEWS_TICKERS:
        for a in yf.Ticker(t).news or []:
            c = a.get("content", a)
            url = ((c.get("canonicalUrl") or {}).get("url")
                   or (c.get("clickThroughUrl") or {}).get("url") or a.get("link"))
            if not url or url in items:
                continue
            published = c.get("pubDate") or (
                datetime.fromtimestamp(a["providerPublishTime"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if a.get("providerPublishTime") else "")
            items[url] = {"title": c.get("title", ""), "summary": c.get("summary", ""),
                          "publisher": (c.get("provider") or {}).get("displayName") or a.get("publisher", ""),
                          "published": published}
    if not items:
        raise RuntimeError("no headlines returned")
    top = sorted(items.items(), key=lambda kv: kv[1]["published"], reverse=True)[:limit]
    li = [f'<li><a href="{html.escape(url)}">{html.escape(n["title"])}</a>'
          f'<div class="sub">{html.escape(n["publisher"])} · {html.escape(n["published"])}</div>'
          f'<div>{html.escape(n["summary"])}</div></li>' for url, n in top]
    return '<ul class="news">' + "\n".join(li) + "</ul>"


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
CSS = """
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#fafafa;color:#1d1d1f}
main{max-width:1200px;margin:auto;padding:16px}
h1{margin:8px 0 0}h2{margin-top:40px;border-bottom:2px solid #ddd;padding-bottom:4px}
nav a{margin-right:14px}
.note,.sub{color:#666;font-size:12px}.err{color:#b00020}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:16px}
.metric{background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:12px}
.card .label{font-weight:600}.card .value{font-size:22px;font-weight:600}
img{max-width:100%;height:auto;display:block;margin:8px 0}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,560px),1fr));gap:12px}
.tbl{border-collapse:collapse;font-size:13px;margin:8px 0;display:block;overflow-x:auto;max-width:100%}
.tbl th,.tbl td{padding:4px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap}
.tbl th:first-child,.tbl td:first-child{text-align:left}
.news li{margin-bottom:12px}
"""


def build_report(years):
    start = pd.Timestamp.today().normalize() - pd.DateOffset(years=years)
    fred_key, fmp_key = os.environ.get("FRED_API_KEY"), os.environ.get("FMP_API_KEY")

    f, failed, fred_error = None, [], "FRED_API_KEY is not set"
    if fred_key:
        try:
            # one extra year so YoY changes and 1Y curve lookbacks cover the whole window
            f, failed = fetch_fred(fred_key, start - pd.DateOffset(years=1))
        except Exception as e:
            fred_error = f"FRED download failed: {e}"

    def needs_fred(build):
        if f is None:
            raise RuntimeError(fred_error)
        return build()

    def needs_fmp(build):
        if not fmp_key:
            raise RuntimeError("FMP_API_KEY is not set")
        return build()

    plan = [
        ("Latest FRED data", lambda: needs_fred(lambda: section_snapshot(f, failed))),
        ("Interest rates", lambda: needs_fred(lambda: section_rates(f, start))),
        ("Credit spreads", lambda: needs_fred(lambda: section_credit(f, start))),
        ("Economy", lambda: needs_fred(lambda: section_economy(f, start))),
        ("Markets", section_markets),
        ("Relative value", section_rel_value),
        ("Calendars", lambda: needs_fmp(lambda: section_calendars(fmp_key))),
        ("Headlines", section_headlines),
    ]

    sections = []
    for title, build in plan:
        print(f"Building {title}...")
        try:
            body = build()
        except Exception as e:
            body = f'<p class="err">Skipped: {html.escape(str(e))}</p>'
            print(f"  skipped: {e}")
        sections.append((title, body))

    anchor = lambda t: t.lower().replace(" ", "-")  # noqa: E731
    nav = " ".join(f'<a href="#{anchor(t)}">{t}</a>' for t, _ in sections)
    body = "\n".join(f'<h2 id="{anchor(t)}">{t}</h2>\n{b}' for t, b in sections)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Market Report {stamp[:10]}</title><style>{CSS}</style></head><body><main>"
            f'<h1>Market and Economic Summary</h1><p class="note">Generated {stamp} · '
            f"stats use a {years}-year window (credit spreads: FRED limits ICE data to ~3 years)</p>"
            f"<nav>{nav}</nav>{body}</main></body></html>")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, default=5, help="history window for FRED stats (default 5)")
    ap.add_argument("--out", help="output file (default reports/market_report_YYYY-MM-DD.html)")
    args = ap.parse_args()

    out = args.out or os.path.join("reports", f"market_report_{datetime.now():%Y-%m-%d}.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build_report(args.years))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
