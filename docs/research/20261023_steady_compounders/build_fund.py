"""SEC companyfacts → (시작일, 종목) 재무 특징 + 창 동안 변화. 시작일 이전에 제출(filed ≤ s)된 연간 값만.

출력 DataVault/staging/steady_compounders/derived/fund.parquet
주식 수·시총은 20261021_qqq_top_n/build_caps.py 방식: 여러 개념을 모아 '공시일 이후 분할'을 곱해 오늘 단위로 맞추고,
±2년 이웃 중앙값과 2.5배 넘게 다른 값은 버림 → 시총 = 분할 보정 종가(오늘 단위) × 주식 수(오늘 단위).
실행: .venv/bin/python -I build_fund.py
"""
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sc_common import ROOT, SEC_DIR, STAGE  # noqa: E402

CF = f"{SEC_DIR}/raw/companyfacts"
SUB = f"{SEC_DIR}/raw/submissions"
OUT = os.path.join(STAGE, "derived")
FORMS = {"10-K", "10-K/A", "10-KT", "20-F", "40-F"}
REV = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "RevenuesNetOfInterestExpense"]
NI = ["NetIncomeLoss", "ProfitLoss"]
EQ = ["StockholdersEquity"]
EQT = ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"]
SHARE_CONCEPTS = [("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"), ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
                  ("dei", "EntityCommonStockSharesOutstanding"), ("us-gaap", "CommonStockSharesOutstanding")]


def splits(t):
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if not os.path.exists(cp):
        return []
    ca = pd.read_csv(cp, parse_dates=["date"])
    ca = ca[ca.type == "split"].drop_duplicates("date")
    return [(d, n / m) for d, n, m in zip(ca.date, ca.numerator, ca.denominator)]


def annual(f, tags, instant=False):
    """연간 값(end, filed, val). 태그는 우선순위 — 같은 결산일은 앞 태그 값."""
    out = []
    for pri, tag in enumerate(tags):
        for v in f.get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", []):
            if v.get("form") not in FORMS or v.get("val") is None:
                continue
            if instant:
                if "start" in v:
                    continue
            else:
                if "start" not in v:
                    continue
                days = (pd.Timestamp(v["end"]) - pd.Timestamp(v["start"])).days
                if not 340 <= days <= 380:
                    continue
            out.append((pd.Timestamp(v["end"]), pd.Timestamp(v["filed"]), float(v["val"]), pri))
    df = pd.DataFrame(out, columns=["end", "filed", "val", "pri"])
    if df.empty:
        return df
    # 같은 결산일·같은 공시일에서는 우선 태그만
    df = df.sort_values(["end", "filed", "pri"]).drop_duplicates(["end", "filed"], keep="first")
    return df


def asof_series(df, s, n=5):
    """s 이전 공시 기준 결산일별 최신 값 → 최근 n 개(오래된→최근). 결산일 간격 300~430일만 이어짐."""
    if df.empty:
        return pd.Series(dtype=float)
    d = df[df.filed <= s]
    if d.empty:
        return pd.Series(dtype=float)
    last = d.sort_values("filed").groupby("end").val.last().sort_index()
    if (s - last.index[-1]).days > 550:
        return pd.Series(dtype=float)
    keep = [last.index[-1]]
    for e in last.index[::-1][1:]:
        gap = (keep[-1] - e).days
        if 300 <= gap <= 430:
            keep.append(e)
        elif gap > 430:
            break
        if len(keep) == n:
            break
    return last.reindex(sorted(keep))


def shares_today(f, t):
    rows = []
    for ns, k in SHARE_CONCEPTS:
        for v in f.get(ns, {}).get(k, {}).get("units", {}).get("shares", []):
            if v.get("val") and v["val"] > 1e6:
                rows.append((pd.Timestamp(v["end"]), pd.Timestamp(v["filed"]), float(v["val"])))
    df = pd.DataFrame(rows, columns=["end", "filed", "val"])
    if df.empty:
        return df
    fac = np.ones(len(df))
    for d, r in splits(t):
        fac[df.filed.to_numpy() < np.datetime64(d)] *= r
    df = df.assign(val=df.val * fac).sort_values(["end", "filed"]).reset_index(drop=True)
    ends, vals = df.end.to_numpy(), df.val.to_numpy()
    keep = np.ones(len(df), bool)
    for i in range(len(df)):
        w = np.abs((ends - ends[i]).astype("timedelta64[D]").astype(int)) <= 730
        med = np.median(vals[w])
        keep[i] = 1 / 2.5 <= vals[i] / med <= 2.5
    return df[keep].reset_index(drop=True)


