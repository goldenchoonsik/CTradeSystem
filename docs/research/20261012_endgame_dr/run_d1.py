"""D1 — 선물·옵션 만기일 효과 (069500 일봉 2002-10 ~). PREREGISTRATION.md 그대로.

  .venv/bin/python docs/research/20261012_endgame_dr/run_d1.py
"""
import os

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DISC_END, VAL_FROM = pd.Timestamp("2015-12-31"), pd.Timestamp("2016-01-01")
COST = 0.0003


def expiry_days(dates):
    """매월 둘째 목요일, 거래일이 아니면 직전 거래일."""
    s = pd.Series(dates)
    out = []
    for (y, m), _ in s.groupby([s.dt.year, s.dt.month]):
        first = pd.Timestamp(y, m, 1)
        thu2 = first + pd.Timedelta(days=(3 - first.weekday()) % 7 + 7)
        prior = s[s <= thu2]
        if len(prior) and prior.iloc[-1].month == m:
            out.append(prior.iloc[-1])
    return pd.DatetimeIndex(out)


def stats(x):
    x = x.dropna()
    if len(x) < 3:
        return dict(n=len(x), mean=np.nan, t=np.nan)
    return dict(n=len(x), mean=x.mean(), t=x.mean() / x.std() * np.sqrt(len(x)))


def main():
    d = pd.read_parquet(os.path.join(REPO, "DataVault/normalized/candles_1d/069500_1d.parquet"))
    d = d.rename(columns={"timestamp": "date"}).sort_values("date").reset_index(drop=True)
    d["date"] = pd.to_datetime(d.date)
    d = d[(d.open > 0) & (d.close > 0)].reset_index(drop=True)
    seg = pd.DataFrame({"date": d.date})
    seg["a"] = d.open.shift(-1) / d.close - 1                 # 그날 종가 → 다음날 시가
    seg["b"] = d.close / d.open - 1                           # 그날 장중
    seg["c"] = d.close.shift(-1) / d.open.shift(-1) - 1       # 다음날 장중
    seg["d"] = d.close / d.close.shift(5) - 1                 # 5거래일 전 종가 → 그날 종가
    exp = expiry_days(d.date)
    seg["exp"] = seg.date.isin(exp)
    seg["qexp"] = seg.exp & seg.date.dt.month.isin([3, 6, 9, 12])

    lines = [f"# D1 만기일 결과 (069500 일봉 {d.date.min():%Y-%m-%d} ~ {d.date.max():%Y-%m-%d}, 만기 {seg.exp.sum()}회·분기 {seg.qexp.sum()}회)", "",
             "방향 = 발견 구간(2002-10~2015-12) 평균 부호. 순수익 = 방향 × 수익 − 0.03%. t 는 거래 단위.", "",
             "| 시험 | 발견 평균(비용 전) | 방향 | 검증 2016~ 순 평균 | 검증 t | 전체 순 t | 연도 양수(전체) | 최근12M 합 | 평소날 같은 구간(검증) | 판정 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    passed = 0
    for col, lab in [("a", "a 만기 오버나잇"), ("b", "b 만기일 장중"), ("c", "c 다음날 장중"), ("d", "d 만기 주간")]:
        for flag, flab in [("qexp", "분기"), ("exp", "월 전체")]:
            ev = seg[seg[flag]].dropna(subset=[col])
            disc = ev[ev.date <= DISC_END][col]
            sign = 1 if disc.mean() > 0 else -1
            net = sign * ev[col] - COST
            net.index = ev.date
            val = net[net.index >= VAL_FROM]
            yr = net.groupby(net.index.year).sum()
            ypos = (yr > 0).mean()
            last12 = val[val.index > val.index.max() - pd.Timedelta(days=365)].sum()
            sv, sa = stats(val), stats(net)
            normal = seg[(~seg.exp) & (seg.date >= VAL_FROM)][col].mean()
            ok = sa["mean"] > 0 and sa["t"] > 3 and ypos >= 2 / 3 and last12 > 0 and sv["mean"] > 0 and sv["t"] > 2
            passed += ok
            lines.append(f"| {lab} · {flab} | {disc.mean()*100:+.3f}% (n{len(disc)}) | {'롱' if sign > 0 else '숏'} | {sv['mean']*100:+.3f}% (n{sv['n']}) | {sv['t']:+.2f} | {sa['t']:+.2f} | {ypos:.0%} | {last12*100:+.2f}% | {normal*100:+.3f}% | {'합격' if ok else '불합격'} |")
    lines += ["", f"합격 {passed} / 8"]
    out = "\n".join(lines)
    print(out)
    with open(os.path.join(os.path.dirname(__file__), "results_d1.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
