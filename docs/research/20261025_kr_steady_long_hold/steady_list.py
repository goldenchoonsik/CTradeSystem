"""삼성전자 수준(연 4.5%) 이상을 12년간 꾸준히 낸 코스피 종목 목록 (2013-01-02 ~ 2024-12-30, 수정주가, 배당 제외).
  .venv/bin/python docs/research/20261025_kr_steady_long_hold/steady_list.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); D = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
U = pd.read_csv(f"{D}/universe.csv").set_index("code")
px = pd.read_parquet(f"{D}/derived/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
C = pd.read_parquet(f"{D}/derived/daily_ext.parquet", columns=["code", "date", "market_cap"]).pivot(index="date", columns="code", values="market_cap").sort_index()
a = px.index[px.index >= "2013-01-02"][0]; b = px.index[px.index <= "2024-12-30"][-1]; y = (b - a).days / 365.25
cap0 = C.loc[:a].ffill().iloc[-1]; inv = [c for c in cap0.nlargest(300).index if U.market.get(c) == "KOSPI" and c in px.columns and pd.notna(px.loc[a, c])]
p = px.loc[a:b, inv].ffill(); last = px.loc[:b, inv].apply(lambda s: s.last_valid_index())
m = p.resample("ME").last()                                              # 월말
cagr = lambda s, n: (s.iloc[-1] / s.iloc[0]) ** (12 / n) - 1
rows = []
for c in inv:
    s = m[c].dropna(); alive = last[c] >= pd.Timestamp("2024-12-20")
    if len(s) < 100: continue
    n = len(s) - 1; full = (p[c].dropna().iloc[-1] / p.loc[a, c]) ** (1 / y) - 1
    half = n // 2; h1, h2 = cagr(s.iloc[:half + 1], half), cagr(s.iloc[half:], n - half)
    r3 = ((s.shift(-36) / s) ** (1 / 3) - 1).dropna()
    yr = s.resample("YE").last().pct_change().dropna()
    dd = ((s / s.cummax()) - 1).min()
    r2526 = px.loc["2025-01-01":, c].dropna(); r2526 = r2526.iloc[-1] / px.loc[:"2024-12-30", c].dropna().iloc[-1] - 1 if len(r2526) else np.nan
    rows.append(dict(code=c, 종목=U.name.get(c), 생존=alive, CAGR=full * 100, 전반6년=h1 * 100, 후반6년=h2 * 100, 롤링3년양비율=(r3 > 0).mean() * 100, 롤링3년삼성이상=(r3 > 0.045).mean() * 100, 연도양비율=(yr > 0).mean() * 100, MDD=dd * 100, 이후_2025이후수익=r2526 * 100))
T = pd.DataFrame(rows).set_index("code"); S = T.loc["A005930"]
print("삼성전자:", {k: (round(v, 1) if isinstance(v, (float, np.floating)) else v) for k, v in S.items()})
sel = T[(T.CAGR >= S.CAGR) & (T.전반6년 > 0) & (T.후반6년 > 0) & (T.롤링3년양비율 >= 70) & (T.MDD > -60)].sort_values("CAGR", ascending=False)
print(f"\n검토 대상 {len(T)}개 → 기준 통과 {len(sel)}개")
pd.set_option("display.width", 250); print(sel.drop(columns=["롤링3년삼성이상"]).round(1).to_string())
print("\n[참고] 12년 CAGR 은 삼성 이상이지만 '꾸준함' 탈락 (전/후반 한쪽 음수 또는 3년 롤링 양 70% 미만 또는 낙폭 -60% 초과):")
print(T[(T.CAGR >= S.CAGR) & ~T.index.isin(sel.index)].sort_values("CAGR", ascending=False)[["종목", "CAGR", "전반6년", "후반6년", "롤링3년양비율", "MDD"]].round(1).to_string())
T.sort_values("CAGR", ascending=False).round(2).to_csv(os.path.join(os.path.dirname(__file__), "steady_all.csv"), encoding="utf-8-sig"); sel.round(2).to_csv(os.path.join(os.path.dirname(__file__), "steady_selected.csv"), encoding="utf-8-sig")
