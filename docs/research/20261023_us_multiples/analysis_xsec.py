"""단면 분석(사전등록 질문 1~4): 분기별 순위상관(IC) 평균·Newey-West t·두 구간 일관성·5분위 차.
입력 DataVault/staging/us_multiples/derived/panel.parquet.  출력 results_xsec.json
실행: .venv/bin/python -I analysis_xsec.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT  # noqa: E402

P = pd.read_parquet(os.path.join(ROOT, "staging", "us_multiples", "derived", "panel.parquet"))
P["lmcap"] = np.log(P.mcap)
P["lpb"] = np.log(P.pb)
P["lpe"] = np.log(P.pe)
SPLIT = pd.Timestamp("2018-01-01")


def nw_t(x, lags):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 8:
        return np.nan
    e = x - x.mean()
    s = (e @ e) / n
    for L in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(x.mean() / np.sqrt(s / n))


def ic_series(a, b, cond=None):
    d = P[["start", a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if cond is not None:
        d = d[cond(d)]
    return d.groupby("start").apply(lambda g: g[a].rank().corr(g[b].rank()) if len(g) >= 30 else np.nan,
                                    include_groups=False).dropna()


def summarize(a, b, lags, cond=None):
    s = ic_series(a, b, cond)
    if s.empty:
        return None
    f, k = s[s.index < SPLIT], s[s.index >= SPLIT]
    tag = "일관" if (len(f) and len(k) and np.sign(f.mean()) == np.sign(k.mean()) and abs(f.mean()) >= 0.03 and abs(k.mean()) >= 0.03) else "불안정"
    return {"IC": round(float(s.mean()), 3), "t": round(nw_t(s.values, lags), 2), "분기": int(len(s)),
            "앞2010~17": round(float(f.mean()), 3), "뒤2018~": round(float(k.mean()), 3), "판정": tag}


def quint_spread(a, ret, yrs):
    d = P[["start", a, ret]].replace([np.inf, -np.inf], np.nan).dropna()
    def one(g):
        if len(g) < 50:
            return np.nan
        q = g[a].rank(pct=True)
        hi, lo = g[ret][q > 0.8].mean(), g[ret][q <= 0.2].mean()
        return (1 + hi) ** (1 / yrs) - (1 + lo) ** (1 / yrs)
    s = d.groupby("start").apply(one, include_groups=False).dropna()
    return {"전체%p": round(100 * float(s.mean()), 2), "앞%p": round(100 * float(s[s.index < SPLIT].mean()), 2),
            "뒤%p": round(100 * float(s[s.index >= SPLIT].mean()), 2)}


def r2_series(y, x, cond=None):
    d = P[["start", y, x]].replace([np.inf, -np.inf], np.nan).dropna()
    if cond is not None:
        d = d[cond(d)]
    def one(g):
        if len(g) < 30:
            return np.nan
        gx = g[x].clip(g[x].quantile(0.01), g[x].quantile(0.99))
        return np.corrcoef(gx, g[y])[0, 1] ** 2
    s = d.groupby("start").apply(one, include_groups=False).dropna()
    return round(float(s.mean()), 3)


def main():
    res = {}
    # 1 동행
    res["1_동행"] = {
        "logPBR vs ROE(현재) IC": summarize("lpb", "roe", 3),
        "logPBR vs ROE(3년 뒤) IC": summarize("lpb", "roe_f3", 11),
        "logPBR ~ ROE(현재) R²": r2_series("lpb", "roe", lambda d: d.roe.between(-0.5, 1.0)),
        "logPBR ~ ROE(3년 뒤) R²": r2_series("lpb", "roe_f3", lambda d: d.roe_f3.between(-0.5, 1.0)),
        "logPER vs 과거3년 매출성장 IC": summarize("lpe", "rev_g3", 3),
        "logPER vs 앞3년 EPS성장(흑자) IC": summarize("lpe", "leps3", 11),
        "logPER ~ 앞3년 EPS성장 R²": r2_series("lpe", "leps3"),
        "logPER vs 앞1년 EPS성장(흑자) IC": summarize("lpe", "leps1", 3),
    }
    # 2 현재 지표 → 미래 지표 (시장 기대가 맞았나)
    res["2_현재→미래지표"] = {
        "E/P → 앞1년 EPS 성장": summarize("ep", "leps1", 3),
        "E/P → 앞3년 EPS 성장": summarize("ep", "leps3", 11),
        "E/P → 앞3년 이익변화/가격": summarize("ep", "deps3_p", 11),
        "B/P → 1년 뒤 ROE": summarize("bp", "roe_f1", 3),
        "B/P → 3년 뒤 ROE": summarize("bp", "roe_f3", 11),
        "S/P → 앞3년 매출 성장": summarize("sp", "lrev3", 11),
        "ROE → 3년 뒤 ROE(지속성)": summarize("roe", "roe_f3", 11),
        "과거3년 매출성장 → 앞3년 매출성장": summarize("rev_g3", "lrev3", 11),
        "과거1년 이익변화 → 앞1년 이익변화": summarize("eps_chg1_past", "deps1_p", 3),
        "[맞춤] logPER → 같은 회계연도 1년 EPS 성장": summarize("lpe", "pf_leps1", 3),
        "[맞춤] logPER → 3년 EPS 성장": summarize("lpe", "pf_leps3", 11),
    }
    # 3 현재 지표 → 미래 수익률
    feats = {"E/P(=1/PER)": "ep", "B/P(=1/PBR)": "bp", "S/P(=1/PSR)": "sp", "CF/P": "cfp", "D/P(배당)": "dp",
             "ROE": "roe", "총이익/자산": "gpa", "과거3년 매출성장": "rev_g3", "과거1년 이익변화/가격": "eps_chg1_past", "시총(log)": "lmcap"}
    res["3_현재→수익률"] = {k: {"1년 IC": summarize(v, "ret1", 3), "3년 IC": summarize(v, "ret3", 11),
                              "5분위차 1년": quint_spread(v, "ret1", 1), "5분위차 3년(연)": quint_spread(v, "ret3", 3)} for k, v in feats.items()}
    # 4 완전 예지(미래참조 의도적)
    res["4_미래지표→같은기간수익률(완전예지)"] = {
        "앞1년 이익변화/가격 → 1년 수익": {"IC": summarize("deps1_p", "ret1", 3), "5분위차": quint_spread("deps1_p", "ret1", 1)},
        "앞3년 이익변화/가격 → 3년 수익": {"IC": summarize("deps3_p", "ret3", 11), "5분위차(연)": quint_spread("deps3_p", "ret3", 3)},
        "앞3년 EPS 성장(흑자) → 3년 수익": {"IC": summarize("leps3", "ret3", 11)},
        "앞3년 매출 성장 → 3년 수익": {"IC": summarize("lrev3", "ret3", 11), "5분위차(연)": quint_spread("lrev3", "ret3", 3)},
        "3년 뒤 ROE → 3년 수익": {"IC": summarize("roe_f3", "ret3", 11)},
        "앞1년 EPS 성장(흑자) → 1년 수익(보조)": {"IC": summarize("leps1", "ret1", 3)},
        "[맞춤] 같은 회계연도 이익변화/가격 → 1년 수익": {"IC": summarize("pf_deps1_p", "ret1", 3), "5분위차": quint_spread("pf_deps1_p", "ret1", 1)},
        "[맞춤] 같은 회계연도 EPS 성장(흑자) → 1년 수익": {"IC": summarize("pf_leps1", "ret1", 3)},
        "[맞춤] 3년 이익변화/가격 → 3년 수익": {"IC": summarize("pf_deps3_p", "ret3", 11), "5분위차(연)": quint_spread("pf_deps3_p", "ret3", 3)},
        "[맞춤] 3년 EPS 성장(흑자) → 3년 수익": {"IC": summarize("pf_leps3", "ret3", 11)},
    }
    # 보조(사전등록 밖): PER 이 이미 반영한 성장 기대를 뺀 '놀람' — 분기마다 앞3년 log EPS 성장을 log PER 에 회귀한 잔차
    def resid(g):
        g = g.dropna(subset=["lpe", "leps3"])
        if len(g) < 30:
            return pd.Series(dtype=float)
        b = np.polyfit(g.lpe, g.leps3, 1)
        return g.leps3 - (b[0] * g.lpe + b[1])
    P["surp3"] = P[P.pe > 0].groupby("start", group_keys=False).apply(resid, include_groups=False)
    res["4_미래지표→같은기간수익률(완전예지)"]["앞3년 EPS 성장 중 PER 로 설명 안 되는 몫(놀람) → 3년 수익(보조)"] = {
        "IC": summarize("surp3", "ret3", 11), "5분위차(연)": quint_spread("surp3", "ret3", 3)}
    res["_표본"] = {"행": int(len(P)), "종목": int(P.ticker.nunique()), "분기": int(P.start.nunique()),
                  "ret1 있음": int(P.ret1.notna().sum()), "ret3 있음": int(P.ret3.notna().sum())}
    with open(os.path.join(HERE, "results_xsec.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    for sec, v in res.items():
        print("\n==", sec)
        if sec.startswith("_"):
            print(v)
            continue
        for k, x in v.items():
            print(" ", k, json.dumps(x, ensure_ascii=False))


if __name__ == "__main__":
    main()
