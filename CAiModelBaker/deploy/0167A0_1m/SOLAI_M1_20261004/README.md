# AI Optimization Session: SOLAI_M1_20261004

- **생성 일시**: 2026-10-07 13:59:52
- **대상 자산**: 0167A0 (1m)
- **최종 챔피언**: Rank 1 (Trial 1)
- **챔피언 피처 (2개)**: `['target_adx', 'target_bb_kc_squeeze']`
- **Optuna Score**: 1.7670347188547584
- **Validation Metrics**: {"score": 1.2569941820188895, "trades": 117, "required_trades": 10.0, "win_rate": 53.84615384615385, "mdd": 4.957246020321833, "profit_factor": 0.8817043764589406, "profit_factor_raw": 0.8817043764589406, "total_return": -2.4161473835500047}
- **Final Test Metrics**: {"profit": 4.29, "trades": 114, "win_rate": 53.5, "mdd": 1.81, "profit_factor": 1.46}
- **Assessment**: PASSED

## 📂 Directory Structure

```
├── model/ (실전 배포용 챔피언 핵심 파일)
│   ├── model_1m.txt           (LightGBM 가중치 모델)
│   ├── best_params_1m.json    (최적 하이퍼파라미터 설정)
│   └── feature_spec.json       (기술 지표 전처리 명세서)
│
│   └── model_source.json       (출처: 이 챔피언을 만든 실행)
│
├── runs/ (자동연구 실행 기록 — 최근 10회 보관, 실행마다 폴더 1개)
│   └── 20261007_135657/  (챔피언 출처 실행)
│       ├── leaderboard.json · result.json · config_used.json
│       ├── train_run.log       (서버가 실행 중 직접 기록한 로그)
│       ├── trials/ · candidates/rank_N/
│       └── work/               (재학습 임시 산출물)
│
└── reports/
    └── test_trades.csv         (챔피언 테스트 구간 상세 거래 기록)
```

---
*본 폴더는 CAiModelBaker 엔진에 의해 자동 아카이빙된 최적화 세션 패키지입니다.*
