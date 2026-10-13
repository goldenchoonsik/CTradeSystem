"""상한가 적중률(정밀도)을 올리는 단서 찾기 — 단서 묶음을 하나씩 더한 모델의 시간 분할 검증.

학습 ~2025-09-30 → 검증 2025-10-01 ~ 2026-05-29 (봉인 2026-06~ 은 쓰지 않음).
지표: 매일 점수 상위 5·10·20 중 그날 상한가 마감 비율(정밀도), AUC, 상위 10 시가→종가 매매 손익(비용 후).

묶음 (모두 진입 시점 = D 시가 전에 알 수 있는 값)
  A 기존 전날 특징(results.md 모델)
  B 수급: D-1 투자자·프로그램 순매수(거래대금 대비), 직전 5일 누적
  C 테마: D-1 같은 업종 상한가 수(자기 제외)·업종 중앙 수익률, 시장 전체 D-1 상한가 수
  D 재무·지분: 공개된 최신 재무(적자 연수·자본잠식·부채비율·TTM 순이익)·최대주주 지분·변화
  E D-1 장중 모양(1분봉): 마지막 30분 수익·거래 비중, 종가/고가, 고가 시각, D-1 상한가 터치 후 풀림
  H D 시초가 갭(동시호가 예상체결가로 진입 전 확인 가능하다고 가정)

  .venv/bin/python -W ignore docs/research/20261012_limit_up/clues.py
"""
import os
import sys
import gc
import multiprocessing as mp

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "20261012_endgame_e"))
sys.path.insert(0, os.path.join(HERE, "..", "20261011_vol_spike"))
import analyze_limit_up as alu  # noqa: E402
import fundamentals as fu  # noqa: E402
import run_e  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

TRAIN_END, VAL_FROM, VAL_END = pd.Timestamp("2025-09-30"), pd.Timestamp("2025-10-01"), pd.Timestamp("2026-05-29")
STAGE = alu.STAGE
INTRA_CACHE = os.path.join(STAGE, "derived", "clues_intraday_prev.parquet")


def intraday(args):
    code, day = args
    fp = os.path.join(STAGE, "raw/creon/candle_1m", code, f"{day:%Y-%m-%d}.csv")
    if not os.path.exists(fp):
        return None
    m = pd.read_csv(fp, usecols=["timestamp", "upbit_high", "upbit_low", "upbit_close", "upbit_trade_price"], parse_dates=["timestamp"])
    hm = m.timestamp.dt.hour * 100 + m.timestamp.dt.minute
    m = m[hm.between(900, 1530)]
    if len(m) < 30 or m.upbit_trade_price.sum() <= 0:
        return None
    hm = m.timestamp.dt.hour * 100 + m.timestamp.dt.minute
    c = m.upbit_close.to_numpy()
    last = m[hm >= 1500]
    pre = m[hm < 1500]
    hi_i = int(np.argmax(m.upbit_high.to_numpy()))
    t = m.timestamp.iloc[hi_i]
    return (code, day, (c[-1] / pre.upbit_close.iloc[-1] - 1) if len(pre) else np.nan,
            last.upbit_trade_price.sum() / m.upbit_trade_price.sum(), c[-1] / m.upbit_high.max(),
            (t.hour - 9) * 60 + t.minute)


def intraday_stage():
    """후보(전날 |수익| ≥ 5% 또는 거래대금 3배+ 또는 20일 변동성 5%+)의 D-1 1분봉 모양 → 캐시. 큰 표를 작업 프로세스에 넘기지 않는다."""
    if os.path.exists(INTRA_CACHE):
        return
    d, _ = alu.features()
    d = d.sort_values(["code", "date"])
    d["prev_date"] = d.groupby("code", sort=False).date.shift(1)
    cand = d[(d.r1.abs() >= 0.05) | (d.vratio >= 3) | (d.vol20 >= 0.05)].dropna(subset=["prev_date"])
    jobs = list(zip(cand.code, cand.prev_date))
    del d, cand
    gc.collect()
    with mp.get_context("spawn").Pool(4) as p:
        res = [r for r in p.imap_unordered(intraday, jobs, chunksize=500) if r]
    e = pd.DataFrame(res, columns=["code", "prev_date", "e_last30_ret", "e_last30_share", "e_close_hi", "e_hi_min"])
    e.to_parquet(INTRA_CACHE)


