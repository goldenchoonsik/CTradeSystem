"""steady_accum 강건성 — 창(24/36/48)·증가폭(3/5/8%p)·상승월비율(.5/.6/.7)·대상(상위50/100/200), 종목 중복 제거, 상위 대박주 제거.
  .venv/bin/python docs/research/20261024_foreign_accum/steady_robust.py"""
import os, itertools, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","market_cap","foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"])
d = g.merge(p, on=["code","date"]).dropna(subset=["close"]); d["ym"] = d.date.dt.to_period("M")
m = d.sort_values("date").groupby(["code","ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap")
last = P.index.max(); dF = F.diff()

def build(W, TH, POS, N, H=12):
    rows = []
    for y in range(2013, 2026):
        t = pd.Period(f"{y}-12", "M")
        if t not in P.index or (t - W) not in P.index or (t + H) > last: continue
        top = C.loc[t].dropna().nlargest(N).index
        df = pd.DataFrame({"dp": F.loc[t, top] - F.loc[t - W, top], "pos": (dF.loc[t - W + 1:t, top] > 0).mean(), "past": P.loc[t, top] / P.loc[t - W, top] - 1})
        fwd = P.loc[t + H, top] / P.loc[t, top] - 1
        df["ex"] = fwd - fwd.median(); df = df.dropna(); df["acc"] = (df.dp >= TH) & (df.pos >= POS)
        df["dec"] = (df.dp <= -TH) & (df.pos <= 1 - POS); df["y"] = y; df.index.name = None; df["code"] = df.index.values
        df["hi"] = df.past.rank(pct=True) > 2 / 3; rows.append(df)
    return pd.concat(rows) if rows else pd.DataFrame()

res = []
for W, TH, POS, N in itertools.product((24, 36, 48), (3, 5, 8), (.5, .6, .7), (50, 100, 200)):
    A = build(W, TH, POS, N)
    if A.empty or A.acc.sum() < 15: continue
    a, nn = A[A.acc], A[~A.acc & ~A.dec]; hi = A[A.hi]
    res.append(dict(창=W, 폭=TH, 상승월=POS, 상위=N, n=len(a), 증가_중앙=a.ex.median()*100, 중립_중앙=nn.ex.median()*100, 차이=(a.ex.median()-nn.ex.median())*100,
                    통제차이=(hi[hi.acc].ex.median()-hi[~hi.acc & ~hi.dec].ex.median())*100 if hi.acc.sum() >= 8 else np.nan,
                    양수연도=f"{(a.groupby('y').ex.median() > 0).sum()}/{a.y.nunique()}"))
R = pd.DataFrame(res); pd.set_option("display.width", 200)
print("정의 조합 수:", len(R), "| 차이>0:", (R.차이 > 0).sum(), "| 통제차이>0:", (R.통제차이 > 0).sum(), "/", R.통제차이.notna().sum())
print(R.차이.describe().round(1).to_dict()); print(R.통제차이.describe().round(1).to_dict())
print("\n창별 평균 차이 / 통제차이:"); print(R.groupby("창")[["차이", "통제차이"]].median().round(1))
print("\n상위 대상별:"); print(R.groupby("상위")[["차이", "통제차이", "n"]].median().round(1))
print("\n최악 5:"); print(R.nsmallest(5, "차이").round(1).to_string(index=False))
# 기준 정의에서 중복 제거·대박주 제거
A = build(36, 5, .6, 100); a, nn = A[A.acc], A[~A.acc & ~A.dec]
first = a.sort_values("y").groupby("code").head(1)
print(f"\n[기준] 증가 {len(a)}건 → 종목 {a.code.nunique()}개; 종목당 첫 사건만: n={len(first)} 초과중앙 {first.ex.median()*100:.1f}%p, 상위반 {(first.ex>0).mean()*100:.0f}%")
print("종목 단위 연도 클러스터 부트스트랩(종목 재표집) 증가-중립 중앙값 차이 95%구간:")
rng = np.random.default_rng(0); codes = a.code.unique(); b = []
for _ in range(2000):
    s = rng.choice(codes, len(codes)); aa = pd.concat([a[a.code == c] for c in s]); b.append((aa.ex.median() - nn.ex.median()) * 100)
print(np.percentile(b, [2.5, 50, 97.5]).round(1))
drop = a.ex.sort_values(ascending=False).index[:5]; print("수익 상위 5건 제거 후 증가 중앙", round(a.drop(drop, errors="ignore").ex.median()*100, 1))
print("증가 종목 코드:", sorted(a.code.unique())[:60])
R.to_csv(os.path.join(os.path.dirname(__file__), "steady_robust.csv"), index=False)
