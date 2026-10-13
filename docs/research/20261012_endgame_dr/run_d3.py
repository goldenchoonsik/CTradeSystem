"""D3 — 배당락 (전 종목 일봉 2년 + 예탁원 배당 원본). PREREGISTRATION.md 그대로.

개발 구간 합격 규칙만 봉인 구간(2026-06-01~)을 1회 연다.

  .venv/bin/python docs/research/20261012_endgame_dr/run_d3.py
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "docs", "research", "20261011_vol_spike"))
from compare_algos import STAGE  # noqa: E402
import compare_algos  # noqa: E402

DEV_END, SEAL_FROM, SEAL_END = pd.Timestamp("2026-05-29"), pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-08")
RT, TAX = 0.0027, 0.154


def load_dividends():
    rows = []
    for f in glob.glob(os.path.join(REPO, "DataVault/raw/kis/ksdinfo/dividend/*.json")):
        j = json.load(open(f, encoding="utf-8"))
        for p in j["pages"]:
            rows += p["body"].get("output1", []) or []
    d = pd.DataFrame(rows)
    d = d[d.stk_kind == "보통"].copy()
    d["amt"] = pd.to_numeric(d.per_sto_divi_amt, errors="coerce").fillna(0)
    d["record"] = pd.to_datetime(d.record_date, format="%Y%m%d", errors="coerce")
    d = d.dropna(subset=["record"])
    d = d.groupby(["sht_cd", "record", "divi_kind"], as_index=False).amt.max()
    d = d.sort_values(["sht_cd", "divi_kind", "record"])
    d["prior_amt"] = d.groupby(["sht_cd", "divi_kind"]).amt.shift(1)
    return d


def main():
    cal = pd.read_parquet(os.path.join(REPO, "DataVault/normalized/candles_1d/069500_1d.parquet")).timestamp
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(cal).unique()))
    compare_algos.END = SEAL_END
    px = compare_algos.load_daily()
    px = px.sort_values(["code", "date"])
    px["val20"] = px.groupby("code").val.transform(lambda s: s.rolling(20, min_periods=15).mean())
    idx = {c: g.set_index("date") for c, g in px.groupby("code")}

    div = load_dividends()
    div = div[(div.amt > 0) & (div.record >= "2024-09-01") & (div.record <= SEAL_END + pd.Timedelta(days=5))]
    ev = []
    for r in div.itertuples():
        code = "A" + r.sht_cd
        g = idx.get(code)
        if g is None:
            continue
        pos = cal.searchsorted(r.record, side="right") - 1          # R' = 기준일 이하 마지막 거래일
        if pos < 12 or pos >= len(cal):
            continue
        ex, cum, pre = cal[pos - 1], cal[pos - 2], cal[pos - 12]
        post = cal[pos - 1 + 5] if pos - 1 + 5 < len(cal) else None
        if cum not in g.index or ex not in g.index or pre not in g.index:
            continue
        rc, rraw = g.loc[cum], g.loc[cum, "raw_close"]
        if not (rraw >= 1000 and rc.val20 >= 5e8) or pd.isna(r.prior_amt):
            continue
        y_exec, y_act = r.prior_amt / rraw, r.amt / rraw
        e = dict(code=code, ex=ex, kind=r.divi_kind, y_exec=y_exec, y_act=y_act,
                 a=g.loc[ex, "o"] / rc.c - 1 + y_act * (1 - TAX) - RT,
                 b=g.loc[ex, "c"] / rc.c - 1 + y_act * (1 - TAX) - RT,
                 c=rc.c / g.loc[pre, "c"] - 1 - RT,
                 d=(g.loc[post, "c"] / g.loc[ex, "c"] - 1 - RT) if post is not None and post <= SEAL_END and post in g.index else np.nan)
        if g.loc[ex, "v"] <= 0:
            continue
        ev.append(e)
    ev = pd.DataFrame(ev)
    ev.to_csv(os.path.join(os.path.dirname(__file__), "d3_events.csv"), index=False)

    lines = [f"# D3 배당락 결과 (이벤트 {len(ev)}건, 배당락일 {ev.ex.nunique()}일, 개발 ≤ {DEV_END:%Y-%m-%d})", "",
             "순수익 = 가격 + 세후 배당(a·b) − 0.27%. t = 배당락일별 동일가중 포트폴리오. 선택은 직전 같은 종류 배당 기준 수익률.", "",
             "| 규칙 | 문턱 | 개발 이벤트 | 개발 일수 | 평균(이벤트) | 평균(날짜) | t | 분기 양수 | 최근12M 합(날짜) | 봉인 | 판정 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    passed = 0
    for col, lab in [("a", "a 권리부 종가→락일 시가"), ("b", "b 권리부 종가→락일 종가"), ("c", "c 락 전 10일 상승"), ("d", "d 락 후 5일 회복")]:
        for th in (0.01, 0.03):
            x = ev[(ev.y_exec >= th)].dropna(subset=[col])
            dev = x[x.ex <= DEV_END]
            day = dev.groupby("ex")[col].mean()
            t = day.mean() / day.std() * np.sqrt(len(day)) if len(day) > 2 else np.nan
            q = day.groupby(day.index.to_period("Q")).mean()
            qpos = (q > 0).mean()
            last12 = day[day.index > DEV_END - pd.Timedelta(days=365)].sum()
            ok_dev = day.mean() > 0 and t > 3 and qpos >= 2 / 3 and last12 > 0
            seal_txt, ok = "열지 않음", False
            if ok_dev:
                s = x[(x.ex >= SEAL_FROM) & (x.ex <= SEAL_END)].groupby("ex")[col].mean()
                st = s.mean() / s.std() * np.sqrt(len(s)) if len(s) > 2 else np.nan
                ok = s.mean() > 0 and st > 2
                seal_txt = f"{s.mean()*100:+.3f}% t{st:+.2f} ({len(s)}일)"
            passed += ok
            lines.append(f"| {lab} | ≥{th:.0%} | {len(dev)} | {len(day)} | {dev[col].mean()*100:+.3f}% | {day.mean()*100:+.3f}% | {t:+.2f} | {qpos:.0%} | {last12*100:+.2f}% | {seal_txt} | {'합격' if ok else '불합격'} |")
    # 참고: 비용·세금 전 분해(규칙 a, 문턱 1%) — 배당 이론값 대비 하락폭
    x = ev[(ev.y_exec >= 0.01) & (ev.ex <= DEV_END)]
    drop = x.a - x.y_act * (1 - TAX) + RT
    lines += ["", f"합격 {passed} / 8", "",
              f"참고(a, 문턱 1%): 락일 시가 하락 평균 {drop.mean()*100:+.3f}% vs 실제 배당수익률 평균 {x.y_act.mean()*100:.3f}% "
              f"(하락/배당 = {(-drop.mean())/x.y_act.mean():.2f}), 세전 비용 전 포착 {(drop + x.y_act).mean()*100:+.3f}%"]
    out = "\n".join(lines)
    print(out)
    with open(os.path.join(os.path.dirname(__file__), "results_d3.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
