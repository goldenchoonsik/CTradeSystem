"""외국인·기관은 실적 정보를 먼저 아는가 — 첫 공개일 E(잠정/정기, kr_pead events) 직전 순매수 vs 서프라이즈(SUE)·공개 반응·사후 수급. 상위 300(시총 순위) 사건, 2016~2025, 사건 시즌(분기)별 순위상관 평균.
순매수 = 외국인 보유수량 변화 / 상장주식수(%), 기관 = inst_net_qty 합 / 상장주식수(%). 지분율 자료는 현재 상장 종목 위주.
  .venv/bin/python docs/research/20261024_foreign_accum/info_advantage.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); V = f"{ROOT}/DataVault/staging/kr_equity/derived"
ext = pd.read_parquet(f"{V}/daily_ext.parquet", columns=["code", "date", "shares_listed", "foreign_hold_qty", "inst_net_qty"])
px = pd.read_parquet(f"{V}/prices_1d.parquet", columns=["code", "date", "close", "factor"]).dropna(subset=["close"])
d = ext.merge(px, on=["code", "date"]); d = d[d.date >= "2015-06-01"]
fz = d.pivot(index="date", columns="code", values="foreign_hold_qty").astype("float32"); sh = d.pivot(index="date", columns="code", values="shares_listed").astype("float32")
ib = d.pivot(index="date", columns="code", values="inst_net_qty").astype("float32"); cl = d.pivot(index="date", columns="code", values="close").astype("float32"); fc = d.pivot(index="date", columns="code", values="factor").astype("float32")
dates = fz.index; col = {c: i for i, c in enumerate(fz.columns)}; A = {k: v.values for k, v in dict(fz=fz, sh=sh, ib=ib, cl=cl, fc=fc).items()}
mc = (sh * cl).values
ev = pd.read_parquet(f"{ROOT}/docs/research/20261021_kr_pead/events.parquet", columns=["code", "E", "sue", "src"]).dropna(subset=["sue"]); ev = ev[(ev.E >= "2016-06-01") & (ev.E <= "2025-11-30") & ev.code.isin(col)]
rows = []
for r in ev.itertuples():
    j = col[r.code]; i = dates.searchsorted(r.E)
    if i < 70 or i + 25 >= len(dates) or dates[i] != r.E: continue
    rank_ok = mc[i - 1, j]
    g = lambda k, a, b: A[k][a, j] - A[k][b, j]
    shs = A["sh"][i - 1, j]
    if not np.isfinite(shs) or shs <= 0: continue
    cs = lambda a, b: np.nansum(A["ib"][a:b + 1, j])
    # 기업행위(주식수·수정계수 변화) 구간 제외
    if np.nanmax(np.abs(np.diff(A["sh"][i - 61:i + 6, j]))) > 0 or np.nanmax(np.abs(np.diff(A["fc"][i - 61:i + 6, j]))) > 0: continue
    c = A["cl"]
    rows.append(dict(code=r.code, E=r.E, sue=r.sue, mc=rank_ok, F5=g("fz", i - 1, i - 6) / shs * 100, F20=g("fz", i - 1, i - 21) / shs * 100, F60=g("fz", i - 21, i - 61) / shs * 100, I5=cs(i - 5, i - 1) / shs * 100, I20=cs(i - 20, i - 1) / shs * 100, I60=cs(i - 60, i - 21) / shs * 100,
                     Rpre20=c[i - 1, j] / c[i - 21, j] - 1, Rann=c[i + 1, j] / c[i - 1, j] - 1, Rpost20=c[i + 21, j] / c[i + 1, j] - 1, Fpost5=g("fz", i + 5, i) / shs * 100, Fpost20=g("fz", i + 20, i) / shs * 100, Ipost5=cs(i + 1, i + 5) / shs * 100))
X = pd.DataFrame(rows); X["ym"] = X.E.dt.to_period("Q"); X["rank"] = X.groupby("ym").mc.rank(ascending=False); X = X[X["rank"] <= 300].copy()
print("사건 수(상위 300, 기업행위 제외):", len(X), "| 분기 시즌 수:", X.ym.nunique(), "| 연도:", X.E.dt.year.min(), "~", X.E.dt.year.max())
def xs(a, b, ctrl=None):
    rs = []
    for q, z in X.groupby("ym"):
        z = z.dropna(subset=[a, b] + ([ctrl] if ctrl else []))
        if len(z) < 60: continue
        if ctrl:
            r = z[[a, b, ctrl]].rank(); M = np.column_stack([np.ones(len(r)), r[ctrl]]); ra = r[a] - M @ np.linalg.lstsq(M, r[a], rcond=None)[0]; rb = r[b] - M @ np.linalg.lstsq(M, r[b], rcond=None)[0]; rs.append(np.corrcoef(ra, rb)[0, 1])
        else: rs.append(z[a].rank().corr(z[b].rank()))
    rs = np.array(rs); return rs.mean(), rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))), (rs > 0).mean() * 100, len(rs)
def show(lab, a, b, ctrl=None):
    m, t, p, n = xs(a, b, ctrl); print(f"{lab:48s} 상관 {m:+.3f}  t {t:+.1f}  양수시즌 {p:.0f}% ({n}시즌)")
print("\n== 발표 전 순매수가 서프라이즈(SUE)를 맞추는가 ==")
for k, lab in (("F60", "외국인 −60~−21일"), ("F20", "외국인 −20~−1일"), ("F5", "외국인 −5~−1일"), ("I60", "기관 −60~−21일"), ("I20", "기관 −20~−1일"), ("I5", "기관 −5~−1일")): show(f"{lab} → SUE", k, "sue")
print("\n== 발표 전 순매수 → 공개 반응(E−1→E+1 수익) ==")
for k, lab in (("F20", "외국인 −20~−1일"), ("F5", "외국인 −5~−1일"), ("I20", "기관 −20~−1일"), ("I5", "기관 −5~−1일")): show(f"{lab} → 반응", k, "Rann"); show(f"{lab} → 반응 (사전 수익 통제)", k, "Rann", "Rpre20")
print("\n== 참고: 서프라이즈 자체 → 반응 / 사전 수익 → SUE ==")
show("SUE → 공개 반응", "sue", "Rann"); show("사전 20일 수익 → SUE", "Rpre20", "sue"); show("외국인 20일 → 사전 20일 수익", "F20", "Rpre20"); show("기관 20일 → 사전 20일 수익", "I20", "Rpre20")
print("\n== 발표 후 수급 반응 ==")
for k, lab in (("Fpost5", "외국인 E~E+5일 지분 변화"), ("Ipost5", "기관 E+1~E+5일 순매수")): show(f"SUE → {lab}", "sue", k)
show("외국인 E~E+5 → 사후 20일 수익", "Fpost5", "Rpost20")
print("\n== 큰 서프라이즈만(SUE 상·하위 10%) 사전 외국인 20일 순매수 평균(%, 상장주식수 대비) ==")
X["sq"] = X.groupby("ym").sue.transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False)); t = X.groupby("sq").agg(건수=("sue", "size"), 외국인20일=("F20", "mean"), 기관20일=("I20", "mean"), 사전수익20=("Rpre20", lambda s: s.mean() * 100), 반응=("Rann", lambda s: s.mean() * 100)); print(t.round(3).to_string())
X.to_csv(f"{ROOT}/docs/research/20261024_foreign_accum/info_advantage_events.csv", index=False)
