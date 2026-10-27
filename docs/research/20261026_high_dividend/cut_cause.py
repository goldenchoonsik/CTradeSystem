"""배당이 줄어든 원인: 순이익(실적) 때문인가. 한국 시총 상위 500 배당 종목(2016~25)과 미국 S&P500(2011~25).
배당 감소 = 해당 회계연도 DPS < 직전 회계연도의 80%. 순이익 변화는 같은 회계연도 대 직전. 실행: .venv/bin/python -I docs/research/20261026_high_dividend/cut_cause.py"""
import os, json, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
H = os.path.dirname(os.path.abspath(__file__)); V = "/home/kuzimo/projects/CTradeSystem/DataVault/staging"
src = open(f"{H}/kr_stock.py", encoding="utf-8").read(); kn = {"__file__": f"{H}/kr_stock.py"}
exec(src[:src.index("def is_reit")], kn)
dps, byc, cal = kn["dps"], kn["byc"], kn["cal"]
fq = pd.read_parquet(f"{V}/kr_equity/derived/fundamentals_q.parquet"); fq = fq[fq.q == 4].sort_values("fs_div").drop_duplicates(["stock_code", "bsns_year"])
NI = fq.set_index(["stock_code", "bsns_year"]).net_income_annual
rows = []
kr = pd.read_csv(f"{H}/kr_stock_rows.csv", dtype={"code": str}); kr = kr[~kr.reit]
for r in kr.itertuples():
    k, code = r.y, r.code; st = code[1:]; g = byc[code]
    d0 = pd.Timestamp(cal[cal >= np.datetime64(f"{k}-05-01")][0]); cf0 = g.loc[d0, "cf"]
    def D(fy):
        v = dps.get((st, fy))
        if v is None: return np.nan
        tr = pd.Timestamp(f"{v[1] + 1}-04-01"); cg = g["cf"][g.index <= tr]
        return v[0] * (cg.iloc[-1] if len(cg) else g["cf"].iloc[-1]) / cf0
    a, b, c = D(k - 1), D(k - 2), D(k)
    n1, n0 = NI.get((st, k), np.nan), NI.get((st, k - 1), np.nan)
    if not (a > 0 and c == c and n1 == n1 and n0 == n0): continue
    rows.append(dict(y=k, code=code, name=r.name, d_prev2=b, d_prev=a, d_now=c, cut=c < 0.8 * a, ni_now=n1, ni_prev=n0))
d = pd.DataFrame(rows)
def ni_cat(x):
    if x.ni_now < 0: return "1 적자"
    if x.ni_prev <= 0: return "4 이익 증가(적자→흑자)"
    ch = x.ni_now / x.ni_prev - 1
    return "2 이익 30%+ 감소" if ch <= -0.3 else ("3 이익 비슷(−30%~+10%)" if ch < 0.1 else "4 이익 증가(10%+)")
d["cat"] = d.apply(ni_cat, axis=1)
print(f"==== 한국 시총 상위 500 배당 종목 {len(d)}건(종목-연) | 전체 배당 20%+ 감소 {100*d.cut.mean():.1f}% ====")
t = d.groupby("cat").agg(건수=("cut", "size"), 감소비율=("cut", lambda s: 100 * s.mean()), 감소건수=("cut", "sum"))
t["전체 감소 중 비중%"] = 100 * t.감소건수 / t.감소건수.sum(); print(t.round(1).to_string())
c = d[d.cut].copy()
def cause(x):
    if x.ni_now < 0 or x.ni_now < 0.7 * x.ni_prev: return "실적 악화(적자·이익 30%+ 감소)"
    if x.d_prev >= 1.5 * x.d_prev2 and x.d_prev2 > 0: return "직전 해 배당 급증(1.5배+)의 정상화"
    return "이익은 유지·배당만 감소(정책·기타)"
c["원인"] = c.apply(cause, axis=1)
print("\n[배당 감소 사건의 원인 분류]"); print(c.원인.value_counts().to_frame("건수").assign(비중=lambda z: (100 * z.건수 / z.건수.sum()).round(1)).to_string())
print("\n[고금리 형성 연도(2022~25) 한정]"); cc = c[c.y >= 2022]; print(cc.원인.value_counts().to_frame("건수").assign(비중=lambda z: (100 * z.건수 / z.건수.sum()).round(1)).to_string())
print("\n[연도별 감소 비율 vs 순이익 감소 종목 비율]")
yy = d.groupby("y").agg(감소=("cut", lambda s: 100 * s.mean()), 이익감소=("ni_now", lambda s: np.nan)); 
yy["이익감소"] = d.groupby("y").apply(lambda z: 100 * ((z.ni_now < z.ni_prev * 0.9)).mean()); print(yy.round(1).T.to_string())
print("상관(연도별):", round(yy.감소.corr(yy.이익감소), 2))
d.to_csv(f"{H}/cut_cause_kr.csv", index=False)
# ---- 미국
u = pd.read_csv(f"{H}/us_stock_rows.csv"); u = u[~u.reit & (u.yld > 0)]
def ser(t):
    fp = f"{V}/us_fundamentals_sec/raw/companyfacts/{t.replace('.', '-')}.json"
    if not os.path.exists(fp): return None
    g = json.load(open(fp)).get("facts", {}).get("us-gaap", {})
    for tag in ("NetIncomeLoss", "ProfitLoss"):
        if tag in g:
            out = {}
            for x in g[tag]["units"].get("USD", []):
                if x.get("form") not in ("10-K", "10-K/A") or "start" not in x: continue
                s, e = pd.Timestamp(x["start"]), pd.Timestamp(x["end"])
                if 340 <= (e - s).days <= 380: out[e.year if e.month > 3 else e.year - 1] = x["val"]
            if out: return out
    return None
cache = {}; rows = []
for r in u.itertuples():
    if r.t not in cache: cache[r.t] = ser(r.t)
    s = cache[r.t]
    if not s or r.y < 2011: continue
    n1, n0 = s.get(r.y), s.get(r.y - 1)
    if n1 is None or n0 is None: continue
    rows.append(dict(y=r.y, t=r.t, cut=r.cut, ni_now=n1, ni_prev=n0))
w = pd.DataFrame(rows); w["cat"] = w.apply(ni_cat, axis=1)
print(f"\n==== 미국 S&P500 배당 종목(리츠 제외, 2011~25, SEC 순이익 있는 {len(w)}건) | 전체 배당 20%+ 감소 {100*w.cut.mean():.1f}% ====")
t = w.groupby("cat").agg(건수=("cut", "size"), 감소비율=("cut", lambda s: 100 * s.mean()), 감소건수=("cut", "sum")); t["전체 감소 중 비중%"] = 100 * t.감소건수 / t.감소건수.sum()
print(t.round(1).to_string())
