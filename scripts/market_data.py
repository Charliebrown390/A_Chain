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
from zoneinfo import ZoneInfo
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
NY = ZoneInfo("America/New_York")


def us_market_open(now=None):
    """True during regular US trading hours (Mon-Fri 09:30-16:00 New York; exchange holidays not checked)."""
    t = (now or NOW).astimezone(NY)
    return t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) < (16, 0)


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


# ---------------------------------------------------------------- credit
def _row(df, *names):
    for n in names:
        if df is not None and n in df.index:
            return df.loc[n]
    return None


def _ttm(df, *names):
    """Sum of the latest four quarters; None if fewer than four are reported."""
    row = _row(df, *names)
    if row is None:
        return None
    vals = [float(v) for v in row.values[:4] if v == v]
    return sum(vals) if len(vals) == 4 else None


def _last(df, *names):
    row = _row(df, *names)
    if row is None:
        return None
    for v in row.values:
        if v == v:
            return float(v)
    return None


def _N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def merton(E, D, sigma_e, rate, T=1.0):
    """Merton distance-to-default. E, D in the same units; returns (DD, risk-neutral 1y PD, asset vol)."""
    if not (E and D and sigma_e) or E <= 0 or D <= 0 or sigma_e <= 0:
        return None, None, None
    V, sv = E + D, sigma_e * E / (E + D)
    for _ in range(200):
        d1 = (math.log(V / D) + (rate + sv * sv / 2) * T) / (sv * math.sqrt(T))
        d2 = d1 - sv * math.sqrt(T)
        Vn = (E + D * math.exp(-rate * T) * _N(d2)) / max(_N(d1), 1e-9)
        svn = sigma_e * E / (Vn * max(_N(d1), 1e-9))
        if abs(Vn - V) < 1e-7 * V and abs(svn - sv) < 1e-7:
            V, sv = Vn, svn
            break
        V, sv = Vn, max(svn, 1e-4)
    dd = (math.log(V / D) + (rate - sv * sv / 2) * T) / (sv * math.sqrt(T))
    return dd, _N(-dd), sv


