"""Pull disclosed customer-concentration and backlog facts from SEC filings.

For every SEC filer in data/universe.yaml this script:
  1. finds the latest annual report (10-K / 20-F / 40-F) and latest 10-Q,
  2. downloads the filing's XBRL instance,
  3. keeps facts that describe concentration of revenue or receivables by customer
     (e.g. us-gaap:ConcentrationRiskPercentage1 on srt:MajorCustomersAxis)
     plus remaining performance obligations (backlog).

Output: data/raw/sec_concentration.json. These are "D" (disclosed) facts; mapping an
anonymised member such as nvda:CustomerAMember to a real company is done by hand in
data/edges.yaml and tagged accordingly.

SEC asks for a descriptive User-Agent with contact details and <=10 requests/second.
"""
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = "A_Chain research github.com/Charliebrown390/A_Chain Charliebrown390@users.noreply.github.com"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})

ANNUAL = {"10-K", "20-F", "40-F", "10-K/A", "20-F/A"}
QUARTERLY = {"10-Q"}
KEEP_NAMES = re.compile(r"(ConcentrationRisk|RemainingPerformanceObligation|RevenueRemainingPerformance|Backlog)", re.I)
CUSTOMER_AXES = re.compile(r"(MajorCustomer|Customer|ConcentrationRisk|Counterpart)", re.I)


def get(url, **kw):
    for attempt in range(4):
        r = S.get(url, timeout=60, **kw)
        time.sleep(0.15)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 503):
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
    r.raise_for_status()


def local(tag):
    return tag.split("}", 1)[-1]


def prefix_of(tag, nsmap):
    if tag.startswith("{"):
        uri = tag[1:].split("}", 1)[0]
        for p, u in nsmap.items():
            if u == uri:
                return p
    return ""


def find_instance(cik, acc):
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
    idx = get(base + "/index.json").json()
    names = [i["name"] for i in idx["directory"]["item"]]
    for n in names:
        if n.endswith("_htm.xml"):
            return base + "/" + n
    for n in names:
        if n.endswith(".xml") and not re.search(r"(FilingSummary|_cal|_def|_lab|_pre|MetaLinks)", n):
            return base + "/" + n
    return None


def parse_instance(xml_bytes):
    nsmap = {}
    for _, (p, u) in ET.iterparse(__import__("io").BytesIO(xml_bytes), events=["start-ns"]):
        nsmap.setdefault(p, u)
    root = ET.fromstring(xml_bytes)
    ctx = {}
    for c in root:
        if local(c.tag) != "context":
            continue
        cid = c.get("id")
        info = {"dims": {}}
        for el in c.iter():
            ln = local(el.tag)
            if ln == "explicitMember":
                info["dims"][el.get("dimension")] = (el.text or "").strip()
            elif ln == "typedMember":
                info["dims"][el.get("dimension")] = "".join(el.itertext()).strip()
            elif ln in ("startDate", "endDate", "instant"):
                info[ln] = (el.text or "").strip()
        ctx[cid] = info
    facts = []
    for el in root:
        ln = local(el.tag)
        if not KEEP_NAMES.search(ln):
            continue
        c = ctx.get(el.get("contextRef"), {})
        dims = c.get("dims", {})
        is_conc = "Concentration" in ln
        if is_conc and not any(CUSTOMER_AXES.search(a) for a in dims):
            continue
        try:
            val = float(el.text)
        except (TypeError, ValueError):
            continue
        facts.append({
            "concept": f"{prefix_of(el.tag, nsmap)}:{ln}",
            "value": val,
            "unit": el.get("unitRef"),
            "start": c.get("startDate"),
            "end": c.get("endDate") or c.get("instant"),
            "dims": dims,
        })
    return facts


def main():
    uni = yaml.safe_load((ROOT / "data/universe.yaml").read_text())
    tick = get("https://www.sec.gov/files/company_tickers.json").json()
    cik_by = {v["ticker"].upper(): v["cik_str"] for v in tick.values()}
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "companies": {}}
    for co in uni["companies"]:
        sym = co.get("sec")
        if not sym:
            continue
        rec = {"cik": None, "filings": [], "errors": []}
        out["companies"][co["id"]] = rec
        cik = cik_by.get(sym.upper())
        if not cik:
            rec["errors"].append(f"ticker {sym} not in SEC list")
            continue
        rec["cik"] = cik
        try:
            subs = get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
            r = subs["filings"]["recent"]
            picks, seen = [], set()
            for form, acc, date, doc in zip(r["form"], r["accessionNumber"], r["filingDate"], r["primaryDocument"]):
                kind = "annual" if form in ANNUAL else "quarterly" if form in QUARTERLY else None
                if kind and kind not in seen:
                    picks.append((kind, form, acc, date, doc))
                    seen.add(kind)
                if len(seen) == 2:
                    break
            for kind, form, acc, date, doc in picks:
                f = {"kind": kind, "form": form, "accession": acc, "filed": date,
                     "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}",
                     "facts": []}
                try:
                    inst = find_instance(cik, acc)
                    if inst:
                        f["instance"] = inst
                        f["facts"] = parse_instance(get(inst).content)
                    else:
                        f["note"] = "no XBRL instance found"
                except Exception as e:  # keep going on one bad filing
                    f["note"] = f"parse error: {e}"
                rec["filings"].append(f)
        except Exception as e:
            rec["errors"].append(str(e))
        n = sum(len(f["facts"]) for f in rec["filings"])
        print(f"{co['id']:>10}  cik={cik}  filings={len(rec['filings'])}  facts={n}  {rec['errors'] or ''}", flush=True)
    dest = ROOT / "data/raw/sec_concentration.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    print("wrote", dest)


if __name__ == "__main__":
    sys.exit(main())
