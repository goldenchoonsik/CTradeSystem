"""미국 Titan 스윙 정정 재계산 (2026-10-21)

원본: 20261021_us_titan_tax_drag_swing/backtest_us_titan_tax_drag.py (+54,942% 세전 / +36,767% 세후 보고)
원본 결함: ① 청산일 누적 수익률 이중 계산 ② 분할 보정이 분할 당일 가격까지 한 번 더 나눔(`<= d`) — 분할일마다 −90%/+900% 가짜 봉
           ③ 종목 명단 = 2024년 기준 초대형 승자 10개(사후 선정)
정정: 분할 보정 `< d`, swing_engine.py(체결 2방식: 종가 체결 / 시가 진입·장중 손절·익절), 편도 0.1% 비용, 미 국채 3개월 금리 현금이자,
      원화 환산 실현손익 연 합산·250만원 공제·22%를 다음 해 5월 인출, 마지막 날 잔여 포지션·2024년분까지 과세(보유 전략과 같은 조건).
사후 선정 점검: 같은 10종목을 2013년 초에 사서 그냥 보유한 결과, 그리고 2012년 말 시총 상위 10(당시 알 수 있던 명단, GE 는 분사 미보정이라 PG 로 대체)
실행: .venv/bin/python docs/research/20261021_slot_swing_recompute/recompute_us_titan.py
"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swing_engine import run, metrics, tstats, fmt

ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault"
TITANS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX']
CAP2012 = ['AAPL', 'XOM', 'GOOGL', 'WMT', 'MSFT', 'BRK-B', 'IBM', 'CVX', 'JNJ', 'PG']

def load(t):
    df = pd.read_csv(f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp")
    df = df[~df.index.duplicated()].sort_index()
    ca = pd.read_csv(f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv", parse_dates=["date"])
    ca = ca[ca.type == "split"].drop_duplicates("date")
    mult = pd.Series(1.0, index=df.index)
    for d, num, den in zip(ca.date, ca.numerator, ca.denominator):
        mult[df.index < d] *= num / den           # 분할 당일(권리락일)은 이미 분할 후 가격
    for k in ('open', 'high', 'low', 'close'):
        df[k] = df[k] / mult
    df['volume'] = df['volume'] * mult
    return df

qqq = load('QQQ')
cal = qqq.loc['2012-01-01':'2024-12-31'].index
START = int(np.searchsorted(cal, pd.Timestamp('2013-01-01')))
fx = pd.read_csv(f"{ROOT}/staging/fx_usdkrw/usdkrw_daily.csv", parse_dates=['date']).set_index('date')['usdkrw']
FX = fx.reindex(fx.index.union(cal)).ffill().reindex(cal).to_numpy()
TB = {2012: 0.09, 2013: 0.06, 2014: 0.03, 2015: 0.05, 2016: 0.32, 2017: 0.93, 2018: 1.94, 2019: 2.06,
      2020: 0.37, 2021: 0.05, 2022: 2.02, 2023: 5.07, 2024: 4.97}
RF = np.array([TB[d.year] / 100 / 252 for d in cal])
TAX = dict(rate=0.22, deduct=2.5e6, init_krw=1e8)

def build(tickers):
    data = {t: load(t).reindex(cal) for t in tickers}
    M = lambda k: np.column_stack([data[t][k].to_numpy(dtype=float) for t in tickers])
    C, O, H, L, V = M('close'), M('open'), M('high'), M('low'), M('volume')
    Cd = pd.DataFrame(C, index=cal); Vd = pd.DataFrame(V, index=cal)
    full = {t: load(t) for t in tickers}   # 지표는 전체 역사로 계산
    Cf = pd.DataFrame({t: full[t]['close'] for t in tickers}); Vf = pd.DataFrame({t: full[t]['volume'] for t in tickers})
    ma20 = Cf.rolling(20, min_periods=10).mean(); ma200 = Cf.rolling(200, min_periods=100).mean()
    d_ = Cf.diff(); up = d_.clip(lower=0); dn = -d_.clip(upper=0)
    rsi = 100 - 100 / (1 + up.ewm(com=13, adjust=False).mean() / dn.ewm(com=13, adjust=False).mean())
    vma = Vf.rolling(20, min_periods=10).mean()
    sig = (Cf / ma200 >= 0.90) & (((Cf / ma20 >= 0.96) & (Cf / ma20 <= 1.04)) | (rsi <= 42.0)) & (Vf >= vma * 0.8)
    SIG = sig.reindex(cal).fillna(False).to_numpy(dtype=bool); RSI = rsi.reindex(cal).to_numpy()
    jump = (Cd.pct_change().abs() > 0.4).to_numpy().sum()
    return C, O, H, L, SIG, RSI, jump

RULE = dict(tp1=.12, tp2=.25, sl=-.07, trail=-.05, hold=35)

def after_tax_terminal(eq_krw, info):
    """마지막 날 전량 청산 가정: 2024년 실현분 + 미실현분까지 과세한 원화 최종 자산(1억 시작 기준)."""
    g = info['realized'].get(2024, 0.0) + info['open_unrealized_krw']
    tax = max(0.0, g - TAX['deduct']) * TAX['rate']
    return eq_krw.iloc[-1] / eq_krw.iloc[START] * 1e8 - tax, info['tax_paid'] + tax

def bh_krw(C, cols):
    """2013년 첫날 동일금액 매수 후 보유(리밸런싱 없음) — 원화 평가, 끝에 한 번 과세."""
    w = np.nansum(C[:, cols] / C[START, cols], axis=1) / len(cols)
    eq = pd.Series(w * FX / FX[START], index=cal); eq.iloc[:START] = 1.0
    gain = (eq.iloc[-1] - 1) * 1e8
    return eq, 1e8 + gain - max(0.0, gain - TAX['deduct']) * TAX['rate']

if __name__ == '__main__':
    COST = dict(buy_cost=lambda i, j: 0.001, sell_cost=lambda i, j: 0.001, cash_rate=lambda i: RF[i])
    rows = []
    for label, tickers in [("Titan10(2024 승자, 사후 선정)", TITANS), ("2012말 시총 상위10(당시 명단)", CAP2012)]:
        C, O, H, L, SIG, RSI, jump = build(tickers)
        print(f"--- {label}: 일간 ±40% 초과 봉 {jump}개(분할 보정 점검)")
        cands = lambda i: sorted([j for j in range(len(tickers)) if SIG[i - 1, j] and not np.isnan(O[i, j])], key=lambda j: RSI[i - 1, j])
        nos = lambda i: [j for j in range(len(tickers)) if not np.isnan(O[i, j])]
        for nm_, cf in [("스윙", cands), ("대조: 신호 없음", nos)]:
          for mlab, mk in [("종가체결", dict(mode='close', entry='close')), ("장중체결", dict(mode='intraday', entry='open'))]:
            eq, t, info = run(cal, C, O, H, L, cf, START, **RULE, **COST, fx=FX, tax=TAX, names=tickers, order='tp1_first', **mk)
            eq_pre, t_pre, _ = run(cal, C, O, H, L, cf, START, **RULE, **COST, fx=FX, names=tickers, order='tp1_first', **mk)
            fin, taxsum = after_tax_terminal(eq, info)
            m = metrics(eq, START, RF); mp = metrics(eq_pre, START, RF)
            print(fmt(f"{label} {nm_} [{mlab}] 세전", mp, tstats(t_pre)))
            print(f"{'':<46} 세후 최종 {fin/1e8:6.2f}억 (총 세금 {taxsum/1e8:5.2f}억), 세후 MDD {m['mdd']*100:.1f}%")
            rows.append((label, nm_, mp['total'], mp['cagr'], fin))
        eqb, finb = bh_krw(C, list(range(len(tickers))))
        mb = metrics(eqb, START, RF)
        print(fmt(f"{label} 동일금액 매수·보유 세전", mb)); print(f"{'':<46} 세후 최종 {finb/1e8:6.2f}억")
    Cq, *_ = build(['QQQ'])
    eqq, finq = bh_krw(Cq, [0])
    print(fmt("QQQ 보유 세전(원화)", metrics(eqq, START, RF))); print(f"{'':<46} 세후 최종 {finq/1e8:6.2f}억")
