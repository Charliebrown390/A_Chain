"""Market data for the A_Chain universe, from free Yahoo Finance sources.

Modes
  full     prices (2y daily), technicals, realised vol, beta, fundamentals, analyst views, news
  options  30-day at-the-money implied vol snapshot (run during US trading hours), appended to history
  news     latest headlines only (cheap; run every few hours)

Outputs
  site/data/market.json        one summary record per company
  site/data/px/<id>.json       daily closes and volumes for the price chart
  site/data/iv.json            latest IV30 and IV history per company
  site/data/news.json          latest headlines per company
  data/history/iv.csv          append-only IV30 log (builds IV percentile over time)

Usage: python scripts/market_data.py --mode full|options|news
"""
import argparse
import csv
import json
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site/data"
HIST = ROOT / "data/history"
BENCH = {"sox": "^SOX", "spx": "^GSPC"}
NOW = datetime.now(timezone.utc)


def universe():
    u = yaml.safe_load((ROOT / "data/universe.yaml").read_text())
    return [c for c in u["companies"] if c.get("yf")]


def r(x, n=4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, n)


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))


def load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


# ---------------------------------------------------------------- prices
def download_prices(symbols):
    df = yf.download(symbols, period="2y", interval="1d", auto_adjust=True, group_by="ticker",
                     threads=True, progress=False)
    out = {}
    for s in symbols:
        try:
            sub = df[s][["Close", "Volume"]].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(sub):
            out[s] = sub
    return out


def realised_vol(close, n):
    lr = np.log(close).diff().dropna()
    return float(lr.tail(n).std() * math.sqrt(252)) if len(lr) >= n else None


def tech(sub, bench):
    c, v = sub["Close"], sub["Volume"]
    last = float(c.iloc[-1])
    lr = np.log(c).diff()
    hv20_series = lr.rolling(20).std() * math.sqrt(252)
    y1 = c.tail(252)

    def ret(n):
        return (last / float(c.iloc[-1 - n]) - 1) * 100 if len(c) > n else None

    ytd_base = c[c.index < pd.Timestamp(date(c.index[-1].year, 1, 1), tz=c.index.tz)]
    sma50 = float(c.tail(50).mean()) if len(c) >= 50 else None
    sma200 = float(c.tail(200).mean()) if len(c) >= 200 else None
    trend = "Mixed"
    if sma50 and sma200:
        trend = "Uptrend" if last > sma50 > sma200 else "Downtrend" if last < sma50 < sma200 else "Mixed"
    # RSI(14), Wilder
    d = c.diff().dropna()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = up.ewm(alpha=1 / 14, adjust=False).mean() / dn.ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float(100 - 100 / (1 + rs.iloc[-1])) if len(d) > 20 else None
    hv20 = realised_vol(c, 20)
    h1 = hv20_series.tail(252).dropna()
    hv_pct = float((h1 < h1.iloc[-1]).mean() * 100) if len(h1) > 60 else None
    dd = float((y1 / y1.cummax() - 1).min() * 100)
    vol_ratio = float(v.iloc[-1] / v.tail(21).head(20).mean()) if len(v) > 21 and v.tail(21).head(20).mean() > 0 else None
    # beta vs benchmarks over 1y on common dates (dates only, to align Asia and US calendars)
    betas = {}
    for k, bs in bench.items():
        a = pd.Series(lr.values, index=lr.index.date)
        b = np.log(bs["Close"]).diff()
        b = pd.Series(b.values, index=b.index.date)
        j = pd.concat([a, b], axis=1, join="inner").dropna().tail(252)
        if len(j) > 60 and j.iloc[:, 1].var() > 0:
            betas[k] = float(np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / j.iloc[:, 1].var())
    chg1d = ret(1)
    z1d = None
    if chg1d is not None and hv20:
        z1d = (math.log(1 + chg1d / 100)) / (hv20 / math.sqrt(252))
    return {
        "last": r(last), "date": str(c.index[-1].date()), "chg1d": r(chg1d, 2), "z1d": r(z1d, 2),
        "r1m": r(ret(21), 2), "r3m": r(ret(63), 2), "r1y": r(ret(252), 2),
        "ytd": r((last / float(ytd_base.iloc[-1]) - 1) * 100, 2) if len(ytd_base) else None,
        "hi52": r(y1.max()), "lo52": r(y1.min()), "from_hi": r((last / y1.max() - 1) * 100, 2),
        "sma50": r(sma50), "sma200": r(sma200), "trend": trend, "rsi": r(rsi, 1),
        "hv20": r(hv20), "hv60": r(realised_vol(c, 60)), "hv252": r(realised_vol(c, 252)),
        "hv20_pct1y": r(hv_pct, 0), "maxdd1y": r(dd, 1), "vol_ratio": r(vol_ratio, 2),
        "beta_sox": r(betas.get("sox"), 2), "beta_spx": r(betas.get("spx"), 2),
    }


