"""R — 순위 모델 회피 필터 (PREREGISTRATION.md 그대로). 개발 합격 규칙만 봉인 구간을 1회 연다.

모델·특징·학습 방식은 next_day_ranker.py(H=5) 그대로. 평가 대상은 '미래 수익 ±35% 제외'를 쓰지 않는다
(원 스크립트의 학습 라벨 정리는 미래참조라 포트폴리오 평가에는 쓰면 안 됨) — 학습에는 원 정의 그대로 쓴다.

  .venv/bin/python docs/research/20261012_endgame_dr/run_r.py
"""
import os
import sys

os.environ["HOLD_DAYS"] = "5"
import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "docs", "research", "20261011_vol_spike"))
import compare_algos  # noqa: E402
import next_day_ranker as ndr  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

H = 5
TEST_FROM, DEV_END = pd.Timestamp("2025-06-01"), pd.Timestamp("2026-05-29")
SEAL_FROM, SEAL_END = pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-08")
RT, AVOID = 0.0027, 0.20
PARAMS = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 200,
          "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8, "verbose": -1, "seed": 7, "num_threads": 4}


def eval_frame(d):
    """원 특징과 같은 계산, 대상 필터에서 미래 수익 조건만 뺀다."""
    d = d.sort_values(["code", "date"]).copy()
    g = d.groupby("code", sort=False)
    d["val20"] = g["val"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    o1, o2 = g["o"].shift(-1), g["o"].shift(-1 - H)
    d["fwd"] = o2 / o1 - 1
    d["entry_raw"] = d.raw_close * (o1 / d.c)
    ok = (d.raw_close >= 1000) & (d.val20 >= 5e8) & (d.v > 0)
    return d.loc[ok, ["date", "code", "fwd", "entry_raw"]].reset_index(drop=True)


def turnover_cost(w_prev, w, price):
    dw = w.sub(w_prev, fill_value=0).abs()
    per = RT / 2 + tick_size(price.reindex(dw.index).fillna(1e9).to_numpy()) / price.reindex(dw.index).fillna(1e9).to_numpy()
    return float((dw * per).sum())


def weekly(test, member_fn):
    rows, wb_prev, ws_prev = [], pd.Series(dtype=float), pd.Series(dtype=float)
    for day, x in test.groupby("date"):
        x = x.set_index("code")
        base = member_fn(day, x)
        base = base[x.loc[base, "fwd"].notna()]                # 거래정지 등으로 청산가 없는 종목은 양쪽 모두 제외
        if len(base) < 10:
            continue
        cut = x.loc[base, "pct"] > AVOID
        strat = base[cut.to_numpy()]
        wb = pd.Series(1 / len(base), index=base)
        ws = pd.Series(1 / len(strat), index=strat)
        price = x.entry_raw
        cb, cs = turnover_cost(wb_prev, wb, price), turnover_cost(ws_prev, ws, price)
        rb, rs = x.loc[base, "fwd"].mean(), x.loc[strat, "fwd"].mean()
        rows.append(dict(date=day, bench=rb - cb, strat=rs - cs, diff=(rs - cs) - (rb - cb), gross_diff=rs - rb,
                         n=len(base), excluded=len(base) - len(strat)))
        wb_prev, ws_prev = wb, ws
    return pd.DataFrame(rows).set_index("date")


def summarize(w):
    t = w["diff"].mean() / w["diff"].std() * np.sqrt(len(w))
    q = w["diff"].groupby(w.index.to_period("Q")).sum()
    last12 = w.loc[w.index > w.index.max() - pd.Timedelta(days=365), "diff"].sum()
    return t, (q > 0).mean(), last12


def main():
    compare_algos.END = SEAL_END
    raw = compare_algos.load_daily()
    train = ndr.features(raw)                                       # 원 정의(학습용)
    ev = eval_frame(raw)
    # 평가 행의 특징: 원 features 는 fwd 조건으로 일부 행을 지우므로 같은 수식으로 다시 계산
    allfeat = _all_features(raw)
    ev = ev.merge(allfeat, on=["date", "code"], how="inner")

    preds = []
    for m0 in pd.date_range(TEST_FROM, SEAL_END, freq="MS"):
        m1 = m0 + pd.offsets.MonthBegin(1)
        tr = train[train.date < m0 - pd.Timedelta(days=3 + 2 * H)]
        te = ev[(ev.date >= m0) & (ev.date < m1)]
        if te.empty:
            continue
        model = lgb.train(PARAMS, lgb.Dataset(tr[ndr.FEATS], tr.y), num_boost_round=300)
        preds.append(pd.Series(model.predict(te[ndr.FEATS]), index=te.index))
    ev = ev.loc[pd.concat(preds).index].assign(p=pd.concat(preds))
    ev["pct"] = ev.groupby("date").p.rank(pct=True)
    days = sorted(ev.date.unique())
    reb = days[::H]
    ev = ev[ev.date.isin(reb)]

    val = raw.pivot(index="date", columns="code", values="val")
    top = val.rolling(60, min_periods=40).median().shift(1).rank(axis=1, ascending=False) <= 30
    rules = {
        "R1 전 종목 하위20% 회피": lambda day, x: x.index,
        "R2 대형주(거래대금 상위30) 하위20% 회피": lambda day, x: x.index[top.loc[day].reindex(x.index).fillna(False).to_numpy()],
    }
    lines = ["# R 순위 모델 회피 필터 결과 (H=5, 주간 리밸런싱, 개발 시험 2025-06 ~ 2026-05)", "",
             "판정량 = 주별 (전략 − 기준), 각자 회전 비용 차감. 기준 = 같은 대상 동일가중.", "",
             "| 규칙 | 주 | 기준 주평균 | 전략 주평균 | 차이(비용 전) | 차이(비용 후) | t | 분기 양수 | 최근12M 합 | 봉인 | 판정 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    passed = 0
    for lab, fn in rules.items():
        w = weekly(ev[ev.date <= DEV_END], fn)
        t, qpos, last12 = summarize(w)
        ok_dev = w["diff"].mean() > 0 and t > 3 and qpos >= 2 / 3 and last12 > 0
        seal_txt, ok = "열지 않음", False
        if ok_dev:
            s = weekly(ev[(ev.date >= SEAL_FROM) & (ev.date <= SEAL_END)].dropna(subset=["fwd"]), fn)
            st = s["diff"].mean() / s["diff"].std() * np.sqrt(len(s))
            ok = s["diff"].mean() > 0 and st > 2
            seal_txt = f"{s['diff'].mean()*100:+.3f}%/주 t{st:+.2f} ({len(s)}주, 기준 {s.bench.mean()*100:+.2f}% 전략 {s.strat.mean()*100:+.2f}%)"
        passed += ok
        lines.append(f"| {lab} | {len(w)} | {w.bench.mean()*100:+.3f}% | {w.strat.mean()*100:+.3f}% | {w.gross_diff.mean()*100:+.3f}% | "
                     f"{w['diff'].mean()*100:+.3f}% | {t:+.2f} | {qpos:.0%} | {last12*100:+.2f}% | {seal_txt} | {'합격' if ok else '불합격'} |")
        w.to_csv(os.path.join(os.path.dirname(__file__), f"r_weekly_{lab.split()[0]}.csv"))
    # 진단: 점수 10분위별 주 수익 — 원 스크립트의 '미래 수익 ±35% 제외'(미래참조) 유무 비교
    dv = ev[ev.date <= DEV_END].dropna(subset=["fwd"])
    dv = dv.assign(q=(dv.pct * 10).clip(upper=9.999).astype(int))
    nof = dv.groupby("q").fwd.mean().mul(100)
    flt = dv[dv.fwd.abs() < 0.35].groupby("q").fwd.mean().mul(100)
    share = dv.assign(big=dv.fwd.abs() >= 0.35).groupby("q").big.mean().mul(100)
    lines += ["", "진단 — 점수 10분위별 주 평균 수익(비용 전, %, 개발): 필터 없음 / 원 스크립트처럼 |수익|≥35% 제외 / 제외 비율",
              "| 분위 | " + " | ".join(str(i) for i in nof.index) + " |", "|---" * (len(nof) + 1) + "|",
              "| 필터 없음 | " + " | ".join(f"{v:+.2f}" for v in nof) + " |",
              "| ±35% 제외 | " + " | ".join(f"{v:+.2f}" for v in flt) + " |",
              "| 제외 비율 | " + " | ".join(f"{v:.2f}" for v in share) + " |"]
    lines += ["", f"합격 {passed} / 2"]
    out = "\n".join(lines)
    print(out)
    with open(os.path.join(os.path.dirname(__file__), "results_r.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


def _all_features(raw):
    """ndr.features 와 같은 특징 계산(대상·라벨 필터 없이)."""
    d = raw.copy()
    uni = pd.read_csv(os.path.join(ndr.STAGE, "universe.csv")).set_index("code")
    d = d.sort_values(["code", "date"])
    g = d.groupby("code", sort=False)
    pc = g["c"].shift(1)
    d["r1"] = d.c / pc - 1
    for n in (5, 20, 60):
        d[f"r{n}"] = d.c / g["c"].shift(n) - 1
    d["gap"] = d.o / pc - 1
    d["intra"] = d.c / d.o - 1
    d["clv"] = (d.c - d.l) / (d.h - d.l).replace(0, np.nan)
    d["vol20"] = g["r1"].transform(lambda s: s.rolling(20, min_periods=15).std())
    d["val20"] = g["val"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    d["vratio"] = d.val / g["val"].transform(lambda s: s.rolling(60, min_periods=40).median())
    d["hi20"] = d.c / g["h"].transform(lambda s: s.rolling(20, min_periods=15).max()) - 1
    d["lo20"] = d.c / g["l"].transform(lambda s: s.rolling(20, min_periods=15).min()) - 1
    d["logval"] = np.log1p(d.val20)
    d["kosdaq"] = (d.code.map(uni.market) == "KOSDAQ").astype(int)
    d["mkt_r1"] = d.groupby("date")["r1"].transform("median")
    d["rank_r1"] = d.groupby("date")["r1"].rank(pct=True)
    d["rank_r5"] = d.groupby("date")["r5"].rank(pct=True)
    return d[["date", "code"] + ndr.FEATS]


if __name__ == "__main__":
    main()
