"""상한가 '장중' 연구 (PREREGISTRATION_intraday.md 그대로).

1단계(캐시): 하루 고가 ≥ 전일 +15% 인 종목-일만 1분봉을 읽어 사건(진입선 15·20·25%)과 직전 흐름·청산 결과를 만든다.
2단계: 서술(상한가 마감 vs 풀림), 규칙 R 9개, 모델 M 6개 판정. 개발 합격만 봉인을 연다.

  .venv/bin/python -W ignore docs/research/20261012_limit_up/intraday_lu.py
"""
import gc
import multiprocessing as mp
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "20261011_vol_spike"))
import analyze_limit_up as alu  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

STAGE = alu.STAGE
CACHE = os.path.join(STAGE, "derived", "intraday_lu_events.parquet")
THRESH = (0.15, 0.20, 0.25)
LUL, SL, RT = 1.295, -0.07, 0.0027
DEV_END, SEAL_FROM, SEAL_END = pd.Timestamp("2026-05-29"), pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-08")
TRAIN_END, VAL_FROM = pd.Timestamp("2025-09-30"), pd.Timestamp("2025-10-01")


def _price_at(ts_min, close, t):
    """t 분(09:00 기준) 이하 마지막 봉 종가."""
    i = np.searchsorted(ts_min, t, side="right") - 1
    return close[i] if i >= 0 else np.nan


def worker(job):
    code, day, prev, val20 = job                                   # prev = 전일 종가(당일 1분봉 단위)
    fp = os.path.join(STAGE, "raw/creon/candle_1m", code, f"{day:%Y-%m-%d}.csv")
    if not os.path.exists(fp):
        return []
    m = pd.read_csv(fp, parse_dates=["timestamp"])
    hm = m.timestamp.dt.hour * 100 + m.timestamp.dt.minute
    m = m[hm.between(900, 1530) & (m.upbit_volume > 0)]
    if len(m) < 10:
        return []
    t = ((m.timestamp.dt.hour - 9) * 60 + m.timestamp.dt.minute).to_numpy()
    o, h, l, c, v = (m[k].to_numpy(dtype=float) for k in ("upbit_open", "upbit_high", "upbit_low", "upbit_close", "upbit_trade_price"))
    lu_line = prev * LUL
    lu_hit = np.nonzero(h >= lu_line)[0]
    lu_touch_min = int(t[lu_hit[0]]) if len(lu_hit) else -1
    lu_close = bool(c[-1] >= lu_line * 0.999 and t[-1] >= 390)
    out = []
    gap = o[0] / prev - 1
    vi_hit = np.nonzero(h >= prev * 1.10)[0]
    for T in THRESH:
        thr = prev * (1 + T)
        if o[0] >= thr:
            continue
        hit = np.nonzero(h >= thr)[0]
        if not len(hit):
            continue
        i = hit[0]
        if not (1 <= t[i] <= 370):                                # 09:01 ~ 15:10
            continue
        # 직전 흐름 (신호 봉까지)
        run_hi, pull = 0.0, 0
        for k in range(i + 1):
            if h[k] > run_hi:
                run_hi = h[k]
            elif l[k] <= run_hi * 0.97 and (k == 0 or l[k - 1] > run_hi * 0.97):
                pull += 1
        v5 = v[(t > t[i] - 5) & (t <= t[i])].sum()
        v30 = v[(t > t[i] - 35) & (t <= t[i] - 5)].sum()
        feat = dict(code=code, date=day, T=T, sig_min=int(t[i]), gap=gap, cumval=v[: i + 1].sum() / max(val20, 1),
                    r5=c[i] / _price_at(t, c, t[i] - 5) - 1, r10=c[i] / _price_at(t, c, t[i] - 10) - 1, r30=c[i] / _price_at(t, c, t[i] - 30) - 1,
                    acc=(v5 / 5) / (v30 / 30) if v30 > 0 else np.nan, pullbacks=pull,
                    vi_since=int(t[i] - t[vi_hit[0]]) if len(vi_hit) and vi_hit[0] <= i else -1,
                    lu_touch_min=lu_touch_min, lu_close=lu_close)
        # 진입: 신호 다음 봉부터 3분 안 첫 체결 봉 시가
        nxt = np.nonzero((t > t[i]) & (t <= t[i] + 3))[0]
        if not len(nxt):
            feat["fill"] = "no_trade"
            out.append(feat)
            continue
        j = nxt[0]
        e = o[j]
        if e >= lu_line * 0.999:
            feat["fill"] = "locked"
            out.append(feat)
            continue
        feat["fill"], feat["entry"] = "ok", e
        last = len(c) - 1
        x1 = x2 = None
        for k in range(j, len(c)):
            if x1 is None and h[k] >= lu_line:
                x1 = lu_line
            if x2 is None:
                sl = e * (1 + SL)
                if k > j and o[k] <= sl:
                    x2 = o[k]
                elif l[k] <= sl:
                    x2 = sl
                elif h[k] >= lu_line:
                    x2 = lu_line
            if x1 is not None and x2 is not None:
                break
        feat["x1"] = (x1 if x1 is not None else c[last]) / e - 1
        feat["x2"] = (x2 if x2 is not None else c[last]) / e - 1
        feat["x3"] = c[last] / e - 1
        out.append(feat)
    return out