def credit(tk, fin_ccy, mcap_usd, sigma_e, rate):
    bs, inc, cf = tk.quarterly_balance_sheet, tk.quarterly_income_stmt, tk.quarterly_cashflow
    f = fx_per_usd(fin_ccy)
    if not f or bs is None or bs.empty:
        return {}
    u = lambda x: None if x is None else x / f / 1e9
    cur_debt = _last(bs, "Current Debt")
    lt_debt = _last(bs, "Long Term Debt")
    if cur_debt is None and lt_debt is None:
        tot, lease = _last(bs, "Total Debt"), _last(bs, "Capital Lease Obligations") or 0
        debt = None if tot is None else tot - lease
    else:
        debt = (cur_debt or 0) + (lt_debt or 0)
    cash = _last(bs, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
    leases = _last(bs, "Capital Lease Obligations")
    ebitda = _ttm(inc, "EBITDA", "Normalized EBITDA")
    ebit = _ttm(inc, "EBIT", "Operating Income")
    interest = _ttm(inc, "Interest Expense", "Interest Expense Non Operating")
    ocf = _ttm(cf, "Operating Cash Flow")
    capex = _ttm(cf, "Capital Expenditure")
    fcf = _ttm(cf, "Free Cash Flow")
    rev_q, ar_q = _row(inc, "Total Revenue"), _row(bs, "Accounts Receivable", "Receivables")
    dso = []
    if rev_q is not None and ar_q is not None:
        for d in list(ar_q.index)[:6]:
            if d in rev_q.index and rev_q[d] == rev_q[d] and ar_q[d] == ar_q[d] and rev_q[d] > 0:
                dso.append([str(d.date()), round(float(ar_q[d]) / float(rev_q[d]) * 91.25, 1)])
    dso.reverse()
    out = {
        "as_of": str(bs.columns[0].date()),
        "debt_usd_bn": r(u(debt), 2), "cash_usd_bn": r(u(cash), 2),
        "net_debt_usd_bn": r(u(debt - cash), 2) if debt is not None and cash is not None else None,
        "lease_liab_usd_bn": r(u(leases), 2),
        "ebitda_ttm_usd_bn": r(u(ebitda), 2), "interest_ttm_usd_bn": r(u(interest), 3),
        "ocf_ttm_usd_bn": r(u(ocf), 2), "capex_ttm_usd_bn": r(u(abs(capex)) if capex is not None else None, 2),
        "fcf_ttm_usd_bn": r(u(fcf), 2), "ar_usd_bn": r(u(_last(bs, "Accounts Receivable", "Receivables")), 2),
        "dso": dso,
    }
    if debt is not None and cash is not None and ebitda and ebitda > 0:
        out["nd_ebitda"] = r((debt - cash) / ebitda, 2)
    if interest and interest > 0:
        if ebitda is not None and ebitda > 0:
            out["ebitda_cover"] = r(ebitda / interest, 1)
        if ebit is not None and ebit > 0:
            out["ebit_cover"] = r(ebit / interest, 1)
    if capex and ocf:
        out["capex_ocf"] = r(abs(capex) / ocf, 2) if ocf > 0 else None
    # Merton distance to default: default point = short-term debt + half of long-term debt (KMV convention)
    dp = None
    if cur_debt is not None or lt_debt is not None:
        dp = (cur_debt or 0) + 0.5 * (lt_debt or 0)
    elif debt:
        dp = 0.75 * debt
    if dp is not None and mcap_usd and sigma_e:
        dpu = u(dp)
        if dpu < 0.005 * mcap_usd:
            out["dd_note"] = "negligible debt"
        else:
            dd, pd_, sv = merton(mcap_usd, dpu, sigma_e, rate)
            if dd is not None:
                out.update({"dd": r(min(dd, 25), 2), "pd_1y": r(pd_, 6), "asset_vol": r(sv, 3), "dd_sigma": r(sigma_e, 3),
                            "dd_default_point_usd_bn": r(dpu, 2)})
    return out


# ---------------------------------------------------------------- returns (ROIC)
ERP = 0.05   # equity risk premium used in the cost of equity


def _col(df, col, *names):
    row = _row(df, *names)
    if row is None or col not in row.index:
        return None
    v = row[col]
    return float(v) if v == v else None


def returns(tk, fin_ccy, mcap_usd, beta, rf, credit_rec):
    """ROIC history (annual), TTM ROIC, incremental ROIC, depreciation stress, WACC and inventory days.
    Invested capital = total debt (Yahoo includes on-balance-sheet leases) + equity - cash, averaged over the year."""
    f = fx_per_usd(fin_ccy)
    if not f:
        return {}
    u = lambda x: None if x is None else x / f / 1e9
    inc, bs, cf = tk.income_stmt, tk.balance_sheet, tk.cashflow
    if inc is None or inc.empty or bs is None or bs.empty:
        return {}
    cols = sorted([c for c in inc.columns if c in bs.columns])
    def tax(c):
        t = _col(inc, c, "Tax Rate For Calcs")
        return min(max(t, 0.0), 0.35) if t is not None else 0.21
    def ic_of(debt, eq, cash):
        """Net-of-cash invested capital; for cash-rich firms (cash > 70% of debt+equity) use debt+equity instead."""
        if eq is None:
            return None, None
        gross, net = debt + eq, debt + eq - cash
        if gross > 0 and net < 0.3 * gross:
            return gross, "incl. cash"
        return net, "net of cash"
    def ic(c):
        debt = _col(bs, c, "Total Debt") or 0
        eq = _col(bs, c, "Stockholders Equity", "Common Stock Equity")
        cash = _col(bs, c, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents") or 0
        return ic_of(debt, eq, cash)[0]
    hist, prev_ic = [], None
    for c in cols:
        ebit = _col(inc, c, "Operating Income", "EBIT")   # operating, so one-off non-operating items do not swamp it
        rev = _col(inc, c, "Total Revenue")
        cap = ic(c)
        if ebit is None or not rev or cap is None:
            prev_ic = cap
            continue
        avg = (cap + prev_ic) / 2 if prev_ic is not None and prev_ic > 0 else cap
        nopat = ebit * (1 - tax(c))
        capex = _col(cf, c, "Capital Expenditure") if cf is not None else None
        ppe = _col(bs, c, "Net PPE")
        da = _col(inc, c, "Reconciled Depreciation") or (_col(cf, c, "Depreciation And Amortization", "Depreciation Amortization Depletion") if cf is not None else None)
        hist.append({"fy": str(c.date())[:7], "roic": r(nopat / avg * 100, 1) if avg and avg > 0 else None,
                     "nopat_usd_bn": r(u(nopat), 2), "ic_usd_bn": r(u(cap), 2),
                     "capex_rev": r(abs(capex) / rev * 100, 1) if capex is not None else None,
                     "ppe_rev": r(ppe / rev, 2) if ppe is not None else None,
                     "da_rev": r(da / rev * 100, 1) if da is not None else None})
        prev_ic = cap
    out = {"hist": hist[-5:]}
    # TTM from quarterly statements
    q_inc, q_bs = tk.quarterly_income_stmt, tk.quarterly_balance_sheet
    ebit_q = _ttm(q_inc, "Operating Income", "EBIT")
    da_q = _ttm(q_inc, "Reconciled Depreciation")
    if ebit_q is not None and q_bs is not None and not q_bs.empty:
        c0 = q_bs.columns[0]
        debt = _col(q_bs, c0, "Total Debt") or 0
        eq = _col(q_bs, c0, "Stockholders Equity", "Common Stock Equity")
        cash = _col(q_bs, c0, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents") or 0
        t = tax(cols[-1]) if cols else 0.21
        icq, basis = ic_of(debt, eq, cash)
        if icq is not None and icq > 0:
            out["ic_basis"] = basis
            out.update({"roic_ttm": r(ebit_q * (1 - t) / icq * 100, 1), "nopat_ttm_usd_bn": r(u(ebit_q * (1 - t)), 2),
                        "ic_usd_bn": r(u(icq), 2), "tax": r(t, 3), "as_of": str(c0.date())})
            gw = _col(q_bs, c0, "Goodwill And Other Intangible Assets", "Goodwill")
            if gw and icq - gw > 0.1 * icq:
                out["roic_ex_gw"] = r(ebit_q * (1 - t) / (icq - gw) * 100, 1)
                out["goodwill_usd_bn"] = r(u(gw), 2)
            if da_q:
                out["roic_dep_stress"] = r((ebit_q - 0.3 * da_q) * (1 - t) / icq * 100, 1)
                out["da_ttm_usd_bn"] = r(u(da_q), 2)
    # incremental ROIC over two fiscal years
    h = [x for x in hist if x["nopat_usd_bn"] is not None and x["ic_usd_bn"] is not None]
    if len(h) >= 3:
        a, b = h[-3], h[-1]
        dic = b["ic_usd_bn"] - a["ic_usd_bn"]
        if dic > 0.05 * max(abs(a["ic_usd_bn"]), 1):
            out["incr_roic"] = r((b["nopat_usd_bn"] - a["nopat_usd_bn"]) / dic * 100, 1)
            out["incr_span"] = f"{a['fy']} to {b['fy']}"
    # WACC: CAPM cost of equity on the US 10-year; cost of debt from interest paid on debt
    debt_usd = (credit_rec or {}).get("debt_usd_bn")
    int_usd = (credit_rec or {}).get("interest_ttm_usd_bn")
    if mcap_usd:
        b = min(max(0.67 * (beta if beta is not None else 1.0) + 0.33, 0.5), 2.5)   # Blume-adjusted beta
        ke = rf + b * ERP
        kd = min(max(int_usd / debt_usd, 0.03), 0.18) if debt_usd and int_usd and debt_usd > 0.5 else rf + 0.015
        t = out.get("tax", 0.21)
        D = debt_usd or 0
        wacc = (mcap_usd * ke + D * kd * (1 - t)) / (mcap_usd + D)
        out.update({"wacc": r(wacc * 100, 1), "ke": r(ke * 100, 1), "kd": r(kd * 100, 1), "beta_used": r(b, 2)})
        if out.get("roic_ttm") is not None:
            out["spread"] = r(out["roic_ttm"] - out["wacc"], 1)
    # inventory days (quarterly)
    cogs = _row(q_inc, "Cost Of Revenue", "Reconciled Cost Of Revenue")
    inv = _row(q_bs, "Inventory")
    days = []
    if cogs is not None and inv is not None:
        for d in list(inv.index)[:6]:
            if d in cogs.index and cogs[d] == cogs[d] and inv[d] == inv[d] and cogs[d] > 0:
                days.append([str(d.date()), round(float(inv[d]) / float(cogs[d]) * 91.25, 1)])
    days.reverse()
    out["inv_days"] = days
    return out


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


def run_options(cos, force=False):
    if not force and not us_market_open():
        print("US market is closed: option quotes are stale, so the implied-vol snapshot is skipped.")
        build_iv_json()
        return
    try:
        rate = float(yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]) / 100
    except Exception:
        rate = 0.04
    HIST.mkdir(parents=True, exist_ok=True)
    path = HIST / "iv.csv"
    new = not path.exists()
    today = NOW.astimezone(NY).date().isoformat()
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
    # one row per (date, id): a new value replaces today's earlier one; earlier values are kept otherwise
    old = []
    if not new:
        with path.open() as fh:
            old = [x for x in csv.reader(fh)][1:]
        fresh = {x[1] for x in rows}
        old = [x for x in old if not (x[0] == today and x[1] in fresh)]
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
        if not items:
            items = load(OUT / "news.json", {}).get("companies", {}).get(co["id"], [])
        items.sort(key=lambda i: i["at"], reverse=True)
        out[co["id"]] = ([i for i in items if i["r"]] + [i for i in items if not i["r"]])[:10]
        time.sleep(0.2)
    write(OUT / "news.json", {"built": NOW.isoformat(timespec="seconds"), "companies": out})
    print("news:", sum(len(v) for v in out.values()), "headlines")


# ---------------------------------------------------------------- full
def run_full(cos):
    try:
        rate = float(yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]) / 100
    except Exception:
        rate = 0.04
    ivs = load(OUT / "iv.json", {}).get("companies", {})
    prev = load(OUT / "market.json", {}).get("companies", {})
    try:
        rf10 = float(yf.Ticker("^TNX").history(period="5d")["Close"].dropna().iloc[-1]) / 100
    except Exception:
        rf10 = 0.045
    syms = [c["yf"] for c in cos] + list(BENCH.values())
    px = download_prices(syms)
    got = sum(1 for c in cos if c["yf"] in px and len(px[c["yf"]]) > 30)
    if got < 0.5 * len(cos):
        print(f"Only {got} of {len(cos)} price series came back; keeping the previous data unchanged.")
        return
    bench = {k: px[v] for k, v in BENCH.items() if v in px}
    summary, failures = {}, []
    for co in cos:
        sub = px.get(co["yf"])
        rec = {"yf": co["yf"]}
        old = prev.get(co["id"], {})
        if (sub is None or len(sub) <= 30) and old:
            rec.update({k: v for k, v in old.items() if k not in ("credit",)})   # keep last good prices
            rec["stale"] = True
            failures.append({"id": co["id"], "yf": co["yf"], "what": "prices"})
        if sub is not None and len(sub) > 30:
            rec.update(tech(sub, bench))
            write(OUT / "px" / f"{co['id']}.json", {
                "id": co["id"], "d": [str(d.date()) for d in sub.index],
                "c": [r(x, 4) for x in sub["Close"]], "v": [int(x) if not math.isnan(x) else 0 for x in sub["Volume"]],
            })
        tk = yf.Ticker(co["yf"])
        try:
            rec.update(fundamentals(tk))
        except Exception as e:
            rec["error"] = f"fundamentals: {e}"
            failures.append({"id": co["id"], "yf": co["yf"], "what": "fundamentals"})
        for k in ("mcap_usd_bn", "rev_usd_bn", "ratings", "tgt_mean", "fwd_pe", "rev_hist", "actions"):
            if rec.get(k) in (None, [], {}) and old.get(k) not in (None, [], {}):
                rec[k] = old[k]            # Yahoo sometimes returns blanks; fall back to the last good value
        try:
            iv = ivs.get(co["id"], {}).get("iv30")
            sig = iv or rec.get("hv252") or rec.get("hv60")
            rec["credit"] = credit(tk, rec.get("fin_ccy") or rec.get("ccy"), rec.get("mcap_usd_bn"), sig, rate) or old.get("credit") or {}
            if rec["credit"]:
                rec["credit"]["dd_vol_source"] = "implied" if iv else "realised 1y"
        except Exception as e:
            rec["credit_error"] = str(e)
        try:
            rec["returns"] = returns(tk, rec.get("fin_ccy") or rec.get("ccy"), rec.get("mcap_usd_bn"), rec.get("beta_spx"),
                                     rf10, rec.get("credit")) or old.get("returns") or {}
        except Exception as e:
            rec["returns"] = old.get("returns") or {}
            rec["returns_error"] = str(e)
        summary[co["id"]] = rec
        cr = rec.get("credit") or {}
        print(f"{co['id']:>10} last={rec.get('last')} {rec.get('ccy')} mcap=${rec.get('mcap_usd_bn')}B pe={rec.get('fwd_pe')} "
              f"nd/ebitda={cr.get('nd_ebitda')} dd={cr.get('dd')} roic={(rec.get('returns') or {}).get('roic_ttm')} "
              f"incr={(rec.get('returns') or {}).get('incr_roic')} wacc={(rec.get('returns') or {}).get('wacc')}")
        time.sleep(0.4)
    bm = {k: tech(v, {}) for k, v in bench.items()}
    write(OUT / "market.json", {"built": NOW.isoformat(timespec="seconds"), "rate": r(rate, 4), "rf10": r(rf10, 4), "erp": ERP, "benchmarks": bm,
                                "failures": failures, "companies": summary})
    if failures:
        print("FETCH FAILURES (kept last good values where available):")
        for f in failures:
            print(f"  {f['id']:>10} ({f['yf']}): {f['what']}")
    run_news(cos)
    build_iv_json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "options", "news"], default="full")
    ap.add_argument("--only", help="comma-separated ids, for testing")
    ap.add_argument("--force", action="store_true", help="options mode: run even when the US market is closed")
    a = ap.parse_args()
    cos = universe()
    if a.only:
        keep = set(a.only.split(","))
        cos = [c for c in cos if c["id"] in keep]
    if a.mode == "options":
        run_options(cos, force=a.force)
    else:
        {"full": run_full, "news": run_news}[a.mode](cos)


if __name__ == "__main__":
    sys.exit(main())
