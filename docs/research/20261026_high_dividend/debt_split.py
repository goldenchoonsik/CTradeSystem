"""부채비율(부채/자본)별로 금리 초과 배당 종목의 이후 배당 추적. dividend_path_{kr,us}.csv 에 재무를 붙인다.
한국: fundamentals_q 연간(q=4, 연결 우선) 중 형성일 이전 접수된 최신. 미국: SEC companyfacts(10-K) 형성일 이전 제출 최신 — 파일 있는 종목만(현재 S&P500·나스닥100 풀).
실행: .venv/bin/python -I docs/research/20261026_high_dividend/debt_split.py"""
import os, json, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
H = os.path.dirname(os.path.abspath(__file__)); V = "/home/kuzimo/projects/CTradeSystem/DataVault/staging"
def show(df, label, bins, names):
    print(label)
    for nm, lo, hi in zip(names, bins[:-1], bins[1:]):
        z = df[(df.dr >= lo) & (df.dr < hi)]
        line = []
        for h in (1, 2, 3):
            s = z[z.h == h].ratio.dropna()
            line.append(f"+{h}y n={len(s):3d} 중앙 {s.median()*100:4.0f} 삭감·중단 {100*(s<0.8).mean():3.0f}%" if len(s) >= 8 else f"+{h}y n={len(s):3d} (표본 부족)")
        print(f"  {nm:14s}| " + " | ".join(line))
# ---- 한국
fq = pd.read_parquet(f"{V}/kr_equity/derived/fundamentals_q.parquet")
fq = fq[(fq.q == 4) & (fq.equity > 0) & (fq.liabilities > 0)].copy()
fq["rcept_dt"] = pd.to_datetime(fq.rcept_dt.astype(str), errors="coerce"); fq = fq.sort_values(["stock_code", "fs_div", "rcept_dt"])
fq["dr"] = fq.liabilities / fq.equity * 100
fq = fq.sort_values("fs_div").drop_duplicates(["stock_code", "bsns_year"])   # CFS(연결) 우선
fqg = {c: g.sort_values("rcept_dt") for c, g in fq.groupby("stock_code")}
k = pd.read_csv(f"{H}/dividend_path_kr.csv", dtype={"code": str})
def kr_dr(code, y):
    g = fqg.get(code[1:])
    if g is None: return np.nan
    g = g[g.rcept_dt <= pd.Timestamp(f"{y}-05-01")]
    return g.dr.iloc[-1] if len(g) else np.nan
cache = {}
k["dr"] = [cache.setdefault((c, y), kr_dr(c, y)) for c, y in zip(k.code, k.y)]
print("한국 부채비율 연결: 전체 중앙값", round(k.dr.median()), "| 결측", int(k.dr.isna().sum()), "/", len(k))
k = k[k.dr.notna() & (k.dr < 1000)]      # 금융(부채비율 1000%+) 제외 — 은행·증권은 구조상 높음
bins = [0, 50, 100, 200, 1e9]; names = ["<50% 저부채", "50~100%", "100~200%", "200%+ 고부채"]
for lab, sub in (("고금리 형성(2022~25)", k[k.y >= 2022]), ("전체 형성 2016~25", k)):
    show(sub, f"\n[한국 비금융·{lab}] 직전 회계연도 DPS=100 기준", bins, names)
    print("  (참고: 종목-연 수", len(sub[sub.h == 1]), ")")
# ---- 미국
def load_cf(t):
    fp = f"{V}/us_fundamentals_sec/raw/companyfacts/{t}.json"
    if not os.path.exists(fp): return None
    j = json.load(open(fp)); g = j.get("facts", {}).get("us-gaap", {})
    def ser(tag):
        if tag not in g: return None
        r = [(pd.Timestamp(x["filed"]), pd.Timestamp(x["end"]), x["val"]) for x in g[tag]["units"].get("USD", []) if x.get("form") in ("10-K", "10-K/A") and x.get("fp") == "FY"]
        return pd.DataFrame(r, columns=["filed", "end", "val"]).sort_values("filed") if r else None
    L, E, A = ser("Liabilities"), ser("StockholdersEquity"), ser("Assets")
    return L, E, A
u = pd.read_csv(f"{H}/dividend_path_us.csv"); u = u[~u.gone]
cfc = {}
def us_dr(t, y):
    key = t.replace(".", "-")
    if key not in cfc: cfc[key] = load_cf(key)
    c = cfc[key]
    if c is None: return np.nan
    L, E, A = c; d0 = pd.Timestamp(f"{y}-01-01")
    def last(s):
        if s is None: return np.nan
        s = s[s.filed <= d0]
        return s.val.iloc[-1] if len(s) else np.nan
    e = last(E); l = last(L)
    if l != l: a = last(A); l = a - e if a == a and e == e else np.nan
    return 100 * l / e if (e == e and e > 0 and l == l) else np.nan
u["dr"] = [us_dr(t, y) for t, y in zip(u.t, u.y)]
print("\n미국: 부채비율 확보", int(u.dr.notna().sum()), "/", len(u), "(SEC XBRL 은 2009~ → 2001~08 형성은 없음)")
bins = [0, 100, 200, 400, 1e9]; names = ["<100% 저부채", "100~200%", "200~400%", "400%+ 고부채"]
us = u[u.dr.notna() & (u.dr < 2000)]
print("미국 연도 분포", us.drop_duplicates(["y"]).y.tolist())
show(us[us.y >= 2023], "\n[미국 · 고금리 형성(2023~25)] 직전 12개월 배당=100", bins, names)
show(us, "\n[미국 · 확보된 전체 형성연도]", bins, names)
k.to_csv(f"{H}/debt_split_kr.csv", index=False); us.to_csv(f"{H}/debt_split_us.csv", index=False)
