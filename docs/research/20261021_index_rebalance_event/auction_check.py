"""편입일(R) 동시호가 실측 — 패시브가 정말 '비싸게' 사 주는가 (2026-10-21)

1분봉(vol_spike_universe staging, 2024-09-12~)에서 15:20 봉 종가 = 마지막 접속매매가, 15:30 봉 = 동시호가 체결가.
  jump      = 15:30 체결가 / 15:20 종가 − 1           (편입일 종가에 판 사람이 장중보다 더 받은 몫)
  jump_ex   = jump − 같은 종목 직전 20거래일 jump 중앙값 (평소 동시호가 버릇 제거)
  auc_share = 동시호가 거래대금 / 하루 정규장 거래대금, 평소(직전 20일 중앙값) 대비 배수
  next_open = 다음 날 시가 / R 동시호가가 − 1 (event_returns.csv 의 C1 과 같은 성격, 비교용)
"""
import os
import numpy as np
import pandas as pd

ROOT = '/home/kuzimo/projects/CTradeSystem'
HERE = f'{ROOT}/docs/research/20261021_index_rebalance_event'
M1 = f'{ROOT}/DataVault/staging/vol_spike_universe/raw/creon/candle_1m'
FIRST = pd.Timestamp('2024-09-12')


def day_stats(code, day):
    f = f'{M1}/{code}/{day:%Y-%m-%d}.csv'
    if not os.path.exists(f): return None
    b = pd.read_csv(f, usecols=['timestamp', 'upbit_close', 'upbit_trade_price'])
    t = b.timestamp.str[11:16]
    cont = b[(t > '09:00') & (t <= '15:20')]
    auc = b[t == '15:30']
    if cont.empty or auc.empty: return None
    reg_value = b[(t > '09:00') & (t <= '15:30')].upbit_trade_price.sum()
    return dict(last_cont=cont.upbit_close.iloc[-1], auc=auc.upbit_close.iloc[-1],
                auc_share=auc.upbit_trade_price.iloc[-1] / reg_value if reg_value > 0 else np.nan)


def main():
    ev = pd.read_csv(f'{HERE}/event_returns.csv', parse_dates=['r', 'e0'])
    ev = ev[ev.r >= FIRST + pd.Timedelta(days=40)]
    cal = pd.DatetimeIndex(sorted({pd.Timestamp(x[:10]) for x in os.listdir(f'{M1}/A005930') if x.endswith('.csv')}))
    rows = []
    for _, e in ev.iterrows():
        r = cal.searchsorted(e.r)
        if r >= len(cal) or cal[r] != e.r: continue
        s = day_stats(e.code, e.r)
        if s is None: continue
        base = [day_stats(e.code, cal[j]) for j in range(max(0, r - 20), r)]
        base = [b for b in base if b]
        jb = np.median([b['auc'] / b['last_cont'] - 1 for b in base]) if base else np.nan
        sb = np.median([b['auc_share'] for b in base]) if base else np.nan
        jump = s['auc'] / s['last_cont'] - 1
        rows.append(dict(index=e['index'], review=e.review, action=e.action, code=e.code, name=e['name'], r=e.r,
                         jump=jump, jump_ex=jump - jb, auc_share=s['auc_share'], auc_mult=s['auc_share'] / sb if sb else np.nan,
                         C1=e.C1))
    d = pd.DataFrame(rows)
    d.to_csv(f'{HERE}/auction_check.csv', index=False, encoding='utf-8-sig')
    for (ix, act), g in d.groupby(['index', 'action']):
        print(f'\n=== {ix} {act}  n={len(g)} 회차={g.review.nunique()}')
        for c in ['jump', 'jump_ex', 'C1']:
            x = g[c].dropna()
            print(f'  {c:8s} 평균 {x.mean()*100:+.2f}% 중앙 {x.median()*100:+.2f}% 플러스 {(x>0).mean()*100:.0f}%')
        print(f'  동시호가 비중 중앙 {g.auc_share.median()*100:.1f}% (평소 대비 {g.auc_mult.median():.1f}배)')
    return d


if __name__ == '__main__':
    main()