def build():
    d, uni = alu.features()
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    g = d.groupby("code", sort=False)
    d["prev_date"] = g.date.shift(1)
    d["gap"] = (d.o * d.f) / g.ca.shift(1) - 1                                     # H
    # B 수급
    fl = run_e.load_flows()
    d = d.merge(fl, on=["date", "code"], how="left")
    g = d.groupby("code", sort=False)
    for c in ("prsn", "frgn", "orgn", "prog"):
        d[f"b1_{c}"] = g[c].shift(1) / g.val.shift(1)
        d[f"b5_{c}"] = g[c].transform(lambda s: s.shift(1).rolling(5, min_periods=3).sum()) / g.val.transform(lambda s: s.shift(1).rolling(5, min_periods=3).sum())
    d = d.drop(columns=["prsn", "frgn", "orgn", "prog"])
    # C 테마
    inf = fu.info()
    d["sector"] = d.code.map(inf.sector).fillna("")
    day_lu = d.groupby("date").lu.sum()
    sec_lu = d.groupby(["date", "sector"]).lu.sum()
    sec_ret = d.groupby(["date", "sector"]).ret.median()
    g = d.groupby("code", sort=False)
    d["lu_y"] = g.lu.shift(1)
    key = list(zip(d.prev_date, d.sector))
    d["c_sec_lu"] = sec_lu.reindex(key).to_numpy() - d.lu_y.fillna(0).to_numpy()
    d["c_sec_ret"] = sec_ret.reindex(key).to_numpy()
    d["c_mkt_lu"] = day_lu.reindex(d.prev_date).to_numpy()
    d.loc[d.sector == "", ["c_sec_lu", "c_sec_ret"]] = np.nan
    # D 재무·지분
    f, ls = fu.fundamentals()
    d = fu.attach(d, f, ls)
    o = fu.ownership()
    fu.OWN_CODES.update(o.stock_code.unique())
    d = fu.attach_own(d, o)
    d["d_impaired"] = (d.total_cptl < d.cpfn).astype(float).where(d.total_cptl.notna())
    # E D-1 장중 모양 — 별도 단계(intraday_stage)에서 계산한 캐시
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    e = pd.read_parquet(INTRA_CACHE)
    d = d.merge(e, on=["code", "prev_date"], how="left")
    g = d.groupby("code", sort=False)
    d["e_touch_fail_y"] = ((g.touch.shift(1) == 1) & (g.lu.shift(1) == 0)).astype(float)
    d["kosdaq"] = (d.market == "KOSDAQ").astype(int)
    return d


GROUPS = {
    "A": alu.FEATS,
    "B": ["b1_prsn", "b1_frgn", "b1_orgn", "b1_prog", "b5_prsn", "b5_frgn", "b5_orgn", "b5_prog"],
    "C": ["c_sec_lu", "c_sec_ret", "c_mkt_lu"],
    "D": ["loss_streak", "d_impaired", "lblt_rate", "ttm_thtr_ntin", "own_total", "own_chg", "top_corp"],
    "E": ["e_last30_ret", "e_last30_share", "e_close_hi", "e_hi_min", "e_touch_fail_y"],
    "H": ["gap"],
}
PARAMS = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 7, "num_threads": 4}


def evaluate(d, feats):
    import lightgbm as lgb
    tr = d[d.date <= TRAIN_END]
    va = d[(d.date >= VAL_FROM) & (d.date <= VAL_END)].copy()
    m = lgb.train(PARAMS, lgb.Dataset(tr[feats], tr.lu), num_boost_round=400)
    va["p"] = m.predict(va[feats])
    r = va.p.rank()
    npos = va.lu.sum()
    auc = (r[va.lu == 1].sum() - npos * (npos + 1) / 2) / (npos * (len(va) - npos))
    va["rk"] = va.groupby("date").p.rank(ascending=False, method="first")
    out = {"auc": auc}
    for k in (5, 10, 20):
        out[f"p{k}"] = va[va.rk <= k].lu.mean()
    t10 = va[(va.rk <= 10) & va.oc.notna()]
    out["oc10"] = (t10.oc - t10.cost).mean()
    out["oc10_lu"] = t10[t10.lu == 1].oc.mean()
    out["oc10_no"] = t10[t10.lu == 0].oc.mean()
    imp = pd.Series(m.feature_importance("gain"), index=feats)
    return out, imp / imp.sum()


