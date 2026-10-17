"""PER 밴드 + 하락 10% 마다 보유액만큼 추가 매수(물타기·배증) — 코스피 시점 상위 20 탐색 시험 (2026-10-16, 사용자 질문, 사전등록 아님).

데이터 준비·유니버스는 20261014_per_band/per_band.py 앞부분을 그대로 쓴다(PER=시총/직전 4분기 순이익, 접수 다음 날부터).
규칙: 평소 PER<=BUY 이고 미보유 → 1단위 매수. 보유 중 직전 매수가 대비 -10% 이하 → 현재 평가액만큼 추가 매수(배증).
      PER>=SELL → 전량 매도. 신호 t 종가 → t+1 종가 체결. 왕복 0.30%. 자본 한도 없음(필요 자본 최대치를 보고).
      유니버스 이탈·적자 전환 후에도 보유분은 계속 관리(신규 진입만 막음). 상장폐지 = 마지막 종가로 청산(정리매매 손실 미반영).
"""
import sys, io, contextlib
import numpy as np
import pandas as pd

src = open("/home/kuzimo/projects/CTradeSystem/docs/research/20261014_per_band/per_band.py", encoding="utf-8").read()
head = src.split("# ---------- 신호·성과")[0]
with contextlib.redirect_stderr(io.StringIO()) as err:
    exec(compile(head, "per_band_head", "exec"))
ELIG = top20_pit("KOSPI")
names = sorted({NAME.get(c, c) for c in ELIG.columns})
print(len(names), "종목:", ", ".join(names), file=sys.stderr)

PX = close.reindex(cal)
LAST_PX = PX.apply(lambda s: s.last_valid_index())
BUY_C, SELL_C = 0.0005, 0.0025

def xirr(flows):
    d0 = flows[0][0]
    t = np.array([(d - d0).days / 365.25 for d, _ in flows]); a = np.array([x for _, x in flows])
    lo, hi = -0.99, 10.0
    f = lambda r: (a / (1 + r) ** t).sum()
    if f(lo) * f(hi) > 0: return np.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0: hi = mid
        else: lo = mid
    return (lo + hi) / 2

def simulate(buy_per, sell_per, drop=0.10, max_adds=99, sell_on_loss=False, start_amt=1.0):
    cols = ELIG.columns
    P = PER[cols].values; LS = LOSS[cols].fillna(False).values; E = ELIG.values; px = PX[cols].values
    n, m = px.shape
    flows, cycles = [], []        # flows: (date, amount) 현금흐름(매수 -, 매도 +)
    invested_t = np.zeros(n)      # 시점별 보유 원가(순투입)
    pnl_unreal = np.zeros(n)
    for j in range(m):
        shares, last_buy, adds, cyc = 0.0, np.nan, 0, None
        cost_basis = 0.0
        pend = None   # ('buy', amt) / ('sell',)
        ended = LAST_PX[cols[j]]
        for i in range(n):
            p = px[i, j]
            if np.isnan(p):
                if shares > 0 and ended is not pd.NaT and cal[i] > ended:    # 상장폐지 후 → 마지막 종가로 청산
                    lp = PX[cols[j]].loc[ended]
                    proceeds = shares * lp * (1 - SELL_C); flows.append((cal[i], proceeds))
                    cyc.update(exit=ended, proceeds=proceeds, delisted=True); cycles.append(cyc); shares = 0; cost_basis = 0; pend = None; cyc = None
                continue
            # 전일 신호 체결
            if pend is not None:
                if pend[0] == "buy":
                    amt = pend[1] if pend[1] > 0 else shares * p
                    if amt > 0:
                        shares += amt * (1 - BUY_C) / p; flows.append((cal[i], -amt)); cost_basis += amt
                        last_buy = p
                        if cyc is None: cyc = dict(code=cols[j], entry=cal[i], invested=0.0, adds=0, max_cost=0.0, delisted=False)
                        else: cyc["adds"] += 1
                        cyc["invested"] += amt; cyc["max_cost"] = max(cyc["max_cost"], cost_basis)
                        adds = cyc["adds"]
                elif pend[0] == "sell" and shares > 0:
                    proceeds = shares * p * (1 - SELL_C); flows.append((cal[i], proceeds))
                    cyc.update(exit=cal[i], proceeds=proceeds); cycles.append(cyc)
                    shares, cost_basis, cyc, adds, last_buy = 0.0, 0.0, None, 0, np.nan
                pend = None
            invested_t[i] += cost_basis; pnl_unreal[i] += shares * p - cost_basis
            # 오늘 신호
            per, loss = P[i, j], LS[i, j]
            if shares == 0:
                if E[i, j] and not np.isnan(per) and per <= buy_per: pend = ("buy", start_amt)
            else:
                if (not np.isnan(per) and per >= sell_per) or (sell_on_loss and loss): pend = ("sell",)
                elif p <= last_buy * (1 - drop) and adds < max_adds and not (np.isnan(per) and loss): pend = ("buy", 0)
        if shares > 0:           # 기간 말 미청산 → 평가
            lp = px[~np.isnan(px[:, j]), j][-1]
            val = shares * lp * (1 - SELL_C); flows.append((cal[-1], val))
            cyc.update(exit=None, proceeds=val, open=True); cycles.append(cyc)
    return flows, cycles, invested_t, pnl_unreal

