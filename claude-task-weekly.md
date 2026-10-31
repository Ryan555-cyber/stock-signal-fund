# TASK: Weekly memo "The Book" (Sundays). On the first Sunday of the month: monthly deep dive. In Feb, May, Aug and Nov: add the quarterly re-underwrite.

Check today's date (America/Chicago) to pick the mode:

| Date | Mode |
|---|---|
| Day 1–7 of the month | MONTHLY |
| Day 1–7 and the month is Feb, May, Aug or Nov | MONTHLY + QUARTERLY |
| Any other Sunday | WEEKLY |

1. **Load state.** Do steps 1–2 of the operating logic below. Use `weekly.json` (SEC fundamentals) and `universe.json` (movers and revision leaders). Re-underwrite any name still on a v1 signal (the migration).
2. **WEEKLY (every Sunday):**
   - **Rescore every watchlist name** fully under spec §5: valuation, quality with CPA checks from `sec.fundamentals`, moat, revisions from `eps_trend`, and leading signals (real-economy tells, insiders from Form 4, reaction from `last_earnings`, positioning).
   - **Run the outlier-mode check** (spec §4.2) on every name and on the top 15 of `universe.json` `revision_leaders`.
   - **Refresh the macro read** (spec §9).
   - **Write a variant perception** (spec §4.4) for any new or changed BUY, ADD or SHORT-THESIS.
   - **Activist and special-situations desk:** use 13D/G and proxy filings from the pipeline, plus web search for new spin-offs and activist campaigns this week.
3. **MONTHLY (also):**
   - **Outlier post-mortem** (spec §4.1) from `universe.json` movers (1m/3m/12m top 20 and bottom 10). Include the semis/memory seed case until it's settled. Use no look-ahead: only data dated before each decision point.
   - **Module A, chokepoints** (spec §7).
   - **Module B, the moonshot radar** (spec §8).
   - **Scorecard and override log** (spec §10). Compute each past call's return since its signal date from the dashboard `signals` and current pipeline prices, and compare it with the S&P 500 (SPY's return over the same period, from the web if needed).
   - **1–2 full write-ups.**
   - **At most 2 proposed rule changes.** A change applies only after 2 consecutive monthly confirmations, and only with Aryan's approval.
   - **Bench candidates:** propose up to 3 names to add to `pipeline/tickers.json`.
4. **QUARTERLY (also):** re-test every thesis and kill criterion, backtest the rules against the quarter's outliers, and prune the book.
5. **Write the dashboard:** `watchlist`, all 4 `macro` rows, and `signals` rows for changed calls.
6. **Publish the memo** in the spec §11.2 format (§11.3 when monthly), 900–1,500 words for weekly and longer for monthly:
   - Send it with SendUserMessage, plus a PushNotification whose summary is the call sheet's headline.
   - Save it to the project with `project_write`, path `claude/memos/<YYYY-MM-DD>-<weekly|monthly>.md`.
   - Link the dashboard.
