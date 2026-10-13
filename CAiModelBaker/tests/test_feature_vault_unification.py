"""
Test Feature Store and Data Vault Unification (피처 스토어와 데이터 볼트 일원화 검증)
- shared/config/symbol_relations.json 의 타겟 종목 상관관계가 /api/features/config 의 reference_symbols 로 자동 바인딩되는지 검증
- NQ, ES, 삼성전자, SK하이닉스 등 과거 캔들 데이터 가용성(ready: True) 검증
"""

import os
import sys
import pytest

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
BAKER_ROOT = os.path.join(PROJECT_ROOT, "CAiModelBaker")
if BAKER_ROOT not in sys.path:
    sys.path.insert(0, BAKER_ROOT)

from starlette.testclient import TestClient
from web_dashboard.main import app
from shared.data_library.symbol_relations_manager import SymbolRelationsManager


@pytest.fixture
def client():
    return TestClient(app)


def test_vault_relations_ssot_sync(client):
    """Data Vault의 symbol_relations.json이 /api/features/config에 자동 반영되는지 검증"""
    # 1. Data Vault 직접 조회
    target_sym = "069500"
    vault_refs = SymbolRelationsManager.get_relations_for_target(target_sym)
    assert len(vault_refs) >= 4, f"Data Vault에 최소 4개 참조 자산이 등록되어 있어야 합니다. (현재: {len(vault_refs)}개)"

    # 검증 대상은 관계에 실제로 등록된 참조 전부다 — 고정 심볼(과거 NQ/ES 직접 참조)을 박으면 참조 구성이
    # CFD 프록시(US_TECH_100/US_500)로 바뀐 순간 코드가 아니라 테스트가 깨진다.
    vault_symbols = {str(r["symbol"]).strip().upper() for r in vault_refs}

    # 2. /api/features/config 호출 (타겟 명시적 질의)
    response = client.get(f"/api/features/config?target={target_sym}")
    assert response.status_code == 200, f"API 응답 실패: {response.text}"

    data = response.json()
    ref_symbols = data.get("reference_symbols", [])
    ref_symbols_map = {str(r["symbol"]).strip().upper(): r for r in ref_symbols}

    # 3. Data Vault의 참조 자산이 Feature Store reference_symbols에 100% 바인딩되었는지 확인
    for sym in sorted(vault_symbols):
        assert sym in ref_symbols_map, f"자산 {sym}이 Feature Store reference_symbols에 자동으로 포함되어야 합니다."

    # 4. 참조 자산 데이터 상태(ready: True, rows > 1000) 검증
    ref_status = {str(k).strip().upper(): v for k, v in (data.get("reference_symbols_status") or {}).items()}
    for sym in sorted(vault_symbols):
        st = ref_status.get(sym)
        assert st is not None, f"자산 {sym}의 상태 정보(reference_symbols_status)가 존재해야 합니다."
        assert st.get("ready") is True, f"자산 {sym}의 데이터 준비 상태(ready)는 True여야 합니다: {st}"
        assert st.get("rows", 0) > 1000, f"자산 {sym}의 과거 캔들 행 수는 1,000행 이상이어야 합니다 (현재: {st.get('rows')})"


def test_symbols_relations_api_consistency(client):
    """GET /api/symbols/relations 와 /api/features/config 간의 데이터 무결성 검증"""
    res_rel = client.get("/api/symbols/relations?target=069500")
    assert res_rel.status_code == 200
    rel_data = res_rel.json()
    assert rel_data.get("status") == "ok"
    
    rel_symbols = {r["symbol"] for r in rel_data.get("references", [])}

    res_feat = client.get("/api/features/config?target=069500")
    assert res_feat.status_code == 200
    feat_data = res_feat.json()
    feat_symbols = {r["symbol"] for r in feat_data.get("reference_symbols", [])}

    # Data Vault에 설정된 모든 심볼이 Feature Store에도 포함되어야 함
    for s in rel_symbols:
        assert s in feat_symbols, f"Data Vault의 {s}가 Feature Store reference_symbols에 동기화되지 않았습니다."