def report(label, **kw):
    flows, cycles, inv, pnl = simulate(**kw)
    flows.sort(key=lambda x: x[0])
    cy = pd.DataFrame(cycles)
    if cy.empty: print(label, "거래 없음"); return None
    cy["ret"] = cy.proceeds / cy.invested - 1
    cy["open"] = cy.get("open", False).fillna(False) if "open" in cy else False
    cy["days"] = (pd.to_datetime(cy.exit.fillna(cal[-1])) - cy.entry).dt.days
    done = cy[~cy.open.astype(bool)]
    peak_cap = inv.max(); avg_cap = inv.mean()
    total_pnl = sum(a for _, a in flows)
    yrs = (cal[-1] - cal[0]).days / 365.25
    print(f"\n[{label}] 사이클 {len(cy)} (완료 {len(done)}·미청산 {int(cy.open.sum())}·상장폐지 청산 {int(cy.get('delisted', pd.Series(False)).fillna(False).sum())})")
    print(f"  완료 사이클: 승률 {float((done.ret>0).mean()):.0%} 평균수익률 {done.ret.mean():+.1%} 중앙 {done.ret.median():+.1%} 최악 {done.ret.min():+.1%} 평균보유 {done.days.mean():.0f}일 평균 추가매수 {cy.adds.mean():.1f}회 최대 {int(cy.adds.max())}회")
    print(f"  총손익 {total_pnl:+.1f}단위 | 최대 투입자본 {peak_cap:.1f}단위(1단위=첫 매수액) | 평균 투입 {avg_cap:.1f} | 최대투입 대비 총수익률 {total_pnl/peak_cap:+.0%} ({total_pnl/peak_cap/yrs:+.1%}/년 단순)")
    print(f"  투입자본 기준 연환산 수익률(IRR) {xirr(flows):+.1%}")
    print(f"  최악 시점 평가손실 {pnl.min():+.1f}단위 ({pnl.min()/max(inv[pnl.argmin()],1e-9):+.0%} of 당시 투입)")
    return cy

bh = ret[ELIG.columns].where(ELIG.shift(0).fillna(False)).mean(axis=1).fillna(0)
eq = (1 + bh).cumprod(); print(f"비교: 같은 20종목 시점상위 동일가중 단순보유 CAGR {eq.iloc[-1] ** (252/len(bh)) - 1:+.1%}, MDD {(eq/eq.cummax()-1).min():.0%}")
print(f"기간 {cal[0].date()} ~ {cal[-1].date()}")
for b, s in [(8, 15), (10, 20), (12, 25), (15, 30)]:
    cy = report(f"PER≤{b} 매수 / ≥{s} 매도, 배증 무제한", buy_per=b, sell_per=s)
    if (b, s) == (10, 20):
        report(f"PER≤{b}/≥{s}, 추가매수 최대 3회", buy_per=b, sell_per=s, max_adds=3)
        report(f"PER≤{b}/≥{s}, 추가매수 없음(비교)", buy_per=b, sell_per=s, max_adds=0)
        report(f"PER≤{b}/≥{s}, 적자 전환 시 손절", buy_per=b, sell_per=s, sell_on_loss=True)
        if cy is not None:
            cy.to_csv("/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale/cycles_10_20.csv", index=False)
            t = cy.sort_values("ret").head(5)[["code", "entry", "exit", "invested", "adds", "ret", "max_cost"]]
            t["name"] = t.code.map(NAME); print(t.to_string())
