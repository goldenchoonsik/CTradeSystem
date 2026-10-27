"""정치·전쟁·폭락·윈도우드레싱·월요일 효과. S&P500(^GSPC 일봉 1927-12~2026-10, 가격수익, DataVault/staging/us_valuation/raw/yahoo) 중심, SPY/코스피 보조.
  .venv/bin/python docs/research/20261026_market_anomalies/events_fx.py"""
import os, json, numpy as np, pandas as pd
from math import sqrt
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
j = json.load(open(f"{ROOT}/DataVault/staging/us_valuation/raw/yahoo/GSPC_20261023.json"))["chart"]["result"][0]
P = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna(); P = P[~P.index.duplicated()]
R = P.pct_change().dropna(); print("GSPC", P.index[0].date(), P.index[-1].date(), len(P))
def t2(a, b): return (a.mean() - b.mean()) / sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
def elec(y):
    d = pd.Timestamp(f"{y}-11-02")
    while d.weekday() != 1: d += pd.Timedelta(days=1)
    return d
def px(d, side="before"):
    i = P.index.searchsorted(d); return (i - 1 if side == "before" else i)
yr = (P.resample("YE").last() / P.resample("YE").last().shift(1) - 1).dropna(); yr.index = yr.index.year
print("\n=== 1. 임기 연차별 연수익 (1929~2025, 연차 = (연도−1929)%4+1, 4년차=대선 해) ===")
t = pd.Series(((yr.index - 1929) % 4) + 1, index=yr.index); yy = yr[yr.index >= 1929]; tt = t[yy.index]
for k, v in yy.groupby(tt): print(f"  {k}년차: 평균 {v.mean()*100:5.1f}% 중앙 {v.median()*100:5.1f}% 양수 {(v>0).sum()}/{len(v)}")
for k, nm in ((3, "3년차"), (4, "대선 해")): a, b = yy[tt == k], yy[tt != k]; print(f"  {nm} vs 나머지: {a.mean()*100:.1f}% vs {b.mean()*100:.1f}%, t={t2(a,b):.2f}")
print("\n=== 2. 중간선거 해 (2년차, 1930~2022 24회): 구간별 ===")
rows = []
for y in range(1930, 2023, 4):
    s = R[f"{y}-01-01":f"{y}-09-30"]; q4 = R[f"{y}-10-01":f"{y}-12-31"]; n = R[f"{y+1}-01-01":f"{y+1}-12-31"]; e = elec(y); i = P.index.searchsorted(e)
    rows.append(dict(y=y, jan_sep=(1+s).prod()-1, vol=s.std()*sqrt(252), oct_dec=(1+q4).prod()-1, y3=(1+n).prod()-1, p6=P.iloc[i+126]/P.iloc[i]-1, p12=P.iloc[i+252]/P.iloc[i]-1 if i+252 < len(P) else np.nan, pre6=P.iloc[i]/P.iloc[i-126]-1))
