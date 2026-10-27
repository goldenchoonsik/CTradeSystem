# 탐색용(사전등록 아님): 낙폭 -X% 최초 도달 시점부터 자본 1을 어떻게 투입하느냐 비교. 24개월(504거래일) 뒤 평가가치.
import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
exec(open("bottom5.py").read().split("R=[]")[0])
H=504
def run(c,th):
    c=c[~c.index.duplicated()]; v=c.values; idx=c.index; dd=(c/c.cummax()-1)*100; out=[]
    inn=False; i=0; n=len(c); pk=0
    while i<n:
        if v[i]>=v[:i+1].max(): inn=False; pk=i
        if dd.iloc[i]<=th and not inn:
            inn=True; t0=i
            if t0+H<n:
                p0=v[t0]; end=v[t0+H]
                def tr(days): # 시간 분할: 균등 tranche
                    w=np.mean([end/v[t0+d] for d in days]); return w
                S0=end/p0
                S3=tr([0,60,120])
                S6=tr([0,24,48,72,96,120])
                # 가격 분할: 도달가 기준 추가 -15%, -30%에서 1/3씩, 미체결은 252일에 투입
                lv=[p0,p0*.85,p0*.70]; tot=0
                for L in lv:
                    j=next((k for k in range(t0,t0+252) if v[k]<=L),None)
                    tot+=(end/v[j] if j is not None else end/v[t0+252])/3
                out.append(dict(고점=idx[pk],도달=idx[t0],일시=S0,시간3=S3,시간6=S6,가격3=tot,군집=idx[pk].year//2))
        i+=1
    return out
for th in (-30,-40):
    rows=[]
    for n,c in S.items():
        for r in run(c,th): r["자산"]=n; rows.append(r)
    R=pd.DataFrame(rows); cols=["일시","시간3","시간6","가격3"]
    g=R.groupby("군집")[cols].mean()   # 군집(2년 고점) 평균
    print(f"\n=== -{abs(th)}% 최초 도달 후, 24개월 뒤 자본 1의 가치 (에피소드 {len(R)}, 군집 {len(g)}) ===")
    print("에피소드 평균", R[cols].mean().round(3).to_dict()); print("에피소드 중앙", R[cols].median().round(3).to_dict())
    print("군집 평균의 평균", g.mean().round(3).to_dict(), " 군집 중앙", g.median().round(3).to_dict())
    print("최악 에피소드 5% 분위", R[cols].quantile(.05).round(3).to_dict())
    for c in cols[1:]: print(f"  {c}가 일시보다 나은 비율: 에피소드 {(R[c]>R['일시']).mean():.2f} / 군집 {(g[c]>g['일시']).mean():.2f}")
    print("1 미만(24개월 뒤 손실) 비율:",(R[cols]<1).mean().round(2).to_dict())
