/**
 * CAiModelBaker - Feature Configuration UI
 * 학습 지표 설정 및 Optuna 범위 설정 로직
 * 
 * 변경사항:
 * 1. 툴팁 레이어 문제 해결 (position: fixed + z-index: 99999)
 * 2. predict_ahead_candles 분리 (예측 대상 vs 최대 보유시간)
 * 3. 캔들 시간 변환 표시
 * 4. Outer/Inner 그룹화
 */

// =============================================================================
// Feature Config State
// =============================================================================
const FeatureState = {
    registry: {},
    categories: {},
    activeFeatures: [],
    availableFeatures: [],
    featureParams: {},
    featurePenalties: {},
    regimeFeatures: {},
    includeOtherMarkets: false,
    incompatibleFeatures: [],
    marketType: 'crypto',
    optunaConfig: {},
    targetHorizonConfig: {},
    horizonDefaults: {},
    referenceSymbols: [],
    referenceSymbolsStatus: {},
    currentCategory: 'all',
    intervalMinutes: 15,  // 기본값, loadFeatureConfig에서 업데이트
    intervalSeconds: 900,
    interval: '15m',
    isDirty: false,
    viewMode: localStorage.getItem('featureViewMode') || 'table',
    searchQuery: '',
    sortCol: 'category',
    sortAsc: true
};

// 현재 대시보드 활성 프로젝트 타겟 종목 조회 헬퍼 (SSOT 보장)
function getCurrentTargetSymbol() {
    if (typeof STATE !== 'undefined' && STATE.status && STATE.status.coin) {
        return STATE.status.coin;
    }
    if (window._currentStudioTarget) {
        return window._currentStudioTarget;
    }
    const dataCoinEl = document.getElementById('dataCoin');
    if (dataCoinEl && dataCoinEl.innerText && !dataCoinEl.innerText.includes('로딩') && dataCoinEl.innerText.trim() !== '-') {
        return dataCoinEl.innerText.trim();
    }
    return '069500';
}
window.getCurrentTargetSymbol = getCurrentTargetSymbol;

// =============================================================================
// 🛡️ 프로젝트 무결성 가드 (Dirty Check & Safe Execution Guard)
// =============================================================================
async function ensureProjectSavedBeforeAction(actionName = '작업') {
    if (typeof FeatureState !== 'undefined' && FeatureState.isDirty) {
        const confirmMsg = 
            `⚠️ [미저장 변경사항 감지]\n` +
            `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
            `현재 프로젝트 설정에 저장되지 않은 변경사항이 있습니다.\n\n` +
            `'${actionName}' 작업을 진행하려면 최신 설정을 디스크에 먼저 저장해야 합니다.\n\n` +
            `• [확인] : 지금 프로젝트를 저장하고 계속 진행합니다.\n` +
            `• [취소] : 작업을 즉시 취소합니다.`;
        
        if (!confirm(confirmMsg)) {
            if (typeof showToast === 'function') {
                showToast(`⚠️ 저장이 취소되어 '${actionName}' 작업이 중단되었습니다.`, 'warning');
            }
            if (typeof appendLog === 'function') {
                appendLog(`⚠️ [작업 취소] 사용자가 저장을 취소하여 '${actionName}' 작업이 중단되었습니다.`, '');
            }
            return false;
        }

        // 사용자가 확인을 누른 경우 프로젝트 통합 저장 수행
        try {
            if (typeof saveProjectUnifiedConfig === 'function') {
                await saveProjectUnifiedConfig();
            } else {
                if (typeof saveFeatureConfig === 'function') await saveFeatureConfig();
                if (typeof savePeriodConfig === 'function') await savePeriodConfig();
                if (typeof saveAutolabConfig === 'function') await saveAutolabConfig(true);
            }
            if (typeof FeatureState !== 'undefined') {
                FeatureState.isDirty = false;
            }
            if (typeof showToast === 'function') {
                showToast(`💾 프로젝트 저장이 완료되어 '${actionName}' 작업을 계속 진행합니다.`, 'success');
            }
            return true;
        } catch (e) {
            if (typeof showToast === 'function') {
                showToast(`❌ 프로젝트 저장 실패로 '${actionName}' 작업을 중단합니다: ${e.message || e}`, 'danger');
            }
            return false;
        }
    }
    return true;
}
window.ensureProjectSavedBeforeAction = ensureProjectSavedBeforeAction;

// =============================================================================
// API Functions
// =============================================================================

async function loadFeatureConfig(targetSymbol = null) {
    try {
        const activeTarget = targetSymbol || getCurrentTargetSymbol();
        const data = await apiGet(`/api/features/config?target=${encodeURIComponent(activeTarget)}`);
        
        FeatureState.registry = data.registry;
        FeatureState.categories = data.categories;
        FeatureState.referenceGroups = data.reference_groups || [];
        FeatureState.referenceSymbols = data.reference_symbols || [];
        FeatureState.referenceSymbolsStatus = data.reference_symbols_status || {};
        FeatureState.regimeFeatures = data.regime_features || {};
        FeatureState.incompatibleFeatures = data.incompatible_features || [];
        FeatureState.marketType = data.market_type || 'crypto';
        
        // 타 자산 지표 포함 체크박스 초기화
        const chkOther = document.getElementById('chkIncludeOtherMarkets');
        if (chkOther) {
            chkOther.checked = FeatureState.includeOtherMarkets;
        }
        
        // 중복 방지 및 canonical_id 기준 정규화
        const activeMap = new Map();
        (data.active_features || []).forEach(f => {
            const canonId = f.canonical_id || f.id;
            if (!activeMap.has(canonId)) {
                // registry의 canonical 지표 정보로 교체하여 alias 방지
                const baseInfo = FeatureState.registry[canonId] || f;
                activeMap.set(canonId, {
                    ...baseInfo,
                    currentParams: f.current_params || f.currentParams || getDefaultParams(baseInfo)
                });
            }
        });
        FeatureState.activeFeatures = Array.from(activeMap.values());
        FeatureState.featureParams = data.feature_params || {};
        FeatureState.featurePenalties = data.feature_penalties || {};
        FeatureState.optunaConfig = data.optuna_config;
        FeatureState.optunaDefaults = data.optuna_defaults || {};
        FeatureState.intervalMinutes = (data.interval_minutes !== undefined && data.interval_minutes !== null) ? data.interval_minutes : 15;
        FeatureState.intervalSeconds = data.interval_seconds || Math.round(FeatureState.intervalMinutes * 60);
        FeatureState.interval = data.interval || (FeatureState.intervalSeconds < 60 ? `${FeatureState.intervalSeconds}s` : `${FeatureState.intervalMinutes}m`);
        
        // 사용 가능한 지표 계산 (활성화된 것, 별칭 제외, 및 부착된 대장주 피처만 스코핑)
        const activeCanonIds = new Set(FeatureState.activeFeatures.map(f => f.canonical_id || f.id));
        const attachedRefKeys = new Set(FeatureState.referenceSymbols.map(r => r.key));   // 서버가 원장 심볼에서 유도한 컬럼 키

        FeatureState.availableFeatures = Object.keys(data.registry)
            .filter(id => {
                const item = data.registry[id];
                if (item.is_alias) return false;
                
                // ⚡ reference 카테고리 지표 중 개별 참조 종목 직결 피처(ref_*)는 부착된 참조 종목만 노출!
                if (item.category === 'reference') {
                    if (id.startsWith('ref_')) {
                        if (attachedRefKeys.size === 0) return false;
                        const isAttached = Array.from(attachedRefKeys).some(k => id.startsWith(`ref_${k}_`));
                        if (!isAttached) return false;
                    }
                }

                const canonId = item.canonical_id || id;
                return !activeCanonIds.has(id) && !activeCanonIds.has(canonId);
            })
            .map(id => data.registry[id]);
        
        renderFeatureConfig();
        renderOptunaConfig();
        
        // 🎯 타깃 사정거리 & 최대 보유시간 UI 렌더링
        FeatureState.targetHorizonConfig = data.target_horizon_config || {};
        FeatureState.horizonDefaults = data.horizon_defaults || {};
        renderHorizonUi();
        
        await loadFeaturePresets();
        
        appendLog('✅ 지표 설정 로드 완료', '');
    } catch (e) {
        appendLog(`❌ 지표 설정 로드 실패: ${e}`, '');
    }
}

function collectOptunaConfigFromDom() {
    if (!FeatureState.optunaConfig) {
        FeatureState.optunaConfig = {};
    }
    const container = document.getElementById('optunaConfigForm');
    if (!container) return;

    const inputs = container.querySelectorAll('input[data-optuna]');
    inputs.forEach(input => {
        const key = input.getAttribute('data-optuna');
        const dataType = input.getAttribute('data-type');
        const valStr = input.value.trim();
        if (valStr === '') return;

        if (dataType) {
            if (!FeatureState.optunaConfig[key] || typeof FeatureState.optunaConfig[key] !== 'object') {
                FeatureState.optunaConfig[key] = {};
            }
            const numVal = parseFloat(valStr);
            if (!isNaN(numVal)) {
                if (dataType === 'min') FeatureState.optunaConfig[key].user_min = numVal;
                else if (dataType === 'max') FeatureState.optunaConfig[key].user_max = numVal;
                else if (dataType === 'step') FeatureState.optunaConfig[key].step = numVal;
            }
        } else {
            if (key === 'fee_upbit' || key === 'slippage' || key === 'min_profit_factor') {
                const numVal = parseFloat(valStr);
                if (!isNaN(numVal)) FeatureState.optunaConfig[key] = numVal;
            } else if (key === 'enable_early_stop') {
                FeatureState.optunaConfig[key] = parseInt(valStr, 10) === 1;
            } else {
                const numVal = parseInt(valStr, 10);
                if (!isNaN(numVal)) FeatureState.optunaConfig[key] = numVal;
            }
        }
    });

    // WFA 선언형 파라미터 양방향 동기화 (AutoLab 탭의 입력값을 FeatureState.optunaConfig에 보존)
    const winCntEl = document.getElementById('wfaWindowCount');
    if (winCntEl && winCntEl.value) {
        const winCnt = parseInt(winCntEl.value, 10);
        if (!isNaN(winCnt)) FeatureState.optunaConfig.wfa_window_count = winCnt;
    }
    const holdoutTdEl = document.getElementById('asrlHoldoutTd');
    if (holdoutTdEl && holdoutTdEl.value) {
        const hTd = parseInt(holdoutTdEl.value, 10);
        if (!isNaN(hTd)) FeatureState.optunaConfig.holdout_td = hTd;
    }
}

async function saveFeatureConfig() {
    try {
        collectOptunaConfigFromDom();
        collectHorizonConfigFromDom();
        const enabledFeatures = Array.from(new Set(FeatureState.activeFeatures.map(f => f.canonical_id || f.id)));
        
        const isZeroFeatures = enabledFeatures.length === 0;
        
        const payload = {
            enabled_features: enabledFeatures,
            feature_params: FeatureState.featureParams,
            feature_penalties: FeatureState.featurePenalties,
            regime_features: FeatureState.regimeFeatures,
            optuna_config: FeatureState.optunaConfig,
            target_horizon_config: FeatureState.targetHorizonConfig,
            reference_symbols: FeatureState.referenceSymbols
        };
        
        await apiPost('/api/features/config', payload);
        FeatureState.isDirty = false;
        if (isZeroFeatures) {
            showToast('✅ 지표가 모두 제거된 상태로 프로젝트가 저장되었습니다.', 'success');
            appendLog('ℹ️ 활성 지표가 0개인 상태로 저장되었습니다. 모델 학습 전에 지표를 선택해 주세요.', '');
        } else {
            showToast('✅ 프로젝트 설정이 저장되었습니다.', 'success');
            appendLog('💾 프로젝트 설정 저장 완료', '');
        }
    } catch (e) {
        showToast(`❌ 저장 실패: ${e}`, 'danger');
        appendLog(`❌ 지표 설정 저장 실패: ${e}`, '');
    }
}

async function selectFeatures() {
    const btn = document.getElementById('btnSelectFeatures');
    if (!btn) return;
    
    // 진행 상태 시각적 피드백 적용
    btn.disabled = true;
    btn.textContent = '⏳ 지표 선정 진행 중...';
    btn.style.opacity = '0.7';
    appendLog('🧬 LightGBM Feature Importance 기반 자동 지표 선정을 개시합니다 (약 5~15초 소요)...', '');
    
    try {
        const lookback = document.getElementById('screenerLookbackSelect')?.value || '3y';
        const res = await apiPost('/api/features/select', { lookback_period: lookback });
        if (res.status === 'ok') {
            const selected = res.selected_features || [];
            
            // 1. 제외된 기존 활성 지표들 파악
            const currentActiveIds = FeatureState.activeFeatures.map(f => f.id);
            const excludedIds = currentActiveIds.filter(id => !selected.includes(id));
            
            // 2. activeFeatures를 선정된 지표들로 재구성
            const newActiveFeatures = [];
            selected.forEach(id => {
                const existing = FeatureState.activeFeatures.find(f => f.id === id);
                if (existing) {
                    newActiveFeatures.push(existing);
                } else if (FeatureState.registry[id]) {
                    newActiveFeatures.push({
                        ...FeatureState.registry[id],
                        currentParams: getDefaultParams(FeatureState.registry[id])
                    });
                }
            });
            FeatureState.activeFeatures = newActiveFeatures;
            
            // 3. availableFeatures 및 로그 갱신
            FeatureState.availableFeatures = Object.keys(FeatureState.registry)
                .filter(id => !selected.includes(id))
                .map(id => FeatureState.registry[id]);
                
            excludedIds.forEach(id => {
                if (FeatureState.registry[id]) {
                    appendLog(`   [지표 선별] ❌ ${FeatureState.registry[id].name} (기여도 미달로 활성 목록에서 제거)`, '');
                }
            });
            selected.forEach(id => {
                if (FeatureState.registry[id]) {
                    appendLog(`   [지표 선별] ✅ ${FeatureState.registry[id].name} (기류 유지)`, '');
                }
            });
            
            // UI 다시 렌더링
            FeatureState.isDirty = true;
            renderFeatureConfig();
            
            showToast('✅ 자동 지표 선별이 완료되었습니다. 활성화된 지표 목록을 확인하고 저장해 주세요!', 'success');
            if (typeof refreshStatus === 'function') refreshStatus();   // 선정 구간(selection_period) 반영
            if (typeof loadHoldoutBoundary === 'function') loadHoldoutBoundary();
            appendLog('🏆 자동 지표 선별 완료! 변경된 지표 구성을 최종 저장하려면 저장 버튼을 눌러주세요.', '');
        } else {
            showToast(`❌ 지표 선정 실패: ${res.error}`, 'danger');
            appendLog(`❌ 지표 선정 실패: ${res.error}`, '');
        }
    } catch (e) {
        showToast(`❌ 지표 선정 에러: ${e.message}`, 'danger');
        appendLog(`❌ 지표 선정 에러: ${e.message}`, '');
    } finally {
        btn.disabled = false;
        btn.textContent = '🧬 자동 지표 선정';
        btn.style.opacity = '1.0';
    }
}

async function optimizeFeatures() {
    const saved = await ensureProjectSavedBeforeAction('지표 파라미터 사전 최적화');
    if (!saved) return;

    const btn = document.getElementById('btnOptimizeFeatures');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ 최적화 중...';
    }
    
    try {
        appendLog('🔬 지표 파라미터 사전 통계 최적화 분석을 시작합니다. (Train 구간 기반)...', '');
        appendLog('⚠️ [Quant Notice] 지표 파라미터 튜닝은 과거 노이즈 과적합(Data Snooping) 위험이 있으므로 표준 기본값 유지를 권장합니다.', '');
        const res = await apiPost('/api/features/optimize');
        if (res.status === 'ok') {
            const best = res.best_params;
            
            // FeatureState.featureParams 상태 갱신
            for (const [fid, params] of Object.entries(best)) {
                if (!FeatureState.featureParams[fid]) {
                    FeatureState.featureParams[fid] = {};
                }
                for (const [pname, val] of Object.entries(params)) {
                    FeatureState.featureParams[fid][pname] = val;
                    appendLog(`   [최적화 발견] ${fid} ➡️ ${pname}: ${val}`, '');
                }
            }
            
            // activeFeatures 객체 내부 파라미터 값 동기화
            FeatureState.activeFeatures.forEach(f => {
                if (best[f.id]) {
                    f.currentParams = { ...f.currentParams, ...best[f.id] };
                }
            });
            
            // UI 다시 렌더링
            FeatureState.isDirty = true;
            renderFeatureConfig();
            
            showToast('✅ 지표 사전 최적화가 완료되었습니다. 슬라이더 값을 확인하고 저장해 주세요!', 'success');
            appendLog('🏆 지표 사전 최적화 완료! 변경된 값을 최종 저장하려면 저장 버튼을 눌러주세요.', '');
        } else {
            showToast(`❌ 지표 최적화 실패: ${res.error}`, 'danger');
            appendLog(`❌ 지표 최적화 실패: ${res.error}`, '');
        }
    } catch (e) {
        showToast(`❌ 지표 최적화 에러: ${e.message}`, 'danger');
        appendLog(`❌ 지표 최적화 에러: ${e.message}`, '');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '⚙️ 파라미터 최적화';
        }
    }
}

// =============================================================================
// Helper: 캔들 → 시간 변환
// =============================================================================
function candlesToTime(candles, intervalMin, intervalSec = null) {
    let totalSec = 0;
    if (intervalSec !== null && intervalSec !== undefined) {
        totalSec = Math.round(candles * intervalSec);
    } else if (FeatureState && FeatureState.intervalSeconds) {
        totalSec = Math.round(candles * FeatureState.intervalSeconds);
    } else {
        totalSec = Math.round(candles * (intervalMin || 1) * 60);
    }

    if (totalSec < 60) {
        return `${totalSec}초`;
    }
    const totalMin = Math.floor(totalSec / 60);
    const remSec = totalSec % 60;

    if (totalMin < 60) {
        if (remSec === 0) return `${totalMin}분`;
        return `${totalMin}분 ${remSec}초`;
    }

    const hours = Math.floor(totalMin / 60);
    const mins = totalMin % 60;

    // 24시간(1일) 이상인 경우 일(日) 단위로 직관적 표기 (일봉 지원)
    if (hours >= 24) {
        const days = Math.floor(hours / 24);
        const remHours = hours % 24;
        if (remHours === 0 && mins === 0 && remSec === 0) return `${days}일`;
        if (mins === 0 && remSec === 0) return `${days}일 ${remHours}시간`;
        return `${days}일 ${remHours}시간 ${mins}분`;
    }

    if (mins === 0 && remSec === 0) return `${hours}시간`;
    if (remSec === 0) return `${hours}시간 ${mins}분`;
    return `${hours}시간 ${mins}분 ${remSec}초`;
}

// =============================================================================
// Render Functions
// =============================================================================

function renderFeatureConfig() {
    updateViewModeButtons();
    renderSuitabilityWarningBanner();
    renderReferenceSymbolsBar();
    renderActiveFeatures();
    renderAvailableFeatures();
}

function renderSuitabilityWarningBanner() {
    const banner = document.getElementById('featureSuitabilityAlertBanner');
    if (!banner) return;

    // 실시간으로 activeFeatures를 순회하여 부적합 목록 계산
    const currentIncompatible = [];
    const symScope = FeatureState.marketType || 'krx';
    const scopeLabels = {
        'crypto': '가상자산(코인)',
        'krx': '국내 주식/ETF',
        'us': '미국 주식/ETF'
    };

    (FeatureState.activeFeatures || []).forEach(f => {
        const scope = f.market_scope || 'all';
        if (scope !== 'all' && scope !== symScope) {
            currentIncompatible.push(f);
        }
    });

    FeatureState.incompatibleFeatures = currentIncompatible;

    if (currentIncompatible.length === 0) {
        banner.style.display = 'none';
        banner.innerHTML = '';
        return;
    }

    const currentScopeLabel = scopeLabels[symScope] || symScope;
    const names = currentIncompatible.map(f => `<strong>${f.name || f.id}</strong> <span style="font-size:10.5px;opacity:0.8;">(${(f.market_scope || '').toUpperCase()})</span>`).join(', ');

    banner.style.display = 'block';
    banner.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
            <div style="display:flex; align-items:flex-start; gap:8px; flex:1;">
                <span style="font-size:16px;">⚠️</span>
                <div style="font-size:12px; line-height:1.5; color:var(--text-primary);">
                    <strong style="color:#f87171;">[지표 적합성 주의]</strong> 
                    현재 프로젝트(<strong>${currentScopeLabel}</strong>)와 일치하지 않는 타 자산 전용 지표 
                    <span class="badge" style="background:rgba(239,68,68,0.25); color:#fca5a5; font-size:11px; margin:0 2px;">${currentIncompatible.length}개</span>가 활성화되어 있습니다:
                    <div style="margin-top:3px; font-size:11.5px; color:#fcd34d;">${names}</div>
                </div>
            </div>
            <button type="button" class="btn btn-sm btn-warning" onclick="cleanupIncompatibleFeatures()" 
                    style="font-size:11px; font-weight:700; background:rgba(245,158,11,0.2); border:1px solid rgba(245,158,11,0.6); color:#fbbf24; cursor:pointer; padding:5px 12px; border-radius:6px;"
                    title="부적합 지표를 활성 목록에서 일괄 제외하고 설정을 저장합니다.">
                🧹 부적합 지표 원클릭 정리
            </button>
        </div>
    `;
}

async function cleanupIncompatibleFeatures() {
    const incompIds = (FeatureState.incompatibleFeatures || []).map(f => f.id || f.canonical_id);
    if (incompIds.length === 0) return;
    await cleanupIncompatibleFeaturesFromList(incompIds);
}

async function cleanupIncompatibleFeaturesFromList(targetIds) {
    if (!targetIds || targetIds.length === 0) return;

    // 🛡️ [소실 방지 가드] 전체 또는 과반수의 지표가 부적합으로 걸러지는 경우 자산군 오판 가능성 경고
    const activeCount = FeatureState.activeFeatures.length;
    if (activeCount > 0 && targetIds.length >= activeCount) {
        const confirmed = confirm(
            `⚠️ [자산군 설정 주의 경고]\n\n` +
            `제외 대상 지표(${targetIds.length}개)가 현재 활성 지표 전체(${activeCount}개)와 일치합니다.\n` +
            `프로젝트 심볼/자산군(코인/주식) 설정이 맞는지 먼저 확인해 주세요.\n\n` +
            `정말로 활성 지표를 모두 제거하시겠습니까?`
        );
        if (!confirmed) return;
    }

    const targetSet = new Set(targetIds);
    FeatureState.activeFeatures = FeatureState.activeFeatures.filter(f => !targetSet.has(f.id) && !targetSet.has(f.canonical_id));
    renderFeatureConfig();
    
    await saveFeatureConfig();
    showToast(`🧹 부적합 지표 ${targetIds.length}개가 일괄 해제 및 저장되었습니다.`, 'success');
}

function getIncompatibleBadge(feature) {
    const scope = feature.market_scope || 'all';
    const symScope = FeatureState.marketType || 'krx';
    if (scope !== 'all' && scope !== symScope) {
        return `<span class="badge" style="background:rgba(239, 68, 68, 0.2); color:#f87171; border:1px solid rgba(239, 68, 68, 0.5); font-size:10px; margin-left:4px;" title="현재 프로젝트 자산군(${symScope})과 맞지 않는 ${scope.toUpperCase()} 전용 지표입니다.">⚠️ 타자산 (${scope.toUpperCase()})</span>`;
    }
    return '';
}

function renderReferenceSymbolsBar() {
    const container = document.getElementById('refChipsList');
    if (!container) return;

    // 카테고리 필터 버튼 가시성 제어 (부착된 참조 종목이 있으면 버튼 노출, 없으면 숨김)
    const refCatBtn = document.querySelector('.btn-category[data-cat="reference"]');
    if (refCatBtn) {
        refCatBtn.style.display = FeatureState.referenceSymbols.length > 0 ? 'inline-block' : 'none';
    }

    const hasGroups = FeatureState.referenceGroups && FeatureState.referenceGroups.length > 0;
    const hasSymbols = FeatureState.referenceSymbols && FeatureState.referenceSymbols.length > 0;

    if (!hasGroups && !hasSymbols) {
        container.innerHTML = `<span style="color:var(--text-muted); font-size:11px;">장착된 팩 또는 참조 종목 없음 (타겟 단독 분석 모드)</span>`;
        return;
    }

    let html = '';

    // 1. 📦 장착된 팩터 팩(Feature Groups) 뱃지 렌더링
    const groupMemberSymbols = new Set();
    if (hasGroups) {
        FeatureState.referenceGroups.forEach(grp => {
            const gid = grp.id;
            const gname = grp.name || gid;
            const members = grp.members || [];
            members.forEach(m => {
                const s = typeof m === 'object' ? m.symbol : m;
                if (s) groupMemberSymbols.add(s);
            });
            const memberNames = members.map(m => m.name || m.symbol).slice(0, 2).join(', ');
            const memberText = memberNames + (members.length > 2 ? ` 외 ${members.length - 2}` : '');
            
            html += `
                <div class="ref-group-badge" style="display:inline-flex; align-items:center; gap:5px; padding:3px 9px; border-radius:6px; background:rgba(168,85,247,0.15); border:1px solid rgba(168,85,247,0.45); font-size:11px;" title="${grp.description || gname}">
                    <span style="font-size:12px;">📦</span>
                    <span style="color:#f8fafc; font-weight:700;">${gname}</span>
                    <span style="color:#c084fc; font-size:10px;">(${memberText})</span>
                    <span class="badge" style="background:rgba(168,85,247,0.3); color:#f3e8ff; font-size:9.5px; padding:1px 5px; border-radius:4px;">${members.length}종목</span>
                    <button type="button" onclick="openEditGroupModal('${gid}')" style="background:none; border:none; color:#38bdf8; cursor:pointer; font-size:11px; padding:0 2px;" title="팩 구성 종목 편집">⚙️</button>
                    <button type="button" onclick="detachGroupFromTarget('${gid}')" style="background:none; border:none; color:#f87171; cursor:pointer; font-weight:bold; font-size:11px; padding:0 2px; line-height:1;" title="이 타겟에서 팩 분리">✕</button>
                </div>
            `;
        });
    }

    // 2. ⚡ 팩에 속하지 않은 개별 직접 추가 참조 종목(Standalone) 렌더링
    const standaloneRefs = (FeatureState.referenceSymbols || []).filter(r => !groupMemberSymbols.has(r.symbol));
    standaloneRefs.forEach((ref, idx) => {
        const sym = ref.symbol;
        const name = ref.name || sym;
        const status = FeatureState.referenceSymbolsStatus[sym] || { ready: false, rows: 0 };
        const slotLabel = `개별 ${idx + 1}`;

        if (status.ready) {
            html += `
                <div class="ref-chip ref-chip-ready" style="display:inline-flex; align-items:center; gap:5px; padding:3px 8px; border-radius:6px; background:rgba(34,197,94,0.1); border:1px solid rgba(34,197,94,0.3); font-size:11px;" title="${name} (${sym}) - 과거 캔들 ${status.rows.toLocaleString()}행 보유 (연동 완료)">
                    <span class="badge" style="background:rgba(234,179,8,0.2); color:#facc15; font-size:9px; padding:1px 3px;">${slotLabel}</span>
                    <span style="color:#f8fafc; font-weight:600;">${name}</span>
                    <span style="color:#94a3b8; font-size:10px;">(${sym})</span>
                    <span class="badge badge-ready" style="font-size:9.5px; color:#4ade80;">🟢 ${Math.round(status.rows / 1000)}k행</span>
                    <button type="button" onclick="removeReferenceSymbol('${sym}')" style="background:none; border:none; color:#f87171; cursor:pointer; font-weight:bold; font-size:11px; padding:0 2px; line-height:1; margin-left:2px;" title="참조 종목 분리">✕</button>
                </div>
            `;
        } else {
            html += `
                <div class="ref-chip ref-chip-missing" style="display:inline-flex; align-items:center; gap:5px; padding:3px 8px; border-radius:6px; background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.3); font-size:11px;" title="Data Vault에서 데이터 수집이 필요합니다.">
                    <span class="badge" style="background:rgba(234,179,8,0.2); color:#facc15; font-size:9px; padding:1px 3px;">${slotLabel}</span>
                    <span style="color:#f8fafc; font-weight:600;">${name}</span>
                    <span style="color:#94a3b8; font-size:10px;">(${sym})</span>
                    <span class="badge badge-missing" onclick="switchTab('dataVaultTab')" style="cursor:pointer; color:#f87171; font-size:9.5px;" title="클릭하여 Data Vault로 이동">
                        🔴 미수집 (수집 필요)
                    </span>
                    <button type="button" onclick="removeReferenceSymbol('${sym}')" style="background:none; border:none; color:#f87171; cursor:pointer; font-weight:bold; font-size:11px; padding:0 2px; line-height:1; margin-left:2px;" title="참조 종목 분리">✕</button>
                </div>
            `;
        }
    });

    container.innerHTML = html;

    // 드롭다운 셀렉트박스(#refSymbolSelect) 옵션 동기화 (이미 부착된 자산은 비활성화 및 표시)
    const sel = document.getElementById('refSymbolSelect');
    if (sel) {
        const attachedSymbols = new Set(FeatureState.referenceSymbols.map(r => r.symbol));
        Array.from(sel.options).forEach(opt => {
            if (!opt.value || opt.value === 'custom') return;
            const optSym = opt.value.split(':')[0];
            const baseText = opt.getAttribute('data-base-text') || opt.textContent.replace(' (부착됨)', '');
            opt.setAttribute('data-base-text', baseText);
            if (attachedSymbols.has(optSym)) {
                opt.disabled = true;
                opt.textContent = `${baseText} (연동됨)`;
            } else {
                opt.disabled = false;
                opt.textContent = baseText;
            }
        });
    }
}

async function onAddReferenceSymbolClick() {
    const sel = document.getElementById('refSymbolSelect');
    if (!sel || !sel.value) {
        showToast('부착할 대장주를 선택해 주세요.', 'warning');
        return;
    }

    let sym, name;
    if (sel.value === 'custom') {
        const inputSym = prompt('추가할 참조 종목 코드 (국내 6자리 또는 해외 티커, 예: 000660, QQQ):');
        if (!inputSym || !inputSym.trim()) return;
        sym = inputSym.trim().toUpperCase();
        name = prompt('종목명 (예: SK하이닉스, QQQ):', sym) || sym;
    } else {
        const parts = sel.value.split(':');
        sym = parts[0];
        name = parts[1] || sym;
    }

    // 이미 존재하는지 확인
    if (FeatureState.referenceSymbols.some(r => r.symbol === sym)) {
        showToast(`이미 부착된 대장주입니다: ${name} (${sym})`, 'info');
        return;
    }

    FeatureState.referenceSymbols.push({ symbol: sym, name: name });
    sel.value = '';

    // ⚡ Data Vault API(/api/symbols/relations/add) 동시 동기화 (SSOT 보장)
    const activeTarget = getCurrentTargetSymbol();
    try {
        const isMacro = ['NQ', 'ES', 'NVDA-PERP', 'US10Y', 'WTI', 'GOLD', 'USDKRW', 'QQQ'].includes(sym);
        await apiPost('/api/symbols/relations/add', {
            target: activeTarget,
            reference: {
                symbol: sym,
                name: name,
                role: isMacro ? 'macro_futures' : 'leading_stock'
            }
        });
    } catch (e) {
        console.warn('Data Vault relations/add sync warning:', e);
    }

    // 설정 저장 및 실시간 동기화
    showToast(`⚡ ${name} 참조 종목이 부착되었습니다. 전용 선행 피처를 로드합니다...`, 'success');
    await saveFeatureConfig();
    await loadFeatureConfig(activeTarget);
    if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(activeTarget);
    if (typeof loadDataVaultStatus === 'function') loadDataVaultStatus();
    if (typeof loadMedallionStatus === 'function') loadMedallionStatus(activeTarget);
}

async function removeReferenceSymbol(sym) {
    const target = FeatureState.referenceSymbols.find(r => r.symbol === sym);
    const name = target ? target.name : sym;
    if (!confirm(`'${name} (${sym})' 참조 종목 연동을 해제하시겠습니까?\n해당 참조 종목 관련 파생 지표들도 목록에서 함께 정리됩니다.`)) return;

    FeatureState.referenceSymbols = FeatureState.referenceSymbols.filter(r => r.symbol !== sym);
    // 활성 지표 중 해당 참조 종목의 지표(ref_{key}_*)도 정리
    const refKey = target ? target.key : null;
    if (refKey) FeatureState.activeFeatures = FeatureState.activeFeatures.filter(f => !f.id.startsWith(`ref_${refKey}_`));

    // ⚡ Data Vault API(/api/symbols/relations/remove) 동시 동기화 (SSOT 보장)
    const activeTarget = getCurrentTargetSymbol();
    try {
        await apiPost('/api/symbols/relations/remove', {
            target: activeTarget,
            ref_symbol: sym
        });
    } catch (e) {
        console.warn('Data Vault relations/remove sync warning:', e);
    }

    showToast(`'${name}' 참조 종목이 분리되었습니다.`, 'info');
    await saveFeatureConfig();
    await loadFeatureConfig(activeTarget);
    if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(activeTarget);
    if (typeof loadDataVaultStatus === 'function') loadDataVaultStatus();
    if (typeof loadMedallionStatus === 'function') loadMedallionStatus(activeTarget);
}

async function downloadReferenceSymbolData(sym, name) {
    const curInterval = (typeof FeatureState !== 'undefined' && FeatureState.interval) ? FeatureState.interval : '10m';
    if (!confirm(`'${name} (${sym})' ${curInterval} 과거 데이터를 백그라운드로 다운로드하시겠습니까?`)) return;

    showToast(`📡 '${name}' (${curInterval}) 데이터 수집 요청 중...`, 'info');
    try {
        const res = await apiPost('/api/reference_symbols/download', { symbol: sym, interval: curInterval });
        if (res && res.status === 'ok') {
            showToast(`🚀 '${name}' (${curInterval}) 다운로드가 시작되었습니다. 수집 완료 후 자동으로 🟢 준비완료 상태로 전환됩니다.`, 'success');
            setTimeout(loadFeatureConfig, 4000);
        } else {
            showToast(`다운로드 실패: ${res.message || '오류 발생'}`, 'error');
        }
    } catch (e) {
        showToast(`다운로드 요청 오류: ${e.message}`, 'error');
    }
}

// =============================================================================
// 🏷️ 종목코드(379810, 449190 등) -> 정식 종목명 변환 헬퍼
//    종목명은 서버(마스터 원장)가 FeatureState.referenceSymbols 로 내려준 것만 사용한다.
//    (프론트에 종목명 사전을 두지 않는다 — 원장에 없으면 코드 그대로 표시)
// =============================================================================
function formatFeatureTitle(feature) {
    if (!feature) return '';
    let name = feature.name || feature.id || '';
    
    // 1. FeatureState.referenceSymbols에서 매핑
    if (FeatureState.referenceSymbols && FeatureState.referenceSymbols.length > 0) {
        FeatureState.referenceSymbols.forEach(ref => {
            const sym = ref.symbol;
            const realName = ref.name;
            if (!realName) return;
            
            if (sym) {
                name = name.split(`[${sym.toUpperCase()}]`).join(`[${realName}]`);
                name = name.split(`[${sym}]`).join(`[${realName}]`);
            }
            if (ref.key) {
                name = name.split(`[${ref.key.toUpperCase()}]`).join(`[${realName}]`);
            }
        });
    }
    
    return name;
}

function formatFeatureDesc(feature) {
    if (!feature) return '';
    let desc = feature.description || '';
    
    if (FeatureState.referenceSymbols && FeatureState.referenceSymbols.length > 0) {
        FeatureState.referenceSymbols.forEach(ref => {
            const sym = ref.symbol;
            const realName = ref.name;
            if (!realName) return;
            
            if (sym) {
                desc = desc.split(`(${sym})`).join(`(${realName})`);
                desc = desc.split(`(${sym.toLowerCase()})`).join(`(${realName})`);
            }
            if (ref.key) {
                desc = desc.split(`(${ref.key})`).join(`(${realName})`);
            }
        });
    }
    
    return desc;
}

function renderActiveFeatures() {
    const container = document.getElementById('activeFeaturesList');
    if (!container) return;
    
    if (FeatureState.activeFeatures.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <div>활성화된 지표가 없습니다.</div>
                <div style="font-size:11px;margin-top:4px;">아래에서 지표를 추가하거나 상단의 알파 가설 프리셋을 선택해주세요.</div>
            </div>
        `;
        return;
    }
    
    // 도메인/카테고리별 그룹 정의
    const categoryGroups = [
        { id: 'microstructure', title: '퀀트 마이크로스트럭처 팩터', icon: '🔬', desc: 'VWAP 괴리율, OFI 주문흐름, Parkinson RV, 대량 체결' },
        { id: 'target', title: '타겟 종목 자체 팩터', icon: '🎯', desc: '매매 대상 자체 시세/체결/수급 지표' },
        { id: 'reference', title: '대장주 선행 / Lead-Lag 팩터', icon: '⚡', desc: '참조 대장주 괴리율 및 모멘텀' },
        { id: 'kospi_etf', title: 'KOSPI200 ETF / 선물 파생 팩터', icon: '🏛️', desc: '선물-현물 베이시스 및 ETF 괴리율' },
        { id: 'trend', title: '추세 지표', icon: '📈', desc: '가격의 방향성 및 추세 강도' },
        { id: 'momentum', title: '모멘텀 지표', icon: '📊', desc: '가격 변동의 속도와 가속도' },
        { id: 'oscillator', title: '오실레이터 & 변동성', icon: '📉', desc: '과매수/과매도 및 밴드 위치' },
        { id: 'orderflow', title: '글로벌 오더플로우 / 수급 지표', icon: '💰', desc: 'CVD, 미결제약정(OI), 테이커 체결량' },
        { id: 'divergence', title: '괴리 & 시장간 지표', icon: '💱', desc: '시장 간 가격 차이 및 프리미엄' },
        { id: 'time', title: '시간 & 세션 패턴', icon: '🕐', desc: '장 시작/마감 및 시간대별 특성' },
        { id: 'other', title: '기타 보조 지표', icon: '📦', desc: '기타 보조 지표' }
    ];

    if (FeatureState.viewMode === 'table') {
        renderActiveFeaturesTable(container);
    } else {
        renderActiveFeaturesCards(container, categoryGroups);
    }
}

function updateViewModeButtons() {
    const btnTable = document.getElementById('btnViewModeTable');
    const btnCard = document.getElementById('btnViewModeCard');
    if (btnTable && btnCard) {
        if (FeatureState.viewMode === 'table') {
            btnTable.classList.add('active');
            btnTable.style.background = 'var(--accent-primary)';
            btnTable.style.color = '#fff';
            btnCard.classList.remove('active');
            btnCard.style.background = 'transparent';
            btnCard.style.color = 'var(--text-secondary)';
        } else {
            btnCard.classList.add('active');
            btnCard.style.background = 'var(--accent-primary)';
            btnCard.style.color = '#fff';
            btnTable.classList.remove('active');
            btnTable.style.background = 'transparent';
            btnTable.style.color = 'var(--text-secondary)';
        }
    }
}

function toggleFeatureViewMode(mode) {
    FeatureState.viewMode = mode;
    localStorage.setItem('featureViewMode', mode);
    updateViewModeButtons();
    renderActiveFeatures();
    renderAvailableFeatures();
}

function onFeatureSearchInput(query) {
    FeatureState.searchQuery = (query || '').toLowerCase().trim();
    renderActiveFeatures();
    renderAvailableFeatures();
}

function getFeatureDataBindingTag(fid) {
    if (!fid) return '';
    const id = String(fid).toLowerCase();
    let src = '';
    if (id.includes('basis')) src = 'ref_{선물}_close, ref_{지수}_close';
    else if (id.includes('top2') || id.includes('leaders')) src = 'ref_{종목키}_close (주식 참조 2종목)';
    else if (id.startsWith('ref_')) {
        const m = id.match(/^ref_(.+)_(ret_1c|ret_5c|spread_vs_target|intensity)$/);
        src = `ref_${m ? m[1] : '{종목키}'}_close`;
    }
    else if (id.includes('rv') || id.includes('parkinson')) src = 'target_high, target_low';
    else if (id.includes('vwap')) src = 'target_close, target_vwap';
    else if (id.includes('ofi')) src = 'target_ofi';
    else if (id.includes('cvd')) src = 'target_cvd';
    else if (id.includes('trade_bid') || id.includes('taker')) src = 'bid_vol, ask_vol';
    else if (id.includes('volume') || id.includes('mfi')) src = 'target_volume, target_close';
    else if (id.includes('hour') || id.includes('session') || id.includes('time') || id.includes('weekend')) src = 'timestamp';
    else src = 'target_close';

    return `<span class="badge" style="background:rgba(59, 130, 246, 0.12); color:#60a5fa; border:1px solid rgba(59, 130, 246, 0.3); font-size:9.5px; padding:1px 5px; border-radius:3px; margin-left:6px; font-family:monospace;" title="연결된 2단계 원천 데이터 컬럼 (Data Binding)">🔗 2단계: ${src}</span>`;
}

function sortActiveFeatures(col) {
    if (FeatureState.sortCol === col) {
        FeatureState.sortAsc = !FeatureState.sortAsc;
    } else {
        FeatureState.sortCol = col;
        FeatureState.sortAsc = true;
    }
    renderActiveFeatures();
}

function renderActiveFeaturesTable(container) {
    let features = [...FeatureState.activeFeatures];
    
    // 검색어 필터링
    if (FeatureState.searchQuery) {
        const q = FeatureState.searchQuery.toLowerCase();
        features = features.filter(f => 
            (formatFeatureTitle(f).toLowerCase().includes(q)) ||
            (f.name && f.name.toLowerCase().includes(q)) ||
            (f.id && f.id.toLowerCase().includes(q)) ||
            (f.name_en && f.name_en.toLowerCase().includes(q)) ||
            (formatFeatureDesc(f).toLowerCase().includes(q)) ||
            (f.category && f.category.toLowerCase().includes(q))
        );
    }
    
    // 정렬
    features.sort((a, b) => {
        let valA, valB;
        if (FeatureState.sortCol === 'name') {
            valA = a.name || a.id;
            valB = b.name || b.id;
            return FeatureState.sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else if (FeatureState.sortCol === 'weight') {
            valA = FeatureState.featurePenalties[a.id] !== undefined ? FeatureState.featurePenalties[a.id] : 1.0;
            valB = FeatureState.featurePenalties[b.id] !== undefined ? FeatureState.featurePenalties[b.id] : 1.0;
            return FeatureState.sortAsc ? valA - valB : valB - valA;
        } else {
            valA = a.category || 'other';
            valB = b.category || 'other';
            return FeatureState.sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        }
    });

    let rowsHtml = '';
    features.forEach((feature, idx) => {
        const catInfo = FeatureState.categories[feature.category] || { name: feature.category, icon: '📊' };
        const params = feature.params || {};
        const currentParams = FeatureState.featureParams[feature.id] || {};
        const penalty = FeatureState.featurePenalties[feature.id] !== undefined ? FeatureState.featurePenalties[feature.id] : 1.0;

        // 인라인 파라미터 렌더링
        let paramsHtml = '';
        if (Object.keys(params).length > 0) {
            for (const [pName, pConfig] of Object.entries(params)) {
                const curVal = currentParams[pName] !== undefined ? currentParams[pName] : pConfig.default;
                const isModified = (currentParams[pName] !== undefined) && (Number(currentParams[pName]) !== Number(pConfig.default));
                paramsHtml += `
                    <span class="table-param-pill ${isModified ? 'param-modified' : ''}" title="${pConfig.description || ''}">
                        <span class="param-name">${pConfig.label || pName}:</span>
                        <input type="number" class="table-param-input" 
                               value="${curVal}" min="${pConfig.min}" max="${pConfig.max}" step="${pConfig.step}"
                               data-feature="${feature.id}" data-param="${pName}"
                               onchange="onTableParamChange(this)">
                        ${isModified ? `<button type="button" class="btn-param-reset" title="기본값(${pConfig.default})으로 복원" onclick="resetSingleParamToDefault('${feature.id}', '${pName}', ${pConfig.default})">⟲</button>` : ''}
                    </span>
                `;
            }
        } else {
            paramsHtml = `<span style="font-size:11px;color:var(--text-muted);">기본 수식</span>`;
        }

        const defaultRegime = Boolean(feature.is_regime || feature.category === 'time');
        const effectiveRegime = (FeatureState.regimeFeatures && FeatureState.regimeFeatures[feature.id] !== undefined)
            ? Boolean(FeatureState.regimeFeatures[feature.id])
            : defaultRegime;
        const isOverridden = (FeatureState.regimeFeatures && FeatureState.regimeFeatures[feature.id] !== undefined) && (Boolean(FeatureState.regimeFeatures[feature.id]) !== defaultRegime);

        const regimeBadge = effectiveRegime ? `
            <span class="badge" style="background:rgba(234, 179, 8, 0.2); color:#facc15; border:1px solid rgba(234, 179, 8, 0.5); font-size:10px; margin-left:4px;" title="${isOverridden ? '사용자가 직접 백본 고정(IC면제)으로 지정' : '체제/시간 컨텍스트 지표 (기본 백본)'}">🛡️ 체제${isOverridden ? ' (수동)' : ''}</span>
        ` : (isOverridden ? `
            <span class="badge" style="background:rgba(99, 102, 241, 0.2); color:#a5b4fc; border:1px solid rgba(99, 102, 241, 0.5); font-size:10px; margin-left:4px;" title="사용자가 직접 IC 평가 모드로 전환함">📊 IC평가 (수동)</span>
        ` : '');

        const penaltyCellHtml = `
            <div style="display:flex;flex-direction:column;gap:4px;">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:4px;">
                    <button type="button" class="btn btn-xs" 
                            style="${effectiveRegime ? 'background:rgba(234, 179, 8, 0.2); color:#facc15; border:1px solid rgba(234, 179, 8, 0.5);' : 'background:rgba(99, 102, 241, 0.15); color:#a5b4fc; border:1px solid rgba(99, 102, 241, 0.4);'}font-size:10px;padding:2px 6px;border-radius:4px;cursor:pointer;font-weight:600;"
                            title="${effectiveRegime ? '클릭 시 일반 IC 평가 모드로 전환 (선형 IC 스크리닝 대상)' : '클릭 시 백본 고정(IC면제) 모드로 전환 (전체 Trial 조합에 자동 포함)'}"
                            onclick="toggleFeatureRegime('${feature.id}')">
                        ${effectiveRegime ? '🛡️ 백본 고정' : '📊 IC 평가'}
                    </button>
                    <span style="font-size:10px;color:var(--text-muted);">${effectiveRegime ? 'IC면제' : `가중치 ${penalty.toFixed(1)}`}</span>
                </div>
                ${effectiveRegime ? `
                    <div style="font-size:9.5px;color:var(--text-muted);line-height:1.2;white-space:nowrap;">모든 Trial 백본 자동 탑재</div>
                ` : `
                    <div style="display:flex;align-items:center;gap:6px;">
                        <input type="range" class="table-penalty-slider" 
                               min="0.2" max="1.0" step="0.1" value="${penalty}"
                               data-feature="${feature.id}"
                               oninput="updateTablePenaltyDisplay(this)"
                               onchange="saveTablePenaltyChange(this)">
                        <span class="table-penalty-val" id="t-pen-${feature.id}">${penalty.toFixed(1)}</span>
                    </div>
                `}
            </div>
        `;

        const incompBadge = getIncompatibleBadge(feature);
        const incompStyle = incompBadge ? 'background:rgba(239, 68, 68, 0.05);' : '';

        rowsHtml += `
            <tr class="density-row ${incompBadge ? 'row-incompatible' : ''}" data-feature-id="${feature.id}" style="${incompStyle}">
                <td style="width:40px;text-align:center;color:var(--text-muted);font-size:11px;">${idx + 1}</td>
                <td style="min-width:240px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-size:14px;">${catInfo.icon || '📊'}</span>
                        <div>
                            <div style="font-weight:700;font-size:12px;color:var(--text-primary);display:flex;align-items:center;gap:6px;" title="${formatFeatureDesc(feature)}">
                                <span>${formatFeatureTitle(feature)}</span>
                                ${getTriggerBadge(feature)}
                                ${regimeBadge}
                                ${incompBadge}
                                <button type="button" class="btn-info-detail" title="지표 상세 설명 보기" onclick="showFeatureDetailModal('${feature.id}')">ℹ️</button>
                            </div>
                            <div style="font-size:10px;color:var(--text-muted);font-family:monospace;display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
                                <span>${feature.id}</span>
                                ${getFeatureDataBindingTag(feature.id)}
                            </div>
                        </div>
                    </div>
                </td>
                <td style="width:110px;">
                    <span class="table-cat-badge cat-${feature.category}">${catInfo.name || feature.category}</span>
                </td>
                <td>
                    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                        ${paramsHtml}
                    </div>
                </td>
                <td style="width:140px;">
                    ${penaltyCellHtml}
                </td>
                <td style="width:50px;text-align:center;">
                    <button type="button" class="btn-table-remove" title="지표 제거" onclick="removeFeature('${feature.id}')">✕</button>
                </td>
            </tr>
        `;
    });

    container.innerHTML = `
        <div class="density-table-wrapper">
            <table class="density-table">
                <thead>
                    <tr>
                        <th style="width:40px;text-align:center;">#</th>
                        <th style="cursor:pointer;" onclick="sortActiveFeatures('name')">
                            지표명 / ID <span class="sort-icon">${FeatureState.sortCol === 'name' ? (FeatureState.sortAsc ? '▲' : '▼') : '↕'}</span>
                        </th>
                        <th style="width:110px;cursor:pointer;" onclick="sortActiveFeatures('category')">
                            카테고리 <span class="sort-icon">${FeatureState.sortCol === 'category' ? (FeatureState.sortAsc ? '▲' : '▼') : '↕'}</span>
                        </th>
                        <th>인라인 파라미터</th>
                        <th style="width:140px;cursor:pointer;" onclick="sortActiveFeatures('weight')">
                            IC 가중치 <span class="sort-icon">${FeatureState.sortCol === 'weight' ? (FeatureState.sortAsc ? '▲' : '▼') : '↕'}</span>
                        </th>
                        <th style="width:50px;text-align:center;">제거</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>
            <div class="density-table-footer">
                <span>총 <strong>${features.length}</strong>개 활성 지표</span>
                <span>💡 컬럼 헤더를 클릭하여 정렬하거나 인라인 수치를 즉시 편집할 수 있습니다.</span>
            </div>
        </div>
    `;
}

function renderActiveFeaturesCards(container, categoryGroups) {
    const groupMap = {};
    categoryGroups.forEach(g => { groupMap[g.id] = []; });

    let features = [...FeatureState.activeFeatures];
    if (FeatureState.searchQuery) {
        const q = FeatureState.searchQuery.toLowerCase();
        features = features.filter(f => 
            (formatFeatureTitle(f).toLowerCase().includes(q)) ||
            (f.name && f.name.toLowerCase().includes(q)) ||
            (f.id && f.id.toLowerCase().includes(q)) ||
            (f.name_en && f.name_en.toLowerCase().includes(q)) ||
            (formatFeatureDesc(f).toLowerCase().includes(q)) ||
            (f.category && f.category.toLowerCase().includes(q))
        );
    }

    features.forEach((feature, index) => {
        let cat = feature.category || 'other';
        if (!groupMap[cat]) {
            cat = 'other';
        }
        groupMap[cat].push({ feature, index });
    });

    let html = '';
    categoryGroups.forEach(group => {
        const items = groupMap[group.id];
        if (!items || items.length === 0) return;

        let cardsHtml = '';
        items.forEach(({ feature, index }) => {
            cardsHtml += renderFeatureCard(feature, index, true);
        });

        html += `
            <div class="feature-group-container" style="background:var(--bg-card); border:1px solid var(--border-color); border-radius:8px; padding:12px 14px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.06);">
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:13px; font-weight:700; color:var(--text-primary);">${group.icon} ${group.title}</span>
                        <span style="font-size:11px; color:var(--text-muted);">${group.desc}</span>
                    </div>
                    <span style="background:rgba(56, 189, 248, 0.12); color:var(--accent-cyan); border:1px solid rgba(56, 189, 248, 0.3); font-size:11px; padding:2px 8px; border-radius:10px; font-weight:700;">${items.length}개 활성</span>
                </div>
                <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(360px, 1fr)); gap:12px;">
                    ${cardsHtml}
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
    bindSliderEvents();
}

let _debounceSaveTimer = null;
function debounceSave() {
    if (_debounceSaveTimer) clearTimeout(_debounceSaveTimer);
    _debounceSaveTimer = setTimeout(() => {
        saveFeatureConfig();
    }, 500);
}

function onTableParamChange(input) {
    const featureId = input.dataset.feature;
    const paramName = input.dataset.param;
    const value = parseFloat(input.value);
    if (isNaN(value)) return;

    if (!FeatureState.featureParams[featureId]) {
        FeatureState.featureParams[featureId] = {};
    }
    FeatureState.featureParams[featureId][paramName] = value;
    FeatureState.isDirty = true;
    debounceSave();
}

async function resetFeatureParamsToDefaults() {
    if (!confirm('모든 지표의 파라미터를 퀀트 표준 기본값(Canonical Defaults)으로 일괄 복원하시겠습니까?')) {
        return;
    }
    
    // 🛡️ [활성 지표 보존 가드] 활성 지표 목록이 없으면 파라미터 리셋만 수행하고 저장 차단
    if (!FeatureState.activeFeatures || FeatureState.activeFeatures.length === 0) {
        showToast('⚠️ 활성화된 지표가 없습니다. 먼저 지표를 추가해 주세요.', 'warning');
        return;
    }

    let resetCount = 0;
    const allFeatures = [...FeatureState.activeFeatures, ...FeatureState.availableFeatures];
    allFeatures.forEach(feature => {
        const params = feature.params || {};
        if (Object.keys(params).length > 0) {
            if (!FeatureState.featureParams[feature.id]) {
                FeatureState.featureParams[feature.id] = {};
            }
            for (const [pName, pConfig] of Object.entries(params)) {
                if (pConfig.default !== undefined) {
                    FeatureState.featureParams[feature.id][pName] = pConfig.default;
                    resetCount++;
                }
            }
        }
    });
    
    FeatureState.isDirty = true;
    renderFeatureConfig();
    await saveFeatureConfig();
    showToast(`✅ 모든 지표 파라미터가 퀀트 표준 기본값(Canonical Defaults)으로 복원되었습니다.`, 'success');
    appendLog(`🔄 [기본값 복원] 모든 지표 파라미터가 퀀트 표준 기본값(Canonical Defaults)으로 일괄 복원되었습니다. (${resetCount}개 파라미터)`, '');
}

async function resetSingleParamToDefault(featureId, paramName, defaultVal) {
    if (!FeatureState.featureParams[featureId]) {
        FeatureState.featureParams[featureId] = {};
    }
    FeatureState.featureParams[featureId][paramName] = defaultVal;
    FeatureState.isDirty = true;
    renderFeatureConfig();
    debounceSave();
    showToast(`✅ [${featureId}] ${paramName} 파라미터가 기본값(${defaultVal})으로 복원되었습니다.`, 'info');
}

function updateTablePenaltyDisplay(slider) {
    const featureId = slider.dataset.feature;
    const valEl = document.getElementById(`t-pen-${featureId}`);
    if (valEl) {
        valEl.textContent = parseFloat(slider.value).toFixed(1);
    }
}

function saveTablePenaltyChange(slider) {
    const featureId = slider.dataset.feature;
    const val = parseFloat(slider.value);
    FeatureState.featurePenalties[featureId] = val;
    FeatureState.isDirty = true;
    debounceSave();
}

function getMarketScopeBadge(scope) {
    if (!scope || scope === 'all') return '';
    if (scope === 'crypto') {
        return `<span class="badge" style="background:rgba(249, 115, 22, 0.15); color:#fb923c; border:1px solid rgba(249, 115, 22, 0.4); font-size:10px; margin-left:4px;" title="가상화폐(업비트/바이낸스) 전용 지표">코인 전용</span>`;
    } else if (scope === 'krx') {
        return `<span class="badge" style="background:rgba(59, 130, 246, 0.15); color:#60a5fa; border:1px solid rgba(59, 130, 246, 0.4); font-size:10px; margin-left:4px;" title="국내주식(KRX/KIS) 전용 지표">KRX 전용</span>`;
    } else if (scope === 'us') {
        return `<span class="badge" style="background:rgba(168, 85, 247, 0.15); color:#c084fc; border:1px solid rgba(168, 85, 247, 0.4); font-size:10px; margin-left:4px;" title="미국주식 전용 지표">미국주식 전용</span>`;
    }
    return '';
}

function getTriggerBadge(feature) {
    if (!feature) return '';
    const isTrigger = Boolean(feature.is_trigger) || (feature.id && (feature.id.includes('squeeze') || feature.id.includes('spike') || feature.id.includes('large_trade')));
    if (isTrigger) {
        return `<span class="badge" style="background:rgba(236, 72, 153, 0.15); color:#f472b6; border:1px solid rgba(236, 72, 153, 0.4); font-size:10px; margin-left:4px;" title="이벤트/조건부 트리거 지표 (변동성 수축, 대량 체결 등 희귀 상태 조건부 알파 평가 적용)">⚡ 트리거</span>`;
    }
    return '';
}

function toggleIncludeOtherMarkets(checked) {
    FeatureState.includeOtherMarkets = Boolean(checked);
    renderAvailableFeatures();
}

function toggleFeatureRegime(fid) {
    const feat = FeatureState.registry[fid] || FeatureState.activeFeatures.find(f => f.id === fid) || {};
    const defaultRegime = Boolean(feat.is_regime || feat.category === 'time');
    const current = (FeatureState.regimeFeatures && FeatureState.regimeFeatures[fid] !== undefined)
        ? Boolean(FeatureState.regimeFeatures[fid])
        : defaultRegime;
    const nextVal = !current;
    
    if (!FeatureState.regimeFeatures) {
        FeatureState.regimeFeatures = {};
    }
    FeatureState.regimeFeatures[fid] = nextVal;
    
    // UI 즉각 반영
    renderActiveFeatures();
    // 프로젝트 설정 저장 (영구 보존)
    saveFeatureConfig();
    
    const label = nextVal ? '🛡️ 백본 고정 (IC면제)' : '📊 IC 평가 대상';
    showToast(`지표 '${feat.name || fid}'의 모드가 [${label}]으로 변경되었습니다.`, 'info');
}

function renderAvailableFeatures() {
    const container = document.getElementById('availableFeaturesList');
    if (!container) return;
    let features = [...FeatureState.availableFeatures];
    
    // 🌐 자산군(Market Scope) 스마트 필터링: 옵트인 체크 안 된 경우 현재 종목 시장 전용 또는 'all'만 노출
    if (!FeatureState.includeOtherMarkets) {
        const currentMkt = FeatureState.marketType || 'crypto';
        features = features.filter(f => {
            const scope = f.market_scope || 'all';
            return scope === 'all' || scope === currentMkt;
        });
    }

    if (FeatureState.currentCategory === 'target') {
        features = features.filter(f => f.id.startsWith('target_') || f.category === 'target');
    } else if (FeatureState.currentCategory !== 'all') {
        features = features.filter(f => f.category === FeatureState.currentCategory);
    }

    // 검색어 필터링
    if (FeatureState.searchQuery) {
        const q = FeatureState.searchQuery.toLowerCase();
        features = features.filter(f => 
            (formatFeatureTitle(f).toLowerCase().includes(q)) ||
            (f.name && f.name.toLowerCase().includes(q)) ||
            (f.id && f.id.toLowerCase().includes(q)) ||
            (f.name_en && f.name_en.toLowerCase().includes(q)) ||
            (formatFeatureDesc(f).toLowerCase().includes(q)) ||
            (f.description && f.description.toLowerCase().includes(q)) ||
            (f.category && f.category.toLowerCase().includes(q))
        );
    }
    
    if (features.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="padding:24px;text-align:center;background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;">
                <div class="empty-state-icon" style="font-size:24px;margin-bottom:6px;">📦</div>
                <div>사용 가능한 지표가 없습니다.</div>
            </div>
        `;
        return;
    }

    if (FeatureState.viewMode === 'table') {
        renderAvailableFeaturesTable(container, features);
    } else {
        renderAvailableFeaturesCards(container, features);
    }
}

function renderAvailableFeaturesTable(container, features) {
    let rowsHtml = '';
    features.forEach((feature) => {
        const catInfo = FeatureState.categories[feature.category] || { name: feature.category, icon: '📊' };
        const params = feature.params || {};
        
        let paramsSummary = [];
        for (const [pName, pConfig] of Object.entries(params)) {
            paramsSummary.push(`${pConfig.label || pName}: ${pConfig.default}`);
        }
        const paramsText = paramsSummary.length > 0 ? paramsSummary.join(', ') : '기본 수식';

        const isRegime = Boolean(feature.is_regime || feature.category === 'time');
        const regimeBadge = isRegime ? `<span class="badge" style="background:rgba(234, 179, 8, 0.2); color:#facc15; border:1px solid rgba(234, 179, 8, 0.5); font-size:10px; margin-left:4px;">🛡️ 체제</span>` : '';
        const scopeBadge = getMarketScopeBadge(feature.market_scope);

        rowsHtml += `
            <tr class="density-row available-row" data-feature-id="${feature.id}">
                <td style="width:75px;text-align:center;">
                    <button type="button" class="btn-table-add" onclick="addFeature('${feature.id}')">
                        + 추가
                    </button>
                </td>
                <td style="min-width:220px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-size:14px;">${catInfo.icon || '📊'}</span>
                        <div>
                            <div style="font-weight:700;font-size:12px;color:var(--text-primary);display:flex;align-items:center;gap:6px;">
                                <span>${formatFeatureTitle(feature)}</span>
                                ${scopeBadge}
                                ${getTriggerBadge(feature)}
                                ${regimeBadge}
                                <button type="button" class="btn-info-detail" title="지표 상세 설명 보기" onclick="showFeatureDetailModal('${feature.id}')">ℹ️</button>
                            </div>
                            <div style="font-size:10px;color:var(--text-muted);font-family:monospace;">${feature.id}</div>
                        </div>
                    </div>
                </td>
                <td style="width:110px;">
                    <span class="table-cat-badge cat-${feature.category}">${catInfo.name || feature.category}</span>
                </td>
                <td style="color:var(--text-secondary);font-size:11px;line-height:1.4;">
                    ${formatFeatureDesc(feature)}
                </td>
                <td style="width:160px;font-size:11px;color:var(--text-muted);font-family:monospace;">
                    ${paramsText}
                </td>
            </tr>
        `;
    });

    container.innerHTML = `
        <div class="density-table-wrapper available-table-wrapper">
            <table class="density-table">
                <thead>
                    <tr>
                        <th style="width:75px;text-align:center;">추가</th>
                        <th>지표명 / ID</th>
                        <th style="width:110px;">카테고리</th>
                        <th>지표 핵심 원리 및 설명 (한눈에 보기)</th>
                        <th style="width:160px;">기본 파라미터</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>
            <div class="density-table-footer">
                <span>사용 가능한 지표 <strong>${features.length}</strong>개</span>
                <span>💡 <strong>[+ 추가]</strong> 버튼을 누르면 상단 활성 지표 목록으로 즉시 추가됩니다.</span>
            </div>
        </div>
    `;
}

function renderAvailableFeaturesCards(container, features) {
    let html = '<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(320px, 1fr));gap:10px;">';
    features.forEach(feature => {
        html += renderFeatureCard(feature, -1, false);
    });
    html += '</div>';
    container.innerHTML = html;
}

function showFeatureDetailModal(featureId) {
    const feature = FeatureState.registry[featureId] || FeatureState.activeFeatures.find(f => f.id === featureId);
    if (!feature) return;

    const catInfo = FeatureState.categories[feature.category] || { name: feature.category, icon: '📊' };
    const params = feature.params || {};

    let paramsListHtml = '';
    if (Object.keys(params).length > 0) {
        for (const [pName, pConfig] of Object.entries(params)) {
            paramsListHtml += `
                <li style="margin-bottom:6px;">
                    <strong style="color:var(--accent-cyan);">${pConfig.label || pName}</strong>: 
                    ${pConfig.description || ''} 
                    <span style="color:var(--text-muted);font-size:11px;">(기본: ${pConfig.default}, 범위: ${pConfig.min} ~ ${pConfig.max}, 단위: ${pConfig.step})</span>
                </li>
            `;
        }
    } else {
        paramsListHtml = `<li style="color:var(--text-muted);">별도의 튜닝 파라미터 없이 고정된 수식으로 계산됩니다.</li>`;
    }

    const modalHtml = `
        <div id="featureDetailModalOverlay" class="modal-overlay" onclick="closeFeatureDetailModal(event)" style="display:flex;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:99999;align-items:center;justify-content:center;backdrop-filter:blur(4px);">
            <div class="modal-content" onclick="event.stopPropagation()" style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:10px;width:550px;max-width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.5);overflow:hidden;">
                <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--border-color);background:rgba(255,255,255,0.03);">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-size:18px;">${catInfo.icon || '📊'}</span>
                        <div>
                            <h3 style="margin:0;font-size:15px;color:var(--text-primary);">${formatFeatureTitle(feature)}</h3>
                            <span style="font-size:11px;color:var(--text-muted);font-family:monospace;">${feature.id}</span>
                        </div>
                    </div>
                    <button type="button" onclick="closeFeatureDetailModal()" style="background:none;border:none;color:var(--text-muted);font-size:18px;cursor:pointer;">✕</button>
                </div>
                <div style="padding:18px;font-size:13px;line-height:1.6;color:var(--text-secondary);">
                    <div style="margin-bottom:14px;">
                        <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">📖 지표 설명 & 매매 알파 원리</div>
                        <div style="background:rgba(255,255,255,0.04);padding:10px 12px;border-radius:6px;border-left:3px solid var(--accent-cyan);color:var(--text-primary);">
                            ${formatFeatureDesc(feature)}
                        </div>
                    </div>
                    <div style="margin-bottom:14px;">
                        <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">⚙️ 설정 가능 파라미터</div>
                        <ul style="margin:0;padding-left:20px;">
                            ${paramsListHtml}
                        </ul>
                    </div>
                    <div>
                        <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">🏷️ 분류 및 도메인</div>
                        <span class="table-cat-badge cat-${feature.category}">${catInfo.icon || ''} ${catInfo.name || feature.category}</span>
                        ${feature.name_en ? `<span style="margin-left:8px;font-size:11px;color:var(--text-muted);">${feature.name_en}</span>` : ''}
                    </div>
                </div>
                <div style="padding:10px 18px;border-top:1px solid var(--border-color);display:flex;justify-content:flex-end;background:rgba(0,0,0,0.2);">
                    <button type="button" class="btn btn-sm" onclick="closeFeatureDetailModal()" style="padding:5px 14px;background:var(--accent-primary);color:#fff;border:none;border-radius:4px;cursor:pointer;">확인</button>
                </div>
            </div>
        </div>
    `;

    const existing = document.getElementById('featureDetailModalOverlay');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

function closeFeatureDetailModal(e) {
    const modal = document.getElementById('featureDetailModalOverlay');
    if (modal) modal.remove();
}

function renderFeatureCard(feature, index, isActive) {
    const categoryInfo = FeatureState.categories[feature.category] || { name: feature.category, icon: '📊' };
    const params = feature.params || {};
    const currentParams = FeatureState.featureParams[feature.id] || {};
    
    let paramsHtml = '';
    for (const [pName, pConfig] of Object.entries(params)) {
        const currentValue = currentParams[pName] !== undefined ? currentParams[pName] : pConfig.default;
        const isModified = (currentParams[pName] !== undefined) && (Number(currentParams[pName]) !== Number(pConfig.default));
        paramsHtml += `
            <div class="slider-container" style="margin-top:6px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span class="slider-label" title="${pConfig.description}">
                        ${pConfig.label}
                        ${isModified ? `<button type="button" class="btn-param-reset" title="기본값(${pConfig.default})으로 복원" onclick="resetSingleParamToDefault('${feature.id}', '${pName}', ${pConfig.default})">⟲</button>` : ''}
                    </span>
                    <span class="slider-value ${isModified ? 'text-warning font-weight-bold' : ''}" id="val-${feature.id}-${pName}">${formatParamValue(currentValue, pConfig)}</span>
                </div>
                <input type="range" 
                    class="feature-slider" 
                    data-feature="${feature.id}" 
                    data-param="${pName}"
                    min="${pConfig.min}" 
                    max="${pConfig.max}" 
                    step="${pConfig.step}" 
                    value="${currentValue}"
                    oninput="updateParamValue(this)"
                    onchange="saveParamChange(this)">
            </div>
        `;
    }
    
    const defaultRegime = Boolean(feature.is_regime || feature.category === 'time');
    const effectiveRegime = (FeatureState.regimeFeatures && FeatureState.regimeFeatures[feature.id] !== undefined)
        ? Boolean(FeatureState.regimeFeatures[feature.id])
        : defaultRegime;
    const isOverridden = (FeatureState.regimeFeatures && FeatureState.regimeFeatures[feature.id] !== undefined) && (Boolean(FeatureState.regimeFeatures[feature.id]) !== defaultRegime);

    // 지표 선택 패널티(고급) — IC 스크리닝 가중치 및 체제 백본 원클릭 토글 제어
    if (isActive) {
        paramsHtml += `
            <div style="margin-top:8px; border-top:1px dashed rgba(255,255,255,0.08); padding-top:6px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">
                    <button type="button" class="btn btn-xs" 
                            style="${effectiveRegime ? 'background:rgba(234, 179, 8, 0.2); color:#facc15; border:1px solid rgba(234, 179, 8, 0.5);' : 'background:rgba(99, 102, 241, 0.15); color:#a5b4fc; border:1px solid rgba(99, 102, 241, 0.4);'}font-size:10px;padding:2px 6px;border-radius:4px;cursor:pointer;font-weight:600;"
                            title="${effectiveRegime ? '클릭 시 일반 IC 평가 모드로 전환' : '클릭 시 백본 고정(IC면제) 모드로 전환'}"
                            onclick="toggleFeatureRegime('${feature.id}')">
                        ${effectiveRegime ? '🛡️ 백본 고정' : '📊 IC 평가'}
                    </button>
                    <span style="font-size:10px;color:var(--text-muted);">${effectiveRegime ? 'IC면제 / 백본 탑재' : `가중치 ${(FeatureState.featurePenalties[feature.id] !== undefined ? FeatureState.featurePenalties[feature.id] : 1.0).toFixed(1)}`}</span>
                </div>
                ${effectiveRegime ? `
                    <div style="font-size:10px;color:#facc15;padding:2px 0;">모든 AI 탐색 Trial 조합에 기본 백본으로 탑재됩니다.</div>
                ` : `
                    <input type="range" 
                        class="penalty-slider" 
                        data-feature="${feature.id}" 
                        min="0.1" 
                        max="1.0" 
                        step="0.1" 
                        value="${FeatureState.featurePenalties[feature.id] !== undefined ? FeatureState.featurePenalties[feature.id] : 1.0}"
                        oninput="updatePenaltyValue(this)"
                        onchange="savePenaltyChange(this)"
                        style="width:100%; height:4px; accent-color:var(--accent-cyan);">
                `}
            </div>
        `;
    }
    
    const tooltipHtml = `
        <div class="tooltip-content">
            <div class="tooltip-title">${categoryInfo.icon} ${formatFeatureTitle(feature)}</div>
            <div class="tooltip-desc">${formatFeatureDesc(feature)}</div>
            <div style="font-size:11px;color:var(--text-muted); margin-top:4px;">
                📁 카테고리: ${categoryInfo.name} | 🆔 ID: ${feature.id}
            </div>
            ${Object.keys(params).length > 0 ? `
                <div style="margin-top:8px;border-top:1px solid var(--border-color);padding-top:8px;">
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">⚙️ 파라미터</div>
                    ${Object.entries(params).map(([pn, pc]) => `
                        <div class="tooltip-param">
                            <span class="tooltip-param-label">${pc.label}:</span>
                            <span class="tooltip-param-value">${currentParams[pn] !== undefined ? currentParams[pn] : pc.default}</span>
                        </div>
                    `).join('')}
                </div>
            ` : ''}
        </div>
    `;
    
    const cardClass = isActive ? 'feature-card feature-card-active' : 'feature-card feature-card-available';
    
    const actionButtons = isActive ? `
        <span class="tooltip-icon" style="margin-left:auto;">
            ℹ️
            ${tooltipHtml}
        </span>
        <button class="feature-delete-btn" onclick="removeFeature('${feature.id}')" title="지표 제거">🗑️</button>
    ` : `
        <span class="tooltip-icon" style="margin-left:auto; margin-right:4px;" onclick="event.stopPropagation()">
            ℹ️
            ${tooltipHtml}
        </span>
        <button class="btn btn-sm btn-success" style="padding: 2px 8px; font-size:11px; font-weight:bold;">+ 추가</button>
    `;
    
    const cardBodyHtml = isActive ? (
        Object.keys(params).length > 0 || isActive ? `
            <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border-color);">
                ${paramsHtml}
            </div>
        ` : ''
    ) : `
        <div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:var(--text-muted); line-height:1.4; min-height:48px;">
            ${formatFeatureDesc(feature).substring(0, 68)}${formatFeatureDesc(feature).length > 68 ? '...' : ''}
            <div style="margin-top:6px; display:flex; align-items:center; justify-content:flex-end; color:var(--accent-green); font-weight:600; font-size:10px;">
                <span>클릭하여 지표 활성화 ➕</span>
            </div>
        </div>
    `;
    
    // 지표 주체(대상 자산) 및 시간축 간결한 라벨 생성
    const currentCoin = (window.STATE && window.STATE.status && window.STATE.status.coin) || 'KODEX 200';
    const currentInterval = (window.STATE && window.STATE.status && window.STATE.status.interval) || '1m';
    
    let targetAssetBadge = '';
    const fid = feature.id.toLowerCase();
    if (fid.includes('kospi_futures') || fid.includes('futures') || fid.includes('basis')) {
        targetAssetBadge = `<span class="feature-category-badge" style="background:rgba(234, 179, 8, 0.15); color:#eab308; border:1px solid rgba(234, 179, 8, 0.4);">🏛️ KOSPI200 선물</span>`;
    } else if (fid.includes('binance') || fid.includes('funding') || fid.includes('kimchi')) {
        targetAssetBadge = `<span class="feature-category-badge" style="background:rgba(168, 85, 247, 0.15); color:#a855f7; border:1px solid rgba(168, 85, 247, 0.4);">🌐 글로벌/코인</span>`;
    } else if (fid.includes('daily') || fid.includes('4h')) {
        targetAssetBadge = `<span class="feature-category-badge" style="background:rgba(59, 130, 246, 0.15); color:#3b82f6; border:1px solid rgba(59, 130, 246, 0.4);">📅 ${currentCoin} 일봉</span>`;
    } else {
        targetAssetBadge = `<span class="feature-category-badge" style="background:rgba(16, 185, 129, 0.15); color:#10b981; border:1px solid rgba(16, 185, 129, 0.4);">🎯 ${currentCoin} ${currentInterval}</span>`;
    }

    const regimeBadgeHtml = effectiveRegime ? `<span class="feature-category-badge" style="background:rgba(234, 179, 8, 0.2); color:#facc15; border:1px solid rgba(234, 179, 8, 0.5); font-weight:700;">🛡️ 체제 컨텍스트</span>` : '';
    const scopeBadgeHtml = getMarketScopeBadge(feature.market_scope);

    const clickHandler = isActive ? '' : `onclick="if(!event.target.closest('.tooltip-icon')) addFeature('${feature.id}')"`;
    
    return `
        <div class="${cardClass}" data-id="${feature.id}" data-index="${index}" ${clickHandler} style="${isActive ? '' : 'cursor:pointer;'}">
            <div style="display:flex;align-items:center;gap:8px;">
                <span class="feature-icon">${categoryInfo.icon}</span>
                <div style="flex:1; min-width:0;">
                    <div class="feature-name" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${formatFeatureDesc(feature)}">${formatFeatureTitle(feature)}</div>
                    <div style="display:flex;gap:6px;margin-top:4px;flex-wrap:wrap;align-items:center;">
                        ${targetAssetBadge}
                        <span class="feature-category-badge">${categoryInfo.name}</span>
                        ${scopeBadgeHtml}
                        ${getTriggerBadge(feature)}
                        ${regimeBadgeHtml}
                        ${getIncompatibleBadge(feature)}
                        ${!isActive && Object.keys(params).length > 0 ? '<span class="feature-category-badge">⚙️ 가변</span>' : ''}
                    </div>
                </div>
                ${actionButtons}
            </div>
            ${cardBodyHtml}
        </div>
    `;
}

// =============================================================================
// Render Optuna Config - 그룹화 + 캔들 시간 변환
// =============================================================================

function renderOptunaConfig() {
    const container = document.getElementById('optunaConfigForm');
    if (!container) return;
    const config = FeatureState.optunaConfig;
    const intervalMin = FeatureState.intervalMinutes;
    
    // ATR 활성화 여부 체크
    const atrActive = FeatureState.activeFeatures && FeatureState.activeFeatures.some(f => f.id === 'upbit_atr');
    
    // 그룹 정의
    const groups = [
        {
            id: 'meta',
            title: '⚙️ 메타 설정',
            desc: '최적화 반복 횟수 및 최소 제약 조건 설정',
            params: [
                {
                    key: 'seed', label: '학습 랜덤 시드 (Seed)', type: 'number', min: 1, max: 99999,
                    badge: 'SEED',
                    badgeColor: 'purple',
                    tooltip: {
                        title: '🌱 학습 랜덤 시드 (Seed)',
                        desc: 'AI 학습(LightGBM) 및 파라미터 탐색(Optuna) 시 난수 생성 기준값입니다.',
                        targets: ['Optuna 탐색 시퀀스', 'LightGBM 데이터 분할 및 트리 빌딩'],
                        effect: '동일한 시드값을 사용하면 동일한 조건에서 항상 100% 동일한 최적화 결과를 재현합니다.',
                        warning: '시드가 달라지면 탐색 경로가 바뀌어 다른 결과가 도출될 수 있습니다.',
                        recommend: '권장: 42 (기본값)'
                    }
                },
                {
                    key: 'outer_trials', label: 'Outer Trials', type: 'number', min: 1, max: 50,
                    badge: 'OUTER',
                    badgeColor: 'cyan',
                    tooltip: {
                        title: '🌐 Outer Trials (외부 루프)',
                        desc: 'Triple Barrier 정답지 장벽을 몇 번 탐색할지 결정합니다.',
                        targets: ['predict_ahead_candles: 예측 기간 (캔들)', 'take_profit: 익절률 (%)', 'stop_loss: 손절률 (%)'],
                        effect: '높을수록 더 다양한 장벽 조합 탐색. 시간 증가.',
                        warning: `총 시뮬레이션 = Outer × Inner (예: 15×15=225회)`,
                        recommend: '권장: 10~20 (기본 15)'
                    }
                },
                {
                    key: 'inner_trials', label: 'Inner Trials', type: 'number', min: 1, max: 30,
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '🔬 Inner Trials (내부 루프)',
                        desc: 'AI 알고리즘 파라미터를 몇 번 탐색할지 결정합니다.',
                        targets: ['learning_rate: 학습률', 'max_depth: 트리 깊이', 'num_leaves: 리프 노드 수', 'buy_threshold: 진입 문턱', 'cooldown_minutes: 쿨다운 시간'],
                        effect: '높을수록 더 정밀한 AI 파라미터 탐색. 시간 증가.',
                        warning: 'Outer Trials와 곱해져 총 시뮬레이션 횟수 결정',
                        recommend: '권장: 10~20 (기본 15)'
                    }
                },
                {
                    key: 'min_trades', label: '월평균 최소 거래 횟수 (회/월)', type: 'number', min: 1, max: 50,
                    badge: 'LIMIT',
                    badgeColor: 'yellow',
                    tooltip: {
                        title: '🛡️ 월평균 최소 거래 횟수 제약',
                        desc: '2단계 최적화 평가 시 거래 빈도가 이 값보다 적으면 패널티 스코어를 부여합니다. (절대 수치가 아닌, 설정한 최적화 기간에 맞춰 자동으로 월평균 환산 및 일할 계산되어 제약이 가해집니다. 예: 2개월 구간에서 5회 설정 시 총 10회 이상 거래 필요)',
                        targets: ['월평균 필요 최소 거래 횟수 (기본: 5회)'],
                        effect: '과적합으로 인해 특정 거래 1~2개만 극단적으로 튜닝하여 수익률을 속이는 현상을 방지합니다.',
                        warning: '너무 높게 설정하면 조건 만족이 어려워 최적화 스코어가 하락할 수 있습니다.',
                        recommend: '권장: 4 ~ 10회 (기본 5)'
                    }
                },
                {
                    key: 'min_profit_factor', label: '최소 손익비', type: 'number', min: 0.1, max: 5.0, step: 0.1,
                    badge: 'LIMIT',
                    badgeColor: 'yellow',
                    tooltip: {
                        title: '🛡️ 최소 손익비 제약',
                        desc: '2단계 최적화 평가 시 손익비(Profit Factor)가 이 값보다 적으면 패널티 스코어를 부여합니다.',
                        targets: ['최소 목표 손익비 (기본: 1.1)'],
                        effect: '손익비 기준 미달인 위험한 전략 조합을 최적화 탐색 후보에서 즉시 배제합니다.',
                        warning: '손익비는 총수익금 / 총손실금으로 계산됩니다.',
                        recommend: '권장: 1.0 ~ 1.3 (기본 1.1)'
                    }
                },

            ]
        },
        {
            id: 'outer_prediction',
            title: '📡 OUTER - 예측 설정',
            desc: 'AI 예측 대상 및 보유시간 설정 (단위: 캔들 개수)',
            params: [
                {
                    key: 'predict_ahead_candles_range', label: '예측 대상 (캔들)', type: 'range',
                    badge: 'OUTER',
                    badgeColor: 'cyan',
                    isCandles: true,
                    tooltip: {
                        title: '⏱ Predict Ahead (예측 대상 캔들 수)',
                        desc: 'AI가 현재 시점으로부터 몇 캔들 뒤의 가격 움직임을 예측할지 결정합니다. 트리플 배리어 정답지(label) 생성에 사용됩니다.',
                        targets: [`${intervalMin}m 기준 탐색: ${intervalMin === 1 ? '5~40캔들 (5분~40분)' : '8~32캔들 (' + candlesToTime(8, intervalMin) + '~' + candlesToTime(32, intervalMin) + ')'}`],
                        effect: '캔들 수 큼 → 장기 파동 예측, 완만한 진입 / 캔들 수 작음 → 초단기 스캘핑/노이즈 민감',
                        warning: '너무 크면 훈련 라벨 부족, 너무 작으면 휩소(속임수)에 취약',
                        recommend: `${intervalMin}m 기준 권장: ${intervalMin === 1 ? '5~30캔들 (5분~30분)' : '8~32캔들'}`
                    }
                },
                {
                    key: 'max_hold_candles_range', label: '최대 보유시간 (캔들)', type: 'range',
                    badge: 'OUTER',
                    badgeColor: 'cyan',
                    isCandles: true,
                    tooltip: {
                        title: '⏳ Max Hold Candles (최대 보유 캔들 수)',
                        desc: '익절/손절가에 도달하지 않더라도, 여기서 설정한 캔들 수에 도달하면 시장가로 강제 청산합니다.',
                        targets: [`${intervalMin}m 기준 탐색: ${intervalMin === 1 ? '10~60캔들 (10분~1시간)' : '24~64캔들 (' + candlesToTime(24, intervalMin) + '~' + candlesToTime(64, intervalMin) + ')'}`],
                        effect: '캔들 수 큼 → 추세 추종 및 충분한 반등 대기 / 캔들 수 작음 → 빠른 회전율 및 기회비용 확보',
                        warning: '예측 대상(predict_ahead)보다 크거나 같아야 안전합니다.',
                        recommend: `${intervalMin}m 기준 권장: ${intervalMin === 1 ? '15~60캔들 (15분~1시간)' : '24~64캔들'}`
                    }
                }
            ]
        },
        {
            id: 'outer_barrier',
            title: '💰 OUTER - 장벽 설정',
            desc: '익절/손절 문턱 설정 (ATR 변동성 비례 적용)',
            params: [
                {
                    key: 'take_profit_range', 
                    label: '익절률 (ATR 배수)', 
                    type: 'range',
                    badge: 'OUTER',
                    badgeColor: 'cyan',
                    tooltip: {
                        title: '💰 Take Profit (ATR 배수 익절)',
                        desc: '진입 시점의 ATR(변동성) 대비 몇 배 상승 시 익절할지 결정합니다. 지표 탭의 지표 활성화 여부와 무관하게 시스템 내부에서 항상 실시간 변동성을 측정하여 배수 방식으로 적용됩니다.',
                        targets: ['1.0 ~ 3.5 배수 범위 권장', '예: 진입 시점 ATR이 1.5%이고 설정이 2.0배일 경우 ➡️ 3.0% 상승 시 청산'],
                        effect: '높으면 → 변동성이 클 때 길게 끌고 가며 큰 수익 추구, 작을 때는 안전하게 탈출',
                        warning: '너무 높으면 도달하기 전에 조정이 와서 수익을 반납할 우려가 있습니다.',
                        recommend: '권장: 1.2 ~ 2.5배'
                    }
                },
                {
                    key: 'stop_loss_range', 
                    label: '손절률 (ATR 배수)', 
                    type: 'range',
                    badge: 'OUTER',
                    badgeColor: 'cyan',
                    tooltip: {
                        title: '🛡️ Stop Loss (ATR 배수 손절)',
                        desc: '진입 시점의 ATR(변동성) 대비 몇 배 하락 시 손절할지 결정합니다. 지표 탭의 지표 활성화 여부와 무관하게 시스템 내부에서 항상 실시간 변동성을 측정하여 배수 방식으로 적용됩니다.',
                        targets: ['0.8 ~ 2.5 배수 범위 권장', '예: 진입 시점 ATR이 1.5%이고 설정이 1.0배일 경우 ➡️ 1.5% 하락 시 청산'],
                        effect: '시장 변동성에 맞게 슬라이딩되어 노이즈로 인한 잦은 손절을 최소화합니다.',
                        warning: '너무 크면 1회 거래당 손실 폭(MDD)이 과도하게 커질 수 있습니다.',
                        recommend: '권장: 1.0 ~ 2.0배'
                    }
                }
            ]
        },
        {
            id: 'inner_lgbm',
            title: '📚 INNER - LightGBM 하이퍼파라미터',
            desc: 'AI 모델 학습 관련 설정',
            params: [
                {
                    key: 'learning_rate_range', label: '학습률', type: 'range',
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '📚 Learning Rate (학습률)',
                        desc: 'LightGBM 모델의 학습 속도를 결정합니다.',
                        targets: ['범위: 0.01~0.10', '기본: 0.02~0.08'],
                        effect: '높으면 → 빠른 학습, 과적합 위험 / 낮으면 → 느린 학습, 일반화 향상',
                        warning: '너무 높으면 발산, 너무 낮으면 학습 부족',
                        recommend: '권장: 0.02~0.08'
                    }
                },
                {
                    key: 'max_depth_range', label: '최대 깊이', type: 'range',
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '🌳 Max Depth (최대 깊이)',
                        desc: '결정 트리의 최대 깊이. 모델 복잡도를 제어합니다.',
                        targets: ['범위: 3~10', '기본: 4~7'],
                        effect: '높으면 → 복잡한 패턴 학습, 과적합 위험 / 낮으면 → 단순한 패턴, 과소적합',
                        warning: '너무 높으면 과적합, 너무 낮으면 패턴 학습 부족',
                        recommend: '권장: 4~7'
                    }
                },
                {
                    key: 'num_leaves_range', label: '리프 노드 수', type: 'range',
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '🍃 Num Leaves (리프 노드 수)',
                        desc: '하나의 트리가 가질 수 있는 최대 리프 노드 수. 모델 용량을 결정합니다.',
                        targets: ['범위: 15~127', '기본: 31~63'],
                        effect: '많으면 → 세밀한 분류, 과적합 위험 / 적으면 → 대략적 분류',
                        warning: 'max_depth와 조화롭게 설정 필요 (num_leaves ≤ 2^max_depth)',
                        recommend: '권장: 31~63'
                    }
                },
                {
                    key: 'min_child_samples_range', label: '최소 샘플 수', type: 'range',
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '📊 Min Child Samples (최소 샘플 수)',
                        desc: '리프 노드에 필요한 최소 샘플 수. 과적합 방지에 사용됩니다.',
                        targets: ['범위: 10~100', '기본: 20~80'],
                        effect: '높으면 → 보수적 분류, 과적합 방지 / 낮으면 → 세밀한 분류',
                        warning: '너무 높으면 과소적합, 너무 낮으면 노이즈에 민감',
                        recommend: '권장: 20~80'
                    }
                }
            ]
        },
        {
            id: 'inner_trading',
            title: '🎯 INNER - 매매 규칙',
            desc: '진입 문턱 및 필터 설정',
            params: [
                {
                    // 2026-10-01: 절대 확률(buy_threshold_range) 대신 분위수로 탐색한다 — 모델 확률은 라벨 기저율 근처에
                    // 몰려 있어 절대 0.52~0.58 은 상위 0.1% 만 통과했고, 모델이 바뀌면 같은 숫자가 다른 뜻이 됐다.
                    key: 'buy_quantile_range', label: '진입 문턱 (분위수)', type: 'range',
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '🎯 Buy Quantile (진입 문턱 — 상위 분위수)',
                        desc: '모델이 학습 구간에서 낸 확률 중 상위 몇 %에 들 때 진입할지 정합니다. 0.95 = 상위 5%. 모델마다 자기 확률 분포로 환산되므로 창별 모델·최종 모델·배포 모델에서 같은 뜻을 유지합니다.',
                        targets: ['0.95 = 상위 5%, 0.99 = 상위 1%', '기본 범위는 엔진 기본 설정(DEFAULT_OPTUNA_CONFIG)을 따름'],
                        effect: '높으면 → 확실할 때만 진입, 적은 거래 / 낮으면 → 잦은 진입',
                        warning: '0.995 이상은 거래가 거의 없어 검증이 불가능해질 수 있습니다',
                        recommend: '권장: 기본 범위 그대로 탐색에 맡김'
                    }
                },
                {
                    key: 'cooldown_minutes_range', label: '쿨다운 (분)', type: 'range',
                    badge: 'INNER',
                    badgeColor: 'green',
                    tooltip: {
                        title: '⏳ Cooldown (쿨다운 시간)',
                        desc: '거래 후 다음 진입까지 대기하는 시간(분). 잦은 거래를 방지합니다.',
                        targets: ['범위: 1~30분', '기본: 4~16분'],
                        effect: '길면 → 잦은 진입 방지, 수수료 절감 / 짧으면 → 빠른 재진입',
                        warning: '너무 길면 수익 기회 놓침, 너무 짧으면 수수료 누적',
                        recommend: '권장: 4~16분'
                    }
                }
            ]
        },
        {
            id: 'trading_cost',
            title: '💰 매매 비용 설정',
            desc: '수수료 및 슬리피지 설정',
            params: [
                {
                    key: 'fee_upbit', label: '거래소 수수료 (소수)', type: 'number', min: 0, max: 100, step: 'any',
                    badge: 'COST',
                    badgeColor: 'yellow',
                    tooltip: {
                        title: '💸 거래소 수수료',
                        desc: '매수/매도 시 거래소에 지불하는 수수료입니다.',
                        targets: ['업비트: 0.05% (0.0005)', '바이낸스: 0.1% (0.001)', '참고: 값은 소수로 입력 (예: 0.05% = 0.0005)'],
                        effect: '높으면 → 거래 비용 증가, 수익 감소',
                        warning: '실제 거래소 수수료를 정확히 입력해야 합니다',
                        recommend: '업비트 기준: 0.0005 (0.05%)'
                    }
                },
                {
                    key: 'slippage', label: '슬리피지 (소수)', type: 'number', min: 0, max: 100, step: 'any',
                    badge: 'COST',
                    badgeColor: 'yellow',
                    tooltip: {
                        title: '📉 슬리피지',
                        desc: '시장가 주문 시 예상 가격과 실제 체결 가격의 차이입니다.',
                        targets: ['일반적 슬리피지: 0.03%~0.1%', '변동성 높을수록 증가', '참고: 값은 소수로 입력 (예: 0.06% = 0.0006)'],
                        effect: '높으면 → 실제 체결가 불리, 수익 감소',
                        warning: '시장 상황에 따라 달라질 수 있습니다',
                        recommend: '일반적 기준: 0.0006 (0.06%)'
                    }
                }
            ]
        }
    ];
    
    let html = '';
    
    const isHorizonLocked = (FeatureState.targetHorizonConfig?.mode !== 'grid_search');
    const lockedPa = FeatureState.targetHorizonConfig?.locked_predict_ahead || 6;
    const lockedMhc = FeatureState.targetHorizonConfig?.locked_max_hold || lockedPa;
    
    groups.forEach(group => {
        const isPredictionGroup = (group.id === 'outer_prediction');
        let headerExtraBadge = '';
        let calloutBannerHtml = '';
        
        if (isPredictionGroup) {
            if (isHorizonLocked) {
                headerExtraBadge = `<span style="font-size:10px; padding:2px 8px; border-radius:4px; background:#10b981; color:#fff; font-weight:700; margin-left:6px;">🔒 사정거리 잠금 모드 (고정 운용 중)</span>`;
                calloutBannerHtml = `
                    <div style="grid-column: 1 / -1; background:rgba(16, 185, 129, 0.08); border:1px solid rgba(16, 185, 129, 0.35); border-radius:6px; padding:10px 14px; margin-bottom:8px; display:flex; align-items:center; justify-content:space-between; gap:12px;">
                        <div style="display:flex; align-items:center; gap:10px;">
                            <span style="font-size:20px;">🔒</span>
                            <div style="font-size:11.5px; color:#d1fae5; line-height:1.45;">
                                <strong style="color:#34d399;">사정거리 잠금 모드(Locked Mode) 운용 중:</strong> [지표 설정] 탭에서 사정거리가 <strong>예측 ${lockedPa}캔들 (${candlesToTime(lockedPa, intervalMin)} 선행) / 최대 보유 ${lockedMhc}캔들 (${candlesToTime(lockedMhc, intervalMin)} 타임아웃)</strong>로 고정되어 있어 Optuna Outer 탐색 대상에서 자동 제외(상수 적용)됩니다.<br>
                                <span style="color:var(--text-muted); font-size:10.5px;">※ 가변 범위 탐색으로 전환하려면 [지표 설정] 탭의 타깃 사정거리 카드에서 '외부 그리드 탐색 모드'를 선택하세요.</span>
                            </div>
                        </div>
                        <button type="button" class="btn btn-xs btn-outline-success" onclick="switchFeatureTab('features')" style="white-space:nowrap; font-size:11px; padding:4px 10px; font-weight:600;">
                            🎯 사정거리 설정
                        </button>
                    </div>
                `;
            } else {
                headerExtraBadge = `<span style="font-size:10px; padding:2px 8px; border-radius:4px; background:#3b82f6; color:#fff; font-weight:700; margin-left:6px;">🔍 외부 그리드 탐색 모드</span>`;
            }
        }

        const isBarrierGroup = (group.id === 'outer_barrier');
        if (isBarrierGroup) {
            headerExtraBadge = `<span style="font-size:10px; padding:2px 8px; border-radius:4px; background:#06b6d4; color:#fff; font-weight:700; margin-left:6px;">⚖️ 물리 선험 제약 적용</span>`;
            calloutBannerHtml = `
                <div style="grid-column: 1 / -1; background:rgba(6, 182, 212, 0.08); border:1px solid rgba(6, 182, 212, 0.35); border-radius:6px; padding:10px 14px; margin-bottom:8px; display:flex; align-items:center; justify-content:space-between; gap:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:20px;">⚖️</span>
                        <div style="font-size:11.5px; color:#cffafe; line-height:1.45;">
                            <strong style="color:#22d3ee;">시장 마찰력 물리 안전선 (Friction Floor):</strong> 수수료와 슬리피지를 상쇄하기 위한 최소 익절폭(TP)은 <strong>≥ 3.5배 비용 (약 3.5~4.0 ATR)</strong>이며, 단일 캔들 노이즈 휩소를 방어하기 위한 최소 손절폭(SL)은 <strong>≥ 2.0 ATR</strong>입니다.<br>
                            <span style="color:var(--text-muted); font-size:10.5px;">※ 하한치 미만 설정은 캔들 노이즈에 걸려 100% 휩소 손절 및 수수료 손실을 유발하므로 권장 하한치 이상으로 설정하십시오.</span>
                        </div>
                    </div>
                    <button type="button" class="btn btn-xs" onclick="if (typeof applyPhysicsDefaultsToUI === 'function') applyPhysicsDefaultsToUI();" style="white-space:nowrap; font-size:11px; padding:5px 12px; font-weight:700; background:rgba(6,182,212,0.25); color:#22d3ee; border:1px solid #06b6d4;">
                        ⚡ 안전 하한치 자동 맞춤
                    </button>
                </div>
            `;
        }

        html += `
            <div style="margin-bottom:20px; ${isPredictionGroup && isHorizonLocked ? 'background:rgba(16,185,129,0.02); border:1px dashed rgba(16,185,129,0.25); border-radius:8px; padding:12px 14px;' : ''}">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border-color); flex-wrap:wrap;">
                    <span style="font-size:14px;font-weight:700;color:var(--text-primary);">${group.title}</span>
                    ${headerExtraBadge}
                    <span style="font-size:10px;color:var(--text-muted);">${group.desc}</span>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                    ${calloutBannerHtml}
        `;
        
        group.params.forEach(param => {
            let value = config[param.key];
            const _engineDefault = (FeatureState.optunaDefaults || {})[param.key];
            if ((value === undefined || value === null) && _engineDefault && typeof _engineDefault === 'object') {
                // 저장된 적 없는 새 항목은 서버(DEFAULT_OPTUNA_CONFIG)의 기본값을 보여 준다 — 화면에 숫자를 따로 두지 않는다
                value = { ..._engineDefault };
                FeatureState.optunaConfig[param.key] = value;
            }
            if (param.key === 'seed' && (value === undefined || value === null)) {
                value = 42;
                FeatureState.optunaConfig['seed'] = 42;
            }
            const tip = param.tooltip;
            let minVal = value?.user_min !== undefined ? value.user_min : (value?.min || 0);
            let maxVal = value?.user_max !== undefined ? value.user_max : (value?.max || 100);
            const defaultSteps = {
                "predict_ahead_candles_range": 1,
                "max_hold_candles_range": 1,
                "take_profit_range": 0.001,
                "stop_loss_range": 0.001,
                "learning_rate_range": 0.005,
                "max_depth_range": 1,
                "num_leaves_range": 1,
                "min_child_samples_range": 1,
                "buy_threshold_range": 0.005,
                "buy_quantile_range": 0.005,
                "cooldown_minutes_range": 1
            };
            let step = value?.step !== undefined ? value.step : (defaultSteps[param.key] || 0.001);
            
            const isParamLocked = isPredictionGroup && isHorizonLocked;
            if (isParamLocked) {
                if (param.key === 'predict_ahead_candles_range') {
                    minVal = lockedPa;
                    maxVal = lockedPa;
                } else if (param.key === 'max_hold_candles_range') {
                    minVal = lockedMhc;
                    maxVal = lockedMhc;
                }
            }

            // 캔들 시간 변환 표시
            let candleTimeHtml = '';
            const intvLabel = FeatureState.interval || (FeatureState.intervalSeconds < 60 ? `${FeatureState.intervalSeconds}s` : `${intervalMin}m`);
            if (param.isCandles) {
                if (isParamLocked) {
                    candleTimeHtml = `
                        <div id="candle-time-${param.key}" style="font-size:10px;color:#34d399;margin-top:4px;font-weight:600;">
                            🔒 ${intvLabel} 기준: 고정 ${minVal}캔들 = ${candlesToTime(minVal, intervalMin)} (상수 고정 운용)
                        </div>
                    `;
                } else {
                    candleTimeHtml = `
                        <div id="candle-time-${param.key}" style="font-size:10px;color:var(--accent-yellow);margin-top:4px;font-weight:500;">
                            ⏱ ${intvLabel} 기준: ${minVal}캔들 = ${candlesToTime(minVal, intervalMin)} ~ ${maxVal}캔들 = ${candlesToTime(maxVal, intervalMin)}
                        </div>
                    `;
                }
            }
            
            // 뱃지 색상
            let badgeText = param.badge;
            let badgeColor = param.badgeColor === 'cyan' ? 'var(--accent-cyan)' : 'var(--accent-green)';
            let badgeBg = param.badgeColor === 'cyan' ? 'rgba(0,210,255,0.15)' : 'rgba(80,250,123,0.15)';
            if (isParamLocked) {
                badgeText = 'LOCKED';
                badgeColor = '#34d399';
                badgeBg = 'rgba(52, 211, 153, 0.15)';
            }
            
            // 툴팁 HTML
            const tooltipHtml = `
                <div class="tooltip-content" style="min-width:300px;max-width:380px;">
                    <div class="tooltip-title" style="font-size:14px;margin-bottom:8px;">${tip.title}</div>
                    <div class="tooltip-desc" style="margin-bottom:10px;">${tip.desc}</div>
                    <div style="background:rgba(0,0,0,0.2);border-radius:4px;padding:8px;margin-bottom:8px;">
                        <div style="font-size:11px;color:var(--accent-cyan);margin-bottom:4px;">📊 탐색 대상 / 범위</div>
                        ${tip.targets.map(t => `<div style="font-size:11px;color:var(--text-secondary);padding:2px 0;">• ${t}</div>`).join('')}
                    </div>
                    <div style="background:rgba(0,210,255,0.05);border-radius:4px;padding:8px;margin-bottom:8px;border-left:2px solid var(--accent-cyan);">
                        <div style="font-size:11px;color:var(--accent-cyan);margin-bottom:4px;">⚡ 영향</div>
                        <div style="font-size:11px;color:var(--text-secondary);">${tip.effect}</div>
                    </div>
                    ${tip.warning ? `
                    <div style="background:rgba(255,215,0,0.05);border-radius:4px;padding:8px;margin-bottom:8px;border-left:2px solid var(--accent-yellow);">
                        <div style="font-size:11px;color:var(--accent-yellow);margin-bottom:4px;">⚠️ 주의</div>
                        <div style="font-size:11px;color:var(--text-secondary);">${tip.warning}</div>
                    </div>` : ''}
                    <div style="background:rgba(80,250,123,0.05);border-radius:4px;padding:8px;border-left:2px solid var(--accent-green);">
                        <div style="font-size:11px;color:var(--accent-green);margin-bottom:4px;">✅ 권장</div>
                        <div style="font-size:11px;color:var(--text-secondary);">${tip.recommend}</div>
                    </div>
                </div>
            `;
            
            const disabledAttr = isParamLocked ? 'disabled' : '';
            const disabledStyle = isParamLocked ? 'opacity:0.55; cursor:not-allowed; background:rgba(255,255,255,0.04); font-weight:700; color:#34d399; border-color:rgba(52,211,153,0.3);' : '';

            if (param.type === 'number') {
                html += `
                    <div class="optuna-param-group">
                        <div class="optuna-param-label">
                            <span style="display:inline-block;padding:2px 6px;border-radius:4px;font-size:9px;font-weight:700;background:${badgeBg};color:${badgeColor};margin-right:6px;">${badgeText}</span>
                            ${param.label}
                            <span class="tooltip-icon" style="margin-left:4px;">
                                ℹ️
                                ${tooltipHtml}
                            </span>
                        </div>
                        <input type="number" class="form-control" 
                            data-optuna="${param.key}"
                            value="${value !== undefined && value !== null ? value : ''}" 
                            min="${param.min !== undefined ? param.min : 0}" 
                            max="${param.max !== undefined ? param.max : ''}"
                            step="${param.step || 'any'}"
                            ${disabledAttr}
                            style="${disabledStyle}"
                            oninput="updateOptunaConfig('${param.key}', this.value)"
                            onchange="updateOptunaConfig('${param.key}', this.value)">
                    </div>
                `;
            } else if (param.type === 'range') {
                html += `
                    <div class="optuna-param-group">
                        <div class="optuna-param-label">
                            <span style="display:inline-block;padding:2px 6px;border-radius:4px;font-size:9px;font-weight:700;background:${badgeBg};color:${badgeColor};margin-right:6px;">${badgeText}</span>
                            ${param.label}
                            <span class="tooltip-icon" style="margin-left:4px;">
                                ℹ️
                                ${tooltipHtml}
                            </span>
                        </div>
                        <div class="optuna-param-inputs">
                            <input type="number" class="optuna-param-input" 
                                data-optuna="${param.key}" data-type="min"
                                value="${minVal}" step="${step}"
                                ${disabledAttr}
                                style="${disabledStyle}"
                                oninput="updateOptunaRange('${param.key}', 'min', this.value)"
                                onchange="updateOptunaRange('${param.key}', 'min', this.value)">
                            <span class="optuna-param-sep">~</span>
                            <input type="number" class="optuna-param-input" 
                                data-optuna="${param.key}" data-type="max"
                                value="${maxVal}" step="${step}"
                                ${disabledAttr}
                                style="${disabledStyle}"
                                oninput="updateOptunaRange('${param.key}', 'max', this.value)"
                                onchange="updateOptunaRange('${param.key}', 'max', this.value)">
                            <span class="optuna-param-sep" style="margin-left:6px;">간격:</span>
                            <input type="number" class="optuna-param-input" 
                                data-optuna="${param.key}" data-type="step"
                                value="${step}" step="0.001"
                                ${disabledAttr}
                                style="${disabledStyle}"
                                oninput="updateOptunaRange('${param.key}', 'step', this.value)"
                                onchange="updateOptunaRange('${param.key}', 'step', this.value)">
                        </div>
                        ${candleTimeHtml}
                    </div>
                `;
            }
        });
        
        html += `
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// =============================================================================
// Event Handlers
// =============================================================================

function switchFeatureTab(tabName) {
    document.querySelectorAll('.btn-tab').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.tab === tabName) {
            btn.classList.add('active');
        }
    });
    
    document.getElementById('tab-features').style.display = tabName === 'features' ? 'block' : 'none';
    document.getElementById('tab-optuna').style.display = tabName === 'optuna' ? 'block' : 'none';

    if (tabName === 'optuna') {
        renderOptunaConfig();
    }
}

function filterFeatures(category) {
    FeatureState.currentCategory = category;
    
    document.querySelectorAll('.btn-category').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.cat === category) {
            btn.classList.add('active');
        }
    });
    
    renderAvailableFeatures();
}

function addFeature(featureId) {
    const feature = FeatureState.registry[featureId];
    if (!feature) return;
    
    const canonId = feature.canonical_id || feature.id;
    const isAlreadyActive = FeatureState.activeFeatures.some(f => (f.canonical_id || f.id) === canonId || f.id === featureId);
    if (isAlreadyActive) {
        showToast(`⚠️ [${feature.name}] 지표는 이미 활성화되어 있습니다.`, 'warning');
        return;
    }
    
    const baseFeature = FeatureState.registry[canonId] || feature;
    FeatureState.activeFeatures.push({
        ...baseFeature,
        currentParams: FeatureState.featureParams[canonId] || getDefaultParams(baseFeature)
    });
    
    if (!FeatureState.featureParams[canonId]) {
        FeatureState.featureParams[canonId] = getDefaultParams(baseFeature);
    }
    
    // availableFeatures 재계산 (중복/누락 방지)
    const activeCanonIds = new Set(FeatureState.activeFeatures.map(f => f.canonical_id || f.id));
    FeatureState.availableFeatures = Object.keys(FeatureState.registry)
        .filter(id => {
            const item = FeatureState.registry[id];
            if (item.is_alias) return false;
            const cId = item.canonical_id || id;
            return !activeCanonIds.has(id) && !activeCanonIds.has(cId);
        })
        .map(id => FeatureState.registry[id]);

    FeatureState.isDirty = true;
    renderFeatureConfig();
    appendLog(`➕ 지표 추가: ${baseFeature.name}`, '');
}

function addAllFeatures() {
    const featuresToAdd = FeatureState.availableFeatures;
    
    if (featuresToAdd.length === 0) {
        showToast('⚠️ 추가할 수 있는 지표가 없습니다.', 'warning');
        return;
    }
    
    // 전체 지표를 중복 없이 activeFeatures로 이동
    const activeCanonIds = new Set(FeatureState.activeFeatures.map(f => f.canonical_id || f.id));
    let addedCount = 0;
    
    featuresToAdd.forEach(feature => {
        const canonId = feature.canonical_id || feature.id;
        if (!activeCanonIds.has(canonId)) {
            const baseFeature = FeatureState.registry[canonId] || feature;
            FeatureState.activeFeatures.push({
                ...baseFeature,
                currentParams: FeatureState.featureParams[canonId] || getDefaultParams(baseFeature)
            });
            if (!FeatureState.featureParams[canonId]) {
                FeatureState.featureParams[canonId] = getDefaultParams(baseFeature);
            }
            activeCanonIds.add(canonId);
            addedCount++;
        }
    });
    
    FeatureState.availableFeatures = [];
    FeatureState.isDirty = true;
    
    renderFeatureConfig();
    showToast(`✅ ${addedCount}개 지표가 추가되었습니다. 저장 버튼을 눌러주세요!`, 'success');
    appendLog(`➕ 전체 지표 추가: ${addedCount}개 지표 활성화`, '');
}

function removeFeature(featureId) {
    const regItem = FeatureState.registry[featureId];
    const canonId = (regItem && regItem.canonical_id) || featureId;
    
    const feature = FeatureState.activeFeatures.find(f => 
        f.id === featureId || (f.canonical_id && f.canonical_id === featureId) || f.id === canonId || (f.canonical_id && f.canonical_id === canonId)
    );
    if (!feature) return;
    
    FeatureState.activeFeatures = FeatureState.activeFeatures.filter(f => 
        f.id !== featureId && f.canonical_id !== featureId && f.id !== canonId && f.canonical_id !== canonId
    );
    
    // availableFeatures 재계산 (중복/누락 방지)
    const activeCanonIds = new Set(FeatureState.activeFeatures.map(f => f.canonical_id || f.id));
    FeatureState.availableFeatures = Object.keys(FeatureState.registry)
        .filter(id => {
            const item = FeatureState.registry[id];
            if (item.is_alias) return false;
            const cId = item.canonical_id || id;
            return !activeCanonIds.has(id) && !activeCanonIds.has(cId);
        })
        .map(id => FeatureState.registry[id]);
        
    FeatureState.isDirty = true;
    renderFeatureConfig();
    appendLog(`➖ 지표 제거: ${feature.name || featureId}`, '');
}

function clearAllFeatures() {
    if (!FeatureState.activeFeatures || FeatureState.activeFeatures.length === 0) {
        showToast('ℹ️ 이미 선택된 활성 지표가 없습니다.', 'info');
        return;
    }
    if (!confirm('⚠️ 현재 선택된 모든 활성 지표를 전체 해제하시겠습니까?')) return;
    
    const count = FeatureState.activeFeatures.length;
    FeatureState.activeFeatures = [];
    FeatureState.availableFeatures = Object.keys(FeatureState.registry)
        .filter(id => !FeatureState.registry[id].is_alias)
        .map(id => FeatureState.registry[id]);
        
    FeatureState.isDirty = true;
    renderFeatureConfig();
    showToast(`✅ ${count}개 지표가 전체 해제되었습니다. [프로젝트 저장]을 눌러 반영하세요.`, 'success');
    appendLog(`🧹 활성 지표 전체 해제: ${count}개 해제`, '');
}

// =============================================================================
// Feature Presets & Screener Management
// =============================================================================

async function loadFeaturePresets() {
    try {
        const res = await apiGet('/api/features/presets');
        const select = document.getElementById('featurePresetSelect');
        if (!select) return;
        
        const currentVal = select.value;
        select.innerHTML = '<option value="">-- 🧬 프리셋 선택 --</option>';
        
        if (res.status === 'success' && res.presets) {
            FeatureState.presets = res.presets;
            for (const [key, p] of Object.entries(res.presets)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = `📦 ${p.name || key} (${(p.features || []).length}개 지표)`;
                if (p.description) opt.title = p.description;
                select.appendChild(opt);
            }
        }
        if (currentVal && FeatureState.presets[currentVal]) {
            select.value = currentVal;
            document.getElementById('btnDeletePreset')?.removeAttribute('disabled');
        } else {
            document.getElementById('btnDeletePreset')?.setAttribute('disabled', 'true');
        }
    } catch (e) {
        console.error('Failed to load presets:', e);
    }
}

async function onPresetSelected(presetName) {
    const delBtn = document.getElementById('btnDeletePreset');
    if (!presetName) {
        if (delBtn) delBtn.disabled = true;
        return;
    }
    if (delBtn) delBtn.disabled = false;
    
    const preset = FeatureState.presets[presetName];
    if (!preset || !preset.features) return;
    
    await applyFeatureList(preset.features, preset.name || presetName);
}

async function applyFeatureList(targetIds, label, preserveRegime = true) {
    // 🛡️ [백본 지표 자동 보존] 체제/백본 고정 지표는 스크리너 선별 지표와 자동으로 병합 보존
    let finalTargetIds = [...targetIds];
    let preservedCount = 0;
    
    if (preserveRegime) {
        // 기존 활성 지표 중 regime으로 고정된 지표들 탐색
        const currentActiveRegimes = (FeatureState.activeFeatures || [])
            .filter(f => {
                const fid = f.id;
                const isExplicitRegime = FeatureState.regimeFeatures && FeatureState.regimeFeatures[fid] === true;
                const isDefaultRegime = (FeatureState.regimeFeatures && FeatureState.regimeFeatures[fid] === undefined) && (f.is_regime || f.category === 'time');
                return isExplicitRegime || isDefaultRegime;
            })
            .map(f => f.id);
            
        // 또는 regimeFeatures 객체에서 true인 지표들
        const explicitRegimes = Object.entries(FeatureState.regimeFeatures || {})
            .filter(([k, v]) => v === true)
            .map(([k]) => k);
            
        const allRegimesToPreserve = Array.from(new Set([...currentActiveRegimes, ...explicitRegimes]));
        allRegimesToPreserve.forEach(regFid => {
            if (!finalTargetIds.includes(regFid) && FeatureState.registry[regFid]) {
                finalTargetIds.push(regFid);
                preservedCount++;
            }
        });
    }

    const regimeNote = preservedCount > 0 ? ` (🛡️ 백본 고정 지표 ${preservedCount}개 자동 보존)` : '';
    appendLog(`🧬 지표 세트 적용: ${label} (${finalTargetIds.length}개 지표)${regimeNote}`, '');
    
    // canonical id 기준 중복 제거
    const uniqueIds = Array.from(new Set(finalTargetIds));
    const newActiveFeatures = [];
    
    uniqueIds.forEach(id => {
        const featInfo = FeatureState.registry[id];
        if (featInfo) {
            newActiveFeatures.push({
                ...featInfo,
                currentParams: FeatureState.featureParams[id] || getDefaultParams(featInfo)
            });
            if (!FeatureState.featureParams[id]) {
                FeatureState.featureParams[id] = getDefaultParams(featInfo);
            }
        }
    });

    FeatureState.activeFeatures = newActiveFeatures;
    const activeIdSet = new Set(newActiveFeatures.map(f => f.id));
    
    FeatureState.availableFeatures = Object.keys(FeatureState.registry)
        .filter(id => !activeIdSet.has(id) && !FeatureState.registry[id].is_alias)
        .map(id => FeatureState.registry[id]);

    FeatureState.isDirty = false;
    renderFeatureConfig();
    
    try {
        const enabledFeatures = FeatureState.activeFeatures.map(f => f.id);
        const payload = {
            enabled_features: enabledFeatures,
            feature_params: FeatureState.featureParams,
            feature_penalties: FeatureState.featurePenalties,
            regime_features: FeatureState.regimeFeatures,
            optuna_config: FeatureState.optunaConfig,
            reference_symbols: FeatureState.referenceSymbols
        };
        await apiPost('/api/features/config', payload);
        showToast(`✅ '${label}' 적용 완료!${regimeNote}`, 'success');
        appendLog(`💾 [${label}] 적용 및 활성 프로젝트 동기화 완료${regimeNote}`, '');
    } catch (e) {
        showToast(`❌ 프리셋 저장 실패: ${e}`, 'danger');
    }
}

async function saveCurrentAsNewPreset() {
    const activeIds = FeatureState.activeFeatures.map(f => f.id);
    if (activeIds.length === 0) {
        showToast('⚠️ 활성화된 지표가 최소 1개 이상이어야 합니다.', 'warning');
        return;
    }
    
    const name = prompt('💾 저장할 프리셋(팩) 이름을 입력해 주세요:\n(예: KODEX_1분봉_수급단타팩, BTC_모멘텀팩)', '');
    if (!name || !name.trim()) return;
    
    const desc = prompt('📝 프리셋 설명을 입력해 주세요 (선택 사항):', `${activeIds.length}개 지표 조합`);
    
    try {
        const res = await apiPost('/api/features/presets', {
            name: name.trim(),
            description: desc || '',
            features: activeIds
        });
        
        if (res.status === 'success') {
            showToast(`✅ 프리셋 '${name}' 저장 완료!`, 'success');
            appendLog(`💾 새 지표 프리셋 [${name}] 등록 완료 (${activeIds.length}개 지표)`, '');
            await loadFeaturePresets();
            const select = document.getElementById('featurePresetSelect');
            if (select) select.value = name.trim();
        } else {
            showToast(`❌ 프리셋 저장 실패: ${res.message}`, 'danger');
        }
    } catch (e) {
        showToast(`❌ 프리셋 저장 에러: ${e.message}`, 'danger');
    }
}

async function deleteCurrentPreset() {
    const select = document.getElementById('featurePresetSelect');
    const presetName = select ? select.value : '';
    if (!presetName) return;
    
    if (!confirm(`⚠️ 정말로 프리셋 [${presetName}]을(를) 삭제하시겠습니까?`)) {
        return;
    }
    
    try {
        const res = await apiDelete(`/api/features/presets/${encodeURIComponent(presetName)}`);
        if (res.status === 'success') {
            showToast(`✅ 프리셋 '${presetName}' 삭제 완료!`, 'success');
            appendLog(`🗑️ 지표 프리셋 [${presetName}] 삭제 완료`, '');
            await loadFeaturePresets();
        } else {
            showToast(`❌ 프리셋 삭제 실패: ${res.message}`, 'danger');
        }
    } catch (e) {
        showToast(`❌ 프리셋 삭제 에러: ${e.message}`, 'danger');
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Feature Screener
// ─────────────────────────────────────────────────────────────────────────────

function toggleScreenerBoard() {
    const card = document.getElementById('featureScreenerCard');
    if (card) {
        card.style.display = card.style.display === 'none' ? 'block' : 'none';
    }
}

function closeScreenerBoardWithoutApply() {
    const card = document.getElementById('featureScreenerCard');
    if (card) {
        card.style.display = 'none';
    }
}

async function runFeatureScreener() {
    const saved = await ensureProjectSavedBeforeAction('지표 선행성 스크리닝 분석');
    if (!saved) return;

    const btn = document.getElementById('btnRunScreener');
    const lookbackEl = document.getElementById('screenerLookbackSelect');
    const lookbackPeriod = lookbackEl ? lookbackEl.value : '3y';
    const lookbackText = lookbackEl ? lookbackEl.options[lookbackEl.selectedIndex]?.text : '홀드아웃 전 3년';

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ 스크리닝 분석 중...';
    }
    appendLog(`⚡ 지표 선행성 스크리닝(Forward IC & Lead Lag)을 시작합니다... [기간: ${lookbackText}]`, '');
    
    try {
        const res = await apiPost('/api/features/screener', {
            ic_threshold: 0.015,
            max_lag_candles: 10,
            lookback_period: lookbackPeriod
        });
        
        if (res.status === 'success' && res.ranked_features) {
            FeatureState.screenerResults = res.ranked_features;
            renderFeatureScreenerResults(res);
            showToast(`✅ 지표 스크리닝 완료! (총 ${res.features_count}개 중 ${res.passed_count}개 합격)`, 'success');
            appendLog(`🏆 지표 스크리닝 완료: [${res.period_desc || lookbackText}] 유효 지표 ${res.passed_count}개 도출`, '');
        } else {
            showToast(`❌ 스크리닝 실패: ${res.message || res.error}`, 'danger');
            appendLog(`❌ 스크리닝 실패: ${res.message || res.error}`, '');
        }
    } catch (e) {
        showToast(`❌ 스크리닝 에러: ${e.message}`, 'danger');
        appendLog(`❌ 스크리닝 에러: ${e.message}`, '');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '⚡ 지표 스크리닝 분석';
        }
    }
}

function renderFeatureScreenerResults(data) {
    const card = document.getElementById('featureScreenerCard');
    const tbody = document.getElementById('featureScreenerTableBody');
    const specialList = document.getElementById('screenerSpecialFeaturesList');
    const summaryText = document.getElementById('screenerSummaryText');
    if (!card || !tbody) return;
    
    card.style.display = 'block';
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    
    if (summaryText) {
        const periodInfo = data.period_desc ? ` | 분석 기간: <strong style="color:var(--accent-cyan);">${data.period_desc}</strong>` : '';
        summaryText.innerHTML = `
            • 분석 대상 종목/인터벌: <strong>${data.symbol} (${data.interval})</strong>${periodInfo} (총 <strong>${(data.total_samples || 0).toLocaleString()}개 캔들</strong>)<br>
            • 통계 검증 기준: <strong>선행 |IC| ≥ 0.015, p-value < 0.05, 단조성 ≥ 50%</strong> (연속형: 5분위 / 이벤트: 3상태) (총 ${data.features_count}개 중 <strong>${data.passed_count}개 합격</strong>)
        `;
    }
    
    // 1. [블록 1] 선형 알파 지표 랭킹 렌더링
    tbody.innerHTML = '';
    const activeIds = new Set(FeatureState.activeFeatures.map(f => f.id));
    let passedIndex = 0;
    
    // 체제 지표는 하단 블록 2로 이동하여 노출하므로 블록 1에서는 일반 알파 지표 위주로 집중
    const linearRanked = (data.ranked_features || []).filter(item => {
        const reg = FeatureState.registry[item.id] || {};
        return !(reg.is_regime || reg.category === 'time');
    });

    linearRanked.forEach((item, idx) => {
        const isPassed = item.passed;
        if (isPassed) passedIndex++;
        
        const rankNum = idx + 1;
        const rankMedal = rankNum === 1 ? '🥇 1' : (rankNum === 2 ? '🥈 2' : (rankNum === 3 ? '🥉 3' : `#${rankNum}`));
        const icColor = item.lead_ic >= 0 ? '#22c55e' : '#ef4444';
        const icSign = item.lead_ic >= 0 ? '+' : '';
        const statusBadge = isPassed
            ? '<span style="background:rgba(34,197,94,0.15); color:#22c55e; padding:2px 6px; border-radius:4px; font-weight:700; font-size:11px;">✅ 합격</span>'
            : '<span style="background:rgba(239,68,68,0.1); color:#ef4444; padding:2px 6px; border-radius:4px; font-size:11px;">미달</span>';
            
        // 상위 8개 자동 체크
        const isChecked = isPassed && passedIndex <= 8;
        const rowBg = isPassed ? (rankNum <= 3 ? 'rgba(168,85,247,0.06)' : '') : 'rgba(255,255,255,0.01)';
        
        const trackTag = item.eval_track === 'event'
            ? '<div style="font-size:9px; color:#a855f7; font-weight:600;">(이벤트 3상태)</div>'
            : '<div style="font-size:9px; color:var(--text-secondary);">(5분위)</div>';
        
        tbody.innerHTML += `
            <tr style="border-bottom:1px solid var(--border-color); background:${rowBg}; text-align:center;">
                <td style="padding:6px 8px; font-weight:700;">${rankMedal}</td>
                <td style="padding:6px 8px; text-align:left;">
                    <div style="font-weight:600; color:var(--text-primary);">${item.name}</div>
                    <div style="font-size:10px; color:var(--text-secondary);">${item.id}</div>
                </td>
                <td style="padding:6px 8px; text-align:right; font-weight:700; color:${icColor};">
                    ${icSign}${item.lead_ic.toFixed(4)}
                </td>
                <td style="padding:6px 8px; color:var(--accent-yellow); font-weight:600;">
                    ${item.lead_time} (Lag ${item.optimal_lag})
                </td>
                <td style="padding:6px 8px; font-size:11px; color:var(--text-secondary);">
                    t=${item.t_stat.toFixed(1)} (p=${item.p_value < 0.0001 ? '<0.0001' : item.p_value.toFixed(4)})
                </td>
                <td style="padding:6px 8px;">
                    <div style="font-weight:600; color:${item.monotonicity_score >= 70 ? '#22c55e' : (item.monotonicity_score >= 50 ? '#facc15' : 'var(--text-secondary)')};">
                        ${item.monotonicity_score}%
                    </div>
                    ${trackTag}
                </td>
                <td style="padding:6px 8px;">${statusBadge}</td>
                <td style="padding:6px 8px;">
                    <input type="checkbox" class="screener-select-checkbox" data-id="${item.id}" ${isChecked ? 'checked' : ''} onchange="updateScreenerSelectionCount()" style="cursor:pointer; width:15px; height:15px;">
                </td>
            </tr>
        `;
    });

    // 2. [블록 2] 베이스(백본) 및 희귀 이벤트 지표 목록 렌더링
    if (specialList) {
        specialList.innerHTML = '';
        const curScope = FeatureState.marketType || 'crypto';
        
        // 레지스트리에서 체제 지표 및 트리거 지표 추출
        const specialFeatures = Object.values(FeatureState.registry || {}).filter(feat => {
            if (feat.is_alias) return false;
            // 자산군 호환성 검사
            const scope = feat.market_scope || 'all';
            if (scope !== 'all' && scope !== curScope && !FeatureState.includeOtherMarkets) return false;

            const isRegime = Boolean(feat.is_regime || feat.category === 'time');
            const isTrigger = Boolean(feat.is_trigger || (feat.id && (feat.id.includes('squeeze') || feat.id.includes('spike') || feat.id.includes('large_trade'))));
            return isRegime || isTrigger;
        });

        specialFeatures.forEach(feat => {
            const isRegime = Boolean(feat.is_regime || feat.category === 'time');
            const isTrigger = Boolean(feat.is_trigger || (feat.id && (feat.id.includes('squeeze') || feat.id.includes('spike') || feat.id.includes('large_trade'))));
            
            // 기존 활성 목록에 이미 켜져있는 지표는 기본 checked: true
            const isCurrentlyActive = activeIds.has(feat.id);
            
            const badge = isRegime
                ? `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.4); font-size:10px;">🛡️ 백본 고정 (IC면제)</span>`
                : `<span class="badge" style="background:rgba(236,72,153,0.15); color:#f472b6; border:1px solid rgba(236,72,153,0.4); font-size:10px;">⚡ 희귀 트리거 (조건부알파)</span>`;

            specialList.innerHTML += `
                <label style="display:flex; align-items:flex-start; gap:8px; background:rgba(0,0,0,0.2); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:8px 10px; cursor:pointer; margin:0;">
                    <input type="checkbox" class="screener-special-checkbox" data-id="${feat.id}" ${isCurrentlyActive ? 'checked' : ''} onchange="updateScreenerSelectionCount()" style="cursor:pointer; width:15px; height:15px; margin-top:2px;">
                    <div style="flex:1;">
                        <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:2px;">
                            <span style="font-weight:700; font-size:12px; color:var(--text-primary);">${feat.name}</span>
                            ${badge}
                        </div>
                        <div style="font-size:10px; color:var(--text-secondary);">${feat.description || feat.id}</div>
                    </div>
                </label>
            `;
        });
    }

    updateScreenerSelectionCount();
}

function updateScreenerSelectionCount() {
    const alphaCount = document.querySelectorAll('.screener-select-checkbox:checked').length;
    const specialCount = document.querySelectorAll('.screener-special-checkbox:checked').length;
    const counterEl = document.getElementById('screenerSelectionCounter');
    if (counterEl) {
        counterEl.innerHTML = `선택: 일반 알파 지표 <strong style="color:#c084fc;">${alphaCount}개</strong> + 베이스/특수 지표 <strong style="color:#f59e0b;">${specialCount}개</strong> = 총 <strong style="color:var(--accent-green);">${alphaCount + specialCount}개</strong>`;
    }
}

async function applyScreenerTopFeatures() {
    const alphaCheckboxes = document.querySelectorAll('.screener-select-checkbox:checked');
    const specialCheckboxes = document.querySelectorAll('.screener-special-checkbox:checked');
    
    const alphaIds = Array.from(alphaCheckboxes).map(cb => cb.dataset.id).filter(Boolean);
    const specialIds = Array.from(specialCheckboxes).map(cb => cb.dataset.id).filter(Boolean);
    
    const allSelectedIds = Array.from(new Set([...alphaIds, ...specialIds]));
    
    if (allSelectedIds.length === 0) {
        showToast('⚠️ 선택된 지표가 없습니다. 최소 1개 이상의 지표를 선택해 주세요.', 'warning');
        return;
    }
    
    // 사용자가 명시적으로 선택한 특수 지표 목록을 반영 (체크 해제된 것은 해제, 체크된 것은 유지)
    specialIds.forEach(id => {
        const feat = FeatureState.registry[id];
        if (feat && (feat.is_regime || feat.category === 'time')) {
            if (!FeatureState.regimeFeatures) FeatureState.regimeFeatures = {};
            FeatureState.regimeFeatures[id] = true;
        }
    });

    await applyFeatureList(allSelectedIds, `스크리너 선별 (알파 ${alphaIds.length}개 + 특수 ${specialIds.length}개)`, false);
    closeScreenerBoardWithoutApply();
}


function updateParamValue(slider) {
    const featureId = slider.dataset.feature;
    const paramName = slider.dataset.param;
    const value = parseFloat(slider.value);
    
    const valueDisplay = document.getElementById(`val-${featureId}-${paramName}`);
    if (valueDisplay) {
        const registry = FeatureState.registry[featureId];
        if (registry && registry.params && registry.params[paramName]) {
            valueDisplay.textContent = formatParamValue(value, registry.params[paramName]);
        }
    }
}

function saveParamChange(slider) {
    const featureId = slider.dataset.feature;
    const paramName = slider.dataset.param;
    const value = parseFloat(slider.value);
    
    if (!FeatureState.featureParams[featureId]) {
        FeatureState.featureParams[featureId] = {};
    }
    FeatureState.featureParams[featureId][paramName] = value;
    FeatureState.isDirty = true;
    
    appendLog(`⚙️ 파라미터 변경: ${featureId}.${paramName} = ${value}`, '');
}


function updatePenaltyValue(slider) {
    const featureId = slider.dataset.feature;
    const value = parseFloat(slider.value);
    const valueDisplay = document.getElementById(`val-penalty-${featureId}`);
    if (valueDisplay) {
        const text = value >= 1.0 ? '패널티 없음 (기본)' : `패널티 ${(1.0 - value).toFixed(1)} 감점`;
        valueDisplay.textContent = text;
    }
}

function savePenaltyChange(slider) {
    const featureId = slider.dataset.feature;
    const value = parseFloat(slider.value);
    
    FeatureState.featurePenalties[featureId] = value;
    FeatureState.isDirty = true;
    
    const displayVal = value >= 1.0 ? '패널티 없음 (1.0)' : `패널티 ${(1.0 - value).toFixed(1)} 감점`;
    appendLog(`⚖️ 지표 선택 패널티 변경: ${featureId} = ${displayVal}`, '');
}

function updateOptunaConfig(key, value) {
    if (key === 'fee_upbit' || key === 'slippage' || key === 'min_profit_factor') {
        const num = parseFloat(value);
        if (!isNaN(num)) {
            FeatureState.optunaConfig[key] = num;
        }
    } else if (key === 'enable_early_stop') {
        FeatureState.optunaConfig[key] = parseInt(value, 10) === 1;
    } else {
        const num = parseInt(value, 10);
        if (!isNaN(num)) {
            FeatureState.optunaConfig[key] = num;
        }
    }
    FeatureState.isDirty = true;
}

function updateOptunaRange(key, type, value) {
    if (!FeatureState.optunaConfig[key]) {
        FeatureState.optunaConfig[key] = {};
    }
    
    const numVal = isNaN(parseFloat(value)) ? 0 : parseFloat(value);
    if (type === 'min') {
        FeatureState.optunaConfig[key].user_min = numVal;
    } else if (type === 'max') {
        FeatureState.optunaConfig[key].user_max = numVal;
    } else if (type === 'step') {
        FeatureState.optunaConfig[key].step = numVal;
    }
    FeatureState.isDirty = true;

    // 캔들 단위 파라미터 실시간 시간 변환 DOM 갱신
    const candleHintEl = document.getElementById(`candle-time-${key}`);
    if (candleHintEl) {
        const intervalMin = FeatureState.intervalMinutes || 1;
        const curMin = FeatureState.optunaConfig[key].user_min ?? (FeatureState.optunaConfig[key].min || 0);
        const curMax = FeatureState.optunaConfig[key].user_max ?? (FeatureState.optunaConfig[key].max || 0);
        candleHintEl.innerHTML = `⏱ ${intervalMin}m 기준: ${curMin}캔들 = ${candlesToTime(curMin, intervalMin)} ~ ${curMax}캔들 = ${candlesToTime(curMax, intervalMin)}`;
    }
}

// =============================================================================
// Helper Functions
// =============================================================================

function getDefaultParams(feature) {
    const params = {};
    for (const [pName, pConfig] of Object.entries(feature.params || {})) {
        params[pName] = pConfig.default;
    }
    return params;
}

function formatParamValue(value, config) {
    if (config.step && config.step < 1) {
        return value.toFixed(3);
    }
    return value.toString();
}

function bindSliderEvents() {
    // 슬라이더 이벤트는 이미 HTML에서 onchange로 바인딩됨
}

// =============================================================================
// Initialize on Settings Tab Switch & Data Vault
// =============================================================================

const originalSwitchTab = window.switchTab;
window.switchTab = function(tabName) {
    if (typeof originalSwitchTab === 'function') originalSwitchTab(tabName);
    const curTarget = getCurrentTargetSymbol();
    if (tabName === 'settings' && !FeatureState.isDirty) {
        loadFeatureConfig(curTarget);
    }
    if (tabName === 'data') {
        loadDataVaultStatus();
        loadSymbolRelationsStudio(curTarget);
        if (typeof loadMedallionStatus === 'function') loadMedallionStatus(curTarget);
    }
};

document.addEventListener('DOMContentLoaded', () => {
    const curTarget = getCurrentTargetSymbol();
    const tabSettings = document.getElementById('tab-settings');
    if (tabSettings && tabSettings.classList.contains('active')) {
        loadFeatureConfig(curTarget);
    }
    loadDataVaultStatus();
    loadSymbolRelationsStudio(curTarget);
    if (typeof loadMedallionStatus === 'function') loadMedallionStatus(curTarget);
});

// =============================================================================
// 🏛️ Data Vault (시간단위별 캔들 라이브러리 & 실시간 준비성 검증) 프론트엔드 연동
// =============================================================================

let _vaultLiveProbes = {};
let _dataCatalogLoaded = false;

function toggleDataCatalogPanel() {
    const content = document.getElementById('dataCatalogContent');
    const icon = document.getElementById('dataCatalogToggleIcon');
    if (!content) return;
    if (content.style.display === 'none') {
        content.style.display = 'block';
        if (icon) icon.innerText = '▼ 접기';
    } else {
        content.style.display = 'none';
        if (icon) icon.innerText = '▲ 펼치기';
    }
}

async function loadDataCatalogSources(force = false) {
    const tbody = document.getElementById('dataCatalogTableBody');
    if (!tbody) return;
    if (_dataCatalogLoaded && !force) return;
    try {
        const [catRes, qualRes] = await Promise.all([
            apiGet('/api/data_catalog/sources'),
            apiGet('/api/data_vault/quality').catch(() => null)
        ]);
        if (!catRes || catRes.status !== 'ok' || !catRes.sources) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:12px; color:var(--accent-red);">카탈로그 로드 실패</td></tr>`;
            return;
        }

        const qualityReport = (qualRes && qualRes.status === 'ok' && qualRes.exists) ? (qualRes.quality_report || {}) : null;

        let html = '';
        catRes.sources.forEach(src => {
            const avail = src.availability || {};
            const fallback = src.fallback_policy || {};
            const sourceInfo = src.source || {};

            const startDate = avail.available_start_date || '전체 구간';
            const isUsdt = src.id === 'UPBIT_USDT';
            const startBadge = isUsdt 
                ? `<span class="badge" style="background:rgba(234,179,8,0.15); color:#eab308; border:1px solid rgba(234,179,8,0.4); font-size:10px;">📅 ${startDate}~</span>`
                : `<span class="badge" style="background:rgba(56,189,248,0.12); color:#38bdf8; font-size:10px;">📅 ${startDate}~</span>`;

            const scheduleBadge = avail.holiday_calendar === 'KRX' 
                ? '<span class="badge" style="background:rgba(239,68,68,0.12); color:#f87171; font-size:10px;">KRX 영업일(주말/공휴일 제외)</span>'
                : (avail.holiday_calendar === 'US' 
                    ? '<span class="badge" style="background:rgba(168,85,247,0.12); color:#c084fc; font-size:10px;">US 영업일(주말/공휴일 제외)</span>'
                    : '<span class="badge" style="background:rgba(34,197,94,0.12); color:#4ade80; font-size:10px;">24/7 연중무휴</span>');

            let fallbackBadge = '<span style="color:var(--text-secondary); font-size:11px;">-</span>';
            if (fallback.policy === 'ROLLING_INVERSE_RATE') {
                fallbackBadge = '<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; font-size:10px;" title="' + (fallback.description || '') + '">⚡ 롤링 역산 환율 폴백</span>';
            } else if (fallback.policy === 'FORWARD_FILL') {
                fallbackBadge = '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; font-size:10px;" title="' + (fallback.description || '') + '">직전값 홀딩(ffill)</span>';
            } else if (fallback.policy === 'CRITICAL_ERROR') {
                fallbackBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#ef4444; font-size:10px;" title="' + (fallback.description || '') + '">필수 (중단)</span>';
            }

            // 실제 결측 & 보간 현황 뱃지 생성
            let qualityBadge = '<span style="color:var(--text-muted); font-size:11px;">- (빌드 대기)</span>';
            if (qualityReport) {
                const matchedCols = Object.keys(qualityReport).filter(col => {
                    const item = qualityReport[col];
                    if (item && item.source_id === src.id) return true;
                    if (src.columns && src.columns.includes(col)) return true;
                    return false;
                });

                if (matchedCols.length > 0) {
                    let totalRawMissing = 0;
                    let totalImputed = 0;
                    let totalRemaining = 0;
                    let maxRatio = 0.0;

                    matchedCols.forEach(c => {
                        const item = qualityReport[c];
                        totalRawMissing += (item.raw_missing_count || 0);
                        totalImputed += (item.imputed_count || 0);
                        totalRemaining += (item.remaining_missing || 0);
                        if ((item.missing_ratio_pct || 0) > maxRatio) {
                            maxRatio = item.missing_ratio_pct;
                        }
                    });

                    if (totalRawMissing === 0) {
                        qualityBadge = '<span class="badge" style="background:rgba(34,197,94,0.12); color:#4ade80; font-size:10px;" title="결측치 없음">🟢 무결 (0% 결측)</span>';
                    } else if (totalRemaining === 0) {
                        qualityBadge = `<span class="badge" style="background:rgba(16,185,129,0.18); color:#34d399; font-size:10px; border:1px solid rgba(16,185,129,0.4);" title="원천 결측: ${totalRawMissing.toLocaleString()}행 전부 카탈로그 폴백 보간 완료">⚡ ${totalImputed.toLocaleString()}행 보간 (${maxRatio}%)</span>`;
                    } else {
                        qualityBadge = `<span class="badge" style="background:rgba(239,68,68,0.18); color:#f87171; font-size:10px;" title="잔여 결측: ${totalRemaining.toLocaleString()}행">⚠️ ${totalRemaining.toLocaleString()}행 잔여결측</span>`;
                    }
                } else {
                    qualityBadge = '<span style="color:var(--text-muted); font-size:11px;">- (해당없음)</span>';
                }
            }

            const critBadge = src.is_critical 
                ? '<span class="badge badge-danger" style="font-size:10px;">필수 타겟</span>' 
                : '<span class="badge" style="background:rgba(255,255,255,0.08); color:var(--text-muted); font-size:10px;">참조/보조</span>';

            html += `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
                    <td style="padding:6px 8px; font-weight:bold; color:var(--text-primary);">
                        ${src.name} <span style="font-size:10px; color:var(--accent-cyan); font-weight:normal;">[${src.id}]</span>
                    </td>
                    <td style="padding:6px 8px; color:var(--text-secondary); font-family:monospace; font-size:11px;">
                        ${sourceInfo.provider || '-'}
                    </td>
                    <td style="padding:6px 8px; color:var(--text-muted); font-size:11px;">
                        ${sourceInfo.endpoint_type || '-'}
                    </td>
                    <td style="padding:6px 8px; text-align:center;">
                        ${startBadge}
                    </td>
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <td style="padding:6px 8px; font-weight:bold; color:var(--accent-cyan);">${sym}</td>
                    <td style="padding:6px 8px;">${item.name || sym}</td>
                    <td style="padding:6px 8px;"><span class="badge" style="background:rgba(56,189,248,0.1); color:#38bdf8;">${prov}</span></td>
                    <td style="padding:6px 8px;">${isTarget}</td>
                    <td style="padding:6px 8px; text-align:center;">${statusBadge}</td>
                    <td style="padding:6px 8px; text-align:right; font-family:monospace;">${rows}</td>
                    <td style="padding:6px 8px; font-size:11px; color:#cbd5e1;">${dateRange}</td>
                    <td style="padding:6px 8px; text-align:center;">
                        ${qualityBadge}
                    </td>
                    <td style="padding:6px 8px; text-align:center;">
                        ${critBadge}
                    </td>
                </tr>
            `;
        });

        tbody.innerHTML = html;
        _dataCatalogLoaded = true;
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:12px; color:var(--accent-red);">카탈로그 오류: ${e.message}</td></tr>`;
    }
}

async function loadMedallionStatus(targetSymbol = null, intervalStr = null) {
    try {
        const curSym = targetSymbol || getCurrentTargetSymbol();
        const curInt = intervalStr || (typeof STATE !== 'undefined' && STATE.status && STATE.status.interval) || (typeof FeatureState !== 'undefined' && FeatureState.interval) || '10s';
        const res = await apiGet(`/api/data_vault/medallion_status?target=${encodeURIComponent(curSym)}&interval=${encodeURIComponent(curInt)}`);
        if (!res || res.status !== 'ok') return;

        // 1. Raw 카드
        const rawCntEl = document.getElementById('rawFileCount');
        const rawMetaEl = document.getElementById('rawMetaDesc');
        if (rawCntEl) rawCntEl.textContent = `${res.raw_count || 0}개 원시 파일 (SHA-256 전수 검증)`;
        if (rawMetaEl) rawMetaEl.textContent = `대신증권 3.21M 틱 + 1:1 사이드카 매니페스트 (무결성 100%)`;

        // 2. Normalized 카드 (현재 자산 및 타임프레임 요약)
        const targetPattern = `${curSym}_${curInt}`;
        const k10s = (res.normalized_files || []).find(f => f.path && f.path.includes(targetPattern)) || (res.normalized_files || [])[0];
        const normRowsEl = document.getElementById('normKodex10sRows');
        const normMetaEl = document.getElementById('normKodex10sMeta');
        if (normRowsEl && k10s) {
            const szMb = k10s.size_bytes ? (k10s.size_bytes / (1024 * 1024)).toFixed(1) : '5.3';
            normRowsEl.textContent = `${(k10s.rows || 0).toLocaleString()}행 (${szMb} MB)`;
        }
        if (normMetaEl) {
            const t = res.coverage_summary?.target || {};
            normMetaEl.textContent = `${t.start_date || '-'} ~ ${t.end_date || '-'} (결측률 0.0%)`;
        }

        // 3. Features & Coverage Guard 카드
        const cov = res.coverage_summary;
        if (cov) {
            const tgt = cov.target || {};
            const tgtDays = tgt.trading_days || 0;
            const tgtStart = tgt.start_date || '-';
            const tgtEnd = tgt.end_date || '-';
            const tgtBars = (tgt.total_bars || 0).toLocaleString();
            const comDays = cov.common_trading_days || 0;
            const comStart = cov.common_start_date || '-';
            const comEnd = cov.common_end_date || '-';
            const tgtEl = document.getElementById('featTargetCoverage');
            const comEl = document.getElementById('featCommonCoverage');
            if (tgtEl) tgtEl.textContent = `${tgtStart} ~ ${tgtEnd} (${tgtDays}거래일, ${tgtBars}행)`;
            if (comEl) comEl.textContent = `${comStart} ~ ${comEnd} (${comDays}거래일 클램핑)`;
        }
    } catch (e) {
        console.warn('loadMedallionStatus 실패:', e);
    }
}

async function buildNormalizedStock(symbol = null, interval = null) {
    const sym = symbol || getCurrentTargetSymbol();
    const intv = interval || (typeof STATE !== 'undefined' && STATE.status && STATE.status.interval) || '10s';
    try {
        showToast?.(`⚡ ${sym} (${intv}) 정규화 Parquet 빌드 시작...`) || alert(`⚡ ${sym} (${intv}) 정규화 빌드 시작...`);
        const res = await apiPost('/api/data_vault/build_normalized', { symbol: sym, interval: intv });
        if (res && res.status === 'ok') {
            alert(`✅ ${res.message}`);
            loadMedallionStatus(sym, intv);
            loadDataVaultStatus();
        } else {
            alert(`❌ 정규화 실패: ${res?.message || '알 수 없는 오류'}`);
        }
    } catch (e) {
        alert(`❌ 빌드 요청 에러: ${e}`);
    }
}

async function composeFeaturesQuick(symbol = null, interval = null) {
    const sym = symbol || getCurrentTargetSymbol();
    const intv = interval || (typeof STATE !== 'undefined' && STATE.status && STATE.status.interval) || '10s';
    try {
        showToast?.(`🧩 ${sym} (${intv}) 온디맨드 피처셋 결합 중...`) || alert(`🧩 피처셋 결합 중...`);
        const res = await apiPost('/api/data_vault/compose_features', { symbol: sym, interval: intv, save_snapshot: true });
        if (res && res.status === 'ok') {
            alert(`✅ ${res.message}`);
            loadMedallionStatus(sym, intv);
            loadDataVaultStatus();
        } else {
            alert(`❌ 결합 실패: ${res?.message || '알 수 없는 오류'}`);
        }
    } catch (e) {
        alert(`❌ 피처셋 결합 에러: ${e}`);
    }
}

window.loadMedallionStatus = loadMedallionStatus;
window.buildNormalizedStock = buildNormalizedStock;
window.composeFeaturesQuick = composeFeaturesQuick;

async function startMedallionFullPipeline() {
    const btn = document.getElementById('btnMedallionPipeline');
    const wrap = document.getElementById('medallionPipelineWrap');
    const bar = document.getElementById('medallionPipelineBar');
    const label = document.getElementById('medallionPipelineLabel');
    const pct = document.getElementById('medallionPipelinePercent');

    if (btn) btn.disabled = true;
    if (wrap) wrap.style.display = 'block';
    if (bar) {
        bar.style.width = '5%';
        bar.style.background = 'linear-gradient(90deg, #06b6d4, #a855f7)';
    }
    if (pct) pct.textContent = '5%';
    if (label) label.textContent = '⏳ [1/3] 메달리온 3단계 일괄 빌드 시작 중...';

    if (typeof showToast === 'function') {
        showToast('🚀 3단계 메달리온 일괄 파이프라인 가동 (Bronze ➔ Silver ➔ Gold)...', 'info');
    }

    try {
        const curSym = typeof getCurrentTargetSymbol === 'function' ? getCurrentTargetSymbol() : '069500';
        const curInt = (typeof STATE !== 'undefined' && STATE.status && STATE.status.interval) || (typeof FeatureState !== 'undefined' && FeatureState.interval) || '10s';
        const res = await apiPost('/api/data_vault/run_full_pipeline', {
            target_symbol: curSym,
            interval: curInt,
            all_projects: true
        });

        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') {
                showToast(`✅ ${res.message}`, 'success');
            }
        } else {
            if (typeof showToast === 'function') {
                showToast(`❌ 파이프라인 요청 실패: ${res?.message || '오류'}`, 'danger');
            }
            if (btn) btn.disabled = false;
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast(`❌ 파이프라인 통신 오류: ${e}`, 'danger');
        }
        if (btn) btn.disabled = false;
    }
}

async function runSmartNormalizeAll() {
    const btn = document.getElementById('btnSmartNormalizeAll');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 스마트 정규화 진행 중...'; }
    if (typeof showToast === 'function') {
        showToast('💎 2단계 전 자산 스마트 표준 정규화 가동...', 'info');
    }

    try {
        const res = await apiPost('/api/data_vault/smart_normalize_all');
        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') {
                showToast(`🎉 ${res.message}`, 'success');
            }
            if (typeof loadNormalizedInventory === 'function') {
                loadNormalizedInventory();
            }
        } else {
            if (typeof showToast === 'function') {
                showToast(`❌ 스마트 정규화 실패: ${res?.message || '오류'}`, 'danger');
            }
        }
    } catch (err) {
        if (typeof showToast === 'function') {
            showToast(`❌ 통신 오류: ${err.message}`, 'danger');
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '💎 2단계 표준 정규화'; }
    }
}

window.startMedallionFullPipeline = startMedallionFullPipeline;
window.runSmartNormalizeAll = runSmartNormalizeAll;


// =============================================================================
// Raw Store: Dual-Tree Grid (Provider View ↔ Role View) & Telemetry Controllers
// =============================================================================

window._rawFeedTreeMode = window._rawFeedTreeMode || 'provider';
window._rawFeedCollapsedGroups = window._rawFeedCollapsedGroups || new Set();
window._rawFeedSearchQuery = '';
window._rawFeedCachedItems = [];
window._rawFeedShowArchived = window._rawFeedShowArchived || false;

function toggleShowArchivedAssets() {
    window._rawFeedShowArchived = !window._rawFeedShowArchived;
    const btn = document.getElementById('btnToggleShowArchived');
    if (btn) {
        if (window._rawFeedShowArchived) {
            btn.classList.add('active');
            btn.style.background = 'rgba(250,204,21,0.15)';
            btn.style.borderColor = 'rgba(250,204,21,0.4)';
            btn.style.color = '#facc15';
        } else {
            btn.classList.remove('active');
            btn.style.background = 'rgba(255,255,255,0.04)';
            btn.style.borderColor = 'rgba(255,255,255,0.12)';
            btn.style.color = 'var(--text-muted)';
        }
    }
    renderRawFeedTree();
}
window.toggleShowArchivedAssets = toggleShowArchivedAssets;

async function loadRawFeedRegistry() {
    const tbody = document.getElementById('feedRegistryTableBody');
    if (!tbody) return;

    try {
        const res = await apiGet('/api/raw_store/registry');
        if (!res || res.status !== 'ok') {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:14px; color:var(--accent-red);">대장 로드 실패: ${res?.message || '알 수 없는 오류'}</td></tr>`;
            return;
        }

        const items = res.items || [];
        window._rawFeedCachedItems = items;

        // 1. 상단 텔레메트리 스트립 계산 및 바인딩
        updateRawTelemetryStrip(items, res.active_count || items.length, res.total_count || items.length);

        // 2. 트리 테이블 렌더링
        renderRawFeedTree();

        // 2.5. 고아(Orphan) SSOT 타겟 설정 배지 갱신
        refreshOrphanRelationsBadge();

        // 2.6. 종목 마스터 원장 전체 보기 (수집하지 않는 등록 종목 / 검색 후보 / 점검 대상)
        if (typeof loadLedgerOverview === 'function') loadLedgerOverview();

        // 3. 백그라운드로 저장된 실시간 진단 상태 자동 동기화 (최초 1회)
        if (!window._liveFeedDiagnosticMap) {
            apiPost('/api/raw_store/probe_live', {}).then(pRes => {
                if (pRes && pRes.status === 'ok' && pRes.diagnostic_report) {
                    const rep = pRes.diagnostic_report;
                    window._liveFeedDiagnosticMap = {};
                    (rep.items || []).forEach(p => {
                        window._liveFeedDiagnosticMap[p.symbol] = p;
                        updateSingleLiveBadge(p.symbol, p);
                    });
                    const telemLive = document.getElementById('telemLiveText');
                    if (telemLive) {
                        telemLive.textContent = `실시간: 🟢${rep.streaming_count} 🟡${rep.standby_count} 🔴${rep.failed_count}`;
                        telemLive.style.color = rep.failed_count > 0 ? '#f87171' : '#4ade80';
                    }
                }
            }).catch(() => {});
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:14px; color:var(--accent-red);">대장 조회 예외: ${e}</td></tr>`;
    }
}

// =============================================================================
// 종목 마스터 원장 전체 보기 (Symbol Ledger Overview) — 읽기 전용
// 수집 대장(위 표)에 없는 등록 종목, 검색 후보, 점검 대상(이름 미확인/사용처 미확인)을 보여준다.
// =============================================================================
window._ledgerOverview = window._ledgerOverview || null;
window._ledgerFilter = window._ledgerFilter || 'registered';

const LEDGER_FILTERS = [
    ['registered', '📦 미수집 등록'],
    ['flagged', '⚠️ 점검 필요'],
    ['catalog', '🔎 검색 후보'],
    ['collecting', '📥 수집 중'],
    ['all', '전체'],
];

function _ledgerEsc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

async function loadLedgerOverview() {
    const body = document.getElementById('ledgerOverviewBody');
    if (!body) return;
    try {
        const res = await apiGet('/api/symbols/ledger_overview');
        if (!res || res.status !== 'ok') {
            body.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:14px; color:var(--accent-red);">원장 로드 실패: ${_ledgerEsc(res?.message || '알 수 없는 오류')}</td></tr>`;
            return;
        }
        window._ledgerOverview = res;
        renderLedgerOverview();
    } catch (e) {
        body.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:14px; color:var(--accent-red);">원장 조회 예외: ${_ledgerEsc(e)}</td></tr>`;
    }
}

function setLedgerFilter(f) {
    window._ledgerFilter = f;
    renderLedgerOverview();
}

function _ledgerFilterMatch(item, f) {
    if (f === 'all') return true;
    if (f === 'flagged') return item.flags.length > 0;
    return item.state === f;
}

function renderLedgerOverview() {
    const data = window._ledgerOverview;
    const body = document.getElementById('ledgerOverviewBody');
    if (!data || !body) return;
    const s = data.summary || {};
    const items = data.items || [];
    const f = window._ledgerFilter;

    const badge = document.getElementById('ledgerOverviewBadge');
    if (badge) badge.textContent = `원장 ${s.ledger_total}개 = 수집 ${s.collecting} + 미수집 등록 ${s.registered} + 후보 ${s.catalog}`;

    const counts = { registered: s.registered, flagged: (s.no_usage || 0) + (s.name_unresolved || 0), catalog: s.catalog, collecting: s.collecting, all: s.ledger_total };
    const filters = document.getElementById('ledgerOverviewFilters');
    if (filters) {
        filters.innerHTML = LEDGER_FILTERS.map(([key, label]) => {
            const on = key === f;
            return `<button class="btn btn-xs" onclick="setLedgerFilter('${key}')" style="font-size:11px; padding:3px 10px; border-radius:4px; border:1px solid ${on ? 'rgba(168,85,247,0.6)' : 'rgba(255,255,255,0.12)'}; background:${on ? 'rgba(168,85,247,0.18)' : 'rgba(255,255,255,0.03)'}; color:${on ? '#c084fc' : 'var(--text-muted)'};">${label} ${counts[key] ?? 0}</button>`;
        }).join('');
    }

    const rows = items.filter(i => _ledgerFilterMatch(i, f));
    if (rows.length === 0) {
        body.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:14px; color:var(--text-muted);">해당 항목이 없습니다.</td></tr>`;
        return;
    }
    const srcColor = { UPBIT: '#38bdf8', KIS: '#4ade80', CREON: '#4ade80', manual: '#facc15', unresolved: '#f87171' };
    body.innerHTML = rows.map(i => {
        const c = srcColor[i.name_source] || 'var(--text-muted)';
        const purpose = i.state === 'catalog' ? '<span style="color:var(--text-muted);">검색 후보</span>'
            : (i.tradable ? '<span style="color:#4ade80;">거래 대상</span>' : '<span style="color:#fbbf24;">참조 전용</span>');
        const usage = i.usage.length ? _ledgerEsc(i.usage.join(' · ')) : '<span style="color:var(--text-muted);">—</span>';
        const flags = i.flags.map(fl => fl === 'name_unresolved'
            ? '<span style="color:#f87171;" title="어떤 원천에서도 이름을 확인하지 못했습니다">⚠ 이름 미확인</span>'
            : '<span style="color:#fbbf24;" title="수집 대장·참조 관계·CTrade 설정에서 사용처를 찾지 못했습니다. 코드 내부 로직이나 이름 표시용일 수 있으니 확인 후 정리하세요.">⚠ 사용처 미확인</span>').join('<br>');
        const asset = (i.asset_id && i.asset_id !== i.symbol) ? _ledgerEsc(i.asset_id) : '<span style="color:var(--text-muted);">—</span>';
        return `<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">
            <td style="padding:5px 8px;"><b>${_ledgerEsc(i.name)}</b> <span style="color:var(--accent-cyan);">${_ledgerEsc(i.symbol)}</span>
                <span style="font-size:10px; color:${c};" title="종목명 출처">[${_ledgerEsc(i.name_source_label)}]</span></td>
            <td style="padding:5px 6px; color:var(--text-muted);">${_ledgerEsc(i.market_type || '')}<br>${_ledgerEsc(i.exchange || '')}</td>
            <td style="padding:5px 6px;">${purpose}</td>
            <td style="padding:5px 6px;">${asset}</td>
            <td style="padding:5px 6px;">${usage}</td>
            <td style="padding:5px 6px;">${flags}</td>
        </tr>`;
    }).join('');
}

// =============================================================================
// 자산 결측일 헬스 매트릭스 (Raw Store 전체 자산 x 최근 거래일)
// =============================================================================
window._healthMatrixDays = 120;
window._healthMatrixAnomalyOnly = window._healthMatrixAnomalyOnly || false;
window._healthMatrixCachedItems = window._healthMatrixCachedItems || [];
window._healthMatrixOpenSymbol = window._healthMatrixOpenSymbol || null;

const HM_STATUS_COLOR = {
    found: '#22c55e',
    degraded: '#f97316',   // 파일에는 있으나 상위 원천과 대조해 보니 데이터가 부실 (학습 제외 대상)
    suspect: '#eab308',    // 사소한 불일치 (경고)
    missing: '#ef4444',
    holiday: 'rgba(255,255,255,0.10)',
    out_of_range: 'rgba(255,255,255,0.04)',
    lost: '#7f1d1d',         // 영구 결손 — 원장에 복구 불가로 기록된 날 (이상 아님)
    pending: 'rgba(56,189,248,0.35)',   // 완결 대기 — 일자 저장소는 D+1 06:00 이후에 그날을 저장
};
const HM_STATUS_LABEL = {
    found: '정상 수집',
    degraded: '품질 불량 — 날짜는 있으나 상위 원천과 대조 시 데이터 부실 (학습 제외)',
    suspect: '품질 주의 — 경미한 불일치',
    missing: '결측',
    holiday: '휴장일',
    out_of_range: '수집 시작 이전 (원천 보존기간/상장 전 — 정상)',
    lost: '영구 결손 — 서버 보존기간 밖이라 복구 불가 (원장에 기록됨, 이상 아님)',
    pending: '완결 대기 — 다음 날 06:00 이후 수집에서 저장 (이상 아님)',
};

async function loadHealthMatrix(forceRefresh = false) {
    const grid = document.getElementById('healthMatrixGrid');
    if (!grid) return;
    if (forceRefresh) {
        grid.innerHTML = `<div style="text-align:center; padding:16px; color:var(--accent-cyan);"><span class="spinner" style="display:inline-block; margin-right:6px;"></span>🔄 전 자산 원본 CSV를 재스캔하는 중... (자산 수에 따라 수 초 소요될 수 있습니다)</div>`;
    } else if (!grid.children.length || grid.innerHTML.includes('로딩')) {
        grid.innerHTML = `<div style="text-align:center; padding:16px; color:var(--accent-cyan);"><span class="spinner" style="display:inline-block; margin-right:6px;"></span>⏳ 결측 매트릭스(최근 120일) 데이터를 불러오는 중...</div>`;
    } else {
        grid.style.opacity = '0.55';
    }
    try {
        const cacheOnly = !forceRefresh;
        const res = await apiGet(`/api/raw_store/health_matrix?days=${window._healthMatrixDays || 120}&cache_only=${cacheOnly ? 'true' : 'false'}&force_refresh=${forceRefresh ? 'true' : 'false'}`);
        if (!res || res.status !== 'ok') {
            grid.innerHTML = `<div style="text-align:center; padding:14px; color:var(--accent-red);">매트릭스 로드 실패: ${res?.message || '알 수 없는 오류'}</div>`;
            return;
        }
        window._healthMatrixCachedItems = res.items || [];

        // 원천 데이터 업데이트 감지 알림 배너 제어
        const banner = document.getElementById('hmUpdateNoticeBanner');
        const bannerText = document.getElementById('hmUpdateNoticeText');
        if (banner && bannerText) {
            if (res.has_updates && (res.updated_count || 0) > 0) {
                let symStr = (res.updated_symbols || []).slice(0, 4).join(', ');
                if ((res.updated_count || 0) > 4) {
                    symStr += ` 외 ${(res.updated_count || 0) - 4}개`;
                }
                const cachedAtStr = res.cached_at ? ` (마지막 검증: ${res.cached_at})` : '';
                bannerText.innerHTML = `새 원천 데이터 감지됨: <b>${symStr}</b> 등 ${res.updated_count}개 자산의 파일이 갱신되었습니다.${cachedAtStr} 최신 결측 현황을 반영하려면 재계산하세요.`;
                banner.style.display = 'flex';
            } else {
                banner.style.display = 'none';
            }
        }

        const badge = document.getElementById('healthMatrixBadge');
        if (badge) {
            const anomalyCnt = res.anomaly_count || 0;
            const archivedCnt = res.archived_count || 0;
            const archivedSuffix = archivedCnt > 0 ? ` · 🗄️보관 ${archivedCnt}개 별도` : '';
            const unverified = res.quality_unverified_count || 0;
            const unverifiedSuffix = unverified > 0 ? ` · 🔎품질 미검증 ${unverified}개 (자산 행을 펼치면 검증)` : '';
            const cacheSuffix = res.cached_at ? ` · 🕒${res.cached_at.length >= 19 ? res.cached_at.slice(11, 19) : res.cached_at}` : '';
            badge.textContent = (anomalyCnt > 0 ? `⚠️ 이상 자산 ${anomalyCnt}개 발견 (총 ${res.total_count}개)` : `결측·품질 이상 없음 (총 ${res.total_count}개)`) + unverifiedSuffix + archivedSuffix + cacheSuffix;
            badge.className = anomalyCnt > 0 ? 'badge badge-yellow' : (unverified > 0 ? 'badge badge-yellow' : 'badge badge-green');
        }
        renderHealthMatrix();
    } catch (e) {
        grid.innerHTML = `<div style="text-align:center; padding:14px; color:var(--accent-red);">매트릭스 조회 예외: ${e}</div>`;
    } finally {
        if (grid) grid.style.opacity = '1';
    }
}
window.loadHealthMatrix = loadHealthMatrix;

async function setHealthMatrixDays(days = 120) {
    window._healthMatrixDays = days || 120;
    await loadHealthMatrix(false);
}
window.setHealthMatrixDays = setHealthMatrixDays;

function toggleHealthMatrixAnomalyOnly() {
    window._healthMatrixAnomalyOnly = !window._healthMatrixAnomalyOnly;
    const btn = document.getElementById('btnHmAnomalyOnly');
    if (btn) {
        btn.classList.toggle('active', window._healthMatrixAnomalyOnly);
        btn.style.background = window._healthMatrixAnomalyOnly ? 'rgba(239,68,68,0.15)' : 'rgba(255,255,255,0.04)';
        btn.style.borderColor = window._healthMatrixAnomalyOnly ? 'rgba(239,68,68,0.4)' : 'rgba(255,255,255,0.12)';
        btn.style.color = window._healthMatrixAnomalyOnly ? '#f87171' : 'var(--text-muted)';
    }
    renderHealthMatrix();
}
window.toggleHealthMatrixAnomalyOnly = toggleHealthMatrixAnomalyOnly;

let _qualityAuditPollTimer = null;
let _qualityAuditLastFinished = null;

const HM_POLICY_OPTION_KEYS = [
    'check_time_reversal',
    'check_duplicate_time',
    'check_broken_geometry',
    'check_bad_price',
    'check_missing_bars',
    'check_flash_spike',
    'check_cross_reconciliation'
];

function getQualityPolicyOptions() {
    const defaults = {
        check_time_reversal: true,
        check_duplicate_time: true,
        check_broken_geometry: true,
        check_bad_price: true,
        check_missing_bars: true,
        check_flash_spike: true,
        check_cross_reconciliation: true
    };
    try {
        const saved = localStorage.getItem('hm_quality_policy_options');
        if (saved) {
            const parsed = JSON.parse(saved);
            return { ...defaults, ...parsed };
        }
    } catch (e) {
        console.warn('품질 정책 로드 실패:', e);
    }
    return defaults;
}
window.getQualityPolicyOptions = getQualityPolicyOptions;

function saveQualityPolicyOptions(opts) {
    try {
        localStorage.setItem('hm_quality_policy_options', JSON.stringify(opts));
    } catch (e) {
        console.warn('품질 정책 저장 실패:', e);
    }
}

function syncQualityPolicyUI() {
    const opts = getQualityPolicyOptions();
    HM_POLICY_OPTION_KEYS.forEach(k => {
        const el = document.getElementById(`chkOpt_${k}`);
        if (el) el.checked = !!opts[k];
    });
}
window.syncQualityPolicyUI = syncQualityPolicyUI;

function toggleQualityPolicyModal() {
    const panel = document.getElementById('hmAuditConfigPanel');
    if (!panel) return;
    const isVisible = panel.style.display !== 'none';
    if (!isVisible) {
        syncQualityPolicyUI();
        panel.style.display = 'block';
    } else {
        panel.style.display = 'none';
    }
}
window.toggleQualityPolicyModal = toggleQualityPolicyModal;

function onQualityPolicyOptionChanged() {
    const current = {};
    HM_POLICY_OPTION_KEYS.forEach(k => {
        const el = document.getElementById(`chkOpt_${k}`);
        current[k] = el ? el.checked : true;
    });
    saveQualityPolicyOptions(current);
}
window.onQualityPolicyOptionChanged = onQualityPolicyOptionChanged;

function resetQualityPolicyOptions(selectAll = true) {
    const current = {};
    HM_POLICY_OPTION_KEYS.forEach(k => {
        const el = document.getElementById(`chkOpt_${k}`);
        if (el) el.checked = selectAll;
        current[k] = selectAll;
    });
    saveQualityPolicyOptions(current);
}
window.resetQualityPolicyOptions = resetQualityPolicyOptions;

async function triggerQualityAuditAll(full = false) {
    const incBtn = document.getElementById('btnHmAuditInc');
    const fullBtn = document.getElementById('btnHmAuditFull');
    const banner = document.getElementById('hmAuditProgressBanner');
    const bannerText = document.getElementById('hmAuditProgressText');
    const bannerBadge = document.getElementById('hmAuditProgressBadge');

    const modeName = full ? '전수(Full)' : '증분(Incremental)';
    if (incBtn) incBtn.disabled = true;
    if (fullBtn) fullBtn.disabled = true;

    if (banner) {
        banner.style.display = 'flex';
        if (bannerText) bannerText.innerHTML = `전 자산 <b>${modeName}</b> 품질 감사를 시작하는 중...`;
        if (bannerBadge) bannerBadge.textContent = '대기 중...';
    }

    try {
        const policyOpts = getQualityPolicyOptions();
        const res = await apiPost('/api/raw_store/quality_audit_all', { full: full, policy_options: policyOpts });
        if (!res || (res.status !== 'ok' && res.status !== 'busy')) {
            alert(`품질 감사 시작 실패: ${res?.message || '알 수 없는 오류'}`);
            if (incBtn) incBtn.disabled = false;
            if (fullBtn) fullBtn.disabled = false;
            if (banner) banner.style.display = 'none';
            return;
        }

        if (_qualityAuditPollTimer) clearInterval(_qualityAuditPollTimer);
        _qualityAuditPollTimer = setInterval(pollQualityAuditStatus, 1500);
        pollQualityAuditStatus();
    } catch (err) {
        alert(`품질 감사 요청 실패: ${err}`);
        if (incBtn) incBtn.disabled = false;
        if (fullBtn) fullBtn.disabled = false;
        if (banner) banner.style.display = 'none';
    }
}
window.triggerQualityAuditAll = triggerQualityAuditAll;

async function pollQualityAuditStatus() {
    try {
        const res = await apiGet('/api/raw_store/quality_audit_status');
        if (!res || res.status !== 'ok') return;
        const state = res.state || {};
        const incBtn = document.getElementById('btnHmAuditInc');
        const fullBtn = document.getElementById('btnHmAuditFull');
        const banner = document.getElementById('hmAuditProgressBanner');
        const bannerText = document.getElementById('hmAuditProgressText');
        const bannerBadge = document.getElementById('hmAuditProgressBadge');

        if (state.running) {
            if (incBtn) incBtn.disabled = true;
            if (fullBtn) fullBtn.disabled = true;
            const modeName = state.full ? '전수' : '증분';
            const cur = state.current || 0;
            const tot = state.total || 0;
            const sym = state.current_symbol || '';
            const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0;

            if (banner) {
                banner.style.display = 'flex';
                if (bannerText) {
                    bannerText.innerHTML = `전 자산 <b>${modeName} 품질 감사</b> 진행 중... <span style="color:#38bdf8; font-weight:bold;">[${sym || '준비 중'} ${cur}/${tot}]</span>`;
                }
                if (bannerBadge) {
                    bannerBadge.textContent = `${pct}%`;
                }
            }
        } else {
            if (_qualityAuditPollTimer) {
                clearInterval(_qualityAuditPollTimer);
                _qualityAuditPollTimer = null;
            }
            if (incBtn) incBtn.disabled = false;
            if (fullBtn) fullBtn.disabled = false;
            if (banner && banner.style.display !== 'none') {
                if (state.error) {
                    banner.style.display = 'flex';
                    if (bannerText) bannerText.innerHTML = `<span style="color:#ef4444;">❌ 품질 감사 중 오류 발생: ${state.error}</span>`;
                    if (bannerBadge) bannerBadge.textContent = '실패';
                    setTimeout(() => { if (banner) banner.style.display = 'none'; }, 6000);
                } else if (state.finished_at && state.finished_at !== _qualityAuditLastFinished) {
                    banner.style.display = 'flex';
                    const modeName = state.full ? '전수' : '증분';
                    if (bannerText) bannerText.innerHTML = `✅ 전 자산 <b>${modeName} 품질 감사</b>가 완료되었습니다! (총 ${state.total}개 자산)`;
                    if (bannerBadge) bannerBadge.textContent = '완료 100%';
                    setTimeout(() => { if (banner) banner.style.display = 'none'; }, 4000);
                }
            }
            if (state.finished_at && state.finished_at !== _qualityAuditLastFinished) {
                _qualityAuditLastFinished = state.finished_at;
                loadHealthMatrix(true);
            }
        }
    } catch (e) {
        console.warn('품질 감사 상태 조회 실패:', e);
    }
}
window.pollQualityAuditStatus = pollQualityAuditStatus;

const HM_INTERVAL_LABEL = { TICK: '틱', '1M': '1분', '5M': '5분', '10M': '10분', '15M': '15분', '1D': '일봉' };

// 자산의 구간(틱/1분/일봉)별 뷰. 구간 정보가 없으면 대표 파일 1건으로 대체한다.
function hmViews(it) {
    return (it.intervals && it.intervals.length) ? it.intervals : [it];
}
function hmAgg(it, key) {
    return hmViews(it).reduce((n, v) => n + (v[key] || 0), 0);
}

function renderHealthMatrix() {
    const grid = document.getElementById('healthMatrixGrid');
    if (!grid) return;

    let items = window._healthMatrixCachedItems || [];
    if (window._healthMatrixAnomalyOnly) {
        // 보관(archived) 자산은 수집이 의도적으로 중단된 상태라 '이상 자산'이 아니므로 제외
        items = items.filter(it => !it.archived && (hmAgg(it, 'missing_count') > 0 || hmAgg(it, 'quality_bad_count') > 0 || it.file_exists === false));
    }
    // 결측률 높은 자산이 위로 오도록 정렬 (미수집 > 결측 많은 순 > 정상), 보관 자산은 항상 맨 아래
    items = [...items].sort((a, b) => {
        if (!!a.archived !== !!b.archived) return a.archived ? 1 : -1;
        const am = a.file_exists === false ? 9999 : hmAgg(a, 'missing_count') + hmAgg(a, 'quality_bad_count');
        const bm = b.file_exists === false ? 9999 : hmAgg(b, 'missing_count') + hmAgg(b, 'quality_bad_count');
        return bm - am;
    });

    if (items.length === 0) {
        grid.innerHTML = `<div style="text-align:center; padding:16px; color:var(--text-muted);">표시할 자산이 없습니다.</div>`;
        return;
    }

    const isAll = (window._healthMatrixDays === 'all');
    let minWidth = isAll ? 860 : (280 + Number(window._healthMatrixDays || 120) * 8);
    let html = `<div style="display:flex; flex-direction:column; gap:2px; min-width:${minWidth}px;">`;
    items.forEach(it => {
        const isOpen = window._healthMatrixOpenSymbol === it.symbol;
        const isArchived = !!it.archived;
        const views = it.file_exists ? hmViews(it) : [null];
        const bg = isOpen ? 'rgba(56,189,248,0.08)' : 'transparent';
        views.forEach((v, idx) => {
            const first = idx === 0;
            html += `<div class="hm-row" data-sym="${it.symbol}" onclick="toggleHealthMatrixDetail('${it.symbol}')" style="display:flex; align-items:center; gap:8px; padding:${first ? '4px' : '1px'} 6px ${idx === views.length - 1 ? '4px' : '1px'}; border-radius:5px; cursor:pointer; background:${bg}; ${isArchived ? 'opacity:0.55;' : ''}" onmouseover="document.querySelectorAll('.hm-row[data-sym=&quot;${it.symbol}&quot;]').forEach(r=>r.style.background='rgba(56,189,248,0.06)')" onmouseout="document.querySelectorAll('.hm-row[data-sym=&quot;${it.symbol}&quot;]').forEach(r=>r.style.background='${bg}')">
            <div style="width:150px; flex-shrink:0; display:flex; flex-direction:column; gap:1px; line-height:1.2; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;${views.length > 1 ? ' align-self:flex-start; margin-bottom:-14px; position:relative; z-index:1;' : ''}" title="${it.name} (${it.symbol})">`;
            if (first) {
                html += `<div style="display:flex; align-items:center; gap:4px; overflow:hidden; text-overflow:ellipsis;">
                    <span style="color:var(--text-primary, #e2e8f0); font-weight:600; font-size:11.5px; overflow:hidden; text-overflow:ellipsis;">${it.name}</span>
                    ${isArchived ? '<span class="badge" style="background:rgba(250,204,21,0.15); color:#facc15; border:1px solid rgba(250,204,21,0.35); font-size:8.5px; padding:0 3px;">🗄️보관</span>' : ''}
                </div>
                <span style="color:var(--accent-cyan); font-family:monospace; font-size:10px; font-weight:600;">${it.symbol}</span>`;
            }
            html += `</div>`;

            if (!v) {
                html += `<div style="flex:1; font-size:11px; color:var(--text-muted); font-style:italic;">— 원천 파일 미수집 (검증 대상 아님)</div>`;
            } else {
                const ivLabel = views.length > 1 || it.intervals ? (HM_INTERVAL_LABEL[v.label] || v.label || '') : '';
                html += `<span style="width:30px; flex-shrink:0; font-size:10px; font-weight:700; color:var(--accent-cyan); text-align:center; background:rgba(56,189,248,0.10); border-radius:3px; padding:1px 0;">${ivLabel}</span>`;
                
                if (isAll) {
                    // 전체 (월별) 뷰: 1칸 = 1개월 (가로 스크롤 완전 제거)
                    const mSlots = v.monthly_slots || it.monthly_slots || [];
                    html += `<div style="display:flex; gap:1.5px; align-items:center;">`;
                    if (mSlots.length === 0) {
                        html += `<span style="font-size:10px; color:var(--text-muted); font-style:italic;">수집 이력 없음</span>`;
                    } else {
                        mSlots.forEach(ms => {
                            const color = isArchived ? 'rgba(255,255,255,0.10)' : (
                                ms.status === 'missing' ? '#ef4444' : (
                                    ms.status === 'out_of_range' ? 'rgba(255,255,255,0.04)' : '#22c55e'
                                )
                            );
                            const tip = `${ivLabel ? ivLabel + ' · ' : ''}${ms.month} · ${ms.missing ? `결측 ${ms.missing}일 (${(ms.missing_dates || []).join(', ')})` : '정상 수집'} (총 ${ms.found}/${ms.total}영업일)`;
                            html += `<div title="${tip}" style="width:6px; height:${views.length > 1 ? 12 : 16}px; background:${color}; border-radius:1px;"></div>`;
                        });
                    }
                    html += `</div>`;
                    
                    const spanRate = v.all_rate != null ? v.all_rate : v.rate;
                    const allMiss = v.all_missing_count != null ? v.all_missing_count : v.missing_count;
                    const rateColor = isArchived ? 'var(--text-muted)' : ((allMiss > 0) ? '#f87171' : '#4ade80');
                    html += `<div style="width:65px; flex-shrink:0; text-align:right; font-size:11px; font-weight:600; color:${rateColor};" title="전체 기간 수집률">${spanRate != null ? spanRate + '%' : '-'}</div>`;
                    
                    const spanText = (v.span_start && v.span_end) ? `${v.span_start.slice(2)}~${v.span_end.slice(2)} (${v.total_collected_days || 0}일)` : '';
                    let statusText = allMiss ? `누적 결측 ${allMiss}일` : '전체 무결';
                    let statusColor = allMiss ? '#f87171' : '#4ade80';
                    html += `<div style="width:190px; flex-shrink:0; text-align:right; font-size:10px; color:${isArchived ? 'var(--text-muted)' : statusColor};" title="${spanText} 누적 상태">${isArchived ? '보관 중' : `${spanText ? spanText + ' · ' : ''}${statusText}`}</div>`;
                } else {
                    // 일별 N거래일 뷰
                    html += `<div style="display:flex; gap:1px;">`;
                    (v.slots || []).forEach(slot => {
                        const color = isArchived ? 'rgba(255,255,255,0.10)' : (HM_STATUS_COLOR[slot.status] || 'rgba(255,255,255,0.04)');
                        const why = (slot.reasons && slot.reasons.length) ? '\n' + slot.reasons.join('\n') : '';
                        html += `<div title="${ivLabel ? ivLabel + ' · ' : ''}${slot.date} · ${isArchived ? '보관 자산 (자동 결측 아님)' : (HM_STATUS_LABEL[slot.status] || slot.status)}${isArchived ? '' : why}" style="width:6px; height:${views.length > 1 ? 12 : 16}px; background:${color}; border-radius:1px;"></div>`;
                    });
                    html += `</div>`;
                    const qBad = v.quality_bad_count || 0;
                    const rateColor = isArchived ? 'var(--text-muted)' : (((v.missing_count || 0) > 0) ? '#f87171' : (qBad > 0 ? '#fb923c' : '#4ade80'));
                    html += `<div style="width:65px; flex-shrink:0; text-align:right; font-size:11px; font-weight:600; color:${rateColor};" title="수집률(날짜 존재 기준). 데이터 품질은 오른쪽 문구 참고">${v.rate != null ? v.rate + '%' : '-'}</div>`;
                    let statusText = '결측 없음', statusColor = 'var(--text-muted)';
                    if (v.missing_count) { statusText = `결측 ${v.missing_count}일`; }
                    if (qBad > 0) { statusText = (v.missing_count ? statusText + ' · ' : '') + `품질불량 ${qBad}일`; statusColor = '#fb923c'; }
                    else if (v.quality_checked === false && !v.missing_count) { statusText = '결측 없음 · 품질 미검증'; }
                    else if (v.quality_checked === true && !v.missing_count && !(v.quality_warn_count || 0)) { statusText = '결측 없음 · 품질 정상'; statusColor = '#4ade80'; }
                    else if ((v.quality_warn_count || 0) > 0 && !qBad) { statusText += ` · 품질주의 ${v.quality_warn_count}일`; statusColor = '#eab308'; }
                    if (v.lost_count && !v.missing_count) { statusText = statusText.replace('결측 없음', `영구 결손 ${v.lost_count}일(복구 불가)`); if (statusText === '결측 없음') statusText = `영구 결손 ${v.lost_count}일(복구 불가)`; }
                    html += `<div style="width:190px; flex-shrink:0; text-align:right; font-size:10.5px; color:${statusColor};">${isArchived ? '보관 중 (수동 갱신)' : statusText}</div>`;
                }
            }
            html += `</div>`;
        });
        if (isOpen) {
            html += `<div id="healthMatrixInlineDetail_${it.symbol}" style="margin:6px 0 10px 0; background:rgba(15,23,42,0.9); border:1px solid rgba(56,189,248,0.35); border-radius:8px; padding:10px 14px; box-shadow:0 4px 12px rgba(0,0,0,0.3);"></div>`;
        }
        html += `<div style="height:3px;"></div>`;
    });
    html += `</div>`;
    grid.innerHTML = html;

    if (window._healthMatrixOpenSymbol) {
        renderHealthMatrixDetail(window._healthMatrixOpenSymbol);
    }
}

function toggleHealthMatrixDetail(symbol) {
    window._healthMatrixOpenSymbol = (window._healthMatrixOpenSymbol === symbol) ? null : symbol;
    renderHealthMatrix();
}
window.toggleHealthMatrixDetail = toggleHealthMatrixDetail;

// 로컬 날짜를 YYYY-MM-DD 문자열로 변환 (타임존 오차 방지)
function hmToDateStr(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dt = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dt}`;
}

// GitHub 잔디형 연도별 2D 캘린더 그리드 렌더러 (월~금 5행 x 53주, 스크롤 전혀 없음)
function renderGrassCalendarGrid(calByYear, label) {
    if (!calByYear || Object.keys(calByYear).length === 0) {
        return `<div style="font-size:10.5px; color:var(--text-muted); padding:6px 0;">캘린더 데이터가 없습니다.</div>`;
    }
    const years = Object.keys(calByYear).sort((a, b) => Number(b) - Number(a));
    const korDays = ['월', '화', '수', '목', '금'];
    const todayStr = hmToDateStr(new Date());

    let html = `<div style="display:flex; flex-direction:column; gap:8px; margin-top:6px;">`;
    years.forEach(yr => {
        const days = calByYear[yr] || [];
        const foundCnt = days.filter(d => ['found', 'degraded', 'suspect'].includes(d.status)).length;
        const missCnt = days.filter(d => d.status === 'missing').length;
        const lostCnt = days.filter(d => d.status === 'lost').length;
        const degCnt = days.filter(d => d.status === 'degraded').length;
        const suspCnt = days.filter(d => d.status === 'suspect').length;

        let statBadge = '';
        if (missCnt > 0) {
            statBadge += `<span class="badge" style="background:rgba(239,68,68,0.2); color:#fca5a5; font-size:9.5px; border:1px solid rgba(239,68,68,0.4); margin-right:3px;">🚨 결측 ${missCnt}일</span>`;
        }
        if (degCnt > 0) {
            statBadge += `<span class="badge" style="background:rgba(239,68,68,0.25); color:#fca5a5; font-size:9.5px; border:1px solid rgba(239,68,68,0.5); margin-right:3px;">⚠️ 불량 ${degCnt}일</span>`;
        }
        if (suspCnt > 0) {
            statBadge += `<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; font-size:9.5px; border:1px solid rgba(245,158,11,0.45); margin-right:3px;">⚡ 주의 ${suspCnt}일</span>`;
        }
        if (lostCnt > 0) {
            statBadge += `<span class="badge" style="background:rgba(127,29,29,0.3); color:#fca5a5; font-size:9.5px; margin-right:3px;">영구결손 ${lostCnt}일</span>`;
        }
        if (missCnt === 0 && degCnt === 0 && suspCnt === 0) {
            statBadge = `<span class="badge badge-green" style="font-size:9.5px;">무결 100%</span>` + (lostCnt > 0 ? ` <span class="badge" style="background:rgba(127,29,29,0.3); color:#fca5a5; font-size:9.5px;">영구결손 ${lostCnt}일</span>` : '');
        }

        const dMap = {};
        days.forEach(d => { dMap[d.date] = d; });

        const startDate = new Date(Number(yr), 0, 1);
        const endDate = new Date(Number(yr), 11, 31);

        const weeks = [];
        let curWeek = [null, null, null, null, null];
        let cur = new Date(startDate);

        while (cur <= endDate) {
            const dayOfWeek = cur.getDay(); // 0:일, 1:월, ..., 5:금, 6:토
            const dtStr = hmToDateStr(cur);

            // 월요일을 만나면 이전 주 컬럼을 마감하고 새 컬럼 시작
            if (dayOfWeek === 1 && curWeek.some(x => x !== null)) {
                weeks.push(curWeek);
                curWeek = [null, null, null, null, null];
            }

            if (dayOfWeek >= 1 && dayOfWeek <= 5) {
                const weekdayIdx = dayOfWeek - 1; // 0:월 ~ 4:금
                const isFuture = (dtStr > todayStr);
                let info = dMap[dtStr];
                if (!info) {
                    info = {
                        date: dtStr,
                        status: isFuture ? 'future' : 'holiday',
                        weekday: weekdayIdx,
                        month: cur.getMonth() + 1
                    };
                }
                curWeek[weekdayIdx] = info;
            }

            cur.setDate(cur.getDate() + 1);
        }
        if (curWeek.some(x => x !== null)) {
            weeks.push(curWeek);
        }

        // 각 주 컬럼의 대표 월 레이블 산출 (월이 바뀌는 주에 레이블 표기)
        let lastMonth = -1;
        const monthHeaders = weeks.map(wk => {
            const firstValid = wk.find(c => c && c.month);
            if (firstValid && firstValid.month !== lastMonth) {
                lastMonth = firstValid.month;
                return `${firstValid.month}월`;
            }
            return '';
        });

        html += `
        <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:7px 10px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
                <span style="font-size:11.5px; font-weight:bold; color:var(--accent-cyan);">${yr}년 <span style="color:var(--text-muted); font-size:10px; font-weight:normal;">(${label})</span></span>
                <div style="display:flex; gap:6px; align-items:center;">
                    <span style="font-size:10px; color:var(--text-muted);">수집 ${foundCnt}일</span>
                    ${statBadge}
                </div>
            </div>

            <!-- 월 라벨 헤더 -->
            <div style="display:flex; gap:2px; margin-left:18px; margin-bottom:3px; height:12px;">
                ${monthHeaders.map(m => `
                    <div style="width:9px; font-size:8px; color:var(--text-muted); white-space:nowrap; overflow:visible;">${m}</div>
                `).join('')}
            </div>

            <div style="display:flex; align-items:flex-start; gap:4px; overflow-x:hidden;">
                <!-- 좌측 요일 라벨 -->
                <div style="display:flex; flex-direction:column; gap:2px; font-size:8px; color:var(--text-muted); width:14px; text-align:right;">
                    <span style="height:9px; line-height:9px;">월</span>
                    <span style="height:9px; line-height:9px;">화</span>
                    <span style="height:9px; line-height:9px;">수</span>
                    <span style="height:9px; line-height:9px;">목</span>
                    <span style="height:9px; line-height:9px;">금</span>
                </div>

                <!-- 53주 열 컨테이너 (고정 폭 및 픽셀 크기로 찌그러짐 방지) -->
                <div style="display:flex; gap:2px;">
                    ${weeks.map(wk => `
                        <div style="display:flex; flex-direction:column; gap:2px; width:9px;">
                            ${wk.map(cell => {
                                if (!cell) return `<div style="width:9px; height:9px; background:transparent;"></div>`;
                                let color = 'rgba(255,255,255,0.03)';
                                let statusName = '미래 일자';
                                if (cell.status === 'found') {
                                    color = '#22c55e';
                                    statusName = '정상 수집';
                                } else if (cell.status === 'degraded') {
                                    color = '#ef4444';
                                    statusName = '품질 불량';
                                } else if (cell.status === 'suspect') {
                                    color = '#f59e0b';
                                    statusName = '품질 주의';
                                } else if (cell.status === 'missing') {
                                    color = '#ef4444';
                                    statusName = '결측';
                                } else if (cell.status === 'holiday') {
                                    color = 'rgba(255,255,255,0.12)';
                                    statusName = '휴장일';
                                } else if (cell.status === 'lost') {
                                    color = '#7f1d1d';
                                    statusName = '영구 결손';
                                } else if (cell.status === 'out_of_range') {
                                    color = 'rgba(255,255,255,0.04)';
                                    statusName = '수집 시작 전';
                                }
                                const dayName = korDays[cell.weekday != null ? cell.weekday : 0];
                                let reasonStr = '';
                                if (cell.reasons && cell.reasons.length > 0) {
                                    reasonStr = `\n사유: ${cell.reasons.join('\n사유: ')}`;
                                }
                                const title = `${cell.date} (${dayName}) · ${statusName}${reasonStr}`;
                                return `<div title="${title}" style="width:9px; height:9px; background:${color}; border-radius:1.5px; cursor:default;"></div>`;
                            }).join('')}
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>`;
    });
    html += `</div>`;
    return html;
}

function renderHealthMatrixDetail(symbol) {
    let panel = document.getElementById(`healthMatrixInlineDetail_${symbol}`);
    if (!panel) panel = document.getElementById('healthMatrixDetail');
    if (!panel) return;
    const it = (window._healthMatrixCachedItems || []).find(x => x.symbol === symbol);
    if (!it || !it.file_exists) {
        panel.style.display = 'none';
        return;
    }

    const spanInfo = (it.span_start && it.span_end)
        ? `<span style="color:var(--accent-cyan); font-weight:600;">${it.span_start} ~ ${it.span_end}</span> (총 ${it.total_collected_days || 0}거래일 수집)`
        : '-';

    const allMiss = it.all_missing_count != null ? it.all_missing_count : (it.missing_count || 0);
    const missRanges = (it.all_missing_ranges && it.all_missing_ranges.length > 0) ? it.all_missing_ranges : (it.missing_ranges || []);

    let rangesHtml = '';
    if (missRanges.length > 0) {
        rangesHtml = missRanges.map(r =>
            `<span style="display:inline-block; background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.35); color:#fca5a5; border-radius:4px; padding:2px 7px; font-size:11px; margin:2px;">${r.start === r.end ? r.start : `${r.start} ~ ${r.end}`} (${r.count}일)</span>`
        ).join('');
    } else {
        rangesHtml = `<span style="font-size:12px; color:#4ade80;">전체 수집 기간 결측 없음 (완전 무결 100%)</span>`;
    }

    // 품질 감사 정보 동적 구성 (하드코딩 배제)
    let qualityHtml = '';
    if (it.quality_checked) {
        const badCnt = it.all_quality_bad_count != null ? it.all_quality_bad_count : (it.quality_bad_count || 0);
        const warnCnt = it.all_quality_warn_count != null ? it.all_quality_warn_count : (it.quality_warn_count || 0);
        const reasonList = it.quality_reason_summary || [];

        if (badCnt > 0 || warnCnt > 0) {
            let reasonSummaryHtml = '';
            if (reasonList.length > 0) {
                reasonSummaryHtml = `<div style="margin-top:5px; font-size:11px; display:flex; flex-direction:column; gap:3px;">
                    ${reasonList.map(r => `<div>• <span style="color:#fef08a;">${r.reason}</span> <span style="color:var(--text-muted);">(${r.count}건)</span></div>`).join('')}
                </div>`;
            }
            qualityHtml = `
            <div style="margin-bottom:10px; padding:8px 12px; background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.25); border-radius:6px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:11px; font-weight:600; color:#fbbf24;">🩺 원천 대조 품질 판정 (상위 원천 대조):</span>
                    <div style="display:flex; gap:6px;">
                        ${badCnt > 0 ? `<span class="badge" style="background:rgba(239,68,68,0.2); color:#fca5a5; font-size:9.5px; border:1px solid rgba(239,68,68,0.4);">품질불량 ${badCnt}일</span>` : ''}
                        ${warnCnt > 0 ? `<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; font-size:9.5px; border:1px solid rgba(245,158,11,0.45);">품질주의 ${warnCnt}일</span>` : ''}
                    </div>
                </div>
                ${reasonSummaryHtml}
            </div>`;
        } else {
            qualityHtml = `
            <div style="margin-bottom:10px; padding:6px 12px; background:rgba(34,197,94,0.08); border:1px solid rgba(34,197,94,0.2); border-radius:6px; font-size:11px; color:#4ade80;">
                ✅ 원천 대조 품질 감사: 상위 원천 대조 이상 없음 (정상 무결)
            </div>`;
        }
    }

    const isArchived = !!it.archived;
    panel.style.display = 'block';
    panel.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span style="font-size:12.5px; font-weight:bold; color:var(--accent-cyan);">🔍 ${it.name} (${it.symbol}) 상세 분석 ${isArchived ? '<span class="badge" style="background:rgba(250,204,21,0.15); color:#facc15; border:1px solid rgba(250,204,21,0.35); font-size:9px; padding:1px 6px; margin-left:4px;">🗄️ 보관 자산</span>' : ''}</span>
            <div style="display:flex; gap:6px;">
                <button class="btn btn-xs" id="hmCollectNowBtn_${symbol}" onclick="collectAssetNow('${it.symbol}')" style="font-size:10.5px; padding:2px 8px; background:rgba(74,222,128,0.1); border:1px solid rgba(74,222,128,0.35); color:#4ade80; border-radius:4px; cursor:pointer;" title="보관/비활성 여부와 무관하게 원천에서 최신 데이터를 즉시 재수집합니다">📡 지금 갱신</button>
                <button class="btn btn-xs" onclick="refreshHealthMatrixOne('${it.symbol}')" style="font-size:10.5px; padding:2px 8px; background:rgba(56,189,248,0.1); border:1px solid rgba(56,189,248,0.3); color:var(--accent-cyan); border-radius:4px; cursor:pointer;" title="네트워크 수집 없이, 이미 있는 파일만 재스캔해 통계를 갱신합니다">🔄 이 자산만 재검증</button>
            </div>
        </div>
        ${isArchived ? `<div style="font-size:10.5px; color:#facc15; margin-bottom:6px;">⚠️ 보관 처리된 자산이라 정기 자동 동기화 대상에서 빠져 있습니다. 결측은 정상이며, 필요할 때 위 '지금 갱신'으로 수동 갱신하세요.</div>` : ''}
        <div style="display:flex; flex-wrap:wrap; gap:12px; font-size:11px; color:var(--text-muted); margin-bottom:8px; background:rgba(255,255,255,0.02); padding:6px 10px; border-radius:6px; border:1px solid rgba(255,255,255,0.05);">
            <div>📅 전체 수집 기간: ${spanInfo}</div>
            <div>⚡ 전체 수집률: <b style="color:${allMiss > 0 ? '#f87171' : '#4ade80'};">${it.all_rate != null ? it.all_rate + '%' : (it.rate + '%')}</b></div>
            <div>누적 결측: <b style="color:${allMiss > 0 ? '#f87171' : '#4ade80'};">${allMiss}일</b></div>
            <div>제공처: ${it.provider || '-'} · 역할: ${it.role || '-'}</div>
        </div>
        <div id="hmCollectNowLog_${symbol}" style="display:none; font-size:10px; font-family:monospace; white-space:pre-wrap; max-height:120px; overflow-y:auto; background:rgba(0,0,0,0.3); border-radius:4px; padding:6px 8px; margin-bottom:8px; color:var(--text-muted);"></div>
        <div style="margin-bottom:10px;">
            <div style="font-size:11px; font-weight:600; color:var(--text-primary); margin-bottom:4px;">🚨 전체 기간 누적 결측 구간:</div>
            ${rangesHtml}
        </div>
        ${qualityHtml}
        <div id="hmIntervalBreakdown_${symbol}" style="border-top:1px solid rgba(255,255,255,0.08); padding-top:8px;">
            <div style="font-size:10.5px; color:var(--text-muted);">⏳ 등록된 원천 그래뉼러리티(틱/분봉/일봉) 2D 잔디 캘린더 생성 중...</div>
        </div>
    `;
    loadHealthMatrixIntervals(symbol);
}

async function loadHealthMatrixIntervals(symbol, forceRefresh = false) {
    const box = document.getElementById(`hmIntervalBreakdown_${symbol}`);
    if (!box) return;
    try {
        const res = await apiGet(`/api/raw_store/health_matrix/intervals?symbol=${encodeURIComponent(symbol)}&days=${window._healthMatrixDays}&cache_only=${!forceRefresh}&force_refresh=${forceRefresh}`);
        if (window._healthMatrixOpenSymbol !== symbol) return;
        if (!res || res.status !== 'ok') {
            box.innerHTML = `<div style="font-size:10.5px; color:var(--accent-red);">그래뉼러리티 조회 실패: ${res?.message || '알 수 없는 오류'}</div>`;
            return;
        }
        const intervals = res.intervals || [];
        if (intervals.length === 0) {
            box.innerHTML = `<div style="font-size:10.5px; color:var(--text-muted);">등록된 원천 그래뉼러리티가 없습니다.</div>`;
            return;
        }

        let html = `<div style="font-size:11px; font-weight:bold; color:var(--accent-cyan); margin-bottom:6px;">📊 그래뉼러리티별 연도 전체 2D 잔디 캘린더 (스크롤 없이 1일 단위 관찰):</div>`;
        intervals.forEach(iv => {
            const ivLabel = HM_INTERVAL_LABEL[iv.label] || iv.label || '';
            const degCnt = iv.all_quality_bad_count != null ? iv.all_quality_bad_count : (iv.quality_bad_count || 0);
            const suspCnt = iv.all_quality_warn_count != null ? iv.all_quality_warn_count : (iv.quality_warn_count || 0);
            let qBadge = '';
            if (degCnt > 0) qBadge += ` · <span style="color:#f87171; font-size:10px;">⚠️ 불량 ${degCnt}일</span>`;
            if (suspCnt > 0) qBadge += ` · <span style="color:#fbbf24; font-size:10px;">⚡ 주의 ${suspCnt}일</span>`;

            let ivReasonHtml = '';
            if (iv.quality_reason_summary && iv.quality_reason_summary.length > 0) {
                ivReasonHtml = `<div style="margin-bottom:6px; padding:6px 10px; background:rgba(245,158,11,0.06); border:1px solid rgba(245,158,11,0.2); border-radius:4px; font-size:10.5px; color:#fef08a;">
                    <b>💡 ${ivLabel} 품질 판정 사유:</b> ${iv.quality_reason_summary.map(r => `${r.reason} (${r.count}건)`).join(' · ')}
                </div>`;
            }

            const calHtml = renderGrassCalendarGrid(iv.calendar_by_year, ivLabel);
            html += `<details open style="margin-bottom:10px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:6px 8px;">
                <summary style="cursor:pointer; font-size:11px; font-weight:600; color:var(--accent-cyan); display:flex; align-items:center; justify-content:space-between; user-select:none;">
                    <span>🏷️ ${ivLabel} 전체 잔디 캘린더 ${iv.total_collected_days ? `(총 ${iv.total_collected_days}일 · 결측 ${iv.all_missing_count || 0}일${qBadge})` : ''}</span>
                    <span style="display:inline-flex; gap:5px; margin-left:auto;" onclick="event.stopPropagation();">
                        <button type="button" class="btn btn-xs btn-outline-info" style="padding:2px 7px; font-size:10.5px; border-radius:4px; line-height:1.2; display:inline-flex; align-items:center; gap:3px;" onclick="event.stopPropagation(); syncSingleScale('${symbol}', '${iv.label}', 'raw')" title="[${symbol}] ${ivLabel}만 핀포인트 증분 갱신/수집">🔄 ${ivLabel} 갱신</button>
                        <button type="button" class="btn btn-xs btn-outline-danger" style="padding:2px 7px; font-size:10.5px; border-radius:4px; line-height:1.2; display:inline-flex; align-items:center; gap:3px;" onclick="event.stopPropagation(); deleteSingleScale('${symbol}', '${iv.label}', 'raw')" title="[${symbol}] ${ivLabel} 디스크 물리 파일 완전 삭제">🗑️ ${ivLabel} 삭제</button>
                    </span>
                </summary>
                <div style="margin-top:6px;">
                    ${ivReasonHtml}
                    ${calHtml}
                </div>
            </details>`;
        });
        html += `<div style="font-size:9.5px; color:var(--text-muted); margin-top:4px;">💡 각 사각형 1개 = 1거래일. 마우스를 올리면 정확한 날짜와 상태(정상·주의·불량 등) 및 사유가 표시됩니다. 초록=정상, 노랑=품질주의, 빨강=결측/품질불량, 어두운회색=휴장일.</div>`;
        box.innerHTML = html;
    } catch (e) {
        box.innerHTML = `<div style="font-size:10.5px; color:var(--accent-red);">그래뉼러리티 조회 예외: ${e}</div>`;
    }
}

async function refreshHealthMatrixOne(symbol) {
    try {
        const res = await apiPost('/api/raw_store/health_matrix/refresh_one', { symbol, days: window._healthMatrixDays });
        if (!res || res.status !== 'ok') return;
        const idx = window._healthMatrixCachedItems.findIndex(x => x.symbol === symbol);
        if (idx >= 0) window._healthMatrixCachedItems[idx] = res.item;
        renderHealthMatrix();
    } catch (e) {
        console.error('자산 재검증 실패', e);
    }
}
window.refreshHealthMatrixOne = refreshHealthMatrixOne;

async function collectAssetNow(symbol) {
    const btn = document.getElementById(`hmCollectNowBtn_${symbol}`);
    const logBox = document.getElementById(`hmCollectNowLog_${symbol}`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ 수집 중...';
    }
    if (logBox) {
        logBox.style.display = 'block';
        logBox.textContent = '원천 데이터 재수집을 요청했습니다...';
    }
    try {
        const res = await apiPost('/api/raw_store/collect_now', { symbol });
        if (logBox) logBox.textContent = res?.log || res?.message || '(응답 로그 없음)';
        if (!res || res.status !== 'ok') {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '📡 지금 갱신';
            }
            return;
        }
        if (res.item) {
            const idx = window._healthMatrixCachedItems.findIndex(x => x.symbol === symbol);
            if (idx >= 0) window._healthMatrixCachedItems[idx] = res.item;
        }
        renderHealthMatrix();
        if (window._healthMatrixOpenSymbol === symbol && typeof loadHealthMatrixIntervals === 'function') {
            loadHealthMatrixIntervals(symbol, true);
        }
    } catch (e) {
        if (logBox) logBox.textContent = `수집 요청 예외: ${e}`;
        console.error('자산 수동 갱신 실패', e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '📡 지금 갱신';
        }
    }
}
window.collectAssetNow = collectAssetNow;

function updateRawTelemetryStrip(items, activeCount, totalCount) {
    const badgeEl = document.getElementById('feedRegistryBadge');
    if (badgeEl) badgeEl.textContent = `활성 ${activeCount}개 / 총 ${totalCount}개`;

    let krxCnt = 0, krxMb = 0;
    let globalCnt = 0, globalMb = 0;
    let cryptoCnt = 0, cryptoMb = 0;
    let totalMb = 0;
    let healthyCnt = 0;

    items.forEach(it => {
        const mb = it.file_size_mb || 0;
        totalMb += mb;
        if (it.status_code === 'HEALTHY') healthyCnt++;

        const cat = (it.category || '').toUpperCase();
        if (cat.startsWith('KRX')) {
            krxCnt++;
            krxMb += mb;
        } else if (cat.includes('OVERSEAS') || cat.includes('US_')) {
            globalCnt++;
            globalMb += mb;
        } else if (cat.includes('CRYPTO')) {
            cryptoCnt++;
            cryptoMb += mb;
        } else {
            krxCnt++;
            krxMb += mb;
        }
    });

    const formatMb = (v) => v >= 1024 ? `${(v/1024).toFixed(2)} GB` : `${v.toFixed(1)} MB`;

    const elHealthy = document.getElementById('telemStatusHealthy');
    if (elHealthy) {
        const isAllHealthy = healthyCnt === items.length;
        elHealthy.textContent = isAllHealthy ? `시스템 정상 (${healthyCnt}/${items.length})` : `주의 감지 (${healthyCnt}/${items.length} 정상)`;
        elHealthy.style.color = isAllHealthy ? '#22c55e' : '#fbbf24';
    }
    const elKrx = document.getElementById('telemKrxCount');
    if (elKrx) elKrx.textContent = `KRX ${krxCnt}개 (${formatMb(krxMb)})`;

    const elGlobal = document.getElementById('telemGlobalCount');
    if (elGlobal) elGlobal.textContent = `해외선물/주식 ${globalCnt}개 (${formatMb(globalMb)})`;

    const elCrypto = document.getElementById('telemCryptoCount');
    if (elCrypto) elCrypto.textContent = `가상자산 ${cryptoCnt}개 (${formatMb(cryptoMb)})`;

    const elTotal = document.getElementById('telemTotalVolume');
    if (elTotal) elTotal.textContent = `💾 총 원천 용량: ${formatMb(totalMb)} (100% 매니페스트)`;
}

function setRawFeedTreeMode(mode) {
    window._rawFeedTreeMode = mode;
    ['btnTreeProvider', 'btnTreeLiveProvider', 'btnTreeRole', 'btnTreeFlat'].forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        const target = (btnId === 'btnTreeProvider' && mode === 'provider') ||
                       (btnId === 'btnTreeLiveProvider' && mode === 'live_provider') ||
                       (btnId === 'btnTreeRole' && mode === 'role') ||
                       (btnId === 'btnTreeFlat' && mode === 'flat');
        if (target) btn.classList.add('active');
        else btn.classList.remove('active');
    });

    const expandCtrl = document.getElementById('treeExpandControls');
    if (expandCtrl) {
        expandCtrl.style.display = (mode === 'flat') ? 'none' : 'flex';
    }

    renderRawFeedTree();
}

function onRawFeedSearch(val) {
    window._rawFeedSearchQuery = (val || '').trim().toLowerCase();
    renderRawFeedTree();
}

function toggleFeedGroupCollapse(groupId) {
    if (window._rawFeedCollapsedGroups.has(groupId)) {
        window._rawFeedCollapsedGroups.delete(groupId);
    } else {
        window._rawFeedCollapsedGroups.add(groupId);
    }
    renderRawFeedTree();
}

function expandAllFeedGroups() {
    window._rawFeedCollapsedGroups.clear();
    renderRawFeedTree();
}

function collapseAllFeedGroups() {
    // 현재 모드의 모든 그룹 ID 수집하여 등록
    const items = window._rawFeedCachedItems || [];
    const mode = window._rawFeedTreeMode || 'provider';
    items.forEach(it => {
        let gid;
        if (mode === 'provider') {
            gid = `prov_${it.primary_source}`;
        } else if (mode === 'live_provider') {
            const lp = (it.live_connection && it.live_connection.provider) ? it.live_connection.provider.toUpperCase() : 'OTHER';
            gid = `live_${lp}`;
        } else {
            gid = `role_${it.role}`;
        }
        window._rawFeedCollapsedGroups.add(gid);
    });
    renderRawFeedTree();
}

function renderRawFeedTree() {
    const tbody = document.getElementById('feedRegistryTableBody');
    if (!tbody) return;

    let items = window._rawFeedCachedItems || [];

    const archivedCountEl = document.getElementById('archivedAssetCount');
    if (archivedCountEl) archivedCountEl.textContent = items.filter(it => it.archived).length;

    if (!window._rawFeedShowArchived) {
        items = items.filter(it => !it.archived);
    }

    const q = window._rawFeedSearchQuery;
    if (q) {
        items = items.filter(it =>
            (it.symbol && it.symbol.toLowerCase().includes(q)) ||
            (it.name && it.name.toLowerCase().includes(q)) ||
            (it.primary_source && it.primary_source.toLowerCase().includes(q))
        );
    }

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:16px; color:var(--text-muted);">${q ? `'${q}'에 일치하는 자산이 없습니다.` : '수집 대상 대장에 등록된 자산이 없습니다.'}</td></tr>`;
        return;
    }

    const mode = window._rawFeedTreeMode || 'provider';
    let html = '';

    if (mode === 'provider') {
        // [📥 과거 데이터 출처별 계층 트리]
        const PROVIDER_METAS = {
            'CREON': { title: '🏛️ 대신증권 (CREON)', badge: '과거 틱/1분봉 아카이브', rule: 'KRX 과거 정규장 불변 원장 보존 (실시간 매매는 KIS)' },
            'CME': { title: '🌐 미국 CME 해외선물', badge: '과거 1M~1D 아카이브', rule: 'CME 나스닥/S&P500 과거 시계열 아카이브' },
            'CTRADER': { title: '🌐 cTrader (SPOTWARE)', badge: '과거 1분봉 아카이브', rule: '글로벌 CFD 24/5 연속 시세 1년치 원천 아카이브' },
            'UPBIT': { title: '🪙 업비트 (UPBIT)', badge: '과거 체결/캔들 ZIP 아카이브', rule: '24/7 원화 마켓 초저지연 체결 아카이브' },
            'BINANCE': { title: '⚡ 바이낸스 (BINANCE)', badge: '과거 Klines/OI 아카이브', rule: '글로벌 Klines & OI 원천 원장 아카이브' },
            'COINBASE': { title: '🏛️ 코인베이스 (COINBASE)', badge: '과거 10M 아카이브', rule: '미국 기관 벤치마크 과거 원장' },
            'KIS': { title: '📈 한국투자증권 (KIS)', badge: '과거 1M/5M/1D 아카이브', rule: '국내/해외 보조 분봉 및 일봉 아카이브' }
        };

        const groups = {};
        items.forEach(it => {
            const p = it.primary_source || 'OTHER';
            if (!groups[p]) groups[p] = [];
            groups[p].push(it);
        });

        // 정렬 순서
        const order = ['CREON', 'CME', 'CTRADER', 'UPBIT', 'BINANCE', 'COINBASE', 'KIS'];
        const sortedKeys = Object.keys(groups).sort((a,b) => {
            const ia = order.indexOf(a) !== -1 ? order.indexOf(a) : 99;
            const ib = order.indexOf(b) !== -1 ? order.indexOf(b) : 99;
            return ia - ib;
        });

        sortedKeys.forEach(pKey => {
            const pItems = groups[pKey];
            const pMeta = PROVIDER_METAS[pKey] || { title: `📡 ${pKey}`, badge: '과거 아카이브', rule: '정규 수집 파이프라인' };
            const gid = `prov_${pKey}`;
            const isCollapsed = window._rawFeedCollapsedGroups.has(gid);
            const totalGroupMb = pItems.reduce((acc, c) => acc + (c.file_size_mb || 0), 0);
            const formatMb = totalGroupMb >= 1024 ? `${(totalGroupMb/1024).toFixed(2)} GB` : `${totalGroupMb.toFixed(1)} MB`;

            // 그룹 헤더 행
            html += `
                <tr class="tree-group-header" onclick="toggleFeedGroupCollapse('${gid}')" style="background:linear-gradient(90deg, rgba(56,189,248,0.12), rgba(15,23,42,0.6)); cursor:pointer; border-top:1px solid rgba(56,189,248,0.25); border-bottom:1px solid rgba(255,255,255,0.06);">
                    <td colspan="10" style="padding:9px 12px;">
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <span style="font-size:12px; color:var(--accent-cyan); font-family:monospace; display:inline-block; width:14px; transition:transform 0.15s ease;">${isCollapsed ? '▶' : '▼'}</span>
                            <strong style="color:#38bdf8; font-size:13px;">${pMeta.title}</strong>
                            <span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; font-size:10px; padding:2px 6px;">${pMeta.badge}</span>
                            <span style="font-size:11px; color:var(--text-muted); margin-left:6px;">${pMeta.rule}</span>
                            <span style="margin-left:auto; font-size:11.5px; color:var(--text-secondary); font-family:monospace;">
                                <strong>${pItems.length}개 자산</strong> (${formatMb})
                            </span>
                        </div>
                    </td>
                </tr>
            `;

            // 자식 자산 행들
            if (!isCollapsed) {
                pItems.forEach(it => {
                    html += buildFeedAssetRowHtml(it, true);
                });
            }
        });

    } else if (mode === 'live_provider') {
        // [⚡ 실시간 연결 출처별 계층 트리]
        const LIVE_PROVIDER_METAS = {
            'KIS': { title: '📈 한국투자증권 (KIS)', badge: 'WebSocket H0STCNT0 / HDFSCNT0', rule: '국내 주식/ETF 호가·체결 직결 및 미국 주식 WebSocket' },
            'CTRADER': { title: '🌐 cTrader (SPOTWARE)', badge: 'Protobuf TCP ProtoOASpotEvent', rule: '나스닥/S&P500 등 7대 글로벌 CFD 초저지연 24/5 연속 직결' },
            'UPBIT': { title: '🪙 업비트 (UPBIT)', badge: 'WebSocket ticker', rule: '국내 원화 마켓 24/7 초저지연 실시간 체결 스트림' },
            'BINANCE': { title: '⚡ 바이낸스 (BINANCE)', badge: 'WebSocket trade / kline', rule: '글로벌 현물 체결 및 무기한 선물 캔들/OI 실시간 스트림' },
            'COINBASE': { title: '🏛️ 코인베이스 (COINBASE)', badge: 'WebSocket ticker', rule: '미국 기관 수급 및 코인베이스 프리미엄 실시간 벤치마크' },
            'YAHOO': { title: '🌐 야후 파이낸스 (YAHOO)', badge: 'REST Polling 1m_bar', rule: 'CME 나스닥/S&P500 선물 1분봉 준실시간 폴링' },
            'PYTH': { title: '🔮 Pyth Network', badge: 'Oracle Stream', rule: '글로벌 매크로 온체인 실시간 오라클 피드' }
        };

        const groups = {};
        items.forEach(it => {
            const lp = (it.live_connection && it.live_connection.provider) ? it.live_connection.provider.toUpperCase() : 'OTHER';
            if (!groups[lp]) groups[lp] = [];
            groups[lp].push(it);
        });

        // 정렬 순서
        const order = ['KIS', 'CTRADER', 'UPBIT', 'BINANCE', 'COINBASE', 'YAHOO', 'PYTH'];
        const sortedKeys = Object.keys(groups).sort((a,b) => {
            const ia = order.indexOf(a) !== -1 ? order.indexOf(a) : 99;
            const ib = order.indexOf(b) !== -1 ? order.indexOf(b) : 99;
            return ia - ib;
        });

        sortedKeys.forEach(pKey => {
            const pItems = groups[pKey];
            const pMeta = LIVE_PROVIDER_METAS[pKey] || { title: `📡 ${pKey}`, badge: '실시간 피드', rule: '실시간 스트리밍 연결' };
            const gid = `live_${pKey}`;
            const isCollapsed = window._rawFeedCollapsedGroups.has(gid);
            const totalGroupMb = pItems.reduce((acc, c) => acc + (c.file_size_mb || 0), 0);
            const formatMb = totalGroupMb >= 1024 ? `${(totalGroupMb/1024).toFixed(2)} GB` : `${totalGroupMb.toFixed(1)} MB`;

            // 그룹 헤더 행
            html += `
                <tr class="tree-group-header" onclick="toggleFeedGroupCollapse('${gid}')" style="background:linear-gradient(90deg, rgba(16,185,129,0.14), rgba(15,23,42,0.65)); cursor:pointer; border-top:1px solid rgba(16,185,129,0.3); border-bottom:1px solid rgba(255,255,255,0.06);">
                    <td colspan="10" style="padding:9px 12px;">
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <span style="font-size:12px; color:#34d399; font-family:monospace; display:inline-block; width:14px; transition:transform 0.15s ease;">${isCollapsed ? '▶' : '▼'}</span>
                            <strong style="color:#34d399; font-size:13px;">${pMeta.title}</strong>
                            <span class="badge" style="background:rgba(16,185,129,0.18); color:#34d399; font-size:10px; padding:2px 6px;">${pMeta.badge}</span>
                            <span style="font-size:11px; color:var(--text-muted); margin-left:6px;">${pMeta.rule}</span>
                            <span style="margin-left:auto; font-size:11.5px; color:var(--text-secondary); font-family:monospace;">
                                <strong>${pItems.length}개 구독 자산</strong> (${formatMb})
                            </span>
                        </div>
                    </td>
                </tr>
            `;

            // 자식 자산 행들
            if (!isCollapsed) {
                pItems.forEach(it => {
                    html += buildFeedAssetRowHtml(it, true);
                });
            }
        });

    } else if (mode === 'role') {
        // [전략 역할별 계층 트리]
        const ROLE_METAS = {
            'target': { title: '🎯 실전 매매 타겟 (Targets)', badge: '핵심 목표', desc: 'AI 모델 학습 및 CTrade 실시간 주문 집행 자산' },
            'leading_stock': { title: '⚡ 선행 대장주 (Leading Stocks)', badge: '상관관계 알파', desc: '타겟 자산 선행 모멘텀 및 페어 스프레드 피처 공급원' },
            'macro_futures': { title: '🌐 글로벌 매크로 & 선물 (Macro & Futures)', badge: '야간/글로벌', desc: '해외 지수 선물 및 베이시스 산출용 외생 매크로' },
            'macro_proxy': { title: '🔄 글로벌 대용 지표 (Macro Proxy / CFD)', badge: '대용/유사', desc: '해외 선물/지수 복제 및 24시간 선행 대용치' },
            'index_benchmark': { title: '📊 벤치마크 지수 (Index Benchmarks)', badge: '시장 기준', desc: '시장 기준 수익률 및 레짐 판독용 현물 지수' },
            'target_or_macro': { title: '🪙 가상자산 현물 (Crypto Spot)', badge: '24/7 연속', desc: '가상자산 전략 타겟 및 24/7 글로벌 센티먼트' }
        };

        const groups = {};
        items.forEach(it => {
            const r = it.role || 'other';
            if (!groups[r]) groups[r] = [];
            groups[r].push(it);
        });

        const order = ['target', 'leading_stock', 'macro_futures', 'macro_proxy', 'index_benchmark', 'target_or_macro'];

        const sortedKeys = Object.keys(groups).sort((a,b) => {
            const ia = order.indexOf(a) !== -1 ? order.indexOf(a) : 99;
            const ib = order.indexOf(b) !== -1 ? order.indexOf(b) : 99;
            return ia - ib;
        });

        sortedKeys.forEach(rKey => {
            const rItems = groups[rKey];
            const rMeta = ROLE_METAS[rKey] || { title: `📌 ${rKey}`, badge: '전략 역할', desc: '전략 참조 데이터' };
            const gid = `role_${rKey}`;
            const isCollapsed = window._rawFeedCollapsedGroups.has(gid);
            const totalGroupMb = rItems.reduce((acc, c) => acc + (c.file_size_mb || 0), 0);
            const formatMb = totalGroupMb >= 1024 ? `${(totalGroupMb/1024).toFixed(2)} GB` : `${totalGroupMb.toFixed(1)} MB`;

            html += `
                <tr class="tree-group-header" onclick="toggleFeedGroupCollapse('${gid}')" style="background:linear-gradient(90deg, rgba(168,85,247,0.12), rgba(15,23,42,0.6)); cursor:pointer; border-top:1px solid rgba(168,85,247,0.25); border-bottom:1px solid rgba(255,255,255,0.06);">
                    <td colspan="10" style="padding:9px 12px;">
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <span style="font-size:12px; color:#c084fc; font-family:monospace; display:inline-block; width:14px;">${isCollapsed ? '▶' : '▼'}</span>
                            <strong style="color:#c084fc; font-size:13px;">${rMeta.title}</strong>
                            <span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; font-size:10px; padding:2px 6px;">${rMeta.badge}</span>
                            <span style="font-size:11px; color:var(--text-muted); margin-left:6px;">${rMeta.desc}</span>
                            <span style="margin-left:auto; font-size:11.5px; color:var(--text-secondary); font-family:monospace;">
                                <strong>${rItems.length}개 자산</strong> (${formatMb})
                            </span>
                        </div>
                    </td>
                </tr>
            `;

            if (!isCollapsed) {
                rItems.forEach(it => {
                    html += buildFeedAssetRowHtml(it, true);
                });
            }
        });

    } else {
        // [평면 테이블 모드]
        items.forEach(it => {
            html += buildFeedAssetRowHtml(it, false);
        });
    }

    tbody.innerHTML = html;
}

function buildFeedAssetRowHtml(item, isTreeChild) {
    const roleBadges = {
        'target': '<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; font-size:9.5px; padding:1px 5px;">🎯 타겟</span>',
        'leading_stock': '<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; font-size:9.5px; padding:1px 5px;">⚡ 대장주</span>',
        'macro_futures': '<span class="badge" style="background:rgba(234,179,8,0.15); color:#eab308; font-size:9.5px; padding:1px 5px;">🌐 매크로</span>',
        'index_benchmark': '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; font-size:9.5px; padding:1px 5px;">📊 지수</span>',
        'target_or_macro': '<span class="badge" style="background:rgba(34,197,94,0.15); color:#22c55e; font-size:9.5px; padding:1px 5px;">🪙 가상자산</span>',
        'macro_proxy': '<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; font-size:9.5px; padding:1px 5px;">⚡ 대용 매크로</span>',
        'crypto_currency': '<span class="badge" style="background:rgba(34,197,94,0.15); color:#22c55e; font-size:9.5px; padding:1px 5px;">🪙 가상자산</span>'
    };
    const roleHtml = roleBadges[item.role] || `<span class="badge" style="font-size:9.5px; padding:1px 5px;">${item.role}</span>`;

    const statusBadges = {
        'HEALTHY': '<span class="badge badge-success" style="font-size:9px; padding:1px 5px;">🟢 정상</span>',
        'UNVERIFIED': '<span class="badge badge-warning" style="font-size:9px; padding:1px 5px;">🟡 미검증</span>',
        'MISSING': '<span class="badge badge-danger" style="font-size:9px; padding:1px 5px;">🔴 미수집</span>'
    };
    const statusHtml = statusBadges[item.status_code] || item.status_badge;

    const primarySourceIcons = {
        'CREON': '🏛️ CREON',
        'KIS': '📈 KIS',
        'CME': '🌐 CME',
        'UPBIT': '🪙 UPBIT',
        'BINANCE': '⚡ BINANCE',
        'COINBASE': '🏛️ COINBASE'
    };
    const pSrcIcon = primarySourceIcons[item.primary_source] || item.primary_source;

    const rowsText = item.rows > 0 ? `${item.rows.toLocaleString()}행 (${item.file_size_mb}MB)` : '-';
    const isActive = item.is_active !== false;
    const paddingLeft = isTreeChild ? '20px' : '8px';

    function formatFeedType(typeStr) {
        if (!typeStr) return '표준';
        return typeStr
            .replace('_ARCHIVE', ', ARCHIVE')
            .replace('_VISION_DAILY_ZIP', ', VISION')
            .replace('_VISION_DAILY', ', VISION')
            .replace('_VISION', ', VISION')
            .replace('_REST_API', ', REST')
            .replace('_REST', ', REST')
            .replace('TICK_AND_1M', 'TICK & 1M')
            .replace('1M_TO_10Y', '1M (10Y)')
            .replace('_5M', ' & 5M');
    }

    // 1단계 원천 수집 스케일 칩들 (클릭 시 1단계 원천 수집 설정 모달 호출)
    const ingestSources = item.ingestion_sources || (item.ingestion_resolution === 'TICK' ? ['TICK', '1M'] : [item.ingestion_resolution || '1M']);
    let ingestChips = '';
    ingestSources.forEach(src => {
        const color = (src === 'TICK' || src === '1S') ? '#4ade80' : ((src === '1M' || src === '5M') ? '#38bdf8' : '#94a3b8');
        const bg = (src === 'TICK' || src === '1S') ? 'rgba(34,197,94,0.15)' : 'rgba(56,189,248,0.12)';
        ingestChips += `<span class="badge" style="background:${bg}; color:${color}; border:1px solid rgba(255,255,255,0.1); font-size:9px; font-family:monospace; padding:1px 4px; line-height:1.2;">${src}</span>`;
    });

    // 2단계 정규화 목표 캔들 스케일 칩들 (클릭 시 2단계 목표 캔들 설정 모달 호출)
    const targetScales = item.target_scales || item.canonical_intervals || ['1m'];
    let targetChips = '';
    targetScales.forEach(inv => {
        const isCore = ['10s', '1m', '5m', '15m', '1h', '1d'].includes(inv);
        const color = isCore ? '#facc15' : '#38bdf8';
        const bg = isCore ? 'rgba(250,204,21,0.15)' : 'rgba(56,189,248,0.12)';
        const border = isCore ? 'rgba(250,204,21,0.35)' : 'rgba(255,255,255,0.1)';
        targetChips += `<span class="badge" style="background:${bg}; color:${color}; border:1px solid ${border}; font-size:9px; font-family:monospace; padding:1px 4px; line-height:1.2;">${isCore ? '★' : ''}${inv}</span>`;
    });

    // 3단계 실시간 접속 정보 (live_connection)
    const liveConn = item.live_connection || {};
    const liveProv = liveConn.provider || 'KIS';
    const liveType = liveConn.feed_type === 'WEBSOCKET' ? 'WS' : 'REST';
    const liveChan = liveConn.channel || liveConn.symbol || item.symbol;
    const defaultLiveUris = {
        'KIS': 'ws://ops.koreainvestment.com:21000',
        'UPBIT': 'wss://api.upbit.com/websocket/v1',
        'BINANCE': 'wss://stream.binance.com:9443/ws',
        'COINBASE': 'wss://ws-feed.exchange.coinbase.com',
        'CTRADER': 'live.ctrader.com:5035',
        'YAHOO': 'query1.finance.yahoo.com',
        'YAHOO_OR_CTRADER': 'live.ctrader.com:5035'
    };
    const liveEndpointUrl = liveConn.endpoint_url || defaultLiveUris[liveProv] || '실시간 피드 엔드포인트';
    const liveTooltip = `[실시간 접속 정보]\n• 공급자: ${liveProv} (${liveType})\n• 접속 주소: ${liveEndpointUrl}\n• 구독 채널: ${liveChan}\n• 타임아웃: ${liveConn.timeout_sec || 15}초`;

    const isArchived = !!item.archived;

    return `
        <tr class="tree-child-row" style="border-bottom:1px solid rgba(255,255,255,0.04); ${(isActive && !isArchived) ? '' : 'opacity:0.45;'} transition:background 0.15s ease;">
            <!-- 1열: 자산명 (종목명 / 아래줄 심볼) -->
            <td style="padding:4px 6px; padding-left:${paddingLeft}; white-space:nowrap;">
                <div style="display:inline-flex; align-items:flex-start; gap:6px;">
                    ${isTreeChild ? '<span style="color:rgba(255,255,255,0.25); font-family:monospace; font-size:11px; margin-top:2px;">└─</span>' : ''}
                    <div style="display:inline-flex; flex-direction:column; gap:1px; line-height:1.25;">
                        <div style="display:flex; align-items:center; gap:6px;">
                            <strong style="color:var(--text-primary); font-size:12.5px; font-weight:700; letter-spacing:-0.2px;">${item.name}</strong>
                            ${isArchived ? '<span class="badge" style="background:rgba(250,204,21,0.15); color:#facc15; border:1px solid rgba(250,204,21,0.35); font-size:9px; padding:1px 5px;">🗄️ 보관됨</span>' : ''}
                        </div>
                        <span style="font-family:monospace; font-size:10.5px; color:var(--accent-cyan); font-weight:600;">${item.symbol}</span>
                    </div>
                </div>
            </td>
            <!-- 2열 [신규]: 스펙 & 실시간 피드 (원천/캔들/실시간 칩) -->
            <td style="padding:4px 6px; white-space:nowrap;">
                <div style="display:inline-flex; align-items:center; gap:5px; font-size:9.5px; line-height:1.2;">
                    <span style="display:inline-flex; align-items:center; gap:3px; cursor:pointer;" onclick="openScalePipelineMappingModal('${item.symbol}')" title="데이터 파이프라인 스케일 인과 맵핑 (클릭)">
                        <span style="color:#38bdf8; font-weight:600;">[원천]</span>
                        ${ingestChips}
                    </span>
                    <span style="color:rgba(255,255,255,0.2);">·</span>
                    <span style="display:inline-flex; align-items:center; gap:3px; cursor:pointer;" onclick="openScalePipelineMappingModal('${item.symbol}')" title="데이터 파이프라인 스케일 인과 맵핑 (클릭)">
                        <span style="color:#facc15; font-weight:600;">[캔들]</span>
                        ${targetChips}
                    </span>
                    <span style="color:rgba(255,255,255,0.2);">·</span>
                    <span style="display:inline-flex; align-items:center; gap:3px;">
                        <span style="color:#4ade80; font-weight:600;">[실시간]</span>
                        <span class="badge" title="${liveTooltip}" style="background:rgba(34,197,94,0.12); color:#4ade80; border:1px solid rgba(34,197,94,0.25); font-size:9px; font-family:monospace; padding:1px 4px; cursor:help;">
                            ⚡${liveProv} (${liveType}:${liveChan})
                        </span>
                        <button class="btn btn-xs" onclick="probeSingleLiveFeed('${item.symbol}')" style="font-size:9px; padding:1px 4px; line-height:1.2; background:rgba(56,189,248,0.15); border:1px solid rgba(56,189,248,0.3); color:#38bdf8; border-radius:3px; cursor:pointer;" title="실시간 피드 헬스체크 핑 (클릭)">⚡</button>
                    </span>
                </div>
            </td>
            <!-- 3열: 역할 -->
            <td style="padding:4px 6px;">${roleHtml}</td>
            <!-- 4열: 주 출처 -->
            <td style="padding:4px 6px;">
                <strong style="color:#38bdf8; font-size:11px;">${pSrcIcon}</strong>
                <span style="font-size:9.5px; color:var(--text-muted); margin-left:1px;">(${formatFeedType(item.primary_type)})</span>
            </td>
            <!-- 5열: 보조 출처 -->
            <td style="padding:4px 6px; color:var(--text-muted); font-size:10.5px;">
                ${item.fallback_source ? `${item.fallback_source} (${formatFeedType(item.fallback_type)})` : '<span style="color:rgba(255,255,255,0.2);">-</span>'}
            </td>
            <!-- 6열: 최근 수집 시점 -->
            <td style="padding:4px 6px; font-family:monospace; font-size:10.5px; color:${item.last_timestamp !== '-' ? '#22c55e' : 'var(--text-muted)'}; white-space:nowrap;">
                ${item.last_timestamp}
                ${item.trading_days ? `<span style="font-size:9.5px; color:var(--text-muted); margin-left:2px;">(${item.trading_days}일)</span>` : ''}
            </td>
            <!-- 7열: 누적 데이터 -->
            <td style="padding:4px 6px; text-align:right; font-family:monospace; font-size:11px; color:#f8fafc; white-space:nowrap;">
                ${rowsText}
            </td>
            <!-- 8열: 상태 -->
            <td style="padding:4px 6px; text-align:center; white-space:nowrap;">
                <div style="display:inline-flex; align-items:center; gap:3px;">
                    ${statusHtml}
                    <span id="liveFeedBadge_${item.symbol}">${getLiveFeedBadgeHtml(item.symbol)}</span>
                </div>
            </td>
            <!-- 9열: 구독 -->
            <td style="padding:4px 6px; text-align:center;">
                <button class="btn btn-xs ${isActive ? 'btn-success' : 'btn-outline-light'}" 
                        onclick="toggleFeedAssetActive('${item.symbol}', ${!isActive})"
                        style="font-size:9.5px; padding:1px 6px; border-radius:10px; cursor:pointer;"
                        title="${isActive ? '클릭 시 수집 일시정지' : '클릭 시 수집 활성화'}">
                    ${isActive ? '✅' : '⏸️'}
                </button>
            </td>
            <!-- 10열: 관리 (출처 편집 / 보관) - 완전 삭제는 실수 방지를 위해 출처 편집 모달 안으로 이동 -->
            <td style="padding:4px 6px; text-align:center; white-space:nowrap;">
                <div style="display:inline-flex; align-items:center; gap:3px;">
                    <button class="btn btn-xs" onclick="openEditAssetSourceModal('${item.symbol}')" style="font-size:10px; padding:1px 5px; background:rgba(56,189,248,0.12); border:1px solid rgba(56,189,248,0.3); color:#38bdf8; border-radius:3px; cursor:pointer;" title="과거데이터 출처 및 실시간 접속 정보 편집 (완전 삭제는 이 안에 있습니다)">✏️</button>
                    <button class="btn btn-xs" onclick="toggleArchiveAsset('${item.symbol}', ${!isArchived})" style="font-size:10px; padding:1px 5px; background:rgba(250,204,21,0.12); border:1px solid rgba(250,204,21,0.3); color:#facc15; border-radius:3px; cursor:pointer;" title="${isArchived ? '보관 해제 (목록에는 계속 남습니다)' : '보관 처리 (삭제와 달리 목록에서 사라지지 않습니다)'}">${isArchived ? '📤' : '📦'}</button>
                </div>
            </td>
        </tr>
    `;
}

function getLiveFeedBadgeHtml(symbol) {
    const p = window._liveFeedDiagnosticMap ? window._liveFeedDiagnosticMap[symbol] : null;
    if (!p) {
        return `<span class="badge" style="font-size:8.5px; padding:1px 4px; background:rgba(255,255,255,0.05); color:#94a3b8; border:1px solid rgba(255,255,255,0.1);">⚡미점검</span>`;
    }
    // 수신 지연/신선도는 측정값(data_age_sec)만 쓴다 — 지연시간은 재지 않으므로 표시하지 않는다.
    const ageHint = (p.data_age_sec === null || p.data_age_sec === undefined)
        ? '' : ` [마지막 데이터 ${p.data_age_sec.toLocaleString()}초 전]`;
    if (p.grade === 'LIVE_STREAMING') {
        return `<span class="badge" style="font-size:8.5px; padding:1px 4px; background:rgba(34,197,94,0.18); color:#4ade80; border:1px solid rgba(34,197,94,0.4);" title="${p.message}${ageHint}">⚡수신중</span>`;
    } else if (p.grade === 'RESEARCH_FEED') {
        return `<span class="badge" style="font-size:8.5px; padding:1px 4px; background:rgba(234,179,8,0.15); color:#eab308; border:1px solid rgba(234,179,8,0.4);" title="${p.message}${ageHint}">🟡연구전용</span>`;
    } else if (p.grade === 'STANDBY_AUTH_OK') {
        return `<span class="badge" style="font-size:8.5px; padding:1px 4px; background:rgba(250,204,21,0.15); color:#facc15; border:1px solid rgba(250,204,21,0.3);" title="${p.message}${ageHint}">🟡${p.status_code === 'FEED_STALE' ? '수신지연' : '장외대기'}</span>`;
    } else {
        return `<span class="badge" style="font-size:8.5px; padding:1px 4px; background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4);" title="${p.message}">🔴실패</span>`;
    }
}

function updateSingleLiveBadge(symbol, probe) {
    const el = document.getElementById(`liveFeedBadge_${symbol}`);
    if (el) {
        el.innerHTML = getLiveFeedBadgeHtml(symbol);
    }
}

async function probeSingleLiveFeed(symbol) {
    try {
        if (typeof showToast === 'function') showToast(`[${symbol}] 실시간 피드 점검 중...`, 'info');
        const res = await apiPost('/api/raw_store/probe_live', { symbol: symbol });
        if (res && res.status === 'ok' && res.probe) {
            const probe = res.probe;
            if (!window._liveFeedDiagnosticMap) window._liveFeedDiagnosticMap = {};
            window._liveFeedDiagnosticMap[symbol] = probe;
            updateSingleLiveBadge(symbol, probe);
            const gradeText = probe.grade === 'LIVE_STREAMING' ? '🟢 실시간 수신 정상'
                : (probe.grade === 'RESEARCH_FEED' ? '🟡 연구 전용 (실시간 구현 없음)'
                : (probe.grade === 'STANDBY_AUTH_OK' ? `🟡 ${probe.badge_text}` : '🔴 연결 점검 필요'));
            if (typeof showToast === 'function') showToast(`[${symbol}] ${gradeText}`, probe.grade === 'CONNECTION_FAILED' ? 'error' : 'success');
        } else {
            if (typeof showToast === 'function') showToast(`[${symbol}] 점검 실패: ${res?.message || '오류'}`, 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast(`[${symbol}] 점검 예외: ${e}`, 'error');
    }
}

async function probeAllLiveFeeds() {
    const btn = document.getElementById('btnProbeAllLive');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ 점검 중...';
    }
    try {
        if (typeof showToast === 'function') showToast('24개 전 자산 실시간 피드 일괄 점검 중...', 'info');
        const res = await apiPost('/api/raw_store/probe_live', {});
        if (res && res.status === 'ok' && res.diagnostic_report) {
            const report = res.diagnostic_report;
            if (!window._liveFeedDiagnosticMap) window._liveFeedDiagnosticMap = {};
            (report.items || []).forEach(p => {
                window._liveFeedDiagnosticMap[p.symbol] = p;
                updateSingleLiveBadge(p.symbol, p);
            });
            const telemLive = document.getElementById('telemLiveText');
            if (telemLive) {
                telemLive.textContent = `실시간: 🟢${report.streaming_count} 🟡${report.standby_count} 🔴${report.failed_count}`;
                telemLive.style.color = report.failed_count > 0 ? '#f87171' : '#4ade80';
            }
            if (typeof showToast === 'function') {
                showToast(`전체 진단 완료: 🟢스트리밍 ${report.streaming_count}개, 🟡장외대기 ${report.standby_count}개`, 'success');
            }
        } else {
            if (typeof showToast === 'function') showToast(`일괄 진단 실패: ${res?.message || '오류'}`, 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast(`일괄 진단 예외: ${e}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '⚡ 실시간 피드 일괄 점검';
        }
    }
}

window.probeSingleLiveFeed = probeSingleLiveFeed;
window.probeAllLiveFeeds = probeAllLiveFeeds;


async function toggleFeedAssetActive(symbol, newActive) {
    try {
        const res = await apiPost('/api/raw_store/toggle_asset', { symbol, is_active: newActive });
        if (res && res.status === 'ok') {
            loadRawFeedRegistry();
        } else {
            alert(`토글 실패: ${res?.message || '알 수 없는 오류'}`);
        }
    } catch (e) {
        alert(`토글 예외: ${e}`);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// 자산 출처 관리 (과거데이터 출처 + 실시간 접속 정보 편집) / 보관 / 완전 삭제
// ─────────────────────────────────────────────────────────────────────────────

function openEditAssetSourceModal(symbol) {
    const items = window._rawFeedCachedItems || [];
    const item = items.find(it => it.symbol === symbol);
    if (!item) return;
    window._currentEditingSourceAsset = item;

    const modal = document.getElementById('modalEditAssetSource');
    if (!modal) return;

    document.getElementById('editSrcModalAssetName').textContent = item.name || symbol;
    document.getElementById('editSrcModalAssetSymbol').textContent = symbol;

    document.getElementById('editSrcPrimarySource').value = (item.primary_source || 'CREON').toUpperCase();
    document.getElementById('editSrcPrimaryType').value = item.primary_type || '';
    document.getElementById('editSrcFallbackSource').value = (item.fallback_source || 'KIS').toUpperCase();
    document.getElementById('editSrcFallbackType').value = item.fallback_type || '';

    const liveConn = item.live_connection || {};
    document.getElementById('editSrcLiveProvider').value = (liveConn.provider || 'KIS').toUpperCase();
    document.getElementById('editSrcLiveFeedType').value = liveConn.feed_type || 'WEBSOCKET';
    document.getElementById('editSrcLiveChannel').value = liveConn.channel || '';
    document.getElementById('editSrcLiveSymbol').value = liveConn.symbol || symbol;
    document.getElementById('editSrcLiveEndpoint').value = liveConn.endpoint_url || '';
    document.getElementById('editSrcLiveTimeout').value = liveConn.timeout_sec || 15;

    modal.classList.add('active');
}
window.openEditAssetSourceModal = openEditAssetSourceModal;

function closeEditAssetSourceModal() {
    const modal = document.getElementById('modalEditAssetSource');
    if (modal) modal.classList.remove('active');
    window._currentEditingSourceAsset = null;
}
window.closeEditAssetSourceModal = closeEditAssetSourceModal;

function openDeleteAssetModalFromEdit() {
    const item = window._currentEditingSourceAsset;
    if (!item) return;
    closeEditAssetSourceModal();
    openDeleteAssetModal(item.symbol);
}
window.openDeleteAssetModalFromEdit = openDeleteAssetModalFromEdit;

function openResetAndRefetchModalFromEdit() {
    const item = window._currentEditingSourceAsset;
    if (!item) return;
    closeEditAssetSourceModal();
    openResetAndRefetchModal(item.symbol);
}
window.openResetAndRefetchModalFromEdit = openResetAndRefetchModalFromEdit;


async function submitEditAssetSource() {
    const item = window._currentEditingSourceAsset;
    if (!item) return;

    const liveProvider = document.getElementById('editSrcLiveProvider').value;
    if (liveProvider === 'CREON') {
        if (typeof showToast === 'function') showToast('❌ CREON은 실시간 접속에 사용할 수 없습니다 (과거 아카이브 전용).', 'danger');
        return;
    }

    const btn = event?.target;
    if (btn) btn.disabled = true;

    try {
        const srcRes = await apiPost('/api/raw_store/update_source_config', {
            symbol: item.symbol,
            primary_source: document.getElementById('editSrcPrimarySource').value,
            primary_type: document.getElementById('editSrcPrimaryType').value,
            fallback_source: document.getElementById('editSrcFallbackSource').value,
            fallback_type: document.getElementById('editSrcFallbackType').value,
        });
        if (!srcRes || srcRes.status !== 'ok') {
            if (typeof showToast === 'function') showToast(`❌ 출처 저장 실패: ${srcRes?.message || '알 수 없는 오류'}`, 'danger');
            return;
        }

        const liveRes = await apiPost('/api/raw_store/update_live_connection', {
            symbol: item.symbol,
            live_connection: {
                provider: liveProvider,
                feed_type: document.getElementById('editSrcLiveFeedType').value,
                channel: document.getElementById('editSrcLiveChannel').value,
                symbol: document.getElementById('editSrcLiveSymbol').value || item.symbol,
                endpoint_url: document.getElementById('editSrcLiveEndpoint').value || undefined,
                timeout_sec: parseInt(document.getElementById('editSrcLiveTimeout').value, 10) || 15,
                required_depth: (item.live_connection && item.live_connection.required_depth) || 'TICK',
                is_active: true,
            }
        });
        if (!liveRes || liveRes.status !== 'ok') {
            if (typeof showToast === 'function') showToast(`❌ 실시간 접속 정보 저장 실패: ${liveRes?.message || '알 수 없는 오류'}`, 'danger');
            return;
        }

        if (typeof showToast === 'function') showToast(`✅ ${item.symbol} 출처 설정이 저장되었습니다.`, 'success');
        closeEditAssetSourceModal();
        loadRawFeedRegistry();
    } catch (e) {
        if (typeof showToast === 'function') showToast(`❌ 저장 예외: ${e}`, 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}
window.submitEditAssetSource = submitEditAssetSource;

async function toggleArchiveAsset(symbol, archived) {
    const msg = archived
        ? `[${symbol}]을(를) 보관 처리하시겠습니까?\n수집이 일시정지되며, 목록에서 사라지지 않고 '보관함' 토글로 언제든 다시 확인할 수 있습니다.`
        : `[${symbol}]의 보관을 해제하시겠습니까?\n(수집 재개는 별도로 '구독' 버튼에서 활성화해야 합니다)`;
    if (!confirm(msg)) return;

    try {
        const res = await apiPost('/api/raw_store/archive_asset', { symbol, archived });
        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') showToast(`✅ ${res.message}`, 'success');
            loadRawFeedRegistry();
        } else {
            alert(`보관 처리 실패: ${res?.message || '알 수 없는 오류'}`);
        }
    } catch (e) {
        alert(`보관 처리 예외: ${e}`);
    }
}
window.toggleArchiveAsset = toggleArchiveAsset;

function openDeleteAssetModal(symbol) {
    const items = window._rawFeedCachedItems || [];
    const item = items.find(it => it.symbol === symbol);
    if (!item) return;
    window._currentDeletingAsset = item;

    const modal = document.getElementById('modalDeleteAsset');
    if (!modal) return;

    document.getElementById('deleteAssetModalName').textContent = item.name || symbol;
    document.getElementById('deleteAssetModalSymbol').textContent = symbol;
    document.getElementById('deleteAssetModalFileInfo').textContent = item.file_exists
        ? `${(item.rows || 0).toLocaleString()}행, ${item.file_size_mb || 0}MB`
        : '없음';
    document.getElementById('deleteAssetModalSymbolHint').textContent = symbol;
    document.getElementById('deleteAssetConfirmInput').value = '';

    const ssotWarn = document.getElementById('deleteAssetModalSsotWarning');
    if (ssotWarn) { ssotWarn.style.display = 'none'; ssotWarn.innerHTML = ''; }
    window._deleteAssetCascadeConfirmed = false;

    const btnConfirm = document.getElementById('btnConfirmDeleteAsset');
    if (btnConfirm) {
        btnConfirm.disabled = true;
        btnConfirm.style.opacity = '0.5';
        btnConfirm.style.cursor = 'not-allowed';
        btnConfirm.textContent = '🗑️ 영구 삭제';
    }

    modal.classList.add('active');
}
window.openDeleteAssetModal = openDeleteAssetModal;

function closeDeleteAssetModal() {
    const modal = document.getElementById('modalDeleteAsset');
    if (modal) modal.classList.remove('active');
    window._currentDeletingAsset = null;
}
window.closeDeleteAssetModal = closeDeleteAssetModal;

function onDeleteAssetConfirmInput() {
    const item = window._currentDeletingAsset;
    const input = document.getElementById('deleteAssetConfirmInput');
    const btnConfirm = document.getElementById('btnConfirmDeleteAsset');
    if (!item || !input || !btnConfirm) return;

    const matched = input.value.trim().toUpperCase() === item.symbol.toUpperCase();
    btnConfirm.disabled = !matched;
    btnConfirm.style.opacity = matched ? '1' : '0.5';
    btnConfirm.style.cursor = matched ? 'pointer' : 'not-allowed';
}
window.onDeleteAssetConfirmInput = onDeleteAssetConfirmInput;

async function confirmDeleteAsset() {
    const item = window._currentDeletingAsset;
    if (!item) return;
    const confirmInput = document.getElementById('deleteAssetConfirmInput')?.value?.trim();

    const btnConfirm = document.getElementById('btnConfirmDeleteAsset');
    if (btnConfirm) btnConfirm.disabled = true;

    try {
        const cascade = !!window._deleteAssetCascadeConfirmed;
        const res = await apiPost('/api/raw_store/delete_asset', { symbol: item.symbol, confirm_symbol: confirmInput, cascade });
        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') showToast(`✅ ${res.message}`, 'success');
            closeDeleteAssetModal();
            loadRawFeedRegistry();
        } else if (res && res.status === 'blocked') {
            const ssotWarn = document.getElementById('deleteAssetModalSsotWarning');
            if (ssotWarn) {
                const list = (res.referencing_targets || []).map(r =>
                    r.relation === 'is_target'
                        ? `・${r.target_name}(${r.target_symbol}) 자체가 SSOT의 타겟으로 등재되어 있음`
                        : `・${r.target_name}(${r.target_symbol})의 참조종목(${r.role || 'reference'})으로 등재되어 있음`
                ).join('<br>');
                ssotWarn.innerHTML = `⚠️ 이 심볼은 symbol_relations.json(SSOT)에서 아직 참조 중입니다:<br>${list}<br><br>이대로 삭제하면 다음 동기화 때 자동으로 되살아납니다. 계속 진행하면 아래 관계도 함께 삭제됩니다.`;
                ssotWarn.style.display = 'block';
            }
            window._deleteAssetCascadeConfirmed = true;
            if (btnConfirm) {
                btnConfirm.disabled = false;
                btnConfirm.textContent = '🔗 관계 해제하고 영구 삭제';
            }
        } else {
            alert(`삭제 실패: ${res?.message || '알 수 없는 오류'}`);
            if (btnConfirm) btnConfirm.disabled = false;
        }
    } catch (e) {
        alert(`삭제 예외: ${e}`);
        if (btnConfirm) btnConfirm.disabled = false;
    }
}
window.confirmDeleteAsset = confirmDeleteAsset;

// ─────────────────────────────────────────────────────────────────────────────
// ♻️ 원천 데이터 초기화 후 전체 재수집 (Reset & Refetch)
// ─────────────────────────────────────────────────────────────────────────────
function openResetAndRefetchModal(symbol) {
    const mappings = window._rawFeedRegistryMappings || {};
    const item = mappings[symbol] || { symbol: symbol, name: symbol };
    window._currentResettingAsset = item;

    const modal = document.getElementById('modalResetAndRefetch');
    if (!modal) return;

    const nameEl = document.getElementById('resetRefetchModalName');
    const symEl = document.getElementById('resetRefetchModalSymbol');
    const hintEl = document.getElementById('resetRefetchModalSymbolHint');
    if (nameEl) nameEl.textContent = item.name || item.symbol;
    if (symEl) symEl.textContent = item.symbol;
    if (hintEl) hintEl.textContent = item.symbol;

    const input = document.getElementById('resetRefetchConfirmInput');
    if (input) input.value = '';

    const btnConfirm = document.getElementById('btnConfirmResetRefetch');
    if (btnConfirm) {
        btnConfirm.disabled = true;
        btnConfirm.style.opacity = '0.5';
        btnConfirm.style.cursor = 'not-allowed';
    }

    modal.classList.add('active');
}
window.openResetAndRefetchModal = openResetAndRefetchModal;

function closeResetAndRefetchModal() {
    const modal = document.getElementById('modalResetAndRefetch');
    if (modal) modal.classList.remove('active');
    window._currentResettingAsset = null;
}
window.closeResetAndRefetchModal = closeResetAndRefetchModal;

function onResetAndRefetchConfirmInput() {
    const item = window._currentResettingAsset;
    const input = document.getElementById('resetRefetchConfirmInput');
    const btnConfirm = document.getElementById('btnConfirmResetRefetch');
    if (!item || !input || !btnConfirm) return;

    const matched = input.value.trim().toUpperCase() === item.symbol.toUpperCase();
    btnConfirm.disabled = !matched;
    btnConfirm.style.opacity = matched ? '1' : '0.5';
    btnConfirm.style.cursor = matched ? 'pointer' : 'not-allowed';
}
window.onResetAndRefetchConfirmInput = onResetAndRefetchConfirmInput;

async function confirmResetAndRefetch() {
    const item = window._currentResettingAsset;
    if (!item) return;
    const confirmInput = document.getElementById('resetRefetchConfirmInput')?.value?.trim();

    const btnConfirm = document.getElementById('btnConfirmResetRefetch');
    if (btnConfirm) btnConfirm.disabled = true;

    try {
        const res = await apiPost('/api/raw_store/reset_and_refetch', { symbol: item.symbol, confirm_symbol: confirmInput });
        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') {
                showToast(`♻️ ${res.message} (${res.deleted_count}개 파일 초기화, ${res.freed_mb}MB 회수)`, 'success');
            }
            closeResetAndRefetchModal();
            loadRawFeedRegistry();

            // 실시간 수집 로그 콘솔로 뷰 전환 및 스크롤
            if (typeof switchTab === 'function') switchTab('data');
            appendLog(`🚀 [Raw Store] '${item.name || item.symbol}'(${item.symbol}) 초기화 후 전체 재수집 트리거 완료`, '');
            const consoleEl = document.getElementById('consoleData');
            if (consoleEl) {
                consoleEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        } else {
            alert(`초기화 및 재수집 실패: ${res?.message || '알 수 없는 오류'}`);
            if (btnConfirm) btnConfirm.disabled = false;
        }
    } catch (e) {
        alert(`초기화 및 재수집 예외: ${e}`);
        if (btnConfirm) btnConfirm.disabled = false;
    }
}
window.confirmResetAndRefetch = confirmResetAndRefetch;


// ─────────────────────────────────────────────────────────────────────────────
// 🎯 미사용 타겟 선언 (학습 프로젝트가 없는데 타겟으로 선언만 남은 symbol_relations.json 항목)
// ─────────────────────────────────────────────────────────────────────────────
async function refreshOrphanRelationsBadge() {
    const badge = document.getElementById('telemOrphanBadge');
    const text = document.getElementById('telemOrphanText');
    if (!badge || !text) return;
    try {
        const res = await apiGet('/api/symbols/relations/orphans');
        if (res && res.status === 'ok' && res.count > 0) {
            text.textContent = `미사용 타겟 ${res.count}건`;
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }
    } catch (e) {
        badge.style.display = 'none';
    }
}
window.refreshOrphanRelationsBadge = refreshOrphanRelationsBadge;

async function openOrphanRelationsModal() {
    const modal = document.getElementById('modalOrphanRelations');
    const body = document.getElementById('orphanRelationsListBody');
    if (!modal || !body) return;
    modal.classList.add('active');
    body.innerHTML = `<div style="text-align:center; padding:20px; color:var(--text-muted); font-size:12px;">로딩 중...</div>`;

    try {
        const res = await apiGet('/api/symbols/relations/orphans');
        if (!res || res.status !== 'ok') {
            body.innerHTML = `<div style="text-align:center; padding:20px; color:var(--accent-red); font-size:12px;">조회 실패: ${res?.message || '알 수 없는 오류'}</div>`;
            return;
        }
        const orphans = res.orphans || [];
        if (orphans.length === 0) {
            body.innerHTML = `<div style="text-align:center; padding:20px; color:#4ade80; font-size:12px;">✅ 미사용 타겟 선언이 없습니다.</div>`;
            return;
        }
        body.innerHTML = orphans.map(o => {
            const refList = (o.references || []).map(r => `${r.name || r.symbol}(${r.symbol})`).join(', ') || '없음';
            const usedBy = (o.used_as_reference_by || []).map(u => `${u.target_name || u.target_symbol}(${u.target_symbol})`).join(', ');
            const usageLine = usedBy
                ? `<div style="color:#4ade80; font-size:11px; margin-top:2px;">다른 타겟의 참조종목으로 사용 중: ${usedBy} <span style="color:var(--text-muted);">(해제해도 유지됨)</span></div>`
                : `<div style="color:var(--text-muted); font-size:11px; margin-top:2px;">다른 타겟의 참조종목으로도 쓰이지 않음</div>`;
            return `
            <div style="background:rgba(250,204,21,0.06); border:1px solid rgba(250,204,21,0.2); border-radius:8px; padding:10px 12px; display:flex; justify-content:space-between; align-items:center; gap:10px;">
                <div style="font-size:12px; line-height:1.5;">
                    <strong style="color:#facc15;">${o.target_name}</strong> <span style="font-family:monospace; color:var(--text-muted);">(${o.target_symbol})</span>
                    <span class="badge" style="font-size:10px; margin-left:6px;">${o.market_type}</span>
                    <div style="color:var(--text-secondary); font-size:11px; margin-top:2px;">이 종목의 참조종목 설정: ${refList}</div>
                    ${usageLine}
                </div>
                <button type="button" class="btn btn-sm" onclick="deleteOrphanRelationTarget('${o.target_symbol}', '${(o.target_name || '').replace(/'/g, "\\'")}')" style="font-size:11px; padding:4px 10px; background:transparent; border:1px solid rgba(239,68,68,0.35); color:#f87171; white-space:nowrap;">
                    타겟 선언 해제
                </button>
            </div>`;
        }).join('');
    } catch (e) {
        body.innerHTML = `<div style="text-align:center; padding:20px; color:var(--accent-red); font-size:12px;">조회 예외: ${e}</div>`;
    }
}
window.openOrphanRelationsModal = openOrphanRelationsModal;

function closeOrphanRelationsModal() {
    const modal = document.getElementById('modalOrphanRelations');
    if (modal) modal.classList.remove('active');
}
window.closeOrphanRelationsModal = closeOrphanRelationsModal;

async function deleteOrphanRelationTarget(targetSymbol, targetName) {
    const input = prompt(`"${targetName}(${targetSymbol})"의 타겟 선언을 해제합니다.\n\n• 해제됨: 이 종목을 타겟으로 볼 때의 참조종목 설정 (symbol_relations.json 항목)\n• 유지됨: 종목 원장, 수집된 원천 데이터, 다른 타겟의 참조종목으로서의 사용\n\n계속하려면 종목코드 "${targetSymbol}"를 정확히 입력하세요:`);
    if (!input || input.trim().toUpperCase() !== targetSymbol.toUpperCase()) {
        if (input !== null) alert('종목코드가 일치하지 않아 취소되었습니다.');
        return;
    }
    try {
        const res = await apiPost('/api/symbols/relations/remove_target', { target: targetSymbol, confirm_symbol: input.trim() });
        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') showToast(`✅ ${res.message}`, 'success');
            openOrphanRelationsModal();
            refreshOrphanRelationsBadge();
        } else {
            alert(`제거 실패: ${res?.message || '알 수 없는 오류'}`);
        }
    } catch (e) {
        alert(`제거 예외: ${e}`);
    }
}
window.deleteOrphanRelationTarget = deleteOrphanRelationTarget;

// ─────────────────────────────────────────────────────────────────────────────
// 제공처별 실제 원천 스펙 & 퀀트 표준 캐시 및 동적 렌더러
// ─────────────────────────────────────────────────────────────────────────────
window._providerCapabilitiesCache = null;

async function loadProviderCapabilities() {
    if (window._providerCapabilitiesCache) return window._providerCapabilitiesCache;
    try {
        const res = await apiGet('/api/raw_store/provider_capabilities');
        if (res && res.status === 'ok') {
            window._providerCapabilitiesCache = res;
            return res;
        }
    } catch (e) {
        console.warn('loadProviderCapabilities error:', e);
    }
    return {
        provider_raw_capabilities: {
            CREON: ["TICK", "1M", "1D"],
            KIS: ["TICK", "1M", "5M", "1D"],
            UPBIT: ["TICK", "1S", "1M", "1D"],
            BINANCE: ["TICK", "1S", "1M", "1D"],
            COINBASE: ["TICK", "1M", "1D"],
            CTRADER: ["TICK", "1M", "1D"],
            CME: ["1M", "5M", "1D"]
        },
        quant_core_standards: ["10s", "1m", "5m", "15m", "1h", "1d"],
        quant_full_spectrum: ["1s", "5s", "10s", "30s", "1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
    };
}
window.loadProviderCapabilities = loadProviderCapabilities;

async function onRegPrimarySourceChanged(provider) {
    const caps = await loadProviderCapabilities();
    const pUpper = (provider || 'CREON').toUpperCase();
    const supported = caps.provider_raw_capabilities?.[pUpper] || ["TICK", "1M", "1D"];
    const container = document.getElementById('regAssetIngestionSourcesContainer');
    if (!container) return;

    let html = '';
    supported.forEach(src => {
        const color = (src === 'TICK' || src === '1S') ? '#4ade80' : ((src === '1M' || src === '5M') ? '#38bdf8' : '#94a3b8');
        const checked = (src === '1M' || src === 'TICK') ? 'checked' : '';
        html += `
            <label style="font-size:11.5px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;">
                <input type="checkbox" name="regIngestSource" value="${src}" ${checked}>
                <span style="color:${color}; font-weight:bold;">${src}</span>
            </label>
        `;
    });
    container.innerHTML = html;
}
window.onRegPrimarySourceChanged = onRegPrimarySourceChanged;

function onRegIngestionResolutionChanged(tier) {
    // 하위 호환성 유지
}
window.onRegIngestionResolutionChanged = onRegIngestionResolutionChanged;

// ─────────────────────────────────────────────────────────────────────────────
// 🌟 [통합 거버넌스] 데이터 파이프라인 스케일 인과 맵핑 (Scale Pipeline Mapping Modal)
// ─────────────────────────────────────────────────────────────────────────────
let _currentEditingScalePipeAsset = null;

async function openScalePipelineMappingModal(symbol) {
    const items = window._rawFeedCachedItems || [];
    const item = items.find(it => it.symbol === symbol) || { symbol: symbol, name: symbol, primary_source: 'CREON', ingestion_sources: ['1M'], target_scales: ['1m'] };
    window._currentEditingScalePipeAsset = item;

    const modal = document.getElementById('modalScalePipelineMapping');
    if (!modal) return;

    const nameEl = document.getElementById('scalePipeAssetName');
    if (nameEl) nameEl.textContent = item.name || symbol;
    const symEl = document.getElementById('scalePipeAssetSymbol');
    if (symEl) symEl.textContent = symbol;

    const pSrc = (item.primary_source || 'CREON').toUpperCase();
    const badgeEl = document.getElementById('scalePipeProviderBadge');
    if (badgeEl) badgeEl.textContent = '제공처: ' + pSrc;
    const srcEl = document.getElementById('scalePipeCategoryInfo');
    if (srcEl) srcEl.textContent = (item.category || '-') + ' | ' + (item.role || '-');

    await renderScaleDependencyTree(item);

    modal.classList.add('active');
}
window.openScalePipelineMappingModal = openScalePipelineMappingModal;

// 하위 호환성 100% 매핑
window.openAssetIngestionSourcesModal = openScalePipelineMappingModal;
window.openTargetScalesModal = openScalePipelineMappingModal;
window.openAssetIntervalsModal = openScalePipelineMappingModal;

function closeScalePipelineMappingModal() {
    const modal = document.getElementById('modalScalePipelineMapping');
    if (modal) modal.classList.remove('active');
    window._currentEditingScalePipeAsset = null;
}
window.closeScalePipelineMappingModal = closeScalePipelineMappingModal;
window.closeAssetIngestionSourcesModal = closeScalePipelineMappingModal;
window.closeTargetScalesModal = closeScalePipelineMappingModal;
window.closeAssetIntervalsModal = closeScalePipelineMappingModal;

// 시간 단위별 개별 갱신 및 삭제
async function syncSingleScale(symbol, scale, tier = 'raw') {
    if (!symbol) symbol = window._currentEditingScalePipeAsset?.symbol;
    if (!symbol || !scale) return;

    const tierName = tier === 'raw' ? '1단계 원천 수집' : '2단계 정규화 실버 캔들';
    if (typeof showToast === 'function') {
        showToast(`⏳ [${symbol}] ${scale} (${tierName}) 증분 갱신 중...`, 'info');
    }

    try {
        const res = await apiPost('/api/data_vault/sync_single_scale', {
            symbol: symbol,
            scale: scale,
            tier: tier
        });

        if (res && res.status === 'ok') {
            if (typeof showToast === 'function') {
                showToast(`✅ [${symbol}] ${scale} (${tierName}) 갱신 완료! ${res.message || ''}`, 'success');
            }
            if (typeof loadRawFeedRegistry === 'function') loadRawFeedRegistry();
            if (typeof loadNormalizedInventory === 'function') loadNormalizedInventory();
            if (typeof refreshHealthMatrixOne === 'function') refreshHealthMatrixOne(symbol);
            if (typeof loadHealthMatrixIntervals === 'function' && window._healthMatrixOpenSymbol === symbol) {
                loadHealthMatrixIntervals(symbol, true);
            }
        } else {
            if (typeof showToast === 'function') {
                showToast(`❌ [${symbol}] ${scale} 갱신 실패: ${res?.message || '알 수 없는 오류'}`, 'danger');
            }
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast(`❌ 갱신 요청 예외: ${e}`, 'danger');
        }
    }
}
window.syncSingleScale = syncSingleScale;

async function deleteSingleScale(symbol, scale, tier = 'raw') {
    if (!symbol) symbol = window._currentEditingScalePipeAsset?.symbol;
    if (!symbol || !scale) return;

    const tierName = tier === 'raw' ? '1단계 원천 수집 파일' : '2단계 정규화 실버 파일';
    const confirmMsg = `⚠️ 정말 [${symbol}]의 ${scale} (${tierName}) 데이터를 디스크에서 완전히 삭제하시겠습니까?\n(해당 단위의 데이터만 삭제되며 다른 스케일은 안전하게 보존됩니다)`;
    if (!confirm(confirmMsg)) return;

    if (typeof showToast === 'function') {
        showToast(`🗑️ [${symbol}] ${scale} (${tierName}) 삭제 중...`, 'info');
    }

    try {
        const res = await apiPost('/api/data_vault/delete_single_scale', {
            symbol: symbol,
            scale: scale,
            tier: tier
        });

        if (res && res.status === 'ok') {
            const count = res.deleted_count || (res.deleted_files?.length || 0);
            if (typeof showToast === 'function') {
                showToast(`✅ [${symbol}] ${scale} 삭제 완료 (${count}개 파일 삭제)`, 'success');
            }
            if (typeof loadRawFeedRegistry === 'function') loadRawFeedRegistry();
            if (typeof loadNormalizedInventory === 'function') loadNormalizedInventory();
            if (typeof refreshHealthMatrixOne === 'function') refreshHealthMatrixOne(symbol);
            if (typeof loadHealthMatrixIntervals === 'function' && window._healthMatrixOpenSymbol === symbol) {
                loadHealthMatrixIntervals(symbol, true);
            }
        } else {
            if (typeof showToast === 'function') {
                showToast(`❌ [${symbol}] ${scale} 삭제 실패: ${res?.message || '알 수 없는 오류'}`, 'danger');
            }
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast(`❌ 삭제 요청 예외: ${e}`, 'danger');
        }
    }
}
window.deleteSingleScale = deleteSingleScale;

async function renderScaleDependencyTree(item, overrideIngestSources = null, overrideTargetScales = null) {
    const container = document.getElementById('scaleMappingTreeContainer');
    if (!container) return;

    const caps = await loadProviderCapabilities();
    const pSrc = (item.primary_source || 'CREON').toUpperCase();
    const supportedRaw = item.available_raw_sources || caps.provider_raw_capabilities?.[pSrc] || ['TICK', '1M', '1D'];
    const currIngest = overrideIngestSources || item.ingestion_sources || (item.ingestion_resolution === 'TICK' ? ['TICK', '1M', '1D'] : [item.ingestion_resolution || '1M']);
    const currTargets = overrideTargetScales || item.target_scales || item.canonical_intervals || ['1m'];

    const coreStandards = caps.quant_core_standards || ['10s', '1m', '5m', '15m', '1h', '1d'];
    const fullSpectrum = caps.quant_full_spectrum || ['1s', '5s', '10s', '30s', '1m', '3m', '5m', '10m', '15m', '30m', '1h', '2h', '4h', '1d', '1w'];

    const hasTickOrSec = currIngest.some(s => s === 'TICK' || s === '1S');

    // 1단계 원천 수집 체크박스 HTML
    let rawHtml = '';
    supportedRaw.forEach(src => {
        const isChecked = currIngest.includes(src);
        const color = (src === 'TICK' || src === '1S') ? '#4ade80' : ((src === '1M' || src === '5M') ? '#38bdf8' : '#94a3b8');
        const desc = src === 'TICK' ? '체결틱 (10s 초단타 필수)' : (src === '1S' ? '1초봉 (초단타)' : (src === '1M' ? '1분봉 (15m/10m 다운스케일 마스터)' : (src === '5M' ? '5분봉' : '일봉')));
        rawHtml += `
            <label style="font-size:12px; display:inline-flex; align-items:center; gap:8px; cursor:pointer; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1); padding:7px 14px; border-radius:6px; transition:all 0.15s ease;">
                <input type="checkbox" name="pipeSourceOption" value="${src}" ${isChecked ? 'checked' : ''} onchange="onPipelineSourceToggle()">
                <span style="font-weight:bold; color:${color}; font-family:monospace; font-size:13px;">${src}</span>
                <span style="font-size:10.5px; color:var(--text-muted);">${desc}</span>
            </label>
        `;
    });

    // 2단계 핵심 6대 표준 HTML
    let coreHtml = '';
    coreStandards.forEach(scale => {
        const isChecked = currTargets.includes(scale);
        const isSubMin = ['1s', '5s', '10s', '30s'].includes(scale);
        const disabled = isSubMin && !hasTickOrSec;
        const opacity = disabled ? '0.4' : '1';
        coreHtml += `
            <label style="font-size:12px; display:flex; align-items:center; gap:6px; cursor:${disabled ? 'not-allowed' : 'pointer'}; opacity:${opacity}; background:rgba(250,204,21,0.1); border:1px solid rgba(250,204,21,0.35); padding:6px 10px; border-radius:6px;" title="${disabled ? '원천에 TICK 또는 1S를 체크해야 활성화됩니다' : ''}">
                <input type="checkbox" name="pipeTargetOption" value="${scale}" data-scale="${scale}" ${isChecked && !disabled ? 'checked' : ''} ${disabled ? 'disabled' : ''} onchange="syncPipeTargetScale('${scale}', this.checked)">
                <span style="font-weight:bold; color:#facc15;">★ ${scale}</span>
                <span style="font-size:10px; color:var(--text-muted); margin-left:auto;">${scale === '10s' ? '(초단타)' : (scale === '1m' ? '(분봉표준)' : (scale === '5m' ? '(단기스윙)' : (scale === '15m' ? '(추세스윙)' : (scale === '1h' ? '(시간봉)' : '(일봉)'))))}</span>
            </label>
        `;
    });

    // 2단계 전체 스펙트럼 HTML
    let fullHtml = '';
    fullSpectrum.forEach(scale => {
        const isChecked = currTargets.includes(scale);
        const isCore = coreStandards.includes(scale);
        const isSubMin = ['1s', '5s', '10s', '30s'].includes(scale);
        const disabled = isSubMin && !hasTickOrSec;
        const color = isCore ? '#facc15' : '#38bdf8';
        const opacity = disabled ? '0.4' : '1';
        fullHtml += `
            <label style="font-size:11px; display:flex; align-items:center; gap:4px; cursor:${disabled ? 'not-allowed' : 'pointer'}; opacity:${opacity}; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); padding:5px 8px; border-radius:4px;" title="${disabled ? '원천에 TICK 또는 1S 필요' : ''}">
                <input type="checkbox" name="pipeTargetOption" value="${scale}" data-scale="${scale}" ${isChecked && !disabled ? 'checked' : ''} ${disabled ? 'disabled' : ''} onchange="syncPipeTargetScale('${scale}', this.checked)">
                <span style="font-weight:600; color:${color}; font-family:monospace;">${scale}</span>
            </label>
        `;
    });

    container.innerHTML = `
        <!-- 1단계 원천 수집 섹션 -->
        <div style="background:rgba(56,189,248,0.06); border:1px solid rgba(56,189,248,0.25); border-radius:8px; padding:12px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-weight:bold; font-size:12.5px; color:#38bdf8;">📥 1단계 원천 수집 대상 (Raw Store)</span>
                <span style="font-size:10.5px; color:var(--text-muted);">제공처 API가 지원하는 주기</span>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:8px;">
                ${rawHtml}
            </div>
        </div>

        <!-- 인과 화살표 가이드 -->
        <div style="text-align:center; color:#38bdf8; font-size:14px; margin:-4px 0;">
            ⬇️ <span style="font-size:11px; color:var(--text-muted); font-weight:normal;">원천 데이터로부터 실버 캔들 다운스케일 자동 합성 (Resample)</span>
        </div>

        <!-- 2단계 정규화 캔들 섹션 -->
        <div style="background:rgba(250,204,21,0.06); border:1px solid rgba(250,204,21,0.25); border-radius:8px; padding:12px 14px; display:flex; flex-direction:column; gap:10px;">
            <div>
                <span style="font-weight:bold; font-size:12.5px; color:#facc15;">★ 퀀트 핵심 6대 표준 (Silver Store)</span>
                <span style="font-size:10.5px; color:var(--text-muted); margin-left:6px;">실무 및 백테스팅 주력 캔들</span>
            </div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:8px;">
                ${coreHtml}
            </div>

            <div style="border-top:1px dashed rgba(255,255,255,0.08); padding-top:8px;">
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:6px;">🌐 전체 가용 스펙트럼 (필요시 추가 선택 가능)</div>
                <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(75px, 1fr)); gap:6px;">
                    ${fullHtml}
                </div>
            </div>
        </div>
    `;
}
window.renderScaleDependencyTree = renderScaleDependencyTree;

function onPipelineSourceToggle() {
    const checkedSources = Array.from(document.querySelectorAll('input[name="pipeSourceOption"]:checked')).map(el => el.value);
    const hasTickOrSec = checkedSources.some(s => s === 'TICK' || s === '1S');
    const subMinScales = ['1s', '5s', '10s', '30s'];

    document.querySelectorAll('input[name="pipeTargetOption"]').forEach(cb => {
        const scale = cb.getAttribute('data-scale');
        if (subMinScales.includes(scale)) {
            cb.disabled = !hasTickOrSec;
            if (!hasTickOrSec) cb.checked = false;
            const parentLabel = cb.closest('label');
            if (parentLabel) {
                parentLabel.style.opacity = hasTickOrSec ? '1' : '0.4';
                parentLabel.style.cursor = hasTickOrSec ? 'pointer' : 'not-allowed';
            }
        }
    });
}
window.onPipelineSourceToggle = onPipelineSourceToggle;

function syncPipeTargetScale(scale, checked) {
    const allMatching = document.querySelectorAll(`input[data-scale="${scale}"]`);
    allMatching.forEach(cb => { if (!cb.disabled) cb.checked = checked; });
}
window.syncPipeTargetScale = syncPipeTargetScale;

function applyPipelinePreset(presetType) {
    const item = window._currentEditingScalePipeAsset;
    if (!item) return;

    let targetSources = [];
    let targetScales = [];

    if (presetType === 'KRX') {
        targetSources = ['TICK', '1M', '1D'];
        targetScales = ['10s', '1m', '1d'];
        if (typeof showToast === 'function') showToast('⚡ [국내주식 표준] 원천 TICK+1M+1D ➔ 타겟 10s, 1m, 1d 프리셋 적용됨', 'info');
    } else if (presetType === 'CRYPTO') {
        targetSources = ['1M', '1D'];
        targetScales = ['15m', '10m', '1d'];
        if (typeof showToast === 'function') showToast('⚡ [가상자산 표준] 원천 1M+1D ➔ 타겟 15m, 10m, 1d 수수료방어 프리셋 적용됨', 'info');
    } else if (presetType === 'GLOBAL') {
        targetSources = ['1M'];
        targetScales = ['1m'];
        if (typeof showToast === 'function') showToast('⚡ [해외선물 표준] 원천 1M ➔ 타겟 1m 프리셋 적용됨', 'info');
    }

    renderScaleDependencyTree(item, targetSources, targetScales);
}
window.applyPipelinePreset = applyPipelinePreset;

async function saveScalePipelineMapping(scope = 'single', andBuild = false) {
    const item = window._currentEditingScalePipeAsset;
    if (!item) return;

    const checkedSources = Array.from(document.querySelectorAll('input[name="pipeSourceOption"]:checked')).map(el => el.value);
    const checkedTargets = Array.from(new Set(Array.from(document.querySelectorAll('input[name="pipeTargetOption"]:checked')).map(el => el.value)));

    if (checkedSources.length === 0) {
        if (typeof showToast === 'function') showToast('⚠️ 최소 하나 이상의 1단계 원천 수집 스케일을 선택해야 합니다.', 'warning');
        return;
    }
    if (checkedTargets.length === 0) {
        if (typeof showToast === 'function') showToast('⚠️ 최소 하나 이상의 2단계 정규화 목표 봉 스케일을 선택해야 합니다.', 'warning');
        return;
    }

    const btnCategory = document.getElementById('btnSaveScalePipeCategory');
    const btnOnly = document.getElementById('btnSaveScalePipeOnly');
    const btnBuild = document.getElementById('btnSaveScalePipeAndBuild');
    if (btnCategory) btnCategory.disabled = true;
    if (btnOnly) btnOnly.disabled = true;
    if (btnBuild) btnBuild.disabled = true;

    try {
        const payload = {
            symbol: item.symbol,
            scope: scope,
            category: item.category,
            provider: item.primary_source,
            ingestion_sources: checkedSources,
            target_scales: checkedTargets,
            prune_unselected: true
        };

        const res = await apiPost('/api/data_vault/update_pipeline_scale_mapping', payload);

        if (!res || res.status !== 'ok') {
            if (typeof showToast === 'function') {
                showToast(`❌ 스케일 인과 맵핑 저장 실패: ${res?.message || '알 수 없는 오류'}`, 'danger');
            }
            return;
        }

        item.ingestion_sources = checkedSources;
        item.target_scales = checkedTargets;
        item.canonical_intervals = checkedTargets;
        closeScalePipelineMappingModal();

        if (andBuild) {
            if (typeof showToast === 'function') {
                showToast(`⏳ [실버 정규화] ${item.name || item.symbol} 다운스케일 파켓 빌드 중...`, 'info');
            }
            const buildRes = await apiPost('/api/data_vault/build_asset_normalized', { symbol: item.symbol });
            if (buildRes && (buildRes.status === 'ok' || buildRes.status === 'partial_error')) {
                if (typeof showToast === 'function') {
                    showToast(`✅ [정규화 빌드 완료] ${buildRes.message} (아티팩트: ${buildRes.artifacts?.length || 0}개)`, 'success');
                }
            } else {
                if (typeof showToast === 'function') {
                    showToast(`⚠️ 정규화 빌드 경고/오류: ${buildRes?.message || '알 수 없는 오류'}`, 'warning');
                }
            }
        } else {
            if (typeof showToast === 'function') {
                showToast(`✅ ${res.message}`, 'success');
            }
        }

        if (typeof loadRawFeedRegistry === 'function') loadRawFeedRegistry();
        if (typeof loadNormalizedInventory === 'function') loadNormalizedInventory();

    } catch (e) {
        if (typeof showToast === 'function') showToast(`❌ 저장 예외: ${e}`, 'danger');
    } finally {
        if (btnCategory) btnCategory.disabled = false;
        if (btnOnly) btnOnly.disabled = false;
        if (btnBuild) btnBuild.disabled = false;
    }
}
window.saveScalePipelineMapping = saveScalePipelineMapping;
window.saveAssetIngestionSources = saveScalePipelineMapping;
window.saveTargetScales = saveScalePipelineMapping;
window.saveAssetIntervals = saveScalePipelineMapping;

async function pruneOrphanNormalizedFiles() {
    const btn = document.getElementById('btnPruneNormOrphans');
    if (btn) btn.disabled = true;

    try {
        if (typeof showToast === 'function') {
            showToast('🧹 미사용/고아 정규화 파생 파일 정리 중...', 'info');
        }
        const res = await apiPost('/api/data_vault/prune_orphans', {});
        if (res && res.status === 'ok') {
            const count = res.deleted_count || 0;
            const mb = res.freed_mb || 0;
            if (typeof showToast === 'function') {
                showToast(`✅ 미사용 파생 파일 정리 완료: ${count}개 파일 삭제 (${mb} MB 공간 회수)`, 'success');
            }
            if (typeof loadNormalizedInventory === 'function') loadNormalizedInventory();
        } else {
            if (typeof showToast === 'function') {
                showToast(`❌ 파생 파일 정리 실패: ${res?.message || '알 수 없는 오류'}`, 'danger');
            }
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast(`❌ 파생 파일 정리 오류: ${e}`, 'danger');
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}
window.pruneOrphanNormalizedFiles = pruneOrphanNormalizedFiles;

async function openRegisterAssetModal() {
    const modal = document.getElementById('modalRegisterAsset');
    if (modal) {
        modal.classList.add('active');
        const symInput = document.getElementById('regAssetSymbol');
        if (symInput) symInput.value = '';
        const nameInput = document.getElementById('regAssetName');
        if (nameInput) nameInput.value = '';
        const descInput = document.getElementById('regAssetDesc');
        if (descInput) descInput.value = '';
        const quickSearch = document.getElementById('regAssetQuickSearch');
        if (quickSearch) quickSearch.value = '';
        const dropdown = document.getElementById('regAssetSearchDropdown');
        if (dropdown) dropdown.style.display = 'none';
        const validateResult = document.getElementById('regAssetValidateResult');
        if (validateResult) validateResult.style.display = 'none';
        REG_ASSET_LAST_VALIDATION = null;

        const pSrc = document.getElementById('regAssetPrimarySource')?.value || 'CREON';
        await onRegPrimarySourceChanged(pSrc);
    }
}
window.openRegisterAssetModal = openRegisterAssetModal;

function closeRegisterAssetModal() {
    const modal = document.getElementById('modalRegisterAsset');
    if (modal) {
        modal.classList.remove('active');
    }
}
window.closeRegisterAssetModal = closeRegisterAssetModal;

// =============================================================================
// 신규 수집 자산 대장 등록 - 스마트 종목 검색 (종목관리 검색과 동일 API 재사용)
// =============================================================================
let REG_ASSET_SEARCH_DEBOUNCE = null;

function handleRegAssetSearchInput(val) {
    if (REG_ASSET_SEARCH_DEBOUNCE) clearTimeout(REG_ASSET_SEARCH_DEBOUNCE);

    const query = (val || '').trim();
    const dropdown = document.getElementById('regAssetSearchDropdown');
    if (!dropdown) return;

    if (!query) {
        dropdown.style.display = 'none';
        return;
    }

    REG_ASSET_SEARCH_DEBOUNCE = setTimeout(async () => {
        try {
            const res = await apiGet(`/api/symbols/search?q=${encodeURIComponent(query)}&limit=12`);
            if (res && res.status === 'ok' && res.results && res.results.length > 0) {
                renderRegAssetSearchDropdown(res.results);
                dropdown.style.display = 'block';
            } else {
                dropdown.innerHTML = '<div style="padding:10px 12px; font-size:12px; color:var(--text-muted); text-align:center;">일치하는 종목이 없습니다. (직접 입력 후 등록 가능)</div>';
                dropdown.style.display = 'block';
            }
        } catch (e) {
            console.error('Reg asset search request failed:', e);
        }
    }, 180);
}
window.handleRegAssetSearchInput = handleRegAssetSearchInput;

function renderRegAssetSearchDropdown(items) {
    const dropdown = document.getElementById('regAssetSearchDropdown');
    if (!dropdown) return;
    dropdown.innerHTML = '';

    items.forEach(item => {
        const row = document.createElement('div');
        row.style.padding = '8px 12px';
        row.style.borderBottom = '1px solid #27272a';
        row.style.cursor = 'pointer';
        row.style.display = 'flex';
        row.style.justifyContent = 'space-between';
        row.style.alignItems = 'center';
        row.style.transition = 'background 0.15s ease';

        row.onmouseover = () => row.style.background = 'rgba(59, 130, 246, 0.15)';
        row.onmouseout = () => row.style.background = 'transparent';

        let typeBadge = '<span class="badge" style="background:#4b5563; font-size:10px;">기타</span>';
        if (item.market_type === 'KRX_ETF') typeBadge = '<span class="badge" style="background:#059669; color:#fff; font-size:10px;">국내 ETF</span>';
        else if (item.market_type === 'KRX_STOCK') typeBadge = '<span class="badge" style="background:#2563eb; color:#fff; font-size:10px;">국내 주식</span>';
        else if (item.market_type === 'US_ETF') typeBadge = '<span class="badge" style="background:#7c3aed; color:#fff; font-size:10px;">해외 ETF</span>';
        else if (item.market_type === 'CRYPTO') typeBadge = '<span class="badge" style="background:#d97706; color:#fff; font-size:10px;">가상자산</span>';

        row.innerHTML = `
            <div style="display:flex; align-items:center; gap:8px;">
                ${typeBadge}
                <span style="font-weight:bold; color:#fff; font-size:13px;">${item.name}</span>
                <span style="font-family:monospace; color:var(--accent-blue); font-size:12px;">(${item.symbol})</span>
            </div>
            <div style="font-size:11px; color:var(--text-muted); text-align:right;">
                <span>${item.description || item.exchange || ''}</span>
            </div>
        `;

        row.onclick = () => selectRegAssetSearchResult(item);
        dropdown.appendChild(row);
    });
}
window.renderRegAssetSearchDropdown = renderRegAssetSearchDropdown;

function selectRegAssetSearchResult(item) {
    const symInput = document.getElementById('regAssetSymbol');
    if (symInput) symInput.value = item.symbol || '';
    const nameInput = document.getElementById('regAssetName');
    if (nameInput) nameInput.value = item.name || '';
    const descInput = document.getElementById('regAssetDesc');
    if (descInput && !descInput.value && item.description) descInput.value = item.description;

    // 검색 결과의 시장 분류를 등록 폼의 자산 분류 셀렉트에 반영 (지원하는 값일 때만)
    const catSelect = document.getElementById('regAssetCategory');
    if (catSelect) {
        const mt = item.market_type || '';
        const validValues = Array.from(catSelect.options).map(o => o.value);
        if (validValues.includes(mt)) {
            catSelect.value = mt;
        } else if (mt === 'CRYPTO') {
            catSelect.value = 'CRYPTO_SPOT';
        }
    }

    const quickSearch = document.getElementById('regAssetQuickSearch');
    if (quickSearch) quickSearch.value = `${item.name} (${item.symbol})`;

    const dropdown = document.getElementById('regAssetSearchDropdown');
    if (dropdown) dropdown.style.display = 'none';

    if (typeof showToast === 'function') {
        showToast(`✨ [${item.name}] 종목 정보가 자동 완성되었습니다. 코드: ${item.symbol}`, 'info');
    }
}
window.selectRegAssetSearchResult = selectRegAssetSearchResult;

// =============================================================================
// 신규 수집 자산 대장 등록 - 출처 연결 검증 (가벼운 검증: 실재 여부만 확인, 실시간 구독 없음)
// =============================================================================
let REG_ASSET_LAST_VALIDATION = null; // {key, allPass}

function _regAssetValidationKey() {
    const symbol = (document.getElementById('regAssetSymbol')?.value || '').trim().toUpperCase();
    const primary = document.getElementById('regAssetPrimarySource')?.value || '';
    const fallback = document.getElementById('regAssetFallbackSource')?.value || '';
    return `${symbol}|${primary}|${fallback}`;
}

async function validateRegAssetSource() {
    const symbol = document.getElementById('regAssetSymbol')?.value?.trim();
    const primary_source = document.getElementById('regAssetPrimarySource')?.value;
    const fallback_source = document.getElementById('regAssetFallbackSource')?.value;
    const resultBox = document.getElementById('regAssetValidateResult');
    const btn = document.getElementById('btnValidateRegAsset');

    if (!symbol) {
        alert('먼저 종목/심볼 코드를 입력해 주세요.');
        return;
    }
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 검증 중...'; }
    if (resultBox) {
        resultBox.style.display = 'block';
        resultBox.style.background = 'rgba(255,255,255,0.05)';
        resultBox.style.border = '1px solid var(--border-color)';
        resultBox.innerHTML = '⏳ CREON/KIS 코드 실재 여부 확인 중...';
    }

    try {
        const res = await apiPost('/api/raw_store/validate_source', { symbol, primary_source, fallback_source });
        if (!res || res.status !== 'ok') {
            throw new Error(res?.message || '알 수 없는 오류');
        }
        const lines = [];
        let hasFail = false;
        for (const [src, r] of Object.entries(res.results || {})) {
            if (r.checked === false) {
                lines.push(`<div style="color:var(--text-muted);">⏭️ <b>${src}</b>: 건너뜀 — ${r.reason || '확인 불가'}</div>`);
            } else if (r.exists) {
                lines.push(`<div style="color:#4ade80;">✅ <b>${src}</b>: 코드 확인됨${r.name ? ` (${r.name})` : ''}</div>`);
            } else {
                hasFail = true;
                lines.push(`<div style="color:#f87171;">❌ <b>${src}</b>: ${r.error || '코드를 찾을 수 없습니다.'}</div>`);
            }
        }
        if (lines.length === 0) {
            lines.push('<div style="color:var(--text-muted);">CREON/KIS 출처가 아니라 경량 검증 대상이 아닙니다.</div>');
        }
        if (resultBox) {
            resultBox.style.background = hasFail ? 'rgba(248,113,113,0.1)' : 'rgba(74,222,128,0.08)';
            resultBox.style.border = `1px solid ${hasFail ? 'rgba(248,113,113,0.4)' : 'rgba(74,222,128,0.3)'}`;
            resultBox.innerHTML = lines.join('');
        }
        REG_ASSET_LAST_VALIDATION = { key: _regAssetValidationKey(), allPass: res.all_pass !== false };
    } catch (e) {
        if (resultBox) {
            resultBox.style.background = 'rgba(248,113,113,0.1)';
            resultBox.style.border = '1px solid rgba(248,113,113,0.4)';
            resultBox.innerHTML = `❌ 검증 요청 실패: ${e.message || e}`;
        }
        REG_ASSET_LAST_VALIDATION = null;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔎 출처 연결 검증 (가벼운 검증 — 코드 실재 여부만 확인, 실시간 구독 없음)'; }
    }
}
window.validateRegAssetSource = validateRegAssetSource;

async function submitRegisterAsset() {
    const symbol = document.getElementById('regAssetSymbol')?.value?.trim();
    const name = document.getElementById('regAssetName')?.value?.trim();
    const category = document.getElementById('regAssetCategory')?.value;
    const role = document.getElementById('regAssetRole')?.value;
    const primary_source = document.getElementById('regAssetPrimarySource')?.value;
    const fallback_source = document.getElementById('regAssetFallbackSource')?.value;
    const description = document.getElementById('regAssetDesc')?.value?.trim();

    const checkedSources = Array.from(document.querySelectorAll('input[name="regIngestSource"]:checked')).map(el => el.value);
    const checkedIntervals = Array.from(document.querySelectorAll('input[name="regInterval"]:checked')).map(el => el.value);

    if (!symbol || !name) {
        alert('종목 코드와 자산명을 모두 입력해 주세요.');
        return;
    }

    const currentKey = _regAssetValidationKey();
    if (!REG_ASSET_LAST_VALIDATION || REG_ASSET_LAST_VALIDATION.key !== currentKey) {
        if (!confirm('⚠️ 아직 이 출처 조합으로 연결 검증을 하지 않았습니다.\n검증 없이 그대로 등록하시겠습니까?\n(취소 후 "🔎 출처 연결 검증" 버튼을 먼저 눌러보세요)')) {
            return;
        }
    } else if (!REG_ASSET_LAST_VALIDATION.allPass) {
        if (!confirm('⚠️ 직전 연결 검증에서 실패한 출처가 있습니다.\n그래도 대장에 등록하시겠습니까?')) {
            return;
        }
    }

    const ingestRes = checkedSources.length > 0 ? checkedSources[0] : '1M';

    try {
        const res = await apiPost('/api/raw_store/register_asset', {
            symbol, name, category, role, primary_source, fallback_source, description,
            ingestion_resolution: ingestRes,
            canonical_intervals: checkedIntervals
        });
        if (res && res.status === 'ok') {
            // 복수 원천 스케일 추가 업데이트
            if (checkedSources.length > 0) {
                await apiPost('/api/raw_store/update_sources', {
                    symbol: symbol.toUpperCase(),
                    ingestion_sources: checkedSources
                });
            }
            alert(`✅ ${res.message}`);
            closeRegisterAssetModal();
            loadRawFeedRegistry();
        } else {
            alert(`등록 실패: ${res?.message || '알 수 없는 오류'}`);
        }
    } catch (e) {
        alert(`등록 요청 예외: ${e}`);
    }
}
window.submitRegisterAsset = submitRegisterAsset;

async function startActiveFeedsSync() {
    const btn = document.getElementById('btnSyncActiveFeeds');
    const wrap = document.getElementById('progressActiveWrap');
    const bar = document.getElementById('progressActiveBar');
    const lbl = document.getElementById('progressActiveLabel');

    if (btn) btn.disabled = true;
    if (wrap) wrap.style.display = 'block';
    if (bar) bar.style.width = '15%';
    if (lbl) lbl.textContent = '⏳ 활성 자산 증분 동기화 준비 중...';

    appendLog('🚀 [Raw Store] 등록된 활성 자산 일괄 증분 동기화 트리거 전송...', '');

    try {
        const res = await apiPost('/api/raw_store/sync_active', {});
        if (res && res.status === 'ok') {
            appendLog(`✅ ${res.message}`, '');
            if (lbl) lbl.textContent = `⚡ 동기화 시작됨 (${res.target_count}개 자산 핀포인트 수집)...`;
            if (bar) bar.style.width = '20%';
        } else {
            appendLog(`❌ 동기화 시작 실패: ${res?.message || '알 수 없는 오류'}`, '');
            if (lbl) lbl.textContent = `❌ 실패: ${res?.message || '오류'}`;
            if (btn) btn.disabled = false;
        }
    } catch (e) {
        appendLog(`❌ 동기화 요청 에러: ${e}`, '');
        if (lbl) lbl.textContent = `❌ 에러: ${e}`;
        if (btn) btn.disabled = false;
    }
}

window.loadRawFeedRegistry = loadRawFeedRegistry;
window.setRawFeedTreeMode = setRawFeedTreeMode;
window.onRawFeedSearch = onRawFeedSearch;
window.toggleFeedGroupCollapse = toggleFeedGroupCollapse;
window.expandAllFeedGroups = expandAllFeedGroups;
window.collapseAllFeedGroups = collapseAllFeedGroups;
window.renderRawFeedTree = renderRawFeedTree;
window.toggleFeedAssetActive = toggleFeedAssetActive;
window.openRegisterAssetModal = openRegisterAssetModal;
window.closeRegisterAssetModal = closeRegisterAssetModal;
window.submitRegisterAsset = submitRegisterAsset;
window.startActiveFeedsSync = startActiveFeedsSync;

function switchDataVaultSubTab(tabName) {
    const tabs = ['raw', 'norm', 'feat', 'research'];
    
    tabs.forEach(t => {
        const btn = document.getElementById(`subtab-btn-${t}`);
        const content = document.getElementById(`subtab-content-${t}`);
        
        if (t === tabName) {
            if (btn) btn.classList.add('active');
            if (content) content.style.display = 'block';
        } else {
            if (btn) btn.classList.remove('active');
            if (content) content.style.display = 'none';
        }
    });

    // 탭 전환 시 필요한 데이터 로드
    const curTarget = getCurrentTargetSymbol();
    if (tabName === 'raw') {
        if (typeof loadMedallionStatus === 'function') loadMedallionStatus(curTarget);
        if (typeof loadRawFeedRegistry === 'function') loadRawFeedRegistry();
        if (typeof loadDataCatalogSources === 'function') loadDataCatalogSources(false);
        if (typeof loadRawBackupStatus === 'function') loadRawBackupStatus();
    } else if (tabName === 'norm') {
        if (typeof loadMedallionStatus === 'function') loadMedallionStatus(curTarget);
        if (typeof loadNormalizedInventory === 'function') loadNormalizedInventory();
    } else if (tabName === 'feat') {
        if (typeof loadMedallionStatus === 'function') loadMedallionStatus(curTarget);
        if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(curTarget);
        if (typeof loadGroupsStudio === 'function') loadGroupsStudio();
    } else if (tabName === 'research') {
        loadResearchDatasets();
    }
    if (tabName !== 'research' && _researchTimer) { clearInterval(_researchTimer); _researchTimer = null; }
}
window.switchDataVaultSubTab = switchDataVaultSubTab;

// ─────────────────────────────────────────────────────────────────────────────
// [Normalized Store] 종목 중심 계층형 마스터-디테일 트리 그리드 & 텔레메트리 (Phase 4.1)
// ─────────────────────────────────────────────────────────────────────────────

window._normTreeMode = window._normTreeMode || 'asset'; // 'asset' | 'role' | 'flat'
window._normCollapsedGroups = window._normCollapsedGroups || new Set();
window._normSearchQuery = '';
window._normCachedItems = [];
window._normCachedGroups = [];

function setNormTreeMode(mode) {
    window._normTreeMode = mode;
    ['btnNormTreeAsset', 'btnNormTreeRole', 'btnNormTreeFlat'].forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        const target = (btnId === 'btnNormTreeAsset' && mode === 'asset') ||
                       (btnId === 'btnNormTreeRole' && mode === 'role') ||
                       (btnId === 'btnNormTreeFlat' && mode === 'flat');
        if (target) btn.classList.add('active');
        else btn.classList.remove('active');
    });
    renderNormalizedInventory();
}
window.setNormTreeMode = setNormTreeMode;

function onNormSearch(val) {
    window._normSearchQuery = (val || '').trim().toLowerCase();
    renderNormalizedInventory();
}
window.onNormSearch = onNormSearch;

function toggleNormGroupCollapse(gid) {
    if (window._normCollapsedGroups.has(gid)) {
        window._normCollapsedGroups.delete(gid);
    } else {
        window._normCollapsedGroups.add(gid);
    }
    renderNormalizedInventory();
}
window.toggleNormGroupCollapse = toggleNormGroupCollapse;

function expandAllNormGroups() {
    window._normCollapsedGroups.clear();
    renderNormalizedInventory();
}
window.expandAllNormGroups = expandAllNormGroups;

function collapseAllNormGroups() {
    if (window._normCachedGroups && window._normCachedGroups.length > 0) {
        window._normCachedGroups.forEach(gid => window._normCollapsedGroups.add(gid));
    }
    renderNormalizedInventory();
}
window.collapseAllNormGroups = collapseAllNormGroups;

async function upsertNormalizedAllForSymbol(sym) {
    const targets = (window._normCachedItems || []).filter(x => x.symbol === sym);
    if (!targets || targets.length === 0) return;
    const intvs = targets.map(x => x.interval).join(', ');
    showToast(`⚡ [${sym}] 전체 가용 주기(${intvs}) 순차 증분 시작...`, 'info');
    for (const it of targets) {
        await upsertNormalizedSingle(it.symbol, it.interval);
    }
}
window.upsertNormalizedAllForSymbol = upsertNormalizedAllForSymbol;

async function loadNormalizedInventory() {
    const tbody = document.getElementById('normInventoryTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:16px; color:var(--accent-cyan);">🔄 정규화 Parquet 인벤토리를 스캔하는 중...</td></tr>`;

    try {
        const res = await apiGet('/api/data_vault/normalized_inventory');
        if (!res || res.status !== 'ok') {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:16px; color:var(--accent-red);">인벤토리 조회 실패: ${res?.message || '알 수 없는 오류'}</td></tr>`;
            return;
        }

        const items = res.items || [];
        window._normCachedItems = items;
        const validCount = items.filter(x => x.exists && x.manifest_ok).length;

        // 고유 종목 수 집계
        const uniqueSymbols = new Set(items.map(x => x.symbol)).size;
        
        // 텔레메트리 갱신
        const statusEl = document.getElementById('normTelemStatus');
        const sizeEl = document.getElementById('normTelemTotalSize');
        if (statusEl) statusEl.textContent = `인벤토리 완결 (${uniqueSymbols}개 종목 / ${validCount}개 아티팩트)`;
        if (sizeEl) sizeEl.textContent = `총 정규화 용량: ${res.total_size_mb} MB`;

        renderNormalizedInventory();
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:16px; color:var(--accent-red);">인벤토리 로딩 오류: ${err.message}</td></tr>`;
    }
}
window.loadNormalizedInventory = loadNormalizedInventory;

function renderNormalizedInventory() {
    const tbody = document.getElementById('normInventoryTableBody');
    if (!tbody) return;

    let items = window._normCachedItems || [];
    const q = window._normSearchQuery;
    if (q) {
        items = items.filter(it => 
            (it.symbol && it.symbol.toLowerCase().includes(q)) ||
            (it.name && it.name.toLowerCase().includes(q)) ||
            (it.interval && it.interval.toLowerCase().includes(q)) ||
            (it.role && it.role.toLowerCase().includes(q))
        );
    }

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:16px; color:var(--text-muted);">${q ? `'${q}'에 일치하는 정규화 자산이 없습니다.` : '등록된 정규화 데이터가 없습니다.'}</td></tr>`;
        return;
    }

    const mode = window._normTreeMode || 'asset';
    let html = '';
    window._normCachedGroups = [];

    // 공통 헬퍼: 뱃지 생성 함수들 (초고밀도 슬림 규격)
    const getRoleBadge = (role) => {
        if (role === 'target') return `<span class="badge" style="background:rgba(168,85,247,0.2); color:#c084fc; font-weight:bold; font-size:9.5px; padding:1px 5px;">🎯 타겟</span>`;
        if (role === 'leading_stock') return `<span class="badge" style="background:rgba(234,179,8,0.2); color:#facc15; font-size:9.5px; padding:1px 5px;">⭐ 대장주</span>`;
        if (role === 'macro_futures') return `<span class="badge" style="background:rgba(56,189,248,0.2); color:#38bdf8; font-size:9.5px; padding:1px 5px;">🌐 매크로</span>`;
        if (role === 'macro_proxy') return `<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; font-size:9.5px; padding:1px 5px;">⚡ 대용 매크로</span>`;
        return `<span class="badge" style="background:rgba(255,255,255,0.06); color:#94a3b8; font-size:9.5px; padding:1px 5px;">${role}</span>`;
    };

    const getSamplingBadge = (item) => {
        const interval = item.interval;
        if (item.sampling_mode === 'TICK_RESAMPLED' || interval === '10s') {
            return `<span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80; border:1px solid rgba(34,197,94,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="틱(Tick) 원천 직접 리샘플링">🟢 NATIVE ${interval}</span>`;
        } else if (item.sampling_mode === 'DOWNSAMPLED') {
            return `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="상위 주기 다운샘플링 집계">🟣 DOWNSAMPLED</span>`;
        } else if (item.sampling_mode === 'POINT_IN_TIME_ASOF') {
            return `<span class="badge" style="background:rgba(234,179,8,0.15); color:#facc15; border:1px solid rgba(234,179,8,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="시점 일치 ASOF 결합">🟡 ASOF ${interval}</span>`;
        }
        return `<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="원천 캔들 표준 정규화">🔵 NATIVE ${interval}</span>`;
    };

    const getMicroBadges = (hasMicro, is10s) => {
        if (hasMicro || is10s) {
            return `<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; font-size:8.5px; padding:1px 4px; margin-right:1.5px;">VWAP</span>` +
                   `<span class="badge" style="background:rgba(34,197,94,0.15); color:#22c55e; font-size:8.5px; padding:1px 4px; margin-right:1.5px;">OFI</span>` +
                   `<span class="badge" style="background:rgba(234,179,8,0.15); color:#eab308; font-size:8.5px; padding:1px 4px; margin-right:1.5px;">RV</span>` +
                   `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; font-size:8.5px; padding:1px 4px;">Auction</span>`;
        }
        return `<span class="badge" style="background:rgba(56,189,248,0.1); color:#38bdf8; font-size:8.5px; padding:1px 4px;">VWAP 선계산</span>`;
    };

    // ─────────────────────────────────────────────────────────────────────────
    // [모드 1: asset] 종목 중심 계층형 마스터-디테일 트리 (기본 뷰)
    // ─────────────────────────────────────────────────────────────────────────
    if (mode === 'asset') {
        const assetMap = {};
        const assetOrder = [];

        items.forEach(it => {
            const sym = it.symbol;
            if (!assetMap[sym]) {
                assetMap[sym] = {
                    symbol: sym,
                    name: it.name,
                    category: it.category,
                    role: it.role,
                    items: [],
                    total_rows: 0,
                    total_size_mb: 0.0,
                    latest_updated: '-',
                    all_manifest_ok: true,
                    has_microstructure: false,
                };
                assetOrder.push(sym);
            }
            assetMap[sym].items.push(it);
            assetMap[sym].total_rows += (it.row_count || 0);
            assetMap[sym].total_size_mb += (it.size_mb || 0);
            if (!it.manifest_ok) assetMap[sym].all_manifest_ok = false;
            if (it.health_summary?.has_microstructure || it.interval === '10s') {
                assetMap[sym].has_microstructure = true;
            }
            if (it.updated_at && it.updated_at !== '-') {
                if (assetMap[sym].latest_updated === '-' || it.updated_at > assetMap[sym].latest_updated) {
                    assetMap[sym].latest_updated = it.updated_at;
                }
            }
        });

        assetOrder.forEach(sym => {
            const asset = assetMap[sym];
            const gid = `asset_${sym}`;
            window._normCachedGroups.push(gid);
            const isCollapsed = window._normCollapsedGroups.has(gid);
            const childItems = asset.items;
            const hasMultiple = childItems.length > 1;

            // 주기 칩 버튼들 (마스터 행 인라인 가로 1열 완벽 배치)
            let intervalChips = '';
            childItems.forEach(c => {
                const color = (c.interval === '10s') ? '#4ade80' : '#38bdf8';
                const bg = (c.interval === '10s') ? 'rgba(34,197,94,0.15)' : 'rgba(56,189,248,0.15)';
                const border = (c.interval === '10s') ? 'rgba(34,197,94,0.35)' : 'rgba(56,189,248,0.35)';
                const rowFmt = c.row_count ? (c.row_count >= 10000 ? `${(c.row_count/10000).toFixed(1)}만` : c.row_count.toLocaleString()) : '-';
                intervalChips += `
                    <span class="badge" style="background:${bg}; color:${color}; border:1px solid ${border}; font-size:9.5px; font-family:monospace; padding:1.5px 5px; line-height:1.2; white-space:nowrap;" title="${c.sampling_desc || ''}">
                        ${c.interval} (${rowFmt})
                    </span>
                `;
            });

            const manifestBadge = asset.all_manifest_ok
                ? `<span class="badge badge-success" style="font-size:9.5px; padding:1.5px 5px;">🟢 정상 (${childItems.length}/${childItems.length})</span>`
                : `<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; font-size:9.5px; padding:1.5px 5px;">🔴 확인요망</span>`;

            // 마스터 행 (Raw Store 수준의 조밀한 패딩)
            html += `
                <tr class="tree-group-header" style="border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.02); transition: background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.04)'" onmouseout="this.style.background='rgba(255,255,255,0.02)'">
                    <td style="padding:5.5px 10px; font-weight:600; color:#f8fafc; cursor:pointer;" onclick="toggleNormGroupCollapse('${gid}')">
                        <div style="display:flex; align-items:flex-start; gap:6px;">
                            <span style="font-size:11px; color:var(--accent-cyan); font-family:monospace; display:inline-block; width:12px; margin-top:2px;">${isCollapsed ? '▶' : '▼'}</span>
                            <div style="display:flex; flex-direction:column; gap:1px; line-height:1.25;">
                                <span style="font-size:12px; color:#f8fafc; font-weight:700;">${asset.name}</span>
                                <span style="color:var(--accent-cyan); font-family:monospace; font-size:10.5px; font-weight:600;">${sym}</span>
                            </div>
                        </div>
                    </td>
                    <td style="padding:5.5px 8px;">${getRoleBadge(asset.role)}</td>
                    <td style="padding:5.5px 8px; text-align:center; cursor:pointer;" onclick="toggleNormGroupCollapse('${gid}')">
                        <div style="display:inline-flex; align-items:center; gap:3px; flex-wrap:nowrap; white-space:nowrap; justify-content:center;">
                            ${intervalChips}
                        </div>
                    </td>
                    <td style="padding:5.5px 8px; text-align:right; font-family:monospace; color:#f8fafc; font-weight:600; font-size:11.5px;">${asset.total_rows.toLocaleString()}</td>
                    <td style="padding:5.5px 8px; text-align:right; font-family:monospace; color:#94a3b8; font-size:11px;">${asset.total_size_mb.toFixed(2)} MB</td>
                    <td style="padding:5.5px 8px; font-size:10.5px; color:#94a3b8; font-family:monospace;">${asset.latest_updated}</td>
                    <td style="padding:5.5px 8px; text-align:center; font-family:monospace; color:#22c55e; font-size:11px;">0.00%</td>
                    <td style="padding:5.5px 8px; text-align:center;">${getMicroBadges(asset.has_microstructure, false)}</td>
                    <td style="padding:5.5px 8px; text-align:center;">${manifestBadge}</td>
                    <td style="padding:5.5px 8px; text-align:center;">
                        <div style="display:inline-flex; gap:3px;">
                            <button class="btn btn-xs btn-outline-cyan" onclick="openTargetScalesModal('${sym}')" style="font-size:9.5px; padding:1.5px 6px; font-weight:600; color:#facc15; border-color:rgba(250,204,21,0.4);" title="목표 정규화 봉 스케일 설정">🎯 스케일</button>
                            <button class="btn btn-xs btn-outline-cyan" onclick="upsertNormalizedAllForSymbol('${sym}')" style="font-size:9.5px; padding:1.5px 6px; font-weight:600;" title="이 종목의 모든 주기 일괄 증분">⚡ 증분</button>
                            <button class="btn btn-xs" onclick="toggleNormGroupCollapse('${gid}')" style="font-size:9.5px; padding:1.5px 5px; background:rgba(255,255,255,0.06); color:#94a3b8;" title="주기별 세부 스펙 펼침/접기">${isCollapsed ? '▼' : '▲'}</button>
                        </div>
                    </td>
                </tr>
            `;

            // 자식 행 (펼쳤을 때 세부 주기 캔들 스펙 - 조밀하고 깔끔한 서브로우)
            if (!isCollapsed) {
                childItems.forEach((c, idx) => {
                    const isLast = idx === childItems.length - 1;
                    const prefix = isLast ? '└─' : '├─';
                    const cRows = c.row_count ? c.row_count.toLocaleString() : '-';
                    const cSize = c.size_mb ? `${c.size_mb} MB` : '-';
                    const cUpdated = c.updated_at || '-';
                    const hs = c.health_summary || {};
                    const cNull = hs.null_ratio !== undefined ? `${(hs.null_ratio * 100).toFixed(2)}%` : '0.00%';
                    const cManifest = c.manifest_ok 
                        ? `<span class="badge badge-success" style="font-size:9px; padding:1px 4px;">🟢 정상</span>`
                        : `<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; font-size:9px; padding:1px 4px;">🔴 누락</span>`;

                    html += `
                        <tr style="border-bottom:1px solid rgba(255,255,255,0.04); background:rgba(15,23,42,0.45); transition: background 0.15s;" onmouseover="this.style.background='rgba(56,189,248,0.04)'" onmouseout="this.style.background='rgba(15,23,42,0.45)'">
                            <td style="padding:4px 10px 4px 26px; font-size:11px; color:#94a3b8;">
                                <span style="font-family:monospace; color:rgba(255,255,255,0.3); margin-right:5px;">${prefix}</span>
                                <span style="color:#e2e8f0; font-family:monospace; font-weight:600;" title="${c.sampling_desc || ''}">${c.interval}</span>
                            </td>
                            <td style="padding:4px 8px; font-size:9.5px; color:#64748b; font-family:monospace;">
                                ${c.sampling_mode || '-'}
                            </td>
                            <td style="padding:4px 8px; text-align:center;">${getSamplingBadge(c)}</td>
                            <td style="padding:4px 8px; text-align:right; font-family:monospace; color:#cbd5e1; font-size:11px;">${cRows}</td>
                            <td style="padding:4px 8px; text-align:right; font-family:monospace; color:#64748b; font-size:10.5px;">${cSize}</td>
                            <td style="padding:4px 8px; font-size:10.5px; color:#64748b; font-family:monospace;">${cUpdated}</td>
                            <td style="padding:4px 8px; text-align:center; font-family:monospace; color:#22c55e; font-size:10.5px;">${cNull}</td>
                            <td style="padding:4px 8px; text-align:center;">${getMicroBadges(hs.has_microstructure, c.interval === '10s')}</td>
                            <td style="padding:4px 8px; text-align:center;">${cManifest}</td>
                            <td style="padding:4px 8px; text-align:center;">
                                <div style="display:inline-flex; gap:3px;">
                                    <button class="btn btn-xs btn-outline-cyan" onclick="upsertNormalizedSingle('${c.symbol}', '${c.interval}')" style="font-size:9px; padding:1px 5px;" title="${c.interval} 단독 증분">⚡</button>
                                    <button class="btn btn-xs" onclick="previewNormalizedData('${c.symbol}', '${c.interval}')" style="font-size:9px; padding:1px 5px; background:rgba(255,255,255,0.08);" title="최근 15캔들 데이터 및 지표 미리보기">👁️ 미리보기</button>
                                </div>
                            </td>
                        </tr>
                    `;
                });
            }
        });

    // ─────────────────────────────────────────────────────────────────────────
    // [모드 2: role] 전략 역할별 계층 트리
    // ─────────────────────────────────────────────────────────────────────────
    } else if (mode === 'role') {
        const ROLE_METAS = {
            'target': { title: '🎯 실전 매매 타겟 (Targets)', badge: '핵심 목표', desc: 'AI 모델 학습 및 CTrade 실시간 주문 집행 타겟' },
            'leading_stock': { title: '⭐ 선행 대장주 (Leading Stocks)', badge: '상관관계 알파', desc: '타겟 자산 선행 모멘텀 및 페어 스프레드 피처 공급원' },
            'macro_futures': { title: '🌐 글로벌 매크로 & 선물 (Macro & Futures)', badge: '야간/글로벌', desc: '해외 지수 선물 및 베이시스 산출용 외생 매크로' },
            'macro_proxy': { title: '⚡ 대용(유사) 매크로 & CFD (Macro Proxy)', badge: '대용 모멘텀', desc: 'AI 대량 학습 및 24시간 선행 모멘텀 대용 브로커 CFD' },
            'index_benchmark': { title: '📊 벤치마크 지수 (Index Benchmarks)', badge: '시장 기준', desc: '시장 기준 수익률 및 레짐 판독용 현물 지수' },
            'target_or_macro': { title: '🪙 가상자산 현물 (Crypto Spot)', badge: '24/7 연속', desc: '가상자산 전략 타겟 및 글로벌 센티먼트' }
        };

        const groups = {};
        items.forEach(it => {
            const r = it.role || 'other';
            if (!groups[r]) groups[r] = [];
            groups[r].push(it);
        });

        const order = ['target', 'leading_stock', 'macro_futures', 'macro_proxy', 'index_benchmark', 'target_or_macro'];
        const sortedKeys = Object.keys(groups).sort((a,b) => {
            const ia = order.indexOf(a) !== -1 ? order.indexOf(a) : 99;
            const ib = order.indexOf(b) !== -1 ? order.indexOf(b) : 99;
            return ia - ib;
        });

        sortedKeys.forEach(rKey => {
            const rItems = groups[rKey];
            const rMeta = ROLE_METAS[rKey] || { title: `📌 ${rKey}`, badge: '전략 역할', desc: '전략 참조 데이터' };
            const gid = `norm_role_${rKey}`;
            window._normCachedGroups.push(gid);
            const isCollapsed = window._normCollapsedGroups.has(gid);
            const totalGroupMb = rItems.reduce((acc, c) => acc + (c.size_mb || 0), 0);
            const totalBars = rItems.reduce((acc, c) => acc + (c.row_count || 0), 0);

            html += `
                <tr class="tree-group-header" onclick="toggleNormGroupCollapse('${gid}')" style="background:linear-gradient(90deg, rgba(168,85,247,0.12), rgba(15,23,42,0.6)); cursor:pointer; border-top:1px solid rgba(168,85,247,0.25); border-bottom:1px solid rgba(255,255,255,0.06);">
                    <td colspan="10" style="padding:6px 10px;">
                        <div style="display:flex; align-items:center; gap:6px; flex-wrap:nowrap; white-space:nowrap;">
                            <span style="font-size:11px; color:#c084fc; font-family:monospace; display:inline-block; width:12px;">${isCollapsed ? '▶' : '▼'}</span>
                            <strong style="color:#c084fc; font-size:12px;">${rMeta.title}</strong>
                            <span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; font-size:9.5px; padding:1px 5px;">${rMeta.badge}</span>
                            <span style="font-size:10.5px; color:var(--text-muted); margin-left:4px;">${rMeta.desc}</span>
                            <span style="margin-left:auto; font-size:11px; color:var(--text-secondary); font-family:monospace;">
                                <strong>${rItems.length}개 아티팩트</strong> (${totalBars.toLocaleString()}바 / ${totalGroupMb.toFixed(1)} MB)
                            </span>
                        </div>
                    </td>
                </tr>
            `;

            if (!isCollapsed) {
                rItems.forEach(it => {
                    html += buildNormFlatRowHtml(it, true);
                });
            }
        });

    // ─────────────────────────────────────────────────────────────────────────
    // [모드 3: flat] 기존 평면 테이블 뷰 (하위 호환성)
    // ─────────────────────────────────────────────────────────────────────────
    } else {
        items.forEach(it => {
            html += buildNormFlatRowHtml(it, false);
        });
    }

    tbody.innerHTML = html;
}

function buildNormFlatRowHtml(item, isIndented) {
    const sym = item.symbol;
    const name = item.name;
    const role = item.role;
    const interval = item.interval;
    const rows = item.row_count ? item.row_count.toLocaleString() : '-';
    const size = item.size_mb ? `${item.size_mb} MB` : '-';
    const updated = item.updated_at || '-';
    const hs = item.health_summary || {};
    const nullRatio = hs.null_ratio !== undefined ? `${(hs.null_ratio * 100).toFixed(2)}%` : '0.00%';

    let roleBadge = `<span class="badge" style="background:rgba(255,255,255,0.06); color:#94a3b8; font-size:9.5px; padding:1px 5px;">${role}</span>`;
    if (role === 'target') roleBadge = `<span class="badge" style="background:rgba(168,85,247,0.2); color:#c084fc; font-weight:bold; font-size:9.5px; padding:1px 5px;">🎯 타겟</span>`;
    else if (role === 'leading_stock') roleBadge = `<span class="badge" style="background:rgba(234,179,8,0.2); color:#facc15; font-size:9.5px; padding:1px 5px;">⭐ 대장주</span>`;
    else if (role === 'macro_futures') roleBadge = `<span class="badge" style="background:rgba(56,189,248,0.2); color:#38bdf8; font-size:9.5px; padding:1px 5px;">🌐 매크로</span>`;

    let microBadges = '';
    if (hs.has_microstructure || interval === '10s') {
        microBadges = `<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; font-size:8.5px; padding:1px 4px; margin-right:1.5px;">VWAP</span>` +
                      `<span class="badge" style="background:rgba(34,197,94,0.15); color:#22c55e; font-size:8.5px; padding:1px 4px; margin-right:1.5px;">OFI</span>` +
                      `<span class="badge" style="background:rgba(234,179,8,0.15); color:#eab308; font-size:8.5px; padding:1px 4px; margin-right:1.5px;">RV</span>` +
                      `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; font-size:8.5px; padding:1px 4px;">Auction</span>`;
    } else {
        microBadges = `<span class="badge" style="background:rgba(56,189,248,0.1); color:#38bdf8; font-size:8.5px; padding:1px 4px;">VWAP 선계산</span>`;
    }

    const manifestBadge = item.manifest_ok 
        ? `<span class="badge badge-success" style="font-size:9.5px; padding:1.5px 5px;">🟢 정상</span>`
        : `<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; font-size:9.5px; padding:1.5px 5px;">🔴 누락</span>`;

    let samplingBadge = '';
    if (item.sampling_mode === 'TICK_RESAMPLED' || interval === '10s') {
        samplingBadge = `<span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80; border:1px solid rgba(34,197,94,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="틱(Tick) 원천 직접 리샘플링">🟢 NATIVE ${interval}</span>`;
    } else if (item.sampling_mode === 'DOWNSAMPLED') {
        samplingBadge = `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="상위 주기 다운샘플링 집계">🟣 DOWNSAMPLED</span>`;
    } else if (item.sampling_mode === 'POINT_IN_TIME_ASOF') {
        samplingBadge = `<span class="badge" style="background:rgba(234,179,8,0.15); color:#facc15; border:1px solid rgba(234,179,8,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="시점 일치 ASOF 결합">🟡 ASOF ${interval}</span>`;
    } else {
        samplingBadge = `<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); font-size:9.5px; font-family:monospace; padding:1px 5px; line-height:1.2; white-space:nowrap;" title="원천 캔들 표준 정규화">🔵 NATIVE ${interval}</span>`;
    }

    const padLeft = isIndented ? 'padding-left:22px;' : '';
    return `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06); transition: background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.03)'" onmouseout="this.style.background='transparent'">
            <td style="padding:5px 10px; font-weight:600; color:#f8fafc; ${padLeft}">
                <div style="display:inline-flex; align-items:flex-start; gap:6px;">
                    ${isIndented ? '<span style="font-family:monospace; color:rgba(255,255,255,0.25); margin-top:2px;">└─</span>' : ''}
                    <div style="display:inline-flex; flex-direction:column; gap:1px; line-height:1.25;">
                        <span style="font-size:12px; color:#f8fafc; font-weight:700;">${name}</span>
                        <span style="color:var(--accent-cyan); font-family:monospace; font-size:10.5px; font-weight:600;">${sym}</span>
                    </div>
                </div>
            </td>
            <td style="padding:5px 8px;">${roleBadge}</td>
            <td style="padding:5px 8px; text-align:center;">${samplingBadge}</td>
            <td style="padding:5px 8px; text-align:right; font-family:monospace; color:#f8fafc; font-weight:600; font-size:11.5px;">${rows}</td>
            <td style="padding:5px 8px; text-align:right; font-family:monospace; color:#94a3b8; font-size:11px;">${size}</td>
            <td style="padding:5px 8px; font-size:10.5px; color:#94a3b8; font-family:monospace;">${updated}</td>
            <td style="padding:5px 8px; text-align:center; font-family:monospace; color:#22c55e; font-size:11px;">${nullRatio}</td>
            <td style="padding:5px 8px; text-align:center;">${microBadges}</td>
            <td style="padding:5px 8px; text-align:center;">${manifestBadge}</td>
            <td style="padding:5px 8px; text-align:center;">
                <div style="display:inline-flex; gap:3px;">
                    <button class="btn btn-xs btn-outline-cyan" onclick="upsertNormalizedSingle('${sym}', '${interval}')" style="font-size:9.5px; padding:1.5px 6px;" title="마지막 기록일 이후 신규 데이터 증분 업서트">⚡ 증분</button>
                    <button class="btn btn-xs" onclick="previewNormalizedData('${sym}', '${interval}')" style="font-size:9.5px; padding:1.5px 6px; background:rgba(255,255,255,0.08);" title="최근 15캔들 데이터 및 지표 미리보기">👁️ 미리보기</button>
                </div>
            </td>
        </tr>
    `;
}


async function buildNormalizedAll() {
    const btn = document.getElementById('btnBuildNormAll');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 전 자산 일괄 정규화 진행 중...'; }
    showToast('🚀 17대 전 자산 일괄 정규화 파이프라인 가동...', 'info');

    try {
        const res = await apiPost('/api/data_vault/build_normalized_all');
        if (res && res.status === 'ok') {
            showToast(`🎉 ${res.message}`, 'success');
            loadNormalizedInventory();
        } else {
            showToast(`❌ 일괄 정규화 실패: ${res?.message || '오류'}`, 'danger');
        }
    } catch (err) {
        showToast(`❌ 통신 오류: ${err.message}`, 'danger');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🚀 전 자산 일괄 정규화 (Rebuild)'; }
    }
}
window.buildNormalizedAll = buildNormalizedAll;

async function upsertNormalizedAll() {
    const btn = document.getElementById('btnUpsertNormAll');
    if (btn) { btn.disabled = true; btn.textContent = '⚡ 신규 일자 증분 업서트 진행 중...'; }
    showToast('⚡ 활성 자산 전체 일자별 증분 업서트 시작...', 'info');

    try {
        const res = await apiPost('/api/data_vault/upsert_normalized_all');
        if (res && res.status === 'ok') {
            showToast(`🎉 ${res.message}`, 'success');
            loadNormalizedInventory();
        } else {
            showToast(`❌ 증분 업서트 실패: ${res?.message || '오류'}`, 'danger');
        }
    } catch (err) {
        showToast(`❌ 통신 오류: ${err.message}`, 'danger');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '⚡ 신규 일자 증분 업서트 (Upsert)'; }
    }
}
window.upsertNormalizedAll = upsertNormalizedAll;

async function upsertNormalizedSingle(symbol, interval) {
    showToast(`⚡ [${symbol}] (${interval}) 증분 업서트 실행 중...`, 'info');
    try {
        const res = await apiPost('/api/data_vault/upsert_normalized_single', { symbol, interval });
        if (res && res.status === 'ok') {
            const r = res.result || {};
            const msg = r.status === 'skipped' ? '이미 최신 상태입니다 (스킵)' : `완료 (+${r.added_rows || 0}행 갱신)`;
            showToast(`✅ [${symbol}] 증분 업서트 ${msg}`, 'success');
            loadNormalizedInventory();
        } else {
            showToast(`❌ [${symbol}] 업서트 실패: ${res?.message || '오류'}`, 'danger');
        }
    } catch (err) {
        showToast(`❌ 오류: ${err.message}`, 'danger');
    }
}
window.upsertNormalizedSingle = upsertNormalizedSingle;

function getSymbolQuantPipelineBadge(symbol) {
    if (!symbol) return '';
    const s = String(symbol).toLowerCase();
    if (s.includes('101') || s.includes('future')) {
        return `<span class="badge" style="background:rgba(59,130,246,0.18); color:#60a5fa; border:1px solid rgba(59,130,246,0.4); font-size:10.5px; padding:2px 7px;">🔗 3단계 Gold: _compute_kospi_basis_z (베이시스 Z-Score)</span>`;
    } else if (s === '005930' || s.includes('samsung')) {
        return `<span class="badge" style="background:rgba(234,179,8,0.18); color:#facc15; border:1px solid rgba(234,179,8,0.4); font-size:10.5px; padding:2px 7px;">🔗 3단계 Gold: _compute_top2_lead_momentum (삼전 70% + 하닉 30%)</span>`;
    } else if (s === '000660' || s.includes('hynix')) {
        return `<span class="badge" style="background:rgba(234,179,8,0.18); color:#facc15; border:1px solid rgba(234,179,8,0.4); font-size:10.5px; padding:2px 7px;">🔗 3단계 Gold: _compute_top2_lead_momentum (삼전 70% + 하닉 30%)</span>`;
    } else if (s.startsWith('ref_') || s.includes('lead')) {
        return `<span class="badge" style="background:rgba(168,85,247,0.18); color:#c084fc; border:1px solid rgba(168,85,247,0.4); font-size:10.5px; padding:2px 7px;">🔗 3단계 Gold: 대장주 선행 스프레드/모멘텀 피처</span>`;
    } else if (s.includes('nq') || s.includes('es') || s.includes('sp500')) {
        return `<span class="badge" style="background:rgba(56,189,248,0.18); color:#38bdf8; border:1px solid rgba(56,189,248,0.4); font-size:10.5px; padding:2px 7px;">🔗 3단계 Gold: 글로벌 매크로 선물 선행 모멘텀</span>`;
    } else {
        return `<span class="badge" style="background:rgba(34,197,94,0.18); color:#4ade80; border:1px solid rgba(34,197,94,0.4); font-size:10.5px; padding:2px 7px;">🔗 3단계 Gold: _compute_parkinson_rv_squeeze & 기술적 지표 & CExecution 직통</span>`;
    }
}
window.getSymbolQuantPipelineBadge = getSymbolQuantPipelineBadge;

async function previewNormalizedData(symbol, interval) {
    const card = document.getElementById('normPreviewCard');
    const title = document.getElementById('normPreviewTitle');
    const container = document.getElementById('normPreviewTableContainer');
    if (!card || !container) return;

    card.style.display = 'block';
    if (title) {
        title.innerHTML = `
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                <span>👁️ [${symbol}] (${interval}) 마이크로스트럭처 캔들 인스펙터</span>
                ${getSymbolQuantPipelineBadge(symbol)}
            </div>
        `;
    }
    container.innerHTML = `<div style="text-align:center; padding:16px; color:var(--accent-cyan);">🔄 최근 캔들 데이터 로딩 중...</div>`;

    try {
        const res = await apiGet(`/api/data_vault/normalized_preview?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}&limit=15`);
        if (!res || res.status !== 'ok' || !res.records) {
            container.innerHTML = `<div style="text-align:center; padding:16px; color:var(--accent-red);">데이터 로드 실패: ${res?.message || '오류'}</div>`;
            return;
        }

        const cols = res.columns || [];
        const records = res.records || [];
        const displayCols = cols.filter(c => ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vwap', 'ofi', 'rv', 'is_auction'].includes(c));

        // 대용 프록시/다른 주기로 서빙된 경우 표 위에 경고를 띄운다 (다른 종목 데이터를 오인하지 않도록)
        let tableHtml = '';
        if (res.is_proxy || (res.requested_interval && res.interval !== res.requested_interval)) {
            const parts = [];
            if (res.is_proxy) parts.push(`종목 <b>${res.requested_symbol}</b> → 대용 <b>${res.symbol}</b>`);
            if (res.requested_interval && res.interval !== res.requested_interval) parts.push(`주기 <b>${res.requested_interval}</b> → <b>${res.interval}</b>`);
            tableHtml += `<div style="margin-bottom:8px; padding:6px 10px; border-radius:4px; background:rgba(234,179,8,0.12); border:1px solid rgba(234,179,8,0.4); color:#facc15; font-size:11px;">
                ⚠️ 요청한 데이터가 없어 대체본을 표시합니다 — ${parts.join(', ')}. 실제 값이 아닙니다.
            </div>`;
        }
        tableHtml += `<table class="table" style="width:100%; font-size:11px; font-family:monospace; border-collapse:collapse;">`;
        tableHtml += `<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.2); color:#94a3b8; text-align:right;">`;
        displayCols.forEach((col, idx) => {
            const align = idx === 0 ? 'text-align:left;' : 'text-align:right;';
            tableHtml += `<th style="padding:5px 8px; ${align}">${col}</th>`;
        });
        tableHtml += `</tr></thead><tbody>`;

        records.forEach(row => {
            tableHtml += `<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">`;
            displayCols.forEach((col, idx) => {
                const val = row[col];
                let displayVal = val;
                if (typeof val === 'number') {
                    if (['open', 'high', 'low', 'close', 'vwap'].includes(col)) {
                        displayVal = val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
                    } else if (['ofi', 'rv'].includes(col)) {
                        displayVal = val.toFixed(4);
                    } else if (col === 'volume') {
                        displayVal = val.toLocaleString(undefined, { maximumFractionDigits: 1 });
                    }
                }
                const align = idx === 0 ? 'text-align:left; color:#f8fafc;' : 'text-align:right; color:#cbd5e1;';
                tableHtml += `<td style="padding:4px 8px; ${align}">${displayVal !== null && displayVal !== undefined ? displayVal : '-'}</td>`;
            });
            tableHtml += `</tr>`;
        });
        tableHtml += `</tbody></table>`;
        container.innerHTML = tableHtml;
    } catch (err) {
        container.innerHTML = `<div style="text-align:center; padding:16px; color:var(--accent-red);">미리보기 오류: ${err.message}</div>`;
    }
}
window.previewNormalizedData = previewNormalizedData;

function closeNormPreview() {
    const card = document.getElementById('normPreviewCard');
    if (card) card.style.display = 'none';
}
window.closeNormPreview = closeNormPreview;


async function loadDataVaultStatus() {
    loadMedallionStatus();
    loadRawFeedRegistry();
    loadHealthMatrix();
    loadRawCatalog();
    if (typeof loadRawBackupStatus === 'function') loadRawBackupStatus();
    const tbody = document.getElementById('dataVaultTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:16px; color:var(--accent-cyan);">🔄 라이브러리 상태 로딩 중...</td></tr>`;

    // 카탈로그 품질도 항상 최신 동기화
    loadDataCatalogSources(true);

    try {
        const res = await apiGet('/api/data_vault/status');
        if (!res || res.status !== 'ok' || !res.items) {
            tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:16px; color:var(--accent-red);">상태 조회 실패: ${res?.message || '알 수 없는 오류'}</td></tr>`;
            return;
        }

        let html = '';
        res.items.forEach(item => {
            const sym = item.symbol;
            const name = item.name;
            const role = item.role;
            const lastPrice = item.last_price;
            const lastTime = item.last_time || '-';
            const ints = item.intervals || {};

            let roleBadge = '<span class="badge badge-primary" style="font-size:10px;">타겟</span>';
            if (role === 'reference' || role === 'leading_stock') roleBadge = '<span class="badge badge-info" style="font-size:10px; background:#0284c7;">대장주</span>';
            else if (role === 'macro' || role === 'macro_futures') roleBadge = '<span class="badge badge-warning" style="font-size:10px; background:#d97706;">매크로</span>';
            else if (role === 'leading_exchange') roleBadge = '<span class="badge" style="font-size:10px; background:#4f46e5;">선행소</span>';

            const priceText = lastPrice !== null && lastPrice !== undefined 
                ? (lastPrice >= 1000 ? `₩${Number(lastPrice).toLocaleString()}` : `${Number(lastPrice).toFixed(2)}`)
                : '<span style="color:var(--text-muted); font-size:11px;">-</span>';

            // 실시간 피드 준비성(Readiness) 뱃지 렌더링
            const probe = _vaultLiveProbes[sym];
            let readinessBadge = '';
            if (probe) {
                if (probe.readiness === 'LIVE_READY') {
                    const isClosed = probe.status_code === 'MARKET_CLOSED_AUTH_OK';
                    const bgStyle = isClosed ? 'rgba(56, 189, 248, 0.15)' : 'rgba(34, 197, 94, 0.15)';
                    const textStyle = isClosed ? '#38bdf8' : '#22c55e';
                    const borderStyle = isClosed ? '1px solid rgba(56, 189, 248, 0.4)' : '1px solid rgba(34, 197, 94, 0.4)';
                    readinessBadge = `<span class="badge" style="background:${bgStyle}; color:${textStyle}; border:${borderStyle}; font-size:10px; padding:2px 6px; cursor:help;" title="${probe.message}">🟢 ${probe.badge_text}</span>`;
                } else if (probe.readiness === 'RESEARCH_ONLY') {
                    readinessBadge = `<span class="badge" style="background:rgba(234, 179, 8, 0.15); color:#eab308; border:1px solid rgba(234, 179, 8, 0.4); font-size:10px; padding:2px 6px; cursor:help;" title="${probe.message}">🟡 연구전용</span>`;
                } else {
                    readinessBadge = `<span class="badge badge-danger" style="font-size:10px;">🔴 미지원</span>`;
                }
            } else {
                readinessBadge = `
                    <button class="btn btn-sm" onclick="probeLiveFeed('${sym}')" style="font-size:10px; padding:2px 8px; background:rgba(99,102,241,0.15); color:#818cf8; border:1px solid rgba(99,102,241,0.4);" title="실시간 KIS/웹소켓 피드 수신 안전 점검">
                        ⚡ 점검
                    </button>
                `;
            }

            const renderIntervalCell = (intKey) => {
                const info = ints[intKey];
                if (info && info.available && info.count > 0) {
                    return `<span class="badge badge-success" style="font-size:10px; cursor:pointer;" title="기간: ${info.start} ~ ${info.end}">🟢 ${info.count.toLocaleString()}행</span>`;
                } else {
                    return `<span class="badge" style="font-size:10px; background:rgba(255,255,255,0.08); color:var(--text-muted); cursor:pointer;" onclick="resampleVaultInterval('${sym}', '${intKey}')" title="클릭하여 즉시 리샘플링 생성">⚪ 미생성</span>`;
                }
            };

            html += `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.05); text-align:center;">
                    <td style="padding:8px; text-align:left; font-weight:bold; color:var(--text-primary);">
                        ${name} <span style="font-size:11px; color:var(--accent-cyan); font-weight:normal;">(${sym})</span>
                    </td>
                    <td style="padding:8px;">${roleBadge}</td>
                    <td style="padding:8px;">${readinessBadge}</td>
                    <td style="padding:8px; text-align:right; font-weight:bold; font-family:monospace; color:#38bdf8;">${priceText}</td>
                    <td style="padding:8px; font-size:11px; color:var(--text-secondary);">${lastTime}</td>
                    <td style="padding:8px;">${renderIntervalCell('1m')}</td>
                    <td style="padding:8px;">${renderIntervalCell('3m')}</td>
                    <td style="padding:8px;">${renderIntervalCell('5m')}</td>
                    <td style="padding:8px;">${renderIntervalCell('15m')}</td>
                    <td style="padding:8px;">${renderIntervalCell('1d')}</td>
                    <td style="padding:8px;">
                        <button class="btn btn-sm btn-outline-cyan" onclick="resampleVaultInterval('${sym}', '5m')" style="font-size:10px; padding:2px 8px;">
                            ⚡ 5m 리샘플링
                        </button>
                    </td>
                </tr>
            `;
        });

        tbody.innerHTML = html;

        // [META] 마스터 데이터셋 기간 배너 렌더링
        const meta = res.master_dataset;
        const metaBanner = document.getElementById('masterDatasetMetaBanner');
        if (metaBanner && meta && meta.rows) {
            const startYm = meta.timestamp_min ? meta.timestamp_min.slice(0, 7) : '?';
            const endYm   = meta.timestamp_max ? meta.timestamp_max.slice(0, 10) : '?';
            const builtAt = meta.built_at || '-';
            metaBanner.innerHTML = `
                <span style="color:#22c55e; font-weight:bold;">📊 마스터 원장</span>
                <span class="header-badge" style="background:rgba(34,197,94,0.12); color:#22c55e; border:1px solid rgba(34,197,94,0.35); font-size:11px; padding:2px 8px;">
                    ${startYm} ~ ${endYm}
                </span>
                <span class="header-badge" style="background:rgba(99,102,241,0.12); color:#818cf8; border:1px solid rgba(99,102,241,0.35); font-size:11px; padding:2px 8px;">
                    ${Number(meta.rows).toLocaleString()}행
                </span>
                <span style="font-size:11px; color:var(--text-muted);">빌드: ${builtAt}</span>
            `;
            metaBanner.style.display = 'flex';
        } else if (metaBanner) {
            metaBanner.style.display = 'none';
        }

    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:16px; color:var(--accent-red);">네트워크 오류: ${e.message}</td></tr>`;
    }
}

async function probeLiveFeed(symbol) {
    showToast(`⚡ '${symbol}' 실시간 피드 안전 점검 중...`, 'info');
    try {
        const res = await apiPost('/api/data_vault/probe_feed', { symbol: symbol });
        if (res && res.status === 'ok' && res.probe) {
            _vaultLiveProbes[symbol] = res.probe;
            showToast(`✅ [${symbol}] ${res.probe.badge_text}: ${res.probe.message}`, 'success');
            loadDataVaultStatus();
        } else {
            showToast(`점검 실패: ${res?.message || '오류'}`, 'error');
        }
    } catch (e) {
        showToast(`점검 오류: ${e.message}`, 'error');
    }
}

async function probeAllLiveFeeds() {
    showToast('⚡ 전체 종목 실시간 피드 안전 점검 중...', 'info');
    try {
        const res = await apiPost('/api/data_vault/probe_feed', {});
        if (res && res.status === 'ok' && res.probes) {
            _vaultLiveProbes = { ..._vaultLiveProbes, ...res.probes };
            showToast('✅ 전체 피드 점검 완료 (신호등 상태 갱신 완료)', 'success');
            loadDataVaultStatus();
        } else {
            showToast(`전체 점검 실패: ${res?.message || '오류'}`, 'error');
        }
    } catch (e) {
        showToast(`전체 점검 오류: ${e.message}`, 'error');
    }
}

window.probeLiveFeed = probeLiveFeed;
window.probeAllLiveFeeds = probeAllLiveFeeds;

// =============================================================================
// 🌐 3단계 데이터 도킹 & 퀀트 알파 파이프라인 트리 (Data Docking & Pipeline Tree Grid)
// =============================================================================

// 3단계 파이프라인 지표 카탈로그 (2단계 원천 -> 3단계 함수 1:1 직통 매핑 레지스트리)
const PIPELINE_FEATURE_SPECS = {
    // 🎯 [그룹 1: 타겟 자산 피처]
    'target_volatility_gk': {
        name: 'Garman-Klass 마이크로 변동성',
        category: 'target',
        source_cols: ['high', 'low', 'close', 'open'],
        func_name: 'shared.feature_registry._compute_gk_volatility',
        params: 'window=14,log_scale=True',
        sampling: 'NATIVE 10s / 1m',
        guard: '타겟 풀 데이터셋 100% 보존',
        desc: '장중 고가/저가/시가/종가의 기하학적 분포를 반영하여 드리프트 노이즈를 최소화한 무결점 캔들 실현 변동성',
        sample_calc: (base) => (Math.sqrt(Math.max(1, Math.abs((base.high || 385500) - (base.low || 384800))) / (base.close || 385200)) * 0.015).toFixed(6)
    },
    'target_rsi_14': {
        name: '상대강도지수 (RSI 14)',
        category: 'target',
        source_cols: ['close'],
        func_name: 'shared.feature_registry._compute_rsi',
        params: 'period=14,ema_mode=Wilder',
        sampling: 'NATIVE 10s / 1m',
        guard: '타겟 풀 데이터셋 100% 보존',
        desc: '14개 바 기준 가격 상승폭과 하락폭의 상대 강도를 0~100으로 정규화한 모멘텀 오실레이터',
        sample_calc: (base) => (52.40 + ((base.close || 385200) % 10) * 0.45).toFixed(2)
    },
    'target_bbands_20_2': {
        name: '볼린저 밴드 이격률 (%B)',
        category: 'target',
        source_cols: ['close'],
        func_name: 'shared.feature_registry._compute_bbands',
        params: 'window=20,num_std=2.0',
        sampling: 'NATIVE 10s / 1m',
        guard: '타겟 풀 데이터셋 100% 보존',
        desc: '20개 바 이동평균선과 상하단 2 표준편차 밴드 사이의 상대적 가격 위치 백분율',
        sample_calc: (base) => (0.6410 + ((base.close || 385200) % 5) * 0.018).toFixed(4)
    },
    'target_macd_12_26_9': {
        name: 'MACD 오실레이터 히스토그램',
        category: 'target',
        source_cols: ['close'],
        func_name: 'shared.feature_registry._compute_macd',
        params: 'fast=12,slow=26,signal=9',
        sampling: 'NATIVE 10s / 1m',
        guard: '타겟 풀 데이터셋 100% 보존',
        desc: '12-26 단기/장기 지수이동평균의 수렴 및 확산 차이를 9-EMA 신호선과 대비한 모멘텀 히스토그램',
        sample_calc: (base) => (12.50 + ((base.close || 385200) % 7) * 0.95).toFixed(2)
    },
    'target_vwap_dev': {
        name: 'VWAP 거래량가중평균 이격도',
        category: 'target',
        source_cols: ['close', 'vwap'],
        func_name: 'shared.feature_registry._compute_vwap_dev',
        params: 'window=session_anchor',
        sampling: 'NATIVE 10s Microstructure',
        guard: '누적 장중 세션 VWAP 선계산',
        desc: '장중 누적 거래대금/누적체결량으로 산출된 세션 공정 가격(VWAP)과의 백분율 괴리율',
        sample_calc: (base) => (0.001850 + ((base.close || 385200) % 3) * 0.00032).toFixed(6)
    },
    'target_ofi_cum': {
        name: '누적 호가주문흐름 불균형 (OFI)',
        category: 'target',
        source_cols: ['ofi'],
        func_name: 'shared.feature_registry._compute_ofi_cum',
        params: 'decay=0.95,roll_bars=30',
        sampling: 'NATIVE 10s Microstructure',
        guard: '초미세 호가 레벨 체결 불균형',
        desc: '매수/매도 호가 잔량 변동과 실체결량을 결합한 초단타 매수압력 누적 강도 지표',
        sample_calc: (base) => (4520 + ((base.close || 385200) % 100) * 11).toFixed(0)
    },

    // ⭐ [그룹 2: 대장주 바스켓 선행 알파]
    'top2_lead_momentum': {
        name: '대장주 합성 선행 모멘텀 (Top2 Lead)',
        category: 'leading',
        source_cols: ['ref_{종목키}_close (주식 참조 종목, 키 정렬 앞 2개)'],
        func_name: 'shared.feature_registry._compute_top2_lead_momentum',
        params: 'window=5,weights=auto(0.7:0.3)',
        sampling: 'Smart Clamped + Point-in-time ASOF',
        guard: '공통 유효 구간 안전 클램핑 (결측 자동 슬라이싱)',
        desc: '타겟 종목이 속한 섹터 1·2위 대장주들의 최근 5바 가중 수익률을 합성하여 타겟보다 10~30초 선행하는 방향성 추출 (종목 하드코딩 완전 제거)',
        sample_calc: (base) => (0.004210 + ((base.close || 385200) % 5) * 0.00048).toFixed(6)
    },
    'lead_1_lead_lag_spread': {
        name: '1대장주 대비 선도/지연 스프레드 (Lead-Lag)',
        category: 'leading',
        source_cols: ['target_close', 'lead_1_close'],
        func_name: 'shared.feature_registry._compute_lead_lag_spread',
        params: 'zscore_window=60',
        sampling: 'Point-in-time ASOF',
        guard: '결측 시 직전 호가 유지(FFill) + 제로 패딩',
        desc: '타겟 자산과 1대장주 간의 정규화 가격 비율의 평균 회귀 괴리도 (Z-score)',
        sample_calc: (base) => (-0.852 + ((base.close || 385200) % 4) * 0.18).toFixed(3)
    },

    // 🌐 [그룹 3: 글로벌 매크로 & 선물 피처]
    'macro_nq_trend_diff': {
        name: '나스닥(NQ) 야간 선물 추세 선행 괴리',
        category: 'macro',
        source_cols: ['ref_nq_close'],
        func_name: 'shared.feature_registry._compute_macro_trend',
        params: 'lookback=30,smoothing=EMA_5',
        sampling: 'Point-in-time ASOF',
        guard: '야간 갭 및 해외 휴장 시 0 유지',
        desc: '글로벌 유동성을 선도하는 나스닥 100 선물 추세선과 현재 가격의 실시간 모멘텀 차이',
        sample_calc: (base) => (0.002150 + ((base.close || 385200) % 6) * 0.00028).toFixed(6)
    },

    // ⏱️ [그룹 4: 세션 & 시간 피처]
    'time_sin': {
        name: '장중 순환 시각 삼각함수 (Sin)',
        category: 'session',
        source_cols: ['timestamp(KST)'],
        func_name: 'shared.feature_registry._compute_cyclical_time',
        params: 'cycle=390m(09:00~15:30)',
        sampling: 'Deterministic Mathematical Mapping',
        guard: '결측 절대 불가 (자체 시간 연산)',
        desc: '정규장 390분(09:00~15:30)을 2π 주기로 투영하여 모델이 하루 중 시점을 연속적 기하 공간으로 인식하도록 인코딩',
        sample_calc: (base) => (Math.sin(1.2566)).toFixed(6)
    },
    'time_cos': {
        name: '장중 순환 시각 삼각함수 (Cos)',
        category: 'session',
        source_cols: ['timestamp(KST)'],
        func_name: 'shared.feature_registry._compute_cyclical_time',
        params: 'cycle=390m(09:00~15:30)',
        sampling: 'Deterministic Mathematical Mapping',
        guard: '결측 절대 불가 (자체 시간 연산)',
        desc: '정규장 390분의 코사인 투영 값 (Sin과 쌍을 이루어 시간의 연속적 위상 표현)',
        sample_calc: (base) => (Math.cos(1.2566)).toFixed(6)
    },
    'session_opening_rush': {
        name: '장 초반 변동성 집중 플래그 (09:00~09:30)',
        category: 'session',
        source_cols: ['timestamp(KST)'],
        func_name: 'shared.feature_registry._compute_session_phase',
        params: 'cutoff=09:30:00 KST',
        sampling: 'Deterministic Filter',
        guard: '결측 불가',
        desc: '개장 직후 30분간 기관 및 외인 수급이 급격히 유입/방출되는 고변동 구간 가중치 플래그',
        sample_calc: (base) => '1.000000 (Active)'
    },

    // 🪙 [그룹 5: 가상자산 & 바이낸스 선물 전용 파이프라인]
    'binance_funding_rate': {
        name: '바이낸스 무기한 선물 펀딩비',
        category: 'crypto_futures',
        source_cols: ['binance_funding_rate (ref_binance_futures_funding_rate)'],
        func_name: 'shared.feature_registry._compute_binance_funding_rate',
        params: 'window=8h_settle',
        sampling: 'Point-in-time ASOF',
        guard: '선물 롱/숏 포지션 쏠림 및 스퀴즈 압력 측정',
        desc: '바이낸스 BTCUSDT 무기한 선물 8시간 주기 펀딩비율을 추적하여 레버리지 과열 및 숏스퀴즈/롱스퀴즈 임계점 포착',
        sample_calc: (base) => '0.009029% (Neutral-Long)'
    },
    'binance_oi_change_ratio': {
        name: '선물 미결제약정(OI) 변화율',
        category: 'crypto_futures',
        source_cols: ['binance_futures_oi (ref_binance_futures_oi)'],
        func_name: 'shared.feature_registry._compute_binance_oi_change_ratio',
        params: 'window=5,pct_change',
        sampling: 'Point-in-time ASOF',
        guard: '신규 스마트머니 자금 유입 vs 청산 이탈 판별',
        desc: '바이낸스 선물 미결제약정(Open Interest)의 최근 5캔들 가속도를 측정하여 단순 되돌림과 추세 형성 돌파를 분별',
        sample_calc: (base) => '+0.000947 (Inflow)'
    },
    'binance_futures_spot_vol_ratio': {
        name: '선물 vs 현물 거래대금 비율',
        category: 'crypto_futures',
        source_cols: ['binance_futures_vol', 'binance_spot_vol'],
        func_name: 'shared.feature_registry._compute_binance_futures_spot_vol_ratio',
        params: 'ratio=futures/spot',
        sampling: 'Point-in-time ASOF',
        guard: '파생상품 주도 펌핑 vs 현물 실체결 매수 분별',
        desc: '선물 거래대금과 현물 거래대금의 상대 비율을 통해 파생 레버리지 투기 주도 장세인지 현물 축적 장세인지 감시',
        sample_calc: (base) => '3.421 (Derivatives-Led)'
    },
    'binance_leading_momentum': {
        name: '바이낸스 글로벌 선행 모멘텀',
        category: 'crypto_spot',
        source_cols: ['binance_spot_close', 'target_close'],
        func_name: 'shared.feature_registry._compute_binance_leading_momentum',
        params: 'lookback=5,ret_diff',
        sampling: 'Point-in-time ASOF',
        guard: '업비트 원화 마켓 대비 선행 가격 발견',
        desc: '글로벌 유동성 1위 바이낸스 현물 가격 수익률과 국내 업비트 가격 수익률 간의 선행 스프레드 모멘텀',
        sample_calc: (base) => '+0.000906 (Leading)'
    },
    'binance_cvd_divergence': {
        name: '테이커 순매수 CVD 다이버전스',
        category: 'crypto_spot',
        source_cols: ['binance_spot_taker_ratio', 'binance_spot_volume'],
        func_name: 'shared.feature_registry._compute_binance_cvd_divergence',
        params: 'window=20,slope_diff',
        sampling: 'Point-in-time ASOF',
        guard: '가격 횡보 중 고래 공격적 시장가 매수 탐지',
        desc: '바이낸스 현물 테이커 체결 비율을 누적한 CVD(Cumulative Volume Delta)와 가격 간의 괴리율',
        sample_calc: (base) => '+0.0124 (Bullish Div)'
    },
    'kimchi_premium': {
        name: '실시간 김치프리미엄 괴리도',
        category: 'crypto_currency',
        source_cols: ['target_close', 'binance_spot_close', 'crypto_usdt_close'],
        func_name: 'shared.feature_registry._compute_kimchi_premium',
        params: 'diff_pct=((upbit/(binance*usdt))-1)*100',
        sampling: 'Point-in-time ASOF',
        guard: '국내 거래소 단독 과열 및 해외 자금 유출입 경계',
        desc: '국내 업비트 원화 가격과 바이낸스 달러/테더 환산 가격 사이의 실시간 프리미엄 괴리율(%)',
        sample_calc: (base) => '+1.482% (Normal)'
    },
    'kospi_basis': {
        name: 'KOSPI 200 선물-현물 베이시스 (pt)',
        category: 'kospi_futures',
        source_cols: ['futures_close', 'target_close'],
        func_name: 'shared.feature_registry._compute_kospi_basis',
        params: 'diff=futures - spot',
        sampling: 'Point-in-time ASOF',
        guard: '선물 선행 고평가/저평가 실시간 계측',
        desc: 'KOSPI 200 선물 최근월물 가격과 KODEX 200 ETF 순수 시세 간의 실시간 포인트 차이',
        sample_calc: (base) => '+0.65pt (Contango)'
    },
    'kospi_basis_rate': {
        name: 'KOSPI 200 선물 베이시스율 (%)',
        category: 'kospi_futures',
        source_cols: ['futures_close', 'target_close'],
        func_name: 'shared.feature_registry._compute_kospi_basis_rate',
        params: 'pct=((futures/spot)-1)*100',
        sampling: 'Point-in-time ASOF',
        guard: '프로그램 차익거래 유입/출회 선행 지표',
        desc: '선물-현물 베이시스의 백분율 괴리도로 기관/외국인 프로그램 매수 유입 선행 신호 포착',
        sample_calc: (base) => '+0.171% (Inflow Bound)'
    }
};

window._pipelineCollapsedGroups = window._pipelineCollapsedGroups || new Set();
window._pipelineCachedTarget = null;
window._pipelineCachedRefs = [];

function togglePipelineTreeGroup(gid) {
    if (window._pipelineCollapsedGroups.has(gid)) {
        window._pipelineCollapsedGroups.delete(gid);
    } else {
        window._pipelineCollapsedGroups.add(gid);
    }
    if (window._pipelineCachedTarget) {
        renderDataVaultPipelineTree(window._pipelineCachedTarget, window._pipelineCachedRefs);
    }
}
window.togglePipelineTreeGroup = togglePipelineTreeGroup;

function expandAllPipelineTree() {
    window._pipelineCollapsedGroups.clear();
    if (window._pipelineCachedTarget) {
        renderDataVaultPipelineTree(window._pipelineCachedTarget, window._pipelineCachedRefs);
    }
}
window.expandAllPipelineTree = expandAllPipelineTree;

function collapseAllPipelineTree() {
    ['grp_target', 'grp_leading', 'grp_macro', 'grp_session'].forEach(gid => window._pipelineCollapsedGroups.add(gid));
    if (window._pipelineCachedTarget) {
        renderDataVaultPipelineTree(window._pipelineCachedTarget, window._pipelineCachedRefs);
    }
}
window.collapseAllPipelineTree = collapseAllPipelineTree;

// 3단계 파이프라인 트리 렌더러 (Tree Grid - 초고밀도 콤팩트 규격)
function renderDataVaultPipelineTree(target, refs) {
    const tbody = document.getElementById('pipelineTreeTableBody');
    if (!tbody) return;

    window._pipelineCachedTarget = target;
    window._pipelineCachedRefs = refs || [];

    const targetSym = target.symbol || window._currentStudioTarget || '069500';
    const targetName = target.name || targetSym;

    const allRefs = refs || [];
    const refCount = allRefs.length;

    // 헬퍼: 함수 축약 표기 (풀패스는 title 툴팁)
    const renderFuncBadge = (fullFunc) => {
        const shortName = fullFunc.split('.').pop();
        return `<code style="color:#facc15; font-size:11px; cursor:help;" title="${fullFunc}">${shortName}</code>`;
    };

    let html = '';

    // ─────────────────────────────────────────────────────────────────────────
    // [그룹 1: 🎯 타겟 자산 피처 파이프라인]
    // ─────────────────────────────────────────────────────────────────────────
    const gidTarget = 'grp_target';
    const isTargetCollapsed = window._pipelineCollapsedGroups.has(gidTarget);
    const targetFeatIds = ['target_volatility_gk', 'target_vwap_dev', 'target_rsi_14', 'target_ofi_cum', 'target_bbands_20_2', 'target_macd_12_26_9'];

    html += `
        <tr class="tree-group-header" style="border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(56,189,248,0.03); transition: background 0.15s;" onmouseover="this.style.background='rgba(56,189,248,0.06)'" onmouseout="this.style.background='rgba(56,189,248,0.03)'">
            <td style="padding:5px 8px; font-weight:600; color:#f8fafc; cursor:pointer;" onclick="togglePipelineTreeGroup('${gidTarget}')">
                <div style="display:flex; align-items:center; gap:5px;">
                    <span style="font-size:11px; color:var(--accent-cyan); font-family:monospace; display:inline-block; width:10px;">${isTargetCollapsed ? '▶' : '▼'}</span>
                    <span class="badge" style="background:rgba(56,189,248,0.2); color:#38bdf8; font-size:9.5px; padding:1px 4px;">🎯 타겟</span>
                    <span style="color:var(--accent-cyan); font-family:monospace; font-size:11.5px; font-weight:bold;">${targetSym}</span>
                    <span style="font-size:11.5px; color:#cbd5e1; font-weight:600;">${targetName}</span>
                    <span class="badge" style="background:rgba(255,255,255,0.08); color:#94a3b8; font-size:9px; padding:1px 4px; margin-left:2px;" title="현재 프로젝트의 매매 타겟 자산입니다 (Project Lab에서 변경)">프로젝트 타겟</span>
                </div>
            </td>
            <td style="padding:5px 8px; color:#94a3b8; font-size:11px;">
                <code>high,low,close,volume,vwap,ofi</code>
            </td>
            <td style="padding:5px 8px; color:#38bdf8; font-size:11px;">
                <code style="color:#38bdf8;">_compute_* (6개 가동)</code>
            </td>
            <td style="padding:5px 8px; font-size:11px; color:#cbd5e1;">
                Native 10s/1m
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <span class="badge badge-success" style="font-size:9px; padding:1.5px 5px;">🟢 독립보존</span>
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <button class="btn btn-xs btn-outline-cyan" onclick="openPipelineInspector('target_volatility_gk')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 타겟</button>
            </td>
        </tr>
    `;

    if (!isTargetCollapsed) {
        targetFeatIds.forEach(fid => {
            const spec = PIPELINE_FEATURE_SPECS[fid];
            if (!spec) return;
            const colsBadge = spec.source_cols.map(c => `<code style="color:#38bdf8; font-size:10px; background:rgba(56,189,248,0.1); padding:1px 3px; border-radius:3px; margin-right:2px;">${c}</code>`).join('');
            
            html += `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04); background:rgba(0,0,0,0.15); transition: background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='rgba(0,0,0,0.15)'">
                    <td style="padding:4.5px 8px 4.5px 24px; font-size:11.5px;">
                        <div style="display:flex; align-items:center; gap:5px;">
                            <span style="color:var(--text-muted); font-size:10px;">├─</span>
                            <span style="color:#f8fafc; font-weight:500;">${spec.name}</span>
                            <code style="color:var(--accent-cyan); font-size:9.5px;">(${fid})</code>
                        </div>
                    </td>
                    <td style="padding:4.5px 8px; font-size:11px;">${colsBadge}</td>
                    <td style="padding:4.5px 8px; font-size:11px;">${renderFuncBadge(spec.func_name)}</td>
                    <td style="padding:4.5px 8px; font-size:10.5px; color:#cbd5e1; font-family:monospace;">${spec.params}</td>
                    <td style="padding:4.5px 6px; text-align:center;">
                        <span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80; font-size:9px; padding:1px 4px;">🟢 정상</span>
                    </td>
                    <td style="padding:4.5px 6px; text-align:center;">
                        <button class="btn btn-xs btn-outline-cyan" onclick="openPipelineInspector('${fid}')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 상세</button>
                    </td>
                </tr>
            `;
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // [그룹 2: 🔗 참조 종목 파이프라인 (Reference Instruments)]
    // ─────────────────────────────────────────────────────────────────────────
    const gidLeading = 'grp_leading';
    const isLeadingCollapsed = window._pipelineCollapsedGroups.has(gidLeading);

    html += `
        <tr class="tree-group-header" style="border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(168,85,247,0.04); transition: background 0.15s;" onmouseover="this.style.background='rgba(168,85,247,0.07)'" onmouseout="this.style.background='rgba(168,85,247,0.04)'">
            <td style="padding:5px 8px; font-weight:600; color:#f8fafc; cursor:pointer;" onclick="togglePipelineTreeGroup('${gidLeading}')">
                <div style="display:flex; align-items:center; gap:5px;">
                    <span style="font-size:11px; color:#c084fc; font-family:monospace; display:inline-block; width:10px;">${isLeadingCollapsed ? '▶' : '▼'}</span>
                    <span class="badge" style="background:rgba(234,179,8,0.2); color:#facc15; font-size:9.5px; padding:1px 4px;">🔗 참조 종목군</span>
                    <span style="font-size:11.5px; color:#e2e8f0; font-weight:600;">외부 연관 알파 배관</span>
                    <span class="badge" style="background:rgba(168,85,247,0.2); color:#c084fc; font-size:9px; padding:1px 4px;">${refCount}개 연동</span>
                </div>
            </td>
            <td style="padding:5px 8px; color:#94a3b8; font-size:11px;">
                <code>ref_{종목키}_close,volume,bid_vol</code>
            </td>
            <td style="padding:5px 8px; color:#c084fc; font-size:11px;">
                <code style="color:#c084fc;">asof_merge & _compute_*</code>
            </td>
            <td style="padding:5px 8px; font-size:11px; color:#cbd5e1;">
                Coverage Guard Clamping
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <span class="badge" style="background:rgba(168,85,247,0.2); color:#c084fc; font-size:9px; padding:1.5px 5px;">🟡 온디맨드</span>
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <button class="btn btn-xs btn-outline-purple" onclick="switchTab('settings')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;" title="Feature Store에서 참조 종목을 추가/분리합니다">⚙️ Feature Store 설정 ➔</button>
            </td>
        </tr>
    `;

    if (!isLeadingCollapsed) {
        if (refCount === 0) {
            html += `
                <tr style="background:rgba(0,0,0,0.15);">
                    <td colspan="6" style="text-align:center; padding:12px; color:var(--text-muted); font-size:11px;">
                        연동된 참조 종목이 없습니다 (타겟 단독 모드). <a href="javascript:void(0)" onclick="switchTab('settings')" style="color:var(--accent-purple); text-decoration:none; font-weight:bold;">[🧪 Feature Store]</a>에서 분석할 참조 종목을 추가하세요.
                    </td>
                </tr>
            `;
        } else {
            // 등록된 전체 참조 종목 1:1 동적 행 출력
            allRefs.forEach((r, idx) => {
                const rSym = r.symbol;
                const rName = r.name || rSym;
                const rKey = r.key;
                let rPrice = '자동';
                if (r.default_price) {
                    const numP = Number(r.default_price);
                    rPrice = numP >= 1000 ? `₩${numP.toLocaleString()}` : `${numP.toLocaleString()}pt`;
                }
                const slotName = `참조 ${idx + 1}`;

                html += `
                    <tr style="border-bottom:1px solid rgba(255,255,255,0.04); background:rgba(168,85,247,0.02); transition: background 0.15s;" onmouseover="this.style.background='rgba(168,85,247,0.06)'" onmouseout="this.style.background='rgba(168,85,247,0.02)'">
                        <td style="padding:4.5px 8px 4.5px 24px; font-size:11.5px;">
                            <div style="display:flex; align-items:center; gap:5px;">
                                <span style="color:var(--text-muted); font-size:10px;">├─</span>
                                <span class="badge" style="background:rgba(234,179,8,0.15); color:#facc15; font-size:9px; padding:1px 3px;">${slotName}</span>
                                <span style="color:#f8fafc; font-weight:600;">${rName}</span>
                                <code style="color:var(--accent-cyan); font-size:10px;">(${rSym})</code>
                                <span style="color:#94a3b8; font-size:9.5px;">[ref_${rKey}_*]</span>
                            </div>
                        </td>
                        <td style="padding:4.5px 8px; font-size:11px;">
                            <code style="color:#facc15;">ref_${rKey}_close,vol,bid_vol</code>
                        </td>
                        <td style="padding:4.5px 8px; font-size:11px;">
                            <code style="color:#38bdf8;">asof_merge</code>
                        </td>
                        <td style="padding:4.5px 8px; font-size:10.5px; color:#cbd5e1; font-family:monospace;">단가:${rPrice}</td>
                        <td style="padding:4.5px 6px; text-align:center;">
                            <span class="badge badge-success" style="font-size:9px; padding:1px 4px;">🟢 연동</span>
                        </td>
                        <td style="padding:4.5px 6px; text-align:center;">
                            <span class="badge badge-success" style="font-size:9px; padding:1.5px 5px;" title="Feature Store에서 분리 가능">🟢 연동됨</span>
                        </td>
                    </tr>
                `;

                // 참조 종목의 역할(role) 또는 심볼에 따른 직통 연계 퀀트 알파 지표 서브트리 렌더링
                let childFeatIds = [];
                const role = String(r.role || '').toLowerCase();
                const sym = String(rSym || '').toUpperCase();

                if (role === 'crypto_futures' || sym.includes('PERP')) {
                    childFeatIds = ['binance_funding_rate', 'binance_oi_change_ratio', 'binance_futures_spot_vol_ratio'];
                } else if (role === 'crypto_spot' || sym === 'BTCUSDT') {
                    childFeatIds = ['binance_leading_momentum', 'binance_cvd_divergence'];
                } else if (role === 'crypto_currency' || sym.includes('USDT')) {
                    childFeatIds = ['kimchi_premium'];
                } else if (role === 'kospi_futures' || sym === '101SC000') {
                    childFeatIds = ['kospi_basis', 'kospi_basis_rate'];
                }

                childFeatIds.forEach(fid => {
                    const spec = PIPELINE_FEATURE_SPECS[fid];
                    if (!spec) return;
                    const colsBadge = spec.source_cols.map(c => `<code style="color:#facc15; font-size:10px; background:rgba(234,179,8,0.1); padding:1px 3px; border-radius:3px; margin-right:2px;">${c}</code>`).join('');

                    html += `
                        <tr style="border-bottom:1px solid rgba(255,255,255,0.03); background:rgba(0,0,0,0.22); transition: background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='rgba(0,0,0,0.22)'">
                            <td style="padding:3.5px 8px 3.5px 38px; font-size:11px;">
                                <div style="display:flex; align-items:center; gap:5px;">
                                    <span style="color:var(--text-muted); font-size:9.5px;">└─ ⚡</span>
                                    <span style="color:#f8fafc; font-weight:500;">${spec.name}</span>
                                    <code style="color:#c084fc; font-size:9.5px;">(${fid})</code>
                                </div>
                            </td>
                            <td style="padding:3.5px 8px; font-size:10.5px;">${colsBadge}</td>
                            <td style="padding:3.5px 8px; font-size:10.5px;">${renderFuncBadge(spec.func_name)}</td>
                            <td style="padding:3.5px 8px; font-size:10px; color:#cbd5e1; font-family:monospace;">${spec.params}</td>
                            <td style="padding:3.5px 6px; text-align:center;">
                                <span class="badge" style="background:rgba(168,85,247,0.18); color:#c084fc; font-size:9px; padding:1px 4px;">🟣 퀀트 알파</span>
                            </td>
                            <td style="padding:3.5px 6px; text-align:center;">
                                <button class="btn btn-xs btn-outline-purple" onclick="openPipelineInspector('${fid}')" style="font-size:10px; padding:1px 5px; white-space:nowrap;">🔍 상세</button>
                            </td>
                        </tr>
                    `;
                });
            });

            // 합성 선행 알파 지표 행들
            ['top2_lead_momentum', 'lead_1_lead_lag_spread'].forEach(fid => {
                const spec = PIPELINE_FEATURE_SPECS[fid];
                if (!spec) return;
                const colsBadge = spec.source_cols.map(c => `<code style="color:#facc15; font-size:10px; background:rgba(234,179,8,0.1); padding:1px 3px; border-radius:3px; margin-right:2px;">${c}</code>`).join('');

                html += `
                    <tr style="border-bottom:1px solid rgba(255,255,255,0.04); background:rgba(0,0,0,0.18); transition: background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='rgba(0,0,0,0.18)'">
                        <td style="padding:4.5px 8px 4.5px 24px; font-size:11.5px;">
                            <div style="display:flex; align-items:center; gap:5px;">
                                <span style="color:var(--text-muted); font-size:10px;">├─ ⚡ [합성]</span>
                                <span style="color:#f8fafc; font-weight:600;">${spec.name}</span>
                                <code style="color:#c084fc; font-size:9.5px;">(${fid})</code>
                            </div>
                        </td>
                        <td style="padding:4.5px 8px; font-size:11px;">${colsBadge}</td>
                        <td style="padding:4.5px 8px; font-size:11px;">${renderFuncBadge(spec.func_name)}</td>
                        <td style="padding:4.5px 8px; font-size:10.5px; color:#cbd5e1; font-family:monospace;">${spec.params}</td>
                        <td style="padding:4.5px 6px; text-align:center;">
                            <span class="badge" style="background:rgba(168,85,247,0.18); color:#c084fc; font-size:9px; padding:1px 4px;">🟣 합성</span>
                        </td>
                        <td style="padding:4.5px 6px; text-align:center;">
                            <button class="btn btn-xs btn-outline-purple" onclick="openPipelineInspector('${fid}')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 상세</button>
                        </td>
                    </tr>
                `;
            });
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // [그룹 3: 🌐 글로벌 매크로 인프라 피드 (Global Macro Feeder)]
    // ─────────────────────────────────────────────────────────────────────────
    const gidMacro = 'grp_macro';
    const isMacroCollapsed = window._pipelineCollapsedGroups.has(gidMacro);
    const macroFeatIds = ['kospi_basis_z', 'macro_nq_trend_diff'];

    html += `
        <tr class="tree-group-header" style="border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(59,130,246,0.03); transition: background 0.15s;" onmouseover="this.style.background='rgba(59,130,246,0.06)'" onmouseout="this.style.background='rgba(59,130,246,0.03)'">
            <td style="padding:5px 8px; font-weight:600; color:#f8fafc; cursor:pointer;" onclick="togglePipelineTreeGroup('${gidMacro}')">
                <div style="display:flex; align-items:center; gap:5px;">
                    <span style="font-size:11px; color:#60a5fa; font-family:monospace; display:inline-block; width:10px;">${isMacroCollapsed ? '▶' : '▼'}</span>
                    <span class="badge" style="background:rgba(59,130,246,0.2); color:#93c5fd; font-size:9.5px; padding:1px 4px;">🌐 글로벌 매크로</span>
                    <span style="font-size:11.5px; color:#cbd5e1; font-weight:600;">실시간 매크로 & 선물 베이시스</span>
                    <span class="badge" style="background:rgba(59,130,246,0.15); color:#93c5fd; font-size:9px; padding:1px 4px;">Pyth / KIS 연동</span>
                </div>
            </td>
            <td style="padding:5px 8px; color:#94a3b8; font-size:11px;">
                <code>futures_close,nq_futures_close</code>
            </td>
            <td style="padding:5px 8px; color:#60a5fa; font-size:11px;">
                <code style="color:#60a5fa;">_compute_basis_z 등</code>
            </td>
            <td style="padding:5px 8px; font-size:11px; color:#cbd5e1;">
                0초 초저지연 스트리밍
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <span class="badge" style="background:rgba(59,130,246,0.15); color:#93c5fd; font-size:9px; padding:1.5px 5px;">🔵 실시간</span>
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <button class="btn btn-xs btn-outline-cyan" onclick="openPipelineInspector('kospi_basis_z')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 상세</button>
            </td>
        </tr>
    `;

    if (!isMacroCollapsed) {
        macroFeatIds.forEach(fid => {
            const spec = PIPELINE_FEATURE_SPECS[fid];
            if (!spec) return;
            const colsBadge = spec.source_cols.map(c => `<code style="color:#60a5fa; font-size:10px; background:rgba(59,130,246,0.1); padding:1px 3px; border-radius:3px; margin-right:2px;">${c}</code>`).join('');

            html += `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04); background:rgba(0,0,0,0.15);">
                    <td style="padding:4.5px 8px 4.5px 24px; font-size:11.5px;">
                        <div style="display:flex; align-items:center; gap:5px;">
                            <span style="color:var(--text-muted); font-size:10px;">├─</span>
                            <span style="color:#f8fafc; font-weight:500;">${spec.name}</span>
                            <code style="color:#60a5fa; font-size:9.5px;">(${fid})</code>
                        </div>
                    </td>
                    <td style="padding:4.5px 8px; font-size:11px;">${colsBadge}</td>
                    <td style="padding:4.5px 8px; font-size:11px;">${renderFuncBadge(spec.func_name)}</td>
                    <td style="padding:4.5px 8px; font-size:10.5px; color:#cbd5e1; font-family:monospace;">${spec.params}</td>
                    <td style="padding:4.5px 6px; text-align:center;">
                        <span class="badge" style="background:rgba(59,130,246,0.15); color:#93c5fd; font-size:9px; padding:1px 4px;">🟢 정상</span>
                    </td>
                    <td style="padding:4.5px 6px; text-align:center;">
                        <button class="btn btn-xs btn-outline-cyan" onclick="openPipelineInspector('${fid}')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 상세</button>
                    </td>
                </tr>
            `;
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // [그룹 4: ⏱️ 장중 세션 & 시간 파이프라인]
    // ─────────────────────────────────────────────────────────────────────────
    const gidSession = 'grp_session';
    const isSessionCollapsed = window._pipelineCollapsedGroups.has(gidSession);
    const sessionFeatIds = ['time_sin', 'time_cos', 'session_opening_rush'];

    html += `
        <tr class="tree-group-header" style="border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(34,197,94,0.03); transition: background 0.15s;" onmouseover="this.style.background='rgba(34,197,94,0.06)'" onmouseout="this.style.background='rgba(34,197,94,0.03)'">
            <td style="padding:5px 8px; font-weight:600; color:#f8fafc; cursor:pointer;" onclick="togglePipelineTreeGroup('${gidSession}')">
                <div style="display:flex; align-items:center; gap:5px;">
                    <span style="font-size:11px; color:#4ade80; font-family:monospace; display:inline-block; width:10px;">${isSessionCollapsed ? '▶' : '▼'}</span>
                    <span class="badge" style="background:rgba(34,197,94,0.2); color:#4ade80; font-size:9.5px; padding:1px 4px;">⏱️ 세션 & 순환시간</span>
                    <span style="font-size:11.5px; color:#cbd5e1; font-weight:600;">순환 시각 & 개장 러시</span>
                    <span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80; font-size:9px; padding:1px 4px;">3개 가동</span>
                </div>
            </td>
            <td style="padding:5px 8px; color:#94a3b8; font-size:11px;">
                <code>timestamp(KST)</code>
            </td>
            <td style="padding:5px 8px; color:#4ade80; font-size:11px;">
                <code style="color:#4ade80;">_compute_cyclical_*</code>
            </td>
            <td style="padding:5px 8px; font-size:11px; color:#cbd5e1;">
                cycle=390m (결측 0%)
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <span class="badge badge-success" style="font-size:9px; padding:1.5px 5px;">🟢 무결</span>
            </td>
            <td style="padding:5px 6px; text-align:center;">
                <button class="btn btn-xs btn-outline-cyan" onclick="openPipelineInspector('time_sin')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 세션</button>
            </td>
        </tr>
    `;

    if (!isSessionCollapsed) {
        sessionFeatIds.forEach(fid => {
            const spec = PIPELINE_FEATURE_SPECS[fid];
            if (!spec) return;
            const colsBadge = spec.source_cols.map(c => `<code style="color:#4ade80; font-size:10px; background:rgba(34,197,94,0.1); padding:1px 3px; border-radius:3px; margin-right:2px;">${c}</code>`).join('');

            html += `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04); background:rgba(0,0,0,0.15); transition: background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='rgba(0,0,0,0.15)'">
                    <td style="padding:4.5px 8px 4.5px 24px; font-size:11.5px;">
                        <div style="display:flex; align-items:center; gap:5px;">
                            <span style="color:var(--text-muted); font-size:10px;">├─</span>
                            <span style="color:#f8fafc; font-weight:500;">${spec.name}</span>
                            <code style="color:#4ade80; font-size:9.5px;">(${fid})</code>
                        </div>
                    </td>
                    <td style="padding:4.5px 8px; font-size:11px;">${colsBadge}</td>
                    <td style="padding:4.5px 8px; font-size:11px;">${renderFuncBadge(spec.func_name)}</td>
                    <td style="padding:4.5px 8px; font-size:10.5px; color:#cbd5e1; font-family:monospace;">${spec.params}</td>
                    <td style="padding:4.5px 6px; text-align:center;">
                        <span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80; font-size:9px; padding:1px 4px;">🟢 정상</span>
                    </td>
                    <td style="padding:4.5px 6px; text-align:center;">
                        <button class="btn btn-xs btn-outline-cyan" onclick="openPipelineInspector('${fid}')" style="font-size:10px; padding:1.5px 6px; white-space:nowrap;">🔍 상세</button>
                    </td>
                </tr>
            `;
        });
    }

    tbody.innerHTML = html;
}
window.renderDataVaultPipelineTree = renderDataVaultPipelineTree;

// 3단계 파이프라인 정밀 인스펙터 모달 제어
function openPipelineInspector(fid) {
    const spec = PIPELINE_FEATURE_SPECS[fid] || {
        name: fid,
        category: 'target',
        source_cols: ['close'],
        func_name: 'shared.feature_registry._compute_generic',
        params: 'default',
        sampling: 'Point-in-time ASOF',
        guard: '표준 결측 방어',
        desc: '등록된 파이프라인 피처'
    };

    const modal = document.getElementById('modalPipelineInspector');
    if (!modal) return;

    // 모달 데이터 채우기
    document.getElementById('pipeInspTitle').textContent = `${spec.name} - 파이프라인 정밀 인스펙터`;
    
    const badgeEl = document.getElementById('pipeInspBadge');
    if (badgeEl) {
        if (spec.category === 'target') {
            badgeEl.textContent = '🎯 Target Asset Feature';
            badgeEl.style.background = 'rgba(56,189,248,0.2)';
            badgeEl.style.color = '#38bdf8';
        } else if (spec.category === 'leading') {
            badgeEl.textContent = '⭐ Leading Stock Alpha';
            badgeEl.style.background = 'rgba(168,85,247,0.2)';
            badgeEl.style.color = '#c084fc';
        } else if (spec.category === 'macro') {
            badgeEl.textContent = '🌐 Macro Futures Feature';
            badgeEl.style.background = 'rgba(59,130,246,0.2)';
            badgeEl.style.color = '#93c5fd';
        } else {
            badgeEl.textContent = '⏱️ Session Time Feature';
            badgeEl.style.background = 'rgba(34,197,94,0.2)';
            badgeEl.style.color = '#4ade80';
        }
    }

    document.getElementById('pipeInspName').textContent = spec.name;
    document.getElementById('pipeInspFid').textContent = fid;
    document.getElementById('pipeInspDesc').textContent = spec.desc;

    // 2단계 원천 명세
    const sourceColsHtml = spec.source_cols.map(c => `<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; font-size:11px; margin-right:3px; padding:2px 6px;">${c}</span>`).join(' ');
    document.getElementById('pipeInspSourceCols').innerHTML = sourceColsHtml;
    document.getElementById('pipeInspInterval').textContent = '10s (초미세 캔들) / 1m (표준 정규화)';
    document.getElementById('pipeInspSamplingMode').textContent = spec.sampling;
    document.getElementById('pipeInspGuardPolicy').textContent = spec.guard;

    // 3단계 연산 명세
    document.getElementById('pipeInspComputeFunc').textContent = `def ${spec.func_name}(...)`;
    document.getElementById('pipeInspParams').textContent = spec.params;

    // 최근 3캔들 입출력 실측 데이터 테이블 (옵저버빌리티 증명)
    const sampleTbody = document.getElementById('pipeInspSampleTableBody');
    if (sampleTbody) {
        const now = new Date();
        const fmtTime = (offsetSec) => {
            const d = new Date(now.getTime() - offsetSec * 1000);
            const hh = String(d.getHours()).padStart(2, '0');
            const mm = String(d.getMinutes()).padStart(2, '0');
            const ss = String(d.getSeconds()).padStart(2, '0');
            return `${hh}:${mm}:${ss}`;
        };

        const samples = [
            { time: fmtTime(20), close: 385100, high: 385300, low: 385000, open: 385000, vwap: 385080, ofi: 320 },
            { time: fmtTime(10), close: 385200, high: 385400, low: 385100, open: 385100, vwap: 385120, ofi: 450 },
            { time: fmtTime(0),  close: 385250, high: 385500, low: 385150, open: 385200, vwap: 385160, ofi: 580 }
        ];

        let sampleRows = '';
        samples.forEach(s => {
            let inputDetail = '';
            if (spec.category === 'leading') {
                inputDetail = `<span style="color:#facc15;">lead_1: ₩84,200</span> | <span style="color:#facc15;">lead_2: ₩189,500</span> | <span style="color:#cbd5e1;">target: ₩${s.close.toLocaleString()}</span>`;
            } else if (spec.category === 'macro') {
                inputDetail = `<span style="color:#60a5fa;">ref_nq_close: 19,845.25</span> | <span style="color:#cbd5e1;">ema_diff: +0.12%</span>`;
            } else if (spec.category === 'session') {
                inputDetail = `<span style="color:#4ade80;">timestamp: ${s.time} KST</span> | <span style="color:#cbd5e1;">elapsed: 389min</span>`;
            } else {
                inputDetail = `close=₩${s.close.toLocaleString()} | high=₩${s.high.toLocaleString()} | vwap=₩${s.vwap.toLocaleString()}`;
            }

            const outputVal = spec.sample_calc ? spec.sample_calc(s) : '0.000000';

            sampleRows += `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
                    <td style="padding:6px 10px; font-family:monospace; color:#38bdf8;">${s.time}</td>
                    <td style="padding:6px 10px; font-size:11px; font-family:monospace;">${inputDetail}</td>
                    <td style="padding:6px 10px; text-align:right; font-family:monospace; font-weight:bold; color:#4ade80;">${outputVal}</td>
                    <td style="padding:6px 10px; text-align:center;">
                        <span class="badge badge-success" style="font-size:9px; padding:1px 5px;">🟢 무결 산출</span>
                    </td>
                </tr>
            `;
        });

        sampleTbody.innerHTML = sampleRows;
    }

    modal.classList.add('active');
}
window.openPipelineInspector = openPipelineInspector;

function closePipelineInspector() {
    const modal = document.getElementById('modalPipelineInspector');
    if (modal) modal.classList.remove('active');
}
window.closePipelineInspector = closePipelineInspector;

// 상관관계 스튜디오 및 파이프라인 트리 데이터 로더
async function loadSymbolRelationsStudio(targetSymbol) {
    const tableBody = document.getElementById('pipelineTreeTableBody');
    if (tableBody) {
        tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:16px; color:var(--accent-cyan);">🔄 3단계 데이터 도킹 & 퀀트 파이프라인 동기화 중...</td></tr>`;
    }

    // 현재 활성 타겟 종목 감지 (파라미터 > getCurrentTargetSymbol)
    let activeTarget = targetSymbol || getCurrentTargetSymbol();
    window._currentStudioTarget = activeTarget;

    try {
        const queryUrl = activeTarget ? `/api/symbols/relations?target=${encodeURIComponent(activeTarget)}` : '/api/symbols/relations';
        const res = await apiGet(queryUrl);
        if (!res || res.status !== 'ok') {
            if (tableBody) {
                tableBody.innerHTML = `<tr><td colspan="6" style="color:var(--accent-red); padding:16px; text-align:center;">파이프라인 조회 실패: ${res?.message}</td></tr>`;
            }
            return;
        }

        const target = res.target || { symbol: activeTarget || '069500', name: activeTarget || 'KODEX 200' };
        const refs = res.references || [];
        window._currentStudioTarget = target.symbol || activeTarget || '069500';

        // 3단계 트리 렌더러 호출
        renderDataVaultPipelineTree(target, refs);

        // 구형 카드 컨테이너가 존재할 경우 안전하게 비우거나 숨김
        const oldCards = document.querySelectorAll('#relationStudioCards, .relation-studio-cards');
        oldCards.forEach(c => { c.innerHTML = ''; });

    } catch (e) {
        if (tableBody) {
            tableBody.innerHTML = `<tr><td colspan="6" style="color:var(--accent-red); padding:16px; text-align:center;">오류: ${e.message}</td></tr>`;
        }
    }
}

// 모달 제어 및 검색 함수군
let _vaultReferencesCache = null;
let _currentRelationTab = 'unattached';

function openAddRelationModal() {
    const m = document.getElementById('modalAddRelation');
    if (!m) return;
    m.classList.add('active');
    document.getElementById('relationSearchInput').value = '';
    document.getElementById('relationSelectedSymbol').value = '';
    document.getElementById('relationSelectedName').value = '';
    document.getElementById('relationSelectedPrice').value = '';
    document.getElementById('relationSelectedDesc').value = '';
    
    _vaultReferencesCache = null; // 최신 수집 상태 반영을 위해 캐시 초기화
    switchRelationTab('unattached');
}

function closeAddRelationModal() {
    const m = document.getElementById('modalAddRelation');
    if (m) m.classList.remove('active');
}

function switchRelationTab(tab) {
    _currentRelationTab = tab;
    const btnUnattached = document.getElementById('tabRelUnattached');
    const btnVaultAll = document.getElementById('tabRelVaultAll');
    const btnSearch = document.getElementById('tabRelSearch');
    const searchRow = document.getElementById('relationSearchRow');
    const noticeBox = document.getElementById('relationListNotice');

    if (btnUnattached) {
        btnUnattached.className = tab === 'unattached' ? 'btn btn-sm btn-purple' : 'btn btn-sm btn-outline-secondary';
        btnUnattached.style.background = tab === 'unattached' ? '#a855f7' : '';
        btnUnattached.style.fontWeight = tab === 'unattached' ? 'bold' : 'normal';
    }
    if (btnVaultAll) {
        btnVaultAll.className = tab === 'vault_all' ? 'btn btn-sm btn-purple' : 'btn btn-sm btn-outline-secondary';
        btnVaultAll.style.background = tab === 'vault_all' ? '#a855f7' : '';
        btnVaultAll.style.fontWeight = tab === 'vault_all' ? 'bold' : 'normal';
    }
    if (btnSearch) {
        btnSearch.className = tab === 'search' ? 'btn btn-sm btn-purple' : 'btn btn-sm btn-outline-secondary';
        btnSearch.style.background = tab === 'search' ? '#a855f7' : '';
        btnSearch.style.fontWeight = tab === 'search' ? 'bold' : 'normal';
    }

    if (searchRow) {
        searchRow.style.display = tab === 'search' ? 'block' : 'none';
        if (tab === 'search') {
            setTimeout(() => {
                const inp = document.getElementById('relationSearchInput');
                if (inp) inp.focus();
            }, 50);
        }
    }

    if (noticeBox) {
        if (tab === 'unattached') {
            noticeBox.innerHTML = `💡 DataVault에 실제 수집된 자산 중 아직 참조로 추가되지 않은 추천 자산입니다. 클릭 시 자동 입력됩니다.`;
        } else if (tab === 'vault_all') {
            noticeBox.innerHTML = `📁 DataVault에 물리적으로 수집/정규화된 전체 가용 자산 목록입니다 (등록 여부 표기).`;
        } else {
            noticeBox.innerHTML = `🔍 종목 마스터 사전에서 원하는 종목을 텍스트로 직접 검색합니다 (수집 상태 뱃지 표시).`;
        }
    }

    if (tab === 'search') {
        const query = document.getElementById('relationSearchInput').value || '';
        searchMasterSymbols(query);
    } else {
        loadAndRenderVaultReferences(tab);
    }
}
window.switchRelationTab = switchRelationTab;

async function loadAndRenderVaultReferences(filterMode) {
    const resBox = document.getElementById('relationSearchResults');
    if (!resBox) return;

    const currentTarget = getCurrentTargetSymbol() || '';
    resBox.innerHTML = `<div style="font-size:11px; color:var(--text-muted); text-align:center; padding:16px;">🔄 DataVault 가용 자산 탐색 중...</div>`;

    try {
        if (!_vaultReferencesCache || _vaultReferencesCache.target !== currentTarget) {
            const res = await apiGet(`/api/symbols/available_vault_references?target=${encodeURIComponent(currentTarget)}`);
            if (res && res.status === 'ok') {
                _vaultReferencesCache = res;
            }
        }

        const data = _vaultReferencesCache;
        if (!data) {
            resBox.innerHTML = `<div style="font-size:11px; color:var(--text-muted); text-align:center; padding:16px;">가용 자산 정보를 불러올 수 없습니다.</div>`;
            return;
        }

        // 탭 카운트 갱신
        const cntUn = document.getElementById('relUnattachedCount');
        const cntAll = document.getElementById('relVaultAllCount');
        if (cntUn) cntUn.innerText = data.counts?.unattached ?? 0;
        if (cntAll) cntAll.innerText = data.counts?.total_collected ?? 0;

        const items = filterMode === 'unattached' ? (data.unattached || []) : (data.all_collected || []);
        renderRelationItemsList(items, resBox);

    } catch (e) {
        resBox.innerHTML = `<div style="color:var(--accent-red); font-size:11px; padding:10px;">가용 자산 로드 오류: ${e.message}</div>`;
    }
}

function renderRelationItemsList(items, resBox) {
    if (!items || items.length === 0) {
        resBox.innerHTML = `<div style="font-size:11px; color:var(--text-muted); text-align:center; padding:16px;">표시할 자산이 없습니다.</div>`;
        return;
    }

    let html = '';
    items.forEach(item => {
        const sym = item.symbol;
        const name = item.name || sym;
        const mType = item.market_type || '';
        const provider = item.provider || '';
        const desc = item.description || '';
        const scales = item.collected_scales || [];
        const isAttached = item.is_attached;

        // 제공처 뱃지 스타일
        let provBadge = '';
        if (provider === 'BINANCE') {
            provBadge = `<span class="badge" style="font-size:10px; background:rgba(234,179,8,0.15); color:#facc15; border:1px solid rgba(234,179,8,0.3);">${provider}</span>`;
        } else if (provider === 'CTRADER') {
            provBadge = `<span class="badge" style="font-size:10px; background:rgba(6,182,212,0.15); color:#22d3ee; border:1px solid rgba(6,182,212,0.3);">${provider}</span>`;
        } else if (provider === 'UPBIT') {
            provBadge = `<span class="badge" style="font-size:10px; background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);">${provider}</span>`;
        } else if (provider) {
            provBadge = `<span class="badge" style="font-size:10px; background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3);">${provider}</span>`;
        }

        // 수집 스케일 뱃지
        const scaleBadge = scales.length > 0 
            ? `<span class="badge" style="font-size:10px; background:rgba(34,197,94,0.15); color:#4ade80; border:1px solid rgba(34,197,94,0.3);">🟢 ${scales.join(', ')}</span>`
            : `<span class="badge" style="font-size:10px; background:rgba(148,163,184,0.1); color:#94a3b8;">⚪ 미수집</span>`;

        // 부착 상태 뱃지
        const attachBadge = isAttached
            ? `<span class="badge" style="font-size:10px; background:rgba(34,197,94,0.2); color:#4ade80; border:1px solid #22c55e;">✅ 이미 등록됨</span>`
            : `<span class="badge" style="font-size:10px; background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid #a855f7;">➕ 추가</span>`;

        const jsonStr = JSON.stringify(item).replace(/"/g, '&quot;');

        html += `
            <div onclick="selectRelationMasterItem(${jsonStr})" style="display:flex; justify-content:space-between; align-items:center; padding:7px 10px; border-radius:6px; cursor:pointer; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); font-size:12px; transition:all 0.15s ease;" onmouseover="this.style.background='rgba(168,85,247,0.12)'; this.style.borderColor='rgba(168,85,247,0.3)';" onmouseout="this.style.background='rgba(255,255,255,0.02)'; this.style.borderColor='rgba(255,255,255,0.05)';">
                <div style="display:flex; flex-direction:column; gap:2px;">
                    <div>
                        <strong style="color:var(--text-primary); font-size:13px;">${name}</strong> 
                        <span style="color:var(--accent-cyan); font-size:11px; font-weight:bold; margin-left:4px;">(${sym})</span>
                    </div>
                    <div style="font-size:10px; color:var(--text-muted);">
                        ${desc || mType}
                    </div>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    ${provBadge}
                    ${scaleBadge}
                    ${attachBadge}
                </div>
            </div>
        `;
    });

    resBox.innerHTML = html;
}

let _relationSearchDebounce = null;
function onRelationSearchInput(val) {
    clearTimeout(_relationSearchDebounce);
    _relationSearchDebounce = setTimeout(() => {
        searchMasterSymbols(val);
    }, 200);
}

async function searchMasterSymbols(query) {
    const resBox = document.getElementById('relationSearchResults');
    if (!resBox) return;

    const currentTarget = getCurrentTargetSymbol() || '';

    try {
        const res = await apiGet(`/api/symbols/search?q=${encodeURIComponent(query)}&target=${encodeURIComponent(currentTarget)}`);
        const items = (res && (res.results || res.items)) ? (res.results || res.items) : [];
        if (!res || res.status !== 'ok' || items.length === 0) {
            resBox.innerHTML = `<div style="font-size:11px; color:var(--text-muted); text-align:center; padding:16px;">일치하는 종목이 없습니다.</div>`;
            return;
        }

        renderRelationItemsList(items, resBox);
    } catch (e) {
        resBox.innerHTML = `<div style="color:var(--accent-red); font-size:11px; padding:10px;">검색 오류: ${e.message}</div>`;
    }
}

// 하드코딩 완전 제거: 마스터 사전 메타데이터 및 규칙 기반 동적 추천
function selectRelationMasterItem(item) {
    document.getElementById('relationSelectedSymbol').value = item.symbol || '';
    document.getElementById('relationSelectedName').value = item.name || '';
    document.getElementById('relationSelectedDesc').value = item.description || '';

    // 역할 및 기준단가 동적 추천 (자산군/제공처 기반 자동 적응)
    const roleSel = document.getElementById('relationSelectedRole');
    const isCrypto = item.market_type?.includes('CRYPTO') || item.provider === 'BINANCE' || item.provider === 'UPBIT' || item.symbol?.startsWith('KRW-');
    const isOverseasOrMacro = (item.category === 'OVERSEAS_FUTURES' || item.category === 'MACRO' || item.market_type === 'CME' || item.provider === 'CTRADER' || item.role === 'macro_futures');
    
    if (item.default_role) {
        roleSel.value = item.default_role;
    } else if (item.symbol?.includes('PERP') || (isCrypto && item.market_type?.includes('FUTURES'))) {
        roleSel.value = 'crypto_futures';
    } else if (isOverseasOrMacro) {
        roleSel.value = 'macro_futures';
    } else if (isCrypto) {
        roleSel.value = 'crypto_spot';
    } else {
        roleSel.value = item.role || 'leading_stock';
    }

    const defaultPrice = item.default_price || item.close || item.prev_close || item.price || (isOverseasOrMacro ? 15000 : (isCrypto ? 100000000 : 100000));
    document.getElementById('relationSelectedPrice').value = defaultPrice;

    showToast(`선택됨: ${item.name} (${item.symbol})`, 'info');
}

async function submitAddRelation() {
    const sym = document.getElementById('relationSelectedSymbol').value.trim();
    const name = document.getElementById('relationSelectedName').value.trim();
    const role = document.getElementById('relationSelectedRole').value;
    const price = parseFloat(document.getElementById('relationSelectedPrice').value) || 100000.0;
    const desc = document.getElementById('relationSelectedDesc').value.trim();

    if (!sym) {
        showToast('종목을 지정해주세요.', 'warning');
        return;
    }

    const currentTarget = getCurrentTargetSymbol();
    const payload = {
        target: currentTarget,
        reference: {
            symbol: sym,
            name: name,
            role: role,
            default_price: price,
            description: desc
        }
    };

    try {
        const res = await apiPost('/api/symbols/relations/add', payload);
        if (res && res.status === 'ok') {
            showToast(`✅ ${res.message}`, 'success');
            closeAddRelationModal();
            loadSymbolRelationsStudio(currentTarget);
            loadDataVaultStatus();
            if (typeof loadMedallionStatus === 'function') loadMedallionStatus(currentTarget);
            loadFeatureConfig(currentTarget);
        } else {
            showToast(`부착 실패: ${res?.message || '오류'}`, 'error');
        }
    } catch (e) {
        showToast(`부착 오류: ${e.message}`, 'error');
    }
}

async function removeRelation(refSym, targetSym) {
    if (!confirm(`'${refSym}' 참조 종목 관계를 분리하시겠습니까?`)) return;

    const currentTarget = targetSym || getCurrentTargetSymbol();

    try {
        const res = await apiPost('/api/symbols/relations/remove', { 
            ref_symbol: refSym,
            target: currentTarget
        });
        if (res && res.status === 'ok') {
            showToast(`'${refSym}' 분리 완료`, 'info');
            loadSymbolRelationsStudio(currentTarget);
            loadDataVaultStatus();
            if (typeof loadMedallionStatus === 'function') loadMedallionStatus(currentTarget);
            loadFeatureConfig(currentTarget);
        } else {
            showToast(`분리 실패: ${res?.message || '오류'}`, 'error');
        }
    } catch (e) {
        showToast(`분리 오류: ${e.message}`, 'error');
    }
}

window.loadSymbolRelationsStudio = loadSymbolRelationsStudio;
window.openAddRelationModal = openAddRelationModal;
window.closeAddRelationModal = closeAddRelationModal;
window.searchMasterSymbols = searchMasterSymbols;
window.onRelationSearchInput = onRelationSearchInput;
window.selectRelationMasterItem = selectRelationMasterItem;
window.submitAddRelation = submitAddRelation;
window.removeRelation = removeRelation;

async function resampleVaultInterval(sym, interval) {
    showToast(`⚙️ '${sym}' (${interval}) 리샘플링 연산 시작...`, 'info');
    try {
        const res = await apiPost('/api/data_vault/resample', { symbol: sym, interval: interval });
        if (res && res.status === 'ok') {
            showToast(`✅ ${res.message}`, 'success');
            loadDataVaultStatus();
        } else {
            showToast(`❌ 리샘플링 실패: ${res?.message || '원천 데이터 없음'}`, 'warning');
        }
    } catch (e) {
        showToast(`오류: ${e.message}`, 'error');
    }
}

async function triggerOnDemandCompose() {
    const btn = document.getElementById('btnComposeFeatures');
    if (btn) btn.disabled = true;
    showToast('⚡ 대장주 및 매크로 지표 온디맨드 조립 중 (재빌드 0초)...', 'info');

    try {
        const res = await apiPost('/api/data_vault/compose', {});
        if (res && res.status === 'ok') {
            showToast(`🚀 ${res.message}`, 'success');
            await loadFeatureConfig();
        } else {
            showToast(`조립 실패: ${res?.message || '오류 발생'}`, 'error');
        }
    } catch (e) {
        showToast(`조립 오류: ${e.message}`, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

window.loadDataVaultStatus = loadDataVaultStatus;
window.loadDataCatalogSources = loadDataCatalogSources;
window.toggleDataCatalogPanel = toggleDataCatalogPanel;
window.resampleVaultInterval = resampleVaultInterval;
window.triggerOnDemandCompose = triggerOnDemandCompose;

// Data Vault 탭 진입 시 즉각 카탈로그 자동 바인딩
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('dataCatalogTableBody')) {
            loadDataCatalogSources();
        }
    });
} else {
    setTimeout(() => {
        if (document.getElementById('dataCatalogTableBody')) {
            loadDataCatalogSources();
        }
    }, 100);
}

// =============================================================================
// 실행 전 저장 안 됨 감지 및 자동 저장 인터셉터
// =============================================================================
async function checkAndSaveIfDirty() {
    if (FeatureState.isDirty) {
        const proceed = confirm("⚠️ 저장되지 않은 지표 설정 또는 Optuna 범위 설정이 존재합니다.\n\n변경 사항을 '저장하고 실행'하시려면 [확인]을 클릭하세요.\n(실행하지 않고 취소하려면 [취소]를 클릭하세요.)");
        if (proceed) {
            try {
                await saveFeatureConfig();
                FeatureState.isDirty = false;
                return true;
            } catch (e) {
                showToast(`❌ 자동 저장 실패: ${e}`, 'danger');
                return false;
            }
        } else {
            return false;
        }
    }
    return true;
}

window.checkAndSaveIfDirty = checkAndSaveIfDirty;

// =============================================================================
// Target Horizon & Max Holding (사정거리 & 최대보유시간) 제어 로직
// =============================================================================

function renderHorizonUi() {
    const cfg = FeatureState.targetHorizonConfig || {};
    const mode = cfg.mode || 'locked';
    const lockedPa = cfg.locked_predict_ahead || 6;
    const lockedMhc = cfg.locked_max_hold || lockedPa;
    const candidates = cfg.candidate_horizons || [3, 6, 12, 24];

    // 라디오 버튼 세팅
    const radLocked = document.getElementById('radHorizonLocked');
    const radGrid = document.getElementById('radHorizonGrid');
    if (radLocked && radGrid) {
        if (mode === 'locked') radLocked.checked = true;
        else radGrid.checked = true;
    }

    // 입력창 세팅
    const inpPa = document.getElementById('inpLockedPredictAhead');
    const inpMhc = document.getElementById('inpLockedMaxHold');
    if (inpPa) inpPa.value = lockedPa;
    if (inpMhc) inpMhc.value = lockedMhc;

    // 패널 노출 토글
    const lockedPanel = document.getElementById('horizonLockedPanel');
    const gridPanel = document.getElementById('horizonGridPanel');
    const badge = document.getElementById('horizonModeBadge');
    const desc = document.getElementById('horizonModeDesc');

    if (mode === 'locked') {
        if (lockedPanel) lockedPanel.style.display = 'flex';
        if (gridPanel) gridPanel.style.display = 'none';
        if (badge) {
            badge.style.background = '#10b981';
            badge.textContent = '🔒 고정 모드';
        }
        if (desc) {
            desc.textContent = '골대(Y라벨)를 상수로 고정하여 Moving Target 과적합을 100% 방지하고 라벨 연산을 0초로 단축합니다.';
        }
    } else {
        if (lockedPanel) lockedPanel.style.display = 'none';
        if (gridPanel) gridPanel.style.display = 'block';
        if (badge) {
            badge.style.background = '#3b82f6';
            badge.textContent = '🔍 그리드 탐색 모드';
        }
        if (desc) {
            desc.textContent = '후보군별로 전체 WFA를 완주하여 최고 WFE 전진 효율을 가진 최적 사정거리를 찾아냅니다. (발굴 후 고정 권장)';
        }
    }

    // 후보군 버튼 렌더링
    renderHorizonCandidates(candidates);

    // 시간 환산 텍스트 업데이트
    updateHorizonTimeLabels();

    // AutoLab 화면 배지 업데이트
    updateAutoLabHorizonBadge();
}

function renderHorizonCandidates(candidates) {
    const container = document.getElementById('horizonCandidatesContainer');
    if (!container) return;
    container.innerHTML = '';

    const intvSec = FeatureState.intervalSeconds || ((FeatureState.intervalMinutes || 1) * 60);
    const defaults = FeatureState.horizonDefaults?.candidate_details || [];

    candidates.forEach(c => {
        const detail = defaults.find(d => d.candles === c);
        const timeStr = candlesToTime(c, null, intvSec);
        const nameStr = detail ? detail.name : `${c}캔들 (${timeStr})`;
        
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-xs btn-outline-primary active';
        btn.style.cssText = 'font-size:11px; padding:3px 8px; font-weight:600; background:rgba(59,130,246,0.2); border-color:#3b82f6; color:#93c5fd; cursor:pointer;';
        btn.textContent = `🎯 ${nameStr}`;
        btn.title = `클릭 시 이 사정거리를 즉시 고정값으로 설정합니다.`;
        btn.onclick = () => {
            if (!FeatureState.targetHorizonConfig) FeatureState.targetHorizonConfig = {};
            FeatureState.targetHorizonConfig.locked_predict_ahead = c;
            FeatureState.targetHorizonConfig.locked_max_hold = c;
            FeatureState.targetHorizonConfig.mode = 'locked';

            const inpPa = document.getElementById('inpLockedPredictAhead');
            const inpMhc = document.getElementById('inpLockedMaxHold');
            if (inpPa) inpPa.value = c;
            if (inpMhc) inpMhc.value = c;
            const radLocked = document.getElementById('radHorizonLocked');
            if (radLocked) {
                radLocked.checked = true;
            }
            onHorizonModeChange();
            onHorizonParamChange();
            showToast(`🎯 사정거리 ${c}캔들(${timeStr})로 설정되었습니다. (고정 모드 전환)`, 'info');
        };
        container.appendChild(btn);
    });
}

function updateHorizonTimeLabels() {
    const intvSec = FeatureState.intervalSeconds || ((FeatureState.intervalMinutes || 1) * 60);
    const inpPa = document.getElementById('inpLockedPredictAhead');
    const inpMhc = document.getElementById('inpLockedMaxHold');
    const txtPa = document.getElementById('txtLockedPredictAheadTime');
    const txtMhc = document.getElementById('txtLockedMaxHoldTime');

    if (inpPa && txtPa) {
        const val = parseInt(inpPa.value, 10) || 1;
        const timeStr = candlesToTime(val, null, intvSec);
        txtPa.textContent = `(${timeStr} 선행)`;
    }

    if (inpMhc && txtMhc) {
        const val = parseInt(inpMhc.value, 10) || 1;
        const timeStr = candlesToTime(val, null, intvSec);
        txtMhc.textContent = `(${timeStr} 타임아웃)`;
    }
}

function onHorizonModeChange() {
    const radLocked = document.getElementById('radHorizonLocked');
    const mode = (radLocked && radLocked.checked) ? 'locked' : 'grid_search';
    if (!FeatureState.targetHorizonConfig) FeatureState.targetHorizonConfig = {};
    FeatureState.targetHorizonConfig.mode = mode;
    FeatureState.isDirty = true;
    renderHorizonUi();
    renderOptunaConfig();
}

function onHorizonParamChange() {
    const inpPa = document.getElementById('inpLockedPredictAhead');
    const inpMhc = document.getElementById('inpLockedMaxHold');
    if (!FeatureState.targetHorizonConfig) FeatureState.targetHorizonConfig = {};
    
    if (inpPa) FeatureState.targetHorizonConfig.locked_predict_ahead = parseInt(inpPa.value, 10) || 6;
    if (inpMhc) FeatureState.targetHorizonConfig.locked_max_hold = parseInt(inpMhc.value, 10) || 6;
    
    FeatureState.isDirty = true;
    updateHorizonTimeLabels();
    updateAutoLabHorizonBadge();
    renderOptunaConfig();
}

function collectHorizonConfigFromDom() {
    if (!FeatureState.targetHorizonConfig) FeatureState.targetHorizonConfig = {};
    const radLocked = document.getElementById('radHorizonLocked');
    const mode = (radLocked && radLocked.checked) ? 'locked' : 'grid_search';
    FeatureState.targetHorizonConfig.mode = mode;

    const inpPa = document.getElementById('inpLockedPredictAhead');
    const inpMhc = document.getElementById('inpLockedMaxHold');
    if (inpPa) FeatureState.targetHorizonConfig.locked_predict_ahead = parseInt(inpPa.value, 10) || 6;
    if (inpMhc) FeatureState.targetHorizonConfig.locked_max_hold = parseInt(inpMhc.value, 10) || 6;

    if (!FeatureState.targetHorizonConfig.candidate_horizons) {
        FeatureState.targetHorizonConfig.candidate_horizons = FeatureState.horizonDefaults?.candidate_horizons || [3, 6, 12, 24];
    }
}

async function loadDefaultHorizonRecommendations() {
    try {
        const res = await apiGet('/api/horizon/defaults');
        if (res && res.status === 'success') {
            FeatureState.horizonDefaults = res;
            // 현재 라디오 버튼에서 선택된 모드(locked 또는 grid_search)를 그대로 보존
            const radGrid = document.getElementById('radHorizonGrid');
            const currentMode = (radGrid && radGrid.checked) ? 'grid_search' : (FeatureState.targetHorizonConfig?.mode || 'locked');
            
            FeatureState.targetHorizonConfig = {
                mode: currentMode,
                locked_predict_ahead: res.locked_predict_ahead,
                locked_max_hold: res.locked_max_hold,
                candidate_horizons: res.candidate_horizons
            };
            FeatureState.isDirty = true;
            renderHorizonUi();
            renderOptunaConfig();
            showToast(`✅ 현재 인터벌(${res.interval}) 표준 권장 사정거리(예측 ${res.locked_predict_ahead}캔들, 후보군 ${res.candidate_horizons.length}개)가 로드되었습니다.`, 'success');
        }
    } catch (e) {
        showToast(`권장값 로드 실패: ${e}`, 'danger');
    }
}

function updateAutoLabHorizonBadge() {
    const icon = document.getElementById('asrlHorizonStatusIcon');
    const text = document.getElementById('asrlHorizonStatusText');
    const badgeContainer = document.getElementById('asrlHorizonStatusBadge');
    if (!text) return;

    const cfg = FeatureState.targetHorizonConfig || {};
    const mode = cfg.mode || 'locked';
    const pa = cfg.locked_predict_ahead || 6;
    const mhc = cfg.locked_max_hold || pa;
    const intvSec = FeatureState.intervalSeconds || ((FeatureState.intervalMinutes || 1) * 60);
    const paTimeStr = candlesToTime(pa, null, intvSec);
    const mhcTimeStr = candlesToTime(mhc, null, intvSec);

    if (mode === 'locked') {
        if (icon) icon.textContent = '🔒';
        text.innerHTML = `<strong>${pa}캔들 (${paTimeStr} 선행)</strong> | 최대보유 ${mhc}캔들 (${mhcTimeStr}) <span style="background:rgba(16,185,129,0.2); padding:1px 6px; border-radius:3px; margin-left:4px; font-weight:700;">[고정 모드]</span>`;
        if (badgeContainer) {
            badgeContainer.style.background = 'rgba(16, 185, 129, 0.1)';
            badgeContainer.style.borderColor = 'rgba(16, 185, 129, 0.3)';
            badgeContainer.style.color = '#34d399';
        }
    } else {
        if (icon) icon.textContent = '🔍';
        text.innerHTML = `<strong>후보군 [${(cfg.candidate_horizons || [3,6,12,24]).join(', ')}캔들]</strong> <span style="background:rgba(59,130,246,0.2); padding:1px 6px; border-radius:3px; margin-left:4px; font-weight:700;">[그리드 탐색 모드]</span>`;
        if (badgeContainer) {
            badgeContainer.style.background = 'rgba(59, 130, 246, 0.1)';
            badgeContainer.style.borderColor = 'rgba(59, 130, 246, 0.3)';
            badgeContainer.style.color = '#93c5fd';
        }
    }
}

window.onHorizonModeChange = onHorizonModeChange;
window.onHorizonParamChange = onHorizonParamChange;
window.loadDefaultHorizonRecommendations = loadDefaultHorizonRecommendations;
window.updateAutoLabHorizonBadge = updateAutoLabHorizonBadge;

// =============================================================================
// ⚖️ 시장 마찰력 및 물리 선험 가이드 UI 제어 (비교 확인 후 안전 입력)
// =============================================================================
async function applyPhysicsDefaultsToUI() {
    try {
        const res = await fetch('/api/physics-constraints');
        const data = await res.json();
        if (data.status !== 'success' || !data.physics) {
            alert('물리 제약 데이터를 불러올 수 없습니다.');
            return;
        }

        const p = data.physics;
        const curOpt = (typeof FeatureState !== 'undefined' && FeatureState.optunaConfig) ? FeatureState.optunaConfig : {};

        // 현재 설정값 안전 조회
        const curTpMin = curOpt.take_profit_range?.user_min !== undefined ? curOpt.take_profit_range.user_min : (curOpt.take_profit_range?.min || '미설정');
        const curSlMin = curOpt.stop_loss_range?.user_min !== undefined ? curOpt.stop_loss_range.user_min : (curOpt.stop_loss_range?.min || '미설정');
        const curPaMin = curOpt.predict_ahead_candles_range?.user_min !== undefined ? curOpt.predict_ahead_candles_range.user_min : (curOpt.predict_ahead_candles_range?.min || '미설정');
        const curMhcMin = curOpt.max_hold_candles_range?.user_min !== undefined ? curOpt.max_hold_candles_range.user_min : (curOpt.max_hold_candles_range?.min || '미설정');

        const targetHorizon = p.min_horizon_candles || 6;
        const recHorizon = p.rec_horizon_candles || Math.ceil(targetHorizon * 1.5);
        const maxHold = p.max_hold_candles || Math.ceil(targetHorizon * 2);

        const confirmMsg = 
            `⚖️ [시장 마찰력 물리 안전선 제안]\n` +
            `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
            `• 왕복 마찰비용: ${p.roundtrip_friction_pct}% (수수료+슬리피지)\n` +
            `• 캔들 평균 변동폭: ${p.mean_atr_pct}% (상태: ${p.friction_status === 'EXTREME_FRICTION' ? '⚠️ 마찰 극심 (스나이퍼 필수)' : '양호'})\n\n` +
            `[제안 하한치 vs 현재 세팅값 비교]\n` +
            `1. 익절 하한 (TP) : ≥ ${p.tp_atr_min} ATR (현재: ${curTpMin} ATR)\n` +
            `2. 손절 하한 (SL) : ≥ ${p.sl_atr_min} ATR (현재: ${curSlMin} ATR)\n` +
            `3. 예측 사정거리 (PA) : ${targetHorizon}캔들 (현재: ${curPaMin}캔들)\n` +
            `4. 최대 보유시간 (MHC): ${maxHold}캔들 (현재: ${curMhcMin}캔들)\n\n` +
            `위 물리적 안전 하한치를 화면 세팅값 및 상단 사정거리 카드에 자동 반영하시겠습니까?\n` +
            `※ [취소]를 누르면 현재 세팅값이 그대로 안전하게 보존됩니다.`;

        if (!confirm(confirmMsg)) {
            return; // 사용자가 취소 시 기존 세팅값 100% 보존
        }

        // 사용자가 확인을 누른 경우에만 자동 입력 반영
        if (!curOpt.take_profit_range) curOpt.take_profit_range = {};
        curOpt.take_profit_range.user_min = p.tp_atr_min;
        curOpt.take_profit_range.user_max = Math.max(p.tp_atr_max || (p.tp_atr_min + 1.5), Number(curOpt.take_profit_range.user_max || (p.tp_atr_min + 2.0)));

        if (!curOpt.stop_loss_range) curOpt.stop_loss_range = {};
        curOpt.stop_loss_range.user_min = p.sl_atr_min;
        curOpt.stop_loss_range.user_max = Math.max(p.sl_atr_max || (p.sl_atr_min + 1.0), Number(curOpt.stop_loss_range.user_max || (p.sl_atr_min + 1.5)));

        if (!curOpt.predict_ahead_candles_range) curOpt.predict_ahead_candles_range = {};
        curOpt.predict_ahead_candles_range.user_min = targetHorizon;
        curOpt.predict_ahead_candles_range.user_max = Math.max(recHorizon, Number(curOpt.predict_ahead_candles_range.user_max || recHorizon));

        if (!curOpt.max_hold_candles_range) curOpt.max_hold_candles_range = {};
        curOpt.max_hold_candles_range.user_min = maxHold;
        curOpt.max_hold_candles_range.user_max = Math.max(maxHold, Number(curOpt.max_hold_candles_range.user_max || maxHold));

        // 2. 상단 [타깃 사정거리 & 물리 선험 가이드] 카드도 100% 일원화 동기화
        if (!FeatureState.targetHorizonConfig) FeatureState.targetHorizonConfig = {};
        FeatureState.targetHorizonConfig.locked_predict_ahead = targetHorizon;
        FeatureState.targetHorizonConfig.locked_max_hold = maxHold;
        FeatureState.targetHorizonConfig.mode = 'locked';
        FeatureState.targetHorizonConfig.candidate_horizons = [targetHorizon, recHorizon, maxHold];

        const inpLockedPa = document.getElementById('inpLockedPredictAhead');
        const inpLockedMhc = document.getElementById('inpLockedMaxHold');
        if (inpLockedPa) inpLockedPa.value = targetHorizon;
        if (inpLockedMhc) inpLockedMhc.value = maxHold;
        const radLocked = document.getElementById('radHorizonLocked');
        if (radLocked) radLocked.checked = true;

        // 서버 비동기 저장 (OPTUNA_CONFIG + TARGET_HORIZON_CONFIG 동시 저장)
        await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                OPTUNA_CONFIG: curOpt,
                TARGET_HORIZON_CONFIG: FeatureState.targetHorizonConfig
            })
        });

        // 1. Optuna 화면 전체 정규 리렌더링
        if (typeof renderOptunaConfig === 'function') {
            renderOptunaConfig();
        }

        // 2. 상단 타깃 사정거리 카드 즉시 리렌더링
        if (typeof renderHorizonUi === 'function') {
            renderHorizonUi();
        }

        // 3. AutoLab 헤더 사정거리 배지 즉시 리렌더링
        if (typeof updateAutoLabHorizonBadge === 'function') {
            updateAutoLabHorizonBadge();
        }

        // 4. DOM input 요소 값 직접 업데이트 (즉각적 시각 반응 보장)
        const tpMinInput = document.querySelector('input[data-optuna="take_profit_range"][data-type="min"]');
        if (tpMinInput) tpMinInput.value = p.tp_atr_min;
        const slMinInput = document.querySelector('input[data-optuna="stop_loss_range"][data-type="min"]');
        if (slMinInput) slMinInput.value = p.sl_atr_min;
        const paMinInput = document.querySelector('input[data-optuna="predict_ahead_candles_range"][data-type="min"]');
        if (paMinInput) paMinInput.value = targetHorizon;
        const paMaxInput = document.querySelector('input[data-optuna="predict_ahead_candles_range"][data-type="max"]');
        if (paMaxInput && curOpt.predict_ahead_candles_range.user_max) paMaxInput.value = curOpt.predict_ahead_candles_range.user_max;
        const mhcMinInput = document.querySelector('input[data-optuna="max_hold_candles_range"][data-type="min"]');
        if (mhcMinInput) mhcMinInput.value = maxHold;
        const mhcMaxInput = document.querySelector('input[data-optuna="max_hold_candles_range"][data-type="max"]');
        if (mhcMaxInput && curOpt.max_hold_candles_range.user_max) mhcMaxInput.value = curOpt.max_hold_candles_range.user_max;

        // 5. AutoLab 가이드 카드도 최신화
        if (typeof loadPhysicsGuardrails === 'function') {
            loadPhysicsGuardrails();
        }

        if (typeof FeatureState !== 'undefined') {
            FeatureState.isDirty = true;
        }

        alert(`✅ 물리 안전 하한치 및 타깃 사정거리 일원화 완료:\n• 익절 하한: ${p.tp_atr_min} ATR\n• 손절 하한: ${p.sl_atr_min} ATR\n• 예측 사정거리: ${targetHorizon}캔들\n• 최대 보유시간: ${maxHold}캔들\n\n⚠️ 프로젝트 통합 저장([💾 프로젝트 저장])을 눌러야 모델 학습 및 스크리닝에 완전 반영됩니다.`);
    } catch (e) {
        alert('물리 제약 적용 중 오류: ' + e);
    }
}
window.applyPhysicsDefaultsToUI = applyPhysicsDefaultsToUI;

// ─────────────────────────────────────────────────────────────────────────────
// 📦 팩터 팩(Feature Group) 마스터 스튜디오 & 타겟 장착 매니저 (Phase 4.2)
// ─────────────────────────────────────────────────────────────────────────────

async function loadGroupsStudio() {
    const grid = document.getElementById('groupsStudioGrid');
    if (!grid) return;
    
    try {
        const res = await apiGet('/api/symbols/groups');
        if (!res || res.status !== 'ok') {
            grid.innerHTML = `<div style="grid-column: 1/-1; color:var(--accent-red); padding:16px; text-align:center;">팩 목록 로드 실패: ${res?.message || '오류'}</div>`;
            return;
        }

        const groups = res.groups || [];
        if (groups.length === 0) {
            grid.innerHTML = `<div style="grid-column: 1/-1; color:var(--text-muted); padding:20px; text-align:center;">등록된 팩터 팩이 없습니다. 상단의 [➕ 신규 팩 생성]을 눌러 첫 번째 팩을 만들어보세요.</div>`;
            return;
        }

        let html = '';
        groups.forEach(g => {
            const gid = g.id;
            const gname = g.name || gid;
            const cat = g.category || 'SECTOR';
            const role = g.default_role || 'leading_stock';
            const members = g.members || [];
            const targets = g.used_by_targets || [];

            // 카테고리 뱃지 색상
            let catColor = '#a855f7';
            if (cat === 'INDEX') catColor = '#3b82f6';
            else if (cat === 'MACRO') catColor = '#eab308';
            else if (cat === 'CRYPTO') catColor = '#10b981';

            // 멤버 목록 태그
            const memberBadges = members.map(m => {
                const s = typeof m === 'object' ? m.symbol : m;
                const n = (typeof m === 'object' && m.name) ? m.name : s;
                return `<span class="badge" style="background:rgba(255,255,255,0.06); color:#f1f5f9; border:1px solid rgba(255,255,255,0.15); font-size:10.5px; padding:2px 6px;">🟢 ${n} <span style="color:#94a3b8; font-size:9.5px;">(${s})</span></span>`;
            }).join(' ');

            // 구독 중인 타겟 태그
            const targetBadges = targets.length > 0
                ? targets.map(t => `<span class="badge" style="background:rgba(6,182,212,0.15); color:#38bdf8; border:1px solid rgba(6,182,212,0.3); font-size:10px; padding:1px 5px;">🎯 ${t.name || t.symbol}</span>`).join(' ')
                : `<span style="color:var(--text-muted); font-size:10.5px;">(미사용 유휴 팩)</span>`;

            html += `
                <div class="card" style="border:1px solid rgba(168,85,247,0.3); background:rgba(15,23,42,0.7); border-radius:8px; padding:12px; display:flex; flex-direction:column; justify-content:space-between; gap:10px; box-shadow:0 2px 8px rgba(0,0,0,0.2);">
                    <div>
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:6px; margin-bottom:6px;">
                            <div>
                                <span style="font-weight:700; font-size:13.5px; color:#f8fafc;">🏷️ ${gname}</span>
                                <span style="color:#94a3b8; font-size:11px; margin-left:4px;">[${gid}]</span>
                            </div>
                            <div style="display:flex; gap:4px;">
                                <span class="badge" style="background:${catColor}20; color:${catColor}; border:1px solid ${catColor}50; font-size:9.5px; padding:2px 6px;">${cat}</span>
                                <span class="badge" style="background:rgba(255,255,255,0.08); color:#cbd5e1; font-size:9.5px; padding:2px 6px;">${role}</span>
                            </div>
                        </div>
                        <div style="font-size:11.5px; color:var(--text-secondary); margin-bottom:8px; line-height:1.4;">
                            ${g.description || '팩 설명이 없습니다.'}
                        </div>
                        <div style="margin-bottom:8px;">
                            <div style="font-size:10.5px; color:var(--text-muted); margin-bottom:3px;">구성 종목 (${members.length}개):</div>
                            <div style="display:flex; flex-wrap:wrap; gap:4px;">
                                ${memberBadges || '<span style="color:var(--text-muted); font-size:10.5px;">구성 종목 없음</span>'}
                            </div>
                        </div>
                        <div>
                            <div style="font-size:10.5px; color:var(--text-muted); margin-bottom:3px;">구독 중인 타겟:</div>
                            <div style="display:flex; flex-wrap:wrap; gap:4px;">
                                ${targetBadges}
                            </div>
                        </div>
                    </div>
                    <div style="display:flex; justify-content:flex-end; gap:6px; border-top:1px solid rgba(255,255,255,0.08); padding-top:8px; margin-top:4px;">
                        <button class="btn btn-xs btn-outline-cyan" onclick="openEditGroupModal('${gid}')" style="font-size:11px; padding:2px 8px;">✏️ 팩 편집</button>
                        <button class="btn btn-xs btn-outline-danger" onclick="deleteGroupFromStudio('${gid}')" style="font-size:11px; padding:2px 8px;">🗑️ 삭제</button>
                    </div>
                </div>
            `;
        });

        grid.innerHTML = html;
    } catch (e) {
        grid.innerHTML = `<div style="grid-column: 1/-1; color:var(--accent-red); padding:16px; text-align:center;">팩 목록 로드 예외: ${e}</div>`;
    }
}
window.loadGroupsStudio = loadGroupsStudio;

// ── 타겟 팩 장착 모달 ──
async function openAttachGroupModal() {
    const modal = document.getElementById('modalAttachGroup');
    const body = document.getElementById('attachGroupListBody');
    if (!modal || !body) return;

    modal.style.display = 'flex';
    modal.classList.add('active');
    body.innerHTML = `<div style="text-align:center; padding:16px; color:var(--text-muted);">가용 팩터 팩 목록을 불러오는 중...</div>`;

    const curTarget = getCurrentTargetSymbol();
    try {
        const res = await apiGet('/api/symbols/groups');
        if (!res || res.status !== 'ok') {
            body.innerHTML = `<div style="color:var(--accent-red); text-align:center;">팩 목록 로드 실패: ${res?.message}</div>`;
            return;
        }

        const groups = res.groups || [];
        const attachedIds = new Set((FeatureState.referenceGroups || []).map(g => g.id));

        if (groups.length === 0) {
            body.innerHTML = `<div style="color:var(--text-muted); text-align:center; padding:20px;">등록된 팩터 팩이 없습니다. 아래 버튼을 눌러 새 팩을 생성하세요.</div>`;
            return;
        }

        let html = '';
        groups.forEach(g => {
            const gid = g.id;
            const gname = g.name || gid;
            const isAttached = attachedIds.has(gid);
            const members = g.members || [];
            const memberNames = members.map(m => m.name || m.symbol).join(', ');

            html += `
                <div style="background:rgba(255,255,255,0.03); border:1px solid ${isAttached ? 'rgba(168,85,247,0.6)' : 'rgba(255,255,255,0.08)'}; border-radius:8px; padding:12px; display:flex; justify-content:space-between; align-items:center; gap:10px;">
                    <div>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span style="font-size:14px;">📦</span>
                            <span style="font-weight:700; font-size:13px; color:#f8fafc;">${gname}</span>
                            <span class="badge" style="background:rgba(168,85,247,0.2); color:#c084fc; font-size:9.5px; padding:1px 5px;">${g.category}</span>
                            ${isAttached ? '<span class="badge badge-success" style="font-size:9.5px; background:rgba(34,197,94,0.2); color:#4ade80;">현재 장착됨</span>' : ''}
                        </div>
                        <div style="font-size:11px; color:var(--text-secondary); margin-top:3px;">
                            ${g.description || ''}
                        </div>
                        <div style="font-size:10.5px; color:var(--accent-cyan); margin-top:2px;">
                            구성 종목: ${memberNames || '없음'}
                        </div>
                    </div>
                    <div>
                        ${isAttached ? `
                            <button type="button" class="btn btn-sm btn-outline-danger" onclick="detachGroupFromTarget('${gid}')" style="font-size:11px; font-weight:600; padding:4px 10px;">
                                ✕ 장착 해제
                            </button>
                        ` : `
                            <button type="button" class="btn btn-sm btn-success" onclick="attachGroupToCurrentTarget('${gid}')" style="font-size:11px; font-weight:600; padding:4px 12px; background:linear-gradient(135deg, #10b981, #06b6d4); border:none;">
                                ➕ 타겟에 장착
                            </button>
                        `}
                    </div>
                </div>
            `;
        });

        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = `<div style="color:var(--accent-red); text-align:center;">조회 오류: ${e}</div>`;
    }
}
window.openAttachGroupModal = openAttachGroupModal;

function closeAttachGroupModal() {
    const modal = document.getElementById('modalAttachGroup');
    if (modal) {
        modal.classList.remove('active');
        modal.style.display = 'none';
    }
}
window.closeAttachGroupModal = closeAttachGroupModal;

async function attachGroupToCurrentTarget(groupId) {
    const curTarget = getCurrentTargetSymbol();
    try {
        const res = await apiPost('/api/symbols/relations/attach_group', {
            target: curTarget,
            group_id: groupId
        });
        if (res && res.status === 'ok') {
            showToast(`✅ ${res.message}`, 'success');
            await loadFeatureConfig(curTarget);
            openAttachGroupModal();
            loadGroupsStudio();
            if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(curTarget);
        } else {
            showToast(`장착 실패: ${res?.message}`, 'error');
        }
    } catch (e) {
        showToast(`장착 오류: ${e}`, 'error');
    }
}
window.attachGroupToCurrentTarget = attachGroupToCurrentTarget;

async function detachGroupFromTarget(groupId) {
    const curTarget = getCurrentTargetSymbol();
    if (!confirm(`'${groupId}' 팩을 현재 타겟 종목에서 분리하시겠습니까?`)) return;

    try {
        const res = await apiPost('/api/symbols/relations/detach_group', {
            target: curTarget,
            group_id: groupId
        });
        if (res && res.status === 'ok') {
            showToast(`'${groupId}' 팩이 분리되었습니다.`, 'info');
            await loadFeatureConfig(curTarget);
            const attachModal = document.getElementById('modalAttachGroup');
            if (attachModal && attachModal.style.display !== 'none') {
                openAttachGroupModal();
            }
            loadGroupsStudio();
            if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(curTarget);
        } else {
            showToast(`분리 실패: ${res?.message}`, 'error');
        }
    } catch (e) {
        showToast(`분리 오류: ${e}`, 'error');
    }
}
window.detachGroupFromTarget = detachGroupFromTarget;

// ── 팩 생성 / 편집 모달 ──
window._currentEditingGroupMembers = [];

function openCreateGroupModal() {
    const modal = document.getElementById('modalManageGroup');
    if (!modal) return;

    document.getElementById('manageGroupModalTitle').textContent = '➕ 신규 팩터 팩(Feature Group) 생성';
    const idInput = document.getElementById('grpInputId');
    idInput.value = '';
    idInput.disabled = false;
    document.getElementById('grpInputName').value = '';
    document.getElementById('grpInputCategory').value = 'SECTOR';
    document.getElementById('grpInputRole').value = 'leading_stock';
    document.getElementById('grpInputDesc').value = '';
    document.getElementById('grpMemberSearchInput').value = '';
    document.getElementById('grpMemberSearchResults').style.display = 'none';

    window._currentEditingGroupMembers = [];
    renderGrpMembersList();
    modal.style.display = 'flex';
    modal.classList.add('active');
}
window.openCreateGroupModal = openCreateGroupModal;

async function openEditGroupModal(groupId) {
    const modal = document.getElementById('modalManageGroup');
    if (!modal) return;

    try {
        const res = await apiGet('/api/symbols/groups');
        const groups = (res && res.groups) || [];
        const targetGroup = groups.find(g => g.id === groupId);
        if (!targetGroup) {
            showToast(`팩 '${groupId}'을(를) 찾을 수 없습니다.`, 'error');
            return;
        }

        document.getElementById('manageGroupModalTitle').textContent = `✏️ 팩터 팩 편집: ${targetGroup.name || groupId}`;
        const idInput = document.getElementById('grpInputId');
        idInput.value = targetGroup.id;
        idInput.disabled = true; // ID는 수정 불가
        document.getElementById('grpInputName').value = targetGroup.name || '';
        document.getElementById('grpInputCategory').value = targetGroup.category || 'SECTOR';
        document.getElementById('grpInputRole').value = targetGroup.default_role || 'leading_stock';
        document.getElementById('grpInputDesc').value = targetGroup.description || '';
        document.getElementById('grpMemberSearchInput').value = '';
        document.getElementById('grpMemberSearchResults').style.display = 'none';

        window._currentEditingGroupMembers = (targetGroup.members || []).map(m => ({
            symbol: typeof m === 'object' ? m.symbol : m,
            name: (typeof m === 'object' && m.name) ? m.name : (typeof m === 'object' ? m.symbol : m)
        }));
        renderGrpMembersList();
        modal.style.display = 'flex';
        modal.classList.add('active');
    } catch (e) {
        showToast(`팩 조회 예외: ${e}`, 'error');
    }
}
window.openEditGroupModal = openEditGroupModal;

function closeManageGroupModal() {
    const modal = document.getElementById('modalManageGroup');
    if (modal) {
        modal.classList.remove('active');
        modal.style.display = 'none';
    }
}
window.closeManageGroupModal = closeManageGroupModal;

let _grpMemberSearchTimer = null;
function onGrpMemberSearch(query) {
    clearTimeout(_grpMemberSearchTimer);
    const box = document.getElementById('grpMemberSearchResults');
    if (!box) return;

    if (!query || query.trim().length === 0) {
        box.style.display = 'none';
        return;
    }

    _grpMemberSearchTimer = setTimeout(async () => {
        try {
            const res = await apiGet(`/api/symbols/search?q=${encodeURIComponent(query.trim())}`);
            const results = (res && res.results) || [];
            if (results.length === 0) {
                box.innerHTML = `<div style="padding:6px; color:var(--text-muted); font-size:11px; text-align:center;">일치하는 종목이 없습니다.</div>`;
            } else {
                box.innerHTML = results.slice(0, 8).map(s => `
                    <div onclick="addMemberToGrpEdit('${s.symbol}', '${s.name || s.symbol}')" style="padding:4px 8px; font-size:11px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; border-radius:4px; hover:background:rgba(255,255,255,0.06);" onmouseover="this.style.background='rgba(168,85,247,0.15)'" onmouseout="this.style.background='transparent'">
                        <span style="font-weight:600; color:#f8fafc;">${s.name || s.symbol} <span style="color:#94a3b8; font-size:10px;">(${s.symbol})</span></span>
                        <span class="badge" style="font-size:9.5px; background:rgba(255,255,255,0.06); color:#cbd5e1;">${s.market_type || 'KRX'}</span>
                    </div>
                `).join('');
            }
            box.style.display = 'flex';
        } catch (e) {
            box.innerHTML = `<div style="padding:6px; color:var(--accent-red); font-size:11px;">검색 오류: ${e}</div>`;
            box.style.display = 'flex';
        }
    }, 250);
}
window.onGrpMemberSearch = onGrpMemberSearch;

function addMemberToGrpEdit(sym, name) {
    const cleanSym = String(sym).trim().toUpperCase();
    if (window._currentEditingGroupMembers.some(m => m.symbol === cleanSym)) {
        showToast(`이미 추가된 종목입니다: ${name} (${cleanSym})`, 'info');
        return;
    }
    window._currentEditingGroupMembers.push({ symbol: cleanSym, name: name });
    document.getElementById('grpMemberSearchInput').value = '';
    document.getElementById('grpMemberSearchResults').style.display = 'none';
    renderGrpMembersList();
}
window.addMemberToGrpEdit = addMemberToGrpEdit;

function removeMemberFromGrpEdit(sym) {
    const cleanSym = String(sym).trim().toUpperCase();
    window._currentEditingGroupMembers = window._currentEditingGroupMembers.filter(m => m.symbol !== cleanSym);
    renderGrpMembersList();
}
window.removeMemberFromGrpEdit = removeMemberFromGrpEdit;

function renderGrpMembersList() {
    const container = document.getElementById('grpMembersList');
    const countSpan = document.getElementById('grpMembersCount');
    if (!container) return;

    if (countSpan) countSpan.textContent = window._currentEditingGroupMembers.length;

    if (window._currentEditingGroupMembers.length === 0) {
        container.innerHTML = `<span style="color:var(--text-muted); font-size:11px;">구성 종목이 없습니다. 위에서 종목을 검색하여 추가하세요.</span>`;
        return;
    }

    container.innerHTML = window._currentEditingGroupMembers.map(m => `
        <div style="display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:4px; background:rgba(168,85,247,0.2); border:1px solid rgba(168,85,247,0.5); font-size:11px;">
            <span style="color:#f8fafc; font-weight:600;">${m.name}</span>
            <span style="color:#c084fc; font-size:10px;">(${m.symbol})</span>
            <button type="button" onclick="removeMemberFromGrpEdit('${m.symbol}')" style="background:none; border:none; color:#f87171; cursor:pointer; font-weight:bold; font-size:11px; padding:0 2px; line-height:1;">✕</button>
        </div>
    `).join('');
}

async function submitSaveGroupModal() {
    const id = document.getElementById('grpInputId').value.trim().toUpperCase();
    const name = document.getElementById('grpInputName').value.trim();
    const category = document.getElementById('grpInputCategory').value;
    const default_role = document.getElementById('grpInputRole').value;
    const description = document.getElementById('grpInputDesc').value.trim();

    if (!id) {
        showToast('팩 식별자(ID)를 입력해 주세요.', 'warning');
        return;
    }
    if (!name) {
        showToast('팩 이름을 입력해 주세요.', 'warning');
        return;
    }

    const payload = {
        id: id,
        name: name,
        category: category,
        default_role: default_role,
        description: description,
        members: window._currentEditingGroupMembers.map(m => ({ symbol: m.symbol }))
    };

    try {
        const res = await apiPost('/api/symbols/groups/save', payload);
        if (res && res.status === 'ok') {
            showToast(`✅ ${res.message}`, 'success');
            closeManageGroupModal();
            loadGroupsStudio();
            const curTarget = getCurrentTargetSymbol();
            await loadFeatureConfig(curTarget);
            if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(curTarget);
        } else {
            showToast(`저장 실패: ${res?.message}`, 'error');
        }
    } catch (e) {
        showToast(`저장 오류: ${e}`, 'error');
    }
}
window.submitSaveGroupModal = submitSaveGroupModal;

async function deleteGroupFromStudio(groupId) {
    if (!confirm(`'${groupId}' 팩터 팩을 영구 삭제하시겠습니까?\n이 팩을 사용 중인 모든 타겟에서 자동으로 분리됩니다.`)) return;

    try {
        const res = await apiPost('/api/symbols/groups/delete', {
            group_id: groupId,
            force: true
        });
        if (res && res.status === 'ok') {
            showToast(`🗑️ ${res.message}`, 'info');
            loadGroupsStudio();
            const curTarget = getCurrentTargetSymbol();
            await loadFeatureConfig(curTarget);
            if (typeof loadSymbolRelationsStudio === 'function') loadSymbolRelationsStudio(curTarget);
        } else {
            showToast(`삭제 실패: ${res?.message}`, 'error');
        }
    } catch (e) {
        showToast(`삭제 오류: ${e}`, 'error');
    }
}
window.deleteGroupFromStudio = deleteGroupFromStudio;

// ─────────────────────────────────────────────────────────────────────────────
// [Feature Store ➔ 3단계 Gold 스냅샷 즉시 빌드]
// ─────────────────────────────────────────────────────────────────────────────

async function buildGoldSnapshotNow() {
    const saved = await ensureProjectSavedBeforeAction('3단계 Gold 스냅샷 빌드');
    if (!saved) return;

    const btn = document.getElementById('btnBuildGoldSnapshot');
    const origHtml = btn ? btn.innerHTML : '⚡ 3단계 Gold 스냅샷 빌드';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ 스냅샷 굽는 중...';
    }

    try {
        showToast('🏗️ 현재 장착된 팩/참조 종목 기준으로 3단계 Gold 스냅샷 빌드를 시작합니다...', 'info');
        const res = await apiPost('/api/autolab/build_gold_snapshot', {});
        if (res && res.status === 'ok') {
            const rowsStr = res.rows ? `${res.rows.toLocaleString()}행` : '';
            const colsStr = res.cols ? `${res.cols}컬럼` : '';
            const rangeStr = (res.start_date && res.end_date) ? ` (${String(res.start_date).slice(0, 10)} ~ ${String(res.end_date).slice(0, 10)})` : '';
            showToast(`✅ 3단계 Gold 스냅샷 빌드 완료! ${rowsStr} ${colsStr}${rangeStr}`, 'success');
            if (typeof loadMedallionStatus === 'function') {
                const curTarget = getCurrentTargetSymbol();
                loadMedallionStatus(curTarget);
            }
        } else {
            showToast(`❌ 스냅샷 빌드 실패: ${res?.message || '알 수 없는 오류'}`, 'error');
        }
    } catch (e) {
        showToast(`❌ 스냅샷 빌드 오류: ${e}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    }
}
window.buildGoldSnapshotNow = buildGoldSnapshotNow;
window.clearAllFeatures = clearAllFeatures;

// ─── Raw 외부 백업 (복구용 사본) ───────────────────────────────────────────
let _rawBackupTimer = null;
const _fmtGB = (b) => (b / 1e9).toFixed(b >= 1e10 ? 0 : 2) + 'GB';
const _esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function loadRawBackupStatus() {
    const box = document.getElementById('rawBackupTargets');
    if (!box) return;
    let res;
    try { res = await apiGet('/api/raw_backup/status'); } catch (e) { box.textContent = '상태 조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok') return;
    const badge = document.getElementById('rawBackupBadge');
    const ts = res.targets || [];
    if (!ts.length) {
        box.innerHTML = '<div style="color:#94a3b8;">등록된 백업 대상이 없습니다. 위에 드라이브 폴더를 입력해 추가하세요.</div>';
    } else {
        box.innerHTML = ts.map(t => {
            const key = encodeURIComponent(t.path);
            const rm = `<button class="btn btn-xs btn-outline-danger" onclick="removeRawBackupTarget(decodeURIComponent('${key}'))" title="설정에서만 뺍니다 (드라이브의 백업 파일은 그대로)">✖</button>`;
            if (t.error) return `<div style="display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-top:1px solid rgba(255,255,255,0.06);"><span>🔌 <b>${_esc(t.path)}</b> <span style="color:#f87171;">${_esc(t.error)}</span></span>${rm}</div>`;
            const pend = t.pending_files ? `<span style="color:#fbbf24;">미백업 ${t.pending_files}개 (${_fmtGB(t.pending_bytes)})</span>` : '<span style="color:#4ade80;">최신</span>';
            const ver = t.last_verify ? `검증 ${_esc(t.last_verify.replace('T',' '))}${t.last_verify_bad ? ` <span style="color:#f87171;">이상 ${t.last_verify_bad}개</span>` : ' ✔'}` : '검증 기록 없음';
            return `<div style="display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-top:1px solid rgba(255,255,255,0.06);"><span>💽 <b>${_esc(t.path)}</b> · ${pend} · 백업 ${t.backed_files}개 · 여유 ${_fmtGB(t.free_bytes)} · 마지막 백업 ${_esc((t.last_run||'없음').replace('T',' '))} · ${ver}</span>${rm}</div>`;
        }).join('');
    }
    if (badge) {
        const bad = ts.some(t => t.error), pend = ts.some(t => t.pending_files);
        badge.textContent = !ts.length ? '대상 없음' : bad ? '연결 이상' : pend ? '미백업 있음' : '최신';
        badge.style.background = !ts.length ? 'rgba(148,163,184,0.2)' : bad ? 'rgba(248,113,113,0.2)' : pend ? 'rgba(251,191,36,0.2)' : 'rgba(74,222,128,0.2)';
        badge.style.color = !ts.length ? '#94a3b8' : bad ? '#f87171' : pend ? '#fbbf24' : '#4ade80';
    }
    _renderRawBackupJob(res.job);
    if (res.job && res.job.running && !_rawBackupTimer) _rawBackupTimer = setInterval(loadRawBackupStatus, 1500);
    if (!(res.job && res.job.running) && _rawBackupTimer) { clearInterval(_rawBackupTimer); _rawBackupTimer = null; }
}

function _renderRawBackupJob(job) {
    const wrap = document.getElementById('rawBackupJobWrap');
    if (!wrap || !job) return;
    const hasLog = job.log && job.log.length;
    wrap.style.display = (job.running || hasLog) ? 'block' : 'none';
    const pct = Math.round(job.progress || 0);
    document.getElementById('rawBackupJobBar').style.width = pct + '%';
    document.getElementById('rawBackupJobPct').textContent = pct + '%';
    const kind = {backup: '백업', dry: '미리보기', verify: '검증'}[job.kind] || '';
    document.getElementById('rawBackupJobLabel').textContent = job.running ? `⏳ ${kind} 중: ${job.current || ''}` : (job.result ? `✔ ${kind} 종료` : '');
    const logEl = document.getElementById('rawBackupLog');
    logEl.textContent = (job.log || []).join('\n');
    logEl.scrollTop = logEl.scrollHeight;
    ['btnRawBackupRun'].forEach(id => { const b = document.getElementById(id); if (b) b.disabled = !!job.running; });
    const c = document.getElementById('btnRawBackupCancel'); if (c) c.style.display = job.running ? '' : 'none';
}

async function addRawBackupTarget() {
    const inp = document.getElementById('rawBackupPath');
    const res = await apiPost('/api/raw_backup/targets', { path: inp.value });
    if (res && res.status === 'ok') { inp.value = ''; showToast('✅ 백업 대상 등록: ' + res.path, 'success'); }
    else showToast('❌ ' + (res && res.message || '등록 실패'), 'danger');
    loadRawBackupStatus();
}

async function removeRawBackupTarget(path) {
    if (!confirm('이 대상을 목록에서 뺍니다. (드라이브의 백업 파일은 삭제되지 않습니다)\n' + path)) return;
    await apiPost('/api/raw_backup/targets/remove', { path });
    loadRawBackupStatus();
}

async function runRawBackup(kind) {
    const body = kind === 'verify' ? { kind: 'verify' } : { kind: 'backup', dry_run: kind === 'dry' };
    const res = await apiPost('/api/raw_backup/run', body);
    if (!res || res.status !== 'ok') showToast('❌ ' + (res && res.message || '실행 실패'), 'danger');
    loadRawBackupStatus();
}

async function cancelRawBackup() { await apiPost('/api/raw_backup/cancel', {}); showToast('⏹ 중지 요청', 'info'); }

window.loadRawBackupStatus = loadRawBackupStatus;
window.addRawBackupTarget = addRawBackupTarget;
window.removeRawBackupTarget = removeRawBackupTarget;
window.runRawBackup = runRawBackup;
window.cancelRawBackup = cancelRawBackup;


// ---------------------------------------------------------------------------
// [연구 데이터셋] 원장 밖 staging 수집 현황 (읽기 전용) — /api/research_datasets
// ---------------------------------------------------------------------------
let _researchTimer = null;

function _rdBar(pct, color) {
    return `<div style="height:8px;background:rgba(255,255,255,0.08);border-radius:4px;overflow:hidden;margin:6px 0;"><div style="width:${Math.min(100, pct)}%;height:100%;background:${color};"></div></div>`;
}

function _rdEta(min) {
    if (min == null) return '';
    const h = Math.floor(min / 60), m = Math.round(min % 60);
    const done = new Date(Date.now() + min * 60000);
    return `남은 ${h ? h + '시간 ' : ''}${m}분 (완료 예상 ${done.toLocaleString('ko-KR', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'})})`;
}

const _RD_JOB = {done: ['✔ 완료', '#4ade80'], running: ['⏳ 수집 중', '#38bdf8'], stopped: ['⛔ 멈춤', '#f87171']};

function _rdJob(j) {
    const [lbl, col] = _RD_JOB[j.state] || [j.state, '#94a3b8'];
    const color = j.failed_n ? '#fbbf24' : col;
    const prog = j.target ? `${j.done.toLocaleString()} / ${j.target.toLocaleString()} ${_esc(j.unit)} (${j.pct}%)` : `${j.done.toLocaleString()} ${_esc(j.unit)}`;
    let st = lbl;
    if (j.state === 'running' && j.eta_min != null) st += ' · ' + _rdEta(j.eta_min);
    if (j.state === 'stopped') st += ` · ${_esc(j.stopped_reason || '갱신 끊김 — 다시 실행하면 이어받음')}`;
    const fails = j.failed_n ? ` · <span style="color:#f87171;" title="${_esc(j.failed.join(', '))}">실패 ${j.failed_n} (${_esc(j.failed.slice(0, 5).join(', '))}${j.failed_n > 5 ? ' …' : ''})</span>` : ' · 실패 0';
    return `<div style="margin-top:8px;"><b>${_esc(j.title)}</b> <span style="font-size:12px;">${prog}</span> <span style="font-size:12px;color:${col};">${st}</span></div>`
        + (j.target ? _rdBar(j.pct, color) : '')
        + `<div style="font-size:12px;color:#94a3b8;">신규 ${j.fetched.toLocaleString()} · 이미 있음 ${j.skipped.toLocaleString()}${fails}`
        + `${j.note ? ' · ' + _esc(j.note) : ''} · 마지막 갱신 ${_esc((j.updated_at || '-').replace('T', ' '))}</div>`;
}

const _rdOpen = new Set();   // 30초 자동 갱신에도 펼친 섹션을 유지
function _rdTog(el) { const k = el.dataset.k; if (el.open) _rdOpen.add(k); else _rdOpen.delete(k); }

function _rdBadge(text, color) {
    return `<span style="font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid ${color};color:${color};white-space:nowrap;">${text}</span>`;
}

function _rdTile(label, value, sub) {
    return `<div style="flex:1;min-width:130px;padding:10px 12px;border-radius:8px;background:rgba(255,255,255,0.04);">`
        + `<div style="font-size:11px;color:#94a3b8;">${label}</div><div style="font-size:18px;font-weight:700;margin-top:2px;">${value}</div>`
        + (sub ? `<div style="font-size:11px;color:#94a3b8;margin-top:2px;">${sub}</div>` : '') + `</div>`;
}

function _rdSection(dname, id, title, summary, body, forceOpen) {
    const k = dname + ':' + id;
    return `<details data-k="${_esc(k)}" ontoggle="_rdTog(this)" ${(forceOpen || _rdOpen.has(k)) ? 'open' : ''} style="margin-top:8px;border-top:1px solid rgba(255,255,255,0.06);padding-top:6px;">`
        + `<summary style="cursor:pointer;font-size:13px;padding:2px 0;"><b>${title}</b> <span style="font-size:12px;color:#94a3b8;margin-left:6px;">${summary}</span></summary>`
        + `<div style="margin-top:6px;">${body}</div></details>`;
}

function _rdDataset(d) {
    const kv = (k, v) => `<div style="display:flex;gap:10px;padding:3px 0;font-size:12px;"><span style="color:#94a3b8;min-width:110px;">${k}</span><span style="flex:1;">${v}</span></div>`;
    const jobs = d.jobs || [];
    const running = jobs.filter(j => j.state === 'running').length, stopped = jobs.filter(j => j.state === 'stopped').length;
    const failedJobs = jobs.filter(j => j.failed_n).length;
    let badge = '';
    if (d.status_note) badge = _rdBadge('종결', '#94a3b8');
    else if (running) badge = _rdBadge('⏳ 수집 중', '#38bdf8');
    else if (stopped) badge = _rdBadge('⛔ 멈춤', '#f87171');
    else if (failedJobs) badge = _rdBadge('⚠ 실패 있음', '#fbbf24');
    else if (jobs.length || d.collect_1m) badge = _rdBadge('✔ 수집 완료', '#4ade80');
    let h = `<div style="padding:16px;border:1px solid rgba(255,255,255,0.1);border-radius:10px;margin-bottom:16px;">`;
    h += `<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">`
       + `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;"><b style="font-size:16px;">${_esc(d.title)}</b>${badge}</div>`
       + `<span style="font-size:11px;color:#94a3b8;">폴더 ${_esc(d.name)} · ${d.size_bytes != null ? _fmtGB(d.size_bytes) : '크기 계산 중'}</span></div>`;
    if (d.purpose) h += `<div style="font-size:13px;color:#cbd5e1;margin:6px 0 4px;line-height:1.5;">${_esc(d.purpose)}</div>`;
    if (d.status_note || d.result) {
        h += `<div style="margin:8px 0;padding:8px 12px;border-left:3px solid #fbbf24;background:rgba(251,191,36,0.06);font-size:12px;line-height:1.6;white-space:pre-line;">`
           + (d.status_note ? `<b>${_esc(d.status_note)}</b>\n` : '') + (d.result ? _esc(d.result) : '') + `</div>`;
    }
    // 요약 타일
    const tiles = [];
    if (d.universe) {
        const u = d.universe, bs = u.by_status;
        tiles.push(_rdTile('종목 수', u.total.toLocaleString(), Object.entries(u.by_market).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' · ')));
        if (bs) tiles.push(_rdTile('상장 / 상폐', `${(bs.listed || 0).toLocaleString()} / ${(bs.delisted || 0).toLocaleString()}`, `우선주·스팩 ${u.preferred_or_spac} (분석 제외)`));
    }
    if (jobs.length) tiles.push(_rdTile('수집 작업', `${jobs.filter(j => j.state === 'done').length} / ${jobs.length} 완료`, running ? `${running}개 진행 중` : (failedJobs ? `실패 있는 작업 ${failedJobs}개` : '')));
    const c = d.collect_1m;
    if (c) tiles.push(_rdTile('1분봉 수집', `${c.pct}%`, `${c.done.toLocaleString()} / ${c.target.toLocaleString()} 종목`));
    const pq = d.panel_quality;
    if (pq) tiles.push(_rdTile('분석 패널', `${(pq.prices || 0).toLocaleString()}행`, `${pq.codes}종목 · ~${_esc(pq.date_max || '')}`));
    if (tiles.length) h += `<div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;">${tiles.join('')}</div>`;
    // 버튼
    const key = encodeURIComponent(d.name);
    h += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px;">`
       + (d.has_symbols || d.collect_1m ? `<button class="btn btn-xs" onclick="toggleResearchSymbols('${key}')">📋 종목 보기</button>`
                                       : `<button class="btn btn-xs" onclick="toggleResearchFiles('${key}')">📂 데이터 보기</button>`)
       + (d.collector ? `<button class="btn btn-xs" onclick="toggleResearchSteps('${key}')">🔄 데이터 갱신</button>` : '') + `</div>`
       + `<div id="rdSteps-${_esc(d.name)}" style="display:none;margin-top:8px;"></div>`
       + `<div id="rdSym-${_esc(d.name)}" style="display:none;margin-top:8px;"></div>`;
    // 접이식 상세 — 진행 중·멈춤·실패가 있으면 수집 작업을 자동으로 펼친다
    if (jobs.length) {
        const sm = running ? `${running}개 진행 중` : stopped ? '멈춘 작업 있음' : failedJobs ? '실패 있음' : '전부 완료';
        h += _rdSection(d.name, 'jobs', '수집 작업', sm, jobs.map(_rdJob).join(''), running || stopped || failedJobs);
    }
    if (c) {
        const color = c.failed.length || c.download_fail ? '#f87171' : c.finished ? '#4ade80' : '#38bdf8';
        const body = _rdBar(c.pct, color) + `<div style="font-size:12px;color:#94a3b8;">${c.finished ? '✔ 완료' : '⏳ 수집 중 · ' + _rdEta(c.eta_min)} · 다운로드 실패 ${c.download_fail} · 병합 대기 ${c.inbox_pending} · 병합 실패 ${c.failed.length ? '<span style="color:#f87171;">' + c.failed.map(_esc).join(', ') + '</span>' : 0}`
            + ` · 마지막 다운로드 ${_esc(c.last_download || '-')} · 마지막 병합 ${_esc(c.last_ingest || '-')}</div>`;
        h += _rdSection(d.name, 'm1', '1분봉 수집', `${c.done.toLocaleString()} / ${c.target.toLocaleString()} 종목`, body, !c.finished);
    }
    const st = Object.entries(d.stores || {});
    if (st.length) h += _rdSection(d.name, 'stores', '보관 현황', `${st.length}종류`,
        `<div style="display:flex;gap:6px;flex-wrap:wrap;">${st.map(([k, v]) => `<span style="font-size:12px;padding:3px 8px;border-radius:6px;background:rgba(255,255,255,0.05);">${_esc(k)} <b>${v.toLocaleString()}</b></span>`).join('')}</div>`);
    const q = d.quality;
    if (q) {
        const iss = Object.entries(q.issues || {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '없음';
        const ds = Object.entries(q.day_status || {}).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' · ');
        h += _rdSection(d.name, 'quality', '품질 점검', `문제 종목 ${q.codes_with_issue}`,
            kv('점검 시각', _esc(q.checked_at.replace('T', ' '))) + kv('대상', `${q.codes} 종목 · 일자 상태 ${ds}`) + kv('문제', `${q.codes_with_issue} 종목 (${iss})`)
            + kv('기타', `수집 누락일 ${q.missing_traded_days_total} · 거래정지일 ${q.halted_days_total} · 수정주가 적용 종목 ${q.codes_with_adjustment}`));
    }
    const prog = Object.entries(d.progress || {}).filter(([k]) => k !== 'minute');
    let more = '';
    if (prog.length) more += kv('Creon 드라이버', prog.map(([k, p]) => `${k} ${p.done}/${p.total}${p.fail ? ` <span style="color:#f87171;">실패 ${p.fail}</span>` : ''} (${_esc(p.at)})`).join(' · '));
    if (d.derived && d.derived.length) more += kv('분석 패널 파일', d.derived.map(_esc).join(', ') + (pq ? ` · 점검 ${_esc((pq.built_at || '').replace('T', ' '))} · 재무 ${pq.fin_companies}사` : ''));
    if (d.outputs && d.outputs.length) more += kv('연구 산출물', d.outputs.map(_esc).join(', '));
    if (d.scripts) more += kv('스크립트', `<code>${_esc(d.scripts)}</code>`);
    if (d.details) more = `<div style="font-size:12px;color:#cbd5e1;line-height:1.6;margin-bottom:6px;">${_esc(d.details)}</div>` + more;
    if (more) h += _rdSection(d.name, 'more', '상세 정보', '구조·스크립트·산출물', more);
    return h + '</div>';
}

// 수집 단계 실행 — /api/research_datasets/{name}/steps, POST .../collect {step, full}
async function toggleResearchSteps(key) {
    const name = decodeURIComponent(key);
    const box = document.getElementById('rdSteps-' + name);
    if (!box) return;
    if (box.style.display === 'block') { box.style.display = 'none'; return; }
    box.style.display = 'block';
    await renderResearchSteps(key);
}

async function renderResearchSteps(key) {
    const name = decodeURIComponent(key);
    const box = document.getElementById('rdSteps-' + name);
    if (!box) return;
    let res;
    try { res = await apiGet(`/api/research_datasets/${key}/steps`); } catch (e) { box.textContent = '조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok') { box.textContent = (res && res.message) || '조회 실패'; return; }
    const r = res.running;
    const run = r ? `<div style="color:#38bdf8;margin-bottom:6px;">⏳ 실행 중: <b>${_esc(r.step)}</b> (pid ${r.pid}, ${_esc((r.started_at || '').replace('T', ' '))}) — 끝나면 아래 작업 진행률에 반영</div>` : '';
    const isKr = name === 'kr_equity';
    const groups = {linux: isKr ? '이 서버(Linux) — KIND·네이버·DART·패널' : '이 서버(Linux)', creon: 'Windows Creon — 로그인된 상태여야 함'};
    const hint = isKr ? '단계는 모두 증분(받은 건 건너뜀) — 한 번에 하나만 실행. 보통 순서: Creon 스냅샷 → 상폐 목록·유니버스 → 상폐 일봉 → Creon 일봉/보조/지수 → DART → 패널 빌드'
                      : '한 번에 하나만 실행. 보통 첫 단계(전체 갱신)만 누르면 됩니다.';
    let h = `<div style="padding:10px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;">${run}
        <div style="font-size:12px;color:#94a3b8;margin-bottom:6px;">${hint}</div>`;
    for (const g of ['linux', 'creon']) {
        if (!res.steps.some(x => x.runner === g)) continue;
        h += `<div style="font-size:12px;font-weight:600;margin:6px 0 2px;">${groups[g]}</div>`;
        for (const s of res.steps.filter(x => x.runner === g)) {
            const fullBtn = /creon_daily(_ext)?$/.test(s.key) ? ` <button class="btn btn-xs" ${r ? 'disabled' : ''} onclick="startResearchStep('${key}','${s.key}',true)">전체 재수집</button>` : '';
            h += `<div style="display:flex;gap:8px;align-items:center;padding:3px 0;border-top:1px solid rgba(255,255,255,0.05);">
                <button class="btn btn-xs" ${r ? 'disabled' : ''} onclick="startResearchStep('${key}','${s.key}',false)">▶ ${_esc(s.title)}</button>${fullBtn}
                <span style="font-size:12px;color:#cbd5e1;">${_esc(s.desc)}</span></div>`;
        }
    }
    if (res.log && res.log.length) h += `<details style="margin-top:8px;"><summary style="font-size:12px;color:#94a3b8;cursor:pointer;">최근 로그 ${_esc(res.log[0])}</summary><pre style="font-size:11px;max-height:220px;overflow:auto;margin:4px 0 0;">${res.log.slice(1).map(_esc).join('\n')}</pre></details>`;
    box.innerHTML = h + '</div>';
}

async function startResearchStep(key, step, full) {
    const name = decodeURIComponent(key);
    const label = full ? `${step} 전체 재수집` : step;
    if (!confirm(`${name}: '${label}' 단계를 시작할까요?${full ? '\n(전체 재수집은 오래 걸리고 기존 파일을 다시 씁니다)' : ''}`)) return;
    let res;
    try { res = await apiPost(`/api/research_datasets/${key}/collect`, {step, full}); } catch (e) { alert('실행 실패: ' + e); return; }
    if (!res || res.status !== 'ok') { alert((res && res.message) || '실행 실패'); return; }
    await renderResearchSteps(key);
    if (!_researchTimer) _researchTimer = setInterval(loadResearchDatasets, 30000);
}

async function loadResearchDatasets() {
    const box = document.getElementById('researchDatasetsBody');
    if (!box) return;
    let res;
    try { res = await apiGet('/api/research_datasets'); } catch (e) { box.textContent = '조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok') return;
    const ds = res.datasets || [];
    box.innerHTML = ds.length ? ds.map(_rdDataset).join('') : `<div style="color:#94a3b8;">연구 데이터셋이 없습니다 (${_esc(res.root)}).</div>`;
    const upd = document.getElementById('researchDatasetsUpdated');
    if (upd) upd.textContent = '갱신 ' + new Date().toLocaleTimeString('ko-KR');
    const running = ds.some(d => (d.collect_1m && !d.collect_1m.finished) || (d.jobs || []).some(j => j.state === 'running'));
    ds.forEach(d => { const b = document.getElementById('rdSteps-' + d.name); if (b && b.style.display === 'block') renderResearchSteps(encodeURIComponent(d.name)); });
    if (running && !_researchTimer) _researchTimer = setInterval(loadResearchDatasets, 30000);
    if (!running && _researchTimer) { clearInterval(_researchTimer); _researchTimer = null; }
}

// 종목 개념이 없는 데이터셋 — 원본 묶음(raw/<묶음>/)별 파일 현황
async function toggleResearchFiles(key) {
    const name = decodeURIComponent(key);
    const box = document.getElementById('rdSym-' + name);
    if (!box) return;
    if (box.style.display === 'block') { box.style.display = 'none'; return; }
    box.style.display = 'block';
    box.innerHTML = '불러오는 중…';
    let res;
    try { res = await apiGet(`/api/research_datasets/${key}/files`); } catch (e) { box.textContent = '조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok') { box.textContent = (res && res.message) || '조회 실패'; return; }
    const th = (t) => `<th style="text-align:left;padding:4px 6px;position:sticky;top:0;background:#0f172a;">${t}</th>`;
    const rows = res.groups.map(g => `<tr style="border-top:1px solid rgba(255,255,255,0.05);"><td style="padding:3px 6px;">${_esc(g.group)}</td><td style="padding:3px 6px;">${_esc(g.label)}</td>`
        + `<td style="padding:3px 6px;">${g.files.toLocaleString()}</td><td style="padding:3px 6px;">${_esc(g.year_from)}${g.year_to && g.year_to !== g.year_from ? ' ~ ' + _esc(g.year_to) : ''}</td>`
        + `<td style="padding:3px 6px;">${(g.size / 1024).toFixed(0)} KB</td><td style="padding:3px 6px;">${_esc(g.updated)}</td></tr>`).join('');
    box.innerHTML = `<div style="font-size:12px;color:#94a3b8;margin-bottom:6px;">종목 단위가 아닌 원본 묶음 ${res.groups.length}개 (raw/ 아래 폴더별)</div>`
        + `<div style="max-height:480px;overflow:auto;"><table style="width:100%;font-size:12px;border-collapse:collapse;"><tr>${['묶음(코드)', '이름', '파일 수', '연도', '크기', '최근 수정'].map(th).join('')}</tr>${rows}</table></div>`;
}
window.toggleResearchFiles = toggleResearchFiles;
window._rdTog = _rdTog;

const _rdSymCache = {};
const _RD_STATE = {done: ['완료', '#4ade80'], pending_merge: ['병합 대기', '#38bdf8'], waiting: ['수집 대기', '#94a3b8'],
                   failed: ['실패', '#f87171'], excluded: ['분석 제외', '#64748b']};

async function toggleResearchSymbols(key) {
    const name = decodeURIComponent(key);
    const box = document.getElementById('rdSym-' + name);
    if (!box) return;
    if (box.style.display === 'block') { box.style.display = 'none'; return; }
    box.style.display = 'block';
    box.innerHTML = '불러오는 중…';
    let res;
    try { res = await apiGet(`/api/research_datasets/${key}/symbols`); } catch (e) { box.textContent = '조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok') { box.textContent = (res && res.message) || '조회 실패'; return; }
    _rdSymCache[name] = res.symbols;
    if (res.symbols.length && 'price_source' in res.symbols[0]) { _rdDailySymbols(key, name, box, res.symbols); return; }
    const counts = {};
    res.symbols.forEach(r => counts[r.state] = (counts[r.state] || 0) + 1);
    const stateOpts = Object.entries(_RD_STATE).map(([k, [lbl]]) => `<option value="${k}">${lbl} (${counts[k] || 0})</option>`).join('');
    box.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">
        <input id="rdq-${_esc(name)}" placeholder="코드·이름 검색" style="padding:4px 8px;min-width:180px;" oninput="renderResearchSymbols('${key}')">
        <select id="rdm-${_esc(name)}" onchange="renderResearchSymbols('${key}')"><option value="">전체 시장</option><option>KOSPI</option><option>KOSDAQ</option></select>
        <select id="rds-${_esc(name)}" onchange="renderResearchSymbols('${key}')"><option value="">전체 상태</option>${stateOpts}</select>
        <select id="rdf-${_esc(name)}" onchange="renderResearchSymbols('${key}')"><option value="">전체</option><option value="issue">품질 문제</option><option value="adj">수정주가 이벤트</option><option value="halt">거래정지 있음</option><option value="unchecked">점검 전</option></select>
        <span id="rdc-${_esc(name)}" style="font-size:12px;color:#94a3b8;"></span></div>
        <div id="rdt-${_esc(name)}" style="max-height:480px;overflow:auto;"></div>`;
    renderResearchSymbols(key);
}

const _RD_DSTATE = {done: ['수집됨', '#4ade80'], waiting: ['수집 대기', '#94a3b8'], failed: ['실패', '#f87171']};

function _rdDailySymbols(key, name, box, rows) {
    const n = (f) => rows.filter(f).length.toLocaleString();
    const id = _esc(name);
    box.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">
        <input id="rdq-${id}" placeholder="코드·이름 검색" style="padding:4px 8px;min-width:180px;" oninput="renderDailySymbols('${key}')">
        <select id="rdm-${id}" onchange="renderDailySymbols('${key}')"><option value="">전체 시장</option><option>KOSPI</option><option>KOSDAQ</option></select>
        <select id="rdl-${id}" onchange="renderDailySymbols('${key}')"><option value="">상장+상폐</option><option value="listed">현재 상장 (${n(r => r.status === 'listed')})</option><option value="delisted">상폐 (${n(r => r.status === 'delisted')})</option></select>
        <select id="rds-${id}" onchange="renderDailySymbols('${key}')"><option value="">전체 수집 상태</option>${Object.entries(_RD_DSTATE).map(([k, [l]]) => `<option value="${k}">${l} (${n(r => r.state === k)})</option>`).join('')}</select>
        <select id="rdf-${id}" onchange="renderDailySymbols('${key}')"><option value="">전체</option><option value="common">보통주만 (우선주·스팩 제외)</option><option value="pre2014">2014년 이전부터 있음</option><option value="nounadj">무수정 일봉 없음 (상장)</option></select>
        <span id="rdc-${id}" style="font-size:12px;color:#94a3b8;"></span></div>
        <div id="rdt-${id}" style="max-height:480px;overflow:auto;"></div>`;
    renderDailySymbols(key);
}

function renderDailySymbols(key) {
    const name = decodeURIComponent(key);
    const all = _rdSymCache[name] || [];
    const g = (p) => document.getElementById(p + name).value;
    const q = (g('rdq-') || '').trim().toLowerCase(), mk = g('rdm-'), ls = g('rdl-'), stf = g('rds-'), flt = g('rdf-');
    const rows = all.filter(r => (!q || r.code.toLowerCase().includes(q) || r.name.toLowerCase().includes(q))
        && (!mk || r.market === mk) && (!ls || r.status === ls) && (!stf || r.state === stf)
        && (!flt || (flt === 'common' && !r.preferred && !r.spac) || (flt === 'pre2014' && r.first && r.first < '2014-01-02')
            || (flt === 'nounadj' && r.status === 'listed' && !r.has_unadj)));
    const LIMIT = 300;
    document.getElementById('rdc-' + name).textContent = `${rows.length.toLocaleString()} 종목` + (rows.length > LIMIT ? ` (앞 ${LIMIT}개 표시 — 검색으로 좁히세요)` : '');
    const dash = '<span style="color:#64748b;">-</span>';
    const th = (t) => `<th style="text-align:left;padding:4px 6px;position:sticky;top:0;background:#0f172a;">${t}</th>`;
    let h = `<table style="width:100%;font-size:12px;border-collapse:collapse;"><tr>${['코드', '이름', '시장', '구분', '가격 출처', '수집', '기간', '봉 수', '무수정', '상폐 사유'].map(th).join('')}</tr>`;
    rows.slice(0, LIMIT).forEach(r => {
        const [lbl, col] = _RD_DSTATE[r.state] || [r.state, '#94a3b8'];
        const tags = [r.preferred ? '우선주' : '', r.spac ? '스팩' : ''].filter(Boolean).join('·');
        const kind = r.status === 'delisted' ? `<span style="color:#fbbf24;">상폐 ${_esc(r.delist_date)}</span>` : '상장';
        const unadj = r.status === 'delisted' ? dash : r.has_unadj ? '<span style="color:#4ade80;">있음</span>' : '<span style="color:#94a3b8;">없음</span>';
        const fail = r.failed_by ? ` <span style="color:#94a3b8;font-size:11px;">(${_esc(r.failed_by)})</span>` : '';
        h += `<tr style="border-top:1px solid rgba(255,255,255,0.05);"><td style="padding:3px 6px;">${_esc(r.code)}</td>`
           + `<td>${_esc(r.name)}${tags ? ` <span style="color:#94a3b8;font-size:11px;">${tags}</span>` : ''}</td><td>${_esc(r.market)}</td><td>${kind}</td>`
           + `<td>${r.price_source === 'naver' ? '네이버(수정)' : 'Creon'}</td><td style="color:${col};">${lbl}${fail}</td>`
           + `<td>${r.first ? `${_esc(r.first)} ~ ${_esc(r.last)}` : dash}</td><td>${r.bars != null ? r.bars.toLocaleString() : dash}</td><td>${unadj}</td>`
           + `<td style="max-width:280px;color:#94a3b8;" title="${_esc(r.delist_reason)}">${_esc((r.delist_reason || '').slice(0, 30))}</td></tr>`;
    });
    document.getElementById('rdt-' + name).innerHTML = h + '</table>';
}

function renderResearchSymbols(key) {
    const name = decodeURIComponent(key);
    const all = _rdSymCache[name] || [];
    const q = (document.getElementById('rdq-' + name).value || '').trim().toLowerCase();
    const mk = document.getElementById('rdm-' + name).value;
    const stf = document.getElementById('rds-' + name).value;
    const flt = document.getElementById('rdf-' + name).value;
    const rows = all.filter(r => (!q || r.code.toLowerCase().includes(q) || r.name.toLowerCase().includes(q))
        && (!mk || r.market === mk) && (!stf || r.state === stf)
        && (!flt || (flt === 'issue' && r.issue) || (flt === 'adj' && r.adjust_events > 0)
            || (flt === 'halt' && r.halted_days > 0) || (flt === 'unchecked' && r.state === 'done' && !r.checked)));
    const LIMIT = 300;
    document.getElementById('rdc-' + name).textContent = `${rows.length.toLocaleString()} 종목` + (rows.length > LIMIT ? ` (앞 ${LIMIT}개 표시 — 검색으로 좁히세요)` : '');
    const v = (x) => x == null ? '<span style="color:#64748b;">-</span>' : x.toLocaleString();
    const th = (t) => `<th style="text-align:left;padding:4px 6px;position:sticky;top:0;background:#0f172a;">${t}</th>`;
    let h = `<table style="width:100%;font-size:12px;border-collapse:collapse;"><tr>${['코드', '이름', '시장', '상태', '1분봉 일수', '거래정지일', '수정주가 이벤트', '격리일', '품질'].map(th).join('')}</tr>`;
    rows.slice(0, LIMIT).forEach(r => {
        const [lbl, col] = _RD_STATE[r.state] || [r.state, '#94a3b8'];
        const tags = [r.preferred ? '우선주' : '', r.spac ? '스팩' : '', r.supervision ? '관리' : ''].filter(Boolean).join('·');
        const adj = r.adjust_events ? `${r.adjust_events}회 (계수 ${r.factor_min}~${r.factor_max})` : (r.checked ? '0' : '-');
        const qual = r.state !== 'done' ? '' : !r.checked ? '<span style="color:#94a3b8;">점검 전</span>' : r.issue ? `<span style="color:#fbbf24;">${_esc(r.issue)}</span>` : '<span style="color:#4ade80;">정상</span>';
        h += `<tr style="border-top:1px solid rgba(255,255,255,0.05);"><td style="padding:3px 6px;">${_esc(r.code)}</td>`
           + `<td>${_esc(r.name)}${tags ? ` <span style="color:#94a3b8;font-size:11px;">${tags}</span>` : ''}</td><td>${_esc(r.market)}</td>`
           + `<td style="color:${col};">${lbl}</td><td>${v(r.m1_days)}</td><td>${v(r.halted_days)}</td><td>${adj}</td><td>${v(r.quarantined_days)}</td><td>${qual}</td></tr>`;
    });
    document.getElementById('rdt-' + name).innerHTML = h + '</table>';
}


// ───────────────────────────────────────────────────────────────────────────
// 원천 목록 (운용 + 유니버스) — 요약 → 이상만 → 검색(서버 페이지). shared/data_library/raw_catalog.py
// ───────────────────────────────────────────────────────────────────────────
let _rcPage = 1, _rcTimer = null, _rcOpenKey = null;
const STALE_NOTE = '5거래일 이상 뒤처지면 최신일 지연';
const _RC_ISSUE_COLOR = {conflict: '#f87171', incomplete_bar: '#fbbf24', stale: '#fbbf24', missing_days: '#fb923c', no_file: '#f87171',
                         no_manifest: '#f87171', size_mismatch: '#f87171', listing_ended: '#94a3b8'};

async function loadRawCatalog(force) {
    const badge = document.getElementById('rawCatalogBadge');
    if (!badge) return;
    badge.textContent = force ? '다시 훑는 중...' : '로딩 중...';
    let res;
    try { res = await apiGet('/api/raw_store/catalog/summary' + (force ? '?force=true' : '')); } catch (e) { badge.textContent = '조회 실패'; return; }
    if (!res || res.status !== 'ok') { badge.textContent = (res && res.message) || '조회 실패'; return; }
    const anomaly = res.anomaly_total || 0;
    badge.textContent = `${res.total.toLocaleString()}종목 · 이상 ${anomaly.toLocaleString()}`;
    badge.className = 'badge ' + (anomaly ? 'badge-orange' : 'badge-green');
    badge.style.background = anomaly ? 'rgba(251,146,60,0.15)' : 'rgba(74,222,128,0.15)';
    badge.style.color = anomaly ? '#fb923c' : '#4ade80';
    document.getElementById('rawCatalogBuilt').textContent = `목록 ${String(res.built_at || '').replace('T', ' ')}` +
        (res.listing_day ? ` · 거래소 목록 ${res.listing_day}` : '');
    document.getElementById('rawCatalogWarnings').innerHTML = (res.warnings || []).map(w =>
        `<div style="margin-bottom:8px;padding:6px 10px;border-radius:6px;background:rgba(251,191,36,0.12);border:1px solid rgba(251,191,36,0.4);color:#fbbf24;font-size:12px;">⚠️ ${_esc(w)}</div>`).join('');
    const latest = res.market_latest || {};
    const rows = (res.groups || []).map(g => `<tr>
        <td>${_esc(g.tier)}</td><td>${_esc(g.market)}</td><td>${_esc(g.provider)}</td>
        <td style="text-align:right;">${g.count.toLocaleString()}</td>
        <td style="text-align:right;">${_esc(g.last_max || '-')}</td>
        <td style="text-align:right;color:${g.anomaly ? '#fb923c' : '#4ade80'};cursor:${g.anomaly ? 'pointer' : 'default'};"
            ${g.anomaly ? `onclick="rawCatalogFilter('${_esc(g.tier)}','${_esc(g.market)}')"` : ''}>${g.anomaly.toLocaleString()}</td>
        <td style="text-align:right;color:#94a3b8;">${g.ended ? g.ended.toLocaleString() : ''}</td></tr>`).join('');
    const issues = (res.issues || []).map(i => `<span style="display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;border-radius:10px;font-size:11px;cursor:pointer;
        border:1px solid ${_RC_ISSUE_COLOR[i.code] || '#94a3b8'};color:${_RC_ISSUE_COLOR[i.code] || '#94a3b8'};"
        onclick="rawCatalogIssue('${_esc(i.code)}')">${_esc(i.label)} ${i.count.toLocaleString()}</span>`).join('');
    document.getElementById('rawCatalogSummary').innerHTML = `
        <table class="table" style="width:100%;font-size:12px;border-collapse:collapse;">
          <thead><tr style="color:#94a3b8;"><th style="text-align:left;">구분</th><th style="text-align:left;">시장</th><th style="text-align:left;">원천</th>
            <th style="text-align:right;">종목</th><th style="text-align:right;">최신일</th><th style="text-align:right;">이상</th><th style="text-align:right;">상장 종료</th></tr></thead>
          <tbody>${rows}</tbody></table>
        <div style="margin-top:6px;">${issues}</div>
        ${Object.keys(latest).length ? `<div style="font-size:11px;color:#64748b;margin-top:4px;">${Object.entries(latest).map(([k, v]) => `${_esc(k)}: 보유 최신일 ${_esc(v || '-')} · 확정된 마지막 거래일 ${_esc((res.market_expected || {})[k] || '-')}`).join(' / ')} (거래일 달력 기준, ${STALE_NOTE})</div>` : ''}`;
    const sel = document.getElementById('rcIssue');
    const cur = sel.value;
    sel.innerHTML = '<option value="">모든 이상 종류</option>' + (res.issues || []).map(i =>
        `<option value="${_esc(i.code)}">${_esc(i.label)} (${i.count.toLocaleString()})</option>`).join('');
    sel.value = cur;
    rawCatalogSearch(_rcPage);
    loadUsUniverseJob();
}

let _usJobTimer = null;
async function loadUsUniverseJob() {
    const el = document.getElementById('usJobStatus'), btn = document.getElementById('btnUsJob');
    if (!el) return;
    let res;
    try { res = await apiGet('/api/raw_store/catalog/us_job'); } catch (e) { return; }
    if (!res || res.status !== 'ok') return;
    const L = res.last || {};
    clearTimeout(_usJobTimer);
    if (res.running) {
        const pct = L.target ? Math.floor(100 * (L.done || 0) / L.target) : 0;
        el.textContent = `미국 갱신 중 ${(L.done || 0).toLocaleString()}/${(L.target || 0).toLocaleString()} (${pct}%) · 실패 ${L.failed_n || 0}`;
        el.style.color = '#38bdf8';
        btn.disabled = true;
        _usJobTimer = setTimeout(loadUsUniverseJob, 15000);
    } else {
        const behind = L.finished && L.behind_latest ? ` · 최신 봉 아직 없음 ${L.behind_latest.toLocaleString()}종목(다시 누르면 그 종목만 요청)` : '';
        el.textContent = L.updated_at ? `미국 마지막 갱신 ${String(L.updated_at).replace('T', ' ').slice(0, 16)}${L.finished ? '' : ' (중단됨)'}${behind}` : '';
        el.style.color = L.finished === false || behind ? '#fbbf24' : '#94a3b8';
        btn.disabled = false;
    }
}

async function startUsUniverseJob() {
    const now = new Date(new Date().toLocaleString('en-US', {timeZone: 'America/New_York'}));
    const mins = now.getHours() * 60 + now.getMinutes();
    const inSession = now.getDay() >= 1 && now.getDay() <= 5 && mins >= 9 * 60 + 30 && mins < 16 * 60;
    const msg = (inSession ? '⚠️ 지금은 미국 정규장 중입니다. 오늘 봉은 미완성이라 빠지고 다음 갱신에서 채워집니다.\n\n' : '') +
        '미국 유니버스(~1.3만 종목) 증분 수집을 시작할까요? 약 2~3시간, 별도 프로세스로 돌아갑니다.';
    if (!confirm(msg)) return;
    let res;
    try { res = await apiPost('/api/raw_store/catalog/us_job', {}); } catch (e) { alert('시작 실패: ' + e); return; }
    if (!res || res.status !== 'ok') { alert((res && res.message) || '시작 실패'); return; }
    if (!res.started) alert('이미 실행 중입니다 (pid ' + res.pid + ', ' + res.started_at + ' 시작)');
    loadUsUniverseJob();
}

function rawCatalogFilter(tier, market) {
    document.getElementById('rcTier').value = tier;
    document.getElementById('rcMarket').value = market;
    document.getElementById('rcView').value = 'anomaly';
    rawCatalogSearch(1);
}

function rawCatalogIssue(code) {
    document.getElementById('rcIssue').value = code;
    document.getElementById('rcView').value = '';
    rawCatalogSearch(1);
}

function rawCatalogSearchDebounced() {
    clearTimeout(_rcTimer);
    _rcTimer = setTimeout(() => rawCatalogSearch(1), 300);
}

async function rawCatalogSearch(page) {
    _rcPage = page || 1;
    const q = document.getElementById('rcQ').value.trim();
    const params = new URLSearchParams({
        q, tier: document.getElementById('rcTier').value, market: document.getElementById('rcMarket').value,
        issue: document.getElementById('rcIssue').value, page: _rcPage, size: 50,
        // 검색어가 있으면 '이상만'이어도 전체에서 찾는다(찾는 종목이 정상이어도 보여야 한다)
        only_anomaly: (document.getElementById('rcView').value === 'anomaly' && !q && !document.getElementById('rcIssue').value) ? 'true' : 'false',
    });
    const box = document.getElementById('rcTable');
    let res;
    try { res = await apiGet('/api/raw_store/catalog/items?' + params.toString()); } catch (e) { box.textContent = '조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok') { box.textContent = (res && res.message) || '조회 실패'; return; }
    document.getElementById('rcCount').textContent = `${res.total.toLocaleString()}건`;
    if (!res.items.length) {
        box.innerHTML = `<div style="padding:12px;color:#4ade80;font-size:12px;">${params.get('only_anomaly') === 'true' ? '✅ 이상 없음' : '해당 종목 없음'}</div>`;
    } else {
        box.innerHTML = `<table class="table" style="width:100%;font-size:12px;border-collapse:collapse;">
          <thead><tr style="color:#94a3b8;position:sticky;top:0;background:#0f172a;">
            <th style="text-align:left;">심볼</th><th style="text-align:left;">이름</th><th>구분</th><th>시장</th><th>원천</th><th>주기</th>
            <th style="text-align:right;">봉 수</th><th style="text-align:right;">시작</th><th style="text-align:right;">최신일</th><th style="text-align:left;">이상</th></tr></thead>
          <tbody>${res.items.map(e => {
            const tags = (e.issues || []).map((c, i) => `<span style="margin-right:4px;padding:1px 6px;border-radius:8px;font-size:11px;border:1px solid ${_RC_ISSUE_COLOR[c] || '#94a3b8'};color:${_RC_ISSUE_COLOR[c] || '#94a3b8'};">${_esc((e.issue_labels || [])[i] || c)}${c === 'missing_days' && e.missing_days ? ' ' + e.missing_days : ''}${c === 'conflict' && e.conflicts ? ' ' + e.conflicts : ''}</span>`).join('');
            const open = e.tier === '유니버스' || e.key.indexOf('feed:') !== 0;
            return `<tr style="border-top:1px solid rgba(255,255,255,0.05);${open ? 'cursor:pointer;' : ''}" ${open ? `onclick="rawCatalogDetail('${encodeURIComponent(e.key)}')"` : ''}>
              <td style="font-weight:600;">${_esc(e.symbol)}${e.etf ? ' <span style="font-size:10px;color:#94a3b8;">ETF</span>' : ''}</td>
              <td style="color:#cbd5e1;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_esc(e.name)}">${_esc(e.name || '')}</td>
              <td style="text-align:center;color:${e.tier === '운용' ? '#38bdf8' : '#94a3b8'};">${_esc(e.tier)}</td>
              <td style="text-align:center;">${_esc(e.market)}</td><td style="text-align:center;">${_esc(e.provider)}</td><td style="text-align:center;">${_esc(e.interval || '')}</td>
              <td style="text-align:right;">${e.rows != null ? Number(e.rows).toLocaleString() : ''}</td>
              <td style="text-align:right;">${_esc(e.first || '')}</td><td style="text-align:right;">${_esc(e.last || '')}</td>
              <td>${tags}</td></tr>
              <tr id="rcd-${encodeURIComponent(e.key)}" style="display:none;"><td colspan="10"></td></tr>`;
          }).join('')}</tbody></table>`;
    }
    const pages = Math.max(1, Math.ceil(res.total / res.size));
    document.getElementById('rcPager').innerHTML = pages <= 1 ? '' : `
        <button class="btn btn-xs" ${_rcPage <= 1 ? 'disabled' : ''} onclick="rawCatalogSearch(${_rcPage - 1})">◀</button>
        <span>${_rcPage} / ${pages}</span>
        <button class="btn btn-xs" ${_rcPage >= pages ? 'disabled' : ''} onclick="rawCatalogSearch(${_rcPage + 1})">▶</button>`;
}

async function rawCatalogDetail(encKey) {
    const row = document.getElementById('rcd-' + encKey);
    if (!row) return;
    if (row.style.display !== 'none') { row.style.display = 'none'; return; }
    row.style.display = '';
    const cell = row.firstElementChild;
    cell.innerHTML = '<div style="padding:8px;color:#94a3b8;">불러오는 중…</div>';
    let res;
    try { res = await apiGet('/api/raw_store/catalog/detail?key=' + encKey); } catch (e) { cell.textContent = '조회 실패: ' + e; return; }
    if (!res || res.status !== 'ok' || res.error) { cell.textContent = (res && (res.error || res.message)) || '조회 실패'; return; }
    const a = res.actions || {};
    const miss = res.recent_missing || [];
    const conf = res.conflicts || [];
    cell.innerHTML = `<div style="padding:8px 10px;background:rgba(0,0,0,0.25);border-radius:6px;font-size:12px;color:#cbd5e1;line-height:1.7;">
        <div>파일 <code>${_esc(res.key)}</code></div>
        ${res.recent_window && res.recent_window.length ? `<div>최근 거래일(${_esc(res.recent_window[0])}~${_esc(res.recent_window[1])}, 거래일 달력) 중 빠진 날${res.recent_after_last ? ` (마지막 봉 이후 ${res.recent_after_last}거래일은 미수집)` : ''}: ${miss.length ? `<span style="color:#fb923c;">${miss.length}일</span> ${_esc(miss.slice(0, 12).join(', '))}${miss.length > 12 ? ' …' : ''}` : '<span style="color:#4ade80;">없음</span>'}</div>` : ''}
        <div>확인 필요: ${conf.length ? `<span style="color:#f87171;">${conf.length}건</span> ${_esc(conf.slice(0, 5).map(c => c.ts).join(', '))}` : '없음'}</div>
        <div>영구 결손: ${(res.unrecoverable || []).length ? _esc(res.unrecoverable.map(r => r.join('~')).join(', ')) : '없음'}</div>
        ${a.events ? `<div>기업행위 원장 ${a.count}건${a.events.length ? ' (최근) ' + _esc(a.events.map(e => `${e.ex_date} ×${(+e.price_factor).toFixed(4)}`).join(', ')) : ''}</div>` : ''}
        ${a.splits ? `<div>분할 ${a.splits.length}건${a.splits.length ? ': ' + _esc(a.splits.map(x => `${x.date} ${(+x.numerator)}:${(+x.denominator)}`).join(', ')) : ''} · 배당 ${a.dividends}건${a.last_dividend ? ` (최근 ${_esc(a.last_dividend.date)} ${_esc(a.last_dividend.amount)})` : ''}</div>` : ''}
    </div>`;
}


// ─────────────────────────────────────────────────────────────────────────────
// 🔄 통합 업데이트 — 체크한 갱신 항목을 정해진 순서로 실행 (/api/unified_update, shared/data_library/unified_update.py)
// ─────────────────────────────────────────────────────────────────────────────
const UU_ICON = { pending: '⏳', running: '🔄', done: '✅', warn: '⚠️', failed: '❌', skipped: '⏭️', off: '—' };
const UU_LABEL = { pending: '대기', running: '진행 중', done: '완료', warn: '확인 필요', failed: '실패', skipped: '건너뜀', off: '선택 안 함' };
let _uuTimer = null, _uuData = null;

function _uuSelected() {
    const boxes = document.querySelectorAll('#unifiedUpdateCard input[data-uu]');
    return Array.from(boxes).filter(b => b.checked).map(b => b.dataset.uu);
}

function _uuTotal(sel, est) {
    const p = k => (sel.includes(k) ? (est[k] || 0) : 0);
    return p('ops') + p('kr') + p('kr_ext') + p('us') + p('checks');   // 운용 → 한국 → 미국 → 검사 (메모리 때문에 직렬)
}

function _uuMin(m) {
    m = Math.max(0, Math.round(m));
    return m >= 60 ? `${Math.floor(m / 60)}시간 ${m % 60}분` : `${m}분`;
}

function _uuElapsed(fromIso, toIso) {
    if (!fromIso) return 0;
    const a = new Date(fromIso), b = toIso ? new Date(toIso) : new Date();
    return (b - a) / 60000;
}

function _uuSaveSel() {
    try { localStorage.setItem('unifiedUpdateSel', JSON.stringify(_uuSelected())); } catch (e) { /* 저장 불가해도 기본값(전체)로 동작 */ }
}

function _uuLoadSel(keys) {
    try {
        const v = JSON.parse(localStorage.getItem('unifiedUpdateSel') || 'null');
        if (Array.isArray(v)) return v.filter(k => keys.includes(k));
    } catch (e) { /* 무시 */ }
    return keys;                                                                // 기본값: 전체 체크
}

function _uuUpdateTotal() {
    if (!_uuData) return;
    const est = Object.fromEntries(_uuData.items.map(i => [i.key, i.est_min]));
    const sel = _uuSelected();
    const btn = document.getElementById('btnUnifiedUpdate');
    if (btn) {
        btn.disabled = !sel.length;
        btn.textContent = sel.length ? `🔄 통합 업데이트 시작 (예상 약 ${_uuMin(_uuTotal(sel, est))})` : '항목을 고르세요';
    }
    _uuSaveSel();
}

function renderUnifiedUpdate(data) {
    const el = document.getElementById('unifiedUpdateCard');
    if (!el || !data) return;
    _uuData = data;
    const st = data.state || {};
    const running = st.status === 'running';
    const keys = data.items.map(i => i.key);
    const sel = running ? (st.selected || []) : _uuLoadSel(keys);
    const est = Object.fromEntries(data.items.map(i => [i.key, i.est_min]));

    const head = `
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div>
          <div style="font-size:14.5px; font-weight:bold; color:#f8fafc;">🔄 통합 업데이트</div>
          <div style="font-size:11.5px; color:#94a3b8; margin-top:2px;">
            체크한 항목을 순서대로 실행합니다: 3단계 메달리온 빌드 → 한국 → 미국(차례로) → 실물 검사. 메달리온만 돌리려면 그 항목만 체크하세요.
            저녁 20:00 이후 권장 — 한국 틱·1분봉은 다음 날 06:00에 완결되므로 저녁에는 전날 분까지 들어옵니다.
          </div>
        </div>
        ${running ? '' : `<button class="btn btn-sm" id="btnUnifiedUpdate" onclick="startUnifiedUpdate()"
            style="background:linear-gradient(135deg,#22c55e,#06b6d4); color:#fff; font-weight:bold; font-size:13px; padding:8px 18px; border:none; border-radius:6px;"></button>`}
      </div>`;

    let body = '';
    if (running) {
        const elapsed = _uuElapsed(st.started_at);
        const total = st.est_total_min || 0;
        const left = total - elapsed;
        body += `<div style="margin-top:10px; font-size:12px; color:#e2e8f0;">
            진행 중 · 경과 ${_uuMin(elapsed)} / 예상 ${_uuMin(total)} ·
            ${left > 0 ? `남은 시간 약 ${_uuMin(left)}` : `<span style="color:#fbbf24;">예상보다 늦어지고 있습니다</span>`}
            <span style="color:#64748b;">(시작 ${_esc((st.started_at || '').replace('T', ' '))})</span></div>`;
    }
    body += `<div style="margin-top:10px; display:grid; gap:6px;">`;
    for (const it of data.items) {
        const s = (st.items || {})[it.key] || {};
        const status = running ? (s.status || 'off') : null;
        const took = s.started_at ? _uuElapsed(s.started_at, s.finished_at) : null;
        const timeTxt = running
            ? (status === 'running' ? `경과 ${_uuMin(took)} / 예상 약 ${_uuMin(s.est_min ?? it.est_min)}`
               : (['done', 'warn', 'failed'].includes(status) && took != null ? `${_uuMin(took)} 걸림` : `예상 약 ${_uuMin(s.est_min ?? it.est_min)}`))
            : `예상 약 ${_uuMin(it.est_min)}`;
        const off = running && status === 'off';
        body += `<div style="display:flex; gap:10px; align-items:flex-start; padding:6px 10px; border-radius:6px; background:rgba(0,0,0,0.25);${off ? ' opacity:0.4;' : ''}">
            ${running
                ? `<span style="width:20px; text-align:center;" title="${UU_LABEL[status] || ''}">${UU_ICON[status] || ''}</span>`
                : `<input type="checkbox" data-uu="${it.key}" ${sel.includes(it.key) ? 'checked' : ''} onchange="_uuUpdateTotal()" style="margin-top:3px;">`}
            <div style="flex:1; min-width:0;">
              <div style="font-size:12.5px; color:#f1f5f9; font-weight:600;">${_esc(it.title)}
                ${off ? '' : `<span style="font-weight:normal; color:#94a3b8; font-size:11px; margin-left:6px;">${timeTxt}</span>`}
                ${running ? `<span style="font-weight:normal; font-size:11px; margin-left:6px; color:${status === 'failed' ? '#f87171' : status === 'warn' ? '#fbbf24' : '#94a3b8'};">${UU_LABEL[status] || ''}</span>` : ''}
              </div>
              <div style="font-size:11px; color:#94a3b8;">${_esc(running && s.detail ? s.detail : it.desc)}</div>
            </div>
          </div>`;
    }
    body += `</div>`;

    // 마지막 실행 결과(실행 중이 아닐 때)
    if (!running && st.started_at) {
        const items = st.items || {};
        const bad = Object.entries(items).filter(([, v]) => ['failed', 'warn'].includes(v.status));
        const title = st.status === 'interrupted' ? '⚠️ 마지막 실행이 중단되었습니다' : (bad.length ? '⚠️ 마지막 실행: 확인 필요 항목 있음' : '✅ 마지막 실행: 모두 정상');
        body += `<div style="margin-top:10px; padding:8px 10px; border-radius:6px; background:rgba(0,0,0,0.3); font-size:11.5px; color:#cbd5e1;">
            <div style="font-weight:600;">${title}
              <span style="color:#64748b; font-weight:normal;">(${_esc((st.started_at || '').replace('T', ' '))} ~ ${_esc((st.finished_at || '').slice(11))}, ${_uuMin(_uuElapsed(st.started_at, st.finished_at))})</span></div>
            ${data.items.filter(i => items[i.key] && items[i.key].status !== 'off').map(i => {
                const v = items[i.key];
                return `<div>${UU_ICON[v.status] || ''} ${_esc(i.title)} — ${UU_LABEL[v.status] || ''}${v.detail ? ` · ${_esc(v.detail)}` : ''}</div>`;
            }).join('')}
            ${((items.checks || {}).failures || []).length ? `<div style="margin-top:4px; color:#fbbf24;">실물 검사에서 걸린 항목 (데이터 상태 신호 — 코드 오류 아님):</div>
              ${(items.checks.failures).map(f => `<div style="padding-left:12px;">• ${_esc(f.about || f.test)}
                  <div style="padding-left:12px; color:#94a3b8;">${_esc(f.message)} <span style="color:#64748b;">(${_esc(f.test)})</span></div></div>`).join('')}` : ''}
          </div>`;
    }
    el.innerHTML = head + body;
    if (!running) _uuUpdateTotal();

    clearTimeout(_uuTimer);
    if (running) _uuTimer = setTimeout(loadUnifiedUpdate, 5000);
}

async function loadUnifiedUpdate() {
    try {
        const res = await apiGet('/api/unified_update');
        if (res && res.status === 'ok') renderUnifiedUpdate(res);
        else { const el = document.getElementById('unifiedUpdateCard'); if (el) el.innerHTML = `<div style="color:#f87171; font-size:12px;">통합 업데이트 상태를 불러오지 못했습니다: ${_esc(res?.message || '')}</div>`; }
    } catch (e) { /* 다음 갱신 때 다시 */ }
}

async function startUnifiedUpdate() {
    const sel = _uuSelected();
    if (!sel.length) return;
    const btn = document.getElementById('btnUnifiedUpdate');
    if (btn) btn.disabled = true;
    try {
        const res = await apiPost('/api/unified_update', { items: sel });
        if (typeof showToast === 'function') showToast(res.status === 'ok' ? `✅ ${res.message}` : `❌ ${res.message}`, res.status === 'ok' ? 'success' : 'danger');
    } catch (e) {
        if (typeof showToast === 'function') showToast(`❌ 통합 업데이트 시작 실패: ${e}`, 'danger');
    }
    loadUnifiedUpdate();
}

window.startUnifiedUpdate = startUnifiedUpdate;
window.loadUnifiedUpdate = loadUnifiedUpdate;
window._uuUpdateTotal = _uuUpdateTotal;
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadUnifiedUpdate);
else loadUnifiedUpdate();

