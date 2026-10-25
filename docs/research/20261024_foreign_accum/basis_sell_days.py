"""외국인 순매도 큰 날 K200 선물 베이시스 — 선물 15:30 분봉 종가 vs K200 지수 종가(%). 롤오버 점프는 제외. 이전 daily_sell_days.csv 사용.
  .venv/bin/python docs/research/20261024_foreign_accum/basis_sell_days.py"""
import os, glob, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
H = os.path.dirname(__file__)
rows = []
for f in sorted(glob.glob(f"{ROOT}/DataVault/raw/creon/candle_1m/A10100/*.csv")):
    try:
        x = pd.read_csv(f, usecols=["timestamp", "upbit_close"], parse_dates=["timestamp"])
    except Exception: continue
    x = x[(x.timestamp.dt.strftime("%H:%M") <= "15:30")]
    if len(x): rows.append((x.timestamp.iloc[-1].normalize(), x.upbit_close.iloc[-1], x.timestamp.iloc[-1].strftime("%H:%M")))
fu = pd.DataFrame(rows, columns=["date", "fut", "t"]).set_index("date")
k2 = pd.read_csv(f"{ROOT}/DataVault/staging/global_macro/series/kr_k200.csv", parse_dates=["date"]).set_index("date").value
a = pd.read_csv(f"{H}/daily_sell_days.csv", parse_dates=["date"]).set_index("date")
a["fut"] = fu.fut; a["k200"] = k2; a["last_t"] = fu.t
a = a.dropna(subset=["fut", "k200"])
a["basis"] = (a.fut / a.k200 - 1) * 100
a["dbasis"] = a.basis.diff()
print("일수", len(a), "마지막 분봉 시각 분포:", a.last_t.value_counts().head(3).to_dict())
print("베이시스(%) 분포:", a.basis.describe(percentiles=[.01, .5, .99]).round(2).to_dict())
ok = a[(a.basis.abs() < 3) & (a.dbasis.abs() < 1.2)].copy()          # 롤오버·결측 점프 제거
print("점프 제거 후", len(ok), "일")
ok["dec"] = pd.qcut(ok.fr, 10, labels=False)
t = ok.groupby("dec").agg(일수=("ret", "size"), 외인순매수비=("fr", "mean"), 지수수익=("ret", "mean"), 베이시스=("basis", "mean"), 베이시스음수비율=("basis", lambda s: (s < 0).mean()*100), 전일대비변화=("dbasis", "mean"))
print(t.round(3).to_string())
lo = ok[ok.fr <= ok.fr.quantile(.05)]; hi = ok[ok.fr >= ok.fr.quantile(.95)]
print("\n매도 상위5%%: 베이시스 %.3f, 변화 %.3f, 음수 %.0f%% | 매수 상위5%%: %.3f, %.3f, %.0f%%" % (lo.basis.mean(), lo.dbasis.mean(), (lo.basis<0).mean()*100, hi.basis.mean(), hi.dbasis.mean(), (hi.basis<0).mean()*100))
# 지수 수익을 통제: 같은 하락폭의 날(외국인 매도 비큰 날)과 비교
dn = ok[ok.ret <= -1.0]; q = dn.fr.median()
print("\n지수 -1%% 이하 하락일 %d일 중 외국인 매도 더 큰 쪽/덜 큰 쪽 베이시스: %.3f / %.3f (변화 %.3f / %.3f)" % (len(dn), dn[dn.fr <= q].basis.mean(), dn[dn.fr > q].basis.mean(), dn[dn.fr <= q].dbasis.mean(), dn[dn.fr > q].dbasis.mean()))
import numpy.linalg as la
X = np.column_stack([np.ones(len(ok)), ok.ret, ok.fr]); b = la.lstsq(X, ok.dbasis.values, rcond=None)[0]
print("회귀 베이시스변화 ~ 지수수익 + 외국인순매수비: 계수 [상수, 수익, 외국인] =", b.round(4))
