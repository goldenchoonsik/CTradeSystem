"""대표 ETF의 한국 노출 규모 vs 외국인 보유 시총 (2026-10-22 ETF 보유, 2026-10-19 외국인 보유).
iShares latest-holdings.csv 4종(EWY·EEM·IEMG·ACWI), 단위 십억달러. 원/달러는 fx_usdkrw 마지막 값.
"""
import io
import pandas as pd

R = "/home/kuzimo/projects/CTradeSystem"
E = f"{R}/docs/research/20261025_foreign_channels/etf"


def load(f):
    t = open(f"{E}/{f}.csv", encoding="utf-8-sig").read().splitlines()
    h = [i for i, l in enumerate(t) if l.startswith("Ticker,")][0]
    d = pd.read_csv(io.StringIO("\n".join(t[h:])), thousands=",")
    d = d[(d["Asset Class"] == "Equity") & d["Location"].str.contains("Korea", na=False)].copy()
    d["code"] = "A" + d["Ticker"].astype(str).str.zfill(6)
    return d.set_index("code")[["Name", "Quantity", "Market Value"]]


etfs = {f: load(f) for f in ["EWY", "EEM", "IEMG", "ACWI"]}
fx = pd.read_csv(f"{R}/DataVault/staging/fx_usdkrw/usdkrw_daily.csv").iloc[-1].usdkrw

de = pd.read_parquet(f"{R}/DataVault/staging/kr_equity/derived/daily_ext.parquet",
                     columns=["code", "date", "market_cap", "foreign_hold_qty", "foreign_hold_pct", "shares_listed"])
de = de[de.date == pd.Timestamp("2026-10-19")].set_index("code")
de["frgn_krw"] = de.market_cap * de.foreign_hold_pct / 100
tot_cap, tot_frgn = de.market_cap.sum() / fx / 1e9, de.frgn_krw.sum() / fx / 1e9
print(f"한국 상장 합계(보통주+우선 포함 daily_ext 2026-10-19): 시총 ${tot_cap:,.0f}bn, 외국인 보유 ${tot_frgn:,.0f}bn (원/달러 {fx:.0f})")

tab = {}
for k, d in etfs.items():
    tab[k] = d["Market Value"].astype(float).sum() / 1e9
tab["합계"] = sum(tab.values())
print("\nETF별 한국 주식 보유(십억달러):", {k: round(v, 2) for k, v in tab.items()})
print(f"합계 / 외국인 보유 시총 = {tab['합계']/tot_frgn:.1%} | / 한국 전체 시총 = {tab['합계']/tot_cap:.1%}")

q = pd.DataFrame({k: d["Quantity"].astype(float) for k, d in etfs.items()}).fillna(0)
q["ETF합"] = q.sum(axis=1)
q = q.join(de[["foreign_hold_qty", "shares_listed", "market_cap", "foreign_hold_pct"]])
q["ETF/외국인보유"] = q["ETF합"] / q.foreign_hold_qty
q["ETF/상장주식"] = q["ETF합"] / q.shares_listed
name = pd.concat(etfs.values())["Name"]
q["name"] = name[~name.index.duplicated()]
q["ETF_USDbn"] = q["ETF합"] * (q.market_cap / q.shares_listed) / fx / 1e9
top = q.sort_values("ETF_USDbn", ascending=False).head(12)
print("\n=== ETF 보유 상위 종목: 외국인 보유 대비 / 상장주식 대비 ===")
print(top[["name", "ETF_USDbn", "ETF/외국인보유", "ETF/상장주식", "foreign_hold_pct"]].round(4).to_string())

# 외국인 보유 가중 평균 vs 순수 ETF 노출: 상위 2종목 제외
rest = q.drop(["A005930", "A000660"], errors="ignore")
print(f"\n삼성·하이닉스 제외: ETF {rest.ETF_USDbn.sum():.1f}bn / 외국인 보유 {(de.drop(['A005930','A000660'],errors='ignore').frgn_krw.sum()/fx/1e9):.0f}bn")

print("\n=== 규모 비교: 2026년 외국인 순매도 vs ETF 규모 ===")
print("외국인 순매도 2026년 −208조원(KRX 코스피) ≈ $%.0fbn (연평균 환율 1.35k 가정 아님, 현재 환율 환산)" % (208e12 / fx / 1e9))
print("→ EWY+EEM+IEMG+ACWI 전체 한국 노출 합 $%.0fbn 의 몇 배인지: %.1f배" % (tab["합계"], 208e12 / fx / 1e9 / tab["합계"]))
q.to_csv(f"{E}/etf_korea_exposure.csv")
