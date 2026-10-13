"""일 단위 체계적 배분 검증 (K200 선물 10년 일봉, 2배 상품 기준).
- 일간 수익 = 15:30 종가 대비. 만기 교체일 수익은 0 처리(연속월물 점프 제거).
- 노출(레버리지 L)은 전날 정보로 결정, 다음날 수익에 적용. 비용: 노출 변화량 x 0.03%(2배 상품 왕복 근사).
"""
import numpy as np, pandas as pd

f = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
f['ts'] = pd.to_datetime(f['timestamp']); f = f.set_index('ts').sort_index()
tm = f.index.hour * 100 + f.index.minute
c = f[tm == 1530]['close']; c.index = c.index.normalize()
r = c.pct_change()
def second_thu(y, m):
    x = pd.Timestamp(y, m, 1); return x + pd.Timedelta(days=(3 - x.weekday()) % 7 + 7)
for y in range(2016, 2027):
    for m in (3, 6, 9, 12):
        d0 = second_thu(y, m); nx = r.index[r.index > d0]
        if len(nx): r.loc[nx[0]] = 0.0  # 교체 다음날 점프 제거
r = r.dropna().clip(-0.15, 0.15)

vol20 = r.rolling(20).std() * np.sqrt(250)
ma = c.reindex(r.index).rolling(120).mean()
px = c.reindex(r.index)

def perf(L, name):
    L = L.shift(1).fillna(0).clip(0, 2)          # 전날 정보로 결정
    ret = L * r - L.diff().abs().fillna(0) * 0.0003
    ret = ret['2017-01-01':]
    eq = (1 + ret).cumprod(); yrs = len(ret) / 250
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; vol = ret.std() * np.sqrt(250); mdd = (1 - eq / eq.cummax()).max()
    yr = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
    print(f'{name:<34} CAGR={cagr:+6.1%} 변동성={vol:5.1%} 샤프={cagr / vol:+.2f} MDD={mdd:5.1%} 평균노출={L["2017":].mean():.2f} | 최악연도={yr.min():+.0%}')

one = pd.Series(1.0, index=r.index)
perf(2 * one, '① 122630 그냥 보유 (2배)')
perf(one, '② 069500 그냥 보유 (1배)')
perf((0.25 / vol20).clip(0, 2), '③ 변동성 목표 25% (0~2배)')
perf(2 * (px > ma).astype(float), '④ 추세 필터 (120일선 위면 2배)')
perf(((0.25 / vol20).clip(0, 2)) * (px > ma), '⑤ 변동성 목표 + 추세 필터')
perf(((0.25 / vol20).clip(0, 2)) * (0.5 + 0.5 * (px > ma)), '⑥ 변동성 목표 + 추세(아래면 절반)')
