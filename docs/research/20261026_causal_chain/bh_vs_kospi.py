import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("comove_persistence.py").read().split("def fwd")[0])
def stats(s,a,b):
    x=np.log(s.reindex(kd)).diff().fillna(0); x=x[(x.index>=a)&(x.index<b)]; yrs=len(x)/252; cum=np.exp(x.cumsum())
    return (np.exp(x.sum())**(1/yrs)-1)*100, x.mean()/x.std()*np.sqrt(252), (cum/cum.cummax()-1).min()*100, (np.exp(x.sum())-1)*100
S={"코스피(가격지수)":kospi,"한국 메모리":mem,"한국 소부장":sobu}
rows=[]
for per,(a,b) in {"2014~2023":("2014-01-01","2024-01-01"),"2014~2019":("2014-01-01","2020-01-01"),"2020~2023":("2020-01-01","2024-01-01"),"2023-01~2026-10":("2023-01-01","2027-01-01"),"2014~2026-10(전체)":("2014-01-01","2027-01-01")}.items():
    for n,s in S.items(): rows.append((per,n,*stats(s,a,b)))
T=pd.DataFrame(rows,columns=["구간","대상","CAGR%","샤프","MDD%","누적%"]); pd.set_option("display.width",200); print(T.round(1).to_string(index=False))
Y={}
for n,s in S.items():
    r=np.log(s.reindex(kd)).diff().fillna(0); Y[n]=r.groupby(r.index.year).sum().pipe(lambda x:(np.exp(x)-1)*100)
Y=pd.DataFrame(Y).loc[2014:2026]; Y["메모리−코스피"]=Y["한국 메모리"]-Y["코스피(가격지수)"]; Y["소부장−코스피"]=Y["한국 소부장"]-Y["코스피(가격지수)"]
print("\n=== 연도별 수익(%) (2026은 10월 중순까지) ==="); print(Y.round(0).to_string())
print(f"\n메모리가 코스피 이긴 해 {(Y['메모리−코스피']>0).sum()}/{len(Y)}, 소부장 {(Y['소부장−코스피']>0).sum()}/{len(Y)}")
# 삼성·하이닉스 제외한 소부장, 삼성전자·하이닉스 개별
for c,n in [("A005930","삼성전자"),("A000660","SK하이닉스")]:
    s=pr[pr.code==c].set_index("date").close; print(n,"2014~2026-10 CAGR %.1f%%, 2014~2023 %.1f%%"%(stats(s,"2014-01-01","2027-01-01")[0],stats(s,"2014-01-01","2024-01-01")[0]))
