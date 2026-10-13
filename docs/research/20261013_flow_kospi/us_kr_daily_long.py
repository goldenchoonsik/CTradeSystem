"""Q1·Q2 장기 재확인: CME 나스닥 선물 일봉(2016~)의 직전 미국 거래일 수익률 → 한국 갭·외국인 (읽기 전용).

CME 일봉 라벨 날짜 D = D 일 미국 정규장이 끝나는 세션. 한국 d 일 개장 전 마지막으로 끝난 세션 = 라벨 < d 인 마지막 D.
한국 휴장 중 미국이 여러 번 열렸으면 그 사이 누적. 매핑 검증: 라벨 = d(같은 날) 로 잘못 붙이면 상관이 무너져야 한다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import flow_kospi as F  # noqa: E402
from us_kr_leadlag import corr, ols, by_year  # noqa: E402

nq = pd.read_csv(os.path.join(F.REPO, "DataVault", "raw", "overseas", "futures_1d", "NQ_1d.csv"), parse_dates=["timestamp"])
nq["d"] = nq.timestamp.dt.normalize()
nqc = nq.set_index("d")["close"].sort_index()
idx = pd.read_parquet(os.path.join(F.KR, "derived", "index_1d.parquet"))
k2 = idx[idx.code == "U180"].set_index("date").sort_index()
k2 = k2[k2.index >= "2016-10-06"]
d = pd.DataFrame(index=k2.index)
d["gap"] = np.log(k2.open / k2.close.shift()) * 100
d["intra"] = np.log(k2.close / k2.open) * 100


def us_close_before(days, inclusive=False):
    pos = nqc.index.searchsorted(days, side="right" if inclusive else "left") - 1
    return pd.Series(np.where(pos >= 0, nqc.values[np.clip(pos, 0, None)], np.nan), index=days)


c_now = us_close_before(d.index)                   # 라벨 < d
d["us_prev"] = np.log(c_now / c_now.shift()) * 100
c_wrong = us_close_before(d.index, inclusive=True)  # 라벨 <= d (검증용 오매핑)
d["us_wrong"] = np.log(c_wrong / c_wrong.shift()) * 100

codes = F.kospi_codes()
cr = F.load_creon(codes)
d["frgn"] = (cr["외국인"] / cr.tv * 100).reindex(d.index)
d["inst"] = (cr["기관계"] / cr.tv * 100).reindex(d.index)

L = ["## 장기 재확인 (CME 나스닥 선물 일봉, " + f"{d.index.min():%Y-%m-%d} ~ {d.index.max():%Y-%m-%d})", "",
     f"- 매핑 검증: 갭 ~ 직전 미국 세션 {corr(d.gap, d.us_prev)} vs 오매핑(같은 날 라벨 포함) {corr(d.gap, d.us_wrong)}",
     f"- 갭 ~ 직전 미국: {ols(d.gap, d[['us_prev']], ['us_prev'])}",
     f"- 갭 연도별 상관: {by_year(lambda s: f'{s.gap.corr(s.us_prev):+.2f}', d)}",
     f"- 장중 ~ 직전 미국 + 갭 (갭 이후 추가 반영/되돌림): {ols(d.intra, d[['us_prev','gap']], ['us_prev','gap'])}",
     f"- 장중 연도별 상관(직전 미국): {by_year(lambda s: f'{s.intra.corr(s.us_prev):+.2f}', d)}",
     f"- 외국인 ~ 직전 미국: {ols(d.frgn, d[['us_prev']], ['us_prev'])}",
     f"- 외국인 ~ 직전 미국 + 갭 + 장중: {ols(d.frgn, d[['us_prev','gap','intra']], ['us_prev','gap','intra'])}",
     f"- 기관 ~ 직전 미국 + 갭 + 장중: {ols(d.inst, d[['us_prev','gap','intra']], ['us_prev','gap','intra'])}",
     f"- 외국인 연도별 상관(직전 미국): {by_year(lambda s: f'{s.frgn.corr(s.us_prev):+.2f}', d)}",
     f"- 외국인 연도별 상관(갭): {by_year(lambda s: f'{s.frgn.corr(s.gap):+.2f}', d)}",
     f"- 외국인 연도별 상관(장중): {by_year(lambda s: f'{s.frgn.corr(s.intra):+.2f}', d)}",
     ""]
with open(os.path.join(os.path.dirname(__file__), "results_us_kr.md"), "a", encoding="utf-8") as f:
    f.write("\n".join(L))
print("\n".join(L))
