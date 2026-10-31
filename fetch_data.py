"""Free market-data pipeline for the Stock Signal Fund.

Runs on GitHub Actions (no API keys). Pulls from free sources, cleans, and writes JSON
that the Claude scheduled tasks read from raw.githubusercontent.com.

Sources: Yahoo Finance via yfinance (prices, forward P/E, EPS estimate trend, earnings dates,
analyst actions, short interest), SEC EDGAR (filings: 8-K, Form 4 insider trades, 13D/G,
XBRL fundamentals), FRED (rates, credit, dollar, oil, copper).

Rule: never fabricate. Anything that fails is null and listed in status.errors.

usage: python pipeline/fetch_data.py daily|weekly
"""
import datetime as dt, io, json, math, os, re, sys, time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

MODE = sys.argv[1] if len(sys.argv) > 1 else "daily"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR") or os.path.join(ROOT, "data")
SEC_UA = os.environ.get("SEC_USER_AGENT", "stock-signal-fund research contact@example.com")
TODAY = dt.datetime.utcnow().date()
ERRORS = []
START = time.time()


def err(where, e):
    ERRORS.append(f"{where}: {type(e).__name__}: {str(e)[:200]}")


def num(x, nd=4):
    try:
        if x is None:
            return None
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else round(f, nd)
    except Exception:
        return None


def pct(a, b):
    a, b = num(a), num(b)
    return None if a is None or b in (None, 0) else round((a / b - 1) * 100, 2)


# ---------------------------------------------------------------- FRED (macro)
FRED = {"DGS10": "10y yield (%)", "BAMLH0A0HYM2": "HY OAS (%)", "DTWEXBGS": "Broad dollar index",
        "DCOILWTICO": "WTI oil ($/bbl)", "PCOPPUSDM": "Copper ($/t, monthly)", "T10Y2Y": "10y-2y spread (%)"}


def fetch_fred():
    out = {}
    for sid, label in FRED.items():
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna()
            last, prev5 = df.iloc[-1], df.iloc[-6] if len(df) > 6 else df.iloc[0]
            out[sid] = {"label": label, "value": num(last.value), "date": str(last.date),
                        "change_5obs": num(last.value - prev5.value), "source": f"FRED {sid}"}
        except Exception as e:
            err(f"fred {sid}", e)
            out[sid] = {"label": label, "value": None, "date": None, "change_5obs": None, "source": f"FRED {sid}"}
    return out


# ---------------------------------------------------------------- SEC EDGAR
_sec_last = [0.0]


def sec_get(url, as_json=True):
    wait = 0.15 - (time.time() - _sec_last[0])  # stay well under 10 req/s
    if wait > 0:
        time.sleep(wait)
    _sec_last[0] = time.time()
    r = requests.get(url, timeout=30, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
    r.raise_for_status()
    return r.json() if as_json else r.text


def cik_map():
    try:
        d = sec_get("https://www.sec.gov/files/company_tickers.json")
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in d.values()}
    except Exception as e:
        err("sec ticker map", e)
        return {}


WATCH_FORMS = {"8-K", "8-K/A", "4", "SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A", "SC 13G",
               "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A", "10-Q", "10-K", "6-K", "20-F", "DEF 14A",
               "DEFA14A", "PREC14A", "DFAN14A", "SC TO-T", "SC 14D9", "S-1", "424B4"}
ITEM_NAMES = {"1.01": "material agreement", "1.02": "agreement terminated", "2.01": "acquisition/disposal",
              "2.02": "results", "2.05": "restructuring costs", "2.06": "impairment",
              "3.01": "delisting notice", "4.01": "AUDITOR CHANGE", "4.02": "NON-RELIANCE (restatement)",
              "5.02": "exec/director change", "5.07": "shareholder vote", "7.01": "Reg FD", "8.01": "other events"}


