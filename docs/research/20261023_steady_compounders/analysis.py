"""1부(설명)·2부(특징 판정) — 사전등록 PREREGISTRATION.md.  출력 results.json + 표 출력.
실행: .venv/bin/python -I analysis.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sc_common import HERE, STAGE  # noqa: E402

D = os.path.join(STAGE, "derived")
PRICE_F = ["p_cagr", "p_mdd", "p_r2", "p_SC", "vol1y", "beta", "mom12_1", "hi52", "divy"]
FUND_F = ["mcap", "rev_g3", "rev_up", "ni_cons", "ni_pos_all", "roe", "gm", "opm", "lev", "sh_chg3", "ep", "bp", "sp"]
WIN_F = ["w_rev_g", "w_ni_g", "w_dlog_mcap", "w_dlog_rev", "w_dlog_ps", "w_dlog_sh"]
# 2부 특징: (코드, 열, 방향, 이진)
FEATS = [("F1 과거SC", "p_SC", +1, True), ("F2 과거R2", "p_r2", +1, False), ("F3 변동성", "vol1y", -1, False),
         ("F4 베타", "beta", -1, False), ("F5 모멘텀", "mom12_1", +1, False), ("F6 ROE", "roe", +1, False),
         ("F7 매출증가횟수", "rev_up", +1, False), ("F8 순이익일관", "ni_cons", +1, False), ("F9 배당", "div_pay", +1, True),
         ("F10 부채", "lev", -1, False)]
EXPLAIN = [("E/P", "ep", +1, False), ("시총", "mcap", +1, False)]
DEV = ("2010-03-01", "2016-12-31")
HOLD = ("2017-03-01", "2023-06-30")


def load():
    W = pd.read_parquet(os.path.join(D, "windows.parquet"))
    F = pd.read_parquet(os.path.join(D, "fund.parquet"))
    X = W.merge(F, on=["start", "ticker"], how="left")
    from build_fund import sector
    X["sector"] = [sector(v) if pd.notna(v) else None for v in X.sic]     # 업종 규칙 수정분을 재빌드 없이 반영
    X["div_pay"] = (X.divy > 0).astype(float)
    X["grp"] = np.select([X.SC == 1, X.RW == 1], ["SC", "RW"], "나머지")
    X.loc[X.SC.isna(), "grp"] = None
    return X


def profile(X, cols, since=None):
    d = X[X.grp.notna()]
    if since:
        d = d[d.start >= since]
    out = {}
    med = d.groupby("grp")[cols].median()
    # 시작일 안 백분위(시대 효과 제거)
    pct = d.groupby("start")[cols].rank(pct=True)
    pct["grp"] = d.grp
    pm = pct.groupby("grp")[cols].median()
    for c in cols:
        out[c] = {g: {"중앙값": (round(float(med.loc[g, c]), 4) if pd.notna(med.loc[g, c]) else None),
                      "백분위": (round(float(pm.loc[g, c]), 3) if pd.notna(pm.loc[g, c]) else None)} for g in med.index}
    out["_n"] = d.grp.value_counts().to_dict()
    return out


def market_regime(X):
    d = X[X.grp.notna()]
    b = pd.cut(d.spy_mdd, [-1, -0.25, -0.15, 0], labels=["SPY 낙폭 25%+", "15~25%", "15% 미만"])
    g = d.groupby(b, observed=False)
    return {str(k): {"창": int(len(v)), "시작일수": int(v.start.nunique()), "SC비율": round(float(v.SC.mean()), 3),
                     "RW비율": round(float(v.RW.mean()), 3)} for k, v in g}


def sectors(X):
    d = X[(X.grp.notna()) & (X.start >= "2010-01-01") & X.sector.notna()]
    tot = d.groupby("sector").agg(창=("SC", "size"), SC비율=("SC", "mean"), RW비율=("RW", "mean"))
    # 시작일 기대 대비(O/E): 시대별 SC 비율 차이 보정
    rate = d.groupby("start").SC.transform("mean")
    oe = d.assign(e=rate).groupby("sector").apply(lambda v: v.SC.sum() / v.e.sum() if v.e.sum() > 0 else np.nan, include_groups=False)
    tot["SC_관측/기대"] = oe
    return tot.sort_values("SC_관측/기대", ascending=False).round(3).to_dict(orient="index")


def feature_lift(X, col, sign, binary, lo, hi):
    d = X[(X.start >= lo) & (X.start <= hi) & X.SC.notna() & X[col].notna()].copy()
    if d.empty:
        return None
    if binary:
        d["fav"] = d[col] == (1 if sign > 0 else 0)
        d["unf"] = ~d.fav
    else:
        r = d.groupby("start")[col].rank(pct=True)
        r = r if sign > 0 else 1 - r + 1e-9
        d["fav"] = r > 0.8
        d["unf"] = r <= 0.2
    d["rate"] = d.groupby("start").SC.transform("mean")
    fav = d[d.fav]
    exp = fav.rate.sum()
    lift = fav.SC.sum() / exp if exp > 0 else np.nan
    yr = fav.assign(y=fav.start.dt.year).groupby("y").apply(
        lambda v: (v.SC.sum() / v.rate.sum()) if v.rate.sum() > 0 else np.nan, include_groups=False).dropna()
    by_start = d.groupby("start").apply(lambda v: pd.Series({
        "cagr_diff": v[v.fav].cagr.mean() - v[v.unf].cagr.mean(),
        "mdd_diff": v[v.fav].mdd.median() - v[v.unf].mdd.median()}), include_groups=False)
    return {"n": int(len(d)), "n_fav": int(len(fav)), "SC_fav": int(fav.SC.sum()), "기대": round(float(exp), 1),
            "lift": round(float(lift), 3), "연도_lift>1": round(float((yr > 1).mean()), 2), "연도수": int(len(yr)),
            "연복리차(유리−불리)%p": round(100 * float(by_start.cagr_diff.mean()), 2),
            "낙폭중앙차(유리−불리)%p": round(100 * float(by_start.mdd_diff.mean()), 2)}


def part2(X):
    res = {}
    for name, col, sign, binary in FEATS + EXPLAIN:
        dv = feature_lift(X, col, sign, binary, *DEV)
        ho = feature_lift(X, col, sign, binary, *HOLD)
        pre = feature_lift(X, col, sign, binary, "2000-03-01", "2009-12-31") if col in ("p_SC", "p_r2", "vol1y", "beta", "mom12_1", "div_pay") else None
        if name.startswith("F"):
            ok_d = dv and dv["lift"] >= 1.25 and dv["연도_lift>1"] >= 0.6
            ok_h = ho and ho["lift"] >= 1.25 and ho["연도_lift>1"] >= 0.6
            verdict = "특징 확인" if ok_d and ok_h else ("개발만" if ok_d else "아님")
        else:
            verdict = "설명용"
        res[name] = {"개발": dv, "확인": ho, "2000~09(설명)": pre, "판정": verdict}
    return res


def main():
    X = load()
    res = {
        "기본": {"창": int(X.grp.notna().sum()), "SC": int((X.SC == 1).sum()), "RW": int((X.RW == 1).sum()),
               "SC비율": round(float(X.SC.mean()), 4), "RW비율": round(float(X.RW.mean()), 4)},
        "시장국면별": market_regime(X),
        "프로필_가격(2000~)": profile(X, PRICE_F),
        "프로필_재무(2010~)": profile(X, FUND_F, "2010-01-01"),
        "창동안(2010~)": profile(X, WIN_F, "2010-01-01"),
        "업종(2010~)": sectors(X),
        "2부": part2(X),
    }
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
