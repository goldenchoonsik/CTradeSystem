# -*- coding: utf-8 -*-
"""
Unit Test: Reference Symbol Crypto Detection & Download API Branching
가상자산(KRW-ETH 등) 타겟에서 참조 대장주(KRW-BTC)의 10분봉 캔들이 정상 감지(ready: True)되고
참조 대장주 다운로드 API가 올바른 파이프라인으로 분기되는지 검증합니다.
"""

import pytest
from starlette.testclient import TestClient
from CAiModelBaker.web_dashboard.main import app


def test_crypto_reference_symbol_detection():
    """KRW-ETH 타겟 10m 요청 시 등록된 가상자산 참조가 모두 ready=True 및 rows > 1000으로 응답하는지 검증.

    기대 심볼은 symbol_relations 에서 도출한다 — 참조 구성은 UI에서 바뀌는 데이터라
    특정 심볼(과거 KRW-BTC)을 박으면 구성이 바뀔 때 코드가 아니라 테스트가 깨진다.
    """
    from shared.data_library.symbol_relations_manager import SymbolRelationsManager
    expected = [str(r.get("symbol", "")).strip() for r in SymbolRelationsManager.get_relations_for_target("KRW-ETH")]
    expected = [s for s in expected if s]
    if not expected:
        pytest.skip("KRW-ETH 에 등록된 참조가 없어 검증할 대상이 없습니다.")

    client = TestClient(app)

    # 1. target=KRW-ETH, interval=10m 으로 /api/features/config 호출
    response = client.get("/api/features/config?target=KRW-ETH&interval=10m")
    assert response.status_code == 200, f"API 요청 실패: {response.text}"

    data = response.json()
    ref_status = data.get("reference_symbols_status", {})

    # 2. 등록된 참조마다 감지 상태 검증
    for sym in expected:
        st = ref_status.get(sym)
        assert st is not None, f"reference_symbols_status에 {sym}가 존재해야 합니다."
        assert st.get("ready") is True, f"{sym}의 ready 상태가 True여야 합니다. (실제: {st})"
        assert st.get("rows", 0) > 1000, f"{sym} 캔들 행 수가 1000행 이상이어야 합니다. (실제: {st.get('rows')})"

    # 3. interval 문자열 응답 검증
    assert data.get("interval") == "10m", f"interval 필드가 '10m'이어야 합니다. (실제: {data.get('interval')})"


def test_reference_symbols_download_api_crypto_branch():
    """가상자산 심볼 다운로드 요청 시 에러 없이 접수되는지 검증"""
    client = TestClient(app)
    
    # KRW-BTC 10m 다운로드 요청
    payload = {"symbol": "KRW-BTC", "interval": "10m"}
    response = client.post("/api/reference_symbols/download", json=payload)
    assert response.status_code == 200
    
    res_data = response.json()
    assert res_data.get("status") == "ok"
    assert "KRW-BTC" in res_data.get("message", "")
