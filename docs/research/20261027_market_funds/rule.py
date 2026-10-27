import pandas as pd,numpy as np
F=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True)
F["r"]=F.신용융자/F.예탁금; F=F.dropna(subset=["r"])
ret=F.KOSPI.pct_change()
sig_r=F.r.shift(1)          # 한 달 지연: t-1 말 비율로 t월 보유 결정
pctl=F.r.expanding(60).apply(lambda s:(s<=s.iloc[-1]).mean(),raw=False).shift(1)
COST=0.002
def run(w):
    w=w.reindex(ret.index).fillna(0); c=w.diff().abs().fillna(0)*COST
    return (w*ret-c).dropna()
def stats(x,lab,w=None):
    x=x.dropna(); eq=(1+x).cumprod(); yrs=len(x)/12
    cagr=eq.iloc[-1]**(1/yrs)-1; vol=x.std()*np.sqrt(12); mdd=(eq/eq.cummax()-1).min()
    inv=(w.reindex(x.index).mean() if w is not None else 1)
    return f"{lab:34s} CAGR {cagr*100:5.1f}%  변동성 {vol*100:4.1f}%  샤프 {cagr/vol if vol else 0:4.2f}  MDD {mdd*100:5.1f}%  투자비중 {inv*100:3.0f}%"
rules={
 "A 비율<0.33 투자, 이상 현금(고정컷)": (sig_r<0.33).astype(float),
 "B 확장창 백분위<0.6 투자":              (pctl<0.6).astype(float).where(pctl.notna()),
 "C 확장창: <0.4=100%,<0.8=50%,이상=0":   pd.Series(np.select([pctl<0.4,pctl<0.8],[1.0,0.5],0.0),index=F.index).astype(float).where(pctl.notna()),
 "D 고정컷 0.28/0.36 (100/50/0)":         pd.Series(np.select([sig_r<0.28,sig_r<0.36],[1.0,0.5],0.0),index=F.index).astype(float),
}
periods=[("전체 1998~","1998","2026"),("2008~","2008","2026"),("2015~","2015","2026"),("2021~","2021","2026")]
for lab,a,b in periods:
    print(f"\n=== {lab}")
    print(stats(ret[a:b],"단순 보유 (KOSPI 가격)"))
    for n,w in rules.items():
        if w.notna().sum()==0: continue
        ww=w[a:b]
        if ww.notna().sum()<12: continue
        print(stats(run(w)[a:b],n,w))
# 위험조정: 같은 투자비중의 무작위 대비 (시기 선택 효과) — 투자비중 일치 무작위 5000회 A규칙
w=rules["A 비율<0.33 투자, 이상 현금(고정컷)"]; x=run(w); base=ret.reindex(x.index)
rng=np.random.default_rng(0); k=int(w.reindex(x.index).sum()); n=len(x)
sims=[]
for _ in range(5000):
    idx=rng.choice(n,k,replace=False); s=np.zeros(n); s[idx]=1; sims.append((1+base.values*s).prod()**(12/n)-1)
actual=(1+x).prod()**(12/n)-1
print(f"\n무작위 비교(규칙 A, 투자 {k}/{n}개월 같은 비중): 실제 CAGR {actual*100:.1f}% , 무작위 중앙 {np.median(sims)*100:.1f}%, 실제보다 높은 무작위 비율 {np.mean(np.array(sims)>=actual)*100:.1f}%")
