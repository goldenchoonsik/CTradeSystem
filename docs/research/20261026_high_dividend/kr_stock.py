"""한국 금리 초과 배당 전략 H1·H3·T vs 동일 유니버스 동일가중 B vs 금리 R (사전등록). 리츠는 제외하고 따로 본다.
형성 = 매년 5월 첫 거래일 2016~2025, 12개월 보유, 배당 15.4% 원천징수 후, 왕복비용 0.27% (모두 동일), 상폐 포함, 시총 상위 500.
배당 = DART alotMatter 보통주 주당 현금배당금(원). 보유창 k 의 배당 = 회계연도 k 의 DPS(결산 기준일 12월이든 2023~ 4월이든 5월~다음 4월 안).
실행: .venv/bin/python -I docs/research/20261026_high_dividend/kr_stock.py"""
import os, sys, json, glob, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem")
from shared.data_library.kr_equity import store
HERE = os.path.dirname(os.path.abspath(__file__))
S = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/kr_equity"
WH, COST, TOPN = 0.846, 0.0027, 500

def num(x):
    x = str(x).replace(",", "").strip()
    try: return float(x)
    except: return np.nan

corp_stock = store.universe_corps()                    # corp_code → 보통주 6자리
dps = {}                                               # (stock, FY) → (보통주 주당 현금배당금(원), 그 값이 실린 보고서 연도) — 회계연도마다 가장 이른 보고서 값
for corp, stock in corp_stock.items():
    for fp in glob.glob(f"{S}/raw/dart/alot_matter/{corp}/*.json"):
        j = json.load(open(fp, encoding="utf-8"))["response"]
        Y = int(os.path.basename(fp)[:4])
        if j.get("status") != "000": continue
        for x in j["list"]:
            if x["se"].startswith("주당 현금배당금") and x.get("stock_knd") in ("보통주", "-", None):
                for k, v in zip((Y, Y - 1, Y - 2), (x["thstrm"], x["frmtrm"], x["lwfr"])):
                    d = num(v)
                    d = 0.0 if (d != d and str(v).strip() in ("-", "")) else d
                    if (stock, k) not in dps or Y < dps[(stock, k)][1]:
                        dps[(stock, k)] = (d, Y)
uni = pd.read_csv(f"{S}/universe.csv", dtype=str).set_index("code")
px = pd.read_parquet(f"{S}/derived/prices_1d.parquet", columns=["code", "date", "close", "close_unadj"])
px["date"] = pd.to_datetime(px["date"]); px = px.sort_values(["code", "date"])
ext = pd.read_parquet(f"{S}/derived/daily_ext.parquet", columns=["code", "date", "market_cap"]); ext["date"] = pd.to_datetime(ext["date"])
rate = pd.read_csv("/home/kuzimo/projects/CTradeSystem/DataVault/staging/global_macro/series/kr_3y.csv", parse_dates=["date"]).set_index("date")["value"]
px["cf"] = px["close"] / px["close_unadj"]          # 이후 분할·권리 등으로 소급 조정된 정도(최신 기준 단위로 환산하는 계수)
cal = np.sort(px["date"].unique())
byc = {c: g.set_index("date") for c, g in px.groupby("code")}
extd = {c: g.set_index("date")["market_cap"] for c, g in ext.groupby("code")}