# ---------------------------------------------------------------- fundamentals & analysts
_fx = {}


def fx_per_usd(ccy):
    """Units of ccy per 1 USD (USD -> 1). Yahoo 'KRW=X' quotes KRW per USD."""
    if not ccy or ccy == "USD":
        return 1.0
    if ccy not in _fx:
        try:
            _fx[ccy] = float(yf.Ticker(f"{ccy}=X").history(period="5d")["Close"].dropna().iloc[-1])
        except Exception:
            _fx[ccy] = None
    return _fx[ccy]


def usd_bn(x, ccy):
    f = fx_per_usd(ccy)
    return r(x / f / 1e9, 2) if (x is not None and f) else None


def fundamentals(tk):
    i = tk.info or {}
    pc, fc = i.get("currency"), i.get("financialCurrency") or i.get("currency")
    f = {
        "name": i.get("shortName"), "ccy": pc, "fin_ccy": fc,
        "mcap_usd_bn": usd_bn(i.get("marketCap"), pc),
        "rev_usd_bn": usd_bn(i.get("totalRevenue"), fc),
        "rev_g": r((i.get("revenueGrowth") or 0) * 100, 1) if i.get("revenueGrowth") is not None else None,
        "gm": r((i.get("grossMargins") or 0) * 100, 1) if i.get("grossMargins") is not None else None,
        "opm": r((i.get("operatingMargins") or 0) * 100, 1) if i.get("operatingMargins") is not None else None,
        "fcf_usd_bn": usd_bn(i.get("freeCashflow"), fc),
        "fwd_pe": r(i.get("forwardPE"), 1), "ttm_pe": r(i.get("trailingPE"), 1),
        "ev_rev": r(i.get("enterpriseToRevenue"), 2),
        "tgt_lo": r(i.get("targetLowPrice")), "tgt_mean": r(i.get("targetMeanPrice")), "tgt_hi": r(i.get("targetHighPrice")),
        "n_analysts": i.get("numberOfAnalystOpinions"), "rec_key": i.get("recommendationKey"), "rec_mean": r(i.get("recommendationMean"), 2),
    }
    # Yahoo sometimes returns nonsense P/E for loss-makers, or a forward P/E with no forward EPS behind it
    if i.get("forwardEps") in (None, 0):
        f["fwd_pe"] = None
    # Yahoo sometimes returns nonsense P/E for loss-makers; drop negatives
    for k in ("fwd_pe", "ttm_pe"):
        if f[k] is not None and (f[k] <= 0 or f[k] > 2000):
            f[k] = None
    last = i.get("currentPrice") or i.get("regularMarketPrice")
    f["tgt_suspect"] = bool(last and f["tgt_lo"] and f["tgt_hi"] and (f["tgt_lo"] > last * 1.3 or f["tgt_hi"] < last * 0.7))
    try:
        rec = tk.recommendations
        row = rec[rec["period"] == "0m"].iloc[0]
        f["ratings"] = {k: int(row[k]) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    except Exception:
        f["ratings"] = None
    try:
        ud = tk.upgrades_downgrades.reset_index()
        ud = ud[ud["GradeDate"] >= pd.Timestamp(NOW.date() - timedelta(days=150))].head(10)
        f["actions"] = [{
            "date": str(x["GradeDate"])[:10], "firm": x["Firm"], "action": x["Action"],
            "from": x.get("FromGrade") or "", "to": x.get("ToGrade") or "",
            "pt": r(x.get("currentPriceTarget"), 2) or None, "pt_prior": r(x.get("priorPriceTarget"), 2) or None,
        } for _, x in ud.iterrows()]
    except Exception:
        f["actions"] = []
    try:
        inc = tk.income_stmt
        rev = inc.loc["Total Revenue"].dropna().sort_index()
        f["rev_hist"] = [{"fy": str(k.date())[:7], "usd_bn": usd_bn(float(v), fc)} for k, v in rev.items()][-5:]
    except Exception:
        f["rev_hist"] = []
    return f


# ---------------------------------------------------------------- implied vol
def bs_price(s, k, t, rate, vol, call):
    if vol <= 0 or t <= 0:
        return max(0.0, (s - k) if call else (k - s))
    d1 = (math.log(s / k) + (rate + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if call:
        return s * N(d1) - k * math.exp(-rate * t) * N(d2)
    return k * math.exp(-rate * t) * N(-d2) - s * N(-d1)


def implied_vol(price, s, k, t, rate, call):
    lo, hi = 0.01, 5.0
    if price <= max(0.0, (s - k) if call else (k - s)) + 1e-6:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(s, k, t, rate, mid, call) > price:
            hi = mid
        else:
            lo = mid
    v = (lo + hi) / 2
    return v if 0.03 < v < 4.5 else None


def atm_iv(tk, expiry, spot, rate):
    ch = tk.option_chain(expiry)
    t = (datetime.strptime(expiry, "%Y-%m-%d").date() - NOW.date()).days / 365
    if t <= 0:
        return None, t
    ivs = []
    for df, call in ((ch.calls, True), (ch.puts, False)):
        df = df[(df["bid"] > 0) & (df["ask"] > 0)]
        if df.empty:
            continue
        row = df.iloc[(df["strike"] - spot).abs().argsort()[:1]].iloc[0]
        if abs(row["strike"] / spot - 1) > 0.1:
            continue
        mid = (row["bid"] + row["ask"]) / 2
        if (row["ask"] - row["bid"]) / mid > 0.5:   # too wide to trust
            continue
        v = implied_vol(mid, spot, row["strike"], t, rate, call)
        if v:
            ivs.append(v)
    return (sum(ivs) / len(ivs) if ivs else None), t


def iv30(tk, spot, rate):
    exps = [e for e in tk.options if (datetime.strptime(e, "%Y-%m-%d").date() - NOW.date()).days >= 7]
    if not exps:
        return None
    days = [(datetime.strptime(e, "%Y-%m-%d").date() - NOW.date()).days for e in exps]
    below = [e for e, d in zip(exps, days) if d <= 30]
    above = [e for e, d in zip(exps, days) if d > 30]
    picks = ([below[-1]] if below else []) + ([above[0]] if above else [])
    pts = []
    for e in picks:
        v, t = atm_iv(tk, e, spot, rate)
        if v:
            pts.append((t, v * v * t))
    if not pts:
        return None
    t30 = 30 / 365
    if len(pts) == 2 and pts[0][0] != pts[1][0]:
        (t1, w1), (t2, w2) = pts
        w = w1 + (w2 - w1) * (t30 - t1) / (t2 - t1)
        return math.sqrt(max(w, 1e-8) / t30)
    t, w = pts[0]
    return math.sqrt(w / t)


def run_options(cos):
    try:
        rate = float(yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]) / 100
    except Exception:
        rate = 0.04
    HIST.mkdir(parents=True, exist_ok=True)
    path = HIST / "iv.csv"
    new = not path.exists()
    today = NOW.date().isoformat()
    rows = []
    for co in cos:
        try:
            tk = yf.Ticker(co["yf"])
            if not tk.options:
                continue
            spot = float(tk.fast_info["last_price"])
            v = iv30(tk, spot, rate)
            if v:
                rows.append([today, co["id"], round(v, 4), round(spot, 4)])
                print(f"{co['id']:>10} iv30={v:.3f}")
        except Exception as e:
            print(f"{co['id']:>10} options error: {e}")
        time.sleep(0.3)
    # keep one row per (date, id): drop any earlier rows for today before appending
    old = []
    if not new:
        with path.open() as fh:
            old = [x for x in csv.reader(fh)][1:]
        old = [x for x in old if x[0] != today]
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "id", "iv30", "spot"])
        w.writerows(old + rows)
    build_iv_json()


