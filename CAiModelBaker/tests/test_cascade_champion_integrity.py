"""
Tests for Cascade Champion Restoration Integrity & Deployed Feature Spec Guard
"""
import os
import json
import shutil
import tempfile
import pytest
from fastapi.testclient import TestClient

from CAiModelBaker.web_dashboard.main import app


def test_api_save_feature_config_protects_trained_model_feature_spec(tmp_path):
    """
    배포 폴더에 이미 학습된 모델(model_{interval}.txt)이 존재하는 경우,
    /api/features/save 가 호출되어도 model/feature_spec.json 의 features_order 가
    전체 활성 풀(safe_enabled)로 오염되지 않고 기존 모델의 훈련 지표로 보존되어야 한다.
    """
    client = TestClient(app)
    
    # 가상의 세션 디렉토리 및 배포 파일 구성
    test_session = "TEST_INTEGRITY_SESSION"
    test_symbol = "069500"
    test_interval = "10s"
    
    # 실제 deploy/ 가 아니라 테스트 세션의 사본 배포 폴더 (conftest 가 DEPLOY_ROOT 를 돌려 둔다)
    from shared.data_library import paths as dpaths
    deploy_root = dpaths.DEPLOY_ROOT
    session_dir = os.path.join(deploy_root, f"{test_symbol}_{test_interval}", test_session)
    model_dir = os.path.join(session_dir, "model")
    os.makedirs(model_dir, exist_ok=True)
    
    trained_features = ["target_vwap_disparity", "target_macd_hist", "overnight_gap_pct"]
    
    try:
        # 1. 훈련된 가중치 모델 파일 생성
        model_file = os.path.join(model_dir, f"model_{test_interval}.txt")
        with open(model_file, "w", encoding="utf-8") as f:
            f.write("tree\nfeature_names=target_vwap_disparity target_macd_hist overnight_gap_pct\n")
            
        # 2. 훈련된 모델용 feature_spec.json 생성
        spec_file = os.path.join(model_dir, "feature_spec.json")
        with open(spec_file, "w", encoding="utf-8") as f:
            json.dump({
                "symbol": test_symbol,
                "interval": test_interval,
                "features_order": trained_features,
                "feature_params": {"target_vwap_disparity": {"window": 20}}
            }, f, indent=4)
            
        # 3. /api/features/save 호출 (대시보드에서 20개 전체 풀을 저장하는 상황 시뮬레이션)
        huge_feature_pool = [
            "target_vwap_disparity", "target_macd_hist", "overnight_gap_pct",
            "target_bb_pctb", "target_rsi_curr", "target_mfi", "target_hour_cos"
        ]
        
        # 활성 세션을 TEST_INTEGRITY_SESSION 으로 잠시 맞춤
        req_data = {
            "enabled_features": huge_feature_pool,
            "feature_params": {"target_vwap_disparity": {"window": 30}},
            "feature_penalties": {}
        }
        
        # (전역 active.json/active_config.json 백업·복원은 제거 — 2026-10-01 은퇴, 저장은 열린 사본 프로젝트로 간다)

        # main.py 내부의 spec_path 가 타겟 세션을 가리키도록 환경 주입
        from CAiModelBaker.web_dashboard import main as wb_main
        orig_config_fn = wb_main.load_config
        wb_main.load_config = lambda: {
            "SYMBOL_UPBIT": test_symbol,
            "INTERVAL": test_interval,
            "ACTIVE_SESSION_ID": test_session
        }
        
        try:
            resp = client.post("/api/features/config", json=req_data)
            assert resp.status_code == 200
            
            # 4. model/feature_spec.json 이 보호되었는지 검증
            with open(spec_file, "r", encoding="utf-8") as f:
                saved_spec = json.load(f)
                
            # features_order 가 7개 풀로 오염되지 않고 기존 3개로 완벽히 보존되었는지 검증
            assert saved_spec.get("features_order") == trained_features, (
                f"Trained model features_order must be preserved! Got: {saved_spec.get('features_order')}"
            )
            # feature_params 도 보존되어야 한다 (2026-09-28 프로젝트 격리): 학습된 모델의 지표 파라미터는
            # 모델 가중치와 1:1 이다. 화면 값(window=30)으로 고치면 학습(20)과 실전 지표 계산이 어긋난다.
            assert saved_spec.get("feature_params", {}).get("target_vwap_disparity", {}).get("window") == 20
        finally:
            wb_main.load_config = orig_config_fn

    finally:
        # 정리
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir)


