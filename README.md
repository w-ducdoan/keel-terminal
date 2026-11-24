# Keel: a free market terminal in the browser

Keel is a Bloomberg-style market terminal built on free data sources. It runs as a small Python web app.
This is the **public edition**: market data and company research only. There's no portfolio, no account and no broker connection.

## What it does

| Code | What you get |
| :-- | :-- |
| `MKTS` | World markets: equity indexes, rates and the curve, FX, commodities, credit, economic data, and a market news wire |
| any ticker | A security window with a function strip, Bloomberg style. Type `TOST`, then `GP`, `FA` or `RV`, or `TOST FA` in one step |
| `DES` | The equity Launchpad: profile, analysts (`ANR`), corporate actions (`CACS`), relative value (`RV`), estimates (`EE`), holders (`HDS`), insiders (`INS`), supply chain (`SPLC`), company news (`CN`), all in one page |
| `GP` | Price chart with ranges, compare against other tickers, and point-to-point measure. **Double-click any point to see why it moved**: the move against SPY, volume, the headlines around that day and the SEC filings |
| `FA` | Full financial statements from SEC XBRL filings (US GAAP and IFRS), annual and quarterly, with ratios and valuation |
| `CN` | Company news back up to two years (LATEST, 1W, 1M to 2Y, or any number of days) |
| `N` | News reader: the market wire by topic, with a look-back from 1 day to 1 month |
| `MAP` | S&P 500 heat map: size is market cap, color is the move over the period |
| `CAL` | Catalyst calendar: FOMC, macro releases, options expiry, index rebalances, market holidays |
| `EVT` | Event-driven feed from SEC filings: spin-offs, tender offers, merger votes, going-private deals, new activist stakes |
| `FLNG` | 13F fund holdings: search any fund that files 13Fs and see its holdings and quarter-on-quarter changes |
| economic data | Type `core CPI`, `unemployment` or a FRED id: the series opens in its own window |

Press `/` or start typing anywhere to use the command bar. `Esc` closes the top window.

## Run it on your computer

You need Python 3.12 or newer.

```
git clone <this repo>
cd keel-terminal
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip install -r requirements.txt
export KEEL_SEC_CONTACT="Keel personal research you@example.com"   # Windows PowerShell: $env:KEEL_SEC_CONTACT = "..."
export FRED_API_KEY="your key"                 # optional: free key from fred.stlouisfed.org, for rates and macro series
python run.py
```

Then open http://localhost:6870.

SEC.gov asks every automated user to put a contact email in their requests, which is what `KEEL_SEC_CONTACT` is for.
Without it, the FA, filings, 13F and event pages say so and the rest still works.

## Host it

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/w-ducdoan/keel-terminal)

The repo includes a `Dockerfile` and a `render.yaml`, so the button above (or **New > Blueprint** on [Render](https://render.com)) sets it up.
Then set `KEEL_SEC_CONTACT`, and optionally `FRED_API_KEY`, in the service's environment. The free plan sleeps when idle, so the first visit after a while takes about a minute.

## Data and terms

- **Prices, fundamentals and company news:** Yahoo Finance through the open-source `yfinance` library. Yahoo's terms allow personal use only, so running a public copy for others is at your own risk; a licensed data provider is the clean way to do that.
- **Headlines:** Google News search RSS, plus public RSS feeds.
- **Filings, financial statements, 13F, events:** SEC EDGAR.
- **Rates and economic data:** FRED (St. Louis Fed). Some series, such as the ICE BofA bond indexes, belong to their owners.
- **Short interest:** FINRA.

Keel is a research tool, not investment advice.
Its numbers come from free sources that can be late or wrong, so check anything important against the original filing or the exchange.
