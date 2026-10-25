"""외국인 지분율 '꾸준한 증가' 구간 — 시총 상위 100(형성 시점) 대상, 연말 형성, 이후 12/24/36개월 수익(시총 상위100 중앙값 대비 초과).
꾸준한 증가 = 최근 36개월 지분율 변화 ≥ +5%p 이면서 월별 변화 중 양(+)인 달 ≥ 60%.  통제 = 같은 36개월 수익 상위 1/3 안에서 증가 종목 vs 비증가 종목.
  .venv/bin/python docs/research/20261024_foreign_accum/steady_accum.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","market_cap","foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"])
d = g.merge(p, on=["code","date"]).dropna(subset=["close"])
d["ym"] = d.date.dt.to_period("M")
m = d.sort_values("date").groupby(["code","ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap")
last = P.index.max(); print("마지막 월", last)
dF = F.diff()
rows = []
for y in range(2013, 2024):
    t = pd.Period(f"{y+3 if False else y}-12", "M")
    if t not in P.index or (t - 36) not in P.index: continue
    top = C.loc[t].dropna().nlargest(100).index
    pc = F.loc[t, top] - F.loc[t - 36, top]
    pos = (dF.loc[t - 35:t, top] > 0).mean()
    past = P.loc[t, top] / P.loc[t - 36, top] - 1
    df = pd.DataFrame({"dp": pc, "pos": pos, "past": past}).dropna()
    df["acc"] = (df.dp >= 5) & (df.pos >= .6)
    df["dec"] = (df.dp <= -5) & (df.pos <= .4)
    for H in (12, 24, 36):
        if (t + H) not in P.index and t + H > last: continue
        tt = min(t + H, last)
        if (tt - t).n < H: continue
        fwd = (P.loc[tt, df.index] / P.loc[t, df.index] - 1)
        df[f"f{H}"] = fwd - fwd.median()
    df["y"] = y; df["code"] = df.index
    df["tert"] = pd.qcut(df.past.rank(method="first"), 3, labels=["하","중","상"])
    rows.append(df)
A = pd.concat(rows)
print("형성 연도별 증가 종목 수:", A[A.acc].groupby("y").size().to_dict())
out = []
for H in (12, 24, 36):
    c = f"f{H}"
    a = A[A.acc].dropna(subset=[c]); n = A[~A.acc & ~A.dec].dropna(subset=[c]); dd = A[A.dec].dropna(subset=[c])
    hi = A[A.tert == "상"].dropna(subset=[c]); ha, hn = hi[hi.acc], hi[~hi.acc & ~hi.dec]
    ya = a.groupby("y")[c].mean(); 
    out.append(dict(이후=f"{H}개월", 증가_n=len(a), 증가_초과평균=a[c].mean()*100, 증가_초과중앙=a[c].median()*100, 연도별_양수=f"{(ya>0).sum()}/{len(ya)}",
                    감소_n=len(dd), 감소_초과평균=dd[c].mean()*100, 중립_초과평균=n[c].mean()*100,
                    통제_증가=ha[c].mean()*100, 통제_비증가=hn[c].mean()*100, 통제_증가n=len(ha)))
print(pd.DataFrame(out).round(1).to_string(index=False))
print("\n증가 종목의 직전 36개월 수익(중앙) vs 비증가:", (A[A.acc].past.median()*100).round(0), (A[~A.acc & ~A.dec].past.median()*100).round(0))
A[A.acc][["y","code","dp","pos","past","f12","f24","f36"]].round(3).to_csv(os.path.join(os.path.dirname(__file__), "steady_accum_episodes.csv"), index=False)

print("\n== 중앙값 비교 (초과수익 %, 시총 상위100 중앙값 대비) ==")
r = []
for H in (12, 24, 36):
    c = f"f{H}"; x = A.dropna(subset=[c]); hi = x[x.tert == "상"]
    r.append(dict(이후=f"{H}개월", 증가=x[x.acc][c].median()*100, 중립=x[~x.acc & ~x.dec][c].median()*100, 감소=x[x.dec][c].median()*100,
                  통제_증가=hi[hi.acc][c].median()*100, 통제_비증가=hi[~hi.acc & ~hi.dec][c].median()*100,
                  증가_상위반_비율=(x[x.acc][c] > 0).mean()*100, 중립_상위반_비율=(x[~x.acc & ~x.dec][c] > 0).mean()*100))
print(pd.DataFrame(r).round(1).to_string(index=False))
print("\n연도별(12개월) 증가 종목 초과 중앙값 %:", (A[A.acc].dropna(subset=["f12"]).groupby("y").f12.median()*100).round(0).to_dict())
print("연도별 증가 종목 수와 12m 초과 평균 - 상위 2종목 제외 평균:",
      round(A[A.acc].dropna(subset=["f12"]).f12.sort_values().iloc[:-2].mean()*100, 1))
