# Playground

## Market report

`market_report.py` builds a market and economic summary as one HTML file:
rates, credit spreads, economic data (FRED), market charts with moving averages,
RSI and Bollinger bands, relative-value tables (Yahoo Finance), economic and
earnings calendars (Financial Modeling Prep), and headlines.

```bash
pip install -r requirements.txt
export FRED_API_KEY=...   # free: https://fred.stlouisfed.org/docs/api/api_key.html
export FMP_API_KEY=...    # for the calendars: https://site.financialmodelingprep.com
python market_report.py   # writes reports/market_report_YYYY-MM-DD.html
```

Options: `--years 5` (history window for FRED stats), `--out path.html`.
Set `EARNINGS_UNIVERSE_FILE` to an .xlsx/.csv with a `symbol` column to choose
which companies show in the earnings calendar (default: S&P 1500 from Wikipedia).
A section whose data or key is missing is skipped with a note; the rest still builds.

## Claude setup

- `CLAUDE.md`: coding guidelines from [andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills).
- `.claude/skills/i-have-adhd`: [i-have-adhd](https://github.com/ayghri/i-have-adhd) answer-style skill. Turn on with `/i-have-adhd`, off with "stop adhd mode".
