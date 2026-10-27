"""DRAM(Roundhill Memory) 상장 후 순설정 대략 추정. 확보 자료: 일별 NAV(4/1~10/23), 순자산 6/30 $26.12bn(반기보고서)·10/23 $18.51bn, 발행주식수 10/23 391.39백만.
6/30 발행주식수 = 순자산/NAV(72.38). 구간 내 일별 주식 증가 경로는 모름 → 균등·초반집중·후반집중 3가지로 민감도.
한국 비중: 7/21 스냅샷(웹) 삼성 26+하이닉스 22=48% (최근 비중 미확인).
"""
import numpy as np, pandas as pd
D=pd.read_csv("/home/kuzimo/projects/CTradeSystem/docs/research/20261025_foreign_channels/etf/raw_xls/roundhill_DRAM_daily.csv")
D["d"]=pd.to_datetime(D["Rate Date"]); nav=D.set_index("d").NAV
s630=26_120_339_189/nav["2026-06-30"]; s1023=391_390_000
print(f"6/30 추정 주식 {s630/1e6:.1f}M (NAV {nav['2026-06-30']:.2f}) → 10/23 {s1023/1e6:.1f}M (+{(s1023-s630)/1e6:.1f}M, 7~10월)")
def cre(w, a, b, s0, s1):
    seg=nav[a:b]; k=np.linspace(0,1,len(seg)); c=(k**w); c=c/c[-1]*(s1-s0); inc=np.diff(np.concatenate([[0],c]))
    return (inc*seg.values).sum()
for lab,w in [("균등",1.0),("초반집중",0.5),("후반집중",2.0)]:
    q2=cre(w,"2026-04-02","2026-06-30",0,s630); q3=cre(1.0,"2026-07-01","2026-10-23",s630,s1023)
    print(f"{lab}: 4/2~6/30 순설정 ${q2/1e9:.1f}bn + 7/1~10/23 ${q3/1e9:.1f}bn = ${(q2+q3)/1e9:.1f}bn → 한국(48%) ${0.48*(q2+q3)/1e9:.1f}bn")
print(f"NAV 상장 28.24 → 고점 {nav.max():.2f}({nav.idxmax().date()}) → 현재 {nav.iloc[-1]:.2f}")
print(f"현재 순자산 $18.51bn 중 삼성+하이닉스 48% ≈ ${18.51*0.48:.1f}bn, 마이크론 27%≈${18.51*0.27:.1f}bn, 기타 25%")
print(f"마이크론 제외 시 한국 비중 {48/73:.0%}(기타 25% 가정)")
