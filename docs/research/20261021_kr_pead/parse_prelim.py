"""DART 영업(잠정)실적(공정공시) 원문 zip → 원시 수치 표 (한국판 PEAD 연구용).

  .venv/bin/python -I docs/research/20261021_kr_pead/parse_prelim.py            # 받은 문서 전부 → prelim_parsed.parquet
  .venv/bin/python -I docs/research/20261021_kr_pead/parse_prelim.py --sample 30 # 무작위 표본 출력(점검용)

양식이 해마다 다르다(열 5·6·7개, 증감액 열 유무, 당기=연간 누적인 회사, 머리글 단위 '백만원'인데 본문 주석은 '조원').
그래서 '매출액/영업이익 > 당해실적' 바로 뒤 첫 숫자(= 당기실적)만 읽고, 기간 종류(분기·누적)와 단위 배수는
build_events.py 가 이미 공개된 재무(직전 분기·전년 보고서)와 대조해 정한다. '-'·빈칸은 NaN, '△'·괄호는 음수.
"""
import argparse
import os
import re
import zipfile

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DOCS = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "raw", "dart", "prelim_docs")
OUT = os.path.dirname(os.path.abspath(__file__))
UNIT = {"조원": 1e12, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}
NUM = re.compile(r"^-?[\d,]*\.?\d+$")


def doc_path(no):
    return os.path.join(DOCS, no[:4], f"{no}.zip")


def tokens(no):
    z = zipfile.ZipFile(doc_path(no))
    b = z.read(z.namelist()[0])
    try:                                                                       # 2022 중반부터 meta 는 euc-kr 인데 실제 바이트는 UTF-8
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        s = b.decode("cp949", "replace")
    s = re.sub(r"(?is)<style.*?</style>", " ", s)
    t = [re.sub(r"\s+", "", x.replace("&nbsp;", " ")) for x in re.split(r"<[^>]+>", s)]
    return [x for x in t if x]


def num(x):
    x = x.replace(",", "")
    neg = x.startswith("△") or x.startswith("▽") or (x.startswith("(") and x.endswith(")"))
    x = x.lstrip("△▽+").strip("()")
    if not NUM.match(x):
        return np.nan
    v = float(x)
    return -abs(v) if neg else v


def parse(no):
    t = tokens(no)
    joined = "|".join(t)
    out = dict(no=no, unit=np.nan, unit_note=np.nan, sales_raw=np.nan, op_raw=np.nan, sales_yoy_raw=np.nan, op_yoy_raw=np.nan,
               sales_seg=0, op_seg=0, op_row=False)
    mu = re.search(r"단위[:：]?\(?(조원|억원|백만원|천원|원)", joined)
    if mu:
        out["unit"] = UNIT[mu.group(1)]
    mn = re.search(r"(조원|억원|백만원|천원)단위", joined)               # 예: '상기금액은조원단위이며'
    if mn:
        out["unit_note"] = UNIT[mn.group(1)]
    has_amt = "증감액" in joined[:3000]
    for key, col in (("매출액", "sales"), ("영업이익", "op")):
        for i, x in enumerate(t[:-2]):
            if (x == key or x.startswith(key + "(")) and t[i + 1] == "당해실적":
                seg = []
                for y in t[i + 2:i + 12]:
                    if y == "누계실적":
                        break
                    seg.append(y)
                out[f"{col}_raw"] = num(seg[0]) if seg else np.nan
                # 전년동기실적 위치: 5열 [당기,전기,%,전년,%] → 3, 7열(전환여부 또는 증감액 열) → 4, 6열 → 증감액 양식이면 4 아니면 3
                yi = {5: 3, 7: 4}.get(len(seg), (4 if has_amt else 3) if len(seg) == 6 else None)
                out[f"{col}_yoy_raw"] = num(seg[yi]) if yi is not None and yi < len(seg) else np.nan
                out[f"{col}_seg"] = len(seg)
                if col == "op":
                    out["op_row"] = True
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    a = ap.parse_args()
    cand = pd.read_csv(os.path.join(OUT, "prelim_candidates.csv"), dtype=str)
    have = cand[[os.path.exists(doc_path(n)) for n in cand.no]]
    if a.sample:
        s = have.sample(min(a.sample, len(have)), random_state=1)
        pd.set_option("display.width", 250)
        print(pd.DataFrame([dict(**parse(n), name=nm, dt=dt) for n, nm, dt in zip(s.no, s.name, s.dt)]))
        return
    rows = []
    for k, n in enumerate(have.no, 1):
        try:
            rows.append(parse(n))
        except Exception as e:
            rows.append(dict(no=n, err=f"{e}"[:80]))
        if k % 4000 == 0:
            print(f"  {k}/{len(have)}", flush=True)
    P = have.merge(pd.DataFrame(rows), on="no")
    P.to_parquet(os.path.join(OUT, "prelim_parsed.parquet"))
    print(f"{len(P)} 문서 — 영업이익 값 있음 {P.op_raw.notna().mean():.1%}, 영업이익 행 없음 {(~P.op_row.astype(bool)).mean():.1%}, "
          f"단위 주석 덮어쓰기 {P.unit_note.notna().sum()}건")


if __name__ == "__main__":
    main()
