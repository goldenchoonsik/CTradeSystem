"""투자자별 순매수(시장 합계) ↔ KOSPI 지수 수익률 상관 분석 (읽기 전용).

A. KIS 종목별 투자자 매매 2024-07-17~2026-10-08 (주체 세분, 금액 백만원) → KOSPI 종목 합계
B. Creon daily_ext 2013~ 외국인(보유수량 일변화≈순매수)·기관 순매수(수량) × 수정 종가 → KOSPI 종목 합계

각 날짜 순매수는 같은 날 KOSPI 종목 전체 거래대금으로 나눈 비율(%)로 정규화한다(시장 규모 성장 제거).
상관은 ① 같은 날 ② 수급(t)→수익률(t+1) ③ 수익률(t)→수급(t+1) ④ 5일 합 → 다음 5일(겹치지 않는 주 단위).

  .venv/bin/python docs/research/20261013_flow_kospi/flow_kospi.py
"""
import glob
import json
import os

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
KR = os.path.join(REPO, "DataVault", "staging", "kr_equity")
FLOWS = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe", "raw", "kis", "flows", "investor")
OUT = os.path.dirname(__file__)

GROUPS = {  # KIS 순매수 금액 필드(백만원)
    "외국인": "frgn_ntby_tr_pbmn", "개인": "prsn_ntby_tr_pbmn", "기관계": "orgn_ntby_tr_pbmn",
    "금융투자": "scrt_ntby_tr_pbmn", "투신": "ivtr_ntby_tr_pbmn", "사모": "pe_fund_ntby_tr_pbmn",
    "은행": "bank_ntby_tr_pbmn", "보험": "insu_ntby_tr_pbmn", "기타금융": "mrbn_ntby_tr_pbmn",
    "연기금": "fund_ntby_tr_pbmn", "기타법인": "etc_corp_ntby_tr_pbmn",
}
REGIMES = [("2013-01", "2015-12"), ("2016-01", "2019-12"), ("2020-01", "2021-12"),
           ("2022-01", "2023-12"), ("2024-01", "2026-10")]


def kospi_codes():
    u = pd.read_csv(os.path.join(KR, "reference", "listed_snapshot.csv"))
    return set(u.loc[u.market == "KOSPI", "code"].str[1:])


def kospi_ret():
    i = pd.read_parquet(os.path.join(KR, "derived", "index_1d.parquet"))
    k = i[i.code == "U001"].set_index("date")["close"].sort_index()
    return np.log(k).diff().rename("ret") * 100


def load_kis(codes):
    rows = []
    for code in sorted(os.listdir(FLOWS)):
        if code not in codes:
            continue
        for fp in glob.glob(os.path.join(FLOWS, code, "*.json")):
            for o in json.load(open(fp))["response"].get("output2") or []:
                if not o.get("stck_bsop_date"):
                    continue
                r = {"code": code, "date": o["stck_bsop_date"], "tv": float(o["acml_tr_pbmn"] or 0) / 1e6}
                r.update({g: float(o[f] or 0) for g, f in GROUPS.items()})
                rows.append(r)
    d = pd.DataFrame(rows).drop_duplicates(["code", "date"])
    d["date"] = pd.to_datetime(d["date"])
    m = d.groupby("date")[["tv", *GROUPS]].sum()
    return m


def load_creon(codes):
    e = pd.read_parquet(os.path.join(KR, "derived", "daily_ext.parquet"),
                        columns=["code", "date", "shares_listed", "foreign_hold_qty", "inst_net_qty"])
    p = pd.read_parquet(os.path.join(KR, "derived", "prices_1d.parquet"), columns=["code", "date", "close", "value"])
    e = e[e.code.str[1:].isin(codes)]
    d = e.merge(p, on=["code", "date"], how="inner").sort_values(["code", "date"])
    g = d.groupby("code")
    dq = g["foreign_hold_qty"].diff()
    # 주식수가 바뀐 날(증자·병합·감자 등)은 보유수량 변화가 매매가 아니므로 제외
    dq[g["shares_listed"].diff().fillna(0) != 0] = np.nan
    d["외국인"] = dq * d["close"] / 1e6
    d["기관계"] = d["inst_net_qty"] * d["close"] / 1e6
    d["tv"] = d["value"] / 1e6
    return d.groupby("date")[["tv", "외국인", "기관계"]].sum(min_count=1)


def md(df):
    cols = [str(c) for c in df.columns]
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def corr_t(x, y):
    m = x.notna() & y.notna()
    n = int(m.sum())
    if n < 20:
        return np.nan, np.nan, n
    r = np.corrcoef(x[m], y[m])[0, 1]
    return r, r * np.sqrt((n - 2) / (1 - r * r)), n


