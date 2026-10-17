"""LM 사전 지표 vs 공시 직후 QQQ 대비 초과수익. 합격선(사전 고정): p < 0.05/검정수 (Bonferroni), 그리고 10-K·10-Q 부호 일치."""
import pandas as pd, numpy as np, os, json
from scipy.stats import spearmanr
import signal_test as S0   # 가격·fwd 함수 재사용 (import 시 기존 검정이 한 번 돌지만 결과는 별도 파일)
D = os.path.dirname(os.path.abspath(__file__))
M = pd.read_csv(f"{D}/metrics_lm.csv", parse_dates=["filed"]).sort_values(["ticker", "form", "filed"])
base = [f"{a}_{b}" for a in ("mda", "rf") for b in ("neg", "pos", "unc", "lit", "con", "tone")]
g = M.groupby(["ticker", "form"])
for c in base:
    M["prev_" + c] = g[c].diff()                                    # 직전 같은 양식 대비
    M["yoy_" + c] = g[c].diff(3).where(M.form == "10-Q", g[c].diff(1))  # 10-Q 는 3건 전(=작년 같은 분기), 10-K 는 작년
    M["dev_" + c] = M[c] - g[c].transform(lambda s: s.shift(1).expanding(3).mean())  # 과거 평균 대비(미래참조 없음)
HZ = [1, 5, 21, 63]
for h in HZ: M[f"x{h}"] = [S0.fwd(r.ticker, r.filed, h) - S0.fwd("QQQ", r.filed, h) for r in M.itertuples()]
# 위험요인 10-Q 는 대부분 10-K 참조(짧음)라 제외
M.loc[(M.form == "10-Q") & (M.rf_n < 3000), [c for c in M.columns if c.startswith(("rf_", "prev_rf_", "yoy_rf_", "dev_rf_"))]] = np.nan
tests = [p + c for c in base for p in ("prev_", "yoy_", "dev_")]
rows = []
for form in ("10-K", "10-Q"):
    X = M[M.form == form]
    for f in tests:
        for h in HZ:
            d = X[[f, f"x{h}"]].dropna()
            if len(d) > 25 and d[f].nunique() > 3:
                r, p = spearmanr(d[f], d[f"x{h}"]); rows.append((form, f, h, len(d), r, p))
R = pd.DataFrame(rows, columns=["form", "feature", "h", "n", "rho", "p"])
R["thr"] = 0.05 / len(R); R["pass_p"] = R.p < R.thr
R.to_csv(f"{D}/signal_test_lm.csv", index=False)
print("검정 수", len(R), "합격선 p<%.2g" % (0.05 / len(R)), "| 통과", int(R.pass_p.sum()), "| p<0.05", int((R.p < .05).sum()), "(우연 기대 %.1f)" % (len(R) * .05), "| 최소 p %.4f" % R.p.min())
print(R.sort_values("p").head(12).round(4).to_string(index=False))
# 부호 일치(10-K·10-Q 같은 특징·지평에서 같은 부호이고 둘 다 p<0.1)
P = R.pivot_table(index=["feature", "h"], columns="form", values=["rho", "p"])
ok = P[(np.sign(P[("rho", "10-K")]) == np.sign(P[("rho", "10-Q")])) & (P[("p", "10-K")] < .1) & (P[("p", "10-Q")] < .1)]
print("\n10-K·10-Q 같은 부호 + 둘 다 p<0.1:", len(ok)); print(ok.round(3).to_string())
