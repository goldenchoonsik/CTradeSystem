"""단면 패널: (분기말 s, 당시 S&P500 종목) 마다 현재 지표(filed ≤ s)·1년/3년 뒤 실제 지표·1년/3년 총수익.

대상·가격·시총 규칙은 20261023_steady_compounders 와 같다(windows.parquet 의 시작일 명단·거래대금 필터 재사용,
SEC 처리 함수 build_fund.annual/asof_series/shares_today 재사용).
출력 DataVault/staging/us_multiples/derived/panel.parquet.  실행: .venv/bin/python -I build_panel.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT, STAGE as SC_STAGE, load_raw, price_tr  # noqa: E402
from build_fund import CF, EQ, NI, REV, annual, asof_series, sector, shares_asof, shares_today  # noqa: E402

OUT = os.path.join(ROOT, "staging", "us_multiples", "derived")
END = pd.Timestamp("2026-08-31")
CFO = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]


def last_fy(df, s):
    """s 시점 공시 기준 최신 FY (결산일, 값) 또는 (None, nan)."""
    x = asof_series(df, s, 1)
    return (x.index[-1], float(x.iloc[-1])) if len(x) else (None, np.nan)


def fy_in(df, asof, lo, hi):
    """asof 까지 공시된 FY 중 결산일이 (lo, hi] 인 마지막 것의 값."""
    if df.empty:
        return np.nan, None
    d = df[(df.filed <= asof) & (df.end > lo) & (df.end <= hi)]
    if d.empty:
        return np.nan, None
    last = d.sort_values("filed").groupby("end").val.last().sort_index()
    return float(last.iloc[-1]), last.index[-1]


def main():
    W = pd.read_parquet(os.path.join(SC_STAGE, "derived", "windows.parquet"))
    W = W[W.start >= "2009-12-01"][["start", "ticker", "close", "divy", "dvol"]]
    cal = load_raw("SPY").loc["2009-06-01":END].index
    pos = {d: i for i, d in enumerate(cal)}
    meta = json.load(open(os.path.join(SC_STAGE, "derived", "sec_meta.json")))
    rows = []
    for t, g in W.groupby("ticker"):
        fp = f"{CF}/{t}.json"
        px = price_tr(t, cal)
        if px is None or not os.path.exists(fp):
            continue
        try:
            f = json.load(open(fp))["facts"]
        except Exception:  # noqa: BLE001
            continue
        rev, ni = annual(f, REV), annual(f, NI)
        eq, assets = annual(f, EQ, True), annual(f, ["Assets"], True)
        gp, cfo = annual(f, ["GrossProfit"]), annual(f, CFO)
        cor = annual(f, ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"])
        sh = shares_today(f, t)
        tr = px["tr"].values
        close = px["close"].values
        sic = (meta.get(t) or {}).get("sic")
        for _, r in g.iterrows():
            s = r.start
            i0 = pos.get(s)
            if i0 is None:
                continue
            o = {"start": s, "ticker": t, "sector": sector(sic) if sic else None, "dvol": r.dvol, "dp": r.divy}
            e0, n0 = last_fy(ni, s)
            _, r0 = last_fy(rev, s)
            _, b0 = last_fy(eq, s)
            _, a0 = last_fy(assets, s)
            _, c0 = last_fy(cfo, s)
            _, g0 = last_fy(gp, s)
            if not np.isfinite(g0):
                _, cr = last_fy(cor, s)
                g0 = r0 - cr if np.isfinite(cr) and np.isfinite(r0) else np.nan
            sh0 = shares_asof(sh, s)
            mcap = close[i0] * sh0
            if not (np.isfinite(mcap) and mcap > 0) or e0 is None:
                continue
            o.update({"mcap": mcap, "ep": n0 / mcap, "bp": b0 / mcap, "sp": r0 / mcap, "cfp": c0 / mcap,
                      "roe": n0 / b0 if b0 > 0 else np.nan, "gpa": g0 / a0 if a0 > 0 else np.nan,
                      "eps0": n0 / sh0, "pe": mcap / n0 if n0 > 0 else np.nan, "pb": mcap / b0 if b0 > 0 else np.nan})
            R = asof_series(rev, s, 4)
            if len(R) == 4 and R.iloc[0] > 0 and R.iloc[-1] > 0:
                o["rev_g3"] = (R.iloc[-1] / R.iloc[0]) ** (1 / 3) - 1
            N2 = asof_series(ni, s, 2)
            if len(N2) == 2:
                o["eps_chg1_past"] = (N2.iloc[-1] - N2.iloc[0]) / sh0 / close[i0]
            # 미래(1년·3년): 그 시점 공시 기준 최신 FY, 현재 FY 보다 충분히 뒤여야
            for h, gap in ((1, 300), (3, 900)):
                sf = s + pd.DateOffset(years=h)
                if sf > END:
                    continue
                jf = cal[cal <= sf][-1]
                i1 = pos[jf]
                o[f"ret{h}"] = tr[i1] / tr[i0] - 1
                ef, nf = last_fy(ni, sf)
                if ef is None or (ef - e0).days < gap:
                    continue
                shf = shares_asof(sh, sf)
                _, bf = last_fy(eq, sf)
                _, rf_ = last_fy(rev, sf)
                if np.isfinite(shf) and shf > 0:
                    epsf = nf / shf
                    o[f"deps{h}_p"] = (epsf - o["eps0"]) / close[i0]
                    if epsf > 0 and o["eps0"] > 0:
                        o[f"leps{h}"] = np.log(epsf / o["eps0"])
                if bf > 0:
                    o[f"roe_f{h}"] = nf / bf
                if rf_ > 0 and r0 > 0:
                    o[f"lrev{h}"] = np.log(rf_ / r0)
            # 보조(변경 기록 1): 수익 기간과 같은 회계연도로 맞춘 완전 예지 — 기준 FY 결산 (s−1y, s], 목표 FY 결산 (s+h−1y, s+h]
            nb, eb = fy_in(ni, s + pd.DateOffset(months=15), s - pd.DateOffset(years=1), s)
            if eb is not None:
                shb = shares_asof(sh, s + pd.DateOffset(months=4))
                for h in (1, 3):
                    sf = s + pd.DateOffset(years=h)
                    if sf + pd.DateOffset(months=3) > END:
                        continue
                    nt, et = fy_in(ni, sf + pd.DateOffset(months=15), sf - pd.DateOffset(years=1), sf)
                    sht = shares_asof(sh, sf + pd.DateOffset(months=4))
                    if et is None or not (np.isfinite(shb) and np.isfinite(sht)) or shb <= 0 or sht <= 0:
                        continue
                    o[f"pf_deps{h}_p"] = (nt / sht - nb / shb) / close[i0]
                    if nt > 0 and nb > 0:
                        o[f"pf_leps{h}"] = np.log((nt / sht) / (nb / shb))
            rows.append(o)
    P = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    P.to_parquet(os.path.join(OUT, "panel.parquet"))
    print("행", len(P), "종목", P.ticker.nunique(), "분기", P.start.nunique())
    print(P.drop(columns=["start", "ticker", "sector"]).notna().mean().round(3).to_string())


if __name__ == "__main__":
    main()