def build_events():
    if os.path.exists(CACHE):
        return pd.read_parquet(CACHE)
    d, _ = alu.features()
    d = d[(d.hret >= 0.15) & (d.p_raw >= 1000) & (d.val20 >= 5)]
    prev_raw_equiv = (d.ca / (1 + d.ret)) / d.f                    # 전일 수정 종가를 당일 원 단위로
    jobs = list(zip(d.code, d.date, prev_raw_equiv, d.val20 * 1e8))
    side = d[["code", "date", "r1", "vol20", "mcap", "p_raw", "lu"]].copy()
    del d
    gc.collect()
    with mp.get_context("spawn").Pool(4) as p:
        rows = [r for res in p.imap_unordered(worker, jobs, chunksize=200) for r in res]
    ev = pd.DataFrame(rows).merge(side, on=["code", "date"], how="left")
    # 시장 열기: 같은 날 이 신호 시각 이전에 상한가에 닿은 종목 수
    touch = ev[ev.lu_touch_min >= 0].drop_duplicates(["code", "date"])[["date", "lu_touch_min"]]
    tm = {dt: np.sort(g.lu_touch_min.to_numpy()) for dt, g in touch.groupby("date")}
    ev["heat"] = [np.searchsorted(tm.get(dt, np.array([])), s, side="left") for dt, s in zip(ev.date, ev.sig_min)]
    ev.to_parquet(CACHE)
    return ev


def stats(x, col):
    day = x.groupby("date")[col].mean()
    t = day.mean() / day.std() * np.sqrt(len(day)) if len(day) > 2 else np.nan
    q = day.groupby(day.index.to_period("Q")).mean()
    return day, t, (q > 0).mean()


