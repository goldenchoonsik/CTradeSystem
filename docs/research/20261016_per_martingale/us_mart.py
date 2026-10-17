"""PER 밴드 + 10% 하락마다 보유액만큼 추가 매수(배증) — 나스닥 대형주 후보군 시점 상위 20 (2026-10-16, 탐색 시험, 사전등록 아님).
us_panel.py 로 만든 PER(가격/직전 4분기 희석 EPS, 공시 다음 날부터)·시총(가격×희석 주식 수)을 쓴다. 규칙·체결은 한국 시험(mart.py)과 같다.
가격 수익만(배당·세금·환율 제외), 왕복 0.10%(편도 0.05%). 후보군 60종목은 사후에 고른 현재 상장 종목(상폐·피인수 종목 없음) → 생존·선택 편향."""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u

cal_all, PRC, EPS, SHR, RAW, bad = u.load_all()
START, END = pd.Timestamp("2016-06-01"), pd.Timestamp("2026-10-14")
cal = PRC.index[(PRC.index >= START) & (PRC.index <= END) & PRC.notna().any(axis=1)]
PRC, EPS, SHR = PRC.reindex(cal), EPS.reindex(cal), SHR.reindex(cal)
MCAP = PRC * SHR
PER = PRC / EPS.where(EPS > 0)
LOSS = (EPS <= 0)
ELIG = pd.DataFrame(False, index=cal, columns=PRC.columns)
for y in sorted(set(cal.year)):
    days = cal[cal.year == y]; first = MCAP.loc[days[0]].dropna()
    ELIG.loc[days, first.nlargest(20).index] = True
BUY_C = SELL_C = 0.0005
ret = PRC.pct_change(fill_method=None)
LAST_PX = PRC.apply(lambda s: s.last_valid_index())

def xirr(flows):
    d0 = flows[0][0]; t = np.array([(d - d0).days / 365.25 for d, _ in flows]); a = np.array([x for _, x in flows])
    lo, hi = -0.99, 10.0; f = lambda r: (a / (1 + r) ** t).sum()
    if f(lo) * f(hi) > 0: return np.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0: hi = mid
        else: lo = mid
    return (lo + hi) / 2

def simulate(buy_per, sell_per, drop=0.10, max_adds=99, sell_on_loss=False, start_amt=1.0):
    cols = ELIG.columns[ELIG.any()]
    Pm = PER[cols].values; LS = LOSS[cols].fillna(False).values; E = ELIG[cols].values; px = PRC[cols].values
    n, m = px.shape; flows, cycles = [], []
    invested_t = np.zeros(n); pnl_unreal = np.zeros(n)
    for j in range(m):
        shares, last_buy, adds, cyc, cost_basis, pend = 0.0, np.nan, 0, None, 0.0, None
        for i in range(n):
            p = px[i, j]
            if np.isnan(p): continue
            if pend is not None:
                if pend[0] == "buy":
                    amt = pend[1] if pend[1] > 0 else shares * p
                    if amt > 0:
                        shares += amt * (1 - BUY_C) / p; flows.append((cal[i], -amt)); cost_basis += amt; last_buy = p
                        if cyc is None: cyc = dict(code=cols[j], entry=cal[i], invested=0.0, adds=0, max_cost=0.0)
                        else: cyc["adds"] += 1
                        cyc["invested"] += amt; cyc["max_cost"] = max(cyc["max_cost"], cost_basis); adds = cyc["adds"]
                elif pend[0] == "sell" and shares > 0:
                    pr = shares * p * (1 - SELL_C); flows.append((cal[i], pr)); cyc.update(exit=cal[i], proceeds=pr, open=False); cycles.append(cyc)
                    shares, cost_basis, cyc, adds, last_buy = 0.0, 0.0, None, 0, np.nan
                pend = None
            invested_t[i] += cost_basis; pnl_unreal[i] += shares * p - cost_basis
            per, loss = Pm[i, j], LS[i, j]
            if shares == 0:
                if E[i, j] and not np.isnan(per) and per <= buy_per: pend = ("buy", start_amt)
            else:
                if (not np.isnan(per) and per >= sell_per) or (sell_on_loss and loss): pend = ("sell",)
                elif p <= last_buy * (1 - drop) and adds < max_adds and not (np.isnan(per) and loss): pend = ("buy", 0)
        if shares > 0:
            lp = px[~np.isnan(px[:, j]), j][-1]; val = shares * lp * (1 - SELL_C); flows.append((cal[-1], val))
            cyc.update(exit=None, proceeds=val, open=True); cycles.append(cyc)
    return flows, cycles, invested_t, pnl_unreal

