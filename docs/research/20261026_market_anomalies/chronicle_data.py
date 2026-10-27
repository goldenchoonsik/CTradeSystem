"""연대기 데이터: 월간 레짐 표 1928-01~2026-09 (S&P500 월말 종가·금리·인플레·신용스프레드·CAPE·NBER 침체·대통령·정당·연차·객관 기준 사건) → regime_monthly.csv + chronicle_data.json.
사건은 사전에 정한 객관 규칙만: ①S&P 하루 −7% 이하(60일 내 중복 제외) ②고점 대비 −20% 약세장(고점→저점) ③전쟁 발발 16건(앞서 사전 지정, events_fx.py 와 동일) ④선거일(대선·중간선거). NBER 침체=FRED USREC.
  .venv/bin/python docs/research/20261026_market_anomalies/chronicle_data.py"""
import os, json, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); OUT = os.path.dirname(__file__); V = f"{ROOT}/DataVault/staging/us_valuation"
j = json.load(open(f"{V}/raw/yahoo/GSPC_20261023.json"))["chart"]["result"][0]
P = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna(); P = P[~P.index.duplicated()]
M = P.resample("ME").last(); M = M[M.index <= "2026-09-30"]; M.index = M.index.to_period("M")
pn = pd.read_csv(f"{V}/derived/panel_monthly.csv"); pn.index = pd.PeriodIndex(pn.ym, freq="M")
rec = pd.read_csv(f"{V}/raw/fred/USREC_20261022_globalmacro.csv", parse_dates=["observation_date"]); rec.index = rec.observation_date.dt.to_period("M")
D = pd.DataFrame({"sp": M}); D["ret"] = D.sp.pct_change(); D["gs10"] = pn.GS10; D["tbl"] = pn.tbl; D["infl"] = pn.infl12 * (100 if pn.infl12.abs().max() < 2 else 1); D["baa"] = pn.BAA; D["credit"] = D.baa - D.gs10
D["cape"] = pn.CAPE_pit; D["rec"] = rec.USREC.reindex(D.index); D = D[D.index >= "1928-01"]
k_ = pd.read_csv(f"{V}/../global_macro/series/kr_kospi.csv", parse_dates=["date"]).set_index("date").value; km = k_.resample("ME").last(); km.index = km.index.to_period("M"); D["kospi"] = km.reindex(D.index)   # 코스피 1980=100 (월말)
D["sp_rb"] = D.sp / D.sp["1980-01"] * 100
terms = [("Coolidge","R","1923-08"),("Hoover","R","1929-03"),("F.Roosevelt","D","1933-03"),("Truman","D","1945-04"),("Eisenhower","R","1953-01"),("Kennedy","D","1961-01"),("L.Johnson","D","1963-11"),("Nixon","R","1969-01"),("Ford","R","1974-08"),("Carter","D","1977-01"),("Reagan","R","1981-01"),("G.H.W.Bush","R","1989-01"),("Clinton","D","1993-01"),("G.W.Bush","R","2001-01"),("Obama","D","2009-01"),("Trump(1)","R","2017-01"),("Biden","D","2021-01"),("Trump(2)","R","2025-01")]
ends = [t[2] for t in terms[1:]] + ["2026-10"]; D["pres"] = ""; D["party"] = ""
for (nm, pt, s), e in zip(terms, ends): m = (D.index >= pd.Period(s, "M")) & (D.index < pd.Period(e, "M")); D.loc[m, "pres"] = nm; D.loc[m, "party"] = pt
D["term_year"] = ((D.index.year - 1929) % 4) + 1
peak = D.sp.cummax(); D["dd"] = D.sp / peak - 1                      # 월말 기준 고점 대비 낙폭
R = P.pct_change().dropna(); big = R[R <= -0.07]; ev = []; last = None
for d in big.index:
    if last is None or (d - last).days > 90: ev.append(d)
    last = d
crash = [dict(date=str(d.date()), ret=float(R[d]), kind="crash") for d in ev]
state, pk, pkd, tr, trd, bears, bull_start = "bull", P.iloc[0], P.index[0], None, None, [], P.index[0]
for d, v in P.items():
    if state == "bull":
        if v > pk: pk, pkd = v, d
        elif v <= pk * 0.8: state, tr, trd, bs = "bear", v, d, pkd
    else:
        if v < tr: tr, trd = v, d
        elif v >= tr * 1.2: bears.append(dict(start=str(bs.date()), end=str(trd.date()), drop=float(tr / P[bs] - 1))); state, pk, pkd = "bull", v, d
if state == "bear": bears.append(dict(start=str(bs.date()), end=str(trd.date()), drop=float(tr / P[bs] - 1), ongoing=True))
W = [("1939-09-01","독일 폴란드 침공"),("1941-12-07","진주만"),("1950-06-25","한국전쟁"),("1964-08-02","통킹만"),("1973-10-06","욤키푸르"),("1982-04-02","포클랜드"),("1990-08-02","쿠웨이트 침공"),("1991-01-17","걸프전 공습"),("1999-03-24","코소보"),("2001-09-11","9/11"),("2001-10-07","아프간"),("2003-03-20","이라크전"),("2014-02-27","크림반도"),("2022-02-24","우크라이나"),("2023-10-07","하마스 공격"),("2025-06-13","이스라엘-이란")]
def elec(y):
    d = pd.Timestamp(f"{y}-11-02")
    while d.weekday() != 1: d += pd.Timedelta(days=1)
    return d
elections = [dict(date=str(elec(y).date()), kind="대선" if y % 4 == 0 else "중간선거") for y in range(1928, 2027, 2)]
D.to_csv(f"{OUT}/regime_monthly.csv", index_label="ym")
Dj = D.rename_axis("ym").reset_index(); Dj["ym"] = Dj.ym.astype(str)
json.dump(dict(months=Dj[["ym","sp","kospi","sp_rb","gs10","tbl","infl","credit","cape","rec","party","pres","term_year","dd"]].replace({np.nan: None}).to_dict("list"), crashes=crash, bears=bears, wars=[dict(date=d, name=n) for d, n in W], elections=elections, terms=[dict(name=n, party=p, start=s) for n, p, s in terms], last=str(D.index[-1])), open(f"{OUT}/chronicle_data.json", "w"), ensure_ascii=False)
print("월", len(D), D.index[0], D.index[-1], "| 컬럼 결측:", D[["gs10","tbl","infl","credit","cape","rec"]].isna().sum().to_dict())
print("현재(마지막 월):", D.iloc[-1][["sp","gs10","tbl","infl","credit","cape","dd","pres"]].to_dict())
print("하루 −7%↓ 사건", len(crash), "| 약세장", len(bears), "| 마지막 약세장", bears[-1])
print(D[["gs10","tbl","infl","credit","cape"]].describe().round(2).loc[["min","max","mean"]])