M = pd.DataFrame(rows).set_index("y"); allj = [(1 + R[f"{y}-01-01":f"{y}-09-30"]).prod() - 1 for y in range(1929, 2026) if y % 4 != 2]; allv = [R[f"{y}-01-01":f"{y}-09-30"].std() * sqrt(252) for y in range(1929, 2026) if y % 4 != 2]
print(f"  1~9월 수익 평균 {M.jan_sep.mean()*100:.1f}% 중앙 {M.jan_sep.median()*100:.1f}% 양수 {(M.jan_sep>0).sum()}/{len(M)} (다른 해 1~9월 평균 {np.mean(allj)*100:.1f}%, 중앙 {np.median(allj)*100:.1f}%) | 변동성 {M.vol.mean()*100:.1f}% vs {np.mean(allv)*100:.1f}%")
base = lambda h: (P.shift(-h) / P - 1).dropna()
for c, h in (("pre6", 126), ("p6", 126), ("p12", 252)): x = M[c].dropna(); b = base(h); print(f"  선거 {'전' if c=='pre6' else '후'}{h//21}개월: 평균 {x.mean()*100:.1f}% 중앙 {x.median()*100:.1f}% 양수 {(x>0).sum()}/{len(x)} | 평소 평균 {b.mean()*100:.1f}% 중앙 {b.median()*100:.1f}% | t={(x.mean()-b.mean())/(x.std(ddof=1)/sqrt(len(x))):.2f}")
print(f"  10~12월 평균 {M.oct_dec.mean()*100:.1f}% 양수 {(M.oct_dec>0).sum()}/{len(M)} | 이듬해(3년차) 평균 {M.y3.mean()*100:.1f}% 양수 {(M.y3>0).sum()}/{len(M)}")
print("  기간 쪼개기: 1930~1970 / 1974~2022 선거 후 6개월 평균 %.1f%% / %.1f%%" % (M.p6[M.index <= 1970].mean()*100, M.p6[M.index > 1970].mean()*100))
print("\n=== 3. 레임덕 랠리: 대선일 이후 (정권교체 확정 vs 동일 정당) ===")
chg = [1932,1952,1960,1968,1976,1980,1992,2000,2008,2016,2020,2024]; same = [1928,1936,1940,1944,1948,1956,1964,1972,1984,1988,1996,2004,2012]
for nm, ys in (("정권교체", chg), ("동일정당", same)):
    rr = []
    for y in ys:
        i = P.index.searchsorted(elec(y)); rr.append((P.iloc[i+63]/P.iloc[i]-1, P.iloc[i+126]/P.iloc[i]-1 if i+126 < len(P) else np.nan, P.iloc[i]/P.iloc[i-63]-1))
    r_ = pd.DataFrame(rr, columns=["post3","post6","pre3"], index=ys); print(f"  {nm} n={len(ys)}: 후3개월 평균 {r_.post3.mean()*100:.1f}% 중앙 {r_.post3.median()*100:.1f}% 양수 {(r_.post3>0).sum()}/{len(r_)} | 후6개월 평균 {r_.post6.mean()*100:.1f}% | 전3개월 {r_.pre3.mean()*100:.1f}%")
b3, b6 = base(63), base(126); print(f"  평소 3개월 평균 {b3.mean()*100:.1f}% 중앙 {b3.median()*100:.1f}% / 6개월 평균 {b6.mean()*100:.1f}%")
print("\n=== 4. 전쟁·지정학 발발 (직전 종가 기준; 사전 지정 목록) ===")
W = [("1939-09-01","독일 폴란드 침공"),("1941-12-07","진주만"),("1950-06-25","한국전쟁"),("1964-08-02","통킹만"),("1973-10-06","욤키푸르"),("1982-04-02","포클랜드"),("1990-08-02","이라크 쿠웨이트 침공"),("1991-01-17","걸프전 공습"),("1999-03-24","코소보"),("2001-09-11","9/11"),("2001-10-07","아프간"),("2003-03-20","이라크전"),("2014-02-27","크림반도"),("2022-02-24","우크라이나"),("2023-10-07","하마스 공격"),("2025-06-13","이스라엘-이란")]
rows = []
for d, nm in W:
    i0 = P.index.searchsorted(pd.Timestamp(d)) - 1
    f = lambda h: P.iloc[i0+h]/P.iloc[i0]-1 if i0+h < len(P) else np.nan
    rows.append(dict(날짜=d, 사건=nm, 직전60일=P.iloc[i0]/P.iloc[i0-60]-1, d1=f(1), d5=f(5), d20=f(20), d60=f(60), d250=f(250), y5=(P.iloc[i0+1260]/P.iloc[i0]-1) if i0+1260 < len(P) else np.nan))
Wd = pd.DataFrame(rows); pd.set_option("display.width", 200)
print((Wd.set_index("날짜").drop(columns="사건").assign(사건=Wd.set_index("날짜").사건) * 1).round(3).to_string() if False else Wd.assign(**{c: (Wd[c]*100).round(1) for c in Wd.columns[2:]}).to_string(index=False))
for c, h in (("직전60일", 60), ("d1", 1), ("d5", 5), ("d20", 20), ("d60", 60), ("d250", 250), ("y5", 1260)):
    x = Wd[c].dropna(); b = base(h) if c != "직전60일" else base(60)
    print(f"  {c}: 평균 {x.mean()*100:5.1f}% 중앙 {x.median()*100:5.1f}% 양수 {(x>0).sum()}/{len(x)} | 평소 평균 {b.mean()*100:5.1f}% 중앙 {b.median()*100:5.1f}% 양수 {(b>0).mean()*100:.0f}%")
