"""B. 예측 — 10년 표본외(B1·B1b), 1·12개월 표본외(B2), 연 1회 비중 기울기(B3), 현재 판독. 사전등록 PREREGISTRATION.md.

출력 results_B.json + 표 출력.  실행: .venv/bin/python -I analysis_B.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vlib import HERE, panel  # noqa: E402

P = pd.Period


def fit_pred(x_tr, y_tr, x_now):
    d = pd.concat([x_tr, y_tr], axis=1).dropna()
    if len(d) < 24:
        return np.nan
    b = np.polyfit(d.iloc[:, 0].values, d.iloc[:, 1].values, 1)
    return b[0] * x_now + b[1]


def oos_stats(df, halves):
    """df: columns y, m0, <models>. 표본외 R²·RMSE·보정 기울기, 반쪽별."""
    out = {}
    models = [c for c in df.columns if c not in ("y", "m0")]
    for lab, (a, b) in halves.items():
        s = df.loc[a:b].dropna()
        e0 = ((s.y - s.m0) ** 2).sum()
        row = {"n": len(s), "M0_RMSE": round(float(np.sqrt(((s.y - s.m0) ** 2).mean())), 2)}
        for m in models:
            em = ((s.y - s[m]) ** 2).sum()
            slope = np.polyfit(s[m].values, s.y.values, 1)[0] if s[m].std() > 0 else np.nan
            row[m] = {"R2_OOS": round(float(1 - em / e0), 3),
                      "RMSE": round(float(np.sqrt(((s.y - s[m]) ** 2).mean())), 2),
                      "편향(실현−예측)": round(float((s.y - s[m]).mean()), 2),
                      "보정기울기": round(float(slope), 2)}
        out[lab] = row
    return out


# ------------------------------------------------------------------ B1 10년
def b1(p):
    lcape = np.log(p["CAPE_pit"])
    y = p["fwd10_real"]
    xs = 100 * p["xs10_real_sh"]                       # Shiller 는 소수 → %
    ldr = np.log(p["D_real"].shift(3).ffill(limit=3))  # 최근 달 배당 미발표 → 최대 3개월 이어 씀(dp 와 같은 규약)
    dp_pct = 100 * np.exp(p["dp"])
    first = lcape.first_valid_index()
    rows, rows_b = [], []
    origins = pd.period_range("1911-01", "2016-10", freq="M")
    for t in origins:
        known = slice(first, t - 120)
        yrs = (t - P("1871-04", "M")).n / 12
        g = 100 * (np.exp((ldr[t] - ldr[P("1871-04", "M")]) / yrs) - 1)
        rows.append({"t": t, "y": y[t], "m0": y[known].mean(),
                     "M1_logCAPE회귀": fit_pred(lcape[known], y[known], lcape[t]),
                     "M2_1/CAPE": 100 / p["CAPE_pit"][t],
                     "M3_DP+g": dp_pct[t] + g})
        rows_b.append({"t": t, "y": xs[t], "m0": xs[known].mean(),
                       "ECY회귀": fit_pred(p["ecy"][known], xs[known], p["ecy"][t])})
    df = pd.DataFrame(rows).set_index("t")
    dfb = pd.DataFrame(rows_b).set_index("t")
    halves = {"전체 1911~2016": ("1911-01", "2016-10"), "1911~1959": ("1911-01", "1959-12"), "1960~2016": ("1960-01", "2016-10")}
    res = {"B1": oos_stats(df, halves), "B1b": oos_stats(dfb, halves)}
    # 판정
    ok = {}
    for m in ["M1_logCAPE회귀", "M2_1/CAPE", "M3_DP+g"]:
        ok[m] = "유효" if all(res["B1"][h][m]["R2_OOS"] > 0 for h in ("1911~1959", "1960~2016")) else "무효"
    ok_b = "유효" if all(res["B1b"][h]["ECY회귀"]["R2_OOS"] > 0 for h in ("1911~1959", "1960~2016")) else "무효"
    res["판정"] = {"B1": ok, "B1b_ECY": ok_b}
    # 현재(2026-10) 추정 — 실현 확인된 짝(시작 ≤ 2016-10) 전부로 적합
    now = p.index[-1]
    known = slice(first, now - 120)
    yrs = (now - P("1871-04", "M")).n / 12
    g = 100 * (np.exp((ldr[now] - ldr[P("1871-04", "M")]) / yrs) - 1)
    res["현재"] = {"월": str(now), "CAPE_pit": round(p["CAPE_pit"][now], 2),
                 "M0_과거평균": round(y[known].mean(), 2),
                 "M1_logCAPE회귀": round(fit_pred(lcape[known], y[known], lcape[now]), 2),
                 "M2_1/CAPE": round(100 / p["CAPE_pit"][now], 2),
                 "M3_DP+g": round(dp_pct[now] + g, 2), "M3_g": round(g, 2), "D/P%": round(dp_pct[now], 2),
                 "ECY": round(p["ecy"][now], 2),
                 "ECY회귀_10년초과(주식−채권)": round(fit_pred(p["ecy"][known], xs[known], p["ecy"][now]), 2),
                 "초과_과거평균": round(xs[known].mean(), 2)}
    # 같은 CAPE 수준 역사: CAPE_pit ≥ 30 에서 시작한 10년 실질 수익
    hi = pd.DataFrame({"cape": p["CAPE_pit"], "y": y}).dropna()
    res["CAPE구간별_10년실질"] = {
        lab: {"개월": int(len(s)), "중앙": round(s.y.median(), 2), "최저": round(s.y.min(), 2), "최고": round(s.y.max(), 2),
              "음수비율": round(float((s.y < 0).mean()), 2)}
        for lab, s in [("<10", hi[hi.cape < 10]), ("10~15", hi[(hi.cape >= 10) & (hi.cape < 15)]),
                       ("15~20", hi[(hi.cape >= 15) & (hi.cape < 20)]), ("20~25", hi[(hi.cape >= 20) & (hi.cape < 25)]),
                       ("25~30", hi[(hi.cape >= 25) & (hi.cape < 30)]), ("≥30", hi[hi.cape >= 30])]}
    hi30 = hi[hi.cape >= 30]
    res["CAPE30이상_시작월_연도"] = sorted({str(t.year) for t in hi30.index})
    # 보조(사전등록 밖, 설명용): 예측 시점 10년대별 M2 오차와 그 원천 — 실현 = 배수 변화 몫 + 나머지(이익 성장·배당)
    lc = np.log(p["CAPE_pit"])
    df["배수변화_연%"] = 100 * (np.exp((lc.shift(-120) - lc).reindex(df.index) / 10) - 1)
    df["M2오차"] = df["y"] - df["M2_1/CAPE"]
    dec = df.groupby((df.index.year // 10) * 10)
    res["보조_M2_10년대별"] = {str(k): {"예측": round(g["M2_1/CAPE"].mean(), 1), "실현": round(g["y"].mean(), 1),
                                    "오차": round(g["M2오차"].mean(), 1), "배수변화_연%": round(g["배수변화_연%"].mean(), 1)}
                            for k, g in dec}
    res["보조_M2오차와_배수변화_상관"] = round(float(df[["M2오차", "배수변화_연%"]].corr().iloc[0, 1]), 3)
    df.to_csv(os.path.join(HERE, "b1_forecasts.csv"))
    return res


# ------------------------------------------------------------------ B2 1·12개월
SIG = ["dp", "ep10", "ecy", "tbl", "term", "credit", "infl12"]


def b2(p):
    ex = np.log1p(p["mkt"]) - np.log1p(p["rf"])
    out = {}
    for h in (1, 12):
        fwd = sum(ex.shift(-j) for j in range(1, h + 1))     # 월 t 신호 → t+1..t+h
        last = ex.last_valid_index() - h
        origins = pd.period_range("1950-01", last, freq="M")
        start = P("1927-01", "M")
        rows = []
        for t in origins:
            known = slice(start, t - h)
            r = {"t": t, "y": fwd[t], "m0": fwd[known].mean()}
            for s in SIG:
                r[s] = fit_pred(p[s][known], fwd[known], p[s][t])
            r["결합"] = np.nanmean([r[s] for s in SIG])
            rows.append(r)
        df = pd.DataFrame(rows).set_index("t")
        halves = {"전체": ("1950-01", str(last)), "1950~1989": ("1950-01", "1989-12"), "1990~": ("1990-01", str(last))}
        st = oos_stats(df, halves)
        # R²_OOS 만 간추림
        slim = {hl: {m: v["R2_OOS"] for m, v in st[hl].items() if isinstance(v, dict)} for hl in st}
        verdict = {m: ("유효" if slim["1950~1989"][m] > 0 and slim["1990~"][m] > 0 else "무효") for m in SIG + ["결합"]}
        out[f"{h}개월"] = {"R2_OOS": slim, "판정": verdict}
    return out


# ------------------------------------------------------------------ B3 연 1회 기울기
def simulate(mkt, rf, w_by_year):
    """연초 비중 w, 그해 월별 표류, 다음 해 초 재조정. 월 수익 경로 반환."""
    rets = []
    for y, w in w_by_year.items():
        m = mkt[mkt.index.year == y]
        f = rf[rf.index.year == y]
        e, b = w, 1 - w
        prev = 1.0
        for (pm, rm), rr in zip(m.items(), f.values):
            e *= 1 + rm
            b *= 1 + rr
            rets.append((pm, (e + b) / prev - 1))
            prev = e + b
    return pd.Series(dict(rets))


def perf(r, rf):
    v = (1 + r).cumprod()
    yrs = len(r) / 12
    exr = r - rf.reindex(r.index)
    return {"CAGR%": round(100 * (v.iloc[-1] ** (1 / yrs) - 1), 2),
            "변동성%": round(100 * r.std() * np.sqrt(12), 2),
            "샤프": round(float(exr.mean() * 12 / (r.std() * np.sqrt(12))), 3),
            "MDD%": round(100 * float((v / v.cummax() - 1).min()), 1)}


def b3(p):
    cape = p["CAPE_pit"].dropna()
    mkt, rf = p["mkt"].dropna(), p["rf"].dropna()
    w = {}
    for y in range(1927, 2026):
        t = P(f"{y - 1}-12", "M")
        hist = cape.loc[:t]
        pct = float((hist < hist.iloc[-1]).mean())
        w[y] = 1.0 - 0.4 * pct
    w = pd.Series(w)
    out = {"비중_평균": round(w.mean(), 3), "비중_최저": round(w.min(), 3), "비중_최고": round(w.max(), 3)}
    verdict_parts = []
    for lab, (a, b) in {"1927~1975": (1927, 1975), "1976~2025": (1976, 2025), "전체 1927~2025": (1927, 2025)}.items():
        ww = w.loc[a:b]
        avg = float(ww.mean())
        rV = simulate(mkt, rf, ww)
        rS = simulate(mkt, rf, pd.Series(avg, index=ww.index))
        r80 = simulate(mkt, rf, pd.Series(0.8, index=ww.index))
        r100 = simulate(mkt, rf, pd.Series(1.0, index=ww.index))
        pv, ps = perf(rV, rf), perf(rS, rf)
        out[lab] = {"V": pv, f"S-avg({avg:.2f})": ps, "S80": perf(r80, rf), "보유100": perf(r100, rf)}
        if lab != "전체 1927~2025":
            verdict_parts.append(pv["CAGR%"] > ps["CAGR%"] and pv["샤프"] > ps["샤프"])
    out["판정"] = "기울기 실전 후보" if all(verdict_parts) else "기울기 버림"
    now = p["CAPE_pit"].dropna()
    pct_now = float((now < now.iloc[-1]).mean())
    out["현재"] = {"CAPE_pit": round(now.iloc[-1], 2), "백분위": round(pct_now, 3), "V비중": round(1 - 0.4 * pct_now, 3)}
    w.to_csv(os.path.join(HERE, "b3_weights.csv"))
    return out


def main():
    p = panel()
    res = {"B1": b1(p), "B2": b2(p), "B3": b3(p)}
    # 현재 판독 보조
    last = p.iloc[-1]
    res["현재_지표"] = {k: (round(float(last[k]), 3) if pd.notna(last[k]) else None)
                     for k in ["CAPE_pit", "dp", "ecy", "real10", "tips10", "breakeven", "GS10", "credit", "infl12"]}
    with open(os.path.join(HERE, "results_B.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
