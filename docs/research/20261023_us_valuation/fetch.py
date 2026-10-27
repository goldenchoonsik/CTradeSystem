"""미국 밸류에이션 연구 원천 수집 (2026-10-23).

받는 것 → DataVault/staging/us_valuation/raw/<원천>/<이름>_<받은날>.<확장자>
  shiller   : ie_data.xls (S&P 1871~ 월 가격·배당·이익·CPI·10년물, shillerdata.com 최신판)
  multpl    : S&P 가격·배당·이익(최근 12개월)·CAPE·PER·10년물 월 표 (Shiller 이후 달 연장·교차 확인용)
  kenfrench : F-F 3요인 월간(시장 초과수익·무위험 1926-07~, CRSP 가치가중 = 배당 포함·상폐 포함)
  treasury  : 미 재무부 실질수익률 곡선(TIPS, 2003~ 일별)
  yahoo     : ^GSPC 일봉(최근 달 월평균 가격)
  fred      : GS10·TB3MS·BAA·AAA·CPIAUCSL·DFII10·T10YIE·USREC — FRED 가 막히면 global_macro 10-22 사본으로 대체(그 사실을 기록)

실행: .venv/bin/python -I docs/research/20261023_us_valuation/fetch.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import date, datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from shared.data_library.paths import data_vault_path  # noqa: E402

STAGE = data_vault_path("staging", "us_valuation")
RAW = os.path.join(STAGE, "raw")
TODAY = f"{date.today():%Y%m%d}"
UA = "Mozilla/5.0 (CTradeSystem us_valuation research)"
LOG = []

SHILLER_URL = ("https://img1.wsimg.com/blobby/go/e5e77e0b-59d1-44d9-ab25-4763ac982e53/downloads/"
               "1449043e-a1ed-4e2e-8e9c-7b9a2a064493/ie_data.xls")
MULTPL = ["s-p-500-price", "s-p-500-dividend", "s-p-500-earnings", "shiller-pe", "s-p-500-pe-ratio",
          "10-year-treasury-rate"]
FRED = ["GS10", "TB3MS", "BAA", "AAA", "CPIAUCSL", "DFII10", "T10YIE", "USREC"]
# FRED 막힘 대비: global_macro 가 10-22 받은 판
FRED_FALLBACK = {"GS10": "us_gs10", "TB3MS": "us_tb3m", "BAA": "us_baa_m", "CPIAUCSL": "us_cpi", "USREC": "us_rec"}
TREASURY_REAL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                 "daily-treasury-rates.csv/{y}/all?type=daily_treasury_real_yield_curve"
                 "&field_tdr_date_value={y}&page&_format=csv")


def note(msg):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.append(line)


def curl(url, timeout=90, http1=False):
    cmd = ["curl", "-sS", "-L", "--fail", "--max-time", str(timeout), "-A", UA]
    if http1:
        cmd.append("--http1.1")
    out = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 15)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(out.stderr.decode(errors="replace").strip()[:200] or f"rc={out.returncode}")
    return out.stdout


def save(src, name, data):
    d = os.path.join(RAW, src)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, name)
    with open(fp + ".tmp", "wb") as f:
        f.write(data)
    os.replace(fp + ".tmp", fp)
    note(f"저장 {src}/{name} ({len(data):,} B)")
    return fp


def main():
    os.makedirs(RAW, exist_ok=True)
    # Shiller
    try:
        save("shiller", f"ie_data_{TODAY}.xls", curl(SHILLER_URL, 120))
    except Exception as e:  # noqa: BLE001
        note(f"실패 shiller: {e}")
    # multpl
    for k in MULTPL:
        try:
            save("multpl", f"{k}_{TODAY}.html", curl(f"https://www.multpl.com/{k}/table/by-month", 60))
        except Exception as e:  # noqa: BLE001
            note(f"실패 multpl {k}: {e}")
    # Ken French
    try:
        z = curl("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip", 90)
        with zipfile.ZipFile(io.BytesIO(z)) as zf:
            csvname = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
            save("kenfrench", f"ff3_monthly_{TODAY}.csv", zf.read(csvname))
    except Exception as e:  # noqa: BLE001
        note(f"실패 kenfrench: {e}")
    # Treasury real yields (연도별 csv)
    chunks = []
    for y in range(2003, date.today().year + 1):
        try:
            chunks.append(curl(TREASURY_REAL.format(y=y), 60).decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            note(f"실패 treasury {y}: {e}")
    if chunks:
        head = chunks[0].splitlines()[0]
        body = [ln for c in chunks for ln in c.splitlines()[1:] if ln.strip()]
        save("treasury", f"real_yield_{TODAY}.csv", ("\n".join([head] + body) + "\n").encode())
    # Yahoo ^GSPC
    try:
        p2 = int(datetime.now(timezone.utc).timestamp())
        save("yahoo", f"GSPC_{TODAY}.json",
             curl(f"https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?period1=-1325635200&period2={p2}&interval=1d", 90))
    except Exception as e:  # noqa: BLE001
        note(f"실패 yahoo: {e}")
    # FRED (+ 대체)
    for sid in FRED:
        try:
            save("fred", f"{sid}_{TODAY}.csv", curl(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", 45, http1=True))
            continue
        except Exception as e:  # noqa: BLE001
            note(f"실패 fred {sid}: {e}")
        fb = FRED_FALLBACK.get(sid)
        if fb:
            src_dir = data_vault_path("staging", "global_macro", "raw", "fred", fb)
            vs = sorted(f for f in os.listdir(src_dir) if f.endswith(".csv")) if os.path.isdir(src_dir) else []
            if vs:
                os.makedirs(os.path.join(RAW, "fred"), exist_ok=True)
                dst = os.path.join(RAW, "fred", f"{sid}_{vs[-1][:8]}_globalmacro.csv")
                shutil.copyfile(os.path.join(src_dir, vs[-1]), dst)
                note(f"대체 fred {sid} ← global_macro/{fb}/{vs[-1]}")
    with open(os.path.join(RAW, f"fetch_log_{TODAY}.json"), "w", encoding="utf-8") as f:
        json.dump(LOG, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