def table(flow, ret, groups):
    """flow: 날짜×주체 순매수 비율(%), ret: KOSPI 로그수익률(%)."""
    df = flow.join(ret, how="inner")
    wk = df.resample("W-FRI").sum(min_count=1).dropna()
    out = []
    for gname in groups:
        f = df[gname]
        row = {"주체": gname}
        for key, (x, y) in {
            "같은날": (f, df.ret),
            "수급→익일": (f, df.ret.shift(-1)),
            "수익률→익일수급": (df.ret, f.shift(-1)),
        }.items():
            r, t, n = corr_t(x, y)
            row[key] = f"{r:+.3f} (t{t:+.1f})"
        r, t, n = corr_t(wk[gname], wk.ret.shift(-1))
        row["주간수급→다음주"] = f"{r:+.3f} (t{t:+.1f})"
        r, t, n = corr_t(wk[gname], wk.ret)
        row["주간 같은주"] = f"{r:+.3f} (t{t:+.1f})"
        row["n일"] = len(df)
        out.append(row)
    return pd.DataFrame(out)


def ratio(m, groups):
    return m[groups].div(m["tv"], axis=0) * 100


def main():
    codes = kospi_codes()
    ret = kospi_ret()
    lines = ["# 투자자별 순매수 ↔ KOSPI 상관 (2026-10-13)", "",
             "순매수 = 같은 날 KOSPI 종목 거래대금 대비 % · 수익률 = KOSPI(U001) 일 로그수익률 % · "
             "t ≈ r·√((n−2)/(1−r²)) (자기상관 미보정, |t|<2 는 잡음 수준으로 볼 것)", ""]

    kis = load_kis(codes)
    kis = kis[kis.index >= "2024-07-17"]
    kr = ratio(kis, list(GROUPS))
    resid = kis[["외국인", "개인", "기관계", "기타법인"]].sum(axis=1).abs().median()
    lines += [f"## A. KIS 주체 세분 ({kis.index.min():%Y-%m-%d} ~ {kis.index.max():%Y-%m-%d}, {len(kis)}일, "
              f"{len(codes)} KOSPI 종목 중 수집분)", "",
              f"검산: 외국인+개인+기관계+기타법인 합의 중앙 |잔차| = {resid:,.0f}백만원/일 "
              f"(거래대금 중앙 {kis.tv.median():,.0f}백만원) → 주체 합 ≈ 0 성립", "",
              md(table(kr, ret, list(GROUPS))), ""]
    lvl = kis[list(GROUPS)].sum() / 1e6
    lines += ["기간 누적 순매수(조원): " + ", ".join(f"{g} {v:+.1f}" for g, v in lvl.items()), ""]

    cr = load_creon(codes)
    cr = cr[cr.index >= "2013-01-03"]
    crr = ratio(cr, ["외국인", "기관계"])
    both = crr.join(kr[["외국인", "기관계"]], rsuffix="_kis", how="inner")
    v1 = corr_t(both["외국인"], both["외국인_kis"])[0]
    v2 = corr_t(both["기관계"], both["기관계_kis"])[0]
    lines += [f"## B. Creon 장기 외국인·기관 ({cr.index.min():%Y-%m-%d} ~ {cr.index.max():%Y-%m-%d})", "",
              f"검증: 겹치는 {len(both)}일에서 Creon 근사 vs KIS 실제 순매수 상관 — 외국인 {v1:+.3f}, 기관계 {v2:+.3f}", "",
              "### 전체 기간", "", md(table(crr, ret, ["외국인", "기관계"])), "",
              "### 체제별 (같은날 / 수급→익일 / 주간수급→다음주)", ""]
    reg = []
    for a, b in REGIMES:
        sub = crr.loc[a:b]
        t = table(sub, ret, ["외국인", "기관계"])
        for _, r in t.iterrows():
            reg.append({"구간": f"{a}~{b}", "주체": r["주체"], "같은날": r["같은날"], "수급→익일": r["수급→익일"],
                        "주간수급→다음주": r["주간수급→다음주"], "n일": r["n일"]})
    lines += [md(pd.DataFrame(reg)), ""]

    # 연도별 같은날 상관 + 연 누적 순매수 vs 연 수익률
    yr = []
    df = crr.join(ret, how="inner").join(cr[["외국인", "기관계"]].add_suffix("_억"), how="inner")
    for y, s in df.groupby(df.index.year):
        yr.append({"연도": y, "KOSPI %": round((np.exp(s.ret.sum() / 100) - 1) * 100, 1),
                   "외국인 순매수(조)": round(s["외국인_억"].sum() / 1e6, 1),
                   "기관 순매수(조)": round(s["기관계_억"].sum() / 1e6, 1),
                   "외국인 같은날 r": round(corr_t(s["외국인"], s.ret)[0], 2),
                   "외국인→익일 r": round(corr_t(s["외국인"], s.ret.shift(-1))[0], 3)})
    lines += ["### 연도별", "", md(pd.DataFrame(yr)), ""]

    with open(os.path.join(OUT, "results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
