"""H1 임원 장내 매수 → 이후 초과수익 (가진 자료로 대략 판정).

표본: Yahoo 현재 상장 보통주만(생존 편향 → 매수 효과가 부풀려지는 쪽. 여기서 없으면 '없음' 확정, 있으면 상폐 가격으로 재확인).
진입: 신고일(filing_date) 다음 첫 거래일 종가. 진입일에 무수정 종가 ≥ $2, 60일 평균 거래대금 ≥ $100만.
기준: 같은 날 같은 거래대금 5분위(전일 기준) 거래가능 종목 동일가중 평균. 일수익 > +200% 또는 < −80% 는 자료 오류로 제외.
판정 t: 달력 시간 포트폴리오(그날 보유창 안의 종목 동일가중 초과수익 → 월 합계, Newey-West 3).
묶음(사전 고정): 전체 / 몰아서(30일 안 서로 다른 임원 ≥2) / 기회주의(CMP: 직전 3년 매년 거래했으나 같은 달 반복 아님) / 일상(같은 달 3년 반복) / $10만 이상 / CEO·CFO.
"""
import numpy as np
import pandas as pd
from pathlib import Path

S = Path(__file__).resolve().parents[3] / "DataVault/staging/us_sec_bulk/derived"
OUT = Path(__file__).resolve().parent
HS = [20, 60, 120]
COST = {0: 0.008, 1: 0.003, 2: 0.001}   # 왕복 비용 추정: 소형·중형·대형(거래대금 3분위)


def nw_t(x, lag=3):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = len(x)
    if n < 12: return np.nan
    e = x - x.mean(); v = e @ e / n
    for k in range(1, lag + 1):
        v += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(v / n)


def excess():
    """초과 일수익 X(같은 날 같은 거래대금 5분위 기준), 거래가능 조건, 진입일 크기 3분위(0..2)."""
    R = pd.read_parquet(S / "panel_ret.parquet")
    C = pd.read_parquet(S / "panel_close.parquet").reindex_like(R)
    V = pd.read_parquet(S / "panel_dvol.parquet").reindex_like(R)
    R = R.where((R < 2) & (R > -0.8))
    ok = (C.shift(1) >= 2) & (V.shift(1) >= 1e6)
    q5 = V.shift(1).where(ok).rank(axis=1, pct=True).mul(5).apply(np.ceil)
    bench = pd.DataFrame(np.nan, index=R.index, columns=R.columns)
    for q in range(1, 6):
        mq = (q5 == q)
        bench = bench.where(~mq, R.where(mq).mean(axis=1), axis=0)
    X = (R - bench).where(ok)                                  # 초과 일수익
    t3 = V.where((C >= 2) & (V >= 1e6)).rank(axis=1, pct=True).mul(3).apply(np.ceil) - 1   # 진입일 크기 3분위 0..2
    return R, C, V, X, t3


def classify(tr):
    """CMP 기회주의/일상 분류: (임원, 회사)별 직전 3년 거래 이력."""
    tr = tr.assign(y=tr.trans_date.dt.year, m=tr.trans_date.dt.month)
    ym = tr.groupby(["owner_cik", "issuercik"]).apply(lambda g: set(zip(g.y, g.m)), include_groups=False)
    yrs = tr.groupby(["owner_cik", "issuercik"]).y.agg(lambda s: set(s))
    out = []
    for o, c, y, m in zip(tr.owner_cik, tr.issuercik, tr.y, tr.m):
        ys = yrs[(o, c)]
        if not all(y - k in ys for k in (1, 2, 3)):
            out.append("na"); continue
        s = ym[(o, c)]
        out.append("routine" if all((y - k, m) in s for k in (1, 2, 3)) else "opp")
    return out


