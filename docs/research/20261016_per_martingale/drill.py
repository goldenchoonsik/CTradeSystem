import sys, io, contextlib
import numpy as np, pandas as pd
src = open("/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale/mart.py", encoding="utf-8").read().split("bh = ret[")[0]
with contextlib.redirect_stderr(io.StringIO()):
    exec(compile(src, "mart_head", "exec"))
pd.set_option("display.width", 220)
flows, cycles, inv, pnl = simulate(buy_per=10, sell_per=20)
cy = pd.DataFrame(cycles); cy["open"] = cy.open.fillna(False).astype(bool); cy["name"] = cy.code.map(NAME)
cy["ret"] = cy.proceeds / cy.invested - 1; cy["gain"] = cy.proceeds - cy.invested
print("== 미청산 사이클 (기말 평가, 매도비용 반영)")
o = cy[cy.open][["name", "entry", "invested", "adds", "max_cost", "proceeds", "ret", "gain"]].sort_values("invested", ascending=False)
print(o.round(2).to_string(index=False))
print("미청산 합: 투입(누적매수) %.1f 평가 %.1f 손익 %.1f" % (o.invested.sum(), o.proceeds.sum(), o.gain.sum()))
d = cy[~cy.open]
print("완료 합: 매수 %.1f 매도 %.1f 손익 %.1f" % (d.invested.sum(), d.proceeds.sum(), d.gain.sum()))
print("\n== 손익 상위/하위 사이클 기여 (총손익 %.1f)" % cy.gain.sum())
print(cy.sort_values("gain", ascending=False).head(6)[["name", "entry", "exit", "invested", "adds", "ret", "gain", "open"]].round(2).to_string(index=False))
print(cy.sort_values("gain").head(4)[["name", "entry", "exit", "invested", "adds", "ret", "gain", "open"]].round(2).to_string(index=False))
print("\n== 연도별 투입자본(연말 원가)·평가손익 변화")
s = pd.DataFrame({"inv": inv, "pnl": pnl}, index=cal)
y = s.groupby(s.index.year).agg(inv_max=("inv", "max"), inv_end=("inv", "last"), pnl_end=("pnl", "last"))
print(y.round(1).T.to_string())
print("\n== 기간 분할 (진입일 기준)")
for lab, m in [("2016-06~2020-12", cy.entry < "2021-01-01"), ("2021-01~2026-09", cy.entry >= "2021-01-01")]:
    z = cy[m]; print(lab, "사이클 %d, 총손익 %.1f, 투입 %.1f, 미청산 %d" % (len(z), z.gain.sum(), z.invested.sum(), z.open.sum()))
# 포트폴리오 최악 구간: 투입 원가 대비 평가손익 비율의 최소와 날짜
r = (s.pnl / s.inv.replace(0, np.nan)); print("\n평가손익/투입 최악 %.0f%% (%s), 같은 날 투입 %.1f 평가손익 %.1f" % (r.min()*100, r.idxmin().date(), s.inv[r.idxmin()], s.pnl[r.idxmin()]))
print("최대 투입일:", s.inv.idxmax().date(), "투입 %.1f" % s.inv.max(), "그날 평가손익 %.1f" % s.pnl[s.inv.idxmax()])
