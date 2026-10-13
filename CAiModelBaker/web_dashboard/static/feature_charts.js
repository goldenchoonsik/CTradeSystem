/**
 * CAiModelBaker — feature_charts.js v3 (Lightweight Charts)
 *
 * 지표 타입 자동 분류:
 *   OVERLAY  → 가격 LWC 차트에 LineSeries 추가 (trend 계열 기본값)
 *   PANEL    → 별도 LWC 인스턴스 (subpanel)
 *
 * 공유 X축: lwcSyncCharts() + subscribeVisibleLogicalRangeChange
 * Overlay/Panel 전환: 체크박스 리스트 토글 버튼
 * 패널 드래그 리사이저: 패널 상단 헤더 드래그
 */
(function () {

    /* ──────────────────────────────────────────────
       1. 색상 / 분류 설정
    ────────────────────────────────────────────── */
    const PALETTE = {
        momentum:   '#00d2ff',
        divergence: '#bd93f9',
        trend:      '#f1fa8c',
        oscillator: '#ff79c6',
        orderflow:  '#50fa7b',
        time:       '#ffb86c',
        raw:        '#8be9fd',
    };

    const OVERLAY_CATEGORIES = new Set(['trend']);

    const RSI_LIKE_IDS = new Set([
        'rsi', 'rsi_14', 'stoch_rsi', 'stoch_k', 'stoch_d',
        'upbit_rsi_daily', 'upbit_rsi', 'binance_rsi',
    ]);

    const VOLUME_LIKE_IDS = new Set([
        'upbit_volume', 'binance_spot_volume', 'binance_futures_volume',
        'binance_usdc_volume', 'upbit_trade_price', 'binance_spot_quote_volume',
        'binance_spot_trade_count', 'upbit_trade_count', 'upbit_large_trade_count',
    ]);

    const RAW_LABELS = {
        upbit_open: '업비트 시가', upbit_high: '업비트 고가',
        upbit_low: '업비트 저가', upbit_close: '업비트 종가',
        binance_spot_open: '바이낸스 시가 ($)', binance_spot_high: '바이낸스 고가 ($)',
        binance_spot_low: '바이낸스 저가 ($)', binance_spot_close: '바이낸스 종가 ($)',
        binance_spot_open_krw: '바이낸스 시가 (원화 환산)', binance_spot_high_krw: '바이낸스 고가 (원화 환산)',
        binance_spot_low_krw: '바이낸스 저가 (원화 환산)', binance_spot_close_krw: '바이낸스 종가 (원화 환산)',
        upbit_volume: '업비트 거래량', binance_spot_volume: '바이낸스 현물 거래량',
        binance_usdc_volume: '바이낸스 USDC 거래량', binance_futures_volume: '바이낸스 선물 거래량',
        upbit_trade_price: '업비트 거래 대금', binance_spot_quote_volume: '바이낸스 달러 대금',
        binance_futures_oi: '바이낸스 OI(수량)', binance_futures_oi_value: '바이낸스 OI 가치($)',
        upbit_trade_count: '업비트 체결 건수', upbit_large_trade_count: '업비트 대량 체결',
        upbit_avg_trade_size: '업비트 평균 체결 크기',
        upbit_trade_bid_ratio: '업비트 매수 비율', binance_spot_taker_ratio: '바이낸스 Taker 비율',
        binance_spot_trade_count: '바이낸스 체결 건수', upbit_atr: '업비트 ATR',
    };

    /* ──────────────────────────────────────────────
       2. 상태
    ────────────────────────────────────────────── */
    // 지표별 모드: 'overlay' | 'panel'
    // STATE.featureChartModes 에 저장 (전역 STATE 확장)
    let _unsubSync = null;     // X축 동기화 해제 함수
    let _panelCharts = [];     // 서브패널 LWC 인스턴스 배열
    let _overlaySeriesMap = {}; // id → LWC series (가격 차트 overlay)

    /* ──────────────────────────────────────────────
       3. 헬퍼
    ────────────────────────────────────────────── */
    function colorFor(id, cat) {
        return PALETTE[cat] || '#8be9fd';
    }

    function getLabel(id, meta, isRaw) {
        if (isRaw) return RAW_LABELS[id] || meta?.name || id;
        return meta?.name || id;
    }

    function defaultMode(id, meta) {
        if (meta && OVERLAY_CATEGORIES.has(meta.category)) return 'overlay';
        return 'panel';
    }

    function getMode(id, meta) {
        const modes = (window.STATE && window.STATE.featureChartModes) || {};
        if (modes[id] !== undefined) return modes[id];
        return defaultMode(id, meta);
    }

    function setMode(id, mode) {
        if (!window.STATE.featureChartModes) window.STATE.featureChartModes = {};
        window.STATE.featureChartModes[id] = mode;
    }

    /* ──────────────────────────────────────────────
       4. 체크박스 + 모드 토글 리스트 렌더링
    ────────────────────────────────────────────── */
    function populateFeatureSelect(featureMeta, rawMeta) {
        const list = document.getElementById('featureCheckList');
        if (!list) return;
        const checkCount = window.STATE.featureChartIds.length;

        const makeBadge = (text, color) =>
            `<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid ${color};color:${color};margin-left:4px;flex-shrink:0;">${text}</span>`;

        // 모드 토글 버튼 생성
        const makeModeToggle = (id, meta, isRaw) => {
            const mode = getMode(id, isRaw ? { category: '' } : meta);
            const oActive = mode === 'overlay';
            const pActive = mode === 'panel';
            return `<span style="display:inline-flex;gap:0;margin-left:auto;flex-shrink:0;">
                <button onclick="setFeatureMode('${id}','overlay')" title="가격 차트 위에 겹쳐서 표시"
                    style="font-size:9px;padding:1px 6px;cursor:pointer;border-radius:3px 0 0 3px;
                    border:1px solid #f1fa8c;
                    background:${oActive ? '#f1fa8c' : 'transparent'};
                    color:${oActive ? '#18181c' : '#f1fa8c'};
                    transition:all .15s;">O</button>
                <button onclick="setFeatureMode('${id}','panel')" title="별도 패널로 분리 표시"
                    style="font-size:9px;padding:1px 6px;cursor:pointer;border-radius:0 3px 3px 0;
                    border:1px solid #8be9fd;border-left:none;
                    background:${pActive ? '#8be9fd' : 'transparent'};
                    color:${pActive ? '#18181c' : '#8be9fd'};
                    transition:all .15s;">P</button>
            </span>`;
        };

        if (window.STATE.binanceKrwChecked === undefined) {
            window.STATE.binanceKrwChecked = true; // 기본값 활성화
        }
        const krwChecked = window.STATE.binanceKrwChecked ? 'checked' : '';

        let html = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
            <span style="font-size:11px;font-weight:bold;">✅ 표시 중: <span id="featureCheckCount">${checkCount}</span>개</span>
            <button class="btn btn-sm" onclick="selectAllFeatures()" style="font-size:10px;padding:1px 8px;">전체 선택</button>
            <button class="btn btn-sm" onclick="deselectAllFeatures()" style="font-size:10px;padding:1px 8px;">전체 해제</button>
            <label style="display:inline-flex;align-items:center;gap:4px;font-size:10px;cursor:pointer;margin-left:8px;background:rgba(80,250,123,0.15);padding:2px 6px;border-radius:3px;border:1px solid rgba(80,250,123,0.3);color:#50fa7b;" title="바이낸스 달러 가격 지표를 업비트 가격과 직접 비교할 수 있도록 실시간 롤링 환율을 적용해 원화로 자동 환산하여 표시합니다.">
                <input type="checkbox" id="binanceKrwToggle" ${krwChecked} onchange="window.STATE.binanceKrwChecked=this.checked; renderAllFeatureCharts();" style="width:12px;height:12px;cursor:pointer;">
                바이낸스 원화(KRW) 환산
            </label>
            <span style="font-size:10px;color:var(--text-muted);margin-left:auto;">
                <span style="border:1px solid #f1fa8c;border-radius:3px;padding:1px 4px;color:#f1fa8c;">O</span> 가격 위 겹치기 &nbsp;
                <span style="border:1px solid #8be9fd;border-radius:3px;padding:1px 4px;color:#8be9fd;">P</span> 별도 패널
            </span>
        </div>`;

        if (rawMeta && Object.keys(rawMeta).length > 0) {
            html += `<div style="font-size:11px;font-weight:bold;color:#ff79c6;margin:8px 0 4px;">📊 원본 데이터 (RAW)</div>`;
            html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:4px;margin-bottom:8px;">`;
            
            // 메인 가격 차트 제어용 체크박스를 첫 줄에 강제 배치
            const priceChecked = (window.STATE.hidePriceChart === true) ? '' : 'checked';
            html += `<label style="display:flex;align-items:center;gap:5px;font-size:11px;cursor:pointer;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,0.05);grid-column: 1 / -1;border:1px dashed rgba(255,255,255,0.15);margin-bottom:2px;">
                <input type="checkbox" value="MAIN_PRICE_CHART" ${priceChecked} onchange="toggleFeatureChart(this)" style="width:14px;height:14px;cursor:pointer;flex-shrink:0;">
                <span style="color:#00d2ff;font-weight:bold;">📈 [기본] 가격 차트 & 매매 시그널</span>
            </label>`;

            Object.entries(rawMeta).forEach(([id, m]) => {
                const checked = window.STATE.featureChartIds.includes(id) ? 'checked' : '';
                const label = getLabel(id, m, true);
                html += `<label style="display:flex;align-items:center;gap:5px;font-size:11px;cursor:pointer;padding:2px 6px;border-radius:4px;${checked ? 'background:rgba(255,121,198,0.08);' : ''}">
                    <input type="checkbox" value="${id}" ${checked} onchange="toggleFeatureChart(this)" style="width:14px;height:14px;cursor:pointer;flex-shrink:0;">
                    <span style="color:${checked ? '#ff79c6' : 'var(--text-secondary)'};">${label}</span>
                    ${makeModeToggle(id, m, true)}
                </label>`;
            });
            html += `</div>`;
        }

        if (featureMeta && Object.keys(featureMeta).length > 0) {
            html += `<div style="font-size:11px;font-weight:bold;color:#8be9fd;margin:10px 0 4px;">📈 계산 지표 (Feature)</div>`;
            html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:4px;">`;
            Object.entries(featureMeta).forEach(([id, m]) => {
                const checked = window.STATE.featureChartIds.includes(id) ? 'checked' : '';
                const color = checked ? colorFor(id, m.category) : 'var(--text-secondary)';
                html += `<label style="display:flex;align-items:center;gap:5px;font-size:11px;cursor:pointer;padding:2px 6px;border-radius:4px;${checked ? 'background:rgba(139,233,253,0.08);' : ''}">
                    <input type="checkbox" value="${id}" ${checked} onchange="toggleFeatureChart(this)" style="width:14px;height:14px;cursor:pointer;flex-shrink:0;">
                    <span style="color:${color};">[${m.category_name || ''}] ${m.name}</span>
                    ${makeModeToggle(id, m, false)}
                </label>`;
            });
            html += `</div>`;
        }

        list.innerHTML = html;
    }

    /* ──────────────────────────────────────────────
       5. 체크박스 토글 / 전체선택
    ────────────────────────────────────────────── */
    function toggleFeatureChart(checkbox) {
        const id = checkbox.value;
        if (id === 'MAIN_PRICE_CHART') {
            window.STATE.hidePriceChart = !checkbox.checked;
            const wrapper = document.getElementById('priceChartWrapper');
            if (wrapper) {
                wrapper.style.display = checkbox.checked ? 'block' : 'none';
            }
            if (window.STATE.lwcPriceChart) {
                setTimeout(() => {
                    try {
                        const priceEl = document.getElementById('priceChart');
                        if (priceEl && checkbox.checked) {
                            window.STATE.lwcPriceChart.resize(priceEl.clientWidth, 450);
                        }
                    } catch (_) {}
                }, 50);
            }
            if (window.appendLog) {
                window.appendLog(checkbox.checked ? '👁️ 가격 차트 노출' : '🙈 가격 차트 숨김', '');
            }
            return;
        }

        if (checkbox.checked) {
            if (!window.STATE.featureChartIds.includes(id)) window.STATE.featureChartIds.push(id);
        } else {
            window.STATE.featureChartIds = window.STATE.featureChartIds.filter(x => x !== id);
        }
        const counter = document.getElementById('featureCheckCount');
        if (counter) counter.textContent = window.STATE.featureChartIds.length;
        renderAll();
    }

    function selectAllFeatures() {
        const r = window.STATE.lastChartResult;
        if (!r) return;
        const all = [];
        if (r.raw_meta) all.push(...Object.keys(r.raw_meta));
        if (r.feature_meta) all.push(...Object.keys(r.feature_meta));
        window.STATE.featureChartIds = all;
        populateFeatureSelect(r.feature_meta || {}, r.raw_meta || {});
        renderAll();
        if (window.appendLog) window.appendLog(`📊 전체 ${all.length}개 표시`, '');
    }

    function deselectAllFeatures() {
        window.STATE.featureChartIds = [];
        const r = window.STATE.lastChartResult;
        if (r) populateFeatureSelect(r.feature_meta || {}, r.raw_meta || {});
        renderAll();
        if (window.appendLog) window.appendLog('🗑️ 시각화 항목 초기화', '');
    }

    /* ──────────────────────────────────────────────
       6. 모드 변경 (Overlay ↔ Panel)
    ────────────────────────────────────────────── */
    function setFeatureMode(id, mode) {
        setMode(id, mode);
        // UI 토글 버튼 즉시 갱신
        const r = window.STATE.lastChartResult;
        if (r) populateFeatureSelect(r.feature_meta || {}, r.raw_meta || {});
        renderAll();
    }

    /* ──────────────────────────────────────────────
       7. 기존 렌더링 정리
    ────────────────────────────────────────────── */
    function clearPanels() {
        // X축 동기화 해제
        if (_unsubSync) { try { _unsubSync(); } catch (_) {} _unsubSync = null; }
        // 서브패널 LWC 인스턴스 제거
        _panelCharts.forEach(({ chart }) => { try { chart.remove(); } catch (_) {} });
        _panelCharts = [];
        // 가격 차트 overlay 시리즈 제거
        if (window.STATE.lwcPriceChart) {
            Object.keys(_overlaySeriesMap).forEach(id => {
                const series = _overlaySeriesMap[id];
                try {
                    // Object is disposed 예외 방지: 이미 소멸된 상태가 아닌지 사전 체크
                    if (series && series._private__series && series._private__series._private__commonRow === null) {
                        // LWC 내부 disposed 상태
                    } else if (series) {
                        window.STATE.lwcPriceChart.removeSeries(series);
                    }
                } catch (_) {}
            });
            try { window.STATE.lwcPriceChart.priceScale('left').applyOptions({ visible: false }); } catch (_) {}
        }
        _overlaySeriesMap = {};
        if (window.STATE) {
            window.STATE.activeOverlaySeriesMap = {};
        }
        // 컨테이너 비우기
        const c = document.getElementById('featureChartsContainer');
        if (c) c.innerHTML = '';
    }

    /* ──────────────────────────────────────────────
       8. 드래그 리사이저 생성
    ────────────────────────────────────────────── */
    function makeResizeHandle(panelWrapper, chart, minHeight, maxHeight) {
        const handle = document.createElement('div');
        handle.style.cssText = [
            'width:100%', 'height:6px', 'cursor:ns-resize',
            'background:transparent',
            'border-top:2px solid #2a2a32',
            'display:flex', 'align-items:center', 'justify-content:center',
            'user-select:none', 'transition:border-color .15s',
        ].join(';');
        handle.innerHTML = '<span style="width:32px;height:2px;background:#3a3a46;border-radius:2px;"></span>';
        handle.addEventListener('mouseenter', () => { handle.style.borderTopColor = '#5c5c70'; });
        handle.addEventListener('mouseleave', () => { handle.style.borderTopColor = '#2a2a32'; });

        let startY = 0, startH = 0;
        handle.addEventListener('mousedown', e => {
            e.preventDefault();
            startY = e.clientY;
            startH = panelWrapper.offsetHeight;
            const onMove = ev => {
                const delta = ev.clientY - startY;
                const newH = Math.min(maxHeight, Math.max(minHeight, startH + delta));
                panelWrapper.style.height = newH + 'px';
                chart.resize(panelWrapper.clientWidth, newH - 6); // -6 for handle
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
        return handle;
    }

    /* ──────────────────────────────────────────────
       9. 메인 렌더링
    ────────────────────────────────────────────── */
    function renderAll() {
        const c = document.getElementById('featureChartsContainer');
        if (!c) return;
        const r = window.STATE.lastChartResult;

        if (!r || !r.chart) {
            c.innerHTML = '<div class="alert alert-info" style="font-size:12px;margin:0;">차트를 먼저 로드하세요.</div>';
            return;
        }
        if (window.STATE.featureChartIds.length === 0) {
            clearPanels();
            c.innerHTML = '<div class="alert alert-info" style="font-size:12px;margin:0;">원본 데이터 또는 계산 지표를 선택하면 가격 차트와 함께 시계열로 표시됩니다.</div>';
            return;
        }

        const { timestamps, close } = r.chart;
        const features    = r.chart.features  || {};
        const raw         = r.chart.raw_data  || {};
        const featureMeta = r.feature_meta    || {};
        const rawMeta     = r.raw_meta        || {};

        // 선택 항목 분류
        const overlayItems  = [];
        const subpanelItems = [];

        window.STATE.featureChartIds.forEach(id => {
            let targetId = id;
            let inFeature = id in features;
            let inRaw     = id in raw;

            // 바이낸스 원화(KRW) 환산 토글 처리
            if (window.STATE.binanceKrwChecked && !inFeature && inRaw && id.startsWith('binance_spot_')) {
                const krwId = id + '_krw';
                if (krwId in raw) {
                    targetId = krwId;
                }
            }

            inFeature = targetId in features;
            inRaw     = targetId in raw;
            if (!inFeature && !inRaw) return;

            const meta   = inFeature ? featureMeta[targetId] : rawMeta[targetId];
            const values = inFeature ? features[targetId] : raw[targetId];
            const label  = getLabel(targetId, meta, !inFeature);
            const color  = inFeature ? colorFor(targetId, meta?.category) : '#ff79c6';
            const mode   = getMode(id, inFeature ? meta : { category: '' }); // 오버레이/패널 제어는 원래 체크박스 id 기준 유지
            const clean  = values.map(v => (v == null || isNaN(v)) ? null : v);

            if (mode === 'overlay') {
                overlayItems.push({ id: targetId, label, values: clean, color, meta });
            } else {
                let kind = 'line';
                if (VOLUME_LIKE_IDS.has(targetId))  kind = 'bar';
                else if (RSI_LIKE_IDS.has(targetId)) kind = 'rsi';
                subpanelItems.push({ id: targetId, label, values: clean, color, kind,
                    category: (featureMeta[targetId] || rawMeta[targetId] || {}).category || '' });
            }
        });

        // 기존 패널 정리
        clearPanels();

        const allCharts = [];
        if (window.STATE.lwcPriceChart) allCharts.push(window.STATE.lwcPriceChart);

        // ── SubPanel → 별도 LWC 인스턴스 렌더링 ──
        if (subpanelItems.length > 0) {
            subpanelItems.forEach((item, idx) => {
                const DEFAULT_H = 150;
                const MIN_H = 80;
                const MAX_H = 400;

                // 패널 wrapper div
                const wrapper = document.createElement('div');
                wrapper.style.cssText = [
                    'width:100%',
                    `height:${DEFAULT_H + 6}px`, // +6 for resize handle
                    'position:relative',
                    'overflow:hidden',
                    'border-radius:4px',
                    'margin-bottom:4px',
                ].join(';');

                // 패널 내부 차트 div
                const chartDiv = document.createElement('div');
                chartDiv.style.cssText = `width:100%;height:${DEFAULT_H}px;`;

                wrapper.appendChild(chartDiv);
                c.appendChild(wrapper);

                // LWC 인스턴스 생성
                const chart = LightweightCharts.createChart(chartDiv, {
                    ...LWC_THEME,
                    width:  chartDiv.clientWidth,
                    height: DEFAULT_H,
                    rightPriceScale: {
                        ...LWC_THEME.rightPriceScale,
                        scaleMargins: { top: 0.1, bottom: 0.1 },
                    },
                    // 마지막 패널에만 시간축 표시
                    timeScale: {
                        ...LWC_THEME.timeScale,
                        visible: idx === subpanelItems.length - 1,
                    },
                });

            // 첫 번째 지표 차트 인스턴스를 오버레이용 베이스로 지정
            if (idx === 0) {
                lwcFeatureBaseChart = chart;
            }

            // 지표 종류별 시리즈 생성
            if (item.kind === 'bar') {
                const series = chart.addSeries
                    ? chart.addSeries(LightweightCharts.HistogramSeries, {
                        color: item.color,
                        title: item.label,
                    })
                    : chart.addHistogramSeries({
                        color: item.color,
                        title: item.label,
                    });
                series.setData(lwcPrepareHistogram(timestamps, item.values, close));
                chart._firstSeries = series;

            } else if (item.kind === 'rsi') {
                const series = chart.addSeries
                    ? chart.addSeries(LightweightCharts.LineSeries, {
                        color: item.color,
                        lineWidth: 1.5,
                        title: item.label,
                    })
                    : chart.addLineSeries({
                        color: item.color,
                        lineWidth: 1.5,
                        title: item.label,
                    });
                series.setData(lwcPrepareLine(timestamps, item.values));
                chart._firstSeries = series;
                // 기준선 30/50/70
                series.createPriceLine({ price: 30, color: 'rgba(38,166,154,0.55)', lineStyle: 2, lineWidth: 1, title: '30' });
                series.createPriceLine({ price: 50, color: 'rgba(255,255,255,0.18)', lineStyle: 1, lineWidth: 1 });
                series.createPriceLine({ price: 70, color: 'rgba(239,83,80,0.55)',   lineStyle: 2, lineWidth: 1, title: '70' });

            } else {
                // 기본 선 or 모멘텀(area)
                const isMomentumLike = ['momentum', 'divergence'].includes(item.category);
                let series;
                if (isMomentumLike) {
                    series = chart.addSeries
                        ? chart.addSeries(LightweightCharts.AreaSeries, {
                            lineColor:   item.color,
                            topColor:    item.color + '30',
                            bottomColor: item.color + '08',
                            lineWidth: 1.5,
                            title: item.label,
                        })
                        : chart.addAreaSeries({
                            lineColor:   item.color,
                            topColor:    item.color + '30',
                            bottomColor: item.color + '08',
                            lineWidth: 1.5,
                            title: item.label,
                        });
                } else {
                    series = chart.addSeries
                        ? chart.addSeries(LightweightCharts.LineSeries, {
                            color: item.color,
                            lineWidth: 1.5,
                            title: item.label,
                        })
                        : chart.addLineSeries({
                            color: item.color,
                            lineWidth: 1.5,
                            title: item.label,
                        });
                }
                series.setData(lwcPrepareLine(timestamps, item.values));
                chart._firstSeries = series;
                // 0 기준선
                if (isMomentumLike) {
                    series.createPriceLine({ price: 0, color: 'rgba(255,255,255,0.20)', lineStyle: 1, lineWidth: 1 });
                }
            }

            // 개별 패널 툴팁 생성
            if (window.lwcMakePanelTooltip && chart._firstSeries) {
                lwcMakePanelTooltip(chartDiv, chart, chart._firstSeries, item.label);
            }

            // 드래그 리사이저
            const resizeHandle = makeResizeHandle(wrapper, chart, MIN_H, MAX_H);
            wrapper.appendChild(resizeHandle);

            allCharts.push(chart);
            _panelCharts.push({ chart, wrapper });
        });
        } // if (subpanelItems.length > 0) 블록 종료

        // ── Overlay → 메인 가격 차트(lwcPriceChart)에 직접 LineSeries 추가 ──
        if (window.STATE.lwcPriceChart && overlayItems.length > 0) {
            // 왼쪽 Y축(left price scale)을 사용해야 하는 아이템이 있는지 검사
            const hasLeftScaleItems = overlayItems.some(item => !(window.STATE.binanceKrwChecked && item.id.endsWith('_krw')));

            try {
                window.STATE.lwcPriceChart.priceScale('left').applyOptions({
                    visible: hasLeftScaleItems,
                    borderColor: '#2a2a32',
                    textColor: '#ff79c6',
                });
            } catch (_) {}

            overlayItems.forEach(item => {
                try {
                    const useRightScale = window.STATE.binanceKrwChecked && item.id.endsWith('_krw');
                    const targetScaleId = useRightScale ? 'right' : 'left';

                    console.log(`[Overlay Debug] Adding overlay series to window.STATE.lwcPriceChart: ${item.id} (${item.label}) on scale: ${targetScaleId}`);
                    const series = window.STATE.lwcPriceChart.addSeries
                        ? window.STATE.lwcPriceChart.addSeries(LightweightCharts.LineSeries, {
                            color: item.color,
                            lineWidth: 2.0,
                            title: item.label,
                            priceScaleId: targetScaleId,
                        })
                        : window.STATE.lwcPriceChart.addLineSeries({
                            color: item.color,
                            lineWidth: 2.0,
                            title: item.label,
                            priceScaleId: targetScaleId,
                        });
                    const preparedData = lwcPrepareLine(timestamps, item.values);
                    series.setData(preparedData);
                    _overlaySeriesMap[item.id] = series;
                    if (window.appendLog) window.appendLog(`ℹ️ [지표 오버레이 반영] ${item.label} (${preparedData.length}포인트) 가격 차트(${targetScaleId === 'right' ? '우측' : '좌측'}축)에 추가됨`, '');
                } catch (e) {
                    console.error('[Overlay Debug] Error during overlay to lwcPriceChart:', e);
                }
            });
            if (hasLeftScaleItems) {
                try {
                    window.STATE.lwcPriceChart.priceScale('left').applyOptions({ autoScale: true });
                } catch (_) {}
            }
        }

        // 전역 상태에 오버레이 맵 공유 (lwc_charts.js에서 풍선도움말 수치 추출용)
        if (window.STATE) {
            window.STATE.activeOverlaySeriesMap = _overlaySeriesMap;
        }

        // X축 동기화 (가격 차트 + 생성된 모든 하위 서브 패널)
        if (allCharts.length > 1) {
            _unsubSync = lwcSyncCharts(allCharts);
        }
    }

    /* ──────────────────────────────────────────────
       10. 리사이즈 (탭 전환 시 호출)
    ────────────────────────────────────────────── */
    function resizeLwcFeatureCharts() {
        _panelCharts.forEach(({ chart, wrapper }) => {
            const h = wrapper.clientHeight - 6;
            if (h > 0) chart.resize(wrapper.clientWidth, h);
        });
    }

    /* ──────────────────────────────────────────────
       11. 데이터 품질 진단 패널 (지표별 스마트 그룹화 & 접이식 요약)
    ────────────────────────────────────────────── */
    function renderDiagnosis(zones) {
        const el = document.getElementById('dataQualityDiagnosis');
        if (!el) return;
        if (!zones || zones.length === 0) {
            el.innerHTML = '<div class="alert alert-success" style="font-size:11px;margin:0;">✅ 이상 구간(결측/백필/이상치)이 감지되지 않았습니다. (데이터 품질 우수)</div>';
            return;
        }

        // 1. 지표별 그룹화 및 집계
        const featMap = {};
        zones.forEach(z => {
            const fName = z.feature || '기타';
            if (!featMap[fName]) {
                featMap[fName] = {
                    feature: fName,
                    severity: z.severity || 'warning',
                    detail: z.detail || '',
                    totalRows: 0,
                    zoneList: []
                };
            }
            featMap[fName].totalRows += (z.count || 0);
            featMap[fName].zoneList.push(z);
            if (z.severity === 'critical') {
                featMap[fName].severity = 'critical';
            }
        });

        const featKeys = Object.keys(featMap);
        const critCount = featKeys.filter(k => featMap[k].severity === 'critical').length;
        const warnCount = featKeys.filter(k => featMap[k].severity === 'warning').length;
        const totalZoneCount = zones.length;

        // 2. 상단 상태 요약 툴바
        let html = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid rgba(255,255,255,0.06); font-size:11px;">
            <div style="display:flex; gap:6px; align-items:center;">
                <span class="badge" style="background:${critCount > 0 ? 'rgba(239,68,68,0.2)' : 'rgba(34,197,94,0.15)'}; color:${critCount > 0 ? '#f87171' : '#4ade80'}; font-size:10px; padding:2px 6px;">
                    ${critCount > 0 ? '🔴 치명적 ' + critCount + '종목' : '✅ 치명적 이상 없음'}
                </span>
                ${warnCount > 0 ? `<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; font-size:10px; padding:2px 6px;">🟡 주의 ${warnCount}종목</span>` : ''}
            </div>
            <span style="color:var(--text-muted); font-size:10.5px;">총 ${totalZoneCount.toLocaleString()}개 구간 감지</span>
        </div>
        <div style="display:flex; flex-direction:column; gap:6px; max-height:180px; overflow-y:auto; padding-right:4px;">
        `;

        // 3. 지표별 접이식 아코디언 카드 렌더링
        featKeys.forEach(fName => {
            const item = featMap[fName];
            const isCrit = item.severity === 'critical';
            const color = isCrit ? '#f87171' : '#fbbf24';
            const borderCol = isCrit ? 'rgba(239,68,68,0.3)' : 'rgba(245,158,11,0.3)';
            const bgCol = isCrit ? 'rgba(239,68,68,0.04)' : 'rgba(245,158,11,0.04)';
            const icon = isCrit ? '🔴' : '🟡';

            const subRows = item.zoneList.slice(0, 4).map(z => 
                `<div style="font-size:10px; color:var(--text-secondary); margin-bottom:2px; padding-left:8px; border-left:2px solid ${borderCol};">
                    • ${z.start} ~ ${z.end} <span style="color:var(--text-muted);">(${z.count}행)</span>
                </div>`
            ).join('');

            const moreText = item.zoneList.length > 4 
                ? `<div style="font-size:9.5px; color:var(--text-muted); padding-left:8px; margin-top:2px;">... 외 ${item.zoneList.length - 4}개 구간 생략됨</div>` 
                : '';

            html += `
            <details style="background:${bgCol}; border:1px solid ${borderCol}; border-radius:6px; padding:6px 8px; font-size:11px;">
                <summary style="cursor:pointer; display:flex; justify-content:space-between; align-items:center; user-select:none; font-weight:600; outline:none;">
                    <div style="display:flex; align-items:center; gap:6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                        <span>${icon}</span>
                        <strong style="color:${color}; font-family:var(--font-mono); font-size:11px;">${fName}</strong>
                    </div>
                    <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
                        <span style="font-size:10px; color:var(--text-muted);">${item.zoneList.length}구간 (${item.totalRows.toLocaleString()}행)</span>
                        <span style="font-size:9px; color:var(--text-muted);">▼</span>
                    </div>
                </summary>
                <div style="margin-top:6px; padding-top:6px; border-top:1px dashed rgba(255,255,255,0.06);">
                    <div style="font-size:10.5px; color:var(--text-primary); margin-bottom:4px;">
                        ⚠️ <strong>${item.detail || '데이터 수집 누락 / 백필 더미 감지'}</strong>
                    </div>
                    ${subRows}
                    ${moreText}
                </div>
            </details>
            `;
        });

        html += '</div>';
        el.innerHTML = html;
    }

    /* ──────────────────────────────────────────────
       12. 전역 노출
    ────────────────────────────────────────────── */
    window.populateFeatureSelect      = populateFeatureSelect;
    window.toggleFeatureChart         = toggleFeatureChart;
    window.selectAllFeatures          = selectAllFeatures;
    window.deselectAllFeatures        = deselectAllFeatures;
    window.setFeatureMode             = setFeatureMode;
    window.addFeatureToChart          = selectAllFeatures;
    window.clearFeatureCharts         = deselectAllFeatures;
    window.showAllFeatures            = selectAllFeatures;
    window.renderAllFeatureCharts     = renderAll;
    window.resizeLwcFeatureCharts     = resizeLwcFeatureCharts;
    window.removeFeatureChart         = id => {
        window.STATE.featureChartIds = window.STATE.featureChartIds.filter(x => x !== id);
        renderAll();
    };
    window.renderDataQualityDiagnosis = renderDiagnosis;

})();