def main():
    f = pd.read_parquet(S / "form4_ps.parquet")
    f = f[f.document_type.isin(["4", "4/A"]) & f.relationship.str.contains("Director|Officer", na=False)
          & (f.trans_shares > 0) & f.trans_date.notna() & f.filing_date.notna()]
    f = f.drop_duplicates(["owner_cik", "issuercik", "trans_date", "trans_code", "trans_shares", "trans_pricepershare"])
    f = f[f.trans_date.between("2003-01-01", "2026-09-30")]
    f["cls"] = classify(f[["owner_cik", "issuercik", "trans_date"]])
    p = f[(f.trans_code == "P") & (f.trans_pricepershare > 0)].copy()
    p["value"] = p.trans_shares * p.trans_pricepershare
    p["ceo_cfo"] = p.title.str.contains(r"CEO|Chief Executive|CFO|Chief Financial", case=False, na=False)

    ev = p.groupby(["issuercik", "filing_date"]).agg(
        value=("value", "sum"), n_ins=("owner_cik", "nunique"), opp=("cls", lambda s: (s == "opp").any()),
        routine=("cls", lambda s: (s == "routine").all()), ceo_cfo=("ceo_cfo", "any"),
        owners=("owner_cik", lambda s: set(s))).reset_index().sort_values(["issuercik", "filing_date"])
    # 몰아서: 30일 안(이 신고 포함) 서로 다른 임원 ≥2
    clus = []
    for c, g in ev.groupby("issuercik", sort=False):
        d, ow = g.filing_date.values, g.owners.values
        for i in range(len(g)):
            j = np.searchsorted(d, d[i] - np.timedelta64(30, "D"))
            clus.append(len(set().union(*ow[j:i + 1])) >= 2)
    ev["cluster"] = clus
    ev = ev[ev.value >= 10_000]

    m = pd.read_parquet(S / "insider_buy_events.parquet")[["issuercik", "cik_sym", "sym_hist", "by_cik", "sym_ok"]]
    m["sym"] = np.where(m.by_cik, m.cik_sym, np.where(m.sym_ok, m.sym_hist, None))
    sym = m.dropna(subset=["sym"]).drop_duplicates("issuercik").set_index("issuercik").sym
    ev["sym"] = ev.issuercik.map(sym)

    R, C, V, X, t3 = excess()
    dates = R.index
    cols = {s: i for i, s in enumerate(R.columns)}
    ev = ev[ev.sym.isin(cols)].copy()
    ev["e"] = dates.searchsorted(ev.filing_date.values, side="right")   # 신고일 다음 첫 거래일
    ev = ev[ev.e < len(dates)]
    ci, ei = ev.sym.map(cols).values, ev.e.values
    ev["tradable"] = ((C.values[ei, ci] >= 2) & (V.values[ei, ci] >= 1e6))
    ev["size"] = t3.values[ei, ci]
    ev = ev[ev.tradable & ev["size"].notna()].copy()
    ev["size"] = ev["size"].astype(int)
    Xv = X.values
    for H in HS:
        car = np.full(len(ev), np.nan)
        for k, (c, e) in enumerate(zip(ev.sym.map(cols).values, ev.e.values)):
            if e + H < len(dates):
                car[k] = np.nansum(Xv[e + 1:e + 1 + H, c])
        ev[f"car{H}"] = car
    ev["year"] = ev.filing_date.dt.year
    ev.drop(columns="owners").to_parquet(S / "h1_events.parquet", index=False)

    groups = {"전체": ev.index == ev.index, "몰아서(≥2명/30일)": ev.cluster.values, "기회주의(CMP)": ev.opp.values,
              "일상(CMP)": ev.routine.values & ~ev.opp.values, "$10만 이상": (ev.value >= 1e5).values, "CEO·CFO": ev.ceo_cfo.values}
    rows = []
    for per, (a, b) in {"2006~2018": (2006, 2018), "2019~2026": (2019, 2026)}.items():
        for gname, gm in groups.items():
            for sz in ["전체", 0, 1, 2]:
                sel = ev[gm & ev.year.between(a, b).values & ((ev["size"] == sz).values if sz != "전체" else True)]
                for H in HS:
                    # 달력 시간 포트폴리오
                    hold = np.zeros(Xv.shape, bool)
                    for c, e in zip(sel.sym.map(cols).values, sel.e.values):
                        hold[e + 1:e + 1 + H, c] = True
                    daily = np.where(hold.sum(1) >= 5, np.nanmean(np.where(hold, Xv, np.nan), axis=1), np.nan)
                    mon = pd.Series(daily, index=dates).loc[str(a):str(b)].resample("ME").sum(min_count=5)
                    c_ = sel[f"car{H}"].dropna()
                    top5 = c_.nlargest(5).sum()
                    cost = sel["size"].map(COST).mean()
                    rows.append(dict(기간=per, 묶음=gname, 크기={"전체": "전체", 0: "소", 1: "중", 2: "대"}[sz], H=H, 사건=len(c_),
                                     평균=c_.mean(), 중앙=c_.median(), 승률=(c_ > 0).mean(), 상위5제외=(c_.sum() - top5) / max(len(c_) - 5, 1),
                                     비용=cost, 비용후=c_.mean() - cost, 월초과=mon.mean(), t=nw_t(mon.values), 개월=mon.notna().sum()))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "h1_results.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    fmt = res.copy()
    for k in ["평균", "중앙", "상위5제외", "비용", "비용후", "월초과"]:
        fmt[k] = (fmt[k] * 100).round(2)
    fmt["승률"] = (fmt.승률 * 100).round(0); fmt["t"] = fmt.t.round(2)
    print(fmt[(fmt.크기 == "전체") | (fmt.H == 60)].to_string(index=False))


if __name__ == "__main__":
    main()
