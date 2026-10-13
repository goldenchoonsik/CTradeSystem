/**
 * CAiModelBaker — lwc_charts.js
 * Lightweight Charts v5 공통 테마 / 유틸리티
 */
(function () {

    /* ─────────────────────────────────────────────
       공통 테마
    ───────────────────────────────────────────── */
    const LWC_THEME = {
        layout: {
            background: { type: 'solid', color: '#18181c' },
            textColor: '#a0a0b0',
            fontSize: 11,
            fontFamily: "'Inter', 'Noto Sans KR', sans-serif",
        },
        grid: {
            vertLines: { color: '#2a2a32', style: 0 },
            horzLines: { color: '#2a2a32', style: 0 },
        },
        crosshair: {
            mode: 0,
            vertLine: { color: '#5c5c70', width: 1, style: 3, labelBackgroundColor: '#2a2a32' },
            horzLine: { color: '#5c5c70', width: 1, style: 3, labelBackgroundColor: '#2a2a32' },
        },
        rightPriceScale: {
            borderColor: '#2a2a32',
            textColor: '#a0a0b0',
            minimumWidth: 100,
            maximumWidth: 100,
        },
        leftPriceScale: {
            borderColor: '#2a2a32',
            textColor: '#ff79c6',
            minimumWidth: 100,
            maximumWidth: 100,
            visible: false,
        },
        timeScale: {
            borderColor: '#2a2a32',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 8,
            barSpacing: 6,
            uniformDistribution: true,
            tickMarkFormatter: (time, tickMarkType, locale) => {
                const date = new Date(time * 1000);
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');

                // tickMarkType: 0=Year, 1=Month, 2=DayOfMonth, 3=Time, 4=TimeWithSeconds
                if (tickMarkType === 3 || tickMarkType === 4) {
                    return `${month}-${day} ${hours}:${minutes}`;
                }
                return `${month}-${day}`;
            },
        },
        localization: {
            locale: 'ko-KR',
            timeFormatter: (time) => {
                const date = new Date(time * 1000);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');
                return `${year}-${month}-${day} ${hours}:${minutes}`;
            },
        },
    };

    /* ─────────────────────────────────────────────
       ISO 8601 → Unix 초
    ───────────────────────────────────────────── */
    function toUnixSec(isoStr) {
        return Math.floor(new Date(isoStr).getTime() / 1000);
    }

    /* ─────────────────────────────────────────────
       OHLCV 배열 변환 (오름차순 보장)
    ───────────────────────────────────────────── */
    function prepareLwcCandles(timestamps, open, high, low, close) {
        const arr = timestamps.map((t, i) => ({
            time: toUnixSec(t),
            open:  open[i],
            high:  high[i],
            low:   low[i],
            close: close[i],
        }));
        arr.sort((a, b) => a.time - b.time);
        return arr;
    }

    function prepareLwcLine(timestamps, values) {
        const arr = timestamps.map((t, i) => ({
            time:  toUnixSec(t),
            value: values[i] == null || isNaN(values[i]) ? null : values[i],
        })).filter(p => p.value !== null);
        arr.sort((a, b) => a.time - b.time);
        return arr;
    }

    function prepareLwcHistogram(timestamps, values, close) {
        const arr = timestamps.map((t, i) => {
            const v = values[i] == null || isNaN(values[i]) ? null : values[i];
            if (v === null) return null;
            const up = (close && i > 0 && close[i] != null && close[i - 1] != null)
                ? close[i] >= close[i - 1]
                : true;
            return {
                time:  toUnixSec(t),
                value: v,
                color: up ? 'rgba(38,166,154,0.70)' : 'rgba(239,83,80,0.70)',
            };
        }).filter(Boolean);
        arr.sort((a, b) => a.time - b.time);
        return arr;
    }

    /* ─────────────────────────────────────────────
       여러 LWC 인스턴스 X축 동기화
    ───────────────────────────────────────────── */
    function syncCharts(charts) {
        let syncing = false;
        const handlers = charts.map((src, srcIdx) => {
            const handler = (range) => {
                if (syncing || !range) return;
                syncing = true;
                charts.forEach((dst, dstIdx) => {
                    if (dstIdx !== srcIdx) {
                        try { dst.timeScale().setVisibleLogicalRange(range); } catch (_) {}
                    }
                });
                syncing = false;
            };
            src.timeScale().subscribeVisibleLogicalRangeChange(handler);
            return { chart: src, handler };
        });

        // 📌 마우스 세로줄(크로스헤어) 전 패널 동시 동기화 연동
        syncCrosshairs(charts);

        return () => {
            handlers.forEach(({ chart, handler }) => {
                try { chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler); } catch (_) {}
            });
        };
    }

    /* ─────────────────────────────────────────────
       크로스헤어 전 패널 동시 표시
    ───────────────────────────────────────────── */
    function syncCrosshairs(charts) {
        let moving = false;
        charts.forEach((src, si) => {
            src.subscribeCrosshairMove((param) => {
                if (moving) return;
                moving = true;
                charts.forEach((dst, di) => {
                    if (di !== si) {
                        try {
                            if (param && param.time != null) {
                                // LWC에서 스케일 및 줌 변화 상태에 영향받지 않는 정확한 데이터 포인트 시간대 추출
                                const time = param.time;
                                const targetSeries = dst._firstSeries;
                                if (targetSeries) {
                                    // time 및 targetSeries를 함께 매핑하여 논리적 가로축 세로선 강제 정합
                                    dst.setCrosshairPosition(0, time, targetSeries);
                                } else {
                                    // 캔들 시리즈가 없을 때 대비 fallback
                                    dst.setCrosshairPosition(0, time);
                                }
                            } else {
                                dst.clearCrosshairPosition();
                            }
                        } catch (err) {
                            try {
                                if (param && param.time) {
                                    dst.setCrosshairPosition(0, param.time);
                                } else {
                                    dst.clearCrosshairPosition();
                                }
                            } catch (_) {}
                        }
                    }
                });
                moving = false;
            });
        });
    }

    /* ─────────────────────────────────────────────
       OHLCV 호버 툴팁
    ───────────────────────────────────────────── */
    function makePriceTooltip(container, chart, candleSeries) {
        const tooltip = document.createElement('div');
        tooltip.style.cssText = [
            'position:absolute', 'top:6px', 'left:12px', 'z-index:100',
            'pointer-events:none', 'display:none',
            'font-size:11px', 'font-family:Inter,monospace',
            'color:#e0e0e8', 'background:rgba(24,24,28,0.92)',
            'border:1px solid #444452', 'border-radius:6px',
            'padding:6px 12px', 'line-height:1.6', 'white-space:nowrap',
            'box-shadow: 0 4px 12px rgba(0,0,0,0.5)',
        ].join(';');
        container.style.position = 'relative';
        container.appendChild(tooltip);

        chart.subscribeCrosshairMove((param) => {
            if (!param || !param.time || !param.seriesData) {
                tooltip.style.display = 'none';
                return;
            }
            const bar = param.seriesData.get(candleSeries);
            if (!bar) { tooltip.style.display = 'none'; return; }

            const dt = new Date(param.time * 1000);
            const dtStr = dt.toISOString().replace('T', ' ').slice(0, 16);
            const fmt = (v) => v != null ? Math.round(Number(v)).toLocaleString('ko-KR') : '-';
            const chg = bar.open != null && bar.close != null
                ? (((bar.close - bar.open) / bar.open) * 100).toFixed(2)
                : '-';
            const chgColor = parseFloat(chg) >= 0 ? '#26a69a' : '#ef5350';

            // 기본 캔들 텍스트 생성
            let tooltipContent = 
                `<div style="font-weight:bold;color:#a0a0b0;margin-bottom:3px;border-bottom:1px solid #3a3a46;padding-bottom:2px;">${dtStr}</div>` +
                `<div>` +
                `<span style="color:#f1fa8c">시</span> ${fmt(bar.open)} &nbsp;` +
                `<span style="color:#26a69a">고</span> ${fmt(bar.high)} &nbsp;` +
                `<span style="color:#ef5350">저</span> ${fmt(bar.low)} &nbsp;` +
                `<span style="color:#8be9fd">종</span> ${fmt(bar.close)} &nbsp;` +
                `<span style="color:${chgColor}">${parseFloat(chg) >= 0 ? '+' : ''}${chg}%</span>` +
                `</div>`;

            // 오버레이 지표 데이터 취합 및 격차(김프) 분석
            if (window.STATE && window.STATE.activeOverlaySeriesMap) {
                Object.entries(window.STATE.activeOverlaySeriesMap).forEach(([id, series]) => {
                    try {
                        const data = param.seriesData.get(series);
                        if (data && data.value !== undefined) {
                            const val = data.value;
                            
                            // 지표 명칭 매핑
                            let label = id;
                            if (window.STATE.lastChartResult) {
                                const rawMeta = window.STATE.lastChartResult.raw_meta || {};
                                const featMeta = window.STATE.lastChartResult.feature_meta || {};
                                const m = rawMeta[id] || featMeta[id];
                                if (m && m.name) {
                                    label = m.name;
                                }
                            }

                            // 업비트 가격과 오버레이 간 격차 계산
                            let gapInfo = '';
                            if (bar.close != null && (id.endsWith('_krw') || id.includes('binance_spot_close_krw'))) {
                                const diffVal = val - bar.close;
                                const diffPct = (diffVal / bar.close) * 100;
                                const diffColor = diffVal >= 0 ? '#26a69a' : '#ef5350';
                                const diffSign = diffVal >= 0 ? '+' : '';
                                gapInfo = ` <span style="color:${diffColor};font-size:10px;">(${diffSign}${fmt(diffVal)}원 / ${diffSign}${diffPct.toFixed(2)}%)</span>`;
                            }

                            const colorCode = series.options().color || '#ff79c6';
                            tooltipContent += `<div style="color:${colorCode};font-size:10px;border-top:1px dashed #3a3a46;margin-top:3px;padding-top:2px;">`;
                            tooltipContent += `↳ <strong>${label}</strong>: ${fmt(val)}원${gapInfo}`;
                            tooltipContent += `</div>`;
                        }
                    } catch (_) {}
                });
            }

            tooltip.innerHTML = tooltipContent;
        });

        return tooltip;
    }

    function makePanelTooltip(container, chart, series, label) {
        const tooltip = document.createElement('div');
        tooltip.style.cssText = [
            'position:absolute', 'top:6px', 'left:12px', 'z-index:100',
            'pointer-events:none', 'display:none',
            'font-size:11px', 'font-family:Inter,monospace',
            'color:#e0e0e8', 'background:rgba(24,24,28,0.92)',
            'border:1px solid #444452', 'border-radius:6px',
            'padding:4px 10px', 'line-height:1.5', 'white-space:nowrap',
            'box-shadow: 0 4px 12px rgba(0,0,0,0.5)',
        ].join(';');
        container.style.position = 'relative';
        container.appendChild(tooltip);

        chart.subscribeCrosshairMove((param) => {
            if (!param || !param.time || !param.seriesData) {
                tooltip.style.display = 'none';
                return;
            }
            const data = param.seriesData.get(series);
            if (!data || data.value === undefined) { tooltip.style.display = 'none'; return; }

            const dt = new Date(param.time * 1000);
            const dtStr = dt.toISOString().replace('T', ' ').slice(0, 16);
            const val = data.value;
            let formattedVal;
            if (Math.abs(val) < 1.0 && val !== 0) {
                formattedVal = val.toFixed(4);
            } else if (Math.abs(val) < 100 && val !== 0) {
                formattedVal = val.toFixed(2);
            } else {
                formattedVal = Math.round(val).toLocaleString('ko-KR');
            }

            tooltip.innerHTML = `<span style="color:#a0a0b0;font-weight:bold;">[${dtStr}]</span> &nbsp;` +
                                 `<span style="color:${series.options().color || '#8be9fd'}; font-weight:bold;">${label}</span>: ${formattedVal}`;
            tooltip.style.display = 'block';
        });

        return tooltip;
    }

    /* ─────────────────────────────────────────────
       전역 노출
    ───────────────────────────────────────────── */
    window.LWC_THEME             = LWC_THEME;
    window.lwcToUnixSec          = toUnixSec;
    window.lwcPrepareCandles     = prepareLwcCandles;
    window.lwcPrepareLine        = prepareLwcLine;
    window.lwcPrepareHistogram   = prepareLwcHistogram;
    window.lwcSyncCharts         = syncCharts;
    window.lwcSyncCrosshairs     = syncCrosshairs;
    window.lwcMakePriceTooltip   = makePriceTooltip;
    window.lwcMakePanelTooltip   = makePanelTooltip;

})();
