"""
shared/live_feed_prober.py
실시간 피드 준비성 검증기 (Live Feed Readiness Prober)

1인 운영 환경의 현실적 제약(동시 접속 제한, KIS API 세션 보호, 장외 시간 거짓 실패 방지)을
안전하게 방어하며, 각 종목의 실시간 시세 수신 가능 여부를 검증하는 공통 모듈.
"""

import os
import time
from datetime import datetime, time as dtime
from typing import Dict, Any, Optional
import zoneinfo

from shared.core.market_utils import is_crypto_symbol

KST = zoneinfo.ZoneInfo("Asia/Seoul")


class LiveFeedProber:
    """실시간 시세 피드 헬스체크 및 실전 매매 적합성 검증기"""

    @staticmethod
    def is_market_open(now: Optional[datetime] = None) -> bool:
        """한국 거래소(KRX) 정규 장운영 시간 여부 판정 (평일 09:00 ~ 15:30 KST)"""
        if now is None:
            now = datetime.now(KST)
        else:
            if now.tzinfo is None:
                now = now.replace(tzinfo=KST)
            else:
                now = now.astimezone(KST)

        # 1. 주말 및 KRX 공휴일/휴장일 확인
        try:
            from shared.core.market_calendar import is_trading_day
            if not is_trading_day(now, market="KRX"):
                return False
        except Exception:
            if now.weekday() >= 5:
                return False

        # 2. 정규 장 시간 (09:00:00 ~ 15:30:00)
        market_start = dtime(9, 0, 0)
        market_end = dtime(15, 30, 0)
        curr_time = now.time()

        return market_start <= curr_time <= market_end

    @staticmethod
    def is_ctrade_running() -> bool:
        """실전 CTrade 엔진이 현재 실행 중인지 감지 (동시 접속 세션 충돌 방지용)"""
        # 1. 락/플래그 파일 확인
        flag_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "state", "ctrade_running.flag"
        )
        if os.path.exists(flag_path):
            try:
                # 파일이 60초 이상 갱신되지 않았으면 비정상 종료로 간주
                mtime = os.path.getmtime(flag_path)
                if time.time() - mtime < 60:
                    return True
            except Exception:
                pass

        # 2. 프로세스 감지 (Linux / bash 환경)
        try:
            import subprocess
            res = subprocess.run(
                ["pgrep", "-f", "CTrade/main.py"],
                capture_output=True, text=True, timeout=1
            )
            if res.returncode == 0 and res.stdout.strip():
                return True
        except Exception:
            pass

        return False

    # 연속 이만큼의 봉을 못 받으면 "수신 중"이라고 말하지 않는다.
    # (도메인 상수가 아니라 '몇 봉을 놓치면 끊긴 것으로 볼 것인가' 라는 운영 허용치다)
    FEED_STALE_BARS = 3

    @staticmethod
    def _artifact_freshness(symbol: str):
        """그 종목 정규화 산출물의 **가장 최근 데이터 시각**과 봉 길이.

        파일을 파싱하지 않고 매니페스트(`integrity.last_timestamp`)만 읽는다.
        수신 여부를 말하려면 근거가 있어야 하고, 이것이 이 모듈이 접근할 수 있는 유일한 실측치다.
        """
        import glob
        import json
        from shared.data_library import paths as dpaths

        pattern = os.path.join(dpaths.DATA_VAULT_ROOT, "normalized", "*", f"{symbol}_*.parquet.manifest.json")
        newest, newest_interval = None, None
        for mf in glob.glob(pattern):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                last = (meta.get("integrity") or {}).get("last_timestamp")
                if not last:
                    continue
                ts = datetime.fromisoformat(str(last))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=KST)
            except (OSError, ValueError, TypeError):
                continue
            if newest is None or ts > newest:
                newest = ts
                newest_interval = str(meta.get("asset_type", "")).replace("candles_", "") or None
        return newest, newest_interval

    @classmethod
    def _is_krx(cls, symbol: str, market_type: Optional[str]) -> bool:
        """KRX 정규장 시간 규칙을 적용할 종목인지 — 판정 근거는 종목 원장이다."""
        mt = str(market_type or "").upper()
        if not mt:
            try:
                from shared.config.config_manager import get_symbol_info
                mt = str(get_symbol_info(symbol).get("market_type", "")).upper()
            except Exception:
                mt = ""
        return mt.startswith("KRX") or mt.startswith("DOMESTIC")

    @classmethod
    def probe_symbol(cls, symbol: str, market_type: Optional[str] = None) -> Dict[str, Any]:
        """종목의 실시간 피드 준비 상태를 **측정해서** 판정한다.

        [T-6] 종전에는 모든 분기가 고정 응답이었다. 심볼 이름 목록을 코드에 박아 두고
        확인 없이 "연동 정상 / LIVE_STREAMING / latency 15ms" 를 돌려주었기 때문에,
        실시간 구독 코드가 아예 없는 자산까지 UI 에 초록 배지로 표시됐다.

        지금은 세 가지를 잰다 — 어느 것도 심볼 이름에 의존하지 않는다:
          1. 수집처 원장이 실시간 연결을 선언했는가 (`live_connection`)
          2. 실전 런타임이 그 수집처를 **구현했는가** (커넥터/파서 모듈 탐색)
          3. 실제로 데이터가 들어오고 있는가 (정규화 매니페스트의 마지막 시각)

        측정할 수 없는 값(네트워크 지연 등)은 지어내지 않고 비워 둔다.
        """
        from shared.data_library.live_feed_contract import implemented_live_providers
        from shared.data_library.source_mapping_manager import SourceMappingManager

        # 원장 심볼 그대로가 산출물 파일 이름이다 (KRW-BTC_1m.parquet 등).
        # 접두어를 떼면 그 종목의 데이터를 못 찾아 '수신 이력 없음' 으로 잘못 판정한다.
        ledger_sym = SourceMappingManager.clean_symbol(symbol)
        clean_sym = ledger_sym
        m_open = cls.is_market_open()
        c_running = cls.is_ctrade_running()
        live_conn = SourceMappingManager.get_live_connection(symbol) or {}
        provider = str(live_conn.get("provider", "")).upper()

        last_ts, interval = cls._artifact_freshness(ledger_sym)
        age_sec = int((datetime.now(KST) - last_ts).total_seconds()) if last_ts else None

        def result(readiness, grade, status_code, badge_text, message, **extra):
            out = {
                "symbol": symbol,
                "readiness": readiness,
                "grade": grade,
                "status_code": status_code,
                "badge_text": badge_text,
                "message": message,
                "is_market_open": m_open,
                "ctrade_running": c_running,
                "live_connection": live_conn,
                "provider": provider or None,
                "data_age_sec": age_sec,
                "last_data_at": last_ts.strftime("%Y-%m-%d %H:%M:%S") if last_ts else None,
                "latency_ms": None,          # 측정하지 않는다 — 지어내지 않는다
            }
            out.update(extra)
            return out

        # 1. 실시간 금지 수집처 (수집 전용)
        if provider == "CREON":
            return result("NO_DATA", "CONNECTION_FAILED", "UNSUPPORTED", "CREON 실시간 금지",
                          "대신증권(CREON)은 과거 틱 수집 전용이며 실시간 피드로 쓸 수 없습니다 (규칙 6).")

        # 2. 원장에 실시간 연결 선언이 없음
        if not provider or live_conn.get("is_active") is False:
            return result("NO_DATA", "CONNECTION_FAILED", "NOT_CONFIGURED", "실시간 연결 미선언",
                          f"{clean_sym} 에 활성 live_connection 선언이 없습니다. "
                          "수집처 원장(source_mappings.json)에 먼저 선언해야 합니다.")

        # 3. 선언은 있으나 실전 런타임에 그 수집처 구현이 없음
        #    (연구용 데이터는 쌓이지만 실전에서는 그 피처를 만들 수 없는 상태)
        implemented = implemented_live_providers()
        if provider not in implemented:
            return result("RESEARCH_ONLY", "RESEARCH_FEED", "NO_RUNTIME_FEED", "연구 전용 (실시간 구현 없음)",
                          f"{clean_sym} 은 {provider} 실시간 연결을 선언했지만 실전 런타임에 {provider} 구현이 없습니다. "
                          f"과거 데이터 수집은 되지만 실전 매매에는 쓸 수 없습니다. "
                          f"(구현된 수집처: {', '.join(sorted(implemented)) or '없음'})")

        # 4. 실전 엔진이 돌고 있으면 세션 충돌을 피하려고 별도 접속을 하지 않는다 — 확인 보류
        if c_running:
            return result("LIVE_READY", "LIVE_STREAMING", "CTRADE_SAFE_READ", "CTrade 실전 수신 중",
                          "실전 CTrade 엔진이 실행 중이라 세션 충돌 방지를 위해 별도 접속 점검을 하지 않았습니다. "
                          + (f"마지막 데이터: {age_sec}초 전." if age_sec is not None else "저장된 데이터 이력이 없습니다."))

        # 5. KRX 종목은 장 마감이면 수신이 없는 것이 정상이다
        if cls._is_krx(clean_sym, market_type) and not m_open:
            return result("LIVE_READY", "STANDBY_AUTH_OK", "MARKET_CLOSED_AUTH_OK", "장외 대기",
                          f"정규장 마감 상태입니다. {provider} 실시간 구현이 있어 장 시작 시 수신됩니다."
                          + (f" (마지막 데이터: {last_ts:%Y-%m-%d %H:%M})" if last_ts else ""))

        # 6. 남은 판단은 '실제로 최근 데이터가 들어왔는가' 하나뿐이다
        if last_ts is None:
            # 실시간 전용 피더(예: Pyth 매크로)는 저장 산출물을 남기지 않는다.
            # 구현이 있는데 저장 이력이 없는 것은 '실패'가 아니라 '확인할 근거가 없음'이다.
            return result("RESEARCH_ONLY", "STANDBY_AUTH_OK", "NO_DATA_TRACE", "확인 근거 없음",
                          f"{provider} 실시간 구현은 있으나 {clean_sym} 의 저장된 데이터가 없어 "
                          "수신 여부를 확인할 수 없습니다 (실시간 전용 피더는 파일을 남기지 않습니다).")

        from shared.config.market_policies import bar_duration
        try:
            bar_sec = bar_duration(interval or "1m").total_seconds()
        except Exception:
            bar_sec = 60.0
        stale_after = bar_sec * cls.FEED_STALE_BARS

        if age_sec <= stale_after:
            return result("LIVE_READY", "LIVE_STREAMING", "LIVE_OK", f"{provider} 수신 중",
                          f"데이터가 계속 들어오고 있습니다 — 마지막 {age_sec}초 전 "
                          f"({interval or '?'} 봉 기준 정상 범위).")

        # 여기서 볼 수 있는 것은 **저장된 데이터**뿐이다. 실시간 스트림이 실제로 흐르는지는
        # 수집기나 실전 엔진이 돌 때만 관측된다 — 관측하지 못한 것을 '정상'이라고 말하지 않는다.
        return result("RESEARCH_ONLY", "STANDBY_AUTH_OK", "FEED_STALE", "대기 (최신 수신 없음)",
                      f"{provider} 실시간 구현은 있습니다. 다만 저장된 데이터의 마지막 시각이 "
                      f"{age_sec:,}초 전이라 ({interval or '?'} 봉 {cls.FEED_STALE_BARS}개분인 "
                      f"{int(stale_after):,}초 초과) 지금 수신 중인지는 확인되지 않습니다. "
                      "수집기/엔진이 실행 중일 때 다시 점검하세요.")

    @classmethod
    def probe_all_and_save_diagnostic(cls) -> Dict[str, Any]:
        """
        모든 활성 자산에 대해 실시간 연결 상태를 비차단(Non-blocking)으로 진단하고
        logs/live_feed_diagnostic.json에 원자적 저장
        """
        import json
        import tempfile
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library.paths import PROJECT_ROOT

        active_mappings = SourceMappingManager.get_active_mappings()
        probes = []
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

        for sym, meta in active_mappings.items():
            try:
                res = cls.probe_symbol(sym, market_type=meta.get("category"))
                probes.append(res)
            except Exception as e:
                probes.append({
                    "symbol": sym,
                    "readiness": "NO_DATA",
                    "grade": "CONNECTION_FAILED",
                    "status_code": "CONNECTION_ERROR",
                    "badge_text": "진단 오류",
                    "message": str(e),
                    "is_market_open": cls.is_market_open(),
                    "ctrade_running": cls.is_ctrade_running(),
                    "latency_ms": 0
                })

        report = {
            "timestamp": now_str,
            "total_count": len(probes),
            "streaming_count": sum(1 for p in probes if p.get("grade") == "LIVE_STREAMING"),
            "standby_count": sum(1 for p in probes if p.get("grade") == "STANDBY_AUTH_OK"),
            "failed_count": sum(1 for p in probes if p.get("grade") == "CONNECTION_FAILED"),
            "items": probes
        }

        # 비차단 원자적 저장 (logs 디렉토리)
        logs_dir = os.path.join(PROJECT_ROOT, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        report_path = os.path.join(logs_dir, "live_feed_diagnostic.json")

        try:
            with tempfile.NamedTemporaryFile("w", dir=logs_dir, delete=False, encoding="utf-8", suffix=".tmp") as tf:
                temp_name = tf.name
                json.dump(report, tf, indent=2, ensure_ascii=False)
            os.replace(temp_name, report_path)
            report["saved_path"] = report_path
        except Exception as e:
            report["save_error"] = str(e)

        return report

