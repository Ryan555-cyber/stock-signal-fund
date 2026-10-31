# TASK: Daily flash (weekdays, after the GitHub data pipeline runs)

**Goal:** catch events that change a thesis, and stay silent when nothing changed.

1. **Load state.** Do steps 1–2 of the operating logic below: pipeline data plus dashboard state.
2. **Scan for flash triggers on every watchlist name** (spec §2 and §6.3), using the pipeline first:
   - `abs(day_change_pct) > 8`: read the news before any call.
   - Earnings reported, or `next_earnings` is today. **No new call on earnings day until the report is read.**
   - A new `sec.flags` entry: 8-K item 4.01 or 4.02, a 13D/G, a proxy filing, or insider buying ≥$100k.
   - An analyst action from a major bank in `analyst_actions_10d` dated today.
   - Revision velocity crosses the spec §4.2 thresholds: `eps_trend.+1y.chg_90d_pct` ≥ 30, or `0y` ≥ 20. The name enters outlier mode only if at least 3 triggers are verified.
   - A kill criterion is hit. Check each name's note.
3. **Re-underwrite only the names that were hit.** An earnings or guidance event gets a full rescore under spec §5, plus the variant view and red team. Other names keep their scores; only refresh `price` and `as_of`.
4. **Apply the call rules (spec §6).** Event-driven changes may change a call the same day. Score-only drift needs 2 runs. Macro never changes a call.
5. **Write the updated `watchlist` to the dashboard**, and append a `signals` row for each call that changed.
6. **Report:**
   - **If anything fired:** send ONE flash per spec §11.1 by SendUserMessage, plus a PushNotification.
     - Headline example: "2 calls: BUY NVDA (4/5), EXIT XYZ (thesis broken)".
     - Then for each name: call and conviction, price, the trigger, 2–3 sourced facts, what it means for the thesis, kill criteria, and the red team in one line.
     - Link the dashboard.
   - **If nothing fired:** send the single line "Daily scan done — no new signals." Don't send a push notification.
