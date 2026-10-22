"""삼성 단일 종목 연구용 외부 원천 수집 (ECOS·KOSIS·FRED·Yahoo).

원문은 DataVault/staging/samsung_cycle/raw/{ecos,kosis,fred,yahoo}/ 에 그대로 저장, 정리본은 같은 폴더 *.csv.
키: ECOS_KEY, KOSIS_KEY (환경변수, 없으면 bash -ic 로 읽음).
  .venv/bin/python docs/research/20261022_samsung_single/fetch_macro.py
"""
import csv, json, os, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from shared.data_library.paths import data_vault_path  # noqa: E402

ROOT = data_vault_path("staging", "samsung_cycle")
UA = {"User-Agent": "Mozilla/5.0 (research; samsung_cycle)"}

# (이름, 통계표, 주기, 항목1, 항목2) — 항목은 ECOS StatisticItemList 로 확인(2026-10-22)
ECOS = [
    ("xpi_dram_usd", "402Y016", "30911201AA", "D"),     # 수출물가 D램, 달러기준
    ("xpi_dram_krw", "402Y016", "30911201AA", "W"),     # 원화기준
    ("xpi_flash_usd", "402Y016", "30911202AA", "D"),    # 플래시메모리, 달러기준
    ("xpi_flash_krw", "402Y016", "30911202AA", "W"),
    ("xpi_ic_usd", "402Y014", "309112AA", "D"),         # 집적회로 전체, 달러기준
    ("kr_call", "721Y001", "1010000", None),            # 무담보콜금리(1일) — 기준금리 대리
    ("kr_tb3y", "721Y001", "5020000", None),            # 국고채 3년
    ("kr_tb10y", "721Y001", "5050000", None),           # 국고채 10년 (2000-10~)
]
# 반도체 제조업(C261) 출하·재고 지수, 전국 — 광업제조업동향조사 DT_1F02001
KOSIS_ITEMS = {"T11": "ship_raw", "T12": "inv_raw", "T21": "ship_sa", "T22": "inv_sa"}
FRED = ["DGS10", "DGS2", "DFII10", "FEDFUNDS", "DEXKOUS", "T10Y2Y"]
YAHOO = ["^SOX", "MU", "SMH", "SOXX", "^GSPC", "^NDX", "^TNX", "^IRX", "^FVX"]  # ^TNX 등 = 미 국채 수익률(%)
# FRED 는 2026-10-22 점검 중(HTML 응답)이라 기본 목록에서 뺌 — 금리=Yahoo, 환율=ECOS 서울 매매기준율


def key(name):
    k = os.environ.get(name) or subprocess.run(["bash", "-ic", f"echo -n ${name}"], capture_output=True, text=True).stdout.strip()
    if not k:
        raise RuntimeError(f"{name} 없음")
    return k


def get(url, hdr=None):
    for i in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=hdr or UA), timeout=300).read()
        except Exception:
            if i == 3:
                raise
            time.sleep(3 * (i + 1))


def save(sub, name, data: bytes):
    p = os.path.join(ROOT, "raw", sub, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def write_csv(name, header, rows):
    with open(os.path.join(ROOT, name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def ecos():
    k = key("ECOS_KEY")
    end = datetime.now().strftime("%Y%m")
    out = {}
    for name, stat, i1, i2 in ECOS:
        path = f"{stat}/M/197001/{end}/{i1}" + (f"/{i2}" if i2 else "")
        raw = get(f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100000/{path}")
        save("ecos", f"{name}.json", raw)
        rows = json.loads(raw).get("StatisticSearch", {}).get("row", [])
        if not rows:
            raise RuntimeError(f"ECOS {name}: {raw[:300]}")
        out[name] = {r["TIME"]: float(r["DATA_VALUE"]) for r in rows if r["DATA_VALUE"] not in ("", None)}
        print("ecos", name, min(out[name]), max(out[name]), len(out[name]), flush=True)
    yms = sorted(set().union(*[v.keys() for v in out.values()]))
    write_csv("ecos_monthly.csv", ["ym"] + list(out), [[ym] + [out[n].get(ym, "") for n in out] for ym in yms])
    # 원/달러 매매기준율(일별) — D일 값은 D-1 서울 시장평균이라 D일 장중 사용 가능
    raw = get(f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100000/731Y001/D/19800101/{datetime.now():%Y%m%d}/0000001")
    save("ecos", "usdkrw_daily.json", raw)
    rows = json.loads(raw)["StatisticSearch"]["row"]
    write_csv("usdkrw_ecos_daily.csv", ["date", "usdkrw"],
              [[f"{r['TIME'][:4]}-{r['TIME'][4:6]}-{r['TIME'][6:]}", float(r["DATA_VALUE"])] for r in rows if r["DATA_VALUE"]])
    print("ecos usdkrw", rows[0]["TIME"], rows[-1]["TIME"], len(rows), flush=True)


def kosis():
    k = key("KOSIS_KEY")
    q = {"method": "getList", "apiKey": k, "itmId": "+".join(KOSIS_ITEMS) + "+", "objL1": "00+", "objL2": "C261+",
         "format": "json", "jsonVD": "Y", "prdSe": "M", "startPrdDe": "198001", "endPrdDe": datetime.now().strftime("%Y%m"),
         "orgId": "101", "tblId": "DT_1F02001"}
    raw = get("https://kosis.kr/openapi/Param/statisticsParameterData.do?" + urllib.parse.urlencode(q))
    save("kosis", "DT_1F02001_C261.json", raw)
    d = json.loads(raw)
    out = {}
    for r in d:
        out.setdefault(r["PRD_DE"], {})[KOSIS_ITEMS[r["ITM_ID"]]] = float(r["DT"])
    cols = list(KOSIS_ITEMS.values())
    write_csv("kosis_semis_monthly.csv", ["ym"] + cols, [[ym] + [out[ym].get(c, "") for c in cols] for ym in sorted(out)])
    print("kosis", min(out), max(out), len(out), flush=True)


def fred():
    for s in FRED:
        raw = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}")
        save("fred", f"{s}.csv", raw)
        print("fred", s, raw.decode().splitlines()[1][:20], raw.decode().splitlines()[-1], flush=True)


def yahoo():
    p2 = int(datetime.now(timezone.utc).timestamp())
    for t in YAHOO:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?period1=0&period2={p2}&interval=1d&events=div%2Csplit"
        raw = get(url)
        fn = t.replace("^", "IDX_")
        save("yahoo", f"{fn}.json", raw)
        r = json.loads(raw)["chart"]["result"][0]
        ts = r["timestamp"]
        q = r["indicators"]["quote"][0]
        adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
        rows = []
        for i, x in enumerate(ts):
            if q["close"][i] is None or adj[i] is None:
                continue
            # Yahoo 일봉 timestamp 는 해당 거래소 개장 시각(UTC) — 뉴욕 날짜로 변환
            d = datetime.fromtimestamp(x + r["meta"].get("gmtoffset", 0), timezone.utc).strftime("%Y-%m-%d")
            rows.append([d, q["close"][i], adj[i]])
        write_csv(f"yahoo_{fn}.csv", ["date", "close", "adjclose"], rows)
        print("yahoo", t, rows[0][0], rows[-1][0], len(rows), flush=True)


if __name__ == "__main__":
    which = sys.argv[1:] or ["ecos", "kosis", "yahoo"]
    for w in which:
        globals()[w]()
