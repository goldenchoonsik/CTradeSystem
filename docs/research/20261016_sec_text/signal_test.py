"""텍스트 지표 변화 vs 공시 직후 QQQ 대비 초과수익. 합격선(사전 고정): Bonferroni p<0.001, 10-K·10-Q 각각에서 동일 부호."""
import pandas as pd, numpy as np, os
from scipy.stats import spearmanr
V = "/home/kuzimo/projects/CTradeSystem/DataVault/raw/yahoo"
D = os.path.dirname(os.path.abspath(__file__))
def px(t):
    d = pd.read_csv(f"{V}/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close
    fp = f"{V}/corporate_actions/{t}.csv"; f = pd.Series(1.0, index=d.index)
    if os.path.exists(fp):
        a = pd.read_csv(fp, parse_dates=["date"]); a = a[a.type == "split"].drop_duplicates("date")
        for r in a.itertuples(): f[f.index < r.date] /= (r.numerator / r.denominator)   # 분할 이전 가격을 분할 후 기준으로 환산
    return d * f
P = {t: px(t) for t in ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "QQQ"]}
M = pd.read_csv(f"{D}/metrics.csv", parse_dates=["filed"])
feat = ["rf_new_frac", "neg_pm", "pos_pm", "mda_words", "rf_chars"] + [c for c in M.columns if c.startswith("kw_")]
M = M.sort_values(["ticker", "form", "filed"])
for c in feat[1:]: M["d_" + c] = M.groupby(["ticker", "form"])[c].diff()   # 직전 같은 양식 대비 변화
HZ = [1, 5, 21, 63]
def fwd(t, filed, h):
    s = P[t]; i = s.index.searchsorted(filed, side="right")        # 접수일 다음 거래일
    return s.iloc[i + h] / s.iloc[i] - 1 if i + h < len(s) else np.nan
for h in HZ:
    M[f"x{h}"] = [fwd(r.ticker, r.filed, h) - fwd("QQQ", r.filed, h) for r in M.itertuples()]
tests = ["rf_new_frac"] + ["d_" + c for c in feat[1:]]
rows = []
for form in ("10-K", "10-Q"):
    S = M[M.form == form]
    for f in tests:
        for h in HZ:
            d = S[[f, f"x{h}"]].dropna()
            if len(d) > 20 and d[f].nunique() > 3:
                r, p = spearmanr(d[f], d[f"x{h}"]); rows.append((form, f, h, len(d), r, p))
R = pd.DataFrame(rows, columns=["form", "feature", "h", "n", "rho", "p"]); R.to_csv(f"{D}/signal_test.csv", index=False)
print("검정 수", len(R), "| 최소 p", R.p.min(), "| p<0.05:", (R.p < .05).sum(), "(우연 기대", round(len(R) * .05, 1), ") | p<0.001:", (R.p < .001).sum())
print(R.sort_values("p").head(10).to_string(index=False))
