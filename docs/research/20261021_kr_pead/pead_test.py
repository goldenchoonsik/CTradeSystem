"""한국판 PEAD — 사전등록(PREREGISTRATION.md) 판정.

  .venv/bin/python -I docs/research/20261021_kr_pead/pead_test.py --selftest  # 단위 검사(미래참조 섭동·기준점·상폐 포함·비용)
  .venv/bin/python -I docs/research/20261021_kr_pead/pead_test.py             # 개발(t0 2018~2023) → results_dev.md
  .venv/bin/python -I docs/research/20261021_kr_pead/pead_test.py --sealed    # 봉인(t0 2024-01~2026-08) 1회 → results_sealed.md (있으면 거부)

입력 events.parquet(build_events.py). 개발 모드는 2024-03-31 이후 가격을 읽지 않는다.
t0 = 공개일 E 이후 첫 거래일, 매수 t0+1 종가, 매도 t0+21 종가, 3분위 기준점 = t0 직전 365일 적격 사건 SUE(300건 이상).
기준 = 같은 매수일→매도일 유니버스 동일가중 보유. 비용 왕복 0.57%(사건만).
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

DEV = dict(judge=(pd.Timestamp("2018-01-01"), pd.Timestamp("2023-12-31")), read_to=pd.Timestamp("2024-03-31"), out="results_dev.md")
SEALED = dict(judge=(pd.Timestamp("2024-01-01"), pd.Timestamp("2026-08-31")), read_to=pd.Timestamp("2026-12-31"), out="results_sealed.md")
LOAD_FROM = pd.Timestamp("2016-06-01")
MIN_PRICE, MIN_ADV, LIMIT = 1000, 5e8, 0.29
HOLD, HOLD_LONG, MIN_HIST = 20, 60, 300

_P = DEFAULT_MARKET_POLICIES["KRX_STOCK"]
BASE_SIDE = _P["default_fee"] + _P["default_slippage"]
SELL_TAX = _P["sell_tax"]
EXTRA_SLIP = 0.0015
assert abs(round_trip_cost(_P["default_fee"], _P["default_slippage"], SELL_TAX) - 0.0027) < 1e-9
COST = 2 * BASE_SIDE + SELL_TAX + 2 * EXTRA_SLIP                              # 0.57%
COST2 = 2 * BASE_SIDE + SELL_TAX + 4 * EXTRA_SLIP                             # 0.87%


# ───────────────────────── 가격 패널 ─────────────────────────
class Panel:
    def __init__(self, read_to, log=print):
        t0 = time.time()
        uni = pd.read_csv(os.path.join(KR, "universe.csv"), dtype=str)
        common = uni[(uni.is_preferred == "0") & (uni.is_spac == "0")]
        self.market = common.set_index("code").market
        self.delisted = set(common[common.status == "delisted"].code)
        idx = pd.read_parquet(os.path.join(DER, "index_1d.parquet"), columns=["code", "date", "close"])
        idx = idx[(idx.date >= LOAD_FROM) & (idx.date <= read_to)]
        self.cal = pd.DatetimeIndex(sorted(idx[idx.code == "U001"].date))
        self.k200 = idx[idx.code == "U180"].set_index("date").close.reindex(self.cal).values.astype("float64")
        p = pd.read_parquet(os.path.join(DER, "prices_1d.parquet"), columns=["code", "date", "close", "close_unadj", "volume", "value"],
                            filters=[("date", ">=", LOAD_FROM), ("date", "<=", read_to)])
        p["code"] = p.code.astype(str)
        p = p[p.code.isin(self.market.index)]
        self.codes = sorted(p.code.unique())
        self.cpos = {c: i for i, c in enumerate(self.codes)}
        w = lambda df, col: df.pivot(index="date", columns="code", values=col).reindex(index=self.cal, columns=self.codes)
        C, CU, V = w(p, "close"), w(p, "close_unadj"), w(p, "volume")
        CU = CU.fillna(C)                                                      # 상폐(네이버)는 무수정 종가·거래대금 없음
        VAL = w(p, "value").fillna(CU * V)
        del p
        e = pd.read_parquet(os.path.join(DER, "daily_ext.parquet"), columns=["code", "date", "market_cap"],
                            filters=[("date", ">=", LOAD_FROM), ("date", "<=", read_to)])
        e["code"] = e.code.astype(str)
        MC = w(e[e.code.isin(self.market.index)], "market_cap").replace(0, np.nan)
        del e
        self.ELIG = elig(C, CU, V, VAL, MC)
        self._raw = (C, CU, V, VAL, MC)                                        # 단위 검사용
        self.C = C.values.astype("float64")
        self.V = V.fillna(0).values
        self.CFF = C.ffill().values.astype("float64")
        self.MC = MC.values.astype("float64")
        self._bcache = {}
        log(f"loaded {len(self.codes)} codes × {len(self.cal)} days ({self.cal[0].date()}~{self.cal[-1].date()}) {time.time() - t0:.0f}s")

    def bench(self, i, j):
        """유니버스(i 일 적격) 동일가중, i 종가 → j 종가 보유 수익."""
        k = (i, j)
        if k not in self._bcache:
            m = self.ELIG[i]
            self._bcache[k] = np.nanmean(self.CFF[j, m] / self.C[i, m] - 1)
        return self._bcache[k]


def elig(C, CU, V, VAL, MC):
    """적격(3절): 그날 거래·무수정 종가 ≥1,000·20일 평균 거래대금 ≥5억·20일 거래량 0 없음·시총 있음 — 과거 20일만 본다."""
    adv = VAL.rolling(20, min_periods=20).mean()
    zero = (V.fillna(0) <= 0).astype("int8").rolling(20, min_periods=20).sum()
    return (C.notna() & (V > 0) & (CU >= MIN_PRICE) & (adv >= MIN_ADV) & (zero == 0) & MC.notna()).values


# ───────────────────────── 사건 → 수익 ─────────────────────────
def breakpoints(ev, qs=(1 / 3, 2 / 3, 0.8)):
    """t0 날짜별로 직전 365일(t0 미포함) 적격 사건 SUE 분위 — 미리 알 수 있는 값만."""
    ev = ev.sort_values("t0")
    t = ev.t0.values
    s = ev.sue.values
    out = {}
    for d in np.unique(t):
        lo, hi = np.searchsorted(t, d - np.timedelta64(365, "D"), "left"), np.searchsorted(t, d, "left")
        w = s[lo:hi]
        out[d] = (len(w), *(np.quantile(w, qs) if len(w) else [np.nan] * len(qs)))
    bp = pd.DataFrame.from_dict(out, orient="index", columns=["n_hist", "q33", "q67", "q80"])
    return bp


def build(pn, events_path=os.path.join(OUT, "events.parquet"), log=print):
    ev = pd.read_parquet(events_path)
    n0 = len(ev)
    ev = ev[ev.code.isin(pn.cpos)].copy()
    ev["E"] = pd.to_datetime(ev.E)
    pos = pn.cal.searchsorted(ev.E.values, "left")                            # E 이후(포함) 첫 거래일
    ev["ti"] = pos
    ev = ev[(ev.ti >= 20) & (ev.ti + 1 < len(pn.cal))].copy()
    ev["t0"] = pn.cal[ev.ti.values]
    ev["ci"] = ev.code.map(pn.cpos).values
    ti, ci = ev.ti.values, ev.ci.values
    ev["elig"] = pn.ELIG[ti, ci]
    ev = ev[ev.elig].copy()
    ti, ci = ev.ti.values, ev.ci.values
    bp = breakpoints(ev)
    ev = ev.join(bp, on="t0")
    ev["grp"] = np.where(ev.n_hist < MIN_HIST, "warm", np.where(ev.sue >= ev.q67, "top", np.where(ev.sue <= ev.q33, "bottom", "mid")))
    ev["top5"] = ev.sue >= ev.q80
    T = len(pn.cal)
    ent = ti + 1
    c0, c1 = pn.C[ti, ci], pn.C[ent, ci]
    ev["can_buy"] = (pn.V[ent, ci] > 0) & np.isfinite(c1) & (np.abs(c1 / c0 - 1) < LIMIT)

    def ret(a, b):
        ok = b < T
        r = np.full(len(a), np.nan)
        r[ok] = pn.CFF[b[ok], ci[ok]] / pn.C[a[ok], ci[ok]] - 1
        return r

    def bench(a, b):
        return np.array([pn.bench(x, y) if y < T else np.nan for x, y in zip(a, b)])

    ev["r"] = np.where(ev.can_buy, ret(ent, ti + 1 + HOLD), np.nan)
    ev["b"] = bench(ent, ti + 1 + HOLD)
    ev["r60"] = np.where(ev.can_buy, ret(ent, ti + 1 + HOLD_LONG), np.nan)
    ev["b60"] = bench(ent, ti + 1 + HOLD_LONG)
    kx = ti + 1 + HOLD
    ev["k200"] = np.where(kx < T, pn.k200[np.minimum(kx, T - 1)] / pn.k200[ent] - 1, np.nan)
    # 미국판 재현용 누적 초과 [0], [1,5], [6,20], [21,60] (t0 기준 종가 구간, 유니버스 동일가중 차감)
    for name, a, b in (("w0", ti - 1, ti), ("w1_5", ti, ti + 5), ("w6_20", ti + 5, ti + 20), ("w21_60", ti + 20, ti + 60)):
        ev[name] = ret(a, b) - bench(a, b)
    ev["mcap"] = pn.MC[ti, ci]
    ev["market"] = pn.market.reindex(ev.code).values
    ev["delisted"] = ev.code.isin(list(pn.delisted))
    log(f"events {n0} → in panel/eligible {len(ev)} (warm {int((ev.grp == 'warm').sum())})")
    return ev


# ───────────────────────── 통계·판정 ─────────────────────────
def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan


def judge(J):
    top = J[J.grp == "top"].dropna(subset=["r", "b"]).copy()
    top["x"] = top.r - COST - top.b
    season = top.groupby(["fy", "q"]).x.mean()
    year = top.groupby(top.t0.dt.year).x.mean()
    gross = J.dropna(subset=["r", "b"]).assign(xg=lambda d: d.r - d.b)
    spread = gross[gross.grp == "top"].xg.mean() - gross[gross.grp == "bottom"].xg.mean()
    ex_best = year.drop(year.idxmax()).mean() if len(year) > 1 else np.nan
    c = [("1 시즌 평균 x>0·t≥2.0", season.mean() > 0 and tstat(season) >= 2.0, f"{season.mean() * 100:+.2f}% t{tstat(season):+.2f} (시즌 {len(season)}, 사건 {len(top)})"),
         ("2 양수 연도 ≥ 2/3", (year > 0).sum() >= np.ceil(len(year) * 2 / 3), f"{(year > 0).sum()}/{len(year)}"),
         ("3 비용 전 상위−하위 > 0", spread > 0, f"{spread * 100:+.2f}%"),
         ("4 최고 연도 빼도 > 0", ex_best > 0, f"{ex_best * 100:+.2f}%")]
    return c, top, season, year


def pct(v, d=2):
    return "" if v is None or pd.isna(v) else f"{v * 100:+.{d}f}"


def line(name, x, by=None):
    x = x.dropna()
    s = f"| {name} | {len(x)} | {pct(x.mean())} | {tstat(x):+.2f} |"
    return s


def report(J, mode, path, check_md):
    c, top, season, year = judge(J)
    ok = all(v for _, v, _ in c)
    L = [f"# 한국판 PEAD — {'개발' if mode == 'dev' else '봉인'} 결과 ({pd.Timestamp.now():%Y-%m-%d %H:%M})", "",
         f"사전등록 PREREGISTRATION.md (sha256 은 PREREGISTRATION.sha256). 매수 t0+1 종가 → 매도 t0+{HOLD + 1} 종가, 비용 왕복 {COST * 100:.2f}%, "
         "x = 사건 수익 − 비용 − 유니버스 동일가중(같은 기간). 가격수익(배당 제외). 단위 %.", "",
         f"## 판정: **{'합격' if ok else '불합격'}**", "", "| 조건 | 충족 | 값 |", "|---|---|---|"]
    L += [f"| {n} | {'✅' if v else '❌'} | {s} |" for n, v, s in c]
    L += ["", "## 연도별 (상위 3분위, x 비용 후)", "", "| 연도 | 사건 | x 평균 | 사건 수익 평균 | 유니버스 | K200 |", "|---|---|---|---|---|---|"]
    for y, g in top.groupby(top.t0.dt.year):
        L.append(f"| {y} | {len(g)} | {pct(g.x.mean())} | {pct(g.r.mean())} | {pct(g.b.mean())} | {pct(g.k200.mean())} |")
    L += ["", "## 시즌별 (회계분기)", "", "| 분기 | x 평균 |", "|---|---|"] + [f"| {fy}Q{q} | {pct(v)} |" for (fy, q), v in season.items()]
    G = J[J.grp.isin(["top", "mid", "bottom"])].dropna(subset=["r", "b"]).copy()
    G["xg"] = G.r - G.b
    L += ["", "## 해석용 (판정 아님)", "", "### 분위별 20거래일 초과(비용 전) — 회피 신호 가치는 하위 행", "",
          "| 집단 | 사건 | 평균 | t(사건) |", "|---|---|---|---|"]
    for gname in ("top", "mid", "bottom"):
        L.append(line(gname, G[G.grp == gname].xg))
    L.append(line("상위 5분위(80%↑)", G[G.top5].xg))
    L += ["", "### 상위 3분위 변형 (x 비용 후, 사건 평균 · 사건 t · 시즌 t)", "", "| 변형 | 사건 | 평균 | t(사건) | t(시즌) |", "|---|---|---|---|---|"]
    T = G[G.grp == "top"]

    def var(name, x, sel=None):
        d = T if sel is None else T[sel(T)]
        xx = x(d)
        st = xx.groupby([d.fy, d.q]).mean()
        L.append(f"| {name} | {xx.notna().sum()} | {pct(xx.mean())} | {tstat(xx):+.2f} | {tstat(st):+.2f} |")

    var("주 시험", lambda d: d.r - COST - d.b)
    var("비용 2배(0.87%)", lambda d: d.r - COST2 - d.b)
    var("60거래일 보유", lambda d: d.r60 - COST - d.b60)
    var("대 K200 (비용 후)", lambda d: d.r - COST - d.k200)
    var("공개=잠정", lambda d: d.r - COST - d.b, lambda d: d.src == "prelim")
    var("공개=정기보고서", lambda d: d.r - COST - d.b, lambda d: d.src == "regular")
    var("코스피", lambda d: d.r - COST - d.b, lambda d: d.market == "KOSPI")
    var("코스닥", lambda d: d.r - COST - d.b, lambda d: d.market == "KOSDAQ")
    size = G.groupby(["fy", "q"]).mcap.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    T = T.assign(size=size.reindex(T.index))
    for k, nm in enumerate(("시총 하위1/3", "시총 중간", "시총 상위1/3")):
        var(nm, lambda d: d.r - COST - d.b, lambda d, k=k: d["size"] == k)
    # 미국판 재현: 같은 회계분기 내 3분위, 상위−하위, 달력월 군집 t
    R = J[J.grp != "warm"].copy()
    R["cq"] = R.groupby(["fy", "q"]).sue.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    L += ["", "### 미국판 재현 — 같은 회계분기 3분위 상위−하위 누적 초과 (달력월 군집 t, 미국 합격선 t≥3.0 참고)", "",
          "| 구간 | 상위 | 하위 | 상위−하위 | t |", "|---|---|---|---|---|"]
    for w in ("w0", "w1_5", "w6_20", "w21_60"):
        hi, lo = R[R.cq == 2], R[R.cq == 0]
        m = (hi.groupby(hi.t0.dt.to_period("M"))[w].mean() - lo.groupby(lo.t0.dt.to_period("M"))[w].mean()).dropna()
        L.append(f"| {w} | {pct(hi[w].mean())} | {pct(lo[w].mean())} | {pct(hi[w].mean() - lo[w].mean())} | {tstat(m):+.2f} |")
    L += ["", f"상폐 종목 사건(판정 구간): {int(J[J.grp != 'warm'].delisted.sum())} · 매수 불가 제외: {int((~J.can_buy).sum())}", "",
          "### 잠정 공시 판별 점검", "", check_md]
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return ok


# ───────────────────────── 단위 검사 ─────────────────────────
def selftest():
    pn = Panel(DEV["read_to"])
    ev = build(pn)
    D = pd.Timestamp("2020-06-30")
    i = pn.cal.searchsorted(D)
    # 1) D 이후 가격을 흔들어도 D 이전 t0 사건의 적격·분위 불변
    C0, CFF0, ELIG0 = pn.C.copy(), pn.CFF.copy(), pn.ELIG.copy()
    rng = np.random.default_rng(0)
    noise = rng.uniform(0.5, 1.5, size=pn.C[i + 1:].shape)
    C, CU, V, VAL, MC = pn._raw
    m = np.vstack([np.ones((i + 1, C.shape[1])), noise])
    pn.ELIG = elig(C * m, CU * m, V * rng.uniform(0, 2, size=V.shape), VAL * m, MC * m)   # 적격 행렬도 흔든 값으로 다시 계산
    assert (pn.ELIG[:i + 1] == ELIG0[:i + 1]).all() and (pn.ELIG[i + 1:] != ELIG0[i + 1:]).any(), "적격 판정이 미래를 봄(또는 섭동 무효)"
    pn.C[i + 1:] *= noise
    pn.CFF = pd.DataFrame(pn.C).ffill().values
    pn._bcache = {}
    ev2 = build(pn)
    a = ev[ev.t0 <= D].set_index(["code", "fy", "q"])
    b = ev2[ev2.t0 <= D].set_index(["code", "fy", "q"])
    assert a.index.equals(b.index) and (a.grp == b.grp).all(), "D 이후 가격이 D 이전 사건 선정에 영향"
    # 2) 매도일 이후 가격만 흔들면 20일 수익 불변(매도일 ≤ D 인 사건)
    pn.C, pn.CFF = C0.copy(), CFF0.copy()
    pn._bcache = {}
    pn.C[i + 1:] *= rng.uniform(0.5, 1.5, size=pn.C[i + 1:].shape)
    pn.CFF = np.vstack([CFF0[:i + 1], pd.DataFrame(pn.C).ffill().values[i + 1:]])
    ev3 = build(pn)
    done = ev[(ev.ti + 1 + HOLD) <= i].set_index(["code", "fy", "q"])
    e3 = ev3.set_index(["code", "fy", "q"]).reindex(done.index)
    assert np.allclose(done.r.fillna(-9), e3.r.fillna(-9)) and np.allclose(done.b.fillna(-9), e3.b.fillna(-9)), "매도일 이후 가격이 수익에 영향"
    pn.C, pn.CFF, pn.ELIG = C0, CFF0, ELIG0
    # 3) 기준점: D 이후 사건 SUE 를 바꿔도 D 이전 기준점 불변
    e4 = ev.copy()
    e4.loc[e4.t0 > D, "sue"] = rng.normal(size=int((e4.t0 > D).sum()))
    bp1, bp2 = breakpoints(ev), breakpoints(e4)
    keep = bp1.index <= np.datetime64(D)
    assert np.allclose(bp1[keep].fillna(0).values, bp2[keep].fillna(0).values), "기준점이 미래 사건을 봄"
    # 4) 상폐 포함, 비용
    assert ev.delisted.sum() > 0, "상폐 종목 사건이 없다"
    assert abs(COST - 0.0057) < 1e-12 and abs(COST2 - 0.0087) < 1e-12
    print(f"selftest OK — 가격 섭동·기준점 섭동 불변, 상폐 사건 {int(ev.delisted.sum())}, 비용 {COST * 100:.2f}%")


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
    ev = build(pn)
    lo, hi = cfg["judge"]
    J = ev[(ev.t0 >= lo) & (ev.t0 <= hi)].copy()
    J.to_parquet(os.path.join(OUT, f"events_{mode}.parquet"))
    chk = open(os.path.join(OUT, "results_prelim_check.md")).read().split("\n", 2)[-1]
    ok = report(J, mode, path, chk)
    print(f"{path} — {'합격' if ok else '불합격'}")


if __name__ == "__main__":
    main()