def main():
    ev = build_events()
    ok = ev[ev.fill == "ok"].copy()
    ok["cost"] = RT + np.maximum(0.003, 2 * tick_size(ok.entry.to_numpy()) / ok.entry.to_numpy())
    for x in ("x1", "x2", "x3"):
        ok[f"n_{x}"] = ok[x] - ok.cost
    L = [f"# 상한가 장중 연구 (1분봉, {ev.date.min():%Y-%m-%d} ~ {ev.date.max():%Y-%m-%d})", "",
         "## 사건 수와 체결 가능성", "", "| 진입선 | 사건 | 체결 가능 | 3분 안 체결 없음 | 진입 봉이 이미 상한가(체결 불가) | 상한가 마감 비율(체결 가능 중) |", "|---|---|---|---|---|---|"]
    for T in THRESH:
        e = ev[ev["T"] == T]
        k = ok[ok["T"] == T]
        L.append(f"| +{T:.0%} | {len(e):,} | {(e.fill == 'ok').mean():.0%} | {(e.fill == 'no_trade').mean():.0%} | {(e.fill == 'locked').mean():.0%} | {k.lu_close.mean():.1%} |")
    # 서술: +15% 사건에서 상한가 마감 vs 풀림
    e15 = ok[(ok["T"] == 0.15) & (ok.date <= DEV_END)]
    base = e15.lu_close.mean()
    L += ["", f"## +15% 첫 도달 사건의 직전 흐름 — 상한가 마감 확률 배수 (개발 구간 {len(e15):,}건, 평균 {base:.1%})", ""]
    feats = [("sig_min", "신호 시각(09:00 후 분)"), ("gap", "시가 갭"), ("cumval", "당일 누적 거래대금 ÷ 평소 하루"), ("r5", "직전 5분 수익"),
             ("r30", "직전 30분 수익"), ("acc", "직전 5분 거래 가속"), ("pullbacks", "−3% 눌림 횟수"), ("vi_since", "+10% 도달 후 경과 분"),
             ("heat", "이 시각 이전 상한가 종목 수"), ("vol20", "20일 변동성"), ("mcap", "시총(억)"), ("r1", "전일 수익")]
    for f, lab in feats:
        x = e15.dropna(subset=[f])
        try:
            qb = pd.qcut(x[f].rank(method="first"), 5, labels=["최저", "낮음", "중간", "높음", "최고"])
        except ValueError:
            continue
        g = x.groupby(qb, observed=True).agg(lo=(f, "min"), hi=(f, "max"), p=("lu_close", "mean"), ret=("n_x1", "mean"))
        L.append(f"- **{lab}**: " + " · ".join(f"{k}[{r.lo:.3g}~{r.hi:.3g}] {r.p:.0%}({r.p/base:.1f}×, X1 {r.ret*100:+.1f}%)" for k, r in g.iterrows()))
    # 판정 R
    L += ["", "## 규칙 R (조건 없음) — 개발 구간", "", "| 진입선 | 청산 | 건 | 비용 전 | 비용 후 | t | 분기 양수 | 마지막 3개월 | 봉인 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|"]
    passed = 0
    xl = {"x1": "상한가 매도", "x2": "상한가 매도+손절 7%", "x3": "종가"}
    for T in THRESH:
        for x in ("x1", "x2", "x3"):
            k = ok[(ok["T"] == T)]
            dev = k[k.date <= DEV_END]
            day, t, qpos = stats(dev, f"n_{x}")
            last3 = day[day.index > DEV_END - pd.Timedelta(days=92)].mean()
            okd = day.mean() > 0 and t > 3 and qpos >= 2 / 3 and last3 > 0
            seal_txt, okk = "열지 않음", False
            if okd:
                sday, st, _ = stats(k[(k.date >= SEAL_FROM) & (k.date <= SEAL_END)], f"n_{x}")
                okk = sday.mean() > 0 and st > 2
                seal_txt = f"{sday.mean()*100:+.2f}% t{st:+.1f}"
            passed += okk
            L.append(f"| +{T:.0%} | {xl[x]} | {len(dev):,} | {dev[x].mean()*100:+.2f}% | {day.mean()*100:+.2f}% | {t:+.2f} | {qpos:.0%} | {last3*100:+.2f}% | {seal_txt} | {'합격' if okk else '불합격'} |")
    # 판정 M
    import lightgbm as lgb
    MF = ["sig_min", "gap", "cumval", "r5", "r10", "r30", "acc", "pullbacks", "vi_since", "heat", "r1", "vol20", "mcap", "p_raw"]
    params = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 50, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 7, "num_threads": 4}
    L += ["", "## 모델 M — 학습 ~2025-09 사건 → 검증 2025-10 ~ 2026-05, 점수 상위 30% 만 진입", "",
          "| 진입선 | AUC(검증) | 상위30% 상한가 마감 | 전체 | 청산 | 건 | 비용 후 | t | 분기 양수 | 마지막 3개월 | 봉인 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for T in THRESH:
        k = ok[ok["T"] == T]
        tr, va = k[k.date <= TRAIN_END], k[(k.date >= VAL_FROM) & (k.date <= DEV_END)].copy()
        mdl = lgb.train(params, lgb.Dataset(tr[MF], tr.lu_close.astype(int)), num_boost_round=300)
        va["p"] = mdl.predict(va[MF])
        r = va.p.rank()
        npos = va.lu_close.sum()
        auc = (r[va.lu_close].sum() - npos * (npos + 1) / 2) / (npos * (len(va) - npos)) if 0 < npos < len(va) else np.nan
        cut = va.p.quantile(0.7)
        top = va[va.p >= cut]
        for x in ("x1", "x2"):
            day, t, qpos = stats(top, f"n_{x}")
            last3 = day[day.index > DEV_END - pd.Timedelta(days=92)].mean()
            okd = day.mean() > 0 and t > 3 and qpos >= 2 / 3 and last3 > 0
            seal_txt, okk = "열지 않음", False
            if okd:
                tr2 = k[k.date <= DEV_END]
                m2 = lgb.train(params, lgb.Dataset(tr2[MF], tr2.lu_close.astype(int)), num_boost_round=300)
                se = k[(k.date >= SEAL_FROM) & (k.date <= SEAL_END)].copy()
                se["p"] = m2.predict(se[MF])
                sday, st, _ = stats(se[se.p >= cut], f"n_{x}")
                okk = sday.mean() > 0 and st > 2
                seal_txt = f"{sday.mean()*100:+.2f}% t{st:+.1f}"
            passed += okk
            L.append(f"| +{T:.0%} | {auc:.3f} | {top.lu_close.mean():.1%} | {va.lu_close.mean():.1%} | {xl[x]} | {len(top):,} | {day.mean()*100:+.2f}% | {t:+.2f} | {qpos:.0%} | {last3*100:+.2f}% | {seal_txt} | {'합격' if okk else '불합격'} |")
    L += ["", f"합격 {passed} / 15"]
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_intraday.md"), "w", encoding="utf-8") as fh:
        fh.write(out + "\n")


if __name__ == "__main__":
    main()
