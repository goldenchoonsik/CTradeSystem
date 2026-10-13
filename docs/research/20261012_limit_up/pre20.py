"""상한가 전 20거래일(약 한 달)의 주가·거래대금·수급 흐름 (서술 분석).

비교 대상
- 대조군: 같은 날, 같은 시장, 전날 기준 20일 변동성 5분위·시가총액 5분위가 같고 그날 상한가가 아닌 종목-일
  (상한가 1건마다 그 칸의 대조군 평균을 하나로 묶어 → 상한가와 같은 가중)
- 전체: 모든 종목-일 평균
경로 기준점 = D-21 종가(한 달 전). 거래대금 배수 = 그날 거래대금 ÷ D-80~D-21 중앙값(한 달 이전 평소).

  .venv/bin/python docs/research/20261012_limit_up/pre20.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "20261012_endgame_e"))
import analyze_limit_up as alu  # noqa: E402
import run_e  # noqa: E402

K = 20


def build():
    d, uni = alu.features()
    d = d.merge(run_e.load_flows(), on=["date", "code"], how="left").sort_values(["code", "date"]).reset_index(drop=True)
    g = d.groupby("code", sort=False)
    base_val = g.val.transform(lambda s: s.shift(K + 1).rolling(60, min_periods=30).median())
    c21 = g.ca.shift(K + 1)
    for k in range(1, K + 1):
        d[f"c{k}"] = g.ca.shift(k) / c21 - 1                     # 한 달 전 대비 누적
        d[f"r{k}_"] = g.ret.shift(k)
        d[f"v{k}"] = g.val.shift(k) / base_val
    for c in ("prsn", "frgn", "orgn", "prog"):
        num = g[c].transform(lambda s: s.shift(1).rolling(K, min_periods=15).sum())
        den = g.val.transform(lambda s: s.shift(1).rolling(K, min_periods=15).sum())
        d[f"f20_{c}"] = num / den
    # 이후: D+1 시가 → D+5 종가 (상한가 다음 날 진입)
    d["oa"] = d.o * d.f
    d["after5"] = g.ca.shift(-5) / g.oa.shift(-1) - 1
    d["after20"] = g.ca.shift(-20) / g.oa.shift(-1) - 1
    d = d[d.c1.notna() & d.v1.notna() & d.vol20.notna() & d.mcap.notna()]
    d["vq"] = d.groupby("date").vol20.transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    d["mq"] = d.groupby("date").mcap.transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    return d


def matched_controls(d, cols):
    """상한가 1건마다 같은 (날짜, 시장, 변동성 5분위, 시총 5분위) 칸의 비상한가 평균 → 상한가 가중 평균."""
    key = ["date", "market", "vq", "mq"]
    ctrl = d[d.lu == 0].groupby(key)[cols].mean()
    ev = d[d.lu == 1][key]
    m = ev.join(ctrl, on=key)
    return m[cols].mean(), m[cols].notna().all(axis=1).mean()


def pct(v):
    return f"{v*100:+.1f}%"


def main():
    d = build()
    ev = d[d.lu == 1]
    first = ev[ev.lu_prev20 == 0]
    cols = [f"c{k}" for k in range(1, K + 1)] + [f"v{k}" for k in range(1, K + 1)] + [f"r{k}_" for k in range(1, K + 1)]
    ctrl, cover = matched_controls(d, cols)
    allm = d[cols].mean()
    L = [f"# 상한가 전 20거래일 흐름 ({d.date.min():%Y-%m-%d} ~ {d.date.max():%Y-%m-%d})", "",
         f"- 상한가 {len(ev):,}건 (그중 직전 20일 상한가 없던 '첫 상한가' {len(first):,}건). 대조군 매칭률 {cover:.0%}",
         "- 대조군 = 같은 날·같은 시장·같은 변동성 5분위·같은 시총 5분위인데 상한가가 아닌 종목 — '원래 변동성 큰 소형주라서'를 걷어낸 비교",
         "- 누적 = 한 달 전(D-21) 종가 대비, 거래대금 배수 = 한 달 이전 평소(D-80~D-21 중앙값) 대비. 평균값", ""]
    L += ["## 날짜별 경로 (D-20 → D-1)", "",
          "| 날 | 상한가 누적 | 첫 상한가 누적 | 대조군 누적 | 상한가 그날 수익 | 대조군 그날 | 상한가 거래대금 배수 | 첫 상한가 배수 | 대조군 배수 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k in range(K, 0, -1):
        L.append(f"| D-{k} | {pct(ev[f'c{k}'].mean())} | {pct(first[f'c{k}'].mean())} | {pct(ctrl[f'c{k}'])} | {pct(ev[f'r{k}_'].mean())} | {pct(ctrl[f'r{k}_'])} | "
                 f"{ev[f'v{k}'].median():.2f}× | {first[f'v{k}'].median():.2f}× | {d[d.lu == 0][f'v{k}'].median():.2f}× |")
    L += ["", "(거래대금 배수는 중앙값 — 대조군 열은 전체 비상한가 중앙값)", ""]

    # 한 달 모양 분류 (D-21 → D-1 누적과 그 사이 최고·최저)
    def shape(x):
        c = x.c1
        mx = x[[f"c{k}" for k in range(1, K + 1)]].max(axis=1)
        mn = x[[f"c{k}" for k in range(1, K + 1)]].min(axis=1)
        return np.select([c >= 0.30, (mx >= 0.30) & (c < 0.15), c <= -0.15, (c.abs() < 0.07) & (mx < 0.10) & (mn > -0.10)],
                         ["① 한 달 +30% 이상 이미 급등", "② 급등 후 되밀림(고점 +30%↑, 지금 +15% 미만)", "③ 한 달 −15% 이하 하락",
                          "④ 조용한 횡보(±7%, 고저 ±10% 안)"], default="⑤ 그 외(완만한 등락)")
    d["shape"] = shape(d)
    ev = d[d.lu == 1]
    base = d.lu.mean()
    L += ["## 한 달 모양별 — 상한가 비중·확률·이후", "",
          "| 모양 | 상한가 중 비중 | 전체 종목-일 중 비중 | 상한가 확률 배수 | 첫 상한가 비중 | 이후 5일(다음날 시가 진입) 중앙 | 이후 20일 중앙 |", "|---|---|---|---|---|---|---|"]
    for s, x in d.groupby("shape"):
        e = x[x.lu == 1]
        L.append(f"| {s} | {len(e)/len(ev):.0%} | {len(x)/len(d):.0%} | {x.lu.mean()/base:.1f}× | {(e.lu_prev20 == 0).mean():.0%} | "
                 f"{pct(e.after5.median())} | {pct(e.after20.median())} |")
    # 직전 5일 특징
    last5_v = ev[[f"v{k}" for k in range(1, 6)]].median(axis=1)
    L += ["", "## 직전 5일 신호", "",
          f"- 직전 5일 거래대금 배수(5일 중앙값) ≥ 3배: 상한가 {(last5_v >= 3).mean():.0%} vs 전체(비상한가) {(d[d.lu == 0][[f'v{k}' for k in range(1, 6)]].median(axis=1) >= 3).mean():.0%}",
          f"- D-1 거래대금 ≥ 5배: 상한가 {(ev.v1 >= 5).mean():.0%} vs 전체 {(d[d.lu == 0].v1 >= 5).mean():.0%}",
          f"- D-1 수익률 ≤ −5%(전날 급락): 상한가 {(ev.r1_ <= -0.05).mean():.0%} vs 전체 {(d[d.lu == 0].r1_ <= -0.05).mean():.0%} · ≥ +10%: {(ev.r1_ >= 0.10).mean():.0%} vs {(d[d.lu == 0].r1_ >= 0.10).mean():.0%}",
          f"- 직전 5일 모두 거래대금 평소 이하(조용하다가 당일 폭발): 상한가 {(ev[[f'v{k}' for k in range(1, 6)]].max(axis=1) <= 1).mean():.0%} vs 전체 {(d[d.lu == 0][[f'v{k}' for k in range(1, 6)]].max(axis=1) <= 1).mean():.0%}", ""]
    # 수급
    fl = ["f20_prsn", "f20_frgn", "f20_orgn", "f20_prog"]
    fc, _ = matched_controls(d.dropna(subset=fl), fl)
    L += ["## 직전 20일 누적 순매수 (20일 거래대금 대비, 평균)", "", "| 주체 | 상한가 | 첫 상한가 | 대조군 |", "|---|---|---|---|"]
    for c, lab in zip(fl, ["개인", "외국인", "기관", "프로그램"]):
        L.append(f"| {lab} | {ev[c].mean()*100:+.2f}% | {ev[ev.lu_prev20 == 0][c].mean()*100:+.2f}% | {fc[c]*100:+.2f}% |")
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_pre20.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