def parse_form4(cik, acc, doc):
    """Return open-market buys (code P) and sells (code S) from one Form 4."""
    raw_doc = doc.split("/")[-1]  # primaryDocument is often the XSL-rendered path
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{raw_doc}"
    root = ET.fromstring(sec_get(url, as_json=False).encode())
    owner = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or ""
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    title = ""
    if rel is not None:
        title = rel.findtext("officerTitle") or ("Director" if rel.findtext("isDirector") in ("1", "true") else "")
    plan = root.findtext(".//aff10b5One") in ("1", "true")
    trades = []
    for t in root.findall(".//nonDerivativeTransaction"):
        code = t.findtext(".//transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        sh = num(t.findtext(".//transactionAmounts/transactionShares/value"))
        px = num(t.findtext(".//transactionAmounts/transactionPricePerShare/value"))
        trades.append({"code": code, "shares": sh, "price": px,
                       "value_usd": round(sh * px) if sh and px else None,
                       "date": t.findtext(".//transactionDate/value")})
    return {"owner": owner, "title": title, "10b5_1_plan": plan, "trades": trades, "url": url}


def fetch_sec(ticker, cik, lookback_days, want_facts):
    out = {"cik": cik, "filings": [], "insider_buys_usd": 0, "insider_sells_usd": 0,
           "insider_sells_ex_plan_usd": 0, "insider_detail": [], "flags": [], "fundamentals": None}
    if not cik:
        out["flags"].append("no CIK (non-SEC filer?)")
        return out
    try:
        sub = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        rec = sub["filings"]["recent"]
        cutoff = TODAY - dt.timedelta(days=lookback_days)
        n4 = 0
        for i, form in enumerate(rec["form"]):
            fdate = dt.date.fromisoformat(rec["filingDate"][i])
            if fdate < cutoff:
                break  # recent filings are newest first
            if form not in WATCH_FORMS:
                continue
            acc, doc = rec["accessionNumber"][i], rec["primaryDocument"][i]
            items = rec.get("items", [""] * len(rec["form"]))[i] or ""
            f = {"form": form, "date": rec["filingDate"][i],
                 "items": [f"{x} {ITEM_NAMES.get(x, '')}".strip() for x in items.split(",") if x],
                 "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"}
            out["filings"].append(f)
            if "4.01" in items or "4.02" in items:
                out["flags"].append(f"8-K {f['date']}: {'; '.join(f['items'])}")
            if "13D" in form:
                out["flags"].append(f"{form} filed {f['date']} (activist/5%+ holder)")
            if form in ("PREC14A", "DFAN14A"):
                out["flags"].append(f"{form} filed {f['date']} (possible proxy fight)")
            if form == "4" and n4 < 25:
                n4 += 1
                try:
                    p = parse_form4(cik, acc, doc)
                    for t in p["trades"]:
                        v = t["value_usd"] or 0
                        if t["code"] == "P":
                            out["insider_buys_usd"] += v
                        else:
                            out["insider_sells_usd"] += v
                            if not p["10b5_1_plan"]:
                                out["insider_sells_ex_plan_usd"] += v
                    if p["trades"]:
                        out["insider_detail"].append({k: p[k] for k in ("owner", "title", "10b5_1_plan", "trades", "url")})
                except Exception as e:
                    err(f"sec form4 {ticker} {acc}", e)
        if out["insider_buys_usd"] >= 100_000:
            out["flags"].append(f"insider open-market buying ${out['insider_buys_usd']:,} in {lookback_days}d")
    except Exception as e:
        err(f"sec submissions {ticker}", e)
    if want_facts:
        try:
            out["fundamentals"] = sec_fundamentals(cik)
        except Exception as e:
            err(f"sec companyfacts {ticker}", e)
    return out


FACT_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "op_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "sbc": ["ShareBasedCompensation"],
    "operating_income": ["OperatingIncomeLoss"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
}


def sec_fundamentals(cik):
    facts = sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")["facts"].get("us-gaap", {})
    res = {"source": f"SEC XBRL companyfacts CIK{cik}"}

    def series(tags, instant=False):
        for tag in tags:
            units = facts.get(tag, {}).get("units", {}).get("USD")
            if not units:
                continue
            pts = {}
            for u in units:
                fr = u.get("frame", "")
                if instant and re.fullmatch(r"CY\d{4}Q\dI", fr):
                    pts[fr[:-1]] = u["val"]
                elif not instant and re.fullmatch(r"CY\d{4}Q\d", fr):
                    pts[fr] = u["val"]
                elif not instant and re.fullmatch(r"CY\d{4}", fr):
                    pts[fr] = u["val"]
            if pts:
                return tag, pts
        return None, {}

    for key, tags in FACT_TAGS.items():
        inst = key in ("receivables", "inventory")
        tag, pts = series(tags, instant=inst)
        q = sorted(k for k in pts if "Q" in k)
        y = sorted(k for k in pts if "Q" not in k)
        entry = {"tag": tag}
        if q:
            last = q[-1]
            prior = f"CY{int(last[2:6]) - 1}{last[6:]}"
            entry.update(latest_q=last, latest_q_val=pts[last], yoy_pct=pct(pts[last], pts.get(prior)))
        if y:
            entry.update(latest_fy=y[-1], latest_fy_val=pts[y[-1]],
                         fy_growth_pct=pct(pts[y[-1]], pts[y[-2]]) if len(y) > 1 else None)
        res[key] = entry
    try:
        ocf, cap = res["op_cash_flow"].get("latest_fy_val"), res["capex"].get("latest_fy_val")
        ni = res["net_income"].get("latest_fy_val")
        fcf = ocf - cap if ocf is not None and cap is not None else None
        res["fcf_fy"] = fcf
        res["fcf_to_net_income"] = round(fcf / ni, 2) if fcf is not None and ni else None
        rq, aq = res["revenue"].get("yoy_pct"), res["receivables"].get("yoy_pct")
        res["receivables_outgrowing_sales"] = (aq is not None and rq is not None and aq > rq + 10)
    except Exception as e:
        err(f"fundamentals derive {cik}", e)
    return res


# ---------------------------------------------------------------- Yahoo (yfinance)
def yf_block(tk, hist):
    import yfinance as yf
    t = yf.Ticker(tk)
    row = {"ticker": tk}
    h = hist.get(tk)
    if h is not None and len(h) > 1:
        row.update(close=num(h.iloc[-1], 2), close_date=str(h.index[-1].date()),
                   day_change_pct=pct(h.iloc[-1], h.iloc[-2]),
                   ret_1m_pct=pct(h.iloc[-1], h.iloc[-22]) if len(h) > 22 else None,
                   ret_3m_pct=pct(h.iloc[-1], h.iloc[-64]) if len(h) > 64 else None,
                   ret_12m_pct=pct(h.iloc[-1], h.iloc[-253]) if len(h) > 253 else None)
    try:
        info = t.info or {}
        row.update(name=info.get("longName") or info.get("shortName"), sector=info.get("sector"),
                   industry=info.get("industry"), market_cap=num(info.get("marketCap"), 0),
                   fwd_pe=num(info.get("forwardPE"), 2), trailing_pe=num(info.get("trailingPE"), 2),
                   fwd_eps=num(info.get("forwardEps"), 3), free_cashflow=num(info.get("freeCashflow"), 0),
                   fcf_yield_pct=(round(info["freeCashflow"] / info["marketCap"] * 100, 2)
                                  if info.get("freeCashflow") and info.get("marketCap") else None),
                   gross_margin=num(info.get("grossMargins")), operating_margin=num(info.get("operatingMargins")),
                   revenue_growth=num(info.get("revenueGrowth")),
                   short_pct_float=num((info.get("shortPercentOfFloat") or 0) * 100, 2) if info.get("shortPercentOfFloat") else None,
                   short_ratio_days=num(info.get("shortRatio"), 2),
                   target_mean=num(info.get("targetMeanPrice"), 2), n_analysts=info.get("numberOfAnalystOpinions"))
    except Exception as e:
        err(f"yf info {tk}", e)
    try:  # estimate revisions: the core leading signal
        et = t.eps_trend
        if et is not None and len(et):
            rev = {}
            for per in ("0y", "+1y"):
                if per in et.index:
                    r = et.loc[per]
                    rev[per] = {"current": num(r.get("current")), "d30": num(r.get("30daysAgo")),
                                "d90": num(r.get("90daysAgo")),
                                "chg_30d_pct": pct(r.get("current"), r.get("30daysAgo")),
                                "chg_90d_pct": pct(r.get("current"), r.get("90daysAgo"))}
            row["eps_trend"] = rev
    except Exception as e:
        err(f"yf eps_trend {tk}", e)
    try:
        er = t.eps_revisions
        if er is not None and len(er) and "+1y" in er.index:
            r = er.loc["+1y"]
            row["eps_revisions_next_fy"] = {k: num(r.get(k), 0) for k in
                                            ("upLast7days", "upLast30days", "downLast7Days", "downLast30days") if k in r}
    except Exception as e:
        err(f"yf eps_revisions {tk}", e)
    try:  # last earnings: surprise and 3-day reaction
        ed = t.get_earnings_dates(limit=8)
        if ed is not None and len(ed):
            ed = ed.copy()
            ed.index = pd.to_datetime(ed.index).tz_localize(None)
            past = ed[ed.index.date <= TODAY].dropna(subset=["Reported EPS"]) if "Reported EPS" in ed else ed.iloc[0:0]
            future = ed[ed.index.date > TODAY]
            if len(future):
                row["next_earnings"] = str(future.index.min().date())
            if len(past):
                d0 = past.index.max()
                last = past.loc[[d0]].iloc[0]
                react = None
                if h is not None:
                    before = h[h.index.tz_localize(None) < d0.normalize()] if h.index.tz is not None else h[h.index < d0.normalize()]
                    after = h[h.index.tz_localize(None) >= d0.normalize()] if h.index.tz is not None else h[h.index >= d0.normalize()]
                    if len(before) and len(after) >= 4:
                        react = pct(after.iloc[3], before.iloc[-1])
                row["last_earnings"] = {"date": str(d0.date()), "eps_est": num(last.get("EPS Estimate")),
                                        "eps_actual": num(last.get("Reported EPS")),
                                        "surprise_pct": num(last.get("Surprise(%)"), 2),
                                        "reaction_3d_pct": react,
                                        "reaction_method": "close before report date -> close 3 sessions after"}
    except Exception as e:
        err(f"yf earnings {tk}", e)
    try:
        ud = t.upgrades_downgrades
        if ud is not None and len(ud):
            ud = ud.copy()
            ud.index = pd.to_datetime(ud.index).tz_localize(None)
            recent = ud[ud.index.date >= TODAY - dt.timedelta(days=10)].head(8)
            row["analyst_actions_10d"] = [{"date": str(i.date()), "firm": r.get("Firm"), "action": r.get("Action"),
                                           "from": r.get("FromGrade"), "to": r.get("ToGrade")} for i, r in recent.iterrows()]
    except Exception as e:
        err(f"yf upgrades {tk}", e)
    time.sleep(0.4)
    return row


def download_closes(tickers, period="13mo"):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        try:
            df = yf.download(chunk, period=period, interval="1d", auto_adjust=True, progress=False,
                             group_by="ticker", threads=True)
            for tk in chunk:
                try:
                    s = (df[tk]["Close"] if len(chunk) > 1 else df["Close"]).dropna()
                    if isinstance(s, pd.DataFrame):
                        s = s.iloc[:, 0]
                    if len(s):
                        out[tk] = s
                except Exception:
                    pass
        except Exception as e:
            err(f"yf download chunk {i}", e)
    return out


# ---------------------------------------------------------------- weekly universe scan
def universe_tickers():
    tks = set()
    for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                "https://en.wikipedia.org/wiki/Nasdaq-100"):
        try:
            html = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 stock-signal-fund"}).text
            for tb in pd.read_html(io.StringIO(html)):
                col = next((c for c in ("Symbol", "Ticker") if c in tb.columns), None)
                if col and len(tb) > 90:
                    tks |= {str(x).replace(".", "-").strip() for x in tb[col] if isinstance(x, str)}
                    break
        except Exception as e:
            err(f"universe {url}", e)
    return sorted(tks)


