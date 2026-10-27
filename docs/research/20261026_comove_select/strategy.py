import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("robustness.py").read().split("CFG=[")[0])
SIDE=0.00185
def names_at(d0,bm="SPY",win=104,minw=80,frac=0.2,top_n=300):
    cap=de[de.date==de.date[de.date<=d0].max()].set_index("code").market_cap.dropna()
    ok=[c for c in cap.index if c in kuni.index and kuni.loc[c,"is_preferred"]=="0" and kuni.loc[c,"is_spac"]=="0" and c in px.columns]
    top=cap.loc[ok].nlargest(top_n).index
    w=pw.loc[(pw.index<=d0)&(pw.index>d0-pd.Timedelta(days=7*win+14)),top]; s=bw[bm].reindex(w.index)
    cor=w.apply(lambda c:c.corr(s) if c.notna().sum()>=minw else np.nan).dropna()
    k=int(round(len(cor)*frac)); hi=cor.nlargest(k).index
    return list(top), list(hi)
def build(dates):
    """dates: 형성일 리스트(오름차순) + 마지막 종료일. 반환 일별 포트 지수(고동행, 상위300)."""
    segs=[]; hold_prev=None; all_prev=None; val={"hi":1.0,"all":1.0}; out={"hi":[],"all":[]}; idx=[]
    for j in range(len(dates)-1):
        d0,d1=dates[j],dates[j+1]; top,hi=names_at(d0)
        # 비용
        for k,names,prev in [("hi",hi,hold_prev),("all",top,all_prev)]:
            if prev is None: cost=SIDE
            else:
                out_f=len(set(prev)-set(names))/len(prev); in_f=len(set(names)-set(prev))/len(names); cost=SIDE*(out_f+in_f)
            val[k]*=(1-cost)
        hold_prev,all_prev=hi,top
        seg=px.loc[d0:d1]; seg=seg.ffill()
        for k,names in [("hi",hi),("all",top)]:
            rel=(seg[names]/seg[names].iloc[0]).mean(axis=1)*val[k]; out[k].append(rel.iloc[1:] if j>0 else rel); val[k]=rel.iloc[-1]
        idx.append(seg.index)
    return pd.DataFrame({k:pd.concat(v) for k,v in out.items()})
def stats(s,a,b):
    s=s[(s.index>=a)&(s.index<=b)]; r=np.log(s).diff().dropna(); yrs=len(r)/252
    return dict(CAGR=(np.exp(r.sum())**(1/yrs)-1)*100,변동성=r.std()*np.sqrt(252)*100,샤프=r.mean()/r.std()*np.sqrt(252),MDD=((s/s.cummax())-1).min()*100)
last=px.index[-1]
def run(forms,label):
    dates=[px.index[px.index<=d][-1] for d in forms]+[last]; P=build(dates); K=ko.reindex(P.index).ffill(); P["코스피"]=K/K.iloc[0]
    print(f"\n### {label}")
    rows=[]
    for per,(a,b) in {"개발(2015~2020)":("2015-01-01","2020-12-31"),"검증(2021~2025)":("2021-01-01","2025-12-31"),"전체(2015~2025)":("2015-01-01","2025-12-31"),"사후(2026-01~10-23)":("2026-01-01",str(last.date()))}.items():
        for k,n in [("hi","고동행 상위20%"),("all","상위300 EW"),("코스피","코스피")]:
            s=P[k]; 
            if per.startswith("사후"): s=s[s.index>=px.index[px.index<="2025-12-31"][-1]]
            st=stats(s,a if not per.startswith("사후") else a,b); rows.append((per,n,*st.values()))
    T=pd.DataFrame(rows,columns=["구간","대상","CAGR%","변동성%","샤프","MDD%"]); print(T.round(2).to_string(index=False)); return P,T
forms_A=[pd.Timestamp(f"{y}-12-31") for y in range(2014,2026)]
forms_B=[pd.Timestamp(f"{y}-{m}") for y in range(2014,2026) for m in ("06-30","12-31")][1:]
PA,TA=run(forms_A,"A 연 1회(12월 말)"); PB,TB=run(forms_B,"B 반기(6월·12월)")
TA.to_csv("strategy_A.csv",index=False); TB.to_csv("strategy_B.csv",index=False); PA.to_csv("strategy_A_path.csv"); PB.to_csv("strategy_B_path.csv")
# 판정
print("\n=== 판정(개발·검증 모두 CAGR>상위300 & 샤프>상위300) ===")
for lab,T in [("A",TA),("B",TB)]:
    ok=True; msg=[]
    for per in ["개발(2015~2020)","검증(2021~2025)"]:
        h=T[(T.구간==per)&(T.대상=="고동행 상위20%")].iloc[0]; a=T[(T.구간==per)&(T.대상=="상위300 EW")].iloc[0]; c=h["CAGR%"]>a["CAGR%"] and h.샤프>a.샤프; ok&=c; msg.append(f"{per}: {h['CAGR%']:.1f}% vs {a['CAGR%']:.1f}%, 샤프 {h.샤프:.2f} vs {a.샤프:.2f} → {'○' if c else '×'}")
    print(f"  {lab}: "+" | ".join(msg)+f" ⇒ {'합격' if ok else '불합격'}")
