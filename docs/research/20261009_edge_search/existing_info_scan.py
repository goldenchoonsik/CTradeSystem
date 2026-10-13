"""기존 데이터의 미사용 정보 스캔 — 122630 향후 h분 (진입 다음 봉, 당일 내, 롱 전용).
신호 후보:
 A fut_mom_vol : 선물 k분 수익 x (선물 k분 거래량 / 같은 시각대 20일 평균)  — 거래량 동반 모멘텀
 B fut_vol_surge_signed : 선물 k분 거래량 급증 z * sign(선물 k분 수익)
 C semi_div : (하이닉스+삼성 평균 k분 수익) - U180 k분 수익   — 반도체 주도/괴리
 D kosdaq_lead : 229200 k분 수익 - 069500급(U180) k분 수익
 E etf_amt_surge : 122630 k분 거래대금 급증 z * sign(122630 k분 수익)
 F u180_vs_fut : U180(현물지수) k분 수익 - 선물 k분 수익 (현물 선행/베이시스 변화 속도)
평가: 분기별 순위 IC 와 부호 일관성, 롱 쪽 상위 5% 순수익(비용 0.03), 1분 지연 진입.
"""
import numpy as np, pandas as pd

cols = ['timestamp', 'close', 'trade_amount', 'ref_10100_close', 'ref_10100_volume', 'ref_u180_close',
        'ref_000660_close', 'ref_005930_close']
d = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=cols)
d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
k2 = pd.read_parquet('DataVault/normalized/candles_1m/229200_1m.parquet', columns=['timestamp', 'close'])
k2['ts'] = pd.to_datetime(k2['timestamp']); d['kq'] = k2.set_index('ts')['close'].reindex(d.index)
tm = d.index.hour * 100 + d.index.minute
d = d[(tm >= 901) & (tm <= 1519)]
tm = d.index.hour * 100 + d.index.minute
day = d.index.normalize(); g = lambda c: d[c].groupby(day)
entry = (tm >= 910) & (tm <= 1500)
q = pd.PeriodIndex(d.index, freq='Q')

def kret(c, k): return g(c).pct_change(k) * 100
def surge(c, k):
    s = g(c).transform(lambda x: x.rolling(k).sum())
    base = s.groupby(tm).transform(lambda x: x.rolling(20, min_periods=10).mean().shift(1))  # 같은 시각대 과거 20일
    return np.log(s / base)

sig = {}
for k in [5, 15]:
    sig[f'A_futmomvol_{k}'] = kret('ref_10100_close', k) * np.exp(surge('ref_10100_volume', k).clip(-3, 3))
    sig[f'B_futvolsurge_{k}'] = surge('ref_10100_volume', k) * np.sign(kret('ref_10100_close', k))
    sig[f'C_semidiv_{k}'] = (kret('ref_000660_close', k) + kret('ref_005930_close', k)) / 2 - kret('ref_u180_close', k)
    sig[f'D_kosdaqlead_{k}'] = kret('kq', k) - kret('ref_u180_close', k)
    sig[f'E_etfamtsurge_{k}'] = surge('trade_amount', k) * np.sign(kret('close', k))
    sig[f'F_spot_vs_fut_{k}'] = kret('ref_u180_close', k) - kret('ref_10100_close', k)

rows = []
for h in [10, 30, 60]:
    fwd = (g('close').shift(-1 - h) / g('close').shift(-1) - 1) * 100
    for name, s in sig.items():
        m = entry & s.notna() & np.isfinite(s) & fwd.notna()
        ss, ff, qq = s[m], fwd[m], q[m]
        ics = pd.Series({p: np.corrcoef(ss[qq == p].rank(), ff[qq == p].rank())[0, 1] for p in qq.unique()})
        sgn = np.sign(ics.mean())
        pick = (ss >= ss.quantile(0.95)) if sgn > 0 else (ss <= ss.quantile(0.05))
        r = ff[pick] - 0.03
        rq = r.groupby(qq[pick]).mean()
        rows.append(dict(sig=name, h=h, IC=round(ics.mean(), 4), IC일관=f'{int((np.sign(ics) == sgn).sum())}/{len(ics)}',
                         롱5순=round(r.mean(), 3), t=round(r.mean() / r.std() * np.sqrt(len(r)), 2), 분기양=f'{int((rq > 0).sum())}/{len(rq)}', n=len(r)))
r = pd.DataFrame(rows).sort_values('롱5순', ascending=False)
pd.set_option('display.width', 200)
print(r.to_string(index=False))