def build_iv_json():
    path = HIST / "iv.csv"
    if not path.exists():
        return
    df = pd.read_csv(path)
    out = {}
    for cid, g in df.sort_values("date").groupby("id"):
        out[cid] = {"date": g["date"].iloc[-1], "iv30": float(g["iv30"].iloc[-1]),
                    "hist": [[d, float(v)] for d, v in zip(g["date"], g["iv30"])][-260:]}
    write(OUT / "iv.json", {"built": NOW.isoformat(timespec="seconds"), "companies": out})


# ---------------------------------------------------------------- news
SUFFIX = re.compile(r"\b(Holding|Holdings|Technology|Technologies|Corp|Corporation|Inc|Group|Platforms|Precision|Systems|Design|Electronics|Devices|Micro)\b.*$", re.I)
EXTRA_KW = {"GOOGL": ["Google"], "AMZN": ["AWS"], "MSFT": ["Azure"], "2317.TW": ["Foxconn"], "TSM": ["TSMC", "Taiwan Semiconductor"],
            "000660.KS": ["Hynix"], "005930.KS": ["Samsung"], "AMAT": ["Applied Materials"], "LRCX": ["Lam Research"], "KLAC": ["KLA"],
            "AMD": ["AMD"], "INTC": ["Intel"], "CEG": ["Constellation"], "GEV": ["GE Vernova", "Vernova"], "NBIS": ["Nebius"]}


