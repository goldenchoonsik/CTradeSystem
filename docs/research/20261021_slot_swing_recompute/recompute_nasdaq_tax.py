"""나스닥 시총 가속도 TOP10 · 슈퍼루키-3 — 양도세 적용/비적용 비교 (2026-10-21)

조건: us_tax_book.Book (원화 1억 시작, 편도 0.1%, 연 250만원 공제 후 22% 다음 해 5월 납부, 현금이자 15.4% 원천징수,
      마지막 날 전량 청산 가정 정산). 세전 = 같은 계좌에서 세금만 뺀 것. QQQ 는 사서 보유, 마지막에 한 번 과세.
전략 정의·결함 정정은 recompute_nasdaq.py / recompute_nasdaq_pit.py 와 같다.
실행: .venv/bin/python docs/research/20261021_slot_swing_recompute/recompute_nasdaq_tax.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import recompute_nasdaq as nd
import recompute_nasdaq_pit as pit
from us_tax_book import Book
from us_data import ROOT

cal, RF, T = nd.cal, nd.RF, len(nd.cal)
fx = pd.read_csv(f"{ROOT}/staging/fx_usdkrw/usdkrw_daily.csv", parse_dates=['date']).set_index('date')['usdkrw']
FX = fx.reindex(fx.index.union(cal)).ffill().reindex(cal).to_numpy()

def idx(d): return int(np.searchsorted(cal, pd.Timestamp(d)))

def summarize(eq, book, i_end):
    e = eq.dropna(); e = e / e.iloc[0]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    fin, taxsum = book.final_after_tax(i_end)
    return dict(final=fin, cagr=fin ** (1 / yrs) - 1, mdd=(e / e.cummax() - 1).min(), tax=taxsum)

def run_qqq(start, tax):
    j = nd.ci['QQQ']; i0 = idx(start)
    b = Book(cal, FX, RF, nd.last_px, i0, tax=tax); eq = pd.Series(np.nan, index=cal)
    b.set_targets(i0, {j: 1.0 * (1 - 1e-9)}, {j: nd.C[i0, j]})
    for i in range(i0, T):
        if i > i0: b.start_day(i)
        eq.iloc[i] = b.equity_krw(i)
    return summarize(eq, b, T - 1)

def run_monthly(select, Cm, Om, lp, start, tax):
    i0 = idx(start)
    b = Book(cal, FX, RF, lp, i0, tax=tax); eq = pd.Series(np.nan, index=cal)
    for i in range(i0, T):
        if i > i0: b.start_day(i)
        if i in nd.mset or i == i0:
            tgt = select(i)
            prices = {j: (Om[i, j] if not np.isnan(Om[i, j]) else lp(i - 1, j)) for j in set(b.sh) | set(tgt)}
            v = b.value(i, prices) * (1 - nd.COST)
            b.set_targets(i, {j: v / len(tgt) for j in tgt} if tgt else {}, prices)
        eq.iloc[i] = b.equity_krw(i)
    return summarize(eq, b, T - 1)

def run_rookie(fill, mode, start, tax, n=3, stop=-0.15, tenure=912):
    C, ci, lp = nd.C, nd.ci, nd.last_px
    ev, init = nd.build_events(mode)
    rem = {t: d for d, t in nd.OFFICIAL_REMOVALS}
    remw = {}
    for d, t in nd.WIKI_REM: remw.setdefault(t, []).append(d)
    i0 = idx(start); fj = ci.get(fill)
    b = Book(cal, FX, RF, lp, i0, tax=tax); eq = pd.Series(np.nan, index=cal)
    hold = {}

    def removed(s, dt):
        if mode.startswith('wiki'):
            return any(hold[s]['d'] < r <= dt for r in remw.get(s, []))
        return not dt < rem.get(s, pd.Timestamp('2099-12-31'))

    def rebalance(i, keep):
        js = {ci[s] for s in keep} | set(b.sh)
        if fj is not None: js.add(fj)
        prices = {j: lp(i, j) for j in js}
        v = b.value(i, prices) * (1 - nd.COST)
        tgt = {ci[s]: v / n for s in keep}
        if fj is not None and len(keep) < n:
            tgt[fj] = tgt.get(fj, 0.0) + v * (n - len(keep)) / n
        for s in keep:
            if s not in hold: hold[s] = dict(e=lp(i, ci[s]), d=cal[i])
        for s in list(hold):
            if s not in keep: del hold[s]
        b.set_targets(i, tgt, prices)

    for i in range(i0, T):
        if i > i0: b.start_day(i)
        if i == i0:
            rebalance(i, [s for s in init if not np.isnan(C[i, ci[s]])][:n] if mode == 'orig' else [])
        else:
            for s in [s for s, h in hold.items() if lp(i, ci[s]) / h['e'] - 1 <= stop]:
                j = ci[s]; got = b.sell(j, b.sh.get(j, 0.0), lp(i, j), i); del hold[s]
                if fj is not None: b.buy(fj, got, lp(i, fj), i)
            if i in ev:
                dt = cal[i]
                surv = [s for s in hold if not removed(s, dt) and (dt - hold[s]['d']).days < tenure]
                new = [s for s in ev[i] if s not in hold and not np.isnan(C[i, ci[s]])]
                free = n - len(surv)
                if len(new) > free and surv:
                    k = min(len(surv), len(new) - free)
                    worst = sorted(surv, key=lambda s: lp(i, ci[s]) / hold[s]['e'])[:k]
                    surv = [s for s in surv if s not in worst]
                rebalance(i, surv + new[:n - len(surv)])
        eq.iloc[i] = b.equity_krw(i)
    return summarize(eq, b, T - 1)

def line(name, f):
    a, t = f(False), f(True)
    print(f"{name:<40} 세전 {a['final']:8.2f}억 (연 {a['cagr']*100:+5.1f}%) | 세후 {t['final']:8.2f}억 (연 {t['cagr']*100:+5.1f}%) "
          f"세금 {t['tax']:7.2f}억 | MDD 세전 {a['mdd']*100:5.1f}% 세후 {t['mdd']*100:5.1f}%", flush=True)

if __name__ == '__main__':
    for start in ('1999-03-10', '2015-01-02'):
        print(f"=== 시작 {start} ~ 2024-12-31, 원화 1억 ===")
        line("QQQ 보유", lambda tx: run_qqq(start, tx))
        line("가속도 TOP10 · 원본 명단(사후 선정)", lambda tx: run_monthly(nd.sel_acc, nd.C, nd.O, nd.last_px, start, tx))
        line("가속도 TOP10 · 당시 명단(생존 편향 있음)", lambda tx: run_monthly(pit.sel_acc, pit.C, pit.O, pit.lp, start, tx))
        if start.startswith('1999'):
            for fill in ('cash', 'AZO', 'QQQ'):
                line(f"슈퍼루키-3 · 원본 이벤트 · 빈 슬롯 {fill}", lambda tx: run_rookie(fill, 'orig', start, tx))
    start = '2016-01-04'
    print(f"=== 시작 {start} ~ 2024-12-31, 원화 1억 ===")
    line("QQQ 보유", lambda tx: run_qqq(start, tx))
    line("슈퍼루키-3 · 원본 편입 목록 · 빈 슬롯 QQQ", lambda tx: run_rookie('QQQ', 'official', start, tx))
    line("슈퍼루키-3 · 실제 편입 이력 · 빈 슬롯 현금", lambda tx: run_rookie('cash', 'wiki', start, tx))
    line("슈퍼루키-3 · 실제 편입 이력 · 빈 슬롯 QQQ", lambda tx: run_rookie('QQQ', 'wiki', start, tx))
