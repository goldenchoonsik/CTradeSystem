/**
 * CAiModelBaker - WFA & 홀드아웃 타임라인 매니저 (파라미터 주도형 선언적 역산 엔진)
 * Plotly Gantt 타임라인 + 데이터 스캔 기반 무결 분할
 */
(function () {
    const COLORS = {
        train: "rgba(99,102,241,0.85)",
        opt: "rgba(245,158,11,0.85)",
        test: "rgba(34,197,94,0.85)",
        holdout: "rgba(239,68,68,0.85)"
    };

    let _refreshTimer = null;
    function scheduleAsrlRefresh() {
        clearTimeout(_refreshTimer);
        _refreshTimer = setTimeout(() => refreshAsrlTimeline(), 250);
    }

    function initAsrlTimeline() {
        // 파라미터 컨트롤 변경 시 실시간 타임라인 역산 갱신 (디바운스)
        ['wfaTrainMonths', 'wfaOptMonths', 'wfaTestMonths', 'wfaWindowCount', 'asrlHoldoutTd', 'asrl_final_test_ratio', 'autolabFinalTestTd'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('input', scheduleAsrlRefresh);
                el.addEventListener('change', scheduleAsrlRefresh);
            }
        });

        refreshAsrlTimeline();
    }

    function getTimelineParams() {
        const params = new URLSearchParams();
        const trainEl = document.getElementById('wfaTrainMonths');
        const optEl = document.getElementById('wfaOptMonths');
        const testEl = document.getElementById('wfaTestMonths');
        const winCntEl = document.getElementById('wfaWindowCount');
        const holdoutTdEl = document.getElementById('asrlHoldoutTd');

        const curUnit = window._currentSplitUnit || 'td';
        params.set('unit', curUnit);

        const trainVal = parseFloat(trainEl?.value) || 28;
        const optVal = parseFloat(optEl?.value) || 7;
        const testVal = parseFloat(testEl?.value) || 7;
        const winCnt = parseInt(winCntEl?.value) || 5;
        const holdoutTd = parseInt(holdoutTdEl?.value) || 14;

        params.set('train_td', trainVal);
        params.set('opt_td', optVal);
        params.set('test_td', testVal);
        params.set('train_months', trainVal);
        params.set('opt_months', optVal);
        params.set('test_months', testVal);
        params.set('wfa_windows_count', winCnt);
        params.set('holdout_td', holdoutTd);

        return params;
    }

    // 여러 곳(입력 변경·스마트 분할·탭 전환)이 동시에 부르므로 늦게 도착한 옛 응답이 새 결과를 덮지 않게 한다
    let _reqSeq = 0;

    async function refreshAsrlTimeline() {
        const params = getTimelineParams();
        const mySeq = ++_reqSeq;

        try {
            const res = await fetch(`/api/asrl/timeline?${params.toString()}`);
            const data = await res.json();
            if (mySeq !== _reqSeq) return;

            // 1. 역산된 날짜 결과 뷰 및 hidden input 동기화
            if (data.wfa_start && data.wfa_end) {
                const sEl = document.getElementById('lblWfaStartDate');
                const eEl = document.getElementById('lblWfaEndDate');
                const hidStart = document.getElementById('wfaStartDate');
                const hidEnd = document.getElementById('wfaEndDate');
                if (sEl) sEl.textContent = data.wfa_start;
                if (eEl) eEl.textContent = data.wfa_end;
                if (hidStart) hidStart.value = data.wfa_start;
                if (hidEnd) hidEnd.value = data.wfa_end;
            }

            if (data.holdout_start && data.holdout_end) {
                const pEl = document.getElementById('lblHoldoutPeriod');
                const hidHStart = document.getElementById('asrl_holdout_start');
                const hidHEnd = document.getElementById('asrl_holdout_end');
                const hTd = data.holdout_td || 14;
                if (pEl) pEl.textContent = `${data.holdout_start} ~ ${data.holdout_end} (${hTd}거래일)`;
                if (hidHStart) hidHStart.value = data.holdout_start;
                if (hidHEnd) hidHEnd.value = data.holdout_end;
            }

            // 2. 간트차트 렌더링
            renderAsrlTimeline(data);

            // 3. 상단 요약 뱃지 및 상태 배너 렌더링
            renderTimelineSummary(data);
            renderStatusAlignPanel(data);

            // 4. 홀드아웃 세부 구간 업데이트 연동
            if (typeof updateAsrlTimelineSplit === 'function') {
                updateAsrlTimelineSplit();
            }
        } catch (e) {
            const el = document.getElementById('asrlTimelineContainer');
            if (el) el.innerHTML = '<div class="alert alert-danger">타임라인 로드 실패: ' + e.message + '</div>';
        }
    }

    const LEGEND_NAMES = {
        train: "Train (학습)",
        opt: "Opt (최적화)",
        test: "Test (전진검증)",
        holdout: "최종 홀드아웃"
    };

    function renderAsrlTimeline(data) {
        const container = document.getElementById('asrlTimelineContainer');
        if (!container) return;

        if (!data || !data.windows || data.windows.length === 0) {
            if (typeof Plotly !== 'undefined' && container.data) Plotly.purge(container);
            container.innerHTML = '<div class="alert alert-info" style="font-size:13px; text-align:center; padding:15px;">WFA 윈도우 구성 불가 — 데이터 기간 또는 WFA 설정을 확인하세요.</div>';
            return;
        }
        // 직전에 쓴 안내문(innerHTML)을 지우지 않으면 Plotly 가 그 아래에 그려 '구성 불가'와 차트가 겹친다 (2026-10-20)
        if (!container.data) container.innerHTML = '';

        const plotData = [];
        const seenLegends = new Set();

        data.windows.forEach((w, i) => {
            const rowName = `W${i + 1}`;
            [['train', 'Train'], ['opt', 'Opt'], ['test', 'Test(OOS)']].forEach(([type, label]) => {
                const showLeg = !seenLegends.has(type);
                if (showLeg) seenLegends.add(type);
                plotData.push(barTrace(rowName, label, type, w[`${type}_start`], w[`${type}_end`], showLeg));
            });
        });

        if (data.holdout_start && data.holdout_end) {
            const showLeg = !seenLegends.has('holdout');
            if (showLeg) seenLegends.add('holdout');
            plotData.push(barTrace('★ 최종 홀드아웃', '최종 홀드아웃', 'holdout', data.holdout_start, data.holdout_end, showLeg));
        }

        const layout = {
            barmode: 'stack',
            height: Math.max(220, (data.windows.length + 2) * 32),
            margin: { l: 95, r: 25, t: 30, b: 35 },
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            font: { color: '#a0a0b0', size: 11 },
            xaxis: { type: 'date', gridcolor: 'rgba(255,255,255,0.06)' },
            yaxis: { autorange: 'reversed', gridcolor: 'rgba(255,255,255,0.06)' },
            showlegend: true,
            legend: { orientation: 'h', y: 1.15, x: 0, font: { size: 10 } },
            hovermode: 'closest'
        };

        Plotly.react(container, plotData, layout, { responsive: true });
    }

    function barTrace(rowName, label, type, start, end, showLegend = false) {
        const startD = new Date(start);
        const endD = new Date(end);
        const diffDays = Math.max(1, Math.round((endD - startD) / 86400000));
        const baseColor = COLORS[type];
        const legendName = LEGEND_NAMES[type] || type;

        return {
            type: 'bar',
            orientation: 'h',
            name: legendName,
            legendgroup: type,
            showlegend: showLegend,
            x: [endD.getTime() - startD.getTime()],
            base: [startD.getTime()],
            y: [rowName],
            marker: { color: baseColor },
            text: [`${start} ~ ${end}`],
            textposition: 'none',
            hovertemplate: `<b>${rowName} ${label}</b><br>기간: ${start} ~ ${end} (${diffDays}일)<extra></extra>`
        };
    }

    function renderStatusAlignPanel(data) {
        const warnEl = document.getElementById('asrlClampWarning');
        if (!warnEl) return;

        let statusHtml = '';
        if (data.data_exceeded) {
            statusHtml = '<div style="background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.5);border-radius:6px;padding:8px 12px;font-size:12px;line-height:1.6;">' +
                '⚠️ <strong>보유 데이터 범위 초과</strong>: 총 필요 ' + data.needed_total_td + '거래일이나 보유 데이터가 ' + data.available_total_td + '거래일입니다.<br>' +
                '<span style="font-size:11px;color:var(--text-muted);">WFA 횟수(창수)를 줄이거나 Train 거래일수를 낮추어 보유 데이터 범위에 맞추어 주세요. (현재는 전체 데이터 시작일에 클램프되었습니다)</span></div>';
        } else if (data.windows && data.windows.length > 0) {
            statusHtml = '<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);border-radius:6px;padding:8px 12px;font-size:12px;">' +
                '✅ <strong>구간 분리 정상</strong> — WFA ' + data.windows.length + '개 창과 홀드아웃(' + data.holdout_start + ' ~)이 데이터 스캔 기반으로 완벽 분할되었습니다.</div>';
        }

        warnEl.innerHTML = statusHtml;
    }

    function renderTimelineSummary(data) {
        const wfaBadge = document.getElementById('asrl_wfa_badge');
        if (wfaBadge) {
            const winCnt = data.windows?.length || 0;
            if (winCnt > 0) {
                const wStart = data.wfa_start || data.windows[0].train_start;
                const wEnd = data.wfa_end || data.windows[winCnt - 1].test_end;
                wfaBadge.innerHTML = `총 <strong>${winCnt}개</strong> 윈도우 (${wStart} ~ ${wEnd})`;
            } else {
                wfaBadge.innerHTML = `생성 윈도우: <strong>0개</strong> (기간 확인 필요)`;
            }
        }

        const holdoutBadge = document.getElementById('asrl_holdout_badge');
        const holdoutSub = document.getElementById('asrl_holdout_sub_info');
        if (holdoutBadge && data.holdout_start && data.holdout_end) {
            const sD = new Date(data.holdout_start);
            const eD = new Date(data.holdout_end);
            const diffDays = Math.max(1, Math.round((eD - sD) / 86400000) + 1);
            const hTd = data.holdout_td || diffDays;
            holdoutBadge.innerHTML = `기간: <strong>${hTd}거래일</strong> (달력 ${diffDays}일)`;
            if (holdoutSub) {
                const snapInfo = (data.snapshot_start && data.snapshot_end) ? ` | 스냅샷: ${data.snapshot_start}~${data.snapshot_end}` : '';
                holdoutSub.innerHTML = `🔒 <strong>최근 ${hTd}거래일</strong> 실전 미공개 데이터로 최종 과적합 차단${snapInfo}`;
            }
        }
    }

    function resizeAsrlTimeline() {
        const container = document.getElementById('asrlTimelineContainer');
        if (container && typeof Plotly !== 'undefined' && container.data) {
            Plotly.Plots.resize(container);
        } else if (typeof refreshAsrlTimeline === 'function') {
            refreshAsrlTimeline();
        }
    }

    window.initAsrlTimeline = initAsrlTimeline;
    window.refreshAsrlTimeline = refreshAsrlTimeline;
    window.resizeAsrlTimeline = resizeAsrlTimeline;
    window.onWfaParamsChange = scheduleAsrlRefresh;
})();
