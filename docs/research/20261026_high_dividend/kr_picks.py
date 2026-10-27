"""한국 현재 시점 후보 선별: 보통주·상장·시총 상위 500, 배당수익률(FY2025 DPS ÷ 최신 무수정 종가) > 국고채3년, 최근 3개 회계연도 연속 배당·삭감 없음(지속 배당).
실행: .venv/bin/python -I docs/research/20261026_high_dividend/kr_picks.py"""
import sys, json, glob, os, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem")
from shared.data_library.kr_equity import store
S = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/kr_equity"
def num(x):
    try: return float(str(x).replace(",", "").strip())
    except: return np.nan
cs = store.universe_corps(); dps = {}
for corp, st in cs.items():
    for fp in glob.glob(f"{S}/raw/dart/alot_matter/{corp}/*.json"):
        j = json.load(open(fp, encoding="utf-8"))["response"]; Y = int(os.path.basename(fp)[:4])
        if j.get("status") != "000": continue
        for x in j["list"]:
            if x["se"].startswith("주당 현금배당금") and x.get("stock_knd") in ("보통주", "-", None):
                for k, v in zip((Y, Y - 1, Y - 2), (x["thstrm"], x["frmtrm"], x["lwfr"])):
                    d = num(v); d = 0.0 if (d != d and str(v).strip() in ("-", "")) else d
                    if (st, k) not in dps or Y < dps[(st, k)][1]: dps[(st, k)] = (d, Y)
px = pd.read_parquet(f"{S}/derived/prices_1d.parquet", columns=["code", "date", "close", "close_unadj"]); px["date"] = pd.to_datetime(px["date"])
for c in ("close", "close_unadj"): px[c] = pd.to_numeric(px[c], errors="coerce")
last = px.date.max()
ext = pd.read_parquet(f"{S}/derived/daily_ext.parquet", columns=["code", "date", "shares_listed", "market_cap"]); ext["date"] = pd.to_datetime(ext["date"])
uni = pd.read_csv(f"{S}/universe.csv", dtype=str).set_index("code")
rate = pd.read_csv("/home/kuzimo/projects/CTradeSystem/DataVault/staging/global_macro/series/kr_3y.csv", parse_dates=["date"]).set_index("date")["value"].iloc[-1]
fq = pd.read_parquet(f"{S}/derived/fundamentals_q.parquet"); fq = fq[(fq.q == 4) & (fq.bsns_year == 2025)].sort_values("fs_div")
fq = fq.drop_duplicates("stock_code").set_index("stock_code")["net_income_annual"]
pl = px.dropna(subset=["close"]).sort_values("date").groupby("code").tail(1).set_index("code")
print("종목별 마지막 유효 종가일 분포", pl.date.value_counts().head(3).to_dict())
y1 = px[px.date <= last - pd.DateOffset(years=1)].sort_values("date").groupby("code").tail(1).set_index("code")["close"]
el = ext.sort_values("date").groupby("code").tail(1).set_index("code")
rows = []; dbg = dict(n=0, listed=0, dps=0)
for code, r in pl.iterrows():
    if code not in uni.index: continue
    u = uni.loc[code]; dbg['n'] += 1
    if u.status != "listed" or u.is_preferred == "1" or u.is_spac == "1" or code[-1] != "0": continue
    dbg['listed'] += 1
    if pd.isna(r['close']) or not (float(r['close']) > 0): continue
    dbg['px'] = dbg.get('px', 0) + 1; st = code[1:]; d = [dps.get((st, k), (np.nan,))[0] for k in (2025, 2024, 2023)]
    if not (d[0] > 0 and d[1] > 0 and d[2] > 0): continue
    dbg['dps'] += 1
    steady = d[0] >= 0.97 * d[1] and d[1] >= 0.97 * d[2]
    y = 100 * d[0] / float(r.close)
    mc = float(el.market_cap.get(code, np.nan)); sh = float(el.shares_listed.get(code, np.nan)); ni = float(fq.get(st, np.nan))
    pay = d[0] * sh / ni * 100 if ni > 0 else np.nan
    p1 = y1.get(code, np.nan)
    rows.append(dict(code=st, name=u["name"], market=u.market, mcap_bn=mc / 1e8, price=float(r.close), dps25=d[0], dps24=d[1], dps23=d[2], yld=y, above=y > rate,
                     steady=steady, ni_bn=ni / 1e8, payout=pay, ret12m=100 * (float(r.close) / float(p1) - 1) if p1 == p1 else np.nan))
d = pd.DataFrame(rows).sort_values("mcap_bn", ascending=False); d["rank"] = range(1, len(d) + 1)
allu = el[(el.index.isin(pl.index))].market_cap
d.to_csv("/home/kuzimo/projects/CTradeSystem/docs/research/20261026_high_dividend/kr_picks_candidates.csv", index=False)
print("기준일", last.date(), "국고채3년", rate)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
cols = ["code", "name", "market", "mcap_bn", "price", "dps25", "dps24", "dps23", "yld", "payout", "ret12m"]
print(len(d), "지급 3년 종목(시총순)")
print(d[d.above & d.steady].round(1)[cols + ["rank"]].to_string(index=False))
