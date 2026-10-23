"""공통 사건 평가: 사건(종목·정보일·묶음 표시) → 60일 초과수익, 같은 회사 무작위 날짜 대조, 달력 시간 t, 실제 포트폴리오 vs QQQ.

h1_insider/h1_placebo/h1_vs_qqq 와 같은 규칙:
- 진입 = 정보일 다음 첫 거래일 종가, 그날 무수정 종가 ≥ $2·60일 평균 거래대금 ≥ $100만.
- 초과 = 같은 날 같은 거래대금 5분위 동일가중 대비. 같은 종목 20거래일 안 재사건은 첫 사건만.
- 가짜 = 같은 회사, 거래가능일 중 실제 사건 ±120거래일 밖 5개. 타이밍 = 실제 − 가짜, CI = 회사 부트스트랩 1,000회.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_insider import excess, nw_t, COST
from panel import load

PERIODS = {"2006~2018": (2006, 2018), "2019~2026": (2019, 2026)}
SIZES = [(None, "전체"), (0, "소"), (1, "중"), (2, "대")]


class Evaluator:
    def __init__(self, H=60):
        self.H = H
        self.R, self.C, self.V, self.X, self.t3 = excess()
        self.dates = self.R.index
        self.cols = {s: i for i, s in enumerate(self.R.columns)}
        self.cs = np.vstack([np.zeros((1, self.X.shape[1])), np.nancumsum(self.X.values, axis=0)])
        self.ok = ((self.C >= 2) & (self.V >= 1e6)).values
        self.qqq = load("QQQ")[1]
        self.rng = np.random.default_rng(20261022)

    def car(self, e, c, H=None):
        H, n = H or self.H, len(self.dates)
        e, c = np.asarray(e), np.asarray(c)
        out = self.cs[np.minimum(e + 1 + H, n), c] - self.cs[np.minimum(e + 1, n), c]
        return np.where(e + H < n, out, np.nan)

    def prepare(self, ev):
        """ev: sym, info_date + 묶음 bool 열 → 진입 e·크기·CAR·가짜 CAR 붙인 표."""
        ev = ev[ev.sym.isin(self.cols)].copy()
        ev["ci"] = ev.sym.map(self.cols)
        ev["e"] = self.dates.searchsorted(ev.info_date.values, side="right")
        ev = ev[ev.e < len(self.dates)]
        ev = ev[self.ok[ev.e.values, ev.ci.values]].sort_values(["ci", "e"])
        keep, last = [], {}
        for i, c, e in zip(ev.index, ev.ci.values, ev.e.values):
            if c in last and e - last[c] < 20:
                continue
            keep.append(i); last[c] = e
        ev = ev.loc[keep].copy()
        ev["size"] = self.t3.values[ev.e.values, ev.ci.values].astype(int)
        ev["year"] = self.dates[ev.e.values].year
        for H in (20, 60, 120):
            ev[f"car{H}"] = self.car(ev.e.values, ev.ci.values, H)
        n = len(self.dates)
        pcar = {}
        for c, g in ev.groupby("ci"):
            cand = np.flatnonzero(self.ok[:n - self.H - 1, c])
            bad = np.zeros(n, bool)
            for e in g.e.values:
                bad[max(0, e - 120):e + 121] = True
            cand = cand[~bad[cand]]
            if len(cand):
                for i in g.index:
                    pcar[i] = np.nanmean(self.car(self.rng.choice(cand, 5), np.full(5, c)))
        ev["pcar"] = pd.Series(pcar)
        ev["d"] = ev[f"car{self.H}"] - ev.pcar
        return ev

    def table(self, ev, groups):
        Xv, rows = self.X.values, []
        for per, (a, b) in PERIODS.items():
            for gname in groups:
                for sz, lab in SIZES:
                    d = ev[ev[gname] & ev.year.between(a, b) & ((ev["size"] == sz) if sz is not None else True)]
                    dd = d.dropna(subset=["d"])
                    row = dict(기간=per, 묶음=gname, 크기=lab, 사건=len(d), 회사=d.ci.nunique())
                    if len(dd) >= 20:
                        g = dd.groupby("ci").d
                        fs, fc = g.sum().values, g.count().values
                        idx = self.rng.integers(0, len(fs), (1000, len(fs)))
                        lo, hi = np.percentile(fs[idx].sum(1) / fc[idx].sum(1), [2.5, 97.5])
                        hold = np.zeros(Xv.shape, bool)
                        for c, e in zip(d.ci.values, d.e.values):
                            hold[e + 1:e + 1 + self.H, c] = True
                        with np.errstate(all="ignore"):
                            daily = np.where(hold.sum(1) >= 5, np.nanmean(np.where(hold, Xv, np.nan), axis=1), np.nan)
                        mon = pd.Series(daily, index=self.dates).loc[str(a):str(b)].resample("ME").sum(min_count=5)
                        row.update(실제=dd[f"car{self.H}"].mean() * 100, 중앙=dd[f"car{self.H}"].median() * 100, 가짜=dd.pcar.mean() * 100,
                                   타이밍=dd.d.mean() * 100, CI하=lo * 100, CI상=hi * 100, 비용=d["size"].map(COST).mean() * 100,
                                   월초과=mon.mean() * 100, t=nw_t(mon.values))
                    rows.append(row)
        return pd.DataFrame(rows).round(2)

    def yearly(self, ev, mask):
        y = ev[mask].dropna(subset=["d"]).groupby("year").d.mean()
        return f"양(+) {int((y > 0).sum())}/{len(y)}년 · " + ", ".join(f"{k}:{v * 100:.1f}" for k, v in y.items())

    def vs_qqq(self, ev, groups):
        Rv, rows = self.R.values, []
        for gname, mask in groups.items():
            d = ev[mask]
            hold = np.zeros(Rv.shape, bool)
            for c, e in zip(d.ci.values, d.e.values):
                hold[e + 1:e + 1 + self.H, c] = True
            k = hold.sum(1)
            cost = d["size"].map(COST).mean()
            with np.errstate(all="ignore"):
                port = pd.Series(np.where(k > 0, np.nanmean(np.where(hold, Rv, np.nan), axis=1) - cost / self.H, 0.0), index=self.dates)
            for per, (a, b) in PERIODS.items():
                for lab, r in {gname: port, "QQQ": self.qqq}.items():
                    if lab == "QQQ" and any(x["대상"] == "QQQ" and x["기간"] == per for x in rows):
                        continue
                    r = r.loc[str(a):str(b)].dropna()
                    eq = (1 + r).cumprod()
                    rows.append(dict(기간=per, 대상=lab, CAGR=(eq.iloc[-1] ** (252 / len(r)) - 1) * 100,
                                     MDD=(eq / eq.cummax() - 1).min() * 100, 샤프=r.mean() / r.std() * np.sqrt(252),
                                     평균보유=k[(self.dates.year >= a) & (self.dates.year <= b)].mean() if lab != "QQQ" else np.nan))
        return pd.DataFrame(rows).round(2)
