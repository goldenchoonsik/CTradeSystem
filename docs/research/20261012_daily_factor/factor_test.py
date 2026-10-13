"""사전등록(PREREGISTRATION_factor.md) 팩터 포트폴리오 시험 — V0(BM+OPA+MAX5 상위30)·V1~V6(개발 진단)·V7(기관 순매도 하위30)·V8(BM 상위30) — V7·V8 은 봉인만 판정.

  .venv/bin/python docs/research/20261012_daily_factor/factor_test.py             # 개발 구간 → results_factor_dev.md
  .venv/bin/python docs/research/20261012_daily_factor/factor_test.py --selftest  # 단위 검사(미래참조 섭동·상폐 포함·비용)
  .venv/bin/python docs/research/20261012_daily_factor/factor_test.py --sealed    # 봉인 구간 1회 → results_factor_sealed.md (있으면 거부)

개발 모드는 2024-02-15 이후 가격을 아예 읽지 않는다(봉인 구간 누출 차단). 패널만 읽고 원본은 수정하지 않는다.
체결: 형성일 t 종가로 순위 → t+1 종가 매수, 다음 형성일 t'+1 종가 평가·교체. 계속 보유 종목은 거래 없음.
보유 중 상폐·정지: 마지막 유효 종가로 평가. t+1 이 상·하한(|t+1/t−1|≥29%)·거래량 0 이면 그 종목 몫은 현금.
비용: 정책 원장(KRX_STOCK) 수수료·슬리피지 편도 + 매도세(매도만) = 왕복 0.27% + 사전등록 슬리피지 편도 0.15% → 왕복 0.57%, 거래 금액에만.
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

DEV = (pd.Timestamp("2016-04-29"), pd.Timestamp("2023-12-29"), pd.Timestamp("2024-02-15"))      # 첫·마지막 형성일, 읽기 한도
SEALED = (pd.Timestamp("2024-01-31"), pd.Timestamp("2026-08-31"), pd.Timestamp("2026-12-31"))
LOAD_FROM = pd.Timestamp("2015-01-01")
MIN_PRICE, MIN_ADV, LIMIT, TOP_N = 1000, 5e8, 0.29, 30

_P = DEFAULT_MARKET_POLICIES["KRX_STOCK"]
BASE_SIDE = _P["default_fee"] + _P["default_slippage"]              # 편도 0.035%
SELL_TAX = _P["sell_tax"]                                            # 0.20%
EXTRA_SLIP = 0.0015                                                  # 사전등록 편도 0.15%
assert abs(round_trip_cost(_P["default_fee"], _P["default_slippage"], SELL_TAX) - 0.0027) < 1e-9


def side_costs(slip_mult=1.0):
    """(매수 편도, 매도 편도) 비용률. 왕복 = 0.27% + 2 × 0.15% × slip_mult."""
    s = EXTRA_SLIP * slip_mult
    return BASE_SIDE + s, BASE_SIDE + SELL_TAX + s


# ───────────────────────── 데이터 → 월별 형성 표 ─────────────────────────
def wide(df, col, cal, codes):
    return df.pivot(index="date", columns="code", values=col).reindex(index=cal, columns=codes).astype("float32")


def load_fin():
    f = pd.read_parquet(os.path.join(DER, "fundamentals_q.parquet"),
                        columns=["stock_code", "q", "period_end", "rcept_dt", "equity", "assets", "op_income",
                                 "op_income_annual", "sales", "sales_annual"])
    f = f.sort_values(["stock_code", "period_end"]).reset_index(drop=True)
    g = f.groupby("stock_code")
    ttm = g.op_income.rolling(4).sum().reset_index(level=0, drop=True)
    span = (f.period_end - g.period_end.shift(3)).dt.days
    ttm[~span.between(255, 290)] = np.nan
    f["op_ttm"] = ttm.where(ttm.notna(), f.op_income_annual.where(f.q == 4))
    f["sales_known"] = f.sales.notna() | f.sales_annual.notna()
    f["code"] = "A" + f.stock_code
    return f


def fin_known(f, key="rcept_dt"):
    """형성일에 알려진 최신 보고서. key='period_end' 는 단위 검사용 미래참조 섭동."""
    x = f.sort_values(["code", key, "period_end"]).drop_duplicates(["code", key], keep="last")
    x = x[["code", key, "equity", "assets", "op_ttm", "sales_known"]].rename(columns={key: "known_dt"})
    return x.sort_values("known_dt")


def build(first_form, last_form, load_to, fin_key="rcept_dt", log=print):
    t0 = time.time()
    uni = pd.read_csv(os.path.join(KR, "universe.csv"), dtype=str)
    common = set(uni[(uni.is_preferred == "0") & (uni.is_spac == "0")].code)
    delisted = set(uni[uni.status == "delisted"].code)
    p = pd.read_parquet(os.path.join(DER, "prices_1d.parquet"), columns=["code", "date", "close", "close_unadj", "volume", "value"])
    first_date = p.groupby("code", observed=True).date.min()
    first_date.index = first_date.index.astype(str)
    p = p[(p.date >= LOAD_FROM) & (p.date <= load_to)]
    p["code"] = p.code.astype(str)
    idx = pd.read_parquet(os.path.join(DER, "index_1d.parquet"), columns=["code", "date", "close"])
    cal = idx[(idx.code == "U001") & (idx.date >= LOAD_FROM) & (idx.date <= load_to)].date.sort_values().reset_index(drop=True)
    index_close = {k: idx[idx.code == k].set_index("date").close.reindex(cal) for k in ("U001", "U180")}
    codes = sorted(set(p.code) & common)
    C = wide(p, "close", cal, codes)
    CU = wide(p, "close_unadj", cal, codes).fillna(C)
    V = wide(p, "volume", cal, codes)
    VAL = wide(p, "value", cal, codes).fillna(C * V)
    del p
    e = pd.read_parquet(os.path.join(DER, "daily_ext.parquet"), columns=["code", "date", "market_cap", "shares_listed", "inst_net_qty"])
    e = e[(e.date >= LOAD_FROM) & (e.date <= load_to)]
    e["code"] = e.code.astype(str)
    MC = wide(e, "market_cap", cal, codes).replace(0, np.nan)
    INETV = wide(e, "inst_net_qty", cal, codes) * C
    del e
    fin = fin_known(load_fin(), fin_key)
    log(f"loaded {len(codes)} codes × {len(cal)} days ({cal.iloc[0].date()}~{cal.iloc[-1].date()}) {time.time() - t0:.0f}s")

    R = C.pct_change(fill_method=None)
    CFF = C.ffill()
    pos = {d: i for i, d in enumerate(cal)}
    month_end = cal.groupby(cal.dt.to_period("M")).max()
    forms = [d for d in month_end if first_form <= d <= last_form]
    nxt = {t: month_end[month_end > t].iloc[0] for t in forms}
    rows = []
    for t in forms:
        i, j = pos[t], pos[nxt[t]]
        c = C.iloc[i].astype("float64")
        win = R.iloc[i - 20:i + 1].values.astype("float64")
        valid = np.isfinite(win).sum(0)
        srt = np.sort(np.where(np.isfinite(win), win, -np.inf), axis=0)
        v20 = V.iloc[i - 19:i + 1]
        d = pd.DataFrame({
            "code": codes, "t": t, "close": c.values, "price_u": CU.iloc[i].astype("float64").values,
            "mcap": MC.iloc[i].astype("float64").values,
            "adv20": VAL.iloc[i - 19:i + 1].mean().values, "zero_vol20": (v20.fillna(0) <= 0).sum().values,
            "MAX5": np.where(valid >= 15, srt[-5:].mean(0), np.nan),
            "REV1": (c / C.iloc[i - 21].astype("float64") - 1).values,
            "INST_TV1M": (INETV.iloc[i - 20:i + 1].sum(min_count=15) / VAL.iloc[i - 20:i + 1].sum()).astype("float64").values,
            "entry": C.iloc[i + 1].astype("float64").values, "entry_vol": V.iloc[i + 1].values,
            "exit": CFF.iloc[j + 1].astype("float64").values,
        })
        d = d[d.close.notna()]
        fm = pd.merge_asof(d[["code", "t"]].sort_values("t"), fin, left_on="t", right_on="known_dt", by="code", direction="backward")
        d = d.merge(fm.drop(columns="t"), on="code", how="left")
        d["fin_age"] = (d.t - d.known_dt).dt.days
        d["BM"] = d.equity / d.mcap
        d["OPA"] = d.op_ttm / d.assets
        d["delisted"] = d.code.isin(delisted)
        listed_12m = first_date.reindex(d.code).values <= (t - pd.Timedelta(days=365))
        base = listed_12m & (d.price_u >= MIN_PRICE) & (d.adv20 >= MIN_ADV) & (d.zero_vol20 == 0) & (d.fin_age <= 456) \
            & (d.equity > 0) & d.sales_known.fillna(False).astype(bool) & d.mcap.notna()
        d = d[base & (d.mcap > d.loc[base, "mcap"].quantile(0.2))].copy()
        # 체결 가능 여부는 t+1 에 드러나는 정보 — 선정(순위)에는 쓰지 않고 체결 단계에서만 쓴다
        d["can_buy"] = d.entry.notna() & (d.entry_vol > 0) & ((d.entry / d.close - 1).abs() < LIMIT)
        d["ret"] = np.where(d.can_buy, d.exit / d.entry - 1, np.nan)                       # 새로 사는 종목
        d["ret_hold"] = np.where(d.entry.notna(), d.exit / d.entry, d.exit / d.close) - 1   # 이미 보유 중인 종목(t+1 평가가 기준)
        rows.append(d.drop(columns=["entry_vol", "known_dt", "equity", "assets", "op_ttm", "sales_known", "zero_vol20"]))
    P = pd.concat(rows, ignore_index=True)
    idx_ret = {}
    for k, s in index_close.items():
        idx_ret[k] = pd.Series({t: s.iloc[pos[nxt[t]] + 1] / s.iloc[pos[t] + 1] - 1 for t in forms})
    log(f"built {len(forms)} months, universe avg {P.groupby('t').size().mean():.0f} ({time.time() - t0:.0f}s)")
    return P, idx_ret


# ───────────────────────── 포트폴리오 ─────────────────────────
def pct_rank(P, col):
    return P.groupby("t")[col].rank(pct=True)


def add_scores(P):
    P = P.copy()
    P["s_V0"] = (pct_rank(P, "BM") + pct_rank(P, "OPA") + (1 - pct_rank(P, "MAX5"))) / 3
    P["s_V1"] = P.BM
    P["s_V2"] = P.OPA
    P["s_V3"] = -P.MAX5
    P["s_V7"] = -P.INST_TV1M
    P["s_REV"] = -P.REV1
    return P


def select(P, score, n=TOP_N, need=()):
    """월별 점수 상위 n (결측 신호 종목 제외). 반환 {t: [codes]}."""
    q = P.dropna(subset=[score, *need])
    return {t: g.nlargest(n, score).code.tolist() for t, g in q.groupby("t")}


def simulate(P, picks, slip_mult=1.0, every=1):
    """picks {t: codes}. 목표 = 고른 종목 1/n 균등(체결 불가 몫은 현금). every=3 이면 3개월마다만 교체.
    반환 월별 표: gross, cost, net, turnover(매수+매도 금액 / 2)."""
    buy_c, sell_c = side_costs(slip_mult)
    look = P.set_index(["t", "code"])[["ret", "ret_hold", "can_buy"]]
    months = sorted(picks)
    held = {}                                                 # code → 비중(월말 평가 후, 포트폴리오 1 기준)
    out = []
    for k, t in enumerate(months):
        if k % every == 0:
            names = picks[t]
            target = {}
            for c in names:
                key = (t, c)
                if key in look.index and bool(look.at[key, "can_buy"]):
                    target[c] = 1.0 / len(names)
            # 계속 보유 종목은 체결 불가여도 유지(이미 들고 있음)
            for c in names:
                if c in held and c not in target:
                    target[c] = 1.0 / len(names)
        else:
            target = dict(held)
        allc = set(held) | set(target)
        buys = sum(max(target.get(c, 0) - held.get(c, 0), 0) for c in allc)
        sells = sum(max(held.get(c, 0) - target.get(c, 0), 0) for c in allc)
        cost = buys * buy_c + sells * sell_c
        gross = 0.0
        new_held = {}
        for c, w in target.items():
            col = "ret_hold" if c in held else "ret"
            r = look.at[(t, c), col] if (t, c) in look.index else np.nan
            if not np.isfinite(r):
                # 유니버스 밖으로 나간 보유 종목(정지·필터 탈락) → 형성 표에 없으면 수익 0 으로 두고 다음 교체에서 정리
                r = 0.0
            gross += w * r
            new_held[c] = w * (1 + r)
        total = 1 - cost + gross                              # 현금 몫은 수익 0
        net = total - 1
        cash = 1 - sum(target.values())
        held = {c: v / total for c, v in new_held.items()}
        out.append({"t": t, "gross": gross, "cost": cost, "net": net, "turnover": (buys + sells) / 2,
                    "n_hold": len(target), "cash": cash})
    return pd.DataFrame(out).set_index("t")


def universe_picks(P, need=()):
    q = P.dropna(subset=list(need)) if need else P
    return {t: g.code.tolist() for t, g in q.groupby("t")}


def simulate_ew(P, picks, slip_mult=1.0):
    """B1: 유니버스 전체 동일가중(종목 수가 달라 1/n 이 매월 다름) — simulate 와 같은 규칙, n=그달 유니버스 수."""
    return simulate(P, picks, slip_mult)


# ───────────────────────── 지표 ─────────────────────────
def tstat(x):
    x = pd.Series(x).dropna()
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 else np.nan


def nw_t(x, lags=3):
    x = pd.Series(x).dropna().values
    n = len(x)
    e = x - x.mean()
    v = e @ e / n
    for L in range(1, lags + 1):
        v += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(x.mean() / np.sqrt(v / n))


def cagr(r):
    r = pd.Series(r).dropna()
    return float((1 + r).prod() ** (12 / len(r)) - 1)


def mdd(r):
    w = (1 + pd.Series(r).fillna(0)).cumprod()
    return float((w / w.cummax() - 1).min())


def summarize(name, r, base):
    ex = (r - base).dropna()
    yr = ex.groupby(ex.index.year).mean()
    return {"구성": name, "CAGR_%": cagr(r) * 100, "MDD_%": mdd(r) * 100, "월초과_%": ex.mean() * 100,
            "t": tstat(ex), "NW_t": nw_t(ex), "양수연도": f"{(yr > 0).sum()}/{len(yr)}"}


def md(df, fmt="{:.2f}"):
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join([""] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, r in df.iterrows():
        out.append("| " + " | ".join([str(i)] + ["" if (isinstance(v, float) and np.isnan(v)) else
                                                (fmt.format(v) if isinstance(v, (float, np.floating)) else str(v)) for v in r]) + " |")
    return "\n".join(out)


# ───────────────────────── 실행 ─────────────────────────
def run(mode):
    first, last, load_to = DEV if mode == "dev" else SEALED
    P, idx_ret = build(first, last, load_to)
    P = add_scores(P)
    kospi, k200 = idx_ret["U001"], idx_ret["U180"]
    uni_all = universe_picks(P)
    b1 = simulate_ew(P, uni_all)
    picks = {"V0": select(P, "s_V0", need=("BM", "OPA", "MAX5"))}
    res = {"V0": simulate(P, picks["V0"])}
    L = []
    if mode == "dev":
        picks.update({"V1": select(P, "s_V1"), "V2": select(P, "s_V2"), "V3": select(P, "s_V3"),
                      "V4": select(P, "s_V0", n=50, need=("BM", "OPA", "MAX5"))})
        for v in ("V1", "V2", "V3", "V4"):
            res[v] = simulate(P, picks[v])
        res["V5"] = simulate(P, picks["V0"], every=3)
        Pl = P[~P.delisted]
        res["V6"] = simulate(Pl, select(Pl, "s_V0", need=("BM", "OPA", "MAX5")))
        b1_v6 = simulate_ew(Pl, universe_picks(Pl))
    # V7: 봉인에서만 판정, 개발 모드에서는 계산만 하고 판정하지 않음(보고는 참고)
    need7 = ("INST_TV1M",)
    picks["V7"] = select(P, "s_V7", need=need7)
    res["V7"] = simulate(P, picks["V7"])
    if mode == "sealed":
        picks["V8"] = select(P, "s_V1", need=("BM",))                  # V1 과 같은 규칙, 봉인 판정용 이름
        res["V8"] = simulate(P, picks["V8"])
    b1_inst = simulate_ew(P, universe_picks(P, need7))
    res_2x = {v: simulate(P, picks[v], slip_mult=2) for v in ("V0", "V7", "V8") if v in picks}
    b1_2x = simulate_ew(P, uni_all, slip_mult=2)
    b1_inst_2x = simulate_ew(P, universe_picks(P, need7), slip_mult=2)

    B = b1.net
    rows = [summarize("B1 유니버스 동일가중", B, B), summarize("B2 KOSPI(비용 없음)", kospi, B),
            summarize("B3 K200(비용 없음)", k200, B)]
    for v, r in res.items():
        base = B
        if v == "V6":
            base = b1_v6.net
        if v == "V7":
            base = b1_inst.net
        rows.append(summarize(v, r.net, base))
    rows.append(summarize("V0 슬리피지2배 (vs B1 2배)", res_2x["V0"].net, b1_2x.net))
    rows.append(summarize("V7 슬리피지2배 (vs B1_INST 2배)", res_2x["V7"].net, b1_inst_2x.net))
    if "V8" in res_2x:
        rows.append(summarize("V8 슬리피지2배 (vs B1 2배)", res_2x["V8"].net, b1_2x.net))
    rows.append(summarize("B1_INST (기관 데이터 있는 유니버스)", b1_inst.net, B))
    S = pd.DataFrame(rows).set_index("구성")

    v0 = res["V0"].net
    ex0 = (v0 - B).dropna()
    yr0 = ex0.groupby(ex0.index.year).mean()
    ex7 = (res["V7"].net - b1_inst.net).dropna()
    yr7 = ex7.groupby(ex7.index.year).mean()
    beta = np.polyfit(kospi.reindex(ex0.index).values, ex0.values, 1)[0]
    turn = {v: r.turnover.mean() * 100 for v, r in res.items()}
    overlap = np.mean([len(set(picks["V7"][t]) & set(select(P[P.t == t], "s_REV").get(t, []))) / TOP_N
                       for t in picks["V7"]])
    delisted_in = P[P.delisted].code.nunique()

    if mode == "dev":
        c1 = ex0.mean() > 0 and tstat(ex0) >= 2.5
        c2 = cagr(v0) >= cagr(B) + 0.03 and cagr(v0) >= cagr(kospi) + 0.03
        c3 = (yr0 > 0).sum() >= 5
        c4 = mdd(v0) >= mdd(B) * 1.2                         # MDD 는 음수: V0 낙폭 ≤ B1 낙폭×1.2
        verdict = [("1 월초과>0·t≥2.5", c1, f"{ex0.mean() * 100:.2f}% t{tstat(ex0):.2f} (NW {nw_t(ex0):.2f})"),
                   ("2 CAGR ≥ B1+3%p 이고 ≥ B2+3%p", c2, f"V0 {cagr(v0) * 100:.1f}% · B1 {cagr(B) * 100:.1f}% · B2 {cagr(kospi) * 100:.1f}%"),
                   ("3 양수 연도 ≥5 / 8", c3, f"{(yr0 > 0).sum()}/{len(yr0)}"),
                   ("4 MDD ≤ B1×1.2", c4, f"V0 {mdd(v0) * 100:.1f}% · B1 {mdd(B) * 100:.1f}% (한도 {mdd(B) * 120:.1f}%)")]
        passed = all(v for _, v, _ in verdict)
    else:
        c5 = ex0.mean() > 0 and tstat(ex0) >= 1.5 and cagr(v0) >= cagr(kospi)
        c7 = ex7.mean() > 0 and tstat(ex7) >= 2.0 and (yr7 > 0).sum() >= 2
        ex8 = (res["V8"].net - B).dropna()
        yr8 = ex8.groupby(ex8.index.year).mean()
        c8 = ex8.mean() > 0 and tstat(ex8) >= 2.0 and (yr8 > 0).sum() >= 2
        verdict = [("(참고, 개발 불합격) V0 5 월초과>0·t≥1.5 이고 CAGR≥B2", c5,
                    f"{ex0.mean() * 100:.2f}% t{tstat(ex0):.2f} · CAGR V0 {cagr(v0) * 100:.1f}% vs B2 {cagr(kospi) * 100:.1f}%"),
                   ("V7 월초과(vs B1_INST)>0·t≥2.0·양수연도≥2", c7,
                    f"{ex7.mean() * 100:.2f}% t{tstat(ex7):.2f} · 연도 {(yr7 > 0).sum()}/{len(yr7)}"),
                   ("V8 월초과(vs B1)>0·t≥2.0·양수연도≥2", c8,
                    f"{ex8.mean() * 100:.2f}% t{tstat(ex8):.2f} (NW {nw_t(ex8):.2f}) · 연도 {(yr8 > 0).sum()}/{len(yr8)} · CAGR V8 {cagr(res['V8'].net) * 100:.1f}% vs B2 {cagr(kospi) * 100:.1f}%")]
        passed = None

    fn = os.path.join(OUT, "results_factor_dev.md" if mode == "dev" else "results_factor_sealed.md")
    with open(fn, "w", encoding="utf-8") as fh:
        span = f"{min(b1.index).date()} ~ {max(b1.index).date()} 형성, {len(b1)}개월"
        fh.write(f"# 팩터 포트폴리오 시험 — {'개발' if mode == 'dev' else '봉인'} 구간 ({span}) · {pd.Timestamp.now():%Y-%m-%d %H:%M}\n\n")
        fh.write(f"사전등록 PREREGISTRATION_factor.md. 비용 왕복 {(side_costs()[0] + side_costs()[1]) * 100:.2f}% (거래 금액만), 가격수익(배당 제외). "
                 f"유니버스 월 평균 {P.groupby('t').size().mean():.0f}종목, 상폐 종목 {delisted_in}개 포함.\n\n")
        fh.write("## 판정\n\n| 기준 | 결과 | 값 |\n|---|---|---|\n")
        for name, ok, val in verdict:
            fh.write(f"| {name} | {'통과' if ok else '**실패**'} | {val} |\n")
        if mode == "dev":
            fh.write(f"\n**V0 개발 구간: {'합격 → 봉인 구간 실행 대상' if passed else '불합격 → 봉인 실행하지 않음(V7 은 별도)'}**. "
                     "V1~V6 은 진단용(판정 무관), V7 은 개발 구간에서 판정하지 않음(참고 수치).\n\n")
        else:
            fh.write("\n봉인 구간 1회 실행 결과. 재실행·수정 불가(수정 시 새 사전등록).\n\n")
        fh.write("## 요약 (월초과는 기준선 대비: V6→상폐 제외 B1, V7→B1_INST, 나머지→B1)\n\n" + md(S) + "\n\n")
        yrs = {"V0−B1": yr0 * 100, "V7−B1_INST": yr7 * 100}
        if "V8" in res:
            e8 = (res["V8"].net - B).dropna()
            yrs["V8−B1"] = e8.groupby(e8.index.year).mean() * 100
        fh.write("## 연도별 월평균 초과 (%)\n\n" + md(pd.DataFrame(yrs).T) + "\n\n")
        fh.write("## 추가 보고\n\n")
        fh.write(f"- 월 회전율(편도, %): " + ", ".join(f"{v} {x:.0f}" for v, x in turn.items()) + "\n")
        fh.write(f"- 평균 현금 비중(체결 불가): V0 {res['V0'].cash.mean() * 100:.1f}% · V7 {res['V7'].cash.mean() * 100:.1f}%\n")
        fh.write(f"- V0 초과의 KOSPI 베타: {beta:+.2f}\n")
        fh.write(f"- V7 과 단기반전(REV1 하위30) 종목 겹침: {overlap * 100:.0f}%\n")
    print(open(fn, encoding="utf-8").read())
    return passed


def selftest():
    """1) 미래참조 섭동: 재무를 접수일 대신 기간말에 알았다고 하면(미래 정보) 결과가 달라져야 한다
    2) 상폐 종목이 유니버스에 실제로 들어간다  3) 비용은 거래 금액에만(같은 종목 계속 보유 시 0)"""
    f = pd.Timestamp("2018-01-31"), pd.Timestamp("2018-12-28"), pd.Timestamp("2019-02-15")
    P1, _ = build(*f)
    P2, _ = build(*f, fin_key="period_end")
    s1 = select(add_scores(P1), "s_V0", need=("BM", "OPA", "MAX5"))
    s2 = select(add_scores(P2), "s_V0", need=("BM", "OPA", "MAX5"))
    diff = np.mean([len(set(s1[t]) ^ set(s2[t])) > 0 for t in s1])
    assert diff > 0.5, f"미래참조 섭동에도 선정이 거의 같음 ({diff:.2f}) — 재무 결합이 접수일을 안 쓰는지 의심"
    assert P1.delisted.any(), "상폐 종목이 유니버스에 하나도 없음"
    t0 = sorted(s1)[0]
    fixed = {t: s1[t0] for t in s1}                           # 매달 같은 종목 → 첫 달 이후 비용은 체결 불가 정리분뿐
    sim = simulate(add_scores(P1), fixed)
    assert sim.cost.iloc[0] > 0 and sim.cost.iloc[1:].max() < sim.cost.iloc[0] * 0.5, sim.cost.round(5).tolist()
    print(f"selftest OK — 섭동 시 선정 변화 {diff:.0%} 월, 상폐 {P1[P1.delisted].code.nunique()}종목, 첫 달 비용 {sim.cost.iloc[0] * 100:.3f}% 이후 최대 {sim.cost.iloc[1:].max() * 100:.3f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sealed", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.sealed:
        fn = os.path.join(OUT, "results_factor_sealed.md")
        if os.path.exists(fn):
            sys.exit(f"봉인 구간은 이미 실행됨: {fn} — 재실행 금지(사전등록 규칙)")
        run("sealed")
    else:
        run("dev")
