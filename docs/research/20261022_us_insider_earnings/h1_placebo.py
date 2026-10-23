"""H1 생존 편향 대조: 같은 회사, 임원 매수가 없던 무작위 날짜(가짜 사건)의 초과수익.

생존 편향(지금 살아 있는 회사만)이 효과를 만든 거라면 가짜 사건도 같은 크기로 양(+)이어야 한다.
가짜 날짜: 그 종목이 거래가능했던 날 중 실제 매수 신고 ±120거래일 밖, 사건당 5개.
타이밍 효과 = 실제 − 가짜, 신뢰구간은 회사 단위 부트스트랩 1,000회.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_insider import S, excess, COST

rng = np.random.default_rng(20261022)
H = 60


def main():
    R, C, V, X, t3 = excess()
    Xv, cols = X.values, {s: i for i, s in enumerate(R.columns)}
    okv = ((C >= 2) & (V >= 1e6)).values
    n = len(R.index)
    ev = pd.read_parquet(S / "h1_events.parquet")
    ev["ci"] = ev.sym.map(cols)
    cs = np.vstack([np.zeros((1, Xv.shape[1])), np.nancumsum(Xv, axis=0)])   # 누적합으로 구간 합 빠르게

    def car(e, c):
        e, c = np.asarray(e), np.asarray(c)
        out = cs[np.minimum(e + 1 + H, n), c] - cs[np.minimum(e + 1, n), c]
        return np.where(e + H < n, out, np.nan)

    pl = []
    for c, g in ev.groupby("ci"):
        cand = np.flatnonzero(okv[:n - H - 1, c])
        bad = np.zeros(n, bool)
        for e in g.e.values:
            bad[max(0, e - 120):e + 121] = True
        cand = cand[~bad[cand]]
        if len(cand) == 0:
            continue
        for _, r in g.iterrows():
            pick = rng.choice(cand, 5)
            pl.append(pd.DataFrame({"ci": c, "e": pick, "src": r.name}))
    pl = pd.concat(pl, ignore_index=True)
    pl["car"] = car(pl.e.values, pl.ci.values)
    pl["size"] = t3.values[pl.e.values, pl.ci.values]
    ev["car"] = car(ev.e.values, ev.ci.values)
    ev["pcar"] = pl.groupby("src").car.mean()          # 각 실제 사건의 가짜 5개 평균(같은 회사, 칸은 실제 사건 것)
    ev["d"] = ev.car - ev.pcar
    ev = ev.dropna(subset=["car", "pcar"])

    groups = {"전체": ev.index == ev.index, "기회주의": ev.opp.values, "몰아서": ev.cluster.values, "$10만 이상": (ev.value >= 1e5).values}
    rows = []
    for per, (a, b) in {"2006~2018": (2006, 2018), "2019~2026": (2019, 2026)}.items():
        for gname, gm in groups.items():
            for sz, lab in [(None, "전체"), (0, "소"), (1, "중"), (2, "대")]:
                d = ev[gm & ev.year.between(a, b).values & (ev["size"].values == sz if sz is not None else True)]
                g = d.groupby("ci").d
                fs, fc = g.sum().values, g.count().values
                idx = rng.integers(0, len(fs), (1000, len(fs)))
                boots = fs[idx].sum(1) / fc[idx].sum(1)
                lo, hi = np.percentile(boots, [2.5, 97.5])
                rows.append(dict(기간=per, 묶음=gname, 크기=lab, 실제=d.car.mean() * 100, 가짜=d.pcar.mean() * 100,
                                 타이밍=d.d.mean() * 100, CI하=lo * 100, CI상=hi * 100, 중앙=d.d.median() * 100,
                                 비용=d["size"].map(COST).mean() * 100, 사건=len(d), 회사=len(fs)))
    res = pd.DataFrame(rows).round(2)
    res.to_csv(Path(__file__).resolve().parent / "h1_placebo.csv", index=False)
    pd.set_option("display.width", 200)
    print(res.to_string(index=False))

    # 연도별 일관성: 타이밍 효과(실제 − 가짜)
    for name, m in {"기회주의·소형": ev.opp & (ev["size"] == 0), "몰아서·소형": ev.cluster & (ev["size"] == 0), "전체·소형": ev["size"] == 0}.items():
        y = ev[m].groupby("year").d.agg(["mean", "count"])
        print(f"\n{name} 연도별 타이밍(%): 양(+) {int((y['mean'] > 0).sum())}/{len(y)}년 ·",
              ", ".join(f"{k}:{v * 100:.1f}" for k, v in y["mean"].items()))

if __name__ == "__main__":
    main()
