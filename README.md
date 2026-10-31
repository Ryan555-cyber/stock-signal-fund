# Stock Signal Fund: free data pipeline

This is the data side of a hybrid system. The setup is free: no API keys, no paid data.

```
GitHub Actions (free)                     Claude scheduled tasks (your Claude plan)
-----------------------                   ------------------------------------------
Yahoo Finance (yfinance)  --+             read latest.json from stock-signal-data
SEC EDGAR (filings, Form 4, |-> clean ->  -> v3 committee logic (outlier engine,
  13D, XBRL fundamentals)   |   JSON  ->     leading signals, chokepoints, moonshots)
FRED (rates, credit, $, oil)-+   (public  -> calls + flash alerts + weekly memo
                         data repo)       -> claude.ai dashboard + phone/email
```

## Files

- **`pipeline/fetch_data.py`:** pulls, cleans and writes the JSON. Anything it can't verify is `null` and listed in `status.json`.
- **`pipeline/tickers.json`:** the names tracked. **Edit this to add or remove names** when the memos propose changes.
- **`latest.json`** (in `stock-signal-data`): the newest snapshot, overwritten each run.
- **`weekly.json`:** the Sunday snapshot, with SEC XBRL fundamentals added.
- **`universe.json`:** the Sunday scan of the S&P 500 and Nasdaq-100: biggest movers over 1, 3 and 12 months, plus consensus EPS revision leaders and laggards. This feeds the outlier engine.
- **`daily/`:** 120 days of history.
- **`prompt/spec.md`:** the v3.1 committee logic, for reference.

## Schedule (UTC)

- **Daily:** weekdays 21:25.
- **Weekly:** Sundays 18:10.
- **Manual:** Actions → data-pipeline → Run workflow.

## What each name gets

| Field | Source | Leading signal it feeds |
|---|---|---|
| Close, day %, 1/3/12-month return | Yahoo | Event triggers (>8% gap), outlier post-mortem |
| Forward P/E, FCF yield, margins, growth | Yahoo | Valuation, quality |
| Next-FY and current-FY consensus EPS now vs 30 and 90 days ago | Yahoo `eps_trend` | Revision velocity (the main outlier signal) |
| Up/down revisions, last 7 and 30 days | Yahoo `eps_revisions` | Revisions |
| Last earnings: surprise and 3-day reaction | Yahoo + prices | Reaction to news |
| Analyst actions, last 10 days | Yahoo | Events |
| Short % of float, days to cover | Yahoo | Positioning |
| 8-K items (auditor change and restatement flagged), 13D/G, proxy filings | SEC EDGAR | Events, activist desk, CPA red flags |
| Form 4 open-market buys and sells, with or without a 10b5-1 plan | SEC EDGAR | Insider signal |
| Revenue, FCF, FCF/net income, SBC, receivables vs sales (weekly) | SEC XBRL | Quality, CPA checks |
| 10y, 10y–2y, HY OAS, dollar, oil, copper | FRED | Regime read |

## Setup (two repos)

| Repo | Visibility | Holds |
|---|---|---|
| `stock-signal-fund` | **Private** | This code, the spec and the prompts |
| `stock-signal-data` | **Public** | Only the cleaned public market data. Claude's runs can only read public GitHub files. |

1. **Create the data repo.** Create a **public** repo named `stock-signal-data`, and tick **Add a README** so it isn't empty.
2. **Create a token.** Go to GitHub → profile photo → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
   - Repository access: **Only select repositories** → `stock-signal-data`.
   - Permissions: **Contents → Read and write**.
   - Expiration: up to 1 year.
   - Copy the token.
3. **Save the token here.** In this private repo, go to Settings → Secrets and variables → Actions → New repository secret. Name it `DATA_REPO_TOKEN` and paste the token.
4. **Optional:** add a variable named `SEC_USER_AGENT` with the value `your-name your-email`.
5. **First run:** Actions → data-pipeline → Run workflow → `weekly`. When it finishes, `stock-signal-data` should contain `status.json` with `"ok": true`.

---

Not financial advice; Claude is not a licensed advisor.
