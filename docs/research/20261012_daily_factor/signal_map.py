"""개발 구간 신호 지도 (2016-04-29 ~ 2023-12-29 형성, 92개월) — 사전등록 확정 전 탐색. 봉인 구간(2024-01~)은 읽지 않는다.

  .venv/bin/python docs/research/20261012_daily_factor/signal_map.py  →  results_signal_map_dev.md / signal_map_dev.csv

신호마다 매월 말 유니버스(사전등록 초안의 필터와 동일) 안에서 5분위를 나누고, t+1 종가 체결 → 다음 형성일 t'+1 종가 청산의
동일가중 수익으로 '상위20% − 하위20%' 스프레드와 순위 IC 를 본다. 비용 없음(순수 신호 세기). 패널만 읽는다(원본 수정 없음).

외국인 흐름 두 정의: FOR_nM = 보유율(%p) 변화, FOR_TVnM = 순매수 금액(보유수량 일변화×수정종가, 주식수 바뀐 날 제외) / 같은 기간 거래대금.
흐름이 가격 추종(모멘텀·반전)의 그림자인지 가르려고 월별 횡단면 회귀(Fama-MacBeth, 신호는 월내 백분위−0.5)를 통제 변수와 함께 본다.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DER = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
OUT = os.path.dirname(os.path.abspath(__file__))
DEV_START, DEV_END = pd.Timestamp("2016-04-29"), pd.Timestamp("2023-12-29")
LOAD_FROM, LOAD_TO = pd.Timestamp("2015-01-01"), pd.Timestamp("2024-02-15")   # 12M 룩백 + 마지막 보유월(2024-01) 청산
REGIMES = [("2016-04~2019", "2016-04-01", "2019-12-31"), ("2020~21", "2020-01-01", "2021-12-31"), ("2022~23", "2022-01-01", "2023-12-31")]
MIN_PRICE, MIN_ADV, LIMIT = 1000, 5e8, 0.29


def md(df):
    df = df.copy()
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join([""] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, r in df.iterrows():
        out.append("| " + " | ".join([str(i)] + ["" if pd.isna(v) else (f"{v:.2f}" if isinstance(v, (float, np.floating)) else str(v)) for v in r]) + " |")
    return "\n".join(out)


def tstat(x):
    x = pd.Series(x).dropna()
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan


def wide(df, col, cal, codes):
    w = df.pivot(index="date", columns="code", values=col).reindex(index=cal, columns=codes)
    return w.astype("float32")


def main():
    t0 = time.time()
    uni = pd.read_csv(os.path.join(DER, "..", "universe.csv"), dtype=str)
    common = set(uni[(uni.is_preferred == "0") & (uni.is_spac == "0")].code)

    p = pd.read_parquet(os.path.join(DER, "prices_1d.parquet"), columns=["code", "date", "close", "close_unadj", "volume", "value"])
    p = p[(p.date >= LOAD_FROM) & (p.date <= LOAD_TO)]
    p["code"] = p.code.astype(str)
    first_date = pd.read_parquet(os.path.join(DER, "prices_1d.parquet"), columns=["code", "date"]).groupby("code", observed=True).date.min()
    first_date.index = first_date.index.astype(str)
    idx = pd.read_parquet(os.path.join(DER, "index_1d.parquet"), columns=["code", "date", "close"])
    cal = idx[(idx.code == "U001") & (idx.date >= LOAD_FROM) & (idx.date <= LOAD_TO)].date.sort_values().reset_index(drop=True)
    kospi = idx[idx.code == "U001"].set_index("date").close.reindex(cal)
    codes = sorted(set(p.code) & common)
    C = wide(p, "close", cal, codes)
    CU = wide(p, "close_unadj", cal, codes).fillna(C)                  # 네이버(상폐)는 무수정 없음 → 수정종가로 대체
    V = wide(p, "volume", cal, codes)
    VAL = wide(p, "value", cal, codes).fillna(C * V)
    del p
    e = pd.read_parquet(os.path.join(DER, "daily_ext.parquet"), columns=["code", "date", "market_cap", "shares_listed", "foreign_hold_qty", "foreign_hold_pct", "inst_net_qty"])
    e = e[(e.date >= LOAD_FROM) & (e.date <= LOAD_TO)]
    e["code"] = e.code.astype(str)
    MC = wide(e, "market_cap", cal, codes).replace(0, np.nan)
    SH = wide(e, "shares_listed", cal, codes).replace(0, np.nan)
    FP = wide(e, "foreign_hold_pct", cal, codes)
    INST = wide(e, "inst_net_qty", cal, codes)
    FQ = wide(e, "foreign_hold_qty", cal, codes)
    del e
    # 외국인 순매수 금액(근사): 보유수량 일변화 × 수정종가. 주식수가 바뀐 날(증자·분할·감자)은 매매가 아니므로 제외
    FNET = (FQ.diff() * C).where(SH.diff().fillna(0) == 0)
    INETV = INST * C
    del FQ
    print(f"loaded {len(codes)} codes × {len(cal)} days in {time.time() - t0:.0f}s", flush=True)

    # 재무: 접수일 기준 최신 보고서 → BM·OPA. 영업이익 TTM = 연속 4분기 합(기간말 간격 검사), 없으면 사업보고서 연간치
    f = pd.read_parquet(os.path.join(DER, "fundamentals_q.parquet"),
                        columns=["stock_code", "q", "period_end", "rcept_dt", "equity", "assets", "op_income", "op_income_annual", "sales", "sales_annual"])
    f = f.sort_values(["stock_code", "period_end"]).reset_index(drop=True)
    g = f.groupby("stock_code")
    ttm = g.op_income.rolling(4).sum().reset_index(level=0, drop=True)
    span = (f.period_end - g.period_end.shift(3)).dt.days
    ttm[~span.between(255, 290)] = np.nan
    f["op_ttm"] = ttm.where(ttm.notna(), f.op_income_annual.where(f.q == 4))
    f["sales_known"] = f.sales.notna() | f.sales_annual.notna()
    f["code"] = "A" + f.stock_code
    f = f.sort_values(["code", "rcept_dt", "period_end"]).drop_duplicates(["code", "rcept_dt"], keep="last")
    fin = f[["code", "rcept_dt", "equity", "assets", "op_ttm", "sales_known"]].sort_values("rcept_dt")

    R = C.pct_change(fill_method=None)
    CFF = C.ffill()
    pos = {d: i for i, d in enumerate(cal)}
    month_end = cal.groupby(cal.dt.to_period("M")).max()
    forms = [d for d in month_end if DEV_START <= d <= DEV_END]
    nxt = {forms[k]: month_end[month_end > forms[k]].iloc[0] for k in range(len(forms))}
    assert nxt[forms[-1]] == pd.Timestamp("2024-01-31"), nxt[forms[-1]]

    rows = []
    for t in forms:
        i, j = pos[t], pos[nxt[t]]
        c, cu = C.iloc[i].astype("float64"), CU.iloc[i].astype("float64")
        win = R.iloc[i - 20:i + 1].values.astype("float64")
        valid = np.isfinite(win).sum(0)
        srt = np.sort(np.where(np.isfinite(win), win, -np.inf), axis=0)
        max5 = np.where(valid >= 15, srt[-5:].mean(0), np.nan)
        vol21 = np.where(valid >= 15, np.nanstd(win, axis=0, ddof=1) * np.sqrt(252), np.nan)
        v20 = V.iloc[i - 19:i + 1]
        d = pd.DataFrame({
            "code": codes, "t": t, "close": c.values, "price_u": cu.values,
            "mcap": MC.iloc[i].astype("float64").values, "shares": SH.iloc[i].astype("float64").values,
            "adv20": VAL.iloc[i - 19:i + 1].mean().values, "zero_vol20": (v20.fillna(0) <= 0).sum().values,
            "MAX5": max5, "VOL21": vol21,
            "MOM12_1": (C.iloc[i - 21] / C.iloc[i - 252] - 1).astype("float64").values,
            "REV1": (c / C.iloc[i - 21].astype("float64") - 1).values,
            "FOR_LVL": FP.iloc[i].astype("float64").values,
            "FOR_1M": (FP.iloc[i] - FP.iloc[i - 21]).astype("float64").values,
            "FOR_3M": (FP.iloc[i] - FP.iloc[i - 63]).astype("float64").values,
            "FOR_6M": (FP.iloc[i] - FP.iloc[i - 126]).astype("float64").values,
            "INST_1M": (INST.iloc[i - 20:i + 1].sum(min_count=15) / SH.iloc[i]).astype("float64").values,
            "INST_3M": (INST.iloc[i - 62:i + 1].sum(min_count=45) / SH.iloc[i]).astype("float64").values,
            "TURN20": (v20.mean() / SH.iloc[i]).astype("float64").values,
            "FOR_TV1M": (FNET.iloc[i - 20:i + 1].sum(min_count=15) / VAL.iloc[i - 20:i + 1].sum()).astype("float64").values,
            "FOR_TV3M": (FNET.iloc[i - 62:i + 1].sum(min_count=45) / VAL.iloc[i - 62:i + 1].sum()).astype("float64").values,
            "INST_TV1M": (INETV.iloc[i - 20:i + 1].sum(min_count=15) / VAL.iloc[i - 20:i + 1].sum()).astype("float64").values,
            "entry": C.iloc[i + 1].astype("float64").values, "entry_vol": V.iloc[i + 1].values,
            "exit": CFF.iloc[j + 1].astype("float64").values,
        })
        d["SIZE"] = np.log(d.mcap)
        d = d[d.close.notna()]
        fm = pd.merge_asof(d[["code", "t"]].sort_values("t"), fin, left_on="t", right_on="rcept_dt", by="code", direction="backward")
        d = d.merge(fm.drop(columns="t"), on="code", how="left")
        d["fin_age"] = (d.t - d.rcept_dt).dt.days
        d["BM"] = d.equity / d.mcap
        d["OPA"] = d.op_ttm / d.assets
        d["listed_12m"] = first_date.reindex(d.code).values <= (t - pd.Timedelta(days=365))
        base = d.listed_12m & (d.price_u >= MIN_PRICE) & (d.adv20 >= MIN_ADV) & (d.zero_vol20 == 0) & (d.fin_age <= 456) \
            & (d.equity > 0) & d.sales_known.fillna(False).astype(bool) & d.mcap.notna()
        size_cut = d.loc[base, "mcap"].quantile(0.2)
        d["in_uni"] = base & (d.mcap > size_cut)
        entry_ok = d.entry.notna() & (d.entry_vol > 0) & ((d.entry / d.close - 1).abs() < LIMIT)
        d["ret"] = np.where(entry_ok, d.exit / d.entry - 1, np.nan)
        d = d[d.in_uni & d.ret.notna()]
        rows.append(d)
        print(f"{t.date()} universe {len(d):,} ({time.time() - t0:.0f}s)", flush=True)
    P = pd.concat(rows, ignore_index=True)
    P.to_parquet(os.path.join(OUT, "signal_map_dev_panel.parquet"), index=False)

    # 복합(초안 V0)과 외국인 추가 복합 — 참고용
    def rk(s):
        return s.groupby(P.t).rank(pct=True)
    P["V0_draft"] = (rk(P.BM) + rk(P.OPA) + (1 - rk(P.MAX5))) / 3
    P["V0+FOR3M"] = (rk(P.BM) + rk(P.OPA) + (1 - rk(P.MAX5)) + rk(P.FOR_3M)) / 4
    SIGNALS = {"BM": "높을수록", "OPA": "높을수록", "MAX5": "낮을수록(복권형 회피)", "SIZE": "작을수록(규모)", "MOM12_1": "높을수록",
               "REV1": "낮을수록(단기 반전)", "VOL21": "낮을수록(저변동)", "TURN20": "낮을수록(저회전)",
               "FOR_LVL": "높을수록", "FOR_1M": "높을수록(외국인 순매수)", "FOR_3M": "높을수록", "FOR_6M": "높을수록",
               "FOR_TV1M": "높을수록(외국인 순매수/거래대금)", "FOR_TV3M": "높을수록",
               "INST_1M": "높을수록(기관 순매수)", "INST_3M": "높을수록", "INST_TV1M": "높을수록(기관 순매수/거래대금)", "V0_draft": "높을수록(초안 복합)", "V0+FOR3M": "높을수록(참고 복합)"}
    b1 = P.groupby("t").ret.mean()
    kret = kospi.reindex([nxt[t] for t in forms]).values / kospi.reindex(forms).values - 1   # 형성일→다음 형성일(참고)
    months = pd.Series(b1.index)
    res, yearly, regimes, quint = [], {}, {}, {}
    for s, direction in SIGNALS.items():
        q = P[["t", "ret", s]].dropna()
        q["q5"] = q.groupby("t")[s].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False) + 1 if len(x) >= 50 else np.nan)
        q = q.dropna(subset=["q5"])
        m = q.groupby(["t", "q5"]).ret.mean().unstack()
        spread = m[5] - m[1]
        ic = q.groupby("t").apply(lambda x: x[s].corr(x.ret, method="spearman"), include_groups=False)
        n = q.groupby("t").size()
        res.append({"signal": s, "direction": direction, "n_avg": n.mean(), "months": len(spread),
                    "IC": ic.mean() * 100, "IC_t": tstat(ic), "spread_%": spread.mean() * 100, "spread_t": tstat(spread),
                    "hit_%": (spread > 0).mean() * 100, "Q5-B1_%": (m[5] - b1.reindex(m.index)).mean() * 100,
                    "Q1-B1_%": (m[1] - b1.reindex(m.index)).mean() * 100})
        yearly[s] = spread.groupby(spread.index.year).mean() * 100
        regimes[s] = {name: spread[(spread.index >= a) & (spread.index <= b)].mean() * 100 for name, a, b in REGIMES}
        quint[s] = m.mean() * 100
    T = pd.DataFrame(res).set_index("signal")

    # Fama-MacBeth: 월별 ret ~ 신호 백분위(−0.5 중심) 횡단면 OLS → 계수 시계열 평균·t. 계수 = 하위 끝→상위 끝 이동 시 월수익 차(%)
    CONTROLS = ["REV1", "MOM12_1", "SIZE", "BM", "OPA", "MAX5", "VOL21", "TURN20"]
    def fm(cols):
        need = ["t", "ret"] + cols
        q = P[need].dropna()
        coefs = []
        for t, x in q.groupby("t"):
            if len(x) < 100:
                continue
            X = np.column_stack([np.ones(len(x))] + [x[c].rank(pct=True).values - 0.5 for c in cols])
            b = np.linalg.lstsq(X, x.ret.values, rcond=None)[0]
            coefs.append(pd.Series(b[1:], index=cols, name=t))
        Bm = pd.DataFrame(coefs)
        return Bm, len(q) / max(len(Bm), 1)
    FLOWS = ["FOR_1M", "FOR_3M", "FOR_6M", "FOR_TV1M", "FOR_TV3M", "FOR_LVL", "INST_1M", "INST_TV1M"]
    fmrows = []
    for fl in FLOWS:
        for spec, cols in (("단독", [fl]), ("통제", [fl] + CONTROLS)):
            Bm, nav = fm(cols)
            c = Bm[fl]
            row = {"flow": fl, "spec": spec, "coef_%": c.mean() * 100, "t": tstat(c), "months": len(c), "n_avg": nav}
            for name, a, b in REGIMES:
                row[name] = c[(c.index >= a) & (c.index <= b)].mean() * 100
            fmrows.append(row)
    FM = pd.DataFrame(fmrows).set_index(["flow", "spec"])
    FM.to_csv(os.path.join(OUT, "signal_map_dev_fm.csv"), float_format="%.3f")
    Bc, _ = fm(["FOR_3M"] + CONTROLS)
    FMC = pd.DataFrame({"coef_%": Bc.mean() * 100, "t": Bc.apply(tstat)})
    # 신호 간 상관(월별 스피어만 평균) — 외국인 흐름이 무엇과 겹치나
    cc = ["FOR_1M", "FOR_3M", "FOR_TV1M", "INST_1M", "REV1", "MOM12_1", "SIZE", "BM", "MAX5", "VOL21", "TURN20"]
    CORR = P[["t"] + cc].dropna().groupby("t").apply(lambda x: x[cc].corr(method="spearman"), include_groups=False).groupby(level=1).mean().reindex(cc)[cc]
    T.to_csv(os.path.join(OUT, "signal_map_dev.csv"), float_format="%.3f")
    Y = pd.DataFrame(yearly).T
    RG = pd.DataFrame(regimes).T
    Q = pd.DataFrame(quint).T
    with open(os.path.join(OUT, "results_signal_map_dev.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# 개발 구간 신호 지도 (형성 {DEV_START.date()} ~ {DEV_END.date()}, {len(forms)}개월) — {pd.Timestamp.now():%Y-%m-%d %H:%M}\n\n")
        fh.write("탐색용. 봉인 구간(2024-01~) 미사용. 비용 0. 스프레드 = 매월 상위20% 동일가중 − 하위20% 동일가중(신호 원값 기준, 방향 열의 '낮을수록'은 부호를 뒤집어 읽을 것), "
                 "체결 t+1 종가 → 다음 형성일 t'+1 종가(상폐·정지는 마지막 종가). IC = 월별 스피어만 상관×100.\n\n")
        fh.write(f"- 유니버스(초안 필터: 보통주·상장12M·가격≥{MIN_PRICE:,}·거래대금20일≥{MIN_ADV / 1e8:.0f}억·거래량0 없음·재무 15개월 내·자본>0·매출 있음·시총 하위20% 제외·t+1 체결 가능): "
                 f"월 평균 {P.groupby('t').size().mean():,.0f}종목, 상폐 포함 {P[P.code.isin(set(uni[uni.status == 'delisted'].code))].code.nunique()}종목\n")
        fh.write(f"- B1 유니버스 동일가중 월수익 평균 {b1.mean() * 100:.2f}% (t {tstat(b1):.1f}), KOSPI 형성일간 월수익 평균 {np.nanmean(kret) * 100:.2f}%\n")
        fh.write(f"- 흐름 신호(FOR_*·INST_*)는 Creon daily_ext 가 있는 상장 종목만 → 상폐 종목은 그 신호에서 빠짐(생존편향 방향으로 유리할 수 있음)\n\n")
        fh.write("## 신호별 요약 (월 %)\n\n" + T.pipe(md) + "\n\n")
        fh.write("## 체제별 스프레드 (월 % 평균)\n\n" + RG.pipe(md) + "\n\n")
        fh.write("## 연도별 스프레드 (월 % 평균)\n\n" + Y.pipe(md) + "\n\n")
        fh.write("## 5분위 평균 월수익 (%) — 1=신호 하위, 5=상위\n\n" + Q.pipe(md) + "\n\n")
        fh.write("## 흐름 신호 Fama-MacBeth (월 %, 계수 = 신호 하위 끝→상위 끝 이동 시 다음달 수익 차)\n\n"
                 f"통제 = {', '.join(CONTROLS)} (각각 월내 백분위). '통제'에서도 살아남아야 가격 추종의 그림자가 아니다.\n\n" + FM.pipe(md) + "\n\n")
        fh.write("### FOR_3M + 통제 전체 계수\n\n" + FMC.pipe(md) + "\n\n")
        fh.write("## 신호 간 월별 스피어만 상관 평균\n\n" + CORR.pipe(md) + "\n\n")
        yb = (b1.groupby(b1.index.year).mean() * 100).round(2)
        fh.write("## B1 연도별 평균 월수익 (%)\n\n" + yb.to_frame("B1").T.pipe(md) + "\n")
    print(T.round(2).to_string())
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
