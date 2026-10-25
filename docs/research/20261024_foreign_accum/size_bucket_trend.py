"""시총 구간별 외국인 지분율 추이 — 연말 기준. (A) 매년 순위로 구간 재분류, (B) 2013 말 구간을 고정한 종목군(생존 종목)의 변화. 우선주 제외.
  .venv/bin/python docs/research/20261024_foreign_accum/size_bucket_trend.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
V = f"{ROOT}/DataVault/staging/kr_equity"; D = f"{V}/derived"
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]).dropna(subset=["market_cap", "foreign_hold_pct"])
nm = pd.read_csv(f"{V}/reference/listed_snapshot.csv").set_index("code"); pref = set(nm.index[nm.is_preferred.astype(str) != "0"])
g = g[~g.code.isin(pref)]; g["y"] = g.date.dt.year
ye = g.sort_values("date").groupby(["code", "y"]).tail(1)
ye = ye[(ye.y <= 2025) | (ye.date >= "2026-10-01")]; ye.loc[ye.y == 2026, "y"] = 2026
B = [(1, 10, "1~10"), (11, 30, "11~30"), (31, 100, "31~100"), (101, 200, "101~200"), (201, 500, "201~500"), (501, 9999, "501~")]
ye["rank"] = ye.groupby("y").market_cap.rank(ascending=False)
def bk(r):
    for lo, hi, n in B:
        if lo <= r <= hi: return n
ye["b"] = ye["rank"].map(bk); order = [b[2] for b in B]
A = ye.groupby(["y", "b"]).foreign_hold_pct.mean().unstack()[order]
print("== (A) 매년 순위로 재분류한 구간 평균 외국인 지분율(%) =="); print(A.round(1).to_string())
dA = A.diff(); print("\n연도별 변화(%p):"); print(dA.round(1).to_string())
print("\n구간별: 평균 변화/년, 양(+)인 해 수, 2013→2026 총변화, 선형추세 기울기(%p/년)")
yrs = np.arange(len(A)); rows = []
for b in order:
    s = A[b].dropna(); sl = np.polyfit(np.arange(len(s)), s.values, 1)[0]
    rows.append(dict(구간=b, 평균변화_년=dA[b].mean(), 양수연도=f"{(dA[b] > 0).sum()}/{dA[b].notna().sum()}", 총변화=s.iloc[-1] - s.iloc[0], 기울기=sl, 최저연도=int(s.idxmin()), 최고연도=int(s.idxmax())))
print(pd.DataFrame(rows).round(2).to_string(index=False))
print("\n구간 간 연도별 변화 동조(상관, 상위 구간과의 상관):", {b: round(dA[b].corr(dA["1~10"]), 2) for b in order[1:]})
# (B) 고정 종목군: 2013 말 순위로 구간 분류, 이후 같은 종목 추적 (2013말에 살아있던 종목, 상폐 제외)
base = ye[ye.y == 2013].set_index("code"); fixed = base.b
Bf = ye[ye.code.isin(fixed.index)].assign(fb=lambda x: x.code.map(fixed)).groupby(["y", "fb"]).foreign_hold_pct.agg(["mean", "size"])
print("\n== (B) 2013 말 구간 고정 종목군(생존) 평균 외국인 지분율(%) =="); print(Bf["mean"].unstack()[order].round(1).to_string())
n13 = base.groupby("b").size()[order]; print("2013 말 구간 종목 수:", n13.to_dict(), "| 2026 생존:", Bf["size"].unstack().loc[2026][order].to_dict())
# 폭: 구간 내 지분율 상승 종목 비율(전년 대비 +1%p 이상 / −1%p 이하)
ye = ye.sort_values(["code", "y"]); ye["d"] = ye.groupby("code").foreign_hold_pct.diff(); ye = ye[ye.y.diff() != 0]
br = ye.dropna(subset=["d"]).groupby(["y", "b"]).d.apply(lambda s: (s >= 1).mean() * 100 - (s <= -1).mean() * 100).unstack()[order]
print("\n== 구간 내 '지분 상승 종목 비율 − 하락 종목 비율'(전년 대비 ±1%p 기준, %p) =="); print(br.round(0).to_string())
A.to_csv(os.path.join(os.path.dirname(__file__), "size_bucket_level.csv")); dA.to_csv(os.path.join(os.path.dirname(__file__), "size_bucket_change.csv"))
