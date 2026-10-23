"""슬롯 스윙 정정 엔진 (2026-10-21)

10-21 슬롯 스윙 스크립트들은 청산일에 진입 이후 누적 수익률을 한 번 더 더했다(이중 계산).
이 엔진은 슬롯마다 실제 보유 수량과 현금을 따로 들고 매일 종가로 평가한다.

- 진입: 전일 종가 신호 → 오늘 시가 매수(현실) 또는 오늘 종가 매수(원본 판정 재현용)
- 청산(intraday): 손절은 장중 지정가, 시가가 이미 손절선 밑이면 시가 체결(갭). 같은 날 손절·익절이 다 닿으면 손절 우선(보수적)
                  1차/2차 익절은 장중 지정가(갭 상승이면 시가). 트레일링·만기는 종가 판정·종가 체결
- 청산(close): 원본과 같은 판정 순서, 체결은 실제 종가
- 세금(선택): 미국 주식 양도세 — 원화 환산 실현손익을 연 단위로 합산, 250만원 공제 후 22%를 다음 해 5월 마지막 거래일에 인출
"""
import numpy as np
import pandas as pd


def run(cal, C, O, H, L, cands, start, *, tp1, tp2, sl, trail, hold, buy_cost, sell_cost, cash_rate,
        mode='intraday', entry='open', nslot=5, monthly=None, fx=None, tax=None, names=None, order='tp1_first'):
    """cands(i) -> 오늘 진입 후보 열 번호 목록(우선순위 순, 전일 정보만 사용).
    buy_cost(i, j)/sell_cost(i, j) -> 비율. cash_rate(i) -> 일 이자율.
    fx: 원화 환율 배열(미국 세금용), tax: dict(rate=0.22, deduct=2.5e6, init_krw=1e8)."""
    T = len(cal)
    sl_ = [dict(cash=1.0 / nslot, sh=0.0, t=None) for _ in range(nslot)]
    eq = np.full(T, np.nan); eq[:start] = 1.0
    trades = []
    realized = {}          # 연도 → 원화 실현손익
    may_last = set()
    if tax is not None:
        for y in sorted(set(cal.year)):
            ix = [k for k in range(T) if cal[k].year == y and cal[k].month == 5]
            if ix: may_last.add(ix[-1])
    unit_usd = (tax or {}).get('init_krw', 1e8) / (fx[start] if fx is not None else 1.0)  # 계좌 1.0 단위 = 몇 달러(원화 1억 기준)
    tax_paid = 0.0
    valid = ~np.isnan(C)
    lastv = np.where(valid.any(0), T - 1 - np.argmax(valid[::-1], axis=0), -1)   # 열별 마지막 거래일

    def value(s, i):
        v = s['cash']
        if s['t'] is not None:
            k = i
            while np.isnan(C[k, s['t']]): k -= 1
            v += s['sh'] * C[k, s['t']]
        return v

    for i in range(start, T):
        # 0) 양도세 인출
        if tax is not None and i in may_last:
            g = realized.get(cal[i].year - 1, 0.0)
            if g > tax['deduct']:
                t_krw = (g - tax['deduct']) * tax['rate']; tax_paid += t_krw
                t_acc = t_krw / fx[i] / unit_usd
                tot = sum(value(s, i - 1) for s in sl_)
                for s in sl_:
                    share = t_acc * value(s, i - 1) / tot
                    take = min(share, s['cash']); s['cash'] -= take; rest = share - take
                    if rest > 0 and s['t'] is not None:
                        pv = s['sh'] * C[i - 1, s['t']]
                        s['sh'] *= max(0.0, 1 - rest / pv) if pv > 0 else 0.0
        # 1) 진입
        empty = [s for s in sl_ if s['t'] is None]
        if empty:
            held = {s['t'] for s in sl_ if s['t'] is not None}
            picks = [j for j in cands(i) if j not in held]
            for s, j in zip(empty, picks):
                p = O[i, j] if entry == 'open' else C[i, j]
                if np.isnan(p) or p <= 0:
                    continue
                bc = buy_cost(i, j)
                s.update(t=j, e=p, pk=p, d=0, half=False, cin=s['cash'], i0=i,
                         basis=s['cash'], fx0=(fx[i] if fx is not None else 1.0))
                s['sh'] = s['cash'] * (1 - bc) / p; s['cash'] = 0.0
        # 2) 보유 관리
        for s in sl_:
            s['cash'] *= 1 + cash_rate(i)
            if s['t'] is None:
                continue
            j = s['t']; o, h, l, c = O[i, j], H[i, j], L[i, j], C[i, j]
            if np.isnan(c) and i > lastv[j]:   # 상장폐지: 마지막 거래가(정리매매 포함)로 청산
                px_last = C[lastv[j], j]; q = s['sh']; proceeds = q * px_last * (1 - sell_cost(lastv[j], j))
                if tax is not None:
                    realized[cal[i].year] = realized.get(cal[i].year, 0.0) + (proceeds * fx[i] - s['basis'] * s['fx0']) * unit_usd
                s['cash'] += proceeds; s['sh'] = 0.0
                trades.append(dict(entry=cal[s['i0']], exit=cal[lastv[j]], j=j, name=(names[j] if names is not None else j),
                                   ret=s['cash'] / s['cin'] - 1, days=s['d'], why='DELIST'))
                s['t'] = None
                continue
            if np.isnan(c) or s['i0'] == i:
                continue
            if np.isnan(o): o = c
            if np.isnan(h): h = max(o, c)
            if np.isnan(l): l = min(o, c)
            s['d'] += 1
            e = s['e']; why = None; sc = sell_cost(i, j)

            def sell(frac, price):
                q = s['sh'] * frac
                proceeds = q * price * (1 - sc)
                if tax is not None:
                    b = s['basis'] * frac; s['basis'] -= b
                    gk = (proceeds * fx[i] - b * s['fx0']) * unit_usd
                    realized[cal[i].year] = realized.get(cal[i].year, 0.0) + gk
                s['cash'] += proceeds; s['sh'] -= q

            if mode == 'intraday':
                stop, t1, t2 = e * (1 + sl), e * (1 + tp1), e * (1 + tp2)
                if o <= stop: sell(1, o); why = 'SL갭'
                elif l <= stop: sell(1, stop); why = 'SL'
                else:
                    if not s['half'] and h >= t1:
                        sell(0.5, max(o, t1)); s['half'] = True
                    if h >= t2:
                        sell(1, max(o, t2)); why = 'TP2'
                if why is None:
                    s['pk'] = max(s['pk'], c)
                    if c / e - 1 >= tp1 and c / s['pk'] - 1 <= trail: sell(1, c); why = 'TRAIL'
                    elif s['d'] >= hold: sell(1, c); why = 'TIME'
            else:
                s['pk'] = max(s['pk'], c); g = c / e - 1; dd = c / s['pk'] - 1
                if g >= tp2: sell(1, c); why = 'TP2'
                elif order == 'tp1_first' and g >= tp1 and not s['half']:
                    sell(0.5, c); s['half'] = True
                elif g >= tp1 and dd <= trail: sell(1, c); why = 'TRAIL'
                elif g <= sl: sell(1, c); why = 'SL'
                elif s['d'] >= hold: sell(1, c); why = 'TIME'
                elif order == 'trail_first' and g >= tp1 and not s['half']:
                    sell(0.5, c); s['half'] = True
            if why:
                trades.append(dict(entry=cal[s['i0']], exit=cal[i], j=j, name=(names[j] if names is not None else j),
                                   ret=s['cash'] / s['cin'] - 1, days=s['d'], why=why))
                s['t'] = None; s['sh'] = 0.0
        eq[i] = sum(value(s, i) for s in sl_)
    out = pd.Series(eq, index=cal)
    if fx is not None:
        out = out * fx / fx[start]          # 원화 평가
    unreal = 0.0
    if tax is not None:                     # 마지막 날 잔여 포지션의 원화 미실현손익(청산 가정)
        for s in sl_:
            if s['t'] is not None:
                v = s['sh'] * C[lastv[s['t']], s['t']] * (1 - sell_cost(T - 1, s['t']))
                unreal += (v * fx[T - 1] - s['basis'] * s['fx0']) * unit_usd
    return out, pd.DataFrame(trades), dict(tax_paid=tax_paid, realized=realized, open_unrealized_krw=unreal)


