"""
바이낸스 펀딩비 히스토리를 스냅샷 CSV에 병합하는 스크립트

⚠️ [RETIRED — Gold 단일 writer 계약 (T-2, 2026-09-15)]
================================================================================
본 스크립트는 **1회성 과거 마이그레이션 도구**이며, 로직이 이미 배치 파이프라인에
통합되어 있어 **기본 실행이 차단**됩니다 (실행하려면 ``--force`` 명시 필요).

  · 대체 경로: ``CAiModelBaker/core/data_manager.py::_download_binance_funding_rate_range``
    (data_manager.py:581 주석 — "scripts/add_funding_rate.py 로직을 파이프라인 내부로 통합")
  · COIN/INTERVAL 이 ``BTC`` / ``30m`` 로 하드코딩되어 있고, 하드코딩 경로로
    학습 데이터셋(``data_snapshot_30m.csv``)을 **직접 덮어씁니다**.
    → Gold 계층 단일 writer 계약 위반 소지 (T-2 / ARCHITECTURE.md Gold 계약 참조)
  · 초기 구간을 ``bfill()`` 로 역방향 채움 — 문서 정책('bfill 전면 금지, ffill 만 허용')과 상충

1. 바이낸스 fapi 펀딩비 히스토리 수집 (8시간 간격)
2. 스냅샷 30m 봉 타임스탬프에 ffill/bfill로 병합
3. 'binance_funding_rate' 컬럼 추가 저장
"""
import sys
import os
import time
import json
import urllib.request
import pandas as pd

# 프로젝트 루트 경로 설정
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

COIN = "BTC"
INTERVAL = "30m"
SNAPSHOT_PATH = os.path.join(ROOT, "CAiModelBaker", "data", COIN, f"data_snapshot_{INTERVAL}.csv")

FUNDING_API = "https://fapi.binance.com/fapi/v1/fundingRate"
LIMIT = 1000


def fetch_funding_history(symbol="BTCUSDT", start_ms=None, end_ms=None):
    """바이낸스 펀딩비 전체 히스토리 수집 (페이징)"""
    all_rows = []
    current_start = start_ms

    while True:
        params = f"symbol={symbol}&limit={LIMIT}"
        if current_start:
            params += f"&startTime={current_start}"
        if end_ms:
            params += f"&endTime={end_ms}"

        url = f"{FUNDING_API}?{params}"
        print(f"  ↳ 수집 중: {url[:120]}...")

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))

        if not rows:
            break

        all_rows.extend(rows)

        # 마지막 fundingTime 기준으로 다음 페이지
        last_time = rows[-1]["fundingTime"]
        if len(rows) < LIMIT:
            break
        if end_ms and last_time >= end_ms:
            break

        current_start = last_time + 1
        time.sleep(0.3)  # rate limit 방어

    return all_rows


def main(force: bool = False):
    # [T-2] Gold 단일 writer 계약 — 학습 데이터셋 직접 덮어쓰기 차단
    if not force:
        print("🛑 [RETIRED] 이 스크립트는 학습 데이터셋을 직접 덮어쓰므로 실행이 차단되었습니다.")
        print(f"   대상: {SNAPSHOT_PATH}")
        print("   · 펀딩비 로직은 배치 파이프라인에 통합되어 있습니다")
        print("     (data_manager.py::_download_binance_funding_rate_range).")
        print("   · 정상 경로: 웹 대시보드에서 데이터셋 빌드(run_dataset_build_pipeline)를 실행하세요.")
        print("   · 정말로 재실행하려면: python scripts/add_funding_rate.py --force")
        return 1

    # 1. 스냅샷 로드
    print(f"📂 스냅샷 로드: {SNAPSHOT_PATH}")
    df = pd.read_csv(SNAPSHOT_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    print(f"   → 총 {len(df):,}행 | {df['timestamp'].min()} ~ {df['timestamp'].max()}")

    # 2. 타임스탬프 범위 계산 (ms)
    start_ts = int(df["timestamp"].min().timestamp() * 1000)
    end_ts = int(df["timestamp"].max().timestamp() * 1000)
    print(f"🕐 펀딩비 수집 범위: {df['timestamp'].min()} ~ {df['timestamp'].max()}")

    # 3. 펀딩비 히스토리 수집
    print("📡 바이낸스 펀딩비 API 호출 중...")
    funding_rows = fetch_funding_history("BTCUSDT", start_ms=start_ts, end_ms=end_ts)
    print(f"   → 펀딩비 {len(funding_rows):,}건 수신")

    if not funding_rows:
        print("❌ 펀딩비 데이터 없음")
        return

    funding_df = pd.DataFrame(funding_rows)
    # fundingTime (ms) → 초 단위 정수로 반올림 후 datetime 변환 (dtype 통일)
    funding_df["timestamp"] = pd.to_datetime(
        funding_df["fundingTime"].astype("int64") // 1000,
        unit="s"
    )
    funding_df["binance_funding_rate"] = funding_df["fundingRate"].astype(float)
    funding_df = funding_df[["timestamp", "binance_funding_rate"]].sort_values("timestamp").drop_duplicates("timestamp")
    funding_df["timestamp"] = funding_df["timestamp"].astype("datetime64[us]")

    print(f"   → 펀딩비 범위: {funding_df['timestamp'].min()} ~ {funding_df['timestamp'].max()}")

    # 4. 스냅샷 30m 봉에 병합 (ffill 후 bfill — look-ahead 방지)
    print("🔗 스냅샷에 병합 중...")
    df = pd.merge_asof(
        df.sort_values("timestamp"),
        funding_df,
        on="timestamp",
        direction="backward"
    )

    # 첫 구간에 대해 bfill (2022-05 초기 펀딩비 없을 때 대비)
    df["binance_funding_rate"] = df["binance_funding_rate"].bfill().fillna(0.0)

    # 5. 컬럼 순서 보장 (마지막에 추가)
    if "binance_funding_rate" not in df.columns:
        print("❌ 병합 실패 — 컬럼 없음")
        return

    # 6. 저장
    print(f"💾 저장 중... {SNAPSHOT_PATH}")
    df.to_csv(SNAPSHOT_PATH, index=False)
    print(f"✅ 완료! 총 {len(df):,}행 | 컬럼 수: {len(df.columns)}")
    print(f"   펀딩비 통계: min={df['binance_funding_rate'].min():.6f}, max={df['binance_funding_rate'].max():.6f}, mean={df['binance_funding_rate'].mean():.6f}")
    return 0


if __name__ == "__main__":
    # [T-2] 기본 실행 차단 — 학습 데이터셋을 덮어쓰려면 --force 명시 필요
    sys.exit(main(force="--force" in sys.argv[1:]))