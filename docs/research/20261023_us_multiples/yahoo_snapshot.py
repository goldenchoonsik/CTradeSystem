"""현재 S&P500(2026-08-18 명단) Yahoo quoteSummary 한 시점 수집 — 기준·예상 PER, PBR, 예상 이익 성장.

저장: DataVault/staging/us_multiples/raw/yahoo_quotesummary/<받은날>/<티커>.json.gz (무수정), 끝에 snapshot.csv 요약.
쿠키(fc.yahoo.com) + crumb 필요. 초당 2회 이하.  실행: .venv/bin/python -I yahoo_snapshot.py
"""
import gzip
import json
import os
import sys
import time
from datetime import date

import subprocess
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT, members_at, membership  # noqa: E402

OUT = os.path.join(ROOT, "staging", "us_multiples", "raw", "yahoo_quotesummary", f"{date.today():%Y%m%d}")
MODS = "defaultKeyStatistics,financialData,earningsTrend,summaryDetail,price"


JAR = os.path.join(tempfile.gettempdir(), "ct_yahoo_cookies.txt")


def curl(url, out=None):
    """curl 로 받기(파이썬 requests 는 Yahoo 가 429 로 막음 — 2026-10-23 실측). → (상태코드, 내용)."""
    cmd = ["curl", "-sS", "-m", "30", "-A", "Mozilla/5.0", "-b", JAR, "-c", JAR, "-w", "\n%{http_code}", url]
    p = subprocess.run(cmd, capture_output=True, timeout=45)
    body, _, code = p.stdout.rpartition(b"\n")
    return int(code or 0), body


def session():
    curl("https://fc.yahoo.com")
    return None, curl("https://query2.finance.yahoo.com/v1/test/getcrumb")[1].decode().strip()


def raw(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d.get("raw") if isinstance(d, dict) else d


def main():
    os.makedirs(OUT, exist_ok=True)
    memb = membership()
    tick = sorted({y for _, y in members_at(memb, memb.date.max()) if y})
    s, crumb = session()
    rows = []
    for i, t in enumerate(tick):
        fp = os.path.join(OUT, f"{t}.json.gz")
        if not os.path.exists(fp):
            for k in range(6):
                code, body = curl(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}?modules={MODS}&crumb={crumb}")
                if code == 401:
                    s, crumb = session()
                    continue
                if code == 429:                          # 요청 제한 → 물러났다가 다시
                    time.sleep(30 * (k + 1))
                    continue
                break
            time.sleep(0.6)
            if code != 200:
                print("실패", t, code, flush=True)
                continue
            open(fp, "wb").write(gzip.compress(body))
        j = json.loads(gzip.decompress(open(fp, "rb").read()))
        res = (j.get("quoteSummary", {}).get("result") or [{}])[0]
        ks, fd, sd, et = res.get("defaultKeyStatistics", {}), res.get("financialData", {}), res.get("summaryDetail", {}), res.get("earningsTrend", {})
        tr = {x.get("period"): x for x in et.get("trend", [])}
        rows.append({"ticker": t, "mcap": raw(sd, "marketCap") or raw(res.get("price", {}), "marketCap"),
                     "trailingPE": raw(sd, "trailingPE"), "forwardPE": raw(ks, "forwardPE") or raw(sd, "forwardPE"),
                     "priceToBook": raw(ks, "priceToBook"), "pegRatio": raw(ks, "pegRatio"),
                     "trailingEps": raw(ks, "trailingEps"), "forwardEps": raw(ks, "forwardEps"),
                     "roe": raw(fd, "returnOnEquity"), "revenueGrowth": raw(fd, "revenueGrowth"), "earningsGrowth": raw(fd, "earningsGrowth"),
                     "eps_cur_year": raw(tr.get("0y", {}), "earningsEstimate", "avg"), "eps_next_year": raw(tr.get("+1y", {}), "earningsEstimate", "avg"),
                     "g_cur_year": raw(tr.get("0y", {}), "growth"), "g_next_year": raw(tr.get("+1y", {}), "growth"),
                     "g_long": raw(tr.get("+5y", {}), "growth") or raw(tr.get("LTG", {}), "growth"),
                     "n_analysts_next": raw(tr.get("+1y", {}), "earningsEstimate", "numberOfAnalysts"),
                     "price": raw(fd, "currentPrice"), "dividendYield": raw(sd, "dividendYield")})
        if (i + 1) % 100 == 0:
            print("진행", i + 1, len(tick), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "snapshot.csv"), index=False)
    print("끝", len(df), "채움", df.notna().mean().round(2).to_dict(), flush=True)


if __name__ == "__main__":
    main()
