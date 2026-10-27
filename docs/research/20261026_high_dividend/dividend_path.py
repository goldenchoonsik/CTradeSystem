"""금리 초과 배당률로 산 종목의 이후 1·2·3년 배당 추적 (형성 직전 12개월 배당 = 100).
미국: us_stock.py 와 같은 명단·유니버스, 형성 1월. 한국: kr_stock.py 와 같은 유니버스, 형성 5월(회계연도 DPS, 분할 단위 환산).
고금리 형성 = 해당 시장 표본 연도 중 금리 상위 1/3, 저금리 = 하위 1/3. 상폐·자료 없음은 '사라짐'으로 따로 센다. 실행: .venv/bin/python -I docs/research/20261026_high_dividend/dividend_path.py"""
import os, sys, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
def summarize(df, label):
    out = []
    for h in (1, 2, 3):
        z = df[df.h == h]
        if len(z) == 0: continue
        r = z.ratio
        out.append(f"  +{h}년: 종목 {len(z):3d} | 중앙값 {r.median()*100:5.0f} | 늘림(≥105) {100*(r>=1.05).mean():3.0f}% 비슷(80~105) {100*((r>=0.8)&(r<1.05)).mean():3.0f}% 20%+삭감 {100*((r<0.8)&(r>0)).mean():3.0f}% 중단(0) {100*(r==0).mean():3.0f}%")
    print(label); print("\n".join(out) if out else "  (표본 없음)")

# ---------------- 미국
RP = "/home/kuzimo/projects/CTradeSystem/docs/research/20261021_random_portfolio_vs_qqq"
src = open(f"{HERE}/us_stock.py", encoding="utf-8").read()
ns = {"__file__": f"{HERE}/us_stock.py"}
exec(src[:src.index("rows = []; years")], ns)
get, paid, cal, memb, rate, yahoo_ticker, REIT, load = (ns[k] for k in ("get", "paid", "cal", "memb", "rate", "yahoo_ticker", "REIT", "load"))
rows = []
for y in range(2001, 2026):
    d0 = cal[cal >= pd.Timestamp(f"{y}-01-01")][0]; r10 = rate[rate.index < d0].iloc[-1]
    m = memb[memb.date <= d0].iloc[-1].tickers.split(",")
    cand = []
    for t in m:
        yt = yahoo_ticker(t, d0)
        if not yt: continue
        g = get(yt)
        if g is None or d0 not in g[0].index: continue
        cand.append((t, yt, g))
    floor = 0.10 * np.nanmedian([g[0]["dvol_60d"].loc[d0] for _, _, g in cand])
    for t, yt, (df, dv) in cand:
        if df["dvol_60d"].loc[d0] < floor or t in REIT or yt in REIT: continue
        p0 = df["close"].loc[d0]; base = paid(dv, d0 - pd.Timedelta(days=365), d0 - pd.Timedelta(days=1))
        if base <= 0 or 100 * base / p0 <= r10: continue
        for h in (1, 2, 3):
            a = d0 + pd.DateOffset(years=h - 1); b = d0 + pd.DateOffset(years=h)
            if b > pd.Timestamp("2026-01-05"): continue
            alive = df.index[-1] >= b - pd.Timedelta(days=15)
            if not alive: rows.append(dict(y=y, h=h, t=t, ratio=np.nan, gone=True, r10=r10)); continue
            rows.append(dict(y=y, h=h, t=t, ratio=paid(dv, a - pd.Timedelta(days=1) if h > 1 else a - pd.Timedelta(days=1), b - pd.Timedelta(days=1)) / base, gone=False, r10=r10))
u = pd.DataFrame(rows); q = u.drop_duplicates("y").set_index("y").r10
q3 = q.quantile([1/3, 2/3]).values
u["reg"] = u.r10.apply(lambda v: "고금리" if v >= q3[1] else ("저금리" if v <= q3[0] else "중간"))
print("==== 미국: 금리 초과 배당 종목(리츠 제외)의 이후 배당 (형성 직전 12개월 = 100) ====")
for reg in ("고금리", "중간", "저금리"):
    z = u[u.reg == reg]; yrs = sorted(z.y.unique())
    summarize(z[~z.gone], f"[{reg}에 산 종목, 형성연도 {yrs[0]}~{yrs[-1]} {len(yrs)}개년]")
    print("  (사라진 종목 수:", int(z.gone.sum()), "/ 추적 대상", len(z), ")")
