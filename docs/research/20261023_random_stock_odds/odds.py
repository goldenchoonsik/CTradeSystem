"""S&P500 종목 하나를 무작위로 사서 3년 보유 시 결과 분포 — steady_compounders 창 패널 재사용(총수익, 생존 편향 있음).
실행: .venv/bin/python -I docs/research/20261023_random_stock_odds/odds.py"""
import numpy as np, pandas as pd
W = pd.read_parquet('DataVault/staging/steady_compounders/derived/windows.parquet')
W = W[~W.incomplete].copy()
print('창', len(W), '끊김(상폐·피인수 등, 결과 없음)', round(W.ended.mean()*100,1), '%')
D = W[~W.ended].copy()
D['tot'] = (1+D.cagr)**3-1
D['spy_tot'] = (1+D.spy_cagr)**3-1
D['yr'] = pd.to_datetime(D.start).dt.year
def summ(g):
    return pd.Series({'n':len(g),'플러스':(g.tot>0).mean(),'SPY초과':(g.tot>g.spy_tot).mean(),
        '-30%이하':(g.tot<=-.30).mean(),'-50%이하':(g.tot<=-.50).mean(),'2배이상':(g.tot>=1).mean(),
        '중앙값':g.tot.median(),'평균':g.tot.mean(),'SPY중앙':g.spy_tot.median()})
fmt = lambda t: t.round(3).to_string()
print(fmt(summ(D).to_frame().T))
D['era'] = pd.cut(D.yr,[1999,2004,2009,2014,2019,2023],labels=['2000-04','2005-09','2010-14','2015-19','2020-23'])
print(fmt(D.groupby('era',observed=True).apply(summ)))
D['spy_mdd_b'] = pd.cut(D.spy_mdd,[-1,-.25,-.15,0],labels=['SPY낙폭25%+','15~25%','15%미만'])
print(fmt(D.groupby('spy_mdd_b',observed=True).apply(summ)))
# 시작일을 동일 가중(시작일별 평균 → 평균)
by = D.groupby('start').apply(summ)
print('시작일 동일가중 평균'); print(fmt(by.drop(columns='n').mean().to_frame().T))
# 끊긴 창을 -100%로 보수 가정 시 플러스 비율
W['tot']=np.where(W.ended,-1.0,(1+W.cagr)**3-1)
print('끊긴 창=전손 가정 플러스비율', round((W.tot>0).mean(),3))
