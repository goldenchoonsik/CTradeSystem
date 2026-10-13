"""상한가 직전 조건 매매 (PREREGISTRATION_trade.md 그대로). 개발 합격 규칙만 봉인 구간을 1회 연다.

  .venv/bin/python docs/research/20261012_limit_up/trade_pre_lu.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "20261011_vol_spike"))
import analyze_limit_up as alu  # noqa: E402
import pre20  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

DEV_END, SEAL_FROM, SEAL_END = pd.Timestamp("2026-05-29"), pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-08")
RT, TP, SL, HOLD, SLOTS = 0.0027, 0.20, -0.10, 5, 10


def prepare():
    d = pre20.build()
    keep = ["code", "date", "market", "o", "l", "f", "ca", "ha", "oa", "lu", "lu_prev20", "p_raw", "val20", "r1_", "c1", "c6"] + \
        [f"v{k}" for k in range(1, 6)] + [c for c in alu.FEATS if c != "kosdaq"]
    d = d[list(dict.fromkeys(keep))].sort_values(["code", "date"]).reset_index(drop=True).copy()
    g = d.groupby("code", sort=False)
    d["la"] = d.l * d.f
    cal = pd.Index(sorted(d.date.unique()))
    pos = pd.Series(np.arange(len(cal)), index=cal)
    d["gap_days"] = d.date.map(pos) - g.date.shift(20).map(pos)        # 20행 전이 정확히 20거래일 전이면 정지 없음
    for k in range(HOLD):
        d[f"H{k}"], d[f"L{k}"], d[f"C{k}"], d[f"O{k}"] = g.ha.shift(-k), g.la.shift(-k), g.ca.shift(-k), g.oa.shift(-k)
    d["entry_raw"] = d.o
    d["slip"] = np.maximum(0.003, 2 * tick_size(d.o.to_numpy()) / d.o.to_numpy())
    d["cost"] = RT + d.slip
    d["elig"] = (d.p_raw >= 1000) & (d.val20 >= 5) & (d.gap_days == 20) & (d.o > 0)
    d["v5med"] = d[[f"v{k}" for k in range(1, 6)]].median(axis=1)
    d["c5"] = (1 + d.c1) / (1 + d.c6) - 1                           # D-6 → D-1 누적
    return d


def exits(x):
    e = x.oa
    out = {"X1": x.C0 / e - 1, "X2": x.C4 / e - 1}
    ret = pd.Series(np.nan, index=x.index)
    done = pd.Series(False, index=x.index)
    for k in range(HOLD):
        o, h, l, c = x[f"O{k}"] / e - 1, x[f"H{k}"] / e - 1, x[f"L{k}"] / e - 1, x[f"C{k}"] / e - 1
        if k > 0:                                                    # 갭으로 이미 넘은 경우 시가 체결
            gsl = ~done & (o <= SL)
            ret[gsl], done = o[gsl], done | gsl
            gtp = ~done & (o >= TP)
            ret[gtp], done = o[gtp], done | gtp
        hit_sl = ~done & (l <= SL)
        ret[hit_sl], done = SL, done | hit_sl
        hit_tp = ~done & (h >= TP)
        ret[hit_tp], done = TP, done | hit_tp
        if k == HOLD - 1:
            ret[~done] = c[~done]
    out["X3"] = ret
    return out


def model_top(d, k=10):
    import lightgbm as lgb
    x = d.assign(kosdaq=(d.market == "KOSDAQ").astype(int)).dropna(subset=["r1", "vol20", "val20"])
    tr, te = x[x.date < "2026-01-01"], x[x.date >= "2026-01-01"].copy()
    params = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 7, "num_threads": 4}
    m = lgb.train(params, lgb.Dataset(tr[alu.FEATS], tr.lu), num_boost_round=400)
    te["p"] = m.predict(te[alu.FEATS])
    te = te[te.elig]
    te["rk"] = te.groupby("date").p.rank(ascending=False, method="first")
    return te.index[te.rk <= k]


def tstat(day, thin):
    s = day.iloc[::thin] if thin > 1 else day
    return s.mean() / s.std() * np.sqrt(len(s)) if len(s) > 2 else np.nan


def account(tr, hold_days):
    """10칸 계좌: 진입일 순서로 빈 칸에 배정, 실현 손익 복리. 같은 종목 중복 금지."""
    cal = sorted(tr.date.unique())
    idx = {dt: i for i, dt in enumerate(cal)}
    eq, peak, mdd, open_pos, n = 1.0, 1.0, 0.0, [], 0
    curve = []
    for dt, x in tr.sort_values("date").groupby("date"):
        i = idx[dt]
        still = []
        for p in open_pos:
            if p["exit_i"] < i:
                eq += p["alloc"] * p["ret"]
            else:
                still.append(p)
        open_pos = still
        peak, mdd = max(peak, eq), min(mdd, eq / peak - 1)
        held = {p["code"] for p in open_pos}
        for r in x.itertuples():
            if len(open_pos) >= SLOTS or r.code in held:
                continue
            open_pos.append({"code": r.code, "alloc": eq / SLOTS, "ret": r.net, "exit_i": i + hold_days - 1})
            held.add(r.code)
            n += 1
        curve.append((dt, eq))
    for p in open_pos:
        eq += p["alloc"] * p["ret"]
    return eq - 1, min(mdd, eq / max(peak, eq) - 1), n


def main():
    d = prepare()
    ex = exits(d)
    for k, v in ex.items():
        d[k] = v - d.cost
    d["gross_X1"], d["gross_X3"] = ex["X1"], ex["X3"]
    e = d.elig
    sig = {
        "S1 거래 폭증 초기": e & (d.v1 >= 5) & d.r1_.between(-0.05, 0.10) & (d.lu_prev20 == 0),
        "S2 소외 후 거래 증가": e & (d.c1 <= 0) & (d.v5med >= 1.5) & d.c5.between(-0.05, 0.05) & (d.lu_prev20 == 0),
        "S4 급락 후 거래 폭증": e & (d.r1_ <= -0.10) & (d.v1 >= 5),
    }
    s3 = pd.Series(False, index=d.index)
    s3[model_top(d)] = True
    sig["S3 상한가 모델 상위 10"] = s3
    hold = {"X1": 1, "X2": HOLD, "X3": HOLD}
    xlab = {"X1": "당일 종가", "X2": "5일 단순", "X3": "5일 +20%/−10%"}
    L = ["# 상한가 직전 조건 매매 결과 (사전등록 PREREGISTRATION_trade.md)", "",
         f"비용 = 왕복 0.27% + max(0.3%, 2틱). 개발 ~{DEV_END:%Y-%m-%d} (S3 는 2026-01~), 봉인 {SEAL_FROM:%Y-%m-%d}~ 은 개발 합격 규칙만.", "",
         "| 신호 | 청산 | 거래 | 진입일 | 그날 상한가 적중 | 비용 전 평균 | 비용 후 평균(진입일 가중) | t | 승률 | 분기 양수 | 마지막 3개월 | 계좌(10칸) 누적 | 최대 낙폭 | 봉인 | 판정 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    passed = 0
    for sname, m in sig.items():
        for x in ("X1", "X2", "X3"):
            t_ = d[m & d[x].notna()]
            dev = t_[t_.date <= DEV_END]
            if len(dev) < 30:
                continue
            day = dev.groupby("date")[x].mean()
            t = tstat(day, hold[x])
            q = day.groupby(day.index.to_period("Q")).mean()
            qpos = (q > 0).mean()
            last3 = day[day.index > DEV_END - pd.Timedelta(days=92)].mean()
            acc, mdd, n = account(dev.assign(net=dev[x]), hold[x])
            gross = (dev[x] + dev.cost).mean()
            ok_dev = day.mean() > 0 and t > 3 and qpos >= 2 / 3 and last3 > 0
            seal_txt, ok = "열지 않음", False
            if ok_dev:
                s = t_[(t_.date >= SEAL_FROM) & (t_.date <= SEAL_END)].groupby("date")[x].mean()
                st = tstat(s, hold[x])
                ok = s.mean() > 0 and st > 2
                seal_txt = f"{s.mean()*100:+.2f}% t{st:+.1f}"
            passed += ok
            L.append(f"| {sname} | {xlab[x]} | {len(dev):,} | {len(day)} | {dev.lu.mean():.1%} | {gross*100:+.2f}% | {day.mean()*100:+.2f}% | {t:+.2f} | "
                     f"{(dev[x] > 0).mean():.0%} | {qpos:.0%} | {last3*100:+.2f}% | {acc*100:+.0f}% ({n}건) | {mdd*100:.0f}% | {seal_txt} | {'합격' if ok else '불합격'} |")
    L += ["", f"합격 {passed} / 12", ""]
    # 참고: 익절·손절 청산 사유 분포
    L += ["## 참고 — X3(+20%/−10%) 청산 사유", "", "| 신호 | 익절 | 손절 | 기간 만료 |", "|---|---|---|---|"]
    for sname, m in sig.items():
        r = d.loc[m & (d.date <= DEV_END), "gross_X3"].dropna()
        if len(r):
            L.append(f"| {sname} | {(r >= TP - 1e-9).mean():.0%} | {(r <= SL + 1e-9).mean():.0%} | {((r < TP - 1e-9) & (r > SL + 1e-9)).mean():.0%} |")
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_trade.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