print("\n=== 5. 폭락 후 평균 회귀·약세장 지속 (S&P500 가격, 1928~) ===")
peak = P.cummax(); dd = P / peak - 1
for thr in (-0.10, -0.20, -0.30):
    ev, armed = [], True
    for i in range(len(P)):
        if dd.iloc[i] >= -0.001: armed = True
        if armed and dd.iloc[i] <= thr: ev.append(i); armed = False
    f = lambda h: np.array([P.iloc[i+h]/P.iloc[i]-1 for i in ev if i+h < len(P)])
    print(f"  고점 대비 {int(thr*100)}% 최초 도달 {len(ev)}회: 이후 1개월 {f(21).mean()*100:5.1f}%(양수 {(f(21)>0).mean()*100:.0f}%) 3개월 {f(63).mean()*100:5.1f}%({(f(63)>0).mean()*100:.0f}%) 12개월 {f(252).mean()*100:5.1f}%({(f(252)>0).mean()*100:.0f}%) 3년 {f(756).mean()*100:5.1f}%({(f(756)>0).mean()*100:.0f}%) | 평소 12개월 {base(252).mean()*100:.1f}%({(base(252)>0).mean()*100:.0f}%) 3년 {base(756).mean()*100:.1f}%")
big = R[R <= -0.07]; ev = [P.index.get_loc(d) for d in big.index]; ev2 = [i for k, i in enumerate(ev) if k == 0 or i - ev[k-1] > 60]
f = lambda h: np.array([P.iloc[i+h]/P.iloc[i]-1 for i in ev2 if i+h < len(P)]); print(f"  하루 −7% 이하(60일 내 중복 제외) {len(ev2)}회: 이후 1일 {f(1).mean()*100:.1f}% 20일 {f(20).mean()*100:.1f}%({(f(20)>0).mean()*100:.0f}%) 60일 {f(60).mean()*100:.1f}% 250일 {f(250).mean()*100:.1f}%({(f(250)>0).mean()*100:.0f}%) | 평소 250일 {base(250).mean()*100:.1f}%")
state, pk, pkd, tr, trd, bears, bulls, bull_start = "bull", P.iloc[0], P.index[0], None, None, [], [], P.index[0]
for d, v in P.items():
    if state == "bull":
        if v > pk: pk, pkd = v, d
        elif v <= pk * 0.8: bulls.append(((pkd - bull_start).days / 30.44, pk / P[bull_start] - 1)); state, tr, trd, bear_start = "bear", v, d, pkd
    else:
        if v < tr: tr, trd = v, d
        elif v >= tr * 1.2: bears.append(((trd - bear_start).days / 30.44, tr / P[bear_start] - 1)); state, pk, pkd, bull_start = "bull", v, d, trd
B = pd.DataFrame(bears, columns=["개월","낙폭"]); U = pd.DataFrame(bulls, columns=["개월","상승"])
print(f"  약세장(고점→저점, ±20% 규칙) {len(B)}회: 지속 평균 {B.개월.mean():.1f}개월 중앙 {B.개월.median():.1f} 범위 {B.개월.min():.1f}~{B.개월.max():.1f}, 낙폭 평균 {B.낙폭.mean()*100:.0f}% | 1950년 이후: 평균 {B.개월[-12:].mean():.1f}개월" if len(B) >= 12 else f"  약세장 {len(B)}회: 평균 {B.개월.mean():.1f}개월")
print(f"  강세장(저점→고점) {len(U)}회: 지속 평균 {U.개월.mean()/12:.1f}년 중앙 {U.개월.median()/12:.1f}년 범위 {U.개월.min()/12:.1f}~{U.개월.max()/12:.1f}년, 상승 평균 {U.상승.mean()*100:.0f}% (현재 진행 중 강세장 제외)")
print("\n=== 6. 윈도우 드레싱: 분기 말(3·6·9·12월) 마지막 3거래일 ===")
def lastn(R, n=3):
    g = R.groupby([R.index.year, R.index.month]); out = {}
    for (y, m), x in g: out[(y, m)] = (1 + x.iloc[-n:]).prod() - 1
    return pd.Series(out)
