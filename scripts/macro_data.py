"""Capital-market backdrop from FRED (free, no key): Treasury curve, term premium, credit spreads, long corporate yields.

Writes site/data/macro.json with ~3 years of weekly observations plus latest values.
Run: python scripts/macro_data.py
"""
import csv
import io
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site/data/macro.json"
SERIES = {
    "DGS3MO": "3-month Treasury",
    "DGS2": "2-year Treasury",
    "DGS5": "5-year Treasury",
    "DGS10": "10-year Treasury",
    "DGS30": "30-year Treasury",
    "THREEFYTP10": "10-year term premium (Kim-Wright)",
    "BAMLC0A0CM": "US investment-grade OAS",
    "BAMLH0A0HYM2": "US high-yield OAS",
    "BAMLC4A0C15PYEY": "IG corporates 15y+ effective yield",
}


def fetch(sid, start):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=40, headers={"User-Agent": "A_Chain research"})
            r.raise_for_status()
            break
        except Exception as e:
            last = e
            time.sleep(3 + attempt * 4)
    else:
        raise last
    rows = list(csv.reader(io.StringIO(r.text)))[1:]
    out = []
    for d, v in rows:
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


def weekly(obs):
    """Keep the last observation of each ISO week."""
    keep = {}
    for d, v in obs:
        y, w, _ = date.fromisoformat(d).isocalendar()
        keep[(y, w)] = (d, v)
    return [list(keep[k]) for k in sorted(keep)]


def main():
    start = (date.today() - timedelta(days=3 * 365 + 10)).isoformat()
    prev = {}
    try:
        prev = json.loads(OUT.read_text())
    except Exception:
        pass
    data, failed = {}, []
    for sid, label in SERIES.items():
        try:
            obs = fetch(sid, start)
            if not obs:
                raise ValueError("no observations")
            data[sid] = {"label": label, "last": obs[-1], "weekly": weekly(obs)}
            print(f"{sid:18} {obs[-1]}")
        except Exception as e:
            failed.append(sid)
            if sid in prev.get("series", {}):
                data[sid] = prev["series"][sid]      # keep the last good copy
            print(f"{sid:18} FAILED: {e}")
    if len(failed) == len(SERIES):
        print("All FRED requests failed; keeping previous macro.json")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "source": "FRED, Federal Reserve Bank of St. Louis", "failed": failed, "series": data},
                              separators=(",", ":")))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
