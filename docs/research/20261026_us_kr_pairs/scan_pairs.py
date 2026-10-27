"""미국 종목(유동성 상위) × 한국 종목(시총 상위) 선행 쌍 전수 스캔. 탐색·기술 통계(사전등록 아님, 개발/검증 분할로 과적합 방어).
신호=미국 d 일 수익(SPY 회귀 잔차), 대상=한국 d 다음 세션 수익(코스피 회귀 잔차). 개발 2016-01~2022-12, 검증 2023-01~2026-10.
선별: 개발 상관 상위 + 검증 상관 부호 일치·크기 기준. 후보 쌍 수가 수십만이라 개발 최댓값이 우연으로 ≈0.10까지 올라가므로 검증 구간 결과가 판정.
"""
import os, sys, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from multiprocessing import Pool
R="/home/kuzimo/projects/CTradeSystem"; V=f"{R}/DataVault"; OUT=f"{R}/docs/research/20261026_us_kr_pairs"; SC="/tmp/claude-1000/-home-kuzimo-projects-CTradeSystem/d094c582-b7b0-44c9-8c91-b513d85505c6/scratchpad"
uni=pd.read_csv(f"{V}/staging/us_equity/reference/universe.csv",dtype=str); uni=uni[uni.etf=="N"]
def load(sym):
    f=f"{V}/raw/yahoo/candle_1d/{sym}_1d.csv"
    try: d=pd.read_csv(f,usecols=["timestamp","close","volume"],parse_dates=["timestamp"]).dropna()
    except Exception: return None
    d=d[d.timestamp>="2014-06-01"]
    if len(d)<1500: return None
    d["dv"]=d.close*d.volume; dv=d.dv.tail(250).median()
    d=d.set_index("timestamp"); d.index=d.index.normalize()
    return sym,float(dv),d.close[~d.index.duplicated()]
if __name__=="__main__":
    cache=f"{SC}/us_close.parquet"
    if not os.path.exists(cache):
        with Pool(8) as p: res=[r for r in p.map(load,list(uni.yahoo_symbol),chunksize=100) if r]
        res.sort(key=lambda x:-x[1]); res=res[:800]
        pd.DataFrame({s:c for s,_,c in res}).to_parquet(cache); pd.Series({s:dv for s,dv,_ in res}).to_csv(f"{SC}/us_dv.csv")
        print("미국",len(res),"종목 캐시")
    usc=pd.read_parquet(cache); print("미국 후보",usc.shape)
