"""2부 바구니 시험 — 개발 구간 통과 특징의 결합 점수 상위 20종목, 매년 재조정. 사전등록 PREREGISTRATION.md.

선정: (Y−1) 12월 마지막 거래일(분기 시작일) 특징 → Y 첫 거래일 종가에 동일가중 매수, Y 마지막 거래일까지 보유(표류), 다음 해 재조정.
비용 편도 0.1% × 회전율. 비교: SPY·QQQ·RSP(총수익), 같은 명단 무작위 20종목 2,000회.
입력: results.json(2부 개발 판정), derived/windows.parquet·fund.parquet.  출력 results_basket.json
실행: .venv/bin/python -I basket.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sc_common import HERE, ROOT, load_raw, price_tr  # noqa: E402
from analysis import FEATS, load  # noqa: E402

N, DRAWS, COST = 20, 2000, 0.001
END = pd.Timestamp("2026-08-31")
rng = np.random.default_rng(20261023)


def chosen_features():
    r = json.load(open(os.path.join(HERE, "results.json"), encoding="utf-8"))["2부"]
    out = []
    for name, col, sign, binary in FEATS:
        dv = r[name]["개발"]
        if dv and dv["lift"] >= 1.25 and dv["연도_lift>1"] >= 0.6:
            out.append((name, col, sign))
    return out


def composite(d, feats):
    zs = []
    for _, col, sign in feats:
        v = d[col].astype(float)
        lo, hi = v.quantile(0.01), v.quantile(0.99)
        v = v.clip(lo, hi)
        z = (v - v.mean()) / (v.std() if v.std() > 0 else 1)
        zs.append(sign * z)
    Z = pd.concat(zs, axis=1)
    ok = Z.notna().sum(axis=1) >= max(1, len(feats) / 2)
    return Z.mean(axis=1).where(ok)


def perf(daily, rf):
    v = (1 + daily).cumprod()
    yrs = len(daily) / 252
    ex = daily - rf.reindex(daily.index).fillna(0)
    return {"CAGR%": round(100 * (v.iloc[-1] ** (1 / yrs) - 1), 2), "변동성%": round(100 * daily.std() * np.sqrt(252), 2),
            "샤프": round(float(ex.mean() / daily.std() * np.sqrt(252)), 3), "MDD%": round(100 * float((v / v.cummax() - 1).min()), 1)}


def perf_np(paths, rf_vec):
    """paths: (draws, days) 일 수익."""
    v = np.cumprod(1 + paths, axis=1)
    yrs = paths.shape[1] / 252
    cagr = v[:, -1] ** (1 / yrs) - 1
    sd = paths.std(axis=1)
    sh = (paths - rf_vec).mean(axis=1) / sd * np.sqrt(252)
    mdd = (v / np.maximum.accumulate(v, axis=1) - 1).min(axis=1)
    return cagr, sd * np.sqrt(252), sh, mdd


def run(X, feats, y0, y1, cal, P, rf):
    years = list(range(y0, y1 + 1))
    port, sel_log = [], {}
    rnd = []
    prev_w = None
    for Y in years:
        dec = X[(X.start.dt.year == Y - 1) & (X.start.dt.month == 12)]
        if dec.empty:
            continue
        days = cal[(cal.year == Y) & (cal <= END)]
        if len(days) < 2:
            continue
        d0 = cal[cal < days[0]][-1]                                      # 신호일(12월 마지막 거래일)
        pool = dec[dec.ticker.isin(P.keys())].copy()
        pool = pool[[pd.notna(P[t]["tr"].get(days[0], np.nan)) for t in pool.ticker]]
        pool["score"] = composite(pool, feats)
        pick = pool.dropna(subset=["score"]).nlargest(N, "score").ticker.tolist()
        sel_log[Y] = pick
        # 첫 거래일 종가 매수 → 그해 마지막 거래일
        G = pd.DataFrame({t: P[t]["tr"].reindex(days) for t in pool.ticker}).ffill()
        G = G / G.iloc[0]
        g = G[pick].mean(axis=1)
        r = g.pct_change().fillna(0.0)
        # 비용: 회전율(직전 해 말 표류 비중 → 새 동일가중)
        w_new = pd.Series(1 / N, index=pick)
        turn = 1.0 if prev_w is None else float(w_new.sub(prev_w, fill_value=0).abs().sum())
        r.iloc[0] -= COST * turn
        prev_w = (G[pick].iloc[-1] / G[pick].iloc[-1].sum())
        port.append(r)
        # 무작위
        M = G.values                                                     # (days, n)
        n = M.shape[1]
        idx = np.argsort(rng.random((DRAWS, n)), axis=1)[:, :N]
        gv = M[:, idx].mean(axis=2).T                                    # (draws, days)
        rr = np.diff(gv, axis=1, prepend=gv[:, :1]) / np.concatenate([gv[:, :1], gv[:, :-1]], axis=1)
        rr[:, 0] -= COST * 2.0
        rnd.append(rr)
    pr = pd.concat(port)
    rf_vec = rf.reindex(pr.index).fillna(0).values
    out = {"바구니": perf(pr, rf)}
    for b in ("SPY", "QQQ", "RSP"):
        tr = P[b]["tr"].reindex(pr.index)
        first = cal[cal < pr.index[0]][-1]
        br = P[b]["tr"].reindex([first] + list(pr.index)).pct_change().dropna()
        out[b] = perf(br, rf)
    R = np.concatenate(rnd, axis=1)
    c, v, s, m = perf_np(R, rf_vec)
    out["무작위20_중앙"] = {"CAGR%": round(100 * float(np.median(c)), 2), "변동성%": round(100 * float(np.median(v)), 2),
                         "샤프": round(float(np.median(s)), 3), "MDD%": round(100 * float(np.median(m)), 1)}
    out["바구니_무작위_백분위"] = {"CAGR": round(float((c < out["바구니"]["CAGR%"] / 100).mean()), 3),
                             "샤프": round(float((s < out["바구니"]["샤프"]).mean()), 3),
                             "MDD(얕을수록 높음)": round(float((m < out["바구니"]["MDD%"] / 100).mean()), 3)}
    return out, sel_log


def main():
    X = load()
    feats = chosen_features()
    print("결합에 쓰는 특징(개발 통과):", [f[0] for f in feats], flush=True)
    cal = load_raw("SPY").loc["2009-01-01":END].index
    need = set(X[X.start >= "2009-12-01"].ticker) | {"SPY", "QQQ", "RSP"}
    P = {t: price_tr(t, cal) for t in need}
    P = {t: v for t, v in P.items() if v is not None}
    pm = pd.read_csv(os.path.join(ROOT, "staging", "us_valuation", "derived", "panel_monthly.csv"), index_col="ym")
    rfm = pm["rf"].dropna()
    rf = pd.Series([rfm.get(f"{d.year}-{d.month:02d}", rfm.iloc[-1]) / 21 for d in cal], index=cal)
    res = {"특징": [f[0] for f in feats]}
    if not feats:
        res["판정"] = "개발 통과 특징 없음 — 바구니 시험 불성립"
    else:
        dev, sel_d = run(X, feats, 2010, 2016, cal, P, rf)
        hold, sel_h = run(X, feats, 2017, 2026, cal, P, rf)
        b, s = hold["바구니"], hold
        ok = (b["샤프"] > s["SPY"]["샤프"] and b["샤프"] > s["RSP"]["샤프"] and b["샤프"] > s["무작위20_중앙"]["샤프"]
              and b["CAGR%"] >= s["SPY"]["CAGR%"] - 1.0)
        res.update({"개발(표본 내) 2010~2016": dev, "확인 2017~2026-08": hold,
                    "판정": "실전 후보" if ok else "설명용", "선정_확인": sel_h, "선정_개발": sel_d})
    with open(os.path.join(HERE, "results_basket.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if not k.startswith("선정")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