def metrics(eq, start, rf_daily):
    e = eq.iloc[start:] / eq.iloc[start]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    rf = np.asarray(rf_daily)[start + 1:start + 1 + len(r)]
    return dict(total=e.iloc[-1] - 1, cagr=e.iloc[-1] ** (1 / yrs) - 1, mdd=(e / e.cummax() - 1).min(),
                sharpe=(r.values - rf).mean() / r.std() * np.sqrt(252))


def tstats(t):
    if t is None or len(t) == 0:
        return dict(n=0, win=np.nan, pf=np.nan, avg=np.nan, tstat=np.nan)
    w = t[t.ret > 0].ret; l = t[t.ret <= 0].ret
    return dict(n=len(t), win=len(w) / len(t), pf=w.sum() / -l.sum() if len(l) else np.inf,
                avg=t.ret.mean(), tstat=t.ret.mean() / t.ret.std() * np.sqrt(len(t)))


def fmt(name, m, ts=None):
    s = f"{name:<46} 누적 {m['total']*100:+9.1f}%  CAGR {m['cagr']*100:+6.2f}%  MDD {m['mdd']*100:6.1f}%  샤프 {m['sharpe']:5.2f}"
    if ts and ts.get('n'):
        s += f" | 매매 {ts['n']:4d} 승률 {ts['win']*100:4.1f}% PF {ts['pf']:4.2f} 평균 {ts['avg']*100:+5.2f}% t {ts['tstat']:4.1f}"
    return s


def bh_ew(cal, C, members, start, rebalance_days=None):
    """members(i) -> 보유 열 목록. rebalance_days 가 None 이면 매일 동일가중(월초 구성 변경)."""
    T = len(cal); eq = np.full(T, np.nan); eq[:start] = 1.0
    for i in range(start, T):
        m = members(i)
        rr = [C[i, j] / C[i - 1, j] - 1 for j in m if not np.isnan(C[i, j]) and not np.isnan(C[i - 1, j])]
        eq[i] = eq[i - 1] * (1 + (np.mean(rr) if rr else 0.0))
    return pd.Series(eq, index=cal)
