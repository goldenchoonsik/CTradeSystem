"""외국인 자금 일일 모니터 — KIS 조회로 (1) 코스피·코스닥 주체별 일별 순매수 갱신 (2) 프로그램매매 갱신 (3) 외국인·기관 순매수 상·하위 30종목 스냅샷 저장 (4) 모니터 대상 200종목 현재 외국인 보유와 종목별 투자자 세부 이력 증분 (5) iShares EWY 발행주식수(외국인 패시브 자금 대리) 저장 후 보고서 출력.
앱 키는 stdin 으로만 받는다(저장 안 함): bash docs/research/20261024_foreign_accum/daily_monitor.sh
토큰: CTrade/config/token_info.json 이 만료 1시간 전이면 새로 발급(같은 키를 쓰는 서버 CTrade 실시간 세션이 켜져 있으면 끊길 수 있음 — 서버 서비스 상태 확인 필요).
  저장: DataVault/staging/kr_investor_daily/{KSP_0001_daily.csv, KSQ_1001_daily.csv, KSP_program_daily.csv, top_flows/YYYYMMDD_*.json, holdings/YYYYMMDD.csv, ewy_shares.csv}"""
import sys, os, json, time, csv, datetime as dt, io, re
import numpy as np, pandas as pd, requests
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); ST = f"{ROOT}/DataVault/staging/kr_investor_daily"; OUT = os.path.dirname(os.path.abspath(__file__))
kv = {}
for l in sys.stdin:
    k, _, v = l.strip().partition("="); kv[k] = v.strip("'\" ")
