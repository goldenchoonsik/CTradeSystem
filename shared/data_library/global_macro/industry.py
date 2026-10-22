"""미국 업종 원장 — Ken French 49업종(1926~) + FRED 업종별 산업생산·생산능력(1972~).

  derived/us_industry/ret_vw.parquet      49업종 가치가중 월수익(소수, 결측 NaN) — 월말 t 에 확정
  derived/us_industry/me.parquet          업종 시가총액 근사(평균 규모 × 기업 수, 백만 달러) — 월 t 시작 기준
  derived/us_industry/bm.parquet          업종 BE/ME(연, 포트폴리오 구성 연도 표기) — 그해 7월 1일부터 사용
  derived/us_industry/ff3.parquet         Mkt-RF·SMB·HML·RF 월(소수)
  derived/us_industry/fund_ip.parquet     업종 산업생산(월, 기간 끝 날짜) — 공개 시차 20일
  derived/us_industry/fund_cap.parquet    업종 생산능력(월) — 공개 시차 20일
원본: raw/kenfrench/<파일>_<받은날>.zip, 업종 FRED 시계열은 series/ind_ip_<업종>.csv · ind_cap_<업종>.csv
"""
import io
import os
import re
import zipfile
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import sources, store
from .store import log

KF_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
KF_FILES = {"ind49": "49_Industry_Portfolios_CSV.zip", "ff3": "F-F_Research_Data_Factors_CSV.zip"}
FUND_LAG_DAYS = 20      # G.17 산업생산·생산능력은 다음 달 중순 공표
BM_AVAILABLE_MONTH = 7  # Ken French BE/ME(구성 연도 t) = t-1 회계연도 BE / t-1 12월 ME → t 년 6월 말 확정, 7월부터 사용

# Ken French 49업종 → FRED (산업생산, 생산능력). 같은 FRED 시계열을 두 업종이 나누지 않게 대표 업종 하나에만 붙임.
FUND_MAP: Dict[str, Tuple[str, Optional[str]]] = {
    "Food": ("IPG311S", "CAPG311S"), "Beer": ("IPG3121S", "CAPG3121S"), "Smoke": ("IPG3122S", "CAPG3122S"),
    "Txtls": ("IPG313A4S", "CAPG313A4S"), "Clths": ("IPG315A6S", "CAPG315A6S"), "Paper": ("IPG322S", "CAPG322S"),
    "Books": ("IPG323S", "CAPG323S"), "Chems": ("IPG325S", "CAPG325S"), "Drugs": ("IPG3254S", "CAPG3254S"),
    "Rubbr": ("IPG326S", "CAPG326S"), "BldMt": ("IPG327S", "CAPG327S"), "Steel": ("IPG331S", "CAPG331S"),
    "FabPr": ("IPG332S", "CAPG332S"), "Mach": ("IPG333S", "CAPG333S"), "Hardw": ("IPG3341S", "CAPG3341S"),
    "Chips": ("IPG3344S", "CAPG3344S"), "LabEq": ("IPG3345S", "CAPG3345S"), "ElcEq": ("IPG335S", "CAPG335S"),
    "Autos": ("IPG3361T3S", "CAPG3361T3S"), "Aero": ("IPG3364S", None), "Oil": ("IPG211S", "CAPG211S"),
    "Mines": ("IPG212S", "CAPG212S"), "Util": ("IPUTIL", "CAPG2211A2S"),
}


# ---------------------------------------------------------------- Ken French 파서(네트워크 없이 시험 가능)
def kf_sections(text: str) -> Dict[str, pd.DataFrame]:
    """Ken French CSV → {구역 제목: 표}. 표 머리는 ',이름,이름…', 행은 'YYYYMM' 또는 'YYYY' 로 시작. −99.99·−999 = NaN, % → 소수 변환은 호출자."""
    out: Dict[str, pd.DataFrame] = {}
    lines = text.splitlines()
    title = None
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith(",") and i > 0:
            cols = [c.strip() for c in ln.split(",")[1:]]
            rows = []
            j = i + 1
            while j < len(lines) and re.match(r"^\s*\d{4,6}\s*,", lines[j]):
                p = [x.strip() for x in lines[j].split(",")]
                rows.append([p[0]] + [float(x) if x else np.nan for x in p[1:len(cols) + 1]])
                j += 1
            name = title or f"table_{len(out)}"
            if rows:
                df = pd.DataFrame(rows, columns=["key"] + cols).set_index("key")
                df = df.replace([-99.99, -999.0], np.nan)
                # 같은 제목이 다시 나오면(월·연) 뒤에 번호
                k = name
                n = 2
                while k in out:
                    k = f"{name} #{n}"
                    n += 1
                out[k] = df
            title = None
            i = j
            continue
        if ln.strip() and not re.match(r"^\s*\d", ln):
            title = ln.strip()
        i += 1
    return out