def weekly_universe(watch):
    import yfinance as yf
    tks = universe_tickers()
    closes = download_closes(tks)
    rows = []
    for tk, s in closes.items():
        if len(s) < 30:
            continue
        rows.append({"ticker": tk, "close": num(s.iloc[-1], 2),
                     "ret_1m_pct": pct(s.iloc[-1], s.iloc[-22]),
                     "ret_3m_pct": pct(s.iloc[-1], s.iloc[-64]) if len(s) > 64 else None,
                     "ret_12m_pct": pct(s.iloc[-1], s.iloc[-253]) if len(s) > 253 else None})
    df = pd.DataFrame(rows)
    movers = {}
    for w in ("ret_1m_pct", "ret_3m_pct", "ret_12m_pct"):
        d = df.dropna(subset=[w]).sort_values(w)
        movers[w] = {"top20": d.tail(20)[::-1].to_dict("records"), "bottom10": d.head(10).to_dict("records")}
    # revision velocity screen across the universe (outlier-engine input), time-boxed
    rev = []
    for tk in df.ticker.tolist():
        if time.time() - START > 40 * 60:
            ERRORS.append("universe revision screen stopped at 40-min budget (partial)")
            break
        try:
            et = yf.Ticker(tk).eps_trend
            if et is not None and "+1y" in et.index:
                r = et.loc["+1y"]
                c90 = pct(r.get("current"), r.get("90daysAgo"))
                if c90 is not None:
                    rev.append({"ticker": tk, "next_fy_eps_chg_90d_pct": c90,
                                "next_fy_eps_chg_30d_pct": pct(r.get("current"), r.get("30daysAgo"))})
        except Exception:
            pass
        time.sleep(0.25)
    rev.sort(key=lambda x: x["next_fy_eps_chg_90d_pct"], reverse=True)
    return {"as_of": str(TODAY), "universe_size": len(df), "movers": movers,
            "revision_leaders": rev[:40], "revision_laggards": rev[-20:][::-1],
            "revision_screen_coverage": len(rev),
            "notes": "Returns are price-only (auto-adjusted closes, Yahoo). Revisions = next-FY consensus EPS change (Yahoo eps_trend)."}


