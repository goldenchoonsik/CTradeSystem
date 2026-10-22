"""전국 메모리 반도체(HS 854232) 월 수출 — 시군구 API 를 전 시도 합산.

품목별(전국) 서비스는 키 미등록이라, 이미 쓰던 시군구 API 캐시 클라이언트를 재사용한다.
원문 캐시: DataVault/staging/customs_export/raw/854232/{sido}_{yyyy}.xml
출력: DataVault/staging/samsung_cycle/customs_854232_monthly.csv (ym, exp_usd_k, n_rows)
  .venv/bin/python docs/research/20261022_samsung_single/fetch_customs.py
"""
import os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../20261014_customs_export"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
import customs_api  # noqa: E402
from shared.data_library.paths import data_vault_path  # noqa: E402

HS = "854232"
SIDO = ["11", "26", "27", "28", "29", "30", "31", "36", "41", "43", "44", "46", "47", "48", "50", "51", "52"]


def main():
    tot = collections.defaultdict(float)
    cnt = collections.Counter()
    for y in range(2000, 2027):
        for s in SIDO:
            for r in customs_api.rows(HS, s, y):
                ym = r.get("priodTitle") or r.get("year") or ""
                ym = ym.replace(".", "").replace("-", "")[:6]
                if not ym.isdigit() or len(ym) != 6:
                    continue
                tot[ym] += r.get("expUsdAmt", 0.0)
                cnt[ym] += 1
        print(y, sum(v for k, v in tot.items() if k.startswith(str(y))), flush=True)
    out = data_vault_path("staging", "samsung_cycle", "customs_854232_monthly.csv")
    with open(out, "w") as f:
        f.write("ym,exp_usd_k,n_rows\n")
        for ym in sorted(tot):
            f.write(f"{ym},{tot[ym]:.0f},{cnt[ym]}\n")
    print("saved", out)


if __name__ == "__main__":
    main()
