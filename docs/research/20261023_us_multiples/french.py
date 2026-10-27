"""장기(생존 편향 없음): Ken French 정렬 포트폴리오 — 싼(상위 10%) − 비싼(하위 10%) 가치가중 월 수익, 10년대별.

원천 → DataVault/staging/us_multiples/raw/kenfrench/<파일>_<받은날>.csv.  출력 results_french.json
실행: .venv/bin/python -I french.py
"""
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT  # noqa: E402

RAW = os.path.join(ROOT, "staging", "us_multiples", "raw", "kenfrench")
BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {"B/M(=1/PBR)": "Portfolios_Formed_on_BE-ME", "E/P(=1/PER)": "Portfolios_Formed_on_E-P",
         "CF/P": "Portfolios_Formed_on_CF-P", "D/P(배당)": "Portfolios_Formed_on_D-P", "2x3": "6_Portfolios_2x3"}


def fetch(name):
    os.makedirs(RAW, exist_ok=True)
    fp = os.path.join(RAW, f"{name}_{date.today():%Y%m%d}.csv")
    if not os.path.exists(fp):
        z = subprocess.run(["curl", "-sS", "-L", "--fail", "-m", "90", "-A", "Mozilla/5.0", BASE + name + "_CSV.zip"],
                           capture_output=True, check=True).stdout
        with zipfile.ZipFile(io.BytesIO(z)) as zf:
            open(fp, "wb").write(zf.read([n for n in zf.namelist() if n.lower().endswith(".csv")][0]))
    return fp


def vw_monthly(fp):
    """첫 'Value Weight Returns -- Monthly' 구역 → DataFrame(월 수익, 소수)."""
    lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
    i = next(k for k, ln in enumerate(lines) if "Value Weight" in ln and "Monthly" in ln)
    header = [c.strip() for c in lines[i + 1].split(",")]
    rows = []
    for ln in lines[i + 2:]:
        p = [c.strip() for c in ln.split(",")]
        if len(p) < 2 or not (len(p[0]) == 6 and p[0].isdigit()):
            break
        rows.append(p)
    df = pd.DataFrame(rows, columns=header).set_index(header[0])
    df.index = pd.PeriodIndex([f"{x[:4]}-{x[4:]}" for x in df.index], freq="M")
    df = df.apply(pd.to_numeric, errors="coerce")
    return df.where(df > -99) / 100.0


def stats(hi, lo):
    sp = hi - lo
    out = {}
    dec = (sp.index.year // 10) * 10
    for d in sorted(set(dec)):
        m = dec == d
        h, l_ = hi[m], lo[m]
        yrs = m.sum() / 12
        out[f"{d}s"] = {"싼_연%": round(100 * ((1 + h).prod() ** (1 / yrs) - 1), 1), "비싼_연%": round(100 * ((1 + l_).prod() ** (1 / yrs) - 1), 1),
                        "차_연%p": round(100 * (((1 + h).prod() ** (1 / yrs)) - ((1 + l_).prod() ** (1 / yrs))), 1), "개월": int(m.sum())}
    t = sp.mean() / (sp.std() / np.sqrt(len(sp)))
    half = sp.index.year < 1990
    out["전체"] = {"기간": f"{sp.index[0]}~{sp.index[-1]}", "월평균차×12 %p": round(1200 * sp.mean(), 2), "t": round(float(t), 2),
                 "~1989 %p": round(1200 * sp[half].mean(), 2), "1990~ %p": round(1200 * sp[~half].mean(), 2),
                 "2007~ %p": round(1200 * sp[sp.index.year >= 2007].mean(), 2)}
    return out


def main():
    res = {}
    for lab, name in FILES.items():
        df = vw_monthly(fetch(name))
        if lab == "2x3":
            res["대형주 B/M (Big HiBM − Big LoBM)"] = stats(df["BIG HiBM"], df["BIG LoBM"])
        else:
            res[lab + " 상위10%−하위10%"] = stats(df["Hi 10"], df["Lo 10"])
    with open(os.path.join(HERE, "results_french.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    for k, v in res.items():
        print("==", k, v["전체"])
        print("   ", {d: x["차_연%p"] for d, x in v.items() if d != "전체"})


if __name__ == "__main__":
    main()