def report(label, **kw):
    flows, cycles, inv, pnl = simulate(**kw); flows.sort(key=lambda x: x[0])
    cy = pd.DataFrame(cycles)
    if cy.empty: print(f"\n[{label}] 거래 없음"); return None
    cy["ret"] = cy.proceeds / cy.invested - 1; cy["days"] = (pd.to_datetime(cy.exit.fillna(cal[-1])) - cy.entry).dt.days
    done = cy[~cy.open]; yrs = (cal[-1] - cal[0]).days / 365.25; tot = sum(a for _, a in flows); pk = inv.max()
    op = cy[cy.open]
    print(f"\n[{label}] 사이클 {len(cy)} (완료 {len(done)}·미청산 {len(op)})")
    if len(done): print(f"  완료: 승률 {float((done.ret>0).mean()):.0%} 평균 {done.ret.mean():+.1%} 중앙 {done.ret.median():+.1%} 최악 {done.ret.min():+.1%} 평균보유 {done.days.mean():.0f}일")
    print(f"  미청산 {len(op)}개: 누적매수 {op.invested.sum():.1f} 평가 {op.proceeds.sum():.1f} 손익 {op.proceeds.sum()-op.invested.sum():+.1f} (손실 {int((op.ret<0).sum())}개)")
    print(f"  추가매수 평균 {cy.adds.mean():.1f}회 최대 {int(cy.adds.max())}회 | 총손익 {tot:+.1f} | 최대 투입 {pk:.1f} 평균 {inv.mean():.1f} | 최대투입 대비 {tot/pk:+.0%} ({tot/pk/yrs:+.1%}/년) | IRR {xirr(flows):+.1%}")
    print(f"  최악 평가손익 {pnl.min():+.1f} ({pnl.min()/max(inv[pnl.argmin()],1e-9):+.0%}, {cal[pnl.argmin()].date()})")
    return cy

if __name__ == "__main__":
    print("제외:", [b[0] for b in bad], "| 후보", PRC.shape[1], "| 기간", cal[0].date(), "~", cal[-1].date())
    pe = PER.where(ELIG).stack(); print("유니버스 내 PER 분위 10/25/50/75/90%:", [round(x, 1) for x in pe.quantile([.1, .25, .5, .75, .9])])
    bh = ret.where(ELIG & ret.notna()).mean(axis=1).fillna(0); eq = (1 + bh).cumprod()
    q = pd.read_csv("/home/kuzimo/projects/CTradeSystem/DataVault/raw/yahoo/candle_1d/QQQ_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close.reindex(cal).ffill()
    print(f"비교: 같은 20종목 동일가중 단순보유 CAGR {eq.iloc[-1]**(252/len(bh))-1:+.1%} MDD {(eq/eq.cummax()-1).min():.0%} | QQQ 가격 CAGR {(q.iloc[-1]/q.iloc[0])**(365.25/((cal[-1]-cal[0]).days))-1:+.1%}")
    for b, s in [(15, 30), (20, 35), (25, 45), (30, 60)]:
        cy = report(f"PER≤{b} 매수 / ≥{s} 매도, 배증 무제한", buy_per=b, sell_per=s)
        report(f"  └ 추가매수 최대 3회", buy_per=b, sell_per=s, max_adds=3)
        report(f"  └ 추가매수 없음", buy_per=b, sell_per=s, max_adds=0)