hi = u[(u.reg == "고금리") & (u.y <= 2008)]; summarize(hi[~hi.gone], "[고금리 2001~08 형성만]")
hi = u[(u.reg == "고금리") & (u.y >= 2023)]; summarize(hi[~hi.gone], "[고금리 2023~25 형성만]")
for yy in (2001, 2002, 2004, 2005, 2006, 2007, 2008, 2023):
    z = u[(u.y == yy)]; 
    s = z[(z.h == 3) & ~z.gone].ratio if yy <= 2022 else z[(z.h == 2) & ~z.gone].ratio
    if len(s): print(f"   {yy} 형성 {int(len(z[z.h==1]))}종목 → {3 if yy<=2022 else 2}년 뒤 중앙값 {s.median()*100:.0f}, 20%+삭감·중단 {100*(s<0.8).mean():.0f}%")

# ---------------- 한국
ksrc = open(f"{HERE}/kr_stock.py", encoding="utf-8").read()
kn = {"__file__": f"{HERE}/kr_stock.py"}
exec(ksrc[:ksrc.index("def is_reit")], kn)
dps, byc, cal_k, rate_k = kn["dps"], kn["byc"], kn["cal"], kn["rate"]
kr = pd.read_csv(f"{HERE}/kr_stock_rows.csv", dtype={"code": str}); kr = kr[~kr.reit & (kr.yld > kr.r3)]
rows = []
for k, g in kr.groupby("y"):
    d0 = pd.Timestamp(cal_k[cal_k >= np.datetime64(f"{k}-05-01")][0])
    for code, r in zip(kr[kr.y == k].code, kr[kr.y == k].itertuples()):
        st = code[1:]; gg = byc[code]; cf0 = gg.loc[d0, "cf"]
        def D(fy):
            v = dps.get((st, fy))
            if v is None: return np.nan
            tr = pd.Timestamp(f"{v[1] + 1}-04-01"); cg = gg["cf"][gg.index <= tr]
            return v[0] * (cg.iloc[-1] if len(cg) else gg["cf"].iloc[-1]) / cf0
        base = D(k - 1)
        if not base > 0: continue
        for h in (1, 2, 3):
            fy = k - 1 + h
            if fy > 2025: continue
            v = D(fy)
            gone = (gg.index[-1] < pd.Timestamp(f"{fy}-12-27"))
            rows.append(dict(y=k, h=h, code=code, ratio=(v / base if v == v else np.nan), gone=gone, r3=r.r3))
ku = pd.DataFrame(rows)
ku["reg"] = ku.r3.apply(lambda v: "고금리" if v >= kr.drop_duplicates("y").r3.quantile(2/3) else ("저금리" if v <= kr.drop_duplicates("y").r3.quantile(1/3) else "중간"))
print("\n==== 한국: 금리 초과 배당 종목(시총 상위 500, 리츠 제외)의 이후 배당 (직전 회계연도 DPS = 100, 상폐 포함) ====")
for reg in ("고금리", "중간", "저금리"):
    z = ku[ku.reg == reg]; yrs = sorted(z.y.unique())
    ok = z[z.ratio.notna()]
    summarize(ok, f"[{reg}에 산 종목, 형성연도 {yrs}]")
    print("  (상폐·소멸:", int(z.gone.sum()), "/ 자료 없음(공시 못 찾음):", int((z.ratio.isna() & ~z.gone).sum()), "/ 전체", len(z), ")")
for kk in (2022, 2023, 2024):
    z = ku[(ku.y == kk) & ku.ratio.notna()]
    for h in (1, 2, 3):
        s = z[z.h == h].ratio
        if len(s): print(f"   {kk} 형성 +{h}년: {len(s)}종목 중앙값 {s.median()*100:.0f}, 20%+삭감·중단 {100*(s<0.8).mean():.0f}%")
u.to_csv(f"{HERE}/dividend_path_us.csv", index=False); ku.to_csv(f"{HERE}/dividend_path_kr.csv", index=False)
