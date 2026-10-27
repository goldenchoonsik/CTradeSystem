import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("strategy.py").read().split("forms_A=")[0])
# 교체율(퇴출 비중) 연 1회·반기
def turn(forms):
    prev=None; r=[]
    for d in forms:
        d0=px.index[px.index<=d][-1]; top,hi=names_at(d0)
        if prev is not None: r.append(len(set(prev)-set(hi))/len(prev))
        prev=hi
    return np.mean(r)
ta=turn([pd.Timestamp(f"{y}-12-31") for y in range(2014,2026)]); tb=turn([pd.Timestamp(f"{y}-{m}") for y in range(2014,2026) for m in ("06-30","12-31")][1:]); print(f"고동행 상위20% 평균 퇴출 비중: 연 1회 {100*ta:.0f}% / 반기 {100*tb:.0f}%")
P=pd.read_csv("strategy_A_path.csv",index_col=0,parse_dates=True); s=P[P.index>=px.index[px.index<="2025-12-31"][-1]]
print(f"2026 사후 누적: 고동행 {(s.hi.iloc[-1]/s.hi.iloc[0]-1)*100:+.0f}% / 상위300 {(s['all'].iloc[-1]/s['all'].iloc[0]-1)*100:+.0f}% / 코스피 {(s['코스피'].iloc[-1]/s['코스피'].iloc[0]-1)*100:+.0f}% (기간 {s.index[0].date()}~{s.index[-1].date()})")
# 직전 12개월 수익 상위/시총 큰 종목에 편중되는지(2025-12 형성 구성)
top,hi=names_at(px.index[px.index<="2025-12-31"][-1]); print("2025-12 고동행 상위20% 종목 수",len(hi)); 
nm=kuni.name; print("예:", ", ".join(nm.reindex(hi[:25]).dropna().tolist()))