def _monthly_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df.index.str.len() == 6].copy()
    df.index = pd.to_datetime(df.index, format="%Y%m") + pd.offsets.MonthEnd(0)
    return df


def _annual_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df.index.str.len() == 4].copy()
    df.index = df.index.astype(int)
    return df


def fetch_kenfrench() -> dict:
    out = {}
    for key, fn in KF_FILES.items():
        raw = store.get(KF_BASE + fn, timeout=180)
        store.save_raw(f"kenfrench/{key}_{date.today():%Y%m%d}.zip", raw)
        z = zipfile.ZipFile(io.BytesIO(raw))
        text = z.read(z.namelist()[0]).decode("latin-1")
        out[key] = kf_sections(text)
    return out


def build_kenfrench(secs: dict) -> dict:
    d = store.path("derived", "us_industry")
    os.makedirs(d, exist_ok=True)
    ind = secs["ind49"]
    ret = _monthly_index(ind["Average Value Weighted Returns -- Monthly"]) / 100.0
    nf = _monthly_index(ind["Number of Firms in Portfolios"])
    sz = _monthly_index(ind["Average Firm Size"])
    bm = _annual_index(ind["Sum of BE / Sum of ME"])
    ret.to_parquet(os.path.join(d, "ret_vw.parquet"))
    (sz * nf).to_parquet(os.path.join(d, "me.parquet"))
    bm.to_parquet(os.path.join(d, "bm.parquet"))
    ff = secs["ff3"]
    f3 = _monthly_index(ff[next(iter(ff))]) / 100.0          # 첫 표 = 월별 요인(그다음 연간)
    f3.to_parquet(os.path.join(d, "ff3.parquet"))
    info = {"ret": [str(ret.index[0].date()), str(ret.index[-1].date()), ret.shape[1]], "bm": [int(bm.index[0]), int(bm.index[-1])],
            "ff3": [str(f3.index[0].date()), str(f3.index[-1].date())]}
    log(f"[industry] Ken French 업종 {info}")
    return info


def fetch_fund() -> dict:
    """FRED 업종 산업생산·생산능력 → series/ind_ip_<업종>.csv · ind_cap_<업종>.csv + derived 표."""
    ip, cap, miss = {}, {}, []
    for ind, (ipid, capid) in FUND_MAP.items():
        for sid, bucket, pre in ((ipid, ip, "ind_ip_"), (capid, cap, "ind_cap_")):
            if not sid:
                continue
            try:
                rows = sources.fetch_fred({"key": pre + ind, "sid": sid, "freq": "M"})
                store.write_series(pre + ind, rows)
                s = pd.Series(dict(rows), dtype=float)
                s.index = pd.to_datetime(s.index)
                bucket[ind] = s.sort_index()
            except Exception as e:   # noqa: BLE001
                miss.append(f"{ind}:{sid} ({str(e)[:60]})")
    d = store.path("derived", "us_industry")
    os.makedirs(d, exist_ok=True)
    pd.DataFrame(ip).to_parquet(os.path.join(d, "fund_ip.parquet"))
    pd.DataFrame(cap).to_parquet(os.path.join(d, "fund_cap.parquet"))
    log(f"[industry] FRED 업종 생산 {len(ip)}·생산능력 {len(cap)}, 실패 {miss}")
    return {"ip": len(ip), "cap": len(cap), "missing": miss}


def run() -> bool:
    import json
    info = build_kenfrench(fetch_kenfrench())
    info.update(fetch_fund())
    os.makedirs(store.path("quality"), exist_ok=True)
    with open(store.path("quality", "industry_status.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return True