def shares_asof(df, s):
    if df.empty:
        return np.nan
    d = df[df.filed <= s]
    if d.empty:
        return np.nan
    le = d.end.max()
    if (s - le).days > 550:
        return np.nan
    return float(d[d.end == le].iloc[-1].val)


def sector(sic):
    if sic is None or not str(sic).isdigit():
        return "기타"
    s = int(sic)
    if 3570 <= s <= 3579 or 3600 <= s <= 3699 or 7370 <= s <= 7379 or 3820 <= s <= 3829:
        return "기술"
    if 2833 <= s <= 2836 or 3840 <= s <= 3851 or 8000 <= s <= 8099 or s in (5122, 6324, 8731):
        return "헬스케어"
    if s in (6798,) or 6500 <= s <= 6553:
        return "부동산"
    if 6000 <= s <= 6799:
        return "금융"
    if 1300 <= s <= 1389 or 2900 <= s <= 2999 or 4610 <= s <= 4619 or 4922 <= s <= 4925:
        return "에너지"
    if 4900 <= s <= 4999 and not 4950 <= s <= 4959:     # 4950~4959 폐기물 처리(WM·RSG) 는 산업재(GICS 와 같게)
        return "유틸리티"
    if 4800 <= s <= 4899 or 2710 <= s <= 2741 or 7810 <= s <= 7841:
        return "통신·미디어"
    if 2000 <= s <= 2199 or 2840 <= s <= 2844 or 5400 <= s <= 5499 or 5140 <= s <= 5149 or s in (5912, 5331):
        return "필수소비재"
    if 5000 <= s <= 5999 or 7000 <= s <= 7099 or 3711 <= s <= 3716 or 2300 <= s <= 2399 or 3940 <= s <= 3949 \
            or 7900 <= s <= 7999 or 2510 <= s <= 2599 or 3630 <= s <= 3639 or 1520 <= s <= 1531:
        return "경기소비재"
    if 1000 <= s <= 1499 or 2400 <= s <= 2699 or 2800 <= s <= 2829 or 2850 <= s <= 2899 or 3200 <= s <= 3399:
        return "소재"
    return "산업재"