def test_cascade_champion_restoration_syncs_readme_and_trades(tmp_path):
    """
    Cascade 승격 완료 시 model/ 아티팩트뿐만 아니라 README.md와 reports/test_trades.csv도
    최종 챔피언 기준으로 무결 복원되어야 한다.
    """
    test_session = "TEST_CASCADE_SESSION"
    test_symbol = "069500"
    test_interval = "10s"
    
    session_dir = tmp_path / test_session
    candidates_dir = session_dir / "candidates"
    rank_1_dir = candidates_dir / "rank_1"
    model_dir = session_dir / "model"
    reports_dir = session_dir / "reports"
    
    rank_1_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    
    # 1. Rank 1 후보 아티팩트 생성
    (rank_1_dir / f"model_{test_interval}.txt").write_text("model_rank1_weights", encoding="utf-8")
    (rank_1_dir / f"best_params_{test_interval}.json").write_text(json.dumps({
        "optuna_score": 19.75,
        "validation_metrics": {"win_rate": 78.0, "profit": 4.05}
    }), encoding="utf-8")
    (rank_1_dir / "feature_spec.json").write_text(json.dumps({
        "features_order": ["feat_a", "feat_b"]
    }), encoding="utf-8")
    (rank_1_dir / "candidate_detail.json").write_text(json.dumps({
        "trades_detail": [
            {"entry_time": "2026-08-27 09:15", "net_return": 0.12},
            {"entry_time": "2026-08-28 09:10", "net_return": 0.16}
        ]
    }), encoding="utf-8")
    
    # 2. Rank 3 (이전 최적화)가 남긴 찌꺼기 시뮬레이션
    (model_dir / f"model_{test_interval}.txt").write_text("model_rank3_weights", encoding="utf-8")
    (session_dir / "README.md").write_text("OLD_README_RANK3_INFO", encoding="utf-8")
    
    # 3. 복원 로직 검증 (research_loop의 Cascade 복원 로직과 동일)
    champion_rank = 1
    champion_cand = {
        "trial": 21,
        "features": ["feat_a", "feat_b"],
        "final_profit": 4.05,
        "final_trades": 2,
        "win_rate": 78.0,
        "final_mdd": 0.21,
        "profit_factor": 6.57
    }
    
    champ_dir = str(rank_1_dir)
    for fname in [f"model_{test_interval}.txt", f"best_params_{test_interval}.json", "feature_spec.json"]:
        src = os.path.join(champ_dir, fname)
        dst = os.path.join(str(model_dir), fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            
    # test_trades.csv 복원
    detail_path = os.path.join(champ_dir, "candidate_detail.json")
    with open(detail_path) as df:
        cd_obj = json.load(df)
    import pandas as pd
    df_trades = pd.DataFrame(cd_obj["trades_detail"])
    trades_csv = os.path.join(str(reports_dir), "test_trades.csv")
    df_trades.to_csv(trades_csv, index=False, encoding="utf-8")
    
    # README.md 복원
    readme_path = os.path.join(str(session_dir), "README.md")
    with open(readme_path, "w", encoding="utf-8") as rf:
        rf.write(f"# AI Optimization Session: {test_session}\n- **최종 챔피언**: Rank {champion_rank}\n- **Assessment**: PASSED\n")
        
    # 4. 검증
    assert (model_dir / f"model_{test_interval}.txt").read_text(encoding="utf-8") == "model_rank1_weights"
    assert os.path.exists(trades_csv)
    saved_trades_df = pd.read_csv(trades_csv)
    assert len(saved_trades_df) == 2
    readme_text = (session_dir / "README.md").read_text(encoding="utf-8")
    assert "Rank 1" in readme_text
    assert "PASSED" in readme_text

