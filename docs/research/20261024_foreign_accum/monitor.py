"""외국인 수급 모니터 — (A) 주체별 자금 강도(KRX 코스피 일별), (B) 현재 시총 상위 200 외국인 지분율 상태(상승 지속/상단 정체/상단 후 하락/감소), (C) 상태별 과거 12개월 성과(12월 형성).
  .venv/bin/python docs/research/20261024_foreign_accum/monitor.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); OUT = os.path.dirname(os.path.abspath(__file__)); V = f"{ROOT}/DataVault/staging/kr_equity"
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
# ---------- (A) 주체별 자금 강도 ----------
def ld(n):
    x = pd.read_csv(f"{ROOT}/KRX/{n}.csv", encoding="cp949"); x["일자"] = pd.to_datetime(x["일자"], format="%Y/%m/%d"); return x.set_index("일자").sort_index()
b, s = ld("거래대금_매수"), ld("거래대금_매도"); tot = b["전체"]
G = {"외국인": ["외국인", "기타외국인"], "기관 합계": ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금 등"], "연기금 등": ["연기금 등"], "금융투자": ["금융투자"], "투신": ["투신"], "사모": ["사모"], "개인": ["개인"], "기타법인": ["기타법인"]}
net = pd.DataFrame({k: (b[v].sum(axis=1, min_count=1) - s[v].sum(axis=1, min_count=1)) / tot * 100 for k, v in G.items()})   # 거래대금 대비 %
share = pd.DataFrame({k: (b[v].sum(axis=1, min_count=1) + s[v].sum(axis=1, min_count=1)) / (2 * tot) * 100 for k, v in G.items()})
roll = lambda n: net.rolling(n).sum(); last = net.index[-1]
A = pd.DataFrame({"거래비중(20일,%)": share.rolling(20).mean().iloc[-1], "순매수 1일(%)": net.iloc[-1], "5일 누적(%p)": roll(5).iloc[-1], "20일 누적(%p)": roll(20).iloc[-1], "60일 누적(%p)": roll(60).iloc[-1]})
A["20일 순위(2016~, %)"] = [(roll(20)[c][roll(20).index >= "2016-01-01"].dropna() <= roll(20)[c].iloc[-1]).mean() * 100 for c in A.index]
print(f"== (A) 주체별 자금 강도 (코스피, 기준일 {last.date()}, 순매수는 그날 거래대금 대비 %, 누적은 %p 합) =="); print(A.round(1).to_string())
# ---------- (B) 외국인 지분율 상태 ----------
g = pd.read_parquet(f"{V}/derived/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]).dropna(subset=["market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{V}/derived/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"]); d = g.merge(p, on=["code", "date"]); d["ym"] = d.date.dt.to_period("M")
nm = pd.read_csv(f"{V}/reference/listed_snapshot.csv").set_index("code"); d = d[~d.code.isin(set(nm.index[nm.is_preferred.astype(str) != "0"]))]
m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap"); P = m.pivot(index="ym", columns="code", values="close")
def state(t, top):
    w = F.loc[t - 36:t, top]; ok = (w.notna().sum() >= 34).values; top = top[ok]; w = w[top]
    X = pd.DataFrame({"지분율": w.iloc[-1], "3년최고": w.max(), "dp36": w.iloc[-1] - w.iloc[0], "dp12": F.loc[t, top] - F.loc[t - 12, top], "dp6": F.loc[t, top] - F.loc[t - 6, top], "dp3": F.loc[t, top] - F.loc[t - 3, top], "cons": (w.diff().iloc[1:] > 0).mean()})
    X["상단까지"] = X["3년최고"] - X["지분율"]
    X["상태"] = np.select([(X.dp36 >= 3) & (X.dp6 > 1), (X.dp36 >= 3) & (X.상단까지 <= 1) & (X.dp6.abs() <= 1), (X.dp36 >= 3) & (X.상단까지 >= 3) & (X.dp6 < -1), (X.dp36 <= -3) & (X.dp6 < 1)], ["상승 지속", "상단 정체", "상단 후 하락", "감소 지속"], "기타")
    return X
t = F.index.max(); top = C.loc[t].dropna().nlargest(200).index; X = state(t, top); X["이름"] = [nm.name.get(c, c) for c in X.index]; X["시총순위"] = C.loc[t, X.index].rank(ascending=False); X["시총(조)"] = C.loc[t, X.index] / 1e12
print(f"\n== (B) 현재({t}) 상위 200 외국인 지분율 상태 분포 ==", X.상태.value_counts().to_dict())
for lab in ("상승 지속", "상단 정체", "상단 후 하락"):
    z = X[X.상태 == lab].sort_values("dp36", ascending=False); print(f"\n[{lab}] {len(z)}종목"); print(z[["이름", "시총순위", "지분율", "3년최고", "dp36", "dp12", "dp6", "dp3"]].round(1).head(15).to_string())
X.sort_values(["상태", "dp36"], ascending=[True, False]).to_csv(f"{OUT}/monitor_current.csv", encoding="utf-8-sig")
A.to_csv(f"{OUT}/monitor_flows.csv", encoding="utf-8-sig")
# ---------- (C) 과거 성과 (12월 형성 → 12개월 초과수익) ----------
rows = []
for y in range(2016, 2025):
    tt = pd.Period(f"{y}-12", "M"); tp = C.loc[tt].dropna().nlargest(200).index; S = state(tt, tp)
    fw = pd.Series({c: (P[c].loc[tt:tt + 12].dropna().iloc[-1] if P[c].loc[tt:tt + 12].notna().any() else np.nan) for c in S.index}) / P.loc[tt, S.index] - 1; S["fwd"] = fw; S["ex"] = fw - fw.median(); S["y"] = y; rows.append(S)
H = pd.concat(rows)
o = H[H.상태 != "기타"].groupby("상태").agg(건수=("fwd", "size"), 초과중앙=("ex", lambda s: s.median() * 100), 초과평균=("ex", lambda s: s.mean() * 100), 승률=("ex", lambda s: (s > 0).mean() * 100), 수익중앙=("fwd", lambda s: s.median() * 100), 하위10퍼센타일=("fwd", lambda s: s.quantile(.1) * 100), 마이너스30이하=("fwd", lambda s: (s <= -.3).mean() * 100), 상위10퍼센타일=("fwd", lambda s: s.quantile(.9) * 100), 플러스50이상=("fwd", lambda s: (s >= .5).mean() * 100), 플러스100이상=("fwd", lambda s: (s >= 1.0).mean() * 100))
base = H.agg({"fwd": ["median"]}); print("\n== (C) 12월 형성 상위 200 → 12개월 (2016~2024 형성, 단위 %) =="); print(o.round(1).to_string()); print("전체 기준 수익 중앙 %.1f%%, 하위10%% %.0f%%, −30%%↓ %.1f%%, 상위10%% %.0f%%, +50%%↑ %.1f%%, +100%%↑ %.1f%%" % (H.fwd.median() * 100, H.fwd.quantile(.1) * 100, (H.fwd <= -.3).mean() * 100, H.fwd.quantile(.9) * 100, (H.fwd >= .5).mean() * 100, (H.fwd >= 1).mean() * 100))
yy = H[H.상태.isin(["상승 지속", "상단 정체", "상단 후 하락"])].pivot_table(index="y", columns="상태", values="ex", aggfunc="median") * 100; print("\n연도별 초과 중앙(%):"); print(yy.round(1).to_string())
