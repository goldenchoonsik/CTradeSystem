"""한국 상장 리츠(+맥쿼리인프라 참고): 배당수익률 − 국고채3년, 이후 12개월 총수익(배당 15.4% 후, 재투자 없음) vs KOSPI(가격지수, 배당 미포함).
형성 = 매년 첫 거래일, 2026 은 10월 말까지(미완). 데이터 = staging/kr_reit_yahoo (Yahoo .KS, 무수정 종가). 실행: .venv/bin/python -I docs/research/20261026_high_dividend/kr_reit.py"""
import json, glob, os, numpy as np, pandas as pd
V = "/home/kuzimo/projects/CTradeSystem/DataVault"
rate = pd.read_csv(f"{V}/staging/global_macro/series/kr_3y.csv", parse_dates=["date"]).set_index("date")["value"]
kospi = pd.read_csv(f"{V}/staging/global_macro/series/kr_kospi.csv", parse_dates=["date"]).set_index("date")["value"]
WH = 0.846
data = {}
for fp in sorted(glob.glob(f"{V}/staging/kr_reit_yahoo/raw/*.json")):
    j = json.load(open(fp)); c = os.path.basename(fp)[:-5]
    ix = pd.to_datetime(j["timestamp"], unit="s").normalize() + pd.Timedelta(hours=0)
    q = j["indicators"]["quote"][0]; cl = pd.Series(q["close"], index=ix).dropna()
    cl = cl[~cl.index.duplicated()]
    dv = pd.Series({pd.Timestamp(pd.to_datetime(int(k), unit="s").date()): v["amount"] for k, v in j.get("events", {}).get("dividends", {}).items()}, dtype=float)
    if len(cl) > 200: data[c] = (j["meta"].get("longName", c), cl, dv)
rows = []
for y in range(2007, 2027):
    d0 = pd.Timestamp(f"{y}-01-01"); r3 = rate[rate.index < d0].iloc[-1]
    ks = kospi[kospi.index < d0]
    if len(ks) == 0: continue
    d1 = pd.Timestamp(f"{y+1}-01-01") if y < 2026 else pd.Timestamp("2026-10-23")
    kret = kospi[kospi.index <= d1].iloc[-1] / ks.iloc[-1] - 1
    for c, (nm, cl, dv) in data.items():
        a = cl[cl.index >= d0]
        if len(a) == 0 or a.index[0] > d0 + pd.Timedelta(days=10) or cl.index[0] > d0 - pd.Timedelta(days=330): continue   # 상장 후 330일 이상(트레일링 12개월 배당 확보)
        p0 = a.iloc[0]; b = cl[cl.index <= d1]; p1 = b.iloc[-1]
        trail = dv[(dv.index >= d0 - pd.Timedelta(days=365)) & (dv.index < d0)].sum()
        nxt = dv[(dv.index >= d0) & (dv.index < d1)].sum()
        rows.append(dict(y=y, code=c, name=nm, r3=r3, yld=100 * trail / p0, spread=100 * trail / p0 - r3, ret=(p1 + WH * nxt) / p0 - 1, price=p1 / p0 - 1, kospi=kret, partial=(y == 2026)))
df = pd.DataFrame(rows); df.to_csv(os.path.join(os.path.dirname(__file__), "kr_reit_rows.csv"), index=False)
pd.set_option("display.width", 250)
infra = df[df.code == "088980"]; reit = df[df.code != "088980"]
g = reit.groupby("y").agg(n=("code", "count"), r3=("r3", "first"), yld=("yld", "mean"), spread=("spread", "mean"), TR=("ret", "mean"), price=("price", "mean"), kospi=("kospi", "first"), part=("partial", "first"))
g["TR"] *= 100; g["price"] *= 100; g["kospi"] *= 100; g["초과vsKOSPI"] = g.TR - g.kospi
print("=== 한국 리츠(맥쿼리인프라 제외) 연도별 동일가중 (형성=1월 첫 거래일) ===")
print(g.round(1).to_string())
i = infra.set_index("y")[["r3", "yld", "spread", "ret", "price", "kospi"]].copy()
for c in ("ret", "price", "kospi"): i[c] *= 100
print("\n=== 맥쿼리인프라(참고: 인프라펀드, 리츠 아님) ===\n", i.round(1).to_string())
full = g[~g.part]
print(f"\n리츠 완결연도 {len(full)}개: 평균 TR {full.TR.mean():.1f}% KOSPI(가격) {full.kospi.mean():.1f}% 평균 배당-금리 스프레드 {full.spread.mean():+.1f}%p")
print("\n=== 개별 리츠 × 연도 TR% (배당 세후) ===")
print((reit.pivot(index="name", columns="y", values="ret") * 100).round(0).to_string())
print("\n=== 개별 리츠 × 연도 (배당수익률−금리) %p ===")
print(reit.pivot(index="name", columns="y", values="spread").round(1).to_string())
