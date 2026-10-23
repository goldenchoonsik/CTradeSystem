"""H1 같은 잣대 비교: 기회주의 임원 매수(소형) 60일 보유 포트폴리오를 실제로 굴렸을 때 vs QQQ·소형 기준.

포트폴리오: 매일 보유창 안의 종목 동일가중(총수익률), 보유 0개면 현금 0%. 비용: 왕복 0.8% 를 보유일수 60일에 나눠 매일 차감.
소형 기준: 같은 날 거래가능 소형 3분위 전체 동일가중. 세전, 원화 환산 없음(USD).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_insider import S, excess
from panel import load

H = 60


def stats(r):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    return dict(CAGR=(eq.iloc[-1] ** (1 / yrs) - 1) * 100, MDD=(eq / eq.cummax() - 1).min() * 100,
                변동성=r.std() * np.sqrt(252) * 100, 샤프=r.mean() / r.std() * np.sqrt(252))


def main():
    R, C, V, X, t3 = excess()
    Rv, dates = R.values, R.index
    cols = {s: i for i, s in enumerate(R.columns)}
    ev = pd.read_parquet(S / "h1_events.parquet")
    rows = []
    small = (t3.shift(1) == 0)
    bench_small = R.where(small).mean(axis=1)
    q = load("QQQ")[1]
    for name, m in {"기회주의·소형": ev.opp & (ev["size"] == 0), "몰아서·소형": ev.cluster & (ev["size"] == 0), "전체·소형": ev["size"] == 0}.items():
        sel = ev[m]
        hold = np.zeros(Rv.shape, bool)
        for c, e in zip(sel.sym.map(cols).values, sel.e.values):
            hold[e + 1:e + 1 + H, c] = True
        k = hold.sum(1)
        with np.errstate(all="ignore"):
            port = np.where(k > 0, np.nanmean(np.where(hold, Rv, np.nan), axis=1) - 0.008 / H, 0.0)
        port = pd.Series(port, index=dates)
        for per, (a, b) in {"2006~2018": ("2006", "2018"), "2019~2026": ("2019", "2026")}.items():
            for lab, r in {name: port, "소형 전체(기준)": bench_small, "QQQ": q}.items():
                if lab != name and any(x["대상"] == lab and x["기간"] == per for x in rows):
                    continue
                rows.append(dict(기간=per, 대상=lab, 평균보유=(k[(dates >= a) & (dates <= b + "-12-31")].mean() if lab == name else np.nan),
                                 **stats(r.loc[a:b])))
    res = pd.DataFrame(rows).round(2)
    res.to_csv(Path(__file__).resolve().parent / "h1_vs_qqq.csv", index=False)
    print(res.sort_values(["기간", "대상"]).to_string(index=False))


if __name__ == "__main__":
    main()
