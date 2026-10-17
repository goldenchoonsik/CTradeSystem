"""event_study 합격 건(sue, post[6,20])의 견고성 점검 — 이상치·회사 쏠림·표본 분할."""
import numpy as np, pandas as pd
from pathlib import Path
D = Path(__file__).resolve().parents[3] / "DataVault/staging/us_fundamentals_simfin/derived"
X = pd.read_csv(D / "event_study_panel.csv", parse_dates=["d0"])
X["cq"] = X["d0"].dt.to_period("Q"); X["m"] = X["d0"].dt.to_period("M")
S, W = "sue", "post[6,20]"
Y = X.dropna(subset=[S, W]).copy()
Y = Y[Y.groupby("cq")[S].transform("size") >= 15]
Y["g"] = Y.groupby("cq")[S].transform(lambda v: pd.qcut(v.rank(method="first"), 3, labels=False))

def spread(df, col=W, agg="mean"):
    gm = df.groupby(["m", "g"])[col].agg(agg).unstack().reindex(columns=[0, 1, 2])
    sp = (gm[2] - gm[0]).dropna()
    return sp.mean(), sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp))), len(sp)

print(f"이벤트 {len(Y)}건 / 회사 {Y['id'].nunique()}개 / 월 셀당 이벤트 중앙값 {int(Y.groupby('m').size().median())}")
print("원본              : 스프레드 %+.2f%% t=%+.2f (월 %d)" % (spread(Y)[0]*100, *spread(Y)[1:]))
lo, hi = Y[W].quantile([.01, .99]); Y["w_win"] = Y[W].clip(lo, hi)
print("1/99 퍼센타일 윈저라이즈: 스프레드 %+.2f%% t=%+.2f (월 %d)" % (spread(Y, "w_win")[0]*100, *spread(Y, "w_win")[1:]))
print("중앙값 집계       : 스프레드 %+.2f%% t=%+.2f" % (spread(Y, W, "median")[0]*100, spread(Y, W, "median")[1]))
print("W 분포: 5%%/50%%/95%% =", [f"{v:.1%}" for v in Y[W].quantile([.05, .5, .95])], " 최대 %.0f%% 최소 %.0f%%" % (Y[W].max()*100, Y[W].min()*100))
# 회사 쏠림: 이벤트 많은 상위 회사 제거, 회사별 한 번씩 leave-one-out 최악
cnt = Y["id"].value_counts()
print("이벤트 상위 5개 회사 비중 %.0f%%" % (100 * cnt.head(5).sum() / len(Y)))
Z = Y[~Y["id"].isin(cnt.head(5).index)]
print("상위5개 회사 제외 : 스프레드 %+.2f%% t=%+.2f (이벤트 %d)" % (spread(Z)[0]*100, spread(Z)[1], len(Z)))
worst = min((spread(Y[Y.id != i])[1], i) for i in Y["id"].unique())
print("한 회사씩 제외 중 최저 t = %.2f" % worst[0])
# 극단 이벤트 제거: |W| 상위 5% 제외
Q = Y[Y[W].abs() <= Y[W].abs().quantile(.95)]
print("극단 5%% 제외      : 스프레드 %+.2f%% t=%+.2f" % (spread(Q)[0]*100, spread(Q)[1]))
# 연도별
Y["yr"] = Y["d0"].dt.year
print("연도별 스프레드:", {int(y): f"{spread(g)[0]:+.1%}(월{spread(g)[2]})" for y, g in Y.groupby("yr") if spread(g)[2] >= 4})
# 회사 군집 부트스트랩 (회사 단위 재표본 1000회)
rng = np.random.default_rng(0); ids = Y["id"].unique(); bs = []
for _ in range(1000):
    pick = rng.choice(ids, len(ids), replace=True)
    B = pd.concat([Y[Y.id == i] for i in pick])
    bs.append(spread(B)[0])
print("회사 단위 부트스트랩 스프레드 95%% 구간: [%+.2f%%, %+.2f%%]" % tuple(np.percentile(bs, [2.5, 97.5]) * 100))
