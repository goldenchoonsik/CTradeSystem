"""시총 구간별 연간 부실 폐지 위험 — 연말(2015~2024) 상장 비스팩 전 종목, 이듬해 안에 부실 폐지로 이어진 거래정지 시작 여부.

상폐 종목 시총(mcap_approx=1)은 DART 주식수 × 네이버 '수정' 종가라, 이후 감자(주식 병합)가 있으면 과거 시총이 부풀려짐
→ 이후 주식수 급감(일간 비율 <0.7)마다 그 비율을 곱해 보정. 주식수 1e10 초과는 오류로 제외.
  .venv/bin/python docs/research/20261014_delisting/mcap_hazard.py
"""
import os
import numpy as np
import pandas as pd

OUT = os.path.dirname(os.path.abspath(__file__))
KR = os.path.join(OUT, "..", "..", "..", "DataVault", "staging", "kr_equity")


def corrected_ext(ext: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for c, g in ext.groupby("code", sort=False):
        g = g.sort_values("date").copy()
        if g.mcap_approx.iloc[-1] == 1:
            sh = g.shares_listed.where(g.shares_listed < 1e10).ffill()
            r = (sh / sh.shift(1)).fillna(1.0)
            drop = r.where(r < 0.7, 1.0)
            later = drop[::-1].cumprod()[::-1].shift(-1).fillna(1.0)     # t 이후 급감 비율의 곱
            g["market_cap"] = g.market_cap.where(g.shares_listed < 1e10) * later
        parts.append(g)
    return pd.concat(parts)


def main():
    S = pd.read_csv(os.path.join(OUT, "samples.csv"), dtype={"code": str}, parse_dates=["t", "halt"])
    D = S[S.group == "부실"][["code", "name", "halt", "type"]]
    u = pd.read_csv(os.path.join(KR, "universe.csv"), dtype=str)
    u = u[(u.is_spac == "0") & (u.is_preferred == "0")]
    ext = pd.read_parquet(os.path.join(KR, "derived", "daily_ext.parquet"),
                          columns=["code", "date", "shares_listed", "market_cap", "mcap_approx"])
    ext["code"] = ext.code.astype(str).str[1:]
    ext = ext[ext.code.isin(set(u.code.str[1:]))]
    ext = corrected_ext(ext)
    rows = []
    for y in range(2015, 2025):
        t = pd.Timestamp(y, 12, 31)
        e = ext[(ext.date > t - pd.Timedelta(days=10)) & (ext.date <= t)].sort_values("date").groupby("code").tail(1)
        e = e[~e.code.isin(set(D[D.halt <= t].code))]
        ev = set(D[(D.halt > t) & (D.halt <= t + pd.DateOffset(years=1))].code)
        rows.append(e.assign(y=y, ev=e.code.isin(ev)))
    A = pd.concat(rows).dropna(subset=["market_cap"])
    bins = [0, 300e8, 500e8, 1000e8, 1300e8, 2000e8, 5000e8, 1e10 * 1e8]
    lab = ["<300억", "300~500억", "500~1000억", "1000~1300억", "1300~2000억", "2000~5000억", "5000억+"]
    A["b"] = pd.cut(A.market_cap, bins, labels=lab)
    g = A.groupby("b", observed=True).agg(종목연=("ev", "size"), 부실=("ev", "sum"))
    g["연간위험"] = (g.부실 / g.종목연).map("{:.2%}".format)
    big = A[A.ev & (A.market_cap >= 1300e8)].merge(D, on="code")
    big = big.assign(시총억=(big.market_cap / 1e8).round()).sort_values("시총억", ascending=False)
    L = ["# 시총 구간별 연간 부실 폐지 위험 (연말 2015~2024 → 이듬해 정지 시작)\n",
         f"전체 종목-연 {len(A)}, 부실 {int(A.ev.sum())} ({A.ev.mean():.2%}/년). 상폐 종목 시총은 이후 감자 보정.\n",
         "```", g.to_string(), "```\n", f"시총 1,300억 이상에서 부실 폐지 {len(big)}건:\n", "```",
         big[["name", "y", "시총억", "type"]].to_string(index=False), "```"]
    open(os.path.join(OUT, "results_mcap_hazard.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