def main():
    intraday_stage()
    d = build()
    for c in d.select_dtypes("float64").columns:
        d[c] = d[c].astype("float32")
    d["oc"] = d.c / d.o - 1
    d["cost"] = 0.0027 + np.maximum(0.003, 2 * tick_size(d.o.to_numpy()) / d.o.to_numpy())
    d = d[(d.p_raw >= 1000) & (d.val20 >= 5) & d.r1.notna() & d.vol20.notna()]            # 매매 가능 대상만
    va = d[(d.date >= VAL_FROM) & (d.date <= VAL_END)]
    L = [f"# 상한가 적중률 단서 (학습 ~{TRAIN_END:%Y-%m-%d} → 검증 {VAL_FROM:%Y-%m-%d} ~ {VAL_END:%Y-%m-%d}, 봉인 미사용)", "",
         f"- 대상: 실제가 ≥ 1,000원·20일 평균 거래대금 ≥ 5억. 검증 구간 상한가 확률 {va.lu.mean()*100:.2f}% (하루 평균 {va.groupby('date').lu.sum().mean():.1f}건)",
         f"- 단서 연결률(검증): 수급 {va.b1_prsn.notna().mean():.0%} · 업종 {va.c_sec_lu.notna().mean():.0%} · 재무 {va.lblt_rate.notna().mean():.0%} · 지분 {va.own_total.notna().mean():.0%} · 전날 장중 {va.e_close_hi.notna().mean():.0%}",
         "- 정밀도 = 매일 점수 상위 k 중 그날 상한가 마감 비율. 매매 = 상위 10 을 시가 매수·종가 매도(비용 후). 본전 적중률 ≈ 28% (results_decompose_s3.md)", "",
         "| 모델 | AUC | 상위5 적중 | 상위10 적중 | 상위20 적중 | 상위10 시가→종가 비용 후 | 맞힌 것 | 못 맞힌 것 |", "|---|---|---|---|---|---|---|---|"]
    combos = [("A 기존", ["A"]), ("A+B 수급", ["A", "B"]), ("A+C 테마", ["A", "C"]), ("A+D 재무·지분", ["A", "D"]),
              ("A+E 전날 장중", ["A", "E"]), ("A+H 시초가 갭", ["A", "H"]), ("A+B+C+D+E (갭 제외)", ["A", "B", "C", "D", "E"]), ("전부", list(GROUPS))]
    imps = None
    for lab, gs in combos:
        feats = [f for gname in gs for f in GROUPS[gname]]
        r, imp = evaluate(d, feats)
        imps = imp if lab == "전부" else imps
        L.append(f"| {lab} | {r['auc']:.3f} | {r['p5']:.1%} | {r['p10']:.1%} | {r['p20']:.1%} | {r['oc10']*100:+.2f}% | {r['oc10_lu']*100:+.1f}% | {r['oc10_no']*100:+.1f}% |")
    L += ["", "## '전부' 모델 특징 중요도 상위 15 (gain 비중)", "", "| 특징 | 비중 |", "|---|---|"]
    for k, v in imps.sort_values(ascending=False).head(15).items():
        L.append(f"| {k} | {v:.1%} |")
    # 시초가 갭과 상한가 — 상한가 확률 높은 후보(전날 거래대금 3배+ 또는 변동성 5%+) 안에서
    c = va[(va.vratio >= 3) | (va.vol20 >= 0.05)]
    L += ["", "## 시초가 갭별 (후보 = 전날 거래대금 3배+ 또는 변동성 5%+, 검증 구간)", "", "| 갭 | 건 | 상한가 마감 | 시가→종가 평균(비용 전) |", "|---|---|---|---|"]
    for lab, m in (("≤ −3%", c.gap <= -0.03), ("−3 ~ 0%", c.gap.between(-0.03, 0, inclusive="right")), ("0 ~ +3%", c.gap.between(0, 0.03, inclusive="right")),
                   ("+3 ~ +10%", c.gap.between(0.03, 0.10, inclusive="right")), ("+10 ~ +20%", c.gap.between(0.10, 0.20, inclusive="right")), ("> +20%", c.gap > 0.20)):
        x = c[m]
        L.append(f"| {lab} | {len(x):,} | {x.lu.mean():.1%} | {x.oc.mean()*100:+.2f}% |")
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_clues.md"), "w", encoding="utf-8") as fh:
        fh.write(out + "\n")


if __name__ == "__main__":
    main()
