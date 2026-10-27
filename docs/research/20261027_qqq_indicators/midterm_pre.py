"""중간선거(미국, 1930~2022 24회) 전 S&P500 저점·상승 시작 시점. 저점=그해 1/1~선거일 종가 최저. 탐색, 비용 무관."""
import numpy as np, pandas as pd
s = pd.read_csv("/home/kuzimo/projects/CTradeSystem/DataVault/staging/global_macro/series/us_spx.csv", parse_dates=["date"]).set_index("date").value
def election(y):   # 11월 첫 월요일 다음 화요일
    d = pd.Timestamp(f"{y}-11-01"); d += pd.Timedelta(days=(7 - d.weekday()) % 7)   # 첫 월요일
    return d + pd.Timedelta(days=1)
rows = []; paths = []
for y in range(1930, 2023, 4):
    e = election(y); idx = s.index; ie = idx.searchsorted(e)          # 선거일(휴장 없음) 위치
    if ie < 130 or ie + 130 >= len(idx): continue
    yr = s[f"{y}-01-01":idx[ie]]; lo = yr.idxmin(); il = idx.get_loc(lo)
    p = s.iloc[ie - 60: ie + 21].values / s.iloc[ie] - 1; paths.append(p)
    pre5, pre10, pre20 = [s.iloc[ie] / s.iloc[ie - k] - 1 for k in (5, 10, 20)]
    post5 = s.iloc[ie + 5] / s.iloc[ie] - 1
    rows.append(dict(년=y, 선거일=idx[ie].date(), 저점일=lo.date(), 저점_선거전_거래일=ie - il, 저점_선거전_달력일=(idx[ie] - lo).days, 저점대비_선거일=s.iloc[ie] / s.loc[lo] - 1, 선거전5일=pre5, 선거전10일=pre10, 선거전20일=pre20, 선거후5일=post5, 올해_YTD=s.iloc[ie] / s[:f"{y-1}-12-31"].iloc[-1] - 1, 고점대비=s.iloc[ie] / s[:idx[ie]].max() - 1))
d = pd.DataFrame(rows); pd.set_option("display.width", 250)
o = d.copy()
for k in ["저점대비_선거일", "선거전5일", "선거전10일", "선거전20일", "선거후5일", "올해_YTD", "고점대비"]: o[k] = (o[k] * 100).round(1)
print(o.to_string(index=False))
print(f"\n표본 {len(d)}회. 저점이 선거 전 며칠(달력)? 중앙 {d.저점_선거전_달력일.median():.0f}일, 평균 {d.저점_선거전_달력일.mean():.0f}일, 사분위 {d.저점_선거전_달력일.quantile(.25):.0f}~{d.저점_선거전_달력일.quantile(.75):.0f}일")
print("저점이 선거 전 30일 안:", int((d.저점_선거전_달력일 <= 30).sum()), " 31~90일:", int(((d.저점_선거전_달력일 > 30) & (d.저점_선거전_달력일 <= 90)).sum()), " 90일 초과:", int((d.저점_선거전_달력일 > 90).sum()))
print("저점이 9~10월:", int(pd.to_datetime(d.저점일).dt.month.isin([9, 10]).sum()), "/", len(d))
for k, lab in (("선거전5일", "선거 5거래일 전~선거일"), ("선거전10일", "10거래일 전~"), ("선거전20일", "20거래일 전~"), ("선거후5일", "선거일~5거래일 후")):
    print(f"{lab}: 평균 {d[k].mean()*100:.2f}%  중앙 {d[k].median()*100:.2f}%  상승 {int((d[k]>0).sum())}/{len(d)}")
P = np.array(paths); m = np.nanmean(P, axis=0); 
print("\n선거일 대비 누적(평균 경로, 선거일=0):")
for k in (-60, -40, -20, -10, -5, -3, -1, 0, 3, 5, 10, 20): print(f"  {k:+d}거래일: {m[60+k]*100:+.2f}%  (상승 {int((P[:,60+k] < 0).sum())}/{len(P)}회는 그 시점이 선거일보다 낮음)")
