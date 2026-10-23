"""연말 대주주 양도세 회피 매도 → 1월 반등 — 사전등록(PREREGISTRATION.md) 시험.

  .venv/bin/python -I docs/research/20261021_yearend_tax_selling/yearend_test.py --selftest  # 단위 검사(D 날짜·미래참조 섭동·상폐 포함·비용)
  .venv/bin/python -I docs/research/20261021_yearend_tax_selling/yearend_test.py             # 개발(연말 2013~2022) → results_dev.md
  .venv/bin/python -I docs/research/20261021_yearend_tax_selling/yearend_test.py --sealed    # 봉인(연말 2023~2025) 1회 → results_sealed.md (있으면 거부)

개발 모드는 2023-02-28 이후 가격을 아예 읽지 않는다(봉인 구간 누출 차단). 패널만 읽고 원본은 수정하지 않는다.
일정: F=11월 마지막 거래일, L=12월 마지막 거래일, D=L 의 2거래일 전(T+2 결제 매도 마감), X=이듬해 1월 10번째 거래일.
점수 = close(F)/close(F 의 11개월 전 달 마지막 거래일) − 1 (12월은 연초 대비). P = 점수 상위 20%, 대조 = 유니버스 동일가중.
매매: D 종가 매수 → X 종가 매도, 왕복 0.57%(factor_test 와 같은 원장 비용 + 슬리피지 편도 0.15%). 대조·지수는 비용 없음.
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from shared.config.market_policies import DEFAULT_MARKET_POLICIES  # noqa: E402
from shared.core.trade_costs import round_trip_cost  # noqa: E402

KR = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
DER = os.path.join(KR, "derived")
OUT = os.path.dirname(os.path.abspath(__file__))

DEV = dict(years=range(2013, 2023), read_to=pd.Timestamp("2023-02-28"), out="results_dev.md")
SEALED = dict(years=range(2023, 2026), read_to=pd.Timestamp("2026-02-27"), out="results_sealed.md")
THRESH = {2013: 50, 2014: 50, 2015: 25, 2016: 25, 2017: 15, 2018: 15, 2019: 10, 2020: 10, 2021: 10, 2022: 10,
          2023: 50, 2024: 50, 2025: 50}                                       # 연말 Y 에 걸린 코스피 기준(억, 2023 은 12-21 변경)
LOW = {y for y, t in THRESH.items() if t <= 15 and y <= 2022}
MIN_PRICE, MIN_ADV, TOP_Q = 1000, 5e8, 0.2
EXIT_N, EXIT_ALT = 10, (5, 20)
PLACEBO_MONTHS = range(2, 11)

_P = DEFAULT_MARKET_POLICIES["KRX_STOCK"]
BASE_SIDE = _P["default_fee"] + _P["default_slippage"]
SELL_TAX = _P["sell_tax"]
EXTRA_SLIP = 0.0015
assert abs(round_trip_cost(_P["default_fee"], _P["default_slippage"], SELL_TAX) - 0.0027) < 1e-9
COST = 2 * BASE_SIDE + SELL_TAX + 2 * EXTRA_SLIP                              # 0.57%
COST2 = 2 * BASE_SIDE + SELL_TAX + 4 * EXTRA_SLIP                             # 슬리피지 2배 0.87%


# ───────────────────────── 데이터 ─────────────────────────
class Panel:
    def __init__(self, read_to, log=print):
        t0 = time.time()
        uni = pd.read_csv(os.path.join(KR, "universe.csv"), dtype=str)
        common = uni[(uni.is_preferred == "0") & (uni.is_spac == "0")]
        self.market = common.set_index("code").market
        self.delisted = set(common[common.status == "delisted"].code)
        idx = pd.read_parquet(os.path.join(DER, "index_1d.parquet"), columns=["code", "date", "close"])
        idx = idx[idx.date <= read_to]
        self.cal = pd.DatetimeIndex(sorted(idx[idx.code == "U001"].date))
        self.k200 = idx[idx.code == "U180"].set_index("date").close.reindex(self.cal)
        p = pd.read_parquet(os.path.join(DER, "prices_1d.parquet"),
                            columns=["code", "date", "open", "close", "close_unadj", "volume", "value"],
                            filters=[("date", "<=", read_to)])
        p["code"] = p.code.astype(str)
        p = p[p.code.isin(self.market.index)]
        self.codes = sorted(p.code.unique())
        w = lambda df, col: df.pivot(index="date", columns="code", values=col).reindex(index=self.cal, columns=self.codes)
        self.C, self.O, self.CU, self.V = (w(p, c) for c in ("close", "open", "close_unadj", "volume"))
        self.CU = self.CU.fillna(self.C)                                       # 상폐(네이버 출처)는 무수정 종가·거래대금 없음 → factor_test 와 같이 대체
        self.VAL = w(p, "value").fillna(self.CU * self.V)
        del p
        e = pd.read_parquet(os.path.join(DER, "daily_ext.parquet"),
                            columns=["code", "date", "market_cap", "shares_listed", "foreign_hold_qty", "inst_net_qty"],
                            filters=[("date", "<=", read_to)])
        e["code"] = e.code.astype(str)
        e = e[e.code.isin(self.market.index)]
        self.MC = w(e, "market_cap").replace(0, np.nan)
        sh, fq = w(e, "shares_listed"), w(e, "foreign_hold_qty")
        ca = (sh / sh.shift(1) - 1).abs() > 0.01                               # 기업행위일은 보유수량 점프 → 흐름 0 처리
        self.FNET = (fq - fq.shift(1)).mask(ca, 0) * self.CU                   # 외국인 순매수 근사(원)
        self.INET = w(e, "inst_net_qty") * self.CU
        del e
        self.CFF = self.C.ffill()
        self.pos = {d: i for i, d in enumerate(self.cal)}
        self.by_month = pd.Series(self.cal, index=self.cal).groupby(self.cal.to_period("M"))
        log(f"loaded {len(self.codes)} codes × {len(self.cal)} days ({self.cal[0].date()}~{self.cal[-1].date()}) {time.time() - t0:.0f}s")

    def month_days(self, per):
        try:
            return list(self.by_month.get_group(per))
        except KeyError:
            return []


def schedule(pn, per_event, exit_n=EXIT_N, allow_first=False):
    """per_event = 사건 달(12월 또는 위약 m월). 반환 F, D, X(dict n→날짜), S0 또는 None(자료 부족)."""
    f_days, e_days, x_days = pn.month_days(per_event - 1), pn.month_days(per_event), pn.month_days(per_event + 1)
    s_days = pn.month_days(per_event - 12)                                    # F 달의 11개월 전 달
    if not f_days or len(e_days) < 3 or not x_days:
        return None
    x0 = pn.pos[x_days[0]]                                                    # 다음 달 첫 거래일부터 n번째(명절로 달을 넘으면 그대로 이어 센다)
    if x0 + max(exit_n, *EXIT_ALT) - 1 >= len(pn.cal):
        return None
    if s_days:
        s0 = s_days[-1]
    elif allow_first and pn.cal[0] > (per_event - 12).end_time:
        s0 = pn.cal[0]                                                        # 2013 연말만: 패널 첫날
    else:
        return None
    L = e_days[-1]
    D = pn.cal[pn.pos[L] - 2]
    X = {n: pn.cal[x0 + n - 1] for n in (exit_n, *EXIT_ALT)}
    return dict(F=f_days[-1], L=L, D=D, X=X, S0=s0)


def event(pn, s):
    """사건 하나의 종목 표. 선정에는 F 까지 정보만, 체결 가능 여부는 D 에서."""
    i_f, i_d, i_s = pn.pos[s["F"]], pn.pos[s["D"]], pn.pos[s["S0"]]
    c_f = pn.C.iloc[i_f]
    v20 = pn.V.iloc[i_f - 19:i_f + 1]
    elig = c_f.notna() & (pn.V.iloc[i_f] > 0) & (pn.CU.iloc[i_f] >= MIN_PRICE) \
        & (pn.VAL.iloc[i_f - 19:i_f + 1].mean() >= MIN_ADV) & ((v20.fillna(0) <= 0).sum() == 0) \
        & pn.MC.iloc[i_f].notna() & pn.C.iloc[i_s].notna()
    d = pd.DataFrame({"score": c_f / pn.C.iloc[i_s] - 1, "mcap": pn.MC.iloc[i_f]})[elig]
    codes = d.index
    d["retS"] = pn.CFF.iloc[i_d][codes] / c_f[codes] - 1
    c_d = pn.C.iloc[i_d][codes]
    can = c_d.notna() & (pn.V.iloc[i_d][codes] > 0)
    for n, x in s["X"].items():
        d[f"retR{n}"] = (pn.CFF.iloc[pn.pos[x]][codes] / c_d - 1).where(can)
    o1, v1 = pn.O.iloc[i_d + 1][codes], pn.V.iloc[i_d + 1][codes]
    d["retR_open"] = (pn.CFF.iloc[pn.pos[s["X"][EXIT_N]]][codes] / o1 - 1).where(can & o1.notna() & (v1 > 0))
    sl = slice(i_f + 1, i_d + 1)
    val = pn.VAL.iloc[sl][codes].sum()
    d["indiv_flow"] = -(pn.FNET.iloc[sl][codes].sum() + pn.INET.iloc[sl][codes].sum()) / val.replace(0, np.nan)
    d["market"] = pn.market.reindex(codes).values
    d["delisted"] = codes.isin(list(pn.delisted))
    q_hi, q_lo = d.score.quantile(1 - TOP_Q), d.score.quantile(TOP_Q)
    d["grp"] = np.where(d.score >= q_hi, "P", np.where(d.score <= q_lo, "Q1", "mid"))
    d["size3"] = pd.qcut(d.mcap.rank(method="first"), 3, labels=["small", "mid", "large"]).astype(str)
    return d


def summarize_event(pn, s, d):
    P, U, Q1 = d[d.grp == "P"], d, d[d.grp == "Q1"]
    r = lambda g, c: g[c].mean()
    k = lambda a, b: pn.k200[b] / pn.k200[a] - 1
    row = dict(F=s["F"].date(), D=s["D"].date(), X=s["X"][EXIT_N].date(), nU=len(U), nP=len(P),
               nP_buy=int(P[f"retR{EXIT_N}"].notna().sum()), nDelist=int(U.delisted.sum()),
               P_R=r(P, f"retR{EXIT_N}"), U_R=r(U, f"retR{EXIT_N}"), Q1_R=r(Q1, f"retR{EXIT_N}"),
               K200_R=k(s["D"], s["X"][EXIT_N]),
               P_S=r(P, "retS"), U_S=r(U, "retS"), Q1_S=r(Q1, "retS"),
               P_flow=P.indiv_flow.median(), U_flow=U.indiv_flow.median(), Q1_flow=Q1.indiv_flow.median(),
               P_Ropen=r(P, "retR_open"), U_Ropen=r(U, "retR_open"))
    for n in EXIT_ALT:
        row[f"P_R{n}"], row[f"U_R{n}"] = r(P, f"retR{n}"), r(U, f"retR{n}")
    for col, vals in (("market", ("KOSPI", "KOSDAQ")), ("size3", ("small", "mid", "large"))):
        for v in vals:
            sub = d[d[col] == v]
            row[f"x_{v}"] = r(sub[sub.grp == "P"], f"retR{EXIT_N}") - r(sub, f"retR{EXIT_N}")
    row["gross_x"] = row["P_R"] - row["U_R"]
    row["x"] = row["P_R"] - COST - row["U_R"]
    row["x_slip2"] = row["P_R"] - COST2 - row["U_R"]
    row["x_open"] = row["P_Ropen"] - COST - row["U_Ropen"]
    row["S_x"] = row["P_S"] - row["U_S"]
    return row


def run_years(pn, years, log=print):
    rows, placebo, events = [], [], {}
    for y in years:
        s = schedule(pn, pd.Period(f"{y}-12", "M"), allow_first=True)
        assert s is not None, f"{y} 일정 자료 부족"
        d = event(pn, s)
        events[y] = d
        rows.append(dict(year=y, thresh=THRESH[y], **summarize_event(pn, s, d)))
        for m in PLACEBO_MONTHS:
            sp = schedule(pn, pd.Period(f"{y}-{m:02d}", "M"))
            if sp is None:
                continue
            dp = event(pn, sp)
            P = dp[dp.grp == "P"]
            placebo.append(dict(year=y, month=m, gross_x=P[f"retR{EXIT_N}"].mean() - dp[f"retR{EXIT_N}"].mean(),
                                S_x=P.retS.mean() - dp.retS.mean()))
        log(f"  {y}: D={s['D'].date()} U={len(d)} P={int((d.grp == 'P').sum())} x={rows[-1]['x'] * 100:+.2f}%")
    return pd.DataFrame(rows), pd.DataFrame(placebo), events


# ───────────────────────── 통계·보고 ─────────────────────────
def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 and x.std(ddof=1) > 0 else np.nan


def judge(Y, PL):
    x = Y.set_index("year").x
    pm = PL.groupby("year").gross_x.mean()
    dplac = (Y.set_index("year").gross_x - pm).dropna()
    low, high = x[x.index.isin(LOW)], x[~x.index.isin(LOW)]
    c = [("1 평균>0·t≥2.0", x.mean() > 0 and tstat(x) >= 2.0, f"평균 {x.mean() * 100:+.2f}% t{tstat(x):+.2f} (n={len(x)})"),
         ("2 양수 해 ≥7/10", (x > 0).sum() >= 7, f"{(x > 0).sum()}/{len(x)}"),
         ("3 LOW > HIGH", low.mean() > high.mean(), f"LOW {low.mean() * 100:+.2f}% (n={len(low)}) vs HIGH {high.mean() * 100:+.2f}% (n={len(high)})"),
         ("4 위약 대비 >0·t≥2.0", dplac.mean() > 0 and tstat(dplac) >= 2.0, f"평균 {dplac.mean() * 100:+.2f}%p t{tstat(dplac):+.2f} (n={len(dplac)})"),
         ("5 P 비용 후 >0", (Y.P_R - COST).mean() > 0, f"{(Y.P_R - COST).mean() * 100:+.2f}%")]
    return c, dplac, pm


def pct(v, d=2):
    return "" if pd.isna(v) else f"{v * 100:+.{d}f}"


def report(Y, PL, mode, path):
    c, dplac, pm = judge(Y, PL)
    ok = all(v for _, v, _ in c)
    L = [f"# 연말 대주주 매도 → 1월 반등 — {'개발' if mode == 'dev' else '봉인'} 결과 ({pd.Timestamp.now():%Y-%m-%d %H:%M})", "",
         f"사전등록 PREREGISTRATION.md (sha256 은 PREREGISTRATION.sha256). 비용 왕복 {COST * 100:.2f}%(P 만), 가격수익(배당 제외). "
         f"P=11개월 수익 상위 20%, U=유니버스 동일가중, 반등 = D 종가 → 1월 {EXIT_N}번째 거래일 종가. 수익 단위 %.", ""]
    if mode == "dev":
        L += [f"## 판정: **{'합격' if ok else '불합격'}**", "", "| 조건 | 충족 | 값 |", "|---|---|---|"]
        L += [f"| {n} | {'✅' if v else '❌'} | {s} |" for n, v, s in c]
    else:
        L += ["## 봉인 3해 (50억 시기 — 가설상 효과가 약해야 함)", "", f"참고 — 개발 판정식 그대로: " + " · ".join(f"{n} {'✅' if v else '❌'} ({s})" for n, v, s in c)]
    L += ["", "## 해별 (반등 구간)", "",
          "| 연말 | 기준(억) | D | X | U | P | 상폐 | P 반등 | U 반등 | K200 | 초과 비용전 | **x 비용후** | 위약 평균 | 12월−위약 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in Y.iterrows():
        pmv = pm.get(r.year, np.nan)
        L.append(f"| {r.year} | {r.thresh} | {r.D} | {r.X} | {r.nU} | {r.nP_buy} | {r.nDelist} | {pct(r.P_R)} | {pct(r.U_R)} | {pct(r.K200_R)} "
                 f"| {pct(r.gross_x)} | **{pct(r.x)}** | {pct(pmv)} | {pct(r.gross_x - pmv) if pd.notna(pmv) else ''} |")
    L += ["", "## 해석용 (판정 아님)", "",
          "### M1 매도 구간 F→D, M2 개인 순매수 근사(중앙값, 거래대금 대비 %)", "",
          "| 연말 | 기준 | P−U 매도구간 | 위약 매도구간 P−U | Q1 매도구간 | U 매도구간 | P 개인흐름 | U 개인흐름 | Q1 개인흐름 |", "|---|---|---|---|---|---|---|---|---|"]
    ps = PL.groupby("year").S_x.mean()
    for _, r in Y.iterrows():
        L.append(f"| {r.year} | {r.thresh} | {pct(r.S_x)} | {pct(ps.get(r.year, np.nan))} | {pct(r.Q1_S - r.U_S)} | {pct(r.U_S)} "
                 f"| {pct(r.P_flow)} | {pct(r.U_flow)} | {pct(r.Q1_flow)} |")
    lowm = Y.year.isin(LOW)
    L += ["", f"매도구간 P−U 평균: LOW {pct(Y[lowm].S_x.mean())} / HIGH {pct(Y[~lowm].S_x.mean())} · 전체 t{tstat(Y.S_x):+.2f}", "",
          "### 변형 (x 비용 후, 평균 · t · 양수 해)", "", "| 변형 | 평균 | t | 양수 | LOW | HIGH |", "|---|---|---|---|---|---|"]
    var = {"주 시험 (D 종가→1월 10일째)": Y.x, "슬리피지 2배": Y.x_slip2, "D+1 시가 진입": Y.x_open,
           **{f"1월 {n}번째 거래일 청산": Y[f"P_R{n}"] - COST - Y[f"U_R{n}"] for n in EXIT_ALT},
           "Q1(손실 종목) 반등 − U, 비용후": Y.Q1_R - COST - Y.U_R, "P 비용후 − K200": Y.P_R - COST - Y.K200_R,
           **{f"시장 {v} (비용 전 P−U)": Y[f"x_{v}"] for v in ("KOSPI", "KOSDAQ")},
           **{f"규모 {v} (비용 전 P−U)": Y[f"x_{v}"] for v in ("small", "mid", "large")}}
    for n, s in var.items():
        L.append(f"| {n} | {pct(s.mean())} | {tstat(s):+.2f} | {(s > 0).sum()}/{s.notna().sum()} | {pct(s[lowm].mean())} | {pct(s[~lowm].mean())} |")
    L += ["", "### 위약 월별 비용 전 P−U 반등 (해 평균)", "", "| 월 | 평균 | t | 양수 |", "|---|---|---|---|"]
    for m, g in PL.groupby("month"):
        L.append(f"| {m} | {pct(g.gross_x.mean())} | {tstat(g.gross_x):+.2f} | {(g.gross_x > 0).sum()}/{len(g)} |")
    L.append(f"| **12월(사건)** | {pct(Y.gross_x.mean())} | {tstat(Y.gross_x):+.2f} | {(Y.gross_x > 0).sum()}/{len(Y)} |")
    L += ["", "주: 시장 구분은 현재값(이전상장 미반영). 개인 흐름 = −(외국인 보유수량 변화 + 기관 순매수)×무수정 종가 / 거래대금 — 기타법인 포함·외국인 근사 상관 0.73, 서술만.",
          "생존 편향: 상폐 548 은 2014~ 상폐분만(2013 연말엔 2013 상폐 종목 없음)."]
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return ok


# ───────────────────────── 단위 검사 ─────────────────────────
def selftest():
    pn = Panel(DEV["read_to"])
    want = {2019: "2019-12-26", 2020: "2020-12-28", 2021: "2021-12-28", 2022: "2022-12-27"}
    for y, dd in want.items():
        s = schedule(pn, pd.Period(f"{y}-12", "M"), allow_first=True)
        assert str(s["D"].date()) == dd, (y, s["D"].date(), dd)
    s13 = schedule(pn, pd.Period("2013-12", "M"), allow_first=True)
    assert s13["S0"] == pn.cal[0] and schedule(pn, pd.Period("2013-05", "M")) is None
    y = 2020
    s = schedule(pn, pd.Period(f"{y}-12", "M"), allow_first=True)
    base = event(pn, s)
    # 미래참조 섭동: D 이후 가격을 흔들어도 선정 불변 / X 이후 가격을 흔들어도 수익 불변
    rng = np.random.default_rng(0)
    i_d, i_x = pn.pos[s["D"]], pn.pos[max(s["X"].values())]
    for frm, cols in ((i_d + 1, ["grp", "score"]), (i_x + 1, ["grp", "score", "retS"] + [f"retR{n}" for n in s["X"]])):
        orig = {k: getattr(pn, k).copy() for k in ("C", "CFF", "O")}
        noise = rng.uniform(0.5, 1.5, size=pn.C.iloc[frm:].shape).astype("float32")
        pn.C.iloc[frm:] = pn.C.iloc[frm:].values * noise
        pn.CFF = pn.C.ffill()
        pn.O.iloc[frm:] = pn.O.iloc[frm:].values * noise
        pert = event(pn, s)
        for col in cols:
            pd.testing.assert_series_equal(base[col], pert[col], check_names=False)
        for k, v in orig.items():
            setattr(pn, k, v)
    assert base.delisted.sum() > 0, "상폐 종목이 유니버스에 없다"
    row = summarize_event(pn, s, base)
    assert abs(row["x"] - (row["P_R"] - 0.0057 - row["U_R"])) < 1e-12 and abs(COST - 0.0057) < 1e-12
    print(f"selftest OK — D 날짜 4건 일치, 섭동 불변, {y} 상폐 {int(base.delisted.sum())}종목 포함, 비용 {COST * 100:.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sealed", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    cfg, mode = (SEALED, "sealed") if a.sealed else (DEV, "dev")
    path = os.path.join(OUT, cfg["out"])
    if a.sealed and os.path.exists(path):
        sys.exit(f"봉인 결과가 이미 있다: {path}")
    pn = Panel(cfg["read_to"])
    Y, PL, events = run_years(pn, cfg["years"])
    Y.to_csv(os.path.join(OUT, f"years_{mode}.csv"), index=False)
    PL.to_csv(os.path.join(OUT, f"placebo_{mode}.csv"), index=False)
    pd.concat({y: d for y, d in events.items()}, names=["year", "code"]).to_parquet(os.path.join(OUT, f"events_{mode}.parquet"))
    ok = report(Y, PL, mode, path)
    print(f"{path} — {'합격' if ok else '불합격'}")


if __name__ == "__main__":
    main()