def main():
    W = pd.read_parquet(os.path.join(OUT, "windows.parquet"))
    W = W[W.start >= "2009-12-31"]
    closes = W[["start", "ticker", "close", "end", "close_end"]]
    rows, meta = [], {}
    for t, g in closes.groupby("ticker"):
        fp = f"{CF}/{t}.json"
        if not os.path.exists(fp):
            continue
        try:
            f = json.load(open(fp))["facts"]
        except Exception:  # noqa: BLE001
            continue
        sp = f"{SUB}/{t}.json.gz"
        sic = None
        if os.path.exists(sp):
            sj = json.loads(gzip.decompress(open(sp, "rb").read()))
            sic = sj.get("sic")
            meta[t] = {"sic": sic, "sicDescription": sj.get("sicDescription"), "name": sj.get("name")}
        rev, ni = annual(f, REV), annual(f, NI)
        eq, eqt, assets = annual(f, EQ, True), annual(f, EQT, True), annual(f, ["Assets"], True)
        gp, oi = annual(f, ["GrossProfit"]), annual(f, ["OperatingIncomeLoss"])
        cor = annual(f, ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"])
        sh = shares_today(f, t)
        for _, r in g.iterrows():
            s, e = r.start, r.end
            o = {"start": s, "ticker": t, "sic": sic, "sector": sector(sic)}
            R = asof_series(rev, s)
            N = asof_series(ni, s)
            if len(R) >= 4 and R.iloc[-4] > 0 and R.iloc[-1] > 0:
                o["rev_g3"] = (R.iloc[-1] / R.iloc[-4]) ** (1 / 3) - 1
            if len(R) >= 3:
                ch = R.diff().dropna().iloc[-4:]
                o["rev_up"] = float((ch > 0).mean())
            if len(N) >= 3:
                ch = N.diff().dropna().iloc[-4:]
                pos = (N.iloc[1:] > 0).iloc[-4:]
                o["ni_cons"] = float(((ch > 0) & pos.values).mean())
                o["ni_pos_all"] = float((N.iloc[-4:] > 0).all())
            ni0 = N.iloc[-1] if len(N) else np.nan
            rev0 = R.iloc[-1] if len(R) else np.nan
            E = asof_series(eq, s, 1)
            ET = asof_series(eqt, s, 1)
            A = asof_series(assets, s, 1)
            eq0 = E.iloc[-1] if len(E) else np.nan
            if eq0 > 0 and np.isfinite(ni0):
                o["roe"] = ni0 / eq0
            if len(A) and len(ET) and A.iloc[-1] > 0:
                o["lev"] = 1 - ET.iloc[-1] / A.iloc[-1]
            G = asof_series(gp, s, 1)
            if len(G) and rev0 > 0:
                o["gm"] = G.iloc[-1] / rev0
            else:
                C = asof_series(cor, s, 1)
                if len(C) and rev0 > 0:
                    o["gm"] = 1 - C.iloc[-1] / rev0
            O = asof_series(oi, s, 1)
            if len(O) and rev0 > 0:
                o["opm"] = O.iloc[-1] / rev0
            sh0 = shares_asof(sh, s)
            sh3 = shares_asof(sh, s - pd.DateOffset(years=3))
            if np.isfinite(sh0) and np.isfinite(sh3) and sh3 > 0:
                o["sh_chg3"] = sh0 / sh3 - 1
            mcap = r.close * sh0
            if np.isfinite(mcap) and mcap > 0:
                o["mcap"] = mcap
                o["ep"] = ni0 / mcap
                o["bp"] = eq0 / mcap
                o["sp"] = rev0 / mcap
            # 창 동안(끝 e 시점 공시 기준)
            R1, N1 = asof_series(rev, e, 1), asof_series(ni, e, 1)
            sh1 = shares_asof(sh, e)
            if len(R1) and rev0 > 0 and R1.iloc[-1] > 0 and len(R):
                yrs = max((R1.index[-1] - R.index[-1]).days / 365.25, 0.5)
                o["w_rev_g"] = (R1.iloc[-1] / rev0) ** (1 / yrs) - 1
                o["w_yrs"] = yrs
                if np.isfinite(sh1) and np.isfinite(mcap) and mcap > 0:
                    mcap1 = r.close_end * sh1
                    o["w_dlog_mcap"] = np.log(mcap1 / mcap)
                    o["w_dlog_rev"] = np.log(R1.iloc[-1] / rev0)
                    o["w_dlog_ps"] = o["w_dlog_mcap"] - o["w_dlog_rev"]
                    o["w_dlog_sh"] = np.log(sh1 / sh0)
            if len(N1) and ni0 > 0 and N1.iloc[-1] > 0 and len(N):
                yrs = max((N1.index[-1] - N.index[-1]).days / 365.25, 0.5)
                o["w_ni_g"] = (N1.iloc[-1] / ni0) ** (1 / yrs) - 1
            rows.append(o)
    F = pd.DataFrame(rows)
    F.to_parquet(os.path.join(OUT, "fund.parquet"))
    json.dump(meta, open(os.path.join(OUT, "sec_meta.json"), "w"), ensure_ascii=False, indent=0)
    cov = F.drop(columns=["start", "ticker", "sic", "sector"]).notna().mean().round(3)
    print("행", len(F), "종목", F.ticker.nunique(), "\n채움 비율\n", cov.to_string(), flush=True)


if __name__ == "__main__":
    main()
