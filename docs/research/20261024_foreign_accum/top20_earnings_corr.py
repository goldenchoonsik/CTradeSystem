"""상위 20 종목 외국인 지분율 3년 변화 vs 순이익 변화 — 연결 기준 연간 누계: 2023 회계연도 → 최근 12개월(2025 연간 + 2026 상반기 − 2025 상반기). 순위상관(주), 변환값 피어슨, 주가수익 통제.
  .venv/bin/python docs/research/20261024_foreign_accum/top20_earnings_corr.py"""
import os, numpy as np, pandas as pd
from scipy import stats
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); OUT = os.path.dirname(os.path.abspath(__file__))
T = pd.read_csv(f"{OUT}/top20_foreign_3y.csv"); V = f"{ROOT}/DataVault/staging/kr_equity/derived"
f = pd.read_parquet(f"{V}/fundamentals_q.parquet", columns=["stock_code", "fs_div", "period_end", "net_income_cum", "op_income_cum", "sales_cum", "rcept_dt"])
f = f[f.fs_div == "CFS"].sort_values(["stock_code", "period_end", "rcept_dt"]).drop_duplicates(["stock_code", "period_end"], keep="last").set_index(["stock_code", "period_end"])
def cum(code, date, col):
    try: v = f.loc[(code, pd.Timestamp(date)), col]
    except KeyError: return np.nan
    return v
def base_end(code, col):
    fy23 = cum(code, "2023-12-31", col); ltm = cum(code, "2025-12-31", col) + cum(code, "2026-06-30", col) - cum(code, "2025-06-30", col); fy25 = cum(code, "2025-12-31", col)
    return fy23, ltm, fy25
rows = []
for _, r in T.iterrows():
    c = r["코드"][1:]; d = {"종목": r["종목"], "코드": r["코드"], "지분변화": r["3년변화(%p)"], "시총(조)": r["시총(조)"]}
    for col, lab in (("net_income_cum", "순이익"), ("op_income_cum", "영업이익"), ("sales_cum", "매출")):
        a, b, y25 = base_end(c, col); d[f"{lab}_2023"] = a / 1e12; d[f"{lab}_2026"] = b / 1e12; d[f"{lab}_2025"] = y25 / 1e12
        d[f"{lab}_증가율"] = (b / a - 1) * 100 if a > 0 else np.nan
        d[f"{lab}_대칭변화"] = (b - a) / (abs(a) + abs(b)) if (abs(a) + abs(b)) > 0 else np.nan
        d[f"{lab}_시총대비변화"] = (b - a) / 1e12 / r["시총(조)"] * 100
    rows.append(d)
R = pd.DataFrame(rows)
# 주가 36개월 수익
p = pd.read_parquet(f"{V}/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"]); end = p.date.max(); st = end - pd.DateOffset(years=3)
pr = {c: p[(p.code == c) & (p.date <= end)].sort_values("date").close.iloc[-1] / p[(p.code == c) & (p.date <= st)].sort_values("date").close.iloc[-1] - 1 for c in R.코드}
R["주가수익_36m"] = R.코드.map(pr) * 100
pd.set_option("display.width", 250); print("== 표 (조원, 2023=2023 회계연도, 2026=최근 12개월(2026-06 기준), 연결) =="); print(R[["종목", "지분변화", "순이익_2023", "순이익_2026", "순이익_증가율", "순이익_대칭변화", "영업이익_증가율", "매출_증가율", "주가수익_36m"]].round(1).to_string(index=False))
print("\n기준 적자(2023 TTM ≤0) 종목:", R[R.순이익_2023 <= 0].종목.tolist(), "| 2026 적자:", R[R.순이익_2026 <= 0].종목.tolist())
def corr(x, y, lab):
    m = x.notna() & y.notna(); a, b = x[m], y[m]
    sp = stats.spearmanr(a, b); kt = stats.kendalltau(a, b); pe = stats.pearsonr(a, b)
    print(f"{lab:34s} n={m.sum():2d} 스피어만 {sp.statistic:+.2f} (p {sp.pvalue:.2f}) | 켄달 {kt.statistic:+.2f} | 피어슨 {pe.statistic:+.2f}")
print("\n== 지분율 3년 변화(%p)와의 상관 ==")
for k, lab in (("순이익_증가율", "순이익 증가율(기준>0만)"), ("순이익_대칭변화", "순이익 대칭변화(−1~+1, 전 종목)"), ("순이익_시총대비변화", "순이익 변화/현재 시총"), ("영업이익_증가율", "영업이익 증가율"), ("영업이익_대칭변화", "영업이익 대칭변화"), ("매출_증가율", "매출 증가율"), ("주가수익_36m", "주가 36개월 수익")): corr(R[k], R.지분변화, lab)
print("\n== 주가 수익을 통제한 순위 편상관(지분변화 ~ 이익, 주가 통제) ==")
for k in ("순이익_대칭변화", "영업이익_대칭변화"):
    m = R[[k, "지분변화", "주가수익_36m"]].dropna(); rk = m.rank(); X = np.column_stack([np.ones(len(rk)), rk["주가수익_36m"]])
    rx = rk[k] - X @ np.linalg.lstsq(X, rk[k], rcond=None)[0]; ry = rk.지분변화 - X @ np.linalg.lstsq(X, rk.지분변화, rcond=None)[0]; print(f"{k}: 편상관 {np.corrcoef(rx, ry)[0,1]:+.2f} (n={len(m)})")
print("주가 수익↔순이익 대칭변화 스피어만 %+.2f" % stats.spearmanr(R.주가수익_36m, R.순이익_대칭변화).statistic)
# 대형 이상치 제외 민감도
ex = R[~R.종목.isin(["삼성전자", "SK하이닉스"])]; corr(ex.순이익_대칭변화, ex.지분변화, "삼성전자·SK하이닉스 제외 대칭변화"); corr(ex.순이익_증가율, ex.지분변화, "삼성전자·SK하이닉스 제외 증가율")
ex2 = R[~R.종목.isin(["삼성SDI", "현대차"])]; corr(ex2.순이익_대칭변화, ex2.지분변화, "삼성SDI·현대차 제외(지분 급감) 대칭변화")
R.to_csv(f"{OUT}/top20_earnings_corr.csv", index=False, encoding="utf-8-sig")