# ---------------------------------------------------------------- main
def main():
    os.makedirs(os.path.join(DATA, "daily"), exist_ok=True)
    cfg = json.load(open(os.path.join(ROOT, "pipeline", "tickers.json")))
    watch = cfg["watchlist"] + [t for t in cfg.get("bench", []) if t not in cfg["watchlist"]]
    weekly = MODE == "weekly"
    closes = download_closes(watch)
    ciks = cik_map()
    rows = []
    for tk in watch:
        row = {}
        try:
            row = yf_block(tk, closes)
        except Exception as e:
            err(f"yf {tk}", e)
            row = {"ticker": tk}
        row["sec"] = fetch_sec(tk, ciks.get(tk.upper()), 35 if weekly else 10, want_facts=weekly)
        rows.append(row)
    out = {"mode": MODE, "as_of": str(TODAY), "fetched_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "macro": fetch_fred(), "tickers": rows}
    if weekly:
        try:
            uni = weekly_universe(watch)
            json.dump(uni, open(os.path.join(DATA, "universe.json"), "w"), indent=1, default=str)
        except Exception as e:
            err("weekly universe", e)
        # keep the weekly fundamentals so daily runs can carry them
        json.dump(out, open(os.path.join(DATA, "weekly.json"), "w"), indent=1, default=str)
    json.dump(out, open(os.path.join(DATA, "latest.json"), "w"), indent=1, default=str)
    json.dump(out, open(os.path.join(DATA, "daily", f"{TODAY}-{MODE}.json"), "w"), default=str)
    ok_px = sum(1 for r in rows if r.get("close") is not None)
    status = {"mode": MODE, "as_of": str(TODAY), "fetched_at": out["fetched_at"],
              "tickers": len(rows), "tickers_with_price": ok_px,
              "ok": ok_px >= max(1, int(0.7 * len(rows))) and not any(e.startswith("sec ticker map") for e in ERRORS),
              "runtime_sec": int(time.time() - START), "errors": ERRORS[:200]}
    json.dump(status, open(os.path.join(DATA, "status.json"), "w"), indent=1)
    # prune daily history older than 120 days
    for f in os.listdir(os.path.join(DATA, "daily")):
        try:
            if (TODAY - dt.date.fromisoformat(f[:10])).days > 120:
                os.remove(os.path.join(DATA, "daily", f))
        except Exception:
            pass
    print(json.dumps({k: status[k] for k in ("ok", "tickers_with_price", "runtime_sec")}), f"errors={len(ERRORS)}")


if __name__ == "__main__":
    main()
