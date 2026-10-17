"""SimFin 재무 팩터 시험 — PREREG.md 규칙 그대로. 실행: .venv/bin/python -I factor_test.py"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[3] / "DataVault/staging/us_fundamentals_simfin/raw"
COST_MONTHLY = 0.002  # 스프레드 월 비용: 양 다리 × 회전 50% × 왕복 0.2%
import os
MIN_MCAP, MIN_PX = 1e8, 1.0
MIN_N = int(os.environ.get("MIN_N", "50"))  # 사전등록=50
NQ = int(os.environ.get("NQ", "5"))  # 사전등록=5분위 (표본 부족 시 탐색용 3분위)


def table(stmt):
    return pd.DataFrame(stmt["data"], columns=stmt["columns"])


def load_company(cid):
    p = RAW / "statements" / f"{cid}.json"
    d = json.loads(p.read_text())
    if not d or not d[0].get("statements"):
        return None
    st = {s["statement"]: table(s) for s in d[0]["statements"]}
    if not {"PL", "BS", "CF"} <= set(st):
        return None
    key = ["Fiscal Year", "Fiscal Period"]
    parts = []
    for name, cols in [("PL", ["Report Date", "Publish Date", "Revenue", "Gross Profit", "Net Income"]),
                       ("BS", ["Total Assets"]),
                       ("CF", ["Cash from Operating Activities", "Change in Fixed Assets & Intangibles"])]:
        t = st[name]
        t = t[t["Fiscal Period"].isin(["Q1", "Q2", "Q3", "Q4"]) & (t["TTM"] == 0)]
        parts.append(t.reindex(columns=key + cols).drop_duplicates(key, keep="last").set_index(key))
    q = pd.concat(parts, axis=1, join="inner").reset_index()
    if len(q) < 5:
        return None
    q["rd"] = pd.to_datetime(q["Report Date"])
    q["pd"] = pd.to_datetime(q["Publish Date"], errors="coerce").fillna(q["rd"] + pd.Timedelta(days=90))
    q = q.sort_values("rd").reset_index(drop=True)
    q["fcf"] = q["Cash from Operating Activities"] + q["Change in Fixed Assets & Intangibles"].fillna(0)
    q["capex_known"] = q["Change in Fixed Assets & Intangibles"].notna()
    span = (q["rd"] - q["rd"].shift(3)).dt.days
    ok = span.between(240, 330)  # 연속 4개 분기
    for c, o in [("Revenue", "rev"), ("Gross Profit", "gp"), ("Net Income", "ni"), ("fcf", "fcf4")]:
        q[o + "_ttm"] = q[c].rolling(4).sum().where(ok)
    q["assets"] = q["Total Assets"]
    span4 = (q["rd"] - q["rd"].shift(4)).dt.days
    ok4 = span4.between(330, 400)
    q["rev_g"] = (q["rev_ttm"] / q["rev_ttm"].shift(4) - 1).where(ok4 & (q["rd"] - q["rd"].shift(7)).dt.days.between(640, 800))
    q["asset_g"] = (q["assets"] / q["assets"].shift(4) - 1).where(ok4)
    q["info"] = q["pd"] + pd.Timedelta(days=1)
    return q[["info", "rd", "rev_ttm", "gp_ttm", "ni_ttm", "fcf4_ttm", "assets", "rev_g", "asset_g"]].sort_values("info")


def load_prices(cid):
    p = RAW / "prices" / f"{cid}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    if not d or not d[0].get("data"):
        return None
    t = table(d[0])
    t["Date"] = pd.to_datetime(t["Date"])
    return t.set_index("Date")[["Last Closing Price", "Adjusted Closing Price", "Common Shares Outstanding"]]


def t_nw(x, lag=3):
    x = np.asarray(x, float); x = x[~np.isnan(x)]; n = len(x)
    if n < 12: return np.nan
    e = x - x.mean(); g0 = e @ e / n
    s = g0 + 2 * sum((1 - k / (lag + 1)) * (e[k:] @ e[:-k] / n) for k in range(1, lag + 1))
    return x.mean() / np.sqrt(s / n)


def main():
    comps = {c["id"]: c for c in json.loads((RAW / "companies_list.json").read_text())}
    rows = []
    for f in sorted((RAW / "statements").glob("*.json")):
        cid = int(f.stem)
        q = load_company(cid); px = load_prices(cid)
        if q is None or px is None:
            continue
        me = px.resample("ME").last().dropna(subset=["Last Closing Price"])
        # 월말 시점 기준 마지막 거래일 가격(월 중간 상폐면 마지막 값이 그 달 말로 잡힘 → 확인용 last_date)
        last_date = px.index.to_series().resample("ME").last()
        me["last_date"] = last_date.reindex(me.index)
        me = me[(me.index.to_series() - me["last_date"]).dt.days <= 7]
        adj = px["Adjusted Closing Price"].resample("ME").last().ffill()  # 상폐 후 마지막 가격 유지(수익률 0)
        me["fwd"] = (adj.shift(-1) / adj - 1).reindex(me.index)
        me["mcap"] = me["Last Closing Price"] * me["Common Shares Outstanding"]
        me = me.reset_index().rename(columns={"Date": "t"}).sort_values("t")
        m = pd.merge_asof(me, q, left_on="t", right_on="info", direction="backward")
        m["id"] = cid
        # 정보가 너무 오래된 경우(>200일) 제외
        m = m[(m["t"] - m["rd"]).dt.days <= 200]
        rows.append(m)
    d = pd.concat(rows, ignore_index=True)
    n0 = d["id"].nunique()
    print(f"[탈락 추적] 가격·재무 결합 {n0}개 회사 → ", end="")
    d = d.dropna(subset=["rev_ttm"]); print(f"TTM 재무 있음 {d['id'].nunique()} → ", end="")
    d = d[d["mcap"].notna()]; print(f"시총 계산 가능 {d['id'].nunique()} → ", end="")
    d = d[d["mcap"] >= MIN_MCAP]; print(f"시총>=$1억 {d['id'].nunique()}")
    d = d[(d["mcap"] >= MIN_MCAP) & (d["Last Closing Price"] >= MIN_PX) & d["fwd"].notna()]
    d["sector"] = d["id"].map(lambda i: comps[i].get("sectorName"))
    d["ey"] = d["ni_ttm"] / d["mcap"]
    d["fcfy"] = d["fcf4_ttm"] / d["mcap"]
    d["gpa"] = d["gp_ttm"] / d["assets"]
    d["revg"] = d["rev_g"]
    d["asset_low"] = -d["asset_g"]
    sigs = ["ey", "fcfy", "gpa", "revg", "asset_low"]

    print(f"표본: 회사 {d['id'].nunique()}개, 회사-월 {len(d)}, 기간 {d['t'].min().date()}~{d['t'].max().date()}")
    print("시총 중앙값 $%.1fB, 하위10%% $%.2fB, 상위10%% $%.1fB" % tuple(np.nanpercentile(d["mcap"], [50, 10, 90]) / 1e9 * np.array([1, 1, 1])))
    print("섹터 상위:", d.drop_duplicates("id")["sector"].value_counts().head(5).to_dict())
    print("월별 종목수 중앙값:", int(d.groupby("t").size().median()))
    ew = d.groupby("t")["fwd"].mean()
    print(f"유니버스 동일가중 월평균 {ew.mean():.3%} (연 {(1+ew.mean())**12-1:.1%})\n")

    res = {}
    for s in sigs:
        x = d.dropna(subset=[s]).copy()
        x = x[x.groupby("t")[s].transform("size") >= MIN_N]
        x["q"] = x.groupby("t")[s].transform(lambda v: pd.qcut(v.rank(method="first"), NQ, labels=False))
        qm = x.groupby(["t", "q"])["fwd"].mean().unstack().reindex(columns=range(NQ))
        if qm[NQ - 1].notna().sum() < 24:
            print(f"[{s}] 시험 불가: 월별 {MIN_N}종목 이상인 달이 24개월 미만\n"); continue
        spread = (qm[NQ - 1] - qm[0]).dropna()
        yr = spread.groupby(spread.index.year).mean()
        res[s] = dict(n=len(spread), mean=spread.mean(), t=t_nw(spread), net=spread.mean() - COST_MONTHLY,
                      drop_best=spread[spread.index.year != yr.idxmax()].mean(), best_year=int(yr.idxmax()),
                      q=qm.mean().round(4).tolist(), yr=yr.round(4).to_dict())
        r = res[s]
        ok = r["t"] >= 2.5 and r["drop_best"] > 0 and r["net"] > 0
        print(f"[{s}] 개월 {r['n']} 스프레드 월평균 {r['mean']:.3%} t={r['t']:.2f} 비용후 {r['net']:.3%} "
              f"최고연도({r['best_year']}) 제외 {r['drop_best']:.3%} → {'합격' if ok else '불합격'}")
        print("   분위(하→상) 월평균:", [f"{v:.2%}" for v in r["q"]])
        print("   연도별 스프레드:", {k: f"{v:.2%}" for k, v in r["yr"].items()})


if __name__ == "__main__":
    main()
