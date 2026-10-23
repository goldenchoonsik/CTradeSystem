"""SEC 일괄 자료 수집: 임원 거래(Form 3/4/5, 2006Q1~) + 재무제표 숫자(Financial Statement Data Sets, 2009Q1~).

링크는 SEC 목록 페이지에서 그대로 읽는다(2026Q2부터 임원 거래 폴더가 structureddata→datastandardsinnovation 으로 바뀜, 주소 추측 금지).
원본 zip 무수정 보존(0o444), 크기 = content-length 검증, 이미 받은 파일은 건너뜀(재개 가능).
User-Agent 는 환경변수 SEC_USER_AGENT(이름+연락처, SEC 공정 접근 정책) — 없으면 중단.
산출: DataVault/staging/us_sec_bulk/raw/{form345,fsds}/<원본 파일명>, reference/manifest.json
"""
import json, os, re, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[3] / "DataVault/staging/us_sec_bulk"
PAGES = {
    "form345": "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
    "fsds": "https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets",
}
UA = os.environ.get("SEC_USER_AGENT")
if not UA:
    sys.exit("SEC_USER_AGENT 가 없습니다: eval \"$(grep '^export SEC_USER_AGENT' ~/.bashrc)\"")
H = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}


def links(kind):
    r = requests.get(PAGES[kind], headers=H, timeout=60)
    r.raise_for_status()
    return sorted(set(re.findall(r'href="([^"]+\.zip)"', r.text)))


def fetch(path, dest):
    url = "https://www.sec.gov" + path
    for k in range(5):
        try:
            with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=120) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                size = int(r.headers.get("content-length", 0))
                tmp = dest.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                if size and tmp.stat().st_size != size:
                    raise RuntimeError(f"크기 불일치 {tmp.stat().st_size} != {size}")
                tmp.rename(dest)
                os.chmod(dest, 0o444)
                return size
        except Exception as e:
            print(f"  재시도 {k + 1}: {path} {e}", flush=True)
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"실패: {path}")


def main():
    manifest = {}
    for kind in PAGES:
        out = ROOT / "raw" / kind
        out.mkdir(parents=True, exist_ok=True)
        paths = links(kind)
        print(f"{kind}: {len(paths)}개", flush=True)
        for p in paths:
            dest = out / p.rsplit("/", 1)[1]
            if dest.exists():
                size = dest.stat().st_size
            else:
                size = fetch(p, dest)
                print(f"  {dest.name} {size / 1e6:.1f}MB", flush=True)
                time.sleep(0.2)
            manifest[f"{kind}/{dest.name}"] = {"url": "https://www.sec.gov" + p, "bytes": size}
    (ROOT / "reference").mkdir(exist_ok=True)
    (ROOT / "reference/manifest.json").write_text(json.dumps(
        {"fetched": time.strftime("%Y-%m-%d %H:%M"), "files": manifest}, ensure_ascii=False, indent=1))
    print(f"완료: {len(manifest)}개 {sum(v['bytes'] for v in manifest.values()) / 1e9:.2f}GB", flush=True)


if __name__ == "__main__":
    main()