def keywords(co):
    kws = {co["label"], co["yf"].split(".")[0]}
    base = SUFFIX.sub("", co["name"]).strip(" ,()")
    if len(base) > 3:
        kws.add(base)
    kws.update(EXTRA_KW.get(co["id"], []))
    kws = {k for k in kws if k and not k.isdigit() and len(k) >= 2}
    return re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(kws, key=len, reverse=True)) + r")\b", re.I)


def run_news(cos):
    out = {}
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (A_Chain research)"
    cutoff = NOW - timedelta(days=4)
    for co in cos:
        items = []
        kw = keywords(co)
        try:
            x = s.get("https://feeds.finance.yahoo.com/rss/2.0/headline",
                      params={"s": co["yf"], "region": "US", "lang": "en-US"}, timeout=20)
            root = ET.fromstring(x.content)
            seen = set()
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                try:
                    when = parsedate_to_datetime(it.findtext("pubDate"))
                except Exception:
                    continue
                if not title or title.lower() in seen or when < cutoff:
                    continue
                seen.add(title.lower())
                host = re.sub(r"^www\.", "", link.split("/")[2]) if link.count("/") >= 2 else ""
                items.append({"t": title, "u": link, "src": host, "at": when.astimezone(timezone.utc).isoformat(timespec="minutes"),
                              "r": 1 if kw.search(title) else 0})
        except Exception as e:
            print(f"{co['id']:>10} news error: {e}")
        items.sort(key=lambda i: i["at"], reverse=True)
        out[co["id"]] = ([i for i in items if i["r"]] + [i for i in items if not i["r"]])[:10]
        time.sleep(0.2)
    write(OUT / "news.json", {"built": NOW.isoformat(timespec="seconds"), "companies": out})
    print("news:", sum(len(v) for v in out.values()), "headlines")


# ---------------------------------------------------------------- full
def run_full(cos):
    syms = [c["yf"] for c in cos] + list(BENCH.values())
    px = download_prices(syms)
    bench = {k: px[v] for k, v in BENCH.items() if v in px}
    summary = {}
    for co in cos:
        sub = px.get(co["yf"])
        rec = {"yf": co["yf"]}
        if sub is not None and len(sub) > 30:
            rec.update(tech(sub, bench))
            write(OUT / "px" / f"{co['id']}.json", {
                "id": co["id"], "d": [str(d.date()) for d in sub.index],
                "c": [r(x, 4) for x in sub["Close"]], "v": [int(x) if not math.isnan(x) else 0 for x in sub["Volume"]],
            })
        try:
            rec.update(fundamentals(yf.Ticker(co["yf"])))
        except Exception as e:
            rec["error"] = f"fundamentals: {e}"
        summary[co["id"]] = rec
        print(f"{co['id']:>10} last={rec.get('last')} {rec.get('ccy')} mcap=${rec.get('mcap_usd_bn')}B pe={rec.get('fwd_pe')}")
        time.sleep(0.4)
    bm = {k: tech(v, {}) for k, v in bench.items()}
    write(OUT / "market.json", {"built": NOW.isoformat(timespec="seconds"), "benchmarks": bm, "companies": summary})
    run_news(cos)
    build_iv_json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "options", "news"], default="full")
    ap.add_argument("--only", help="comma-separated ids, for testing")
    a = ap.parse_args()
    cos = universe()
    if a.only:
        keep = set(a.only.split(","))
        cos = [c for c in cos if c["id"] in keep]
    {"full": run_full, "options": run_options, "news": run_news}[a.mode](cos)


if __name__ == "__main__":
    sys.exit(main())
