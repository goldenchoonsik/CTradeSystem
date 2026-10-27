import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
A=pd.read_csv("model_states.csv",parse_dates=["t","고점"]); K=A[A.자산=="KOSPI"].copy()
M=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True)
M["r"]=M.신용융자/M.예탁금
M["융자고점대비"]=(M.신용융자/M.신용융자.cummax()-1)*100   # 누적 최고 대비(과거만)
M["융자3m"]=np.log(M.신용융자).diff(3)*100; M["예탁3m"]=np.log(M.예탁금).diff(3)*100
M=M.shift(1)   # 1개월 지연
K["m"]=K.t.dt.to_period("M").dt.to_timestamp("M")
K=K.merge(M[["r","융자고점대비","융자3m","예탁3m"]],left_on="m",right_index=True,how="left").dropna(subset=["r","융자3m"])
K["ep"]=K.고점.astype(str)
print("KOSPI 상태",len(K),"에피소드",K.ep.nunique(),"바닥나옴 비율",round(K.바닥나옴.mean(),2))
def auc(p,y):
    r=pd.Series(p).rank().values; n1=y.sum(); n0=len(y)-n1
    return np.nan if n1==0 or n0==0 else (r[y==1].sum()-n1*(n1+1)/2)/(n1*n0)
print("\n단일 지표 AUC (전체 / 에피소드별 평균; >0.5 = 값이 클수록 바닥 나옴)")
for c in ["낙폭","일수","반등","저점후일","r","융자고점대비","융자3m","예탁3m"]:
    per=[auc(g[c].values,g.바닥나옴.values) for _,g in K.groupby("ep")]; per=[x for x in per if x==x]
    print(f"{c:10s} {auc(K[c].values,K.바닥나옴.values):.2f} / {np.mean(per):.2f} (n={len(per)}, 최소 {np.min(per):.2f}, 최대 {np.max(per):.2f})")
# 현재
cur=M.iloc[-1]; print("\n현재(1개월 지연, 2026-09 값): 신용/예탁금 %.3f, 융자 누적최고 대비 %.1f%%, 융자3m %.1f%%, 예탁3m %.1f%%"%(cur.r,cur.융자고점대비,cur.융자3m,cur.예탁3m))