def is_reit(name): return (("리츠" in name) and "메리츠" not in name and "블리츠" not in name) or ("위탁관리" in name) or ("부동산투자" in name)
rows = []
for k in range(2016, 2026):
    d0 = pd.Timestamp(cal[cal >= np.datetime64(f"{k}-05-01")][0]); d1 = pd.Timestamp(cal[cal >= np.datetime64(f"{k+1}-05-01")][0]) if k < 2025 else pd.Timestamp(cal[-1])
    if k == 2025: d1 = pd.Timestamp(cal[cal >= np.datetime64("2026-05-01")][0]) if cal[-1] >= np.datetime64("2026-05-01") else pd.Timestamp(cal[-1])
    r3 = rate[rate.index < d0].iloc[-1]
    cand = []
    for code, g in byc.items():
        if code not in uni.index: continue
        u = uni.loc[code]
        if u.is_preferred == "1" or u.is_spac == "1" or code[-1] != "0": continue
        if d0 not in g.index: continue
        mc = extd.get(code); 
        if mc is None: continue
        m = mc[mc.index <= d0]
        if len(m) == 0 or m.index[-1] < d0 - pd.Timedelta(days=10): continue
        cand.append((code, u["name"], m.iloc[-1], g))
    cand.sort(key=lambda z: -z[2]); cand = [c for c in cand if not is_reit(c[1])][:TOPN] + [c for c in cand if is_reit(c[1])]   # 리츠는 시총 컷 없이 전부
    for code, name, mcap, g in cand:
        st = code[1:]
        p0u, p0 = g.loc[d0, "close_unadj"], g.loc[d0, "close"]
        gg = g.loc[:d1]; endd = gg.index[-1]; p1 = gg.loc[endd, "close"]
        gone = endd < d1 - pd.Timedelta(days=10)
        cf0 = g.loc[d0, "cf"]
        def D(fy):      # 형성일 주식 단위로 환산한 DPS (보고서 시점 계수 / 형성일 계수)
            v = dps.get((st, fy))
            if v is None: return np.nan
            tr = pd.Timestamp(f"{v[1] + 1}-04-01"); cg = g["cf"][g.index <= tr]
            return v[0] * (cg.iloc[-1] if len(cg) else g["cf"].iloc[-1]) / cf0
        d_a = D(k - 1); d_b = D(k - 2); d_c = D(k - 3)
        d_now = D(k)
        divret = 0.0 if (gone and endd < pd.Timestamp(f"{k}-12-27")) else (0.0 if d_now != d_now else d_now / p0u)
        rows.append(dict(y=k, code=code, name=name, reit=is_reit(name), mcap=mcap, yld=100 * (0 if d_a != d_a else d_a) / p0u, has_dps=(d_a == d_a), r3=r3,
                         steady=bool(d_a > 0 and d_b > 0 and d_c > 0 and d_a >= 0.97 * d_b and d_b >= 0.97 * d_c) if (d_a == d_a and d_b == d_b and d_c == d_c) else False,
                         ret=(p1 / p0 - 1) + WH * divret, cut=bool(d_a > 0 and d_now == d_now and d_now < 0.8 * d_a), gone=gone, nodps_next=(d_now != d_now)))
df = pd.DataFrame(rows); df.to_csv(f"{HERE}/kr_stock_rows.csv", index=False)
out = []
for y, g in df.groupby("y"):
    r3 = g.r3.iloc[0]
    for grp, gg in (("주식", g[~g.reit]), ("리츠", g[g.reit])):
        def P(sel):
            s = gg[sel(gg)]; return (s.ret.mean() - COST) if len(s) else np.nan, len(s)
        B, nB = P(lambda s: s.ret == s.ret)
        H1, n1 = P(lambda s: (s.yld > r3))
        H3, n3 = P(lambda s: (s.yld > r3) & s.steady)
        pay = gg[gg.yld > 0]; q = pay.yld.quantile(0.8) if len(pay) else np.inf
        T, nT = P(lambda s: (s.yld >= q) & (s.yld > 0))
        out.append(dict(y=y, grp=grp, r3=r3, nB=nB, B=B, n1=n1, H1=H1, n3=n3, H3=H3, nT=nT, T=T, R=r3 / 100 * 1.0,
                        cut1=gg[gg.yld > r3].cut.mean() if n1 else np.nan, dpsmiss=gg.nodps_next.mean(), gone1=gg[gg.yld > r3].gone.mean() if n1 else np.nan))
o = pd.DataFrame(out); o.to_csv(f"{HERE}/kr_stock_yearly.csv", index=False)
q3 = o.drop_duplicates("y").r3.quantile([1/3, 2/3]).values
o["reg"] = o.r3.apply(lambda v: "고금리" if v >= q3[1] else ("저금리" if v <= q3[0] else "중간"))
pd.set_option("display.width", 250)
print("KR 3Y 3분위 경계", np.round(q3, 2))
for grp in ("주식", "리츠"):
    s = o[o.grp == grp]
    print(f"\n==== 한국 {grp} 연도별 % ====")
    t = s.set_index("y")[["r3", "nB", "n1", "n3", "B", "H1", "H3", "T", "R", "cut1", "gone1", "dpsmiss"]].copy()
    for c in ["B", "H1", "H3", "T", "R", "cut1", "gone1", "dpsmiss"]: t[c] = t[c] * 100
    print(t.round(1).to_string())
    for reg in ("고금리", "중간", "저금리", None):
        z = s if reg is None else s[s.reg == reg]
        lab = "전체" if reg is None else reg
        res = {}
        for kk in ("H1", "H3", "T"):
            e = (z[kk] - z["B"]).dropna()
            res[kk] = f"{z[kk].mean()*100:5.1f}% (B대비 {e.mean()*100:+5.1f}%p, 승 {int((e>0).sum())}/{len(e)})"
        print(f"[{grp}|{lab} {len(z)}년] B {z.B.mean()*100:5.1f}% 금리 {z.r3.mean():4.1f}% | H1 {res['H1']} | H3 {res['H3']} | T {res['T']}")