BASE = "https://openapi.koreainvestment.com:9443"; TF = f"{ROOT}/CTrade/config/token_info.json"
def token():
    try:
        j = json.load(open(TF)); exp = dt.datetime.strptime(j["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
        if exp - dt.datetime.now() > dt.timedelta(hours=1): return j["access_token"]
    except Exception: pass
    r = requests.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": kv["KIS_APP_KEY"], "appsecret": kv["KIS_APP_SECRET"]}, timeout=20).json()
    json.dump({"access_token": r["access_token"], "access_token_token_expired": r.get("access_token_token_expired"), "token_type": r.get("token_type", "Bearer"), "expires_in": r.get("expires_in")}, open(TF, "w")); print("새 토큰 발급, 만료", r.get("access_token_token_expired")); return r["access_token"]
TOK = token()
def call(path, tr, p):
    h = {"authorization": "Bearer " + TOK, "appkey": kv["KIS_APP_KEY"], "appsecret": kv["KIS_APP_SECRET"], "tr_id": tr, "custtype": "P"}
    time.sleep(0.12); return requests.get(BASE + path, headers=h, params=p, timeout=30).json()
today = dt.date.today(); d1 = today.strftime("%Y%m%d"); d0 = (today - dt.timedelta(days=40)).strftime("%Y%m%d")
def merge_csv(path, rows, key):
    old = pd.read_csv(path, dtype=str) if os.path.exists(path) else pd.DataFrame(); new = pd.DataFrame(rows).astype(str)
    allr = pd.concat([old, new]).drop_duplicates(key, keep="last").sort_values(key); allr.to_csv(path, index=False); return allr
# (1) 주체별 일별
inv = {}
for m, c in (("KSP", "0001"), ("KSQ", "1001")):
    j = call("/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market", "FHPTJ04040000", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": c, "FID_INPUT_DATE_1": d1, "FID_INPUT_ISCD_1": m, "FID_INPUT_DATE_2": d0, "FID_INPUT_ISCD_2": c})
    if j.get("rt_cd") != "0": print("오류", m, j.get("msg1")); continue
    inv[m] = merge_csv(f"{ST}/{m}_{c}_daily.csv", j["output"], "stck_bsop_date")
last_td = inv["KSP"].stck_bsop_date.max()
# (2) 프로그램매매
j = call("/uapi/domestic-stock/v1/quotations/comp-program-trade-daily", "FHPPG04600001", {"FID_COND_MRKT_DIV_CODE": "J", "FID_MRKT_CLS_CODE": "K", "FID_SCTN_CLS_CODE": "", "FID_INPUT_ISCD": "", "FID_COND_MRKT_DIV_CODE1": "", "FID_INPUT_DATE_1": d0, "FID_INPUT_DATE_2": d1})
prog = merge_csv(f"{ST}/KSP_program_daily.csv", j["output"], "stck_bsop_date") if j.get("rt_cd") == "0" else None
# (3) 순매수 상·하위 스냅샷
snap = {}
for etc, lab in (("1", "foreign"), ("2", "inst")):
    for sort, sl in (("0", "buy"), ("1", "sell")):
        j = call("/uapi/domestic-stock/v1/quotations/foreign-institution-total", "FHPTJ04400000", {"FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449", "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "1", "FID_RANK_SORT_CLS_CODE": sort, "FID_ETC_CLS_CODE": etc})
        if j.get("rt_cd") == "0":
            snap[f"{lab}_{sl}"] = j["output"]; json.dump({"fetched": dt.datetime.now().isoformat(timespec="seconds"), "trading_date_guess": last_td, "output": j["output"]}, open(f"{ST}/top_flows/{last_td}_{lab}_{sl}.json", "w"), ensure_ascii=False)
# (4) 모니터 대상 200종목 현재 보유
cur = pd.read_csv(f"{OUT}/monitor_current.csv"); codes = list(cur.code)
hold = []
for c in codes:
    j = call("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c[1:]}); o = j.get("output") or {}
    if o: hold.append(dict(code=c, price=o.get("stck_prpr"), chg=o.get("prdy_ctrt"), frgn_ehrt=o.get("hts_frgn_ehrt"), frgn_qty=o.get("frgn_hldn_qty"), listed=o.get("lstn_stcn"), frgn_ntby_qty=o.get("frgn_ntby_qty"), vol=o.get("acml_vol")))
H = pd.DataFrame(hold); H.to_csv(f"{ST}/holdings/{last_td}.csv", index=False)
# (4b) 모니터 대상 종목별 투자자 이력 증분(30거래일/호출 → 며칠 건너뛰어도 빈 구간 없음)
os.makedirs(f"{ST}/stock_daily", exist_ok=True); yd = (today - dt.timedelta(days=1)).strftime("%Y%m%d")
for c in codes:
    j = call("/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily", "FHPTJ04160001", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c[1:], "FID_INPUT_DATE_1": yd, "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""})
    if j.get("rt_cd") == "0" and j.get("output2"): merge_csv(f"{ST}/stock_daily/{c}.csv", j["output2"], "stck_bsop_date")
# (5) EWY 발행주식수
try:
    t = requests.get("https://www.ishares.com/us/products/239681/ishares-msci-south-korea-etf/latest-holdings.csv", headers={"User-Agent": "Mozilla/5.0"}, timeout=40).text
    asof = re.search(r'Fund Holdings as of,"([^"]+)"', t).group(1); so = float(re.search(r'Shares Outstanding,"([\d,\.]+)"', t).group(1).replace(",", ""))
    p = f"{ST}/ewy_shares.csv"; e = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame(columns=["asof", "shares"]); e = pd.concat([e, pd.DataFrame([{"asof": pd.to_datetime(asof).strftime("%Y-%m-%d"), "shares": so}])]).drop_duplicates("asof", keep="last").sort_values("asof"); e.to_csv(p, index=False)
except Exception as ex: e = None; print("EWY 실패:", str(ex)[:80])
# ---------------- 보고서 ----------------
nm = pd.read_csv(f"{ROOT}/DataVault/staging/kr_equity/reference/listed_snapshot.csv").set_index("code").name
def zs(s, n=250): s = s.dropna(); return (s.iloc[-1] - s.iloc[-n:].mean()) / s.iloc[-n:].std()
print(f"\n===== 외국인 자금 일일 모니터 — 기준 거래일 {last_td} (실행 {dt.datetime.now():%Y-%m-%d %H:%M}) =====")
rows = []
for m, lab in (("KSP", "코스피"), ("KSQ", "코스닥")):
    x = inv[m].copy(); x["d"] = pd.to_datetime(x.stck_bsop_date); x = x.set_index("d").sort_index()
    for col, who in (("frgn_ntby_tr_pbmn", "외국인"), ("orgn_ntby_tr_pbmn", "기관"), ("prsn_ntby_tr_pbmn", "개인")):
        v = x[col].astype(float) / 1e6                       # 조원
        rows.append(dict(시장=lab, 주체=who, **{"당일(조)": v.iloc[-1], "5일(조)": v.iloc[-5:].sum(), "20일(조)": v.iloc[-20:].sum(), "z(당일, 40일기준)": (v.iloc[-1] - v.mean()) / v.std()}))
print(pd.DataFrame(rows).round(2).to_string(index=False))
if prog is not None:
    pg = prog.copy(); pg["d"] = pd.to_datetime(pg.stck_bsop_date); pg = pg.set_index("d").sort_index(); ar = (pg.arbt_smtn_shnu_tr_pbmn.astype(float) - pg.arbt_smtn_seln_tr_pbmn.astype(float)) / 1e6; na = (pg.nabt_smtn_shnu_tr_pbmn.astype(float) - pg.nabt_smtn_seln_tr_pbmn.astype(float)) / 1e6
    print(f"프로그램매매(코스피) 당일: 차익 {ar.iloc[-1]:+.2f}조, 비차익 {na.iloc[-1]:+.2f}조 | 5일: 차익 {ar.iloc[-5:].sum():+.2f}, 비차익 {na.iloc[-5:].sum():+.2f}")
print("\n[외국인 순매수 상위 10] (억원, 당일)  기관 / 기타법인 상대방")
def show(key, field="frgn_ntby_tr_pbmn", n=10):
    for r in snap.get(key, [])[:n]:
        print(f"  {r['hts_kor_isnm']:<12s} {int(r[field])/100:>+9.0f}억  기관 {int(r['orgn_ntby_tr_pbmn'])/100:>+8.0f}  기타법인 {int(r['etc_corp_ntby_tr_pbmn'])/100:>+8.0f}  ({float(r['prdy_ctrt']):+.1f}%)")
show("foreign_buy"); print("[외국인 순매도 상위 10]"); show("foreign_sell")
print("[기관 순매수 상위 5]"); show("inst_buy", "orgn_ntby_tr_pbmn", 5)
if e is not None and len(e) >= 2: print(f"\nEWY 발행주식수(해외 패시브 자금 대리): {e['asof'].iloc[-1]} {e['shares'].iloc[-1]:,.0f} (이전 {e['asof'].iloc[-2]} 대비 {e['shares'].iloc[-1] - e['shares'].iloc[-2]:+,.0f}주)")
elif e is not None: print(f"\nEWY 발행주식수 {e['asof'].iloc[-1]} {e['shares'].iloc[-1]:,.0f} (첫 기록 — 다음 실행부터 변화 표시)")
# 모니터 대상 변화: daily_ext 마지막(10-19) 대비
H["pct"] = H.frgn_qty.astype(float) / H.listed.astype(float) * 100; M = cur.merge(H, on="code"); M["Δ(%p)"] = M.pct - M.지분율
print(f"\n[모니터 대상 {len(M)}종목: 최근 외국인 지분율 변화 (월말 10-19 기준 대비, 현재 보유 기준 %p)]")
top_mv = M.reindex(M["Δ(%p)"].abs().sort_values(ascending=False).index).head(12)
for r in top_mv.itertuples(): print(f"  {r.이름:<12s} [{r.상태}] 지분율 {r.pct:5.1f}% (월말 {r.지분율:.1f}) Δ{(r.pct - r.지분율):+.1f}%p, 당일 외국인 순매수수량 {int(float(r.frgn_ntby_qty)) if r.frgn_ntby_qty else 0:+,}")
for lab in ("상단 정체", "상승 지속", "상단 후 하락"):
    z = M[M.상태 == lab]; print(f"\n[{lab}] 지분율 변화(월말 대비) 평균 {z['Δ(%p)'].mean():+.2f}%p, 늘어난 종목 {int((z['Δ(%p)'] > 0.3).sum())} / 줄어든 종목 {int((z['Δ(%p)'] < -0.3).sum())} (n={len(z)})")
M.to_csv(f"{OUT}/daily_monitor_latest.csv", index=False, encoding="utf-8-sig")
print("\n저장 완료:", ST)
