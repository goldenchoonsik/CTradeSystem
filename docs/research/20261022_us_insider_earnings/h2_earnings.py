"""H2 실적 놀람(매출·순이익 전년 같은 분기 대비) → 10-Q 제출 다음 날부터 초과수익. H3 대형주 '나쁜 놀람 피하기' 크기.

신호(사전 고정): 이익 놀람 sue = (순이익 − 작년 같은 분기 순이익) / 총자산, 매출 성장 = 매출 / 작년 같은 분기 − 1.
분위: 제출 분기마다 거래가능 사건 안에서 3분위. 좋은 놀람 = 둘 다 상위, 나쁜 놀람 = 둘 다 하위.
진입: 10-Q 제출일 다음 첫 거래일 종가(실적 보도자료가 먼저 나온 회사는 첫 반응을 놓침 → 보수적).
기준선: 같은 기간·크기의 전체 10-Q 사건 평균(생존 편향은 모든 사건에 공통 → 차이로 상쇄; 나쁜 놀람 회사가 더 많이 사라지므로 나쁜 쪽 하락은 과소 추정).
가격: Yahoo 현재 상장만, 현재 SEC 티커 목록으로 CIK 매칭.
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_insider import S, excess, nw_t, COST

HS = [5, 20, 60]


def main():
    df = pd.read_parquet(S / "fsds_10q.parquet")
    df = df[(df.assets > 0) & df.ni.notna() & df.ni_py.notna()].copy()
    df["sue"] = (df.ni - df.ni_py) / df.assets
    df["revg"] = np.where(df.rev_py > 0, df.rev / df.rev_py - 1, np.nan)
    tk = json.loads((S.parent / "reference/company_tickers_exchange_20261022.json").read_text())
    cik2t = {}
    for cik, _, t, _ in tk["data"]:
        cik2t.setdefault(int(cik), t.replace(".", "-"))

    R, C, V, X, t3 = excess()
    dates, cols = R.index, {s: i for i, s in enumerate(R.columns)}
    df["sym"] = df.cik.map(cik2t)
    df = df[df.sym.isin(cols)].copy()
    df["e"] = dates.searchsorted(df.filed.values, side="right")
    df = df[df.e < len(dates)]
    ci, ei = df.sym.map(cols).values, df.e.values
    df = df[(C.values[ei, ci] >= 2) & (V.values[ei, ci] >= 1e6)].copy()
    df["size"] = t3.values[df.e.values, df.sym.map(cols).values]
    df = df[df["size"].notna()]
    df["size"] = df["size"].astype(int)
    df["fq"] = df.filed.dt.to_period("Q")
    df["sue_t"] = df.groupby("fq").sue.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    df["rev_t"] = df.groupby("fq").revg.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if s.notna().sum() >= 30 else np.nan)
    cs = np.vstack([np.zeros((1, X.shape[1])), np.nancumsum(X.values, axis=0)])
    n = len(dates)
    e, c = df.e.values, df.sym.map(cols).values
    for H in HS:
        df[f"car{H}"] = np.where(e + H < n, cs[np.minimum(e + 1 + H, n), c] - cs[np.minimum(e + 1, n), c], np.nan)
    df["year"] = df.filed.dt.year
    df.to_parquet(S / "h2_events.parquet", index=False)

    Xv = X.values
    groups = {"좋은 놀람(둘 다 상위)": (df.sue_t == 2) & (df.rev_t == 2), "나쁜 놀람(둘 다 하위)": (df.sue_t == 0) & (df.rev_t == 0),
              "이익 상위": df.sue_t == 2, "이익 하위": df.sue_t == 0, "매출 상위": df.rev_t == 2, "매출 하위": df.rev_t == 0}
    rows = []
    for per, (a, b) in {"2011~2018": (2011, 2018), "2019~2026": (2019, 2026)}.items():
        for sz, lab in [(None, "전체"), (0, "소"), (1, "중"), (2, "대")]:
            base = df[df.year.between(a, b) & ((df["size"] == sz) if sz is not None else True)]
            for gname, gm in groups.items():
                sel = base[gm.reindex(base.index).values]
                for H in HS:
                    hold = np.zeros(Xv.shape, bool)
                    for cc, ee in zip(sel.sym.map(cols).values, sel.e.values):
                        hold[ee + 1:ee + 1 + H, cc] = True
                    bh = np.zeros(Xv.shape, bool)
                    for cc, ee in zip(base.sym.map(cols).values, base.e.values):
                        bh[ee + 1:ee + 1 + H, cc] = True
                    with np.errstate(all="ignore"):
                        dg = np.where(hold.sum(1) >= 5, np.nanmean(np.where(hold, Xv, np.nan), axis=1), np.nan)
                        db = np.where(bh.sum(1) >= 5, np.nanmean(np.where(bh, Xv, np.nan), axis=1), np.nan)
                    mon = pd.Series(dg - db, index=dates).loc[str(a):str(b)].resample("ME").sum(min_count=5)
                    cg, cb = sel[f"car{H}"].dropna(), base[f"car{H}"].dropna()
                    rows.append(dict(기간=per, 크기=lab, 묶음=gname, H=H, 사건=len(cg), 평균=cg.mean() * 100, 기준선=cb.mean() * 100,
                                     차이=(cg.mean() - cb.mean()) * 100, 중앙차=(cg.median() - cb.median()) * 100,
                                     월차이=mon.mean() * 100, t=nw_t(mon.values), 비용=sel["size"].map(COST).mean() * 100))
    res = pd.DataFrame(rows).round(2)
    res.to_csv(Path(__file__).resolve().parent / "h2_results.csv", index=False)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 500)
    print(res[res.묶음.str.contains("놀람")].to_string(index=False))
    print()
    print(res[~res.묶음.str.contains("놀람") & (res.H == 20)].to_string(index=False))


if __name__ == "__main__":
    main()
