"""코스피50 재무 지표 상위10 — 매매 기록(편입·편출일, 보유 기간, 구간 수익).

실행: .venv/bin/python -I docs/research/20261021_kospi50_fundamental/trade_log.py
출력: trades.csv, trade_log.md
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kospi50_test as kt  # noqa: E402

trades = [(d, kt.next_tday(d)) for d in kt.sig_dates]
trades = [(d, t) for d, t in trades if t is not None]
snaps = {d: kt.signals_at(d) for d, _ in trades}
tdays = [t for _, t in trades]
last_exit = tdays[-1]          # 마지막 신호의 다음 체결일 = 시험 끝(보유 중이면 여기서 평가)

rows = []
for g in kt.SIGS[:5]:
    held = {}  # code → 편입 체결일
    for i, (d, t) in enumerate(trades[:-1]):
        v = snaps[d][g].dropna()
        top = set(v.nlargest(kt.N_TOP).index) if len(v) >= 2 * kt.N_TOP else set()
        for c in list(held):
            if c not in top:
                rows.append((g, c, held.pop(c), t))
        for c in top:
            held.setdefault(c, t)
    for c, t0 in held.items():
        rows.append((g, c, t0, None))
T = pd.DataFrame(rows, columns=["signal", "code", "entry", "exit"])
T["open"] = T.exit.isna()
T["exit_eval"] = T.exit.fillna(last_exit)
T["name"] = T.code.map(kt.names)
T["months"] = [sum(1 for x in tdays if e <= x < x2) for e, x2 in zip(T.entry, T.exit_eval)]
T["days"] = (T.exit_eval - T.entry).dt.days
T["ret"] = [kt.cf.at[x2, c] / kt.cf.at[e, c] - 1 for c, e, x2 in zip(T.code, T.entry, T.exit_eval)]
T.to_csv(os.path.join(kt.HERE, "trades.csv"), index=False)

L = ["# 코스피50 재무 지표 상위10 — 매매 기록", "",
     f"규칙: 매달 마지막 거래일 신호 → 다음 거래일 종가 체결. 상위10 에 새로 들면 매수, 빠지면 매도. 남아 있는 종목은 매달 10% 비중으로 맞춤(소량 매매). "
     f"기간 {tdays[0].date()} ~ {last_exit.date()}.", "",
     "| 신호 | 매수 건수 | 연 매수 | 보유 개월 중앙 | 평균 | 1개월만 보유 비율 | 12개월 이상 비율 | 최장 | 구간 승률 | 구간 수익 중앙 |",
     "|---|---|---|---|---|---|---|---|---|---|"]
yrs = (last_exit - tdays[0]).days / 365.25
for g, x in T.groupby("signal", sort=False):
    lg = x.loc[x.months.idxmax()]
    L.append(f"| {g} | {len(x)} | {len(x) / yrs:.1f} | {x.months.median():.0f} | {x.months.mean():.1f} | {(x.months == 1).mean():.0%} | "
             f"{(x.months >= 12).mean():.0%} | {lg['name']} {int(lg.months)}개월 | {(x.ret > 0).mean():.0%} | {x.ret.median():+.1%} |")

g = "S2 E/P"
x = T[T.signal == g].sort_values("entry")
L += ["", f"## {g}(저PER) 상세", "", "### 오래 들고 간 종목 (보유 개월 상위 12)", "",
      "| 종목 | 편입 | 편출 | 개월 | 구간 수익 |", "|---|---|---|---|---|"]
for _, r in x.nlargest(12, "months").iterrows():
    L.append(f"| {r['name']} | {r.entry.date()} | {'보유 중' if r.open else r.exit.date()} | {int(r.months)} | {r.ret:+.0%} |")
L += ["", "### 연도별 매매", "", "| 연도 | 매수 | 매도 | 연말 보유 종목 |", "|---|---|---|---|"]
for y in range(tdays[0].year, last_exit.year + 1):
    b = x[x.entry.dt.year == y]
    s = x[(x.exit.dt.year == y)]
    ye = min(pd.Timestamp(f"{y}-12-31"), last_exit - pd.Timedelta(days=1))
    hold = x[(x.entry <= ye) & (x.exit_eval > ye)]
    L.append(f"| {y} | {len(b)} | {len(s)} | {', '.join(hold.name.tolist())} |")
L += ["", "### 최근 12개월 편입·편출", ""]
recent = x[(x.entry >= last_exit - pd.Timedelta(days=365)) | (x.exit >= last_exit - pd.Timedelta(days=365))]
for _, r in recent.sort_values("entry").iterrows():
    L.append(f"- {r['name']}: {r.entry.date()} 매수 → {'보유 중' if r.open else str(r.exit.date()) + ' 매도'} ({int(r.months)}개월, {r.ret:+.0%})")
with open(os.path.join(kt.HERE, "trade_log.md"), "w") as fh:
    fh.write("\n".join(L) + "\n")
print("\n".join(L))
