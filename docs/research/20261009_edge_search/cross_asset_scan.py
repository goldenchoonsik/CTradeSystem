"""교차시장 선행 스캔: 해외 자산 과거 k분 수익 → KRX 타깃 향후 h분 수익.
누출 방지: cTrader/바이낸스 등은 bar_open 라벨 → 확정 시각 = 라벨+1분, 여기에 1분 추가 지연(총 +2분)해서 결합.
타깃: 122630(2024-08~, 순수익 판단) 과 K200 선물(2020~, 반기 일관성 판단). 진입 09:10~15:00, 같은 날 안에서만.
"""
import numpy as np, pandas as pd

def load(path, label_shift_min):
    d = pd.read_parquet(path)[['timestamp', 'close']]
    d['ts'] = pd.to_datetime(d['timestamp']) + pd.Timedelta(minutes=label_shift_min)
    return d.set_index('ts')['close'].sort_index()

tgt = {'122630': load('DataVault/normalized/candles_1m/122630_1m.parquet', 0),
       'K200F': load('DataVault/normalized/macro/10100_1m.parquet', 0)['2020-01-01':]}
X = {s: load(f'DataVault/normalized/candles_1m/{s}_1m.parquet', 2)
     for s in ['US_TECH_100', 'US_500', 'JAPAN_225', 'HONG_KONG_50', 'USDJPY', 'XAUUSD', 'BTCUSDT']}

COST = 0.03
rows = []
for tn, t in tgt.items():
    idx = t.index; day = idx.normalize(); tm = idx.hour * 100 + idx.minute
    entry = (tm >= 910) & (tm <= 1500)
    for h in [15, 30, 60]:
        fwd = (t.groupby(day).shift(-h) / t - 1) * 100
        for xn, x in X.items():
            xa = x.reindex(idx, method='ffill', tolerance=pd.Timedelta(minutes=3))
            for k in [5, 15, 30]:
                xk = x.reindex(idx - pd.Timedelta(minutes=k), method='ffill', tolerance=pd.Timedelta(minutes=3)).values
                sig = pd.Series(xa.values / xk - 1, index=idx)
                m = entry & sig.notna().values & fwd.notna().values
                if m.sum() < 2000: continue
                s, f = sig[m], fwd[m]
                hy = pd.PeriodIndex(s.index, freq='Q-DEC').asfreq('Q') if tn == '122630' else pd.Series(s.index.year * 10 + (s.index.month > 6), index=s.index)
                blocks = pd.Series(hy, index=s.index) if not isinstance(hy, pd.Series) else hy
                ics = pd.Series({b: np.corrcoef(s[blocks == b].rank(), f[blocks == b].rank())[0, 1] for b in blocks.unique() if (blocks == b).sum() > 300})
                mean_ic = ics.mean()
                cons = (np.sign(ics) == np.sign(mean_ic)).mean()
                # 롱 전용: 신호 방향이 양(+)이면 상위10%, 음이면 하위10% 에서 매수
                q = s.quantile(0.9) if mean_ic > 0 else s.quantile(0.1)
                pick = (s >= q) if mean_ic > 0 else (s <= q)
                mult = 1 if tn == '122630' else 2  # 선물은 122630 환산(2배)
                net = (f[pick] * mult).mean() - COST
                rows.append(dict(tgt=tn, x=xn, k=k, h=h, IC=round(mean_ic, 4), 일관=round(cons, 2), 블록=len(ics), 상위10순=round(net, 3), n=int(pick.sum())))
r = pd.DataFrame(rows)
piv = r.pivot_table(index=['x', 'k', 'h'], columns='tgt', values=['IC', '일관', '상위10순'])
piv.columns = [f'{a}_{b}' for a, b in piv.columns]
piv['both_net+'] = (piv['상위10순_122630'] > 0) & (piv['상위10순_K200F'] > 0) & (piv['일관_K200F'] >= 0.75) & (np.sign(piv['IC_122630']) == np.sign(piv['IC_K200F']))
pd.set_option('display.width', 250)
print(piv.sort_values('상위10순_122630', ascending=False).head(30).to_string())
print('\n합격(양쪽 순수익+, 선물 반기일관≥75%, IC 동부호):', int(piv['both_net+'].sum()))
