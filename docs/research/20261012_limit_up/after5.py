"""상한가 '전날 특징'을 충족한 종목의 이후 5거래일 움직임 (서술 분석, 전략 판정 아님).

- 특징은 analyze_limit_up.features() 와 같은 정의 — 행 D 의 특징 = D-1 장마감까지 알 수 있는 값
- 진입 = D 시가(수정가), 경로 = D·D+1·…·D+4 종가 (5거래일), 최대 상승·하락 = 그 5일 고가·저가
- 비용(참고) = 왕복 0.27% + 2틱(진입 실제가 기준)
- t 는 신호일별 동일가중 후 5거래일 간격으로 솎아 겹침 없이 계산

  .venv/bin/python docs/research/20261012_limit_up/after5.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "20261011_vol_spike"))
import analyze_limit_up as alu  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

HERE = os.path.dirname(__file__)
H = 5
RT = 0.0027


def paths(d):
    d = d.sort_values(["code", "date"]).copy()
    g = d.groupby("code", sort=False)
    d["oa"], d["la"] = d.o * d.f, d.l * d.f
    d["gap"] = d.oa / g.ca.shift(1) - 1
    hi, lo = [], []
    for k in range(H):
        d[f"d{k+1}"] = g.ca.shift(-k) / d.oa - 1
        hi.append(g.ha.shift(-k))
        lo.append(g.la.shift(-k))
        d[f"lu{k}"] = g.lu.shift(-k)
        d[f"ld{k}"] = ((g.ret.shift(-k) <= -alu.LU) & (g.c.shift(-k) <= g.l.shift(-k) * 1.001)).astype(float)
    d["mfe"] = pd.concat(hi, axis=1).max(axis=1, skipna=False) / d.oa - 1
    d["mae"] = pd.concat(lo, axis=1).min(axis=1, skipna=False) / d.oa - 1
    d["any_lu"] = d[[f"lu{k}" for k in range(H)]].max(axis=1)
    d["any_ld"] = d[[f"ld{k}" for k in range(H)]].max(axis=1)
    d["cost"] = RT + 2 * tick_size(d.o.to_numpy()) / d.o.to_numpy()
    d["net5"] = d[f"d{H}"] - d.cost
    return d[d[f"d{H}"].notna() & (d.o > 0)]


def model_top(d, k=20):
    """analyze_limit_up.predictability 와 같은 모델로 2026 시험 구간 매일 상위 k (전날 특징만)."""
    import lightgbm as lgb
    x = d.assign(kosdaq=(d.market == "KOSDAQ").astype(int)).dropna(subset=["r1", "vol20", "val20"])
    tr, te = x[x.date < "2026-01-01"], x[x.date >= "2026-01-01"].copy()
    params = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 7, "num_threads": 4}
    m = lgb.train(params, lgb.Dataset(tr[alu.FEATS], tr.lu), num_boost_round=400)
    te["p"] = m.predict(te[alu.FEATS])
    te["rk"] = te.groupby("date").p.rank(ascending=False, method="first")
    return te.loc[te.rk <= k, ["code", "date"]].assign(top=1)


def summarize(lab, x):
    if len(x) < 30:
        return None
    day = x.groupby("date").net5.mean()
    thin = day.iloc[::H]
    t = thin.mean() / thin.std() * np.sqrt(len(thin)) if len(thin) > 2 else np.nan
    yr = day.groupby(day.index.year).mean()
    return dict(lab=lab, n=len(x), dates=x.date.nunique(), gap=x.gap.median(),
                **{f"d{k}": x[f"d{k}"].median() for k in range(1, H + 1)},
                mean5=x[f"d{H}"].mean(), net_day=day.mean(), t=t, up10=(x[f"d{H}"] >= 0.10).mean(), dn10=(x[f"d{H}"] <= -0.10).mean(),
                win=(x[f"d{H}"] > 0).mean(), lu=x.any_lu.mean(), ld=x.any_ld.mean(), mfe=x.mfe.median(), mae=x.mae.median(),
                y2025=yr.get(2025, np.nan), y2026=yr.get(2026, np.nan))


def main():
    d, uni = alu.features()
    p = paths(d)
    p = p.merge(model_top(d), on=["code", "date"], how="left")
    conds = [
        ("전체(기준)", p.index == p.index),
        ("① 직전 20일 상한가 2회+", p.lu_prev20 >= 2),
        ("② 전일 거래대금 10배+", p.vratio >= 10),
        ("③ 20일 변동성 8%+", p.vol20 >= 0.08),
        ("④ 직전 20일 +50%+", p.r20 >= 0.5),
        ("⑤ 120일 고점 대비 −60% 이하", p.hi120 <= -0.6),
        ("⑥ 주가 1천원 미만", p.p_raw < 1000),
        ("⑦ 시총 300억 미만", p.mcap < 300),
        ("⑧ 전일 −5% 이하", p.r1 <= -0.05),
        ("⑨ 전일 상한가", p.r1 >= alu.LU),
        ("⑩ 거래대금 10배+ & 변동성 8%+", (p.vratio >= 10) & (p.vol20 >= 0.08)),
        ("⑪ 낙폭 −60% & 거래대금 10배+", (p.hi120 <= -0.6) & (p.vratio >= 10)),
        ("⑫ 모델 상위 20 (2026 시험만)", p.top == 1),
        ("   └ 같은 기간 전체(2026 기준)", p.date >= "2026-01-01"),
    ]
    rows = [r for r in (summarize(lab, p[m]) for lab, m in conds) if r]
    s = pd.DataFrame(rows)
    pct = lambda v: f"{v*100:+.1f}%"
    L = [f"# 상한가 '전날 특징' 충족 종목의 이후 5거래일 ({p.date.min():%Y-%m-%d} ~ {p.date.max():%Y-%m-%d})", "",
         "진입 = 신호 다음 날 시가(전날 장마감까지의 특징으로 판단). 누적 수익은 진입가 대비 각 날 종가, 중앙값. 비용 전.", "",
         "## 경로 (중앙값)", "",
         "| 조건 | 건 | 갭(전일 종가→시가) | 1일차 | 2일차 | 3일차 | 4일차 | 5일차 | 5일 평균 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in s.itertuples():
        L.append(f"| {r.lab} | {r.n:,} | {pct(r.gap)} | {pct(r.d1)} | {pct(r.d2)} | {pct(r.d3)} | {pct(r.d4)} | {pct(r.d5)} | {pct(r.mean5)} |")
    L += ["", "## 5일 안의 분포", "",
          "| 조건 | 5일 뒤 플러스 | +10% 이상 | −10% 이하 | 5일 안 상한가 | 5일 안 하한가 | 최대 상승(중앙) | 최대 하락(중앙) |", "|---|---|---|---|---|---|---|---|"]
    for r in s.itertuples():
        L.append(f"| {r.lab} | {r.win:.0%} | {r.up10:.0%} | {r.dn10:.0%} | {r.lu:.1%} | {r.ld:.1%} | {pct(r.mfe)} | {pct(r.mae)} |")
    L += ["", "## 비용 후 (신호일별 동일가중, 겹침 없이 t)", "",
          "| 조건 | 신호일 | 5일 비용 후 평균 | t | 2025 | 2026 |", "|---|---|---|---|---|---|"]
    for r in s.itertuples():
        L.append(f"| {r.lab} | {r.dates} | {pct(r.net_day)} | {r.t:+.2f} | {pct(r.y2025)} | {pct(r.y2026)} |")
    L += ["", "주의: 현재 상장 종목만(상장폐지 종목 없음 → 하락 쪽 과소), 같은 종목이 며칠 연속 조건을 충족하면 겹쳐 셈(경로 중앙값은 그대로, t 는 솎아서 계산).", ""]
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_after5.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
