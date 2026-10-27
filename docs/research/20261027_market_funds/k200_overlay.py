# 사전 고정: 매년 12월 마지막 거래일 시총 상위 200(코스피, 보통주, 상폐 포함, 우선주·스팩 제외) 동일가중 보유(1년), 상폐·거래정지는 마지막 가격 유지.
# A2 규칙 그대로(c=0.33, 경고월 50%, 2개월 지연, 비용 편도 0.185%×|Δw|). 비교: 보유. 개발 2015~2020 / 검증 2021~2025 / 2026.
import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"; SIDE=0.00185
kuni=pd.read_csv(f"{V}/staging/kr_equity/universe.csv",dtype=str).set_index("code")
de=pd.read_parquet(f"{V}/staging/kr_equity/derived/daily_ext.parquet",columns=["code","date","market_cap"]); de["date"]=pd.to_datetime(de.date)
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
px=pr.pivot(index="date",columns="code",values="close").sort_index()
print("가격",px.index.min().date(),px.index.max().date(),px.shape)
M=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True); M["r"]=M.신용융자/M.예탁금; rm=M.r.copy(); rm.index=rm.index.to_period("M")
def wser(idx,c=0.33,w=0.5,lag=2):
    src=idx.to_period("M")-lag; v=pd.Series([rm.get(p,np.nan) for p in src],index=idx).ffill(); return pd.Series(np.where(v<c,1.0,w),index=idx)
last=px.index[-1]; forms=[px.index[px.index<=pd.Timestamp(f"{y}-12-31")][-1] for y in range(2014,2026)]+[last]
hold_s=[];ov_s=[];rows=[];hv=1.0;ov=1.0;idx_all=[]
for j in range(len(forms)-1):
    d0,d1=forms[j],forms[j+1]
    cap=de[de.date==de.date[de.date<=d0].max()].set_index("code").market_cap.dropna()
    ok=[c for c in cap.index if c in kuni.index and kuni.loc[c,"market"]=="KOSPI" and kuni.loc[c,"is_preferred"]=="0" and kuni.loc[c,"is_spac"]=="0" and c in px.columns and pd.notna(px.loc[d0,c])]
    top=list(cap.loc[ok].nlargest(200).index)
    seg=px.loc[d0:d1,top].ffill(); rel=seg/seg.iloc[0]               # 종목별 상대 가격
    ew=rel.mean(axis=1); r=ew.pct_change().fillna(0)
    w=wser(seg.index).shift(1).fillna(1.0)
    hv_seg=hv*ew/ew.iloc[0]; hold_s.append(hv_seg.iloc[1:] if j else hv_seg); hv=hv_seg.iloc[-1]
    cost=w.diff().abs().fillna(0)*SIDE
    ov_seg=ov*(1+(w*r-cost)).cumprod(); ov_s.append(ov_seg.iloc[1:] if j else ov_seg); ov=ov_seg.iloc[-1]
    # 종목별 (같은 w 적용)
    for c in top:
        x=rel[c]; rr=x.pct_change().fillna(0); o=(1+(w*rr-cost)).cumprod()
        rows.append(dict(년=d0.year,code=c,hold_ret=(x.iloc[-1]-1)*100,ov_ret=(o.iloc[-1]-1)*100,hold_mdd=((x/x.cummax())-1).min()*100,ov_mdd=((o/o.cummax())-1).min()*100,
                         delisted=int(kuni.loc[c,"status"]!="listed")))
H=pd.concat(hold_s); O=pd.concat(ov_s); K=pd.read_csv("kospi_daily_ecos.csv",index_col=0,parse_dates=True).iloc[:,0].reindex(H.index).ffill(); K=K/K.iloc[0]
def st(x,a,b):
    x=x[(x.index>=a)&(x.index<=b)]; l=np.log(x).diff().dropna(); y=len(l)/252
    return dict(C=(np.exp(l.sum())**(1/y)-1)*100,S=l.mean()/l.std()*np.sqrt(252),M=((x/x.cummax())-1).min()*100)
per={"개발 2015~20":("2015-01-01","2020-12-31"),"검증 2021~25":("2021-01-01","2025-12-31"),"2026":("2026-01-01",str(last.date())),"전체":("2015-01-01",str(last.date()))}
print("\n### 시총 상위 200 동일가중(연 1회 재구성): 보유 vs A2 규칙 vs 코스피")
for k,(a,b) in per.items():
    h,o,c=st(H,a,b),st(O,a,b),st(K,a,b)
    print(f"{k:12s} 보유 {h['C']:5.1f}% 샤{h['S']:4.2f} MDD{h['M']:6.1f} | 규칙 {o['C']:5.1f}% 샤{o['S']:4.2f} MDD{o['M']:6.1f} | 코스피 {c['C']:5.1f}% 샤{c['S']:4.2f} MDD{c['M']:6.1f}")
R=pd.DataFrame(rows); R.to_csv("k200_stockyear.csv",index=False,encoding="utf-8-sig")
R["dmdd"]=R.ov_mdd-R.hold_mdd; R["dret"]=R.ov_ret-R.hold_ret
print(f"\n### 종목-연도 {len(R)}건 (종목 {R.code.nunique()}개, 상폐 {R[R.delisted==1].code.nunique()}개)")
for lab,g in (("전체",R),("2015~2020",R[R.년<=2019]),("2021~2025",R[(R.년>=2020)&(R.년<=2024)]),("이후 상폐 종목",R[R.delisted==1])):
    print(f"{lab:12s} n={len(g):4d} 낙폭 개선 비율 {(g.dmdd>0).mean()*100:3.0f}% (평균 낙폭 {g.hold_mdd.mean():.1f}→{g.ov_mdd.mean():.1f}) | 수익 평균 {g.hold_ret.mean():.1f}→{g.ov_ret.mean():.1f}%, 중앙 {g.hold_ret.median():.1f}→{g.ov_ret.median():.1f}% | 수익 개선 비율 {(g.dret>0).mean()*100:.0f}%")
# 규모별
cap_rank=None
print("\n### 연도별 EW 연수익(%)  보유 / 규칙")
Y=R.groupby("년")[["hold_ret","ov_ret"]].mean().round(1); print(Y.T.to_string())