for nm, r in (("S&P500 1928~", R), ("S&P500 1993~", R[R.index >= "1993-01-01"]), ("S&P500 2013~", R[R.index >= "2013-01-01"])):
    s = lastn(r); q = s[[m in (3, 6, 9, 12) for (_, m) in s.index]]; o = s[[m not in (3, 6, 9, 12) for (_, m) in s.index]]; base3 = (1 + r.mean()) ** 3 - 1
    print(f"  {nm}: 분기말 마지막3일 평균 {q.mean()*100:.2f}% (양수 {(q>0).mean()*100:.0f}%, n={len(q)}) vs 비분기 월말 {o.mean()*100:.2f}% ({(o>0).mean()*100:.0f}%) | 평소 3일 {base3*100:.2f}% | t(차)={t2(q,o):.2f}")
k = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet", columns=["code","date","close"]); k = k[k.code == "U001"].set_index("date").close.pct_change().dropna()
s = lastn(k); q = s[[m in (3, 6, 9, 12) for (_, m) in s.index]]; o = s[[m not in (3, 6, 9, 12) for (_, m) in s.index]]; print(f"  코스피 2013~: 분기말 {q.mean()*100:.2f}% (양수 {(q>0).mean()*100:.0f}%, n={len(q)}) vs 비분기 {o.mean()*100:.2f}% | t={t2(q,o):.2f}")
D = f"{ROOT}/DataVault/staging/kr_equity/derived"; g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","ret_60","market_cap"]); p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"]).dropna(subset=["close"])
C = p.pivot(index="date", columns="code", values="close"); RET = C.pct_change(fill_method=None); G60 = g.pivot(index="date", columns="code", values="ret_60"); CAP = g.pivot(index="date", columns="code", values="market_cap")
dts = C.index; res = {"q": [], "o": []}
for (y, m), grp in pd.Series(dts, index=dts).groupby([dts.year, dts.month]):
    if y < 2013 or len(grp) < 5: continue
    w = grp.iloc[-3:]; d0 = grp.iloc[-4]; top = CAP.loc[d0].nlargest(500).index; sc = G60.loc[d0, top].dropna(); 
    if len(sc) < 100: continue
    rk = sc.rank(pct=True); win, los = rk[rk >= .8].index, rk[rk <= .2].index
    wr = (1 + RET.loc[w, win]).prod().mean() - 1; lr = (1 + RET.loc[w, los]).prod().mean() - 1
    res["q" if m in (3, 6, 9, 12) else "o"].append(wr - lr)
q, o = pd.Series(res["q"]), pd.Series(res["o"]); print(f"  코스피 상위500 종목 간 승자(60일 수익 상위20%)−패자 3일 수익: 분기말 {q.mean()*100:.2f}%p (양수 {(q>0).mean()*100:.0f}%, n={len(q)}) vs 비분기 월말 {o.mean()*100:.2f}%p (n={len(o)}) | t(차)={t2(q,o):.2f}")
print("\n=== 7. 월요일 효과 (일 수익률 평균, bp) ===")
def wk(r, nm):
    d = r.groupby(r.index.weekday).agg(["mean", "count"]); m = r[r.index.weekday == 0]; o = r[r.index.weekday != 0]
    print(f"  {nm}: 월 {d.loc[0,'mean']*1e4:5.1f} 화 {d.loc[1,'mean']*1e4:5.1f} 수 {d.loc[2,'mean']*1e4:5.1f} 목 {d.loc[3,'mean']*1e4:5.1f} 금 {d.loc[4,'mean']*1e4:5.1f} | 월요일 양수 {(m>0).mean()*100:.1f}% vs 타요일 {(o>0).mean()*100:.1f}% | t(월−타)={t2(m,o):.2f}")
for nm, a, b in (("S&P 1928~1969","1928","1969"),("S&P 1970~1999","1970","1999"),("S&P 2000~2026","2000","2026"),("S&P 2016~2026","2016","2026")): wk(R[a:b], nm)
wk(k, "코스피 2013~")
