"""2024년 이전(2014~2023) 구간에 기존 규칙(변경 없음)을 적용: BH · M1 월말 폭≥4 · S3 자기 50일선 · D2 전환 즉시 매도. 비용 왕복 0.37%. 탐색(사전 고정 규칙 재사용, 새 파라미터 없음)."""
import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
src=open("exit_backtest.py").read().split("rows=[]")[0]; exec(src)
def seg(a,b): return (a,b)
SEGS={"2014~2023(전체)":("2014-01-01","2024-01-01"),"2014~2019":("2014-01-01","2020-01-01"),"2020~2023":("2020-01-01","2024-01-01")}
rows=[]; yearly=[]
for an,asset in [("한국 메모리",mem),("한국 소부장",sobu)]:
    own50=(asset.reindex(kd)>asset.reindex(kd).rolling(50).mean())
    P={"BH":pd.Series(1.0,index=kd),"M1 월말 폭":monthly(breadth>=4),"S3 자기 50일선":monthly(own50),"D2 전환 즉시 매도":d2(asset)}
    for rn,pos in P.items():
        net=run(asset,pos)
        for sn,(a,b) in SEGS.items(): rows.append((an,rn,sn,*met(net,pos,a,b)))
        for y in range(2014,2024):
            x=net[(net.index>=f"{y}-01-01")&(net.index<f"{y+1}-01-01")]; yearly.append((an,rn,y,(np.exp(x.sum())-1)*100))
T=pd.DataFrame(rows,columns=["자산","규칙","구간","CAGR%","샤프","MDD%","투자%","연거래수"]); pd.set_option("display.width",220); print(T.round(2).to_string(index=False))
Y=pd.DataFrame(yearly,columns=["자산","규칙","연도","수익%"]).pivot_table(index=["자산","규칙"],columns="연도",values="수익%"); print("\n=== 연도별 수익(%) ===");print(Y.round(0).to_string())
for an in ["한국 메모리","한국 소부장"]:
    b=Y.loc[(an,"BH")]; m=Y.loc[(an,"M1 월말 폭")]; print(f"\n{an}: M1이 BH보다 나은 해 {(m>b).sum()}/10 | BH가 음수인 해({', '.join(str(y) for y in b.index[b<0])}) M1 평균 {m[b<0].mean():+.0f}% vs BH {b[b<0].mean():+.0f}% | BH 양수인 해 M1 {m[b>=0].mean():+.0f}% vs BH {b[b>=0].mean():+.0f}%")
T.to_csv("pre2024.csv",index=False); Y.to_csv("pre2024_yearly.csv")
