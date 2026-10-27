"""보조(사전등록 밖, 탐색): '이익이 오르면 주가가 오를 확률' — 같은 기간(사후) vs 미리 알 수 있는 이익(사전).

지수(Shiller 월, 1881~): 12개월 실질 총수익 > 0 확률을 (a) 같은 12개월 실질 EPS 성장 부호별(사후), (b) 이미 발표된 직전 12개월 EPS 성장 부호별(사전, 3개월 지연)로.
종목(S&P500 2010~, 분기): 시장 중앙값을 이길 확률을 (a) 같은 기간 이익 변화 5분위별(사후, pf_*), (b) 과거 1년 이익 변화 5분위별(사전)로.
출력 results_earnings_prob.json.  실행: .venv/bin/python -I earnings_prob.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT  # noqa: E402

ERAS = {"1881~1949": ("1881-01", "1949-12"), "1950~1997": ("1950-01", "1997-12"), "1998~2026": ("1998-01", "2026-10"), "전체": ("1881-01", "2026-10")}


def index_part():
    p = pd.read_csv(os.path.join(ROOT, "staging", "us_valuation", "derived", "panel_monthly.csv"), index_col="ym")
    p.index = pd.PeriodIndex(p.index, freq="M")
    ltr, le = np.log(p.TR_real), np.log(p.E_real)
    ret_same = ltr - ltr.shift(12)                 # t-12 → t
    g_same = le - le.shift(12)                     # 같은 12개월 EPS 성장(사후에만 앎)
    ret_next = ltr.shift(-12) - ltr                # t → t+12
    g_known = le.shift(3) - le.shift(15)           # t 시점에 발표돼 있는 직전 12개월 성장
    g_next = le.shift(-12) - le                    # t → t+12 EPS 성장(미래)
    out = {}
    for lab, (a, b) in ERAS.items():
        d = pd.DataFrame({"rs": ret_same, "gs": g_same, "rn": ret_next, "gk": g_known, "gn": g_next}).loc[a:b]
        s, k, n = d.dropna(subset=["rs", "gs"]), d.dropna(subset=["rn", "gk"]), d.dropna(subset=["rn", "gn"])
        out[lab] = {
            "기본 상승확률(12개월)": round(float((s.rs > 0).mean()), 2),
            "[사후] 같은 기간 EPS↑ 일 때 상승확률": round(float((s.rs[s.gs > 0] > 0).mean()), 2),
            "[사후] 같은 기간 EPS↓ 일 때 상승확률": round(float((s.rs[s.gs <= 0] > 0).mean()), 2),
            "[사후] EPS↑ 비율": round(float((s.gs > 0).mean()), 2),
            "[사전] 발표된 직전 성장↑ → 다음 12개월 상승확률": round(float((k.rn[k.gk > 0] > 0).mean()), 2),
            "[사전] 발표된 직전 성장↓ → 다음 12개월 상승확률": round(float((k.rn[k.gk <= 0] > 0).mean()), 2),
            "[미래] 다음 12개월 EPS↑ 일 때 상승확률": round(float((n.rn[n.gn > 0] > 0).mean()), 2),
            "[미래] 다음 12개월 EPS↓ 일 때 상승확률": round(float((n.rn[n.gn <= 0] > 0).mean()), 2),
            "직전 성장↑ → 다음 12개월도 EPS↑ 확률": round(float((n.join(k.gk, rsuffix="_")["gn"][k.gk.reindex(n.index) > 0] > 0).mean()), 2),
        }
    last = p.E_real.dropna()
    out["현재"] = {"최근 발표 12개월 실질 EPS 성장": round(float(np.log(last.iloc[-1] / last.iloc[-13])), 3), "기준월": str(last.index[-1])}
    return out


def stock_part():
    P = pd.read_parquet(os.path.join(ROOT, "staging", "us_multiples", "derived", "panel.parquet"))
    out = {}
    for lab, sig, ret in (("[사후] 같은 1년 이익변화 → 1년 수익", "pf_deps1_p", "ret1"),
                          ("[사후] 같은 3년 이익변화 → 3년 수익", "pf_deps3_p", "ret3"),
                          ("[사전] 과거 1년 이익변화 → 다음 1년 수익", "eps_chg1_past", "ret1")):
        d = P[["start", sig, ret]].replace([np.inf, -np.inf], np.nan).dropna().copy()
        d["q"] = d.groupby("start")[sig].rank(pct=True)
        d["win"] = d[ret] > d.groupby("start")[ret].transform("median")
        d["pos"] = d[ret] > 0
        row = {}
        for name, m in (("상위20%", d.q > 0.8), ("중간", (d.q > 0.4) & (d.q <= 0.6)), ("하위20%", d.q <= 0.2)):
            row[name] = {"시장중앙값 이길 확률": round(float(d.win[m].mean()), 2), "플러스 확률": round(float(d.pos[m].mean()), 2)}
        out[lab] = row
    return out


def main():
    res = {"지수(Shiller 1881~, 실질 총수익)": index_part(), "종목(S&P500 2010~)": stock_part()}
    with open(os.path.join(HERE, "results_earnings_prob.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
