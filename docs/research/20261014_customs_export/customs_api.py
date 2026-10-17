"""관세청_시군구별 품목별 수출입실적 API 캐시 클라이언트.

응답 원문은 DataVault/staging/customs_export/raw/{hs}/{sido}_{yyyy}.xml 에 한 번만 저장(재조회 없음).
금액 단위는 천 달러(USD k) 로 보임 — 검산은 feasibility.py 에서.
  .venv/bin/python docs/research/20261014_customs_export/customs_api.py 190230 48 2025
"""
import os, re, sys, time, subprocess, urllib.parse, urllib.request
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from shared.data_library.paths import data_vault_path

URL = "https://apis.data.go.kr/1220000/sigunguperprlstperacrs/getSigunguPerPrlstPerAcrs"
ROOT = data_vault_path("staging", "customs_export")
NUM = {"expUsdAmt", "expCnt", "impUsdAmt", "impCnt", "cmtrBlncAmt"}


def _key() -> str:
    k = os.environ.get("OPEN_DATA_KEY")
    if not k:
        k = subprocess.run(["bash", "-ic", "echo -n $OPEN_DATA_KEY"], capture_output=True, text=True).stdout.strip()
    if not k:
        raise RuntimeError("OPEN_DATA_KEY 없음")
    return k


def fetch(hs: str, sido: str, year: int) -> str:
    """1년치(또는 올해는 전월까지) 원문 XML. 이미 있으면 파일에서. 올해 파일은 매번 새로."""
    fp = os.path.join(ROOT, "raw", hs, f"{sido}_{year}.xml")
    current = year >= date.today().year
    if os.path.exists(fp) and not current:
        return open(fp, encoding="utf-8").read()
    q = {"serviceKey": _key(), "strtYymm": f"{year}01", "endYymm": f"{year}12", "HsSgn": hs, "sidoCd": sido}
    for i in range(4):
        try:
            t = urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(q), timeout=120).read().decode()
            break
        except Exception:
            if i == 3:
                raise
            time.sleep(3 * (i + 1))
    code = re.search(r"<resultCode>(\d+)</resultCode>", t)
    if not code or code.group(1) != "00":
        raise RuntimeError(f"{hs} {sido} {year}: {t[:300]}")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(t)
    return t


def parse(t: str) -> list[dict]:
    out = []
    for x in re.findall(r"<item>(.*?)</item>", t, re.S):
        d = {a: b.strip() for a, b in re.findall(r"<(\w+)>([^<]*)</\1>", x)}
        for n in NUM & d.keys():
            d[n] = float(d[n].replace(",", "") or 0)
        out.append(d)
    return out


def rows(hs: str, sido: str, year: int) -> list[dict]:
    return parse(fetch(hs, sido, year))


if __name__ == "__main__":
    for d in rows(*sys.argv[1:3], int(sys.argv[3]))[:20]:
        print(d)
