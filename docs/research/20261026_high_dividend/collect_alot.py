"""DART 배당에 관한 사항(alotMatter) 원본 수집 — 원장 밖 staging/kr_equity/raw/dart/alot_matter/<corp>/<연도>.json
한 번 호출이 당기·전기·전전기 3개 연도를 준다 → 보고연도 2017·2020·2023·2025 로 FY2015~2025 를 덮는다.
응답 원본 그대로(0o444, 있으면 건너뜀). 하루 한도(020) 걸리면 멈추고 같은 명령으로 이어받는다."""
import os, sys
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem")
from shared.data_library.kr_equity import store
from shared.data_library.kr_equity.store import Dart, DartQuota, log, path

YEARS = [2017, 2020, 2023, 2025]
d = Dart()
corps = store.universe_corps()
log(f"alotMatter 대상 회사 {len(corps)}개 × {len(YEARS)}", "alot_log.txt")
n = 0
try:
    for y in YEARS:
        for corp in sorted(corps):
            fp = path("raw", "dart", "alot_matter", corp, f"{y}.json")
            if os.path.exists(fp):
                continue
            q = {"corp_code": corp, "bsns_year": y, "reprt_code": "11011"}
            st, j = d.call("alotMatter", q)
            if j is None:
                continue
            d.save(fp, "alotMatter", q, j)
            n += 1
            if n % 500 == 0:
                log(f"  {n}건 (연도 {y})", "alot_log.txt")
except DartQuota as e:
    log(f"하루 한도 — 중단 ({n}건): {e}", "alot_log.txt")
    sys.exit(0)
log(f"완료 {n}건", "alot_log.txt")
