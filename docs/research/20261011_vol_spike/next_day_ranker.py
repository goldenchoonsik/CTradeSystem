"""'내일 오를 확률이 가장 높은 종목' 맞추기 — 횡단면 일간 순위 모델 (탐색, 개발 구간만).

- 대상: 전 종목(우선주·스팩 제외), 전일 실제 종가 ≥ 1,000원, 직전 20일 평균 거래대금 ≥ 5억
- 신호: D 종가까지의 일봉 특징 → 진입 D+1 시가, 청산 D+2 시가 (하루 보유)
- 모델: LightGBM 이진 분류(오를까), 매월 재학습·전진 검증(학습=과거 전부, 시험=다음 1개월)
- 비교: 단순 규칙(1일·5일 반전, 20일 모멘텀)
- 비용: 매일 전량 교체 → 왕복 0.27% + 2틱(실제 가격)

  .venv/bin/python docs/research/20261011_vol_spike/next_day_ranker.py
"""
import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from compare_algos import load_daily  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

STAGE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "DataVault", "staging", "vol_spike_universe")
DEV_END = pd.Timestamp("2026-05-29")
TEST_FROM = pd.Timestamp("2025-06-01")
TOPK = 20
H = int(os.environ.get("HOLD_DAYS", "1"))      # 보유 일수 (1 = 다음날 하루, 5 = 1주)


def features(d):
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv")).set_index("code")
    d = d.sort_values(["code", "date"]).copy()
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
    # 라벨: D+1 시가 → D+2 시가
    o1, o2 = g["o"].shift(-1), g["o"].shift(-1 - H)
    d["fwd"] = o2 / o1 - 1
    d["entry_raw"] = g["raw_close"].shift(0) * (o1 / d.c)       # D+1 시가의 실제 가격 근사
    d["y"] = (d.fwd > 0).astype(float).where(d.fwd.notna())
    ok = (d.raw_close >= 1000) & (d.val20 >= 5e8) & (d.v > 0) & d.fwd.notna() & (d.fwd.abs() < 0.35)
    keep = ["date", "code", "fwd", "y", "entry_raw"] + FEATS
    out = d.loc[ok, keep].copy()
    for c in FEATS + ["fwd"]:
        out[c] = out[c].astype("float32")
    return out.reset_index(drop=True)


FEATS = ["r1", "r5", "r20", "r60", "gap", "intra", "clv", "vol20", "vratio", "hi20", "lo20", "logval", "kosdaq", "mkt_r1", "rank_r1", "rank_r5"]


def daily_pick(df, score, k=TOPK, ascending=False):
    x = df.assign(s=score).dropna(subset=["s"])
    top = x.sort_values("s", ascending=ascending).groupby("date").head(k)
    cost = 0.0027 + 2 * tick_size(top.entry_raw.to_numpy()) / top.entry_raw.to_numpy()
    top = top.assign(net=top.fwd - cost)
    day = top.groupby("date").agg(gross=("fwd", "mean"), net=("net", "mean"), hit=("y", "mean"))
    return day


def report(name, day, base):
    t = day.net.mean() / day.net.std() * np.sqrt(len(day))
    print(f"{name:30s} 상승 적중 {day.hit.mean():.1%} (전체 {base:.1%}) | 일 비용 전 {day.gross.mean()*100:+.3f}% → 후 {day.net.mean()*100:+.3f}% (t{t:+.2f})")


def main():
    d = features(load_daily())
    d = d[d.date <= DEV_END]
    test = d[d.date >= TEST_FROM]
    base = test.y.mean()
    print(f"표본 {d.date.min():%Y-%m-%d}~{d.date.max():%Y-%m-%d}, 시험 구간 {TEST_FROM:%Y-%m-%d}~ ({test.date.nunique()}일, 하루 평균 {test.groupby('date').size().mean():.0f}종목)")
    print(f"시험 구간 '다음날 상승' 기본 비율 {base:.1%}, 전체 평균 하루 수익 {test.fwd.mean()*100:+.3f}%\n")

    preds = []
    for m0 in pd.date_range(TEST_FROM, DEV_END, freq="MS"):
        m1 = m0 + pd.offsets.MonthBegin(1)
        tr = d[d.date < m0 - pd.Timedelta(days=3 + 2 * H)]          # 라벨이 D+1+H 시가까지 → 경계 여유
        te = d[(d.date >= m0) & (d.date < m1)]
        if te.empty:
            continue
        params = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 200,
                  "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8, "verbose": -1, "seed": 7, "num_threads": 2}
        model = lgb.train(params, lgb.Dataset(tr[FEATS], tr.y), num_boost_round=300)
        preds.append(pd.Series(model.predict(te[FEATS]), index=te.index))
    p = pd.concat(preds)
    test = test.loc[p.index]
    if H > 1:                                                         # 겹치지 않게 H 거래일마다 리밸런싱
        days = sorted(test.date.unique())[::H]
        keep = test.date.isin(days)
        test, p = test[keep], p[keep]
    # 순위 상관(일별) 과 AUC 근사
    rk = test.assign(p=p)
    rk["rp"], rk["rf"] = rk.groupby("date").p.rank(), rk.groupby("date").fwd.rank()
    ic = rk.groupby("date")[["rp", "rf"]].corr().unstack().iloc[:, 1]
    print(f"모델 일별 순위상관(IC) 평균 {ic.mean():+.4f} (t{ic.mean()/ic.std()*np.sqrt(len(ic)):+.2f}, 양수인 날 {(ic>0).mean():.0%})")
    report(f"모델 상위 {TOPK}", daily_pick(test, p), base)
    report(f"모델 하위 {TOPK} (참고)", daily_pick(test, p, ascending=True), base)
    report("규칙: 1일 낙폭 최대(반전)", daily_pick(test, test.r1, ascending=True), base)
    report("규칙: 5일 낙폭 최대(반전)", daily_pick(test, test.r5, ascending=True), base)
    report("규칙: 20일 모멘텀 상위", daily_pick(test, test.r20), base)
    report("무작위 기준(전 종목 평균)", test.groupby("date").agg(gross=("fwd", "mean"), net=("fwd", lambda s: s.mean() - 0.0027 - 0.002), hit=("y", "mean")), base)
    # 분위별: 모델 점수 10분위의 평균 수익(비용 전)
    dec = test.assign(p=p, q=test.assign(p=p).groupby("date").p.rank(pct=True).mul(10).clip(upper=9.999).astype(int))
    print("\n모델 점수 10분위별 하루 평균 수익(비용 전, %):", dec.groupby("q").fwd.mean().mul(100).round(3).to_dict())


if __name__ == "__main__":
    main()
