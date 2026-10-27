"""보조(사전등록 밖, 설명용): '불안감' 지표 비교 — 어떤 불안 척도가 미국 배수(log CAPE) 수준·변화를 설명하나.

척도: VIX(주식 옵션 30일 내재변동성, 1990~), MOVE(국채 옵션 내재변동성, 1988~), 실현 변동성(S&P 일별 수익 월 표준편차, 1928~),
      신용 스프레드(BAA−10년물, 1919~), 물가 변동성(60개월 CPI 변화 표준편차, 1881~), 정책 불확실성 EPU(Baker–Bloom–Davis 뉴스 기반, 1900~).
원천: raw/yahoo/{VIX,MOVE}_20261023.json(월봉), raw/yahoo/GSPC_20261023.json(일봉), raw/epu/US_Policy_Uncertainty_Data_20261023.xlsx(openpyxl 필요: --pylib).
출력 results_uncertainty.json.  실행: .venv/bin/python -I uncertainty.py --pylib <openpyxl 폴더>
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vlib import HERE, panel  # noqa: E402

RAW = os.path.join(os.path.dirname(HERE), "..", "..", "DataVault", "staging", "us_valuation", "raw")
RAW = os.path.abspath(os.path.join(HERE, "..", "..", "..", "DataVault", "staging", "us_valuation", "raw"))


def yahoo(fp, daily=False):
    j = json.load(open(fp))["chart"]["result"][0]
    ts = pd.to_datetime(j["timestamp"], unit="s")
    c = pd.Series(j["indicators"]["quote"][0]["close"], index=ts, dtype=float).dropna()
    if daily:
        return c
    return c.groupby(c.index.to_period("M")).last()


def r2(y, x):
    d = pd.concat([y, x], axis=1).dropna()
    return round(float(d.corr().iloc[0, 1] ** 2), 3) if len(d) > 24 else None, int(len(d))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pylib")
    a = ap.parse_args()
    if a.pylib:
        sys.path.insert(0, a.pylib)
    p = panel()
    M = pd.DataFrame(index=p.index)
    M["lcape"] = np.log(p.CAPE_pit)
    M["dlp"] = np.log(p.P).diff()
    M["VIX"] = yahoo(os.path.join(RAW, "yahoo", "VIX_20261023.json"))
    M["MOVE"] = yahoo(os.path.join(RAW, "yahoo", "MOVE_20261023.json"))
    g = yahoo(os.path.join(RAW, "yahoo", "GSPC_20261023.json"), daily=True)
    lr = np.log(g).diff().dropna()
    M["실현변동성"] = (lr.groupby(lr.index.to_period("M")).std() * np.sqrt(252) * 100)
    M["신용스프레드"] = p.credit
    M["물가변동성"] = p.infl_vol
    e = pd.read_excel(os.path.join(RAW, "epu", "US_Policy_Uncertainty_Data_20261023.xlsx"))
    e = e[pd.to_numeric(e.Year, errors="coerce").notna() & e.Month.notna()]
    e.index = pd.PeriodIndex([f"{int(y)}-{int(m):02d}" for y, m in zip(e.Year, e.Month)], freq="M")
    M["정책불확실성EPU"] = e.iloc[:, 2].astype(float).groupby(level=0).mean()
    meas = ["VIX", "MOVE", "실현변동성", "신용스프레드", "물가변동성", "정책불확실성EPU"]
    for m in meas:
        M[m + "_12m"] = M[m].rolling(12, min_periods=12).mean()
    out = {"기간": {m: f"{M[m].first_valid_index()}~{M[m].last_valid_index()}" for m in meas}}
    # (a) 공통 기간 1990-01~ : 수준 단독 R² (월값 / 12개월 평균)
    rows = {}
    for lab, (s0, s1) in {"1990~2026": ("1990-01", "2026-09"), "1990~2007": ("1990-01", "2007-12"), "2008~2026": ("2008-01", "2026-09")}.items():
        S = M.loc[s0:s1]
        rows[lab] = {m: {"월값R2": r2(S.lcape, S[m])[0], "12개월평균R2": r2(S.lcape, S[m + "_12m"])[0],
                         "부호": ("−" if S[["lcape", m]].corr().iloc[0, 1] < 0 else "+")} for m in meas}
    out["수준_단독R2(log CAPE)"] = rows
    # (b) 긴 역사(1928~) — 실현변동성·신용·물가변동성·EPU
    S = M.loc["1928-01":"2026-09"]
    out["수준_단독R2_1928~"] = {m: {"월값R2": r2(S.lcape, S[m])[0], "12개월평균R2": r2(S.lcape, S[m + "_12m"])[0]}
                             for m in ["실현변동성", "신용스프레드", "물가변동성", "정책불확실성EPU"]}
    # (c) 척도 간 상관(1990~, 12개월 평균) + 공통 성분(1주성분)
    C = M.loc["1990-01":"2026-09", [m + "_12m" for m in meas]].dropna()
    out["척도간상관(1990~,12개월평균)"] = C.corr().round(2).rename(index=lambda x: x[:-4], columns=lambda x: x[:-4]).to_dict()
    Z = (C - C.mean()) / C.std()
    w, v = np.linalg.eigh(np.cov(Z.T.values))
    pc1 = pd.Series(Z.values @ v[:, -1], index=Z.index)
    pc1 *= np.sign(pc1.corr(C["신용스프레드_12m"]))
    out["공통성분"] = {"설명분산비율": round(float(w[-1] / w.sum()), 3),
                    "가중치": dict(zip([m for m in meas], np.round(v[:, -1] * np.sign(pc1.corr(C['신용스프레드_12m']) or 1), 2).tolist())),
                    "log CAPE R²": r2(M.lcape, pc1)[0]}
    # (d) 느림: 12개월 자기상관(월값)
    S = M.loc["1990-01":"2026-09"]
    out["12개월자기상관(느릴수록 1)"] = {m: round(float(S[m].autocorr(12)), 2) for m in meas + ["lcape"]}
    # (e) 월 변화 동행: Δlog 가격 vs Δ척도
    out["월변화_동행R2(1990~)"] = {m: r2(S.dlp, S[m].diff())[0] for m in meas}
    # 현재
    last = M.loc["2026-09"]
    pct = {m: round(float((M[m].dropna() < last[m]).mean()), 3) for m in meas if pd.notna(last[m])}
    out["현재(2026-09)"] = {m: {"값": round(float(last[m]), 2), "역사백분위": pct.get(m)} for m in meas if pd.notna(last[m])}
    with open(os.path.join(HERE, "results_uncertainty.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
