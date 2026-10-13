import re
from typing import Optional, Dict, Any, List
import asyncio
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from multiprocessing import Process
import uvicorn
from utils.retry import atomic_write_json
from ui.auth_manager import auth_mgr

app = FastAPI()
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "trading_config.json")

# 공유 상태를 위한 전역 변수 (프로세스 시작 시 설정됨)
shared_state_ref = None

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        if not self.active_connections:
            return
        
        # JSON 직렬화 및 브로드캐스트 (비표준 NaN/Infinity를 표준 null로 치환하여 브라우저 JSON.parse 크래시 원천 차단)
        message = json.dumps(data)
        if "NaN" in message or "Infinity" in message:
            message = re.sub(r'\b(NaN|-?Infinity)\b', 'null', message)

        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

# ── [2FA OTP 보안 인증 엔드포인트] ──────────────────────────────────────────

@app.get("/api/auth/status")
async def get_auth_status(request: Request):
    """현재 클라이언트의 세션 인증 상태 및 IP 차단 여부 반환"""
    client_ip = auth_mgr._get_client_ip(request)
    is_blocked, rem_sec = auth_mgr.is_ip_blocked(client_ip)
    
    session_token = request.cookies.get("ctrade_session")
    is_auth = auth_mgr.verify_session(session_token)

    return {
        "authenticated": is_auth,
        "is_blocked": is_blocked,
        "blocked_remaining_sec": rem_sec,
        "client_ip": client_ip,
        "is_local": auth_mgr.is_local_ip(client_ip)
    }

@app.post("/api/auth/request-otp")
async def request_otp(request: Request):
    """텔레그램으로 6자리 일회용 인증번호 발송 요청"""
    client_ip = auth_mgr._get_client_ip(request)
    ok, msg, cooldown = auth_mgr.request_otp(client_ip)
    if not ok:
        return JSONResponse(status_code=400, content={"status": "error", "message": msg, "cooldown": cooldown})
    return {"status": "success", "message": msg, "cooldown": cooldown}

@app.post("/api/auth/verify-otp")
async def verify_otp(request: Request, response: Response, payload: dict = None):
    """6자리 OTP 검증 및 24시간 세션 쿠키 발급 (로컬 PC 무인증 로그인 지원)"""
    client_ip = auth_mgr._get_client_ip(request)
    code = (payload or {}).get("otp_code", "") if payload else ""
    ok, msg, session_token = auth_mgr.verify_otp(client_ip, code)
    
    if not ok:
        return JSONResponse(status_code=400, content={"status": "error", "message": msg})
    
    # 24시간 유효 세션 쿠키 설정 (HttpOnly, SameSite=Lax)
    resp = JSONResponse(content={"status": "success", "message": msg})
    resp.set_cookie(
        key="ctrade_session",
        value=session_token,
        max_age=86400, # 24시간
        httponly=False, # JS 확인용
        samesite="lax"
    )
    return resp

@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """로그아웃 및 세션 쿠키 파기"""
    session_token = request.cookies.get("ctrade_session")
    if session_token:
        auth_mgr.revoke_session(session_token)
    resp = JSONResponse(content={"status": "success", "message": "로그아웃되었습니다."})
    resp.delete_cookie("ctrade_session")
    return resp

# ── [페이지 렌더링] ──────────────────────────────────────────────────────────

@app.get("/")
async def get(request: Request):
    client_ip = auth_mgr._get_client_ip(request)
    is_blocked, rem_sec = auth_mgr.is_ip_blocked(client_ip)
    if is_blocked:
        return HTMLResponse(
            content=f"<h1>403 Forbidden</h1><p>비정상적인 접근 시도로 인해 IP({client_ip})가 차단되었습니다. {rem_sec}초 후 해제됩니다.</p>",
            status_code=403
        )
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard Template Not Found</h1>")

@app.get("/analytics")
async def get_analytics(request: Request):
    client_ip = auth_mgr._get_client_ip(request)
    is_blocked, rem_sec = auth_mgr.is_ip_blocked(client_ip)
    if is_blocked:
        return HTMLResponse(
            content=f"<h1>403 Forbidden</h1><p>비정상적인 접근 시도로 인해 IP({client_ip})가 차단되었습니다. {rem_sec}초 후 해제됩니다.</p>",
            status_code=403
        )
    template_path = os.path.join(os.path.dirname(__file__), "templates", "analytics.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Analytics Template Not Found</h1>")

@app.get("/api/history")
async def get_history():
    history_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_history.json")
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return []
    return []

@app.get("/api/version")
async def get_version():
    v_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "version.json")
    if not os.path.exists(v_path):
        v_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": "v1.3.0", "commit": "dev", "deployed_at": "Local"}

@app.get("/api/candles")
async def get_candles(symbol: str):
    """현재 포커스된 종목의 캔들 데이터를 반환합니다. 데이터가 부족할 경우 업비트 API를 통해 100개 과거 데이터를 백필합니다."""
    if not shared_state_ref:
        return []
        
    focused_candles = shared_state_ref.data.get('__focused_candles__', [])
    
    # 캔들 데이터가 없거나 극소수인 경우 업비트 REST API를 활용해 15분봉 기준 백필
    if len(focused_candles) < 5 and "KRW-" in symbol:
        try:
            import requests
            url = "https://api.upbit.com/v1/candles/minutes/15"
            params = {"market": symbol, "count": 100}
            headers = {"accept": "application/json"}
            res = requests.get(url, params=params, headers=headers, timeout=3.0)
            if res.status_code == 200:
                upbit_candles = res.json()
                backfilled = []
                from datetime import datetime
                for c in reversed(upbit_candles):
                    # candle_date_time_utc 예: "2026-07-15T00:00:00" -> ISO 포맷 파싱
                    dt_str = c["candle_date_time_utc"] + "+00:00"
                    dt = datetime.fromisoformat(dt_str)
                    backfilled.append({
                        "time": int(dt.timestamp()),
                        "upbit_open": c["opening_price"],
                        "upbit_close": c["trade_price"],
                        "upbit_high": c["high_price"],
                        "upbit_low": c["low_price"],
                        "upbit_volume": c["candle_acc_trade_volume"]
                    })
                return backfilled
        except Exception as e:
            logging.error(f"[API] Upbit candle backfill failed: {e}")
            
    return focused_candles

@app.post("/api/clear/log")
async def clear_log():
    """trade_debug.log 파일 초기화"""
    try:
        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_debug.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
        return {"status": "success", "file": "trade_debug.log"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/clear/history")
async def clear_history():
    """trade_history.json 파일 초기화 및 엔진 상태 리셋"""
    try:
        history_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_history.json")
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        if shared_state_ref:
            shared_state_ref.cmd_queue.put({"type": "reset_simulation"})
        return {"status": "success", "file": "trade_history.json"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/simulator/reset")
async def reset_simulator():
    """시뮬레이션 매매 기록(trade_history.json), 가상 포지션, 잔고, 통계를 완전 리셋합니다."""
    try:
        history_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_history.json")
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        
        # 프로젝트 루트 trade_history.json도 동기화
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        root_hist_path = os.path.join(root_dir, "trade_history.json")
        if os.path.exists(root_hist_path) or root_hist_path != history_path:
            try:
                with open(root_hist_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
            except Exception:
                pass

        if shared_state_ref:
            shared_state_ref.cmd_queue.put({"type": "reset_simulation"})
            
        return {"status": "success", "message": "Simulation reset successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/portfolio/reset_baseline")
async def reset_portfolio_baseline(broker: str = "ALL", note: str = "수동 재설정"):
    """실전 계좌 기준선(시작금액)을 현재 총자산으로 재설정합니다 (broker: ALL, UPBIT, KIS)."""
    try:
        if shared_state_ref:
            shared_state_ref.cmd_queue.put({"type": "reset_baseline", "broker": broker.upper(), "note": note})
        return {"status": "success", "broker": broker.upper(), "message": f"Baseline reset triggered for {broker.upper()}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/portfolio/baseline/history")
async def get_portfolio_baseline_history():
    """기준선 변경 이력 목록을 조회합니다."""
    try:
        from utils.portfolio_tracker import PortfolioTracker
        history = PortfolioTracker.get_baseline_history()
        return {"status": "success", "history": history}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/portfolio/baseline/restore")
async def restore_portfolio_baseline(history_id: str):
    """지정된 과거 이력 스냅샷으로 기준선을 복구합니다."""
    try:
        if shared_state_ref:
            shared_state_ref.cmd_queue.put({"type": "restore_baseline", "history_id": history_id})
        return {"status": "success", "message": f"Baseline restore triggered for {history_id}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/portfolio/baseline/delete")
async def delete_portfolio_baseline_history(history_id: str):
    """지정된 기준선 이력 항목을 삭제합니다."""
    try:
        from utils.portfolio_tracker import PortfolioTracker
        ok = PortfolioTracker.delete_baseline_history(history_id)
        if ok:
            return {"status": "success", "message": f"History {history_id} deleted"}
        return {"status": "error", "message": "History item not found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/config")
async def get_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "Config file not found"}

@app.get("/api/symbols/registry")
async def get_symbols_registry():
    """중앙 종목 원장 (한글 종목명 및 메타데이터) 반환.
    화면의 이름/시장 분류(호가 단위 판정) 조회용이라 등록 종목뿐 아니라 검색 후보(catalog)도 포함한다."""
    from shared.config.config_manager import load_symbols_registry
    return load_symbols_registry(include_catalog=True)

@app.post("/config")
async def save_config(new_config: dict):
    try:
        # Atomic Write: 임시 파일 쓰기 후 os.replace()로 교체하여 Race Condition 방지
        atomic_write_json(CONFIG_PATH, new_config)
        
        # 1. 즉시 적용 가능한 설정은 리로드 (Targets 등)
        if shared_state_ref:
            shared_state_ref.cmd_queue.put({"type": "reload_config"})
            
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/engine/restart")
async def restart_engine():
    """엔진 프로세스를 재시작합니다 (전략 코드 완전 갱신용)"""
    if not shared_state_ref:
        return {"status": "error", "message": "Shared state not available"}
    
    # 현재 포지션 상태 확인
    sim_data = shared_state_ref.data.get('__sim__', {})
    active_count = sim_data.get('active_positions', 0)
    
    # 재시작 플래그 설정 (main.py 감지용)
    shared_state_ref.update_control({"restart_engine": True})
    # 엔진 종료 명령 전달 (main.py가 재시작시킴)
    shared_state_ref.cmd_queue.put({"type": "restart"})
    
    return {
        "status": "success", 
        "active_positions": active_count,
        "message": "Engine restart signal sent"
    }

@app.post("/api/engine/trade-control")
async def control_trading(payload: dict = None, active: Optional[bool] = None):
    """매매 활성화(시작) / 비활성화(일시정지) 제어"""
    if not shared_state_ref:
        return {"status": "error", "message": "Shared state not available"}
    
    target_active = active
    if target_active is None and payload:
        target_active = payload.get("active")
    if target_active is None:
        # 토글
        current = shared_state_ref.control.get("trading_active", False)
        target_active = not current
    
    target_active = bool(target_active)
    shared_state_ref.update_control({"trading_active": target_active})
    
    # config 파일에 영속화
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if "GLOBAL_STRATEGY" in cfg:
            cfg["GLOBAL_STRATEGY"]["TRADING_ACTIVE"] = target_active
            atomic_write_json(CONFIG_PATH, cfg)
    except Exception as e:
        logging.error(f"[API] Failed to persist TRADING_ACTIVE: {e}")
        
    return {
        "status": "success",
        "trading_active": target_active,
        "message": f"Trading is now {'ACTIVE (매매 시작)' if target_active else 'PAUSED (매매 일시정지)'}"
    }

@app.get("/api/engine/symbol-auto-trade")
async def get_symbol_auto_trade():
    """등록된 종목들의 자동매매(auto_trade) 활성화 현황 반환"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        targets = cfg.get("SCALPING_TARGETS", [])
        result = []
        for t in targets:
            result.append({
                "symbol": t.get("symbol"),
                "exchange": t.get("exchange", "UPBIT"),
                "active": t.get("active", True),
                "auto_trade": t.get("auto_trade", True),
                "strategy_name": t.get("strategy_name", "default_scalping")
            })
        return {"status": "success", "targets": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/engine/symbol-auto-trade")
async def set_symbol_auto_trade(payload: dict = None, symbol: Optional[str] = None, auto_trade: Optional[bool] = None):
    """특정 종목의 자동매매(auto_trade) 활성화/비활성화 제어"""
    if not shared_state_ref:
        return {"status": "error", "message": "Shared state not available"}
    
    target_symbol = symbol
    target_auto = auto_trade
    if payload:
        if not target_symbol:
            target_symbol = payload.get("symbol")
        if target_auto is None and "auto_trade" in payload:
            target_auto = payload.get("auto_trade")
            
    if not target_symbol:
        return {"status": "error", "message": "symbol parameter is required"}
        
    target_symbol = target_symbol.strip().upper()
    
    cmd = {
        "type": "set_symbol_auto_trade" if target_auto is not None else "toggle_symbol_auto_trade",
        "symbol": target_symbol
    }
    if target_auto is not None:
        cmd["auto_trade"] = bool(target_auto)
        
    shared_state_ref.cmd_queue.put(cmd)
    
    # config 파일 직접 동기화 (즉각 반영)
    persisted_val = target_auto
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        found = False
        for t in cfg.get("SCALPING_TARGETS", []):
            if t.get("symbol") == target_symbol:
                if target_auto is not None:
                    t["auto_trade"] = bool(target_auto)
                else:
                    t["auto_trade"] = not t.get("auto_trade", True)
                persisted_val = t["auto_trade"]
                found = True
                break
        if not found:
            new_target = {
                "symbol": target_symbol,
                "exchange": "UPBIT",
                "active": True,
                "auto_trade": bool(target_auto) if target_auto is not None else False
            }
            cfg.setdefault("SCALPING_TARGETS", []).append(new_target)
            persisted_val = new_target["auto_trade"]
            
        atomic_write_json(CONFIG_PATH, cfg)
    except Exception as e:
        logging.error(f"[API] Failed to persist symbol auto_trade for {target_symbol}: {e}")
        
    return {
        "status": "success",
        "symbol": target_symbol,
        "auto_trade": persisted_val,
        "message": f"{target_symbol} auto_trade is now {'ON (자동매매 활성)' if persisted_val else 'OFF (자동매매 비활성)'}"
    }

@app.post("/api/engine/close")
async def close_position(payload: dict = None, symbol: Optional[str] = None):
    """특정 종목 즉시 시장가 수동 청산"""
    if not shared_state_ref:
        return {"status": "error", "message": "Shared state not available"}
    
    target_symbol = symbol
    if not target_symbol and payload:
        target_symbol = payload.get("symbol")
    
    if not target_symbol:
        return {"status": "error", "message": "symbol parameter is required"}
        
    target_symbol = target_symbol.strip().upper()
    shared_state_ref.cmd_queue.put({"type": "close_symbol", "symbol": target_symbol})
    return {
        "status": "success",
        "symbol": target_symbol,
        "message": f"Close order queued for {target_symbol}"
    }

@app.post("/api/engine/close-all")
async def close_all_positions():
    """보유 중인 모든 종목 일괄 긴급 청산"""
    if not shared_state_ref:
        return {"status": "error", "message": "Shared state not available"}
        
    shared_state_ref.cmd_queue.put({"type": "close_all"})
    return {
        "status": "success",
        "message": "Emergency close all positions queued"
    }

@app.get("/api/engine/status")
async def get_engine_status():
    """엔진의 현재 상태(포지션 등)를 반환합니다."""
    if not shared_state_ref:
        return {"status": "error", "message": "Shared state not available"}
    
    sim_data = shared_state_ref.data.get('__sim__', {})
    return {
        "active_positions": sim_data.get('active_positions', 0),
        "is_running": shared_state_ref.control.get("is_running", False),
        "trading_active": shared_state_ref.control.get("trading_active", False)
    }

@app.get("/api/radar/verification")
async def get_radar_verification():
    """스캘핑 타겟 레이더의 급등 사후 검증 통계 및 역사적 내역을 반환합니다."""
    import os
    history_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spike_verification_history.json")
    
    history = []
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception as e:
            return {"status": "error", "message": f"Failed to load history: {e}"}
            
    # 누적 통계 계산
    total_triggers = len(history)
    wins = sum(1 for item in history if item.get("success_30s", False))
    win_rate = (wins / total_triggers * 100) if total_triggers > 0 else 0.0
    
    avg_ret_5s = sum(item.get("return_5s", 0.0) for item in history) / total_triggers if total_triggers > 0 else 0.0
    avg_ret_30s = sum(item.get("return_30s", 0.0) for item in history) / total_triggers if total_triggers > 0 else 0.0
    avg_ret_60s = sum(item.get("return_60s", 0.0) for item in history) / total_triggers if total_triggers > 0 else 0.0
    
    return {
        "status": "success",
        "stats": {
            "total_triggers": total_triggers,
            "wins": wins,
            "win_rate": win_rate,
            "avg_return_5s": avg_ret_5s,
            "avg_return_30s": avg_ret_30s,
            "avg_return_60s": avg_ret_60s
        },
        "history": list(reversed(history)) # 최신이 가장 먼저 오도록 역순 반환
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_ip = auth_mgr._get_client_ip(websocket)
    is_blocked, _ = auth_mgr.is_ip_blocked(client_ip)
    if is_blocked:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket)
    try:
        while True:
            # 클라이언트로부터 메시지 수신
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if shared_state_ref:
                    # 모든 UI 명령을 엔진의 큐로 전달
                    shared_state_ref.cmd_queue.put(msg)
                    logging.info(f"[API] Command queued: {msg.get('type')}")
            except json.JSONDecodeError:
                pass # 하트비트 등 비JSON 메시지 무시
    except WebSocketDisconnect:
        manager.disconnect(websocket)

async def state_broadcaster():
    """SharedState를 주기적으로 읽어 WebSocket으로 전송합니다."""
    while True:
        if shared_state_ref:
            try:
                # [Optimization] dict(shared_state_ref.data) 전체 복사로 유발되던 장시간 소켓 락 점유 차단
                # 웹소켓 갱신에 꼭 필요한 핵심 키들만 개별 get으로 초고속 획득
                all_symbols_data = shared_state_ref.data.get('__symbols__', {})
                control_snapshot = dict(shared_state_ref.control)
                
                serializable_data = {}
                
                model_health_data = shared_state_ref.data.get('__model_health__', {}) or {}

                # 심볼 데이터 병합
                for symbol, info in all_symbols_data.items():
                    if isinstance(info, dict):
                        info_copy = info.copy()
                        if symbol in model_health_data:
                            info_copy['model_health'] = model_health_data[symbol]
                        if 'orderbook' in info_copy and info_copy['orderbook'] is not None:
                            ob = info_copy['orderbook']
                            # ob가 실제 Orderbook 인스턴스이고 units 속성이 있을 때만 직렬화 보정
                            if hasattr(ob, 'units'):
                                info_copy['orderbook'] = {
                                    'units': [{'ask_p': u.ask_price, 'ask_s': u.ask_size, 
                                               'bid_p': u.bid_price, 'bid_s': u.bid_size} 
                                              for u in ob.units]
                                }
                            else:
                                info_copy['orderbook'] = {}
                        serializable_data[symbol] = info_copy
                
                # 기타 글로벌/시뮬레이터/시스템 지표 개별 get 취득 (락 경합 차단)
                serializable_data['__global__'] = shared_state_ref.data.get('__global__', {})
                serializable_data['__sim__'] = shared_state_ref.data.get('__sim__', {})
                serializable_data['__system__'] = shared_state_ref.data.get('__system__', {})
                serializable_data['__logs__'] = shared_state_ref.data.get('__logs__', [])
                serializable_data['__radar_scanner__'] = shared_state_ref.data.get('__radar_scanner__', [])
                serializable_data['__focused_candles__'] = shared_state_ref.data.get('__focused_candles__', [])
                serializable_data['__portfolio__'] = shared_state_ref.data.get('__portfolio__', {})
                serializable_data['__model_health__'] = model_health_data

                await manager.broadcast({
                    "type": "update",
                    "data": serializable_data,
                    "control": control_snapshot
                })
            except Exception as e:
                if isinstance(e, OSError) and e.errno == 32:
                    logging.debug("[API] Broadcaster: Client disconnected (Broken pipe)")
                else:
                    logging.error(f"[API] Broadcaster Error: {e}")
        
        await asyncio.sleep(0.2) # 5Hz (성능과 가독성 사이의 타협점)


@app.get("/api/model/health")
async def get_model_health():
    """모든 활성 AI 모델의 건강 상태 및 재학습 진단 정보를 반환합니다."""
    if shared_state_ref:
        return shared_state_ref.data.get('__model_health__', {}) or {}
    try:
        from core.model_health_monitor import ModelHealthMonitor
        return ModelHealthMonitor.get_instance().get_all_reports(notify=False)
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/baker/models")
async def get_baker_models():
    """shared/deploy 디렉토리를 스캔하여 대분류 폴더와 세션 폴더들을 트리 구조로 반환합니다."""
    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        deploy_dir = os.path.join(root_dir, "shared", "deploy")
        
        models_tree = {}
        if os.path.exists(deploy_dir):
            for base in os.listdir(deploy_dir):
                base_path = os.path.join(deploy_dir, base)
                if os.path.isdir(base_path) and not base.startswith("."):
                    sessions = []
                    for session in os.listdir(base_path):
                        session_path = os.path.join(base_path, session)
                        if os.path.isdir(session_path) and not session.startswith("."):
                            sessions.append(session)
                    models_tree[base] = sorted(sessions)
        return {"status": "success", "models": models_tree}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/baker/model/spec")
async def get_baker_model_spec(base: str, session: str):
    """지정된 모델 세션의 스펙 정보(feature_spec, best_params, model 가중치 해시)를 파싱하여 요약 반환합니다."""
    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        session_dir = os.path.join(root_dir, "shared", "deploy", base, session)
        
        spec_info = {}
        if os.path.exists(session_dir):
            model_dir = os.path.join(session_dir, "model")
            
            # 1. feature_spec.json 로드
            spec_path = os.path.join(model_dir, "feature_spec.json")
            spec = {}
            if os.path.exists(spec_path):
                try:
                    with open(spec_path, "r", encoding="utf-8") as f:
                        spec = json.load(f)
                        spec_info["features_count"] = len(spec.get("features_order", []))
                        spec_info["features_list"] = spec.get("features_order", [])
                        spec_info["interval"] = spec.get("interval") or spec.get("feature_params", {}).get("interval", "N/A")
                except Exception as e:
                    logging.warning(f"Failed to read feature_spec.json: {e}")
            
            # 2. best_params.json 로드
            params_path = os.path.join(model_dir, f"best_params_{base}.json")
            if not os.path.exists(params_path) and os.path.exists(model_dir):
                for file in os.listdir(model_dir):
                    if file.startswith("best_params_") and file.endswith(".json"):
                        params_path = os.path.join(model_dir, file)
                        break
            
            params = {}
            if os.path.exists(params_path):
                try:
                    with open(params_path, "r", encoding="utf-8") as f:
                        params = json.load(f)
                        spec_info["tp"] = params.get("take_profit") or params.get("TP", 0.0)
                        spec_info["sl"] = params.get("stop_loss") or params.get("SL", 0.0)
                        spec_info["prob_threshold"] = params.get("buy_threshold") or params.get("threshold", 0.500)
                        spec_info["train_start"] = params.get("train_start")
                        spec_info["train_end"] = params.get("train_end")
                        spec_info["opt_session_id"] = params.get("opt_session_id") or session
                        spec_info["memo"] = params.get("memo", "")
                        spec_info["validation_metrics"] = params.get("validation_metrics", {})
                        spec_info["optuna_score"] = params.get("optuna_score")
                except Exception as e:
                    logging.warning(f"Failed to read best_params.json: {e}")

            # 3. model_*.txt 가중치 파일 SHA-256 지문(Fingerprint) 계산
            model_fingerprint = None
            if os.path.exists(model_dir):
                model_files = [f for f in os.listdir(model_dir) if f.startswith("model_") and f.endswith(".txt")]
                if model_files:
                    target_model_file = os.path.join(model_dir, model_files[0])
                    try:
                        sha256 = hashlib.sha256()
                        with open(target_model_file, "rb") as mf:
                            for chunk in iter(lambda: mf.read(65536), b""):
                                sha256.update(chunk)
                        model_fingerprint = sha256.hexdigest()[:8]
                    except Exception as e:
                        logging.warning(f"Failed to compute model SHA-256: {e}")
            spec_info["model_fingerprint"] = model_fingerprint or "N/A"

            # 4. 학습/생성 일시 (created_at / trained_at) 추출 (best_params > spec > snapshot > mtime)
            created_at_str = params.get("created_at") or spec.get("created_at")
            
            # 5. 실전 배포 일시 (deployed_at) 추출 (config_snapshot > mtime)
            deployed_at_str = None
            snap_path = os.path.join(session_dir, "config_snapshot.json")
            if os.path.exists(snap_path):
                try:
                    with open(snap_path, "r", encoding="utf-8") as f:
                        snap = json.load(f)
                        deployed_at_str = snap.get("DEPLOYED_AT") or snap.get("deployed_at")
                        if not created_at_str:
                            created_at_str = snap.get("CREATED_AT") or snap.get("created_at")
                except Exception:
                    pass

            if not created_at_str and os.path.exists(spec_path):
                try:
                    mtime = os.path.getmtime(spec_path)
                    created_at_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            if not deployed_at_str and os.path.exists(session_dir):
                try:
                    mtime = os.path.getmtime(session_dir)
                    deployed_at_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            spec_info["created_at"] = created_at_str or "N/A"
            spec_info["deployed_at"] = deployed_at_str or created_at_str or "N/A"

            return {"status": "success", "spec": spec_info}
        return {"status": "error", "message": "Session directory not found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/baker/model/delete")
async def delete_baker_model(payload: dict):
    """
    지정된 모델 세션 폴더(shared/deploy/<base>/<session>)를 영구 삭제합니다.
    [안전 가드]:
    1. 경로 탐색(Directory Traversal) 방어 ('..' 또는 특수문자 검증)
    2. 현재 trading_config.json 내 활성 종목(SCALPING_TARGETS) 및 템플릿(STRATEGY_TEMPLATES)에서 사용 중인 경우 삭제 차단
    """
    try:
        base = (payload.get("base") or "").strip().replace("\\", "/").split("/")[-1]
        session = (payload.get("session") or "").strip().replace("\\", "/").split("/")[-1]
        
        if not base or not session:
            return JSONResponse(status_code=400, content={"status": "error", "message": "유효하지 않은 모델 베이스 또는 세션명입니다."})
        
        # 특수문자/디렉토리 탈출 방어
        if ".." in base or ".." in session or "/" in base or "/" in session:
            return JSONResponse(status_code=400, content={"status": "error", "message": "허용되지 않는 경로 문자열입니다."})
            
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        session_dir = os.path.join(root_dir, "shared", "deploy", base, session)
        
        if not os.path.exists(session_dir) or not os.path.isdir(session_dir):
            return JSONResponse(status_code=404, content={"status": "error", "message": f"삭제할 모델 세션 폴더를 찾을 수 없습니다: {session}"})
            
        # 1. trading_config.json 검증: 현재 사용 중인 세션인지 확인
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = json.load(f)
                
                # 템플릿 검사
                templates = config.get("STRATEGY_TEMPLATES", {})
                active_template_names = set()
                if isinstance(templates, dict):
                    for tmpl_name, tmpl_val in templates.items():
                        if isinstance(tmpl_val, dict) and tmpl_val.get("deploy_session") == session:
                            active_template_names.add(tmpl_name)
                            return JSONResponse(status_code=400, content={
                                "status": "error", 
                                "message": f"현재 전략 템플릿 [{tmpl_name}]에서 사용 중인 모델 세션이므로 삭제할 수 없습니다. 먼저 템플릿의 모델을 변경해 주세요."
                            })
                
                # 종목별 설정 검사 (리스트 또는 딕셔너리 지원)
                targets = config.get("SCALPING_TARGETS", [])
                if isinstance(targets, list):
                    for t_conf in targets:
                        if isinstance(t_conf, dict):
                            sym = t_conf.get("symbol", "UNKNOWN")
                            if t_conf.get("deploy_session") == session:
                                return JSONResponse(status_code=400, content={
                                    "status": "error",
                                    "message": f"현재 종목 [{sym}]에서 직접 사용 중인 모델 세션이므로 삭제할 수 없습니다. 먼저 종목의 모델을 변경해 주세요."
                                })
                            # 템플릿을 통해 참조하고 있는지도 검사
                            strat_name = t_conf.get("strategy_name", "")
                            if strat_name in active_template_names and t_conf.get("active", False):
                                return JSONResponse(status_code=400, content={
                                    "status": "error",
                                    "message": f"현재 활성 종목 [{sym}]이 전략 템플릿 [{strat_name}]을 통해 해당 모델 세션을 운용 중입니다. 먼저 모델을 변경해 주세요."
                                })
                elif isinstance(targets, dict):
                    for sym, t_conf in targets.items():
                        if isinstance(t_conf, dict):
                            if t_conf.get("deploy_session") == session:
                                return JSONResponse(status_code=400, content={
                                    "status": "error",
                                    "message": f"현재 종목 [{sym}]에서 직접 사용 중인 모델 세션이므로 삭제할 수 없습니다. 먼저 종목의 모델을 변경해 주세요."
                                })
            except Exception as e:
                logging.warning(f"Error checking config before model deletion: {e}")

        # 2. 물리적 폴더 삭제
        shutil.rmtree(session_dir)
        logging.info(f"[BakerModel] Successfully deleted model session: {base}/{session}")
        
        # 3. 만약 상위 base 디렉토리에 더 이상 하위 세션이 없다면 base 폴더 정리
        base_dir = os.path.join(root_dir, "shared", "deploy", base)
        if os.path.exists(base_dir):
            remaining = [d for d in os.listdir(base_dir) if not d.startswith(".")]
            if len(remaining) == 0:
                try:
                    os.rmdir(base_dir)
                except Exception:
                    pass

        return {
            "status": "success", 
            "message": f"모델 세션 [{session}]이(가) 안전하게 영구 삭제되었습니다.",
            "base": base,
            "session": session
        }
    except Exception as e:
        logging.error(f"[BakerModel] Failed to delete model session: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": f"모델 세션 삭제 실패: {str(e)}"})

# -------------------------------------------------------------
# 실시간 데이터 무결성 및 AI 추론 피드 진단 API (Diagnostics)
# -------------------------------------------------------------
from utils.diagnostics.data_analyzer import DataAnalyzer

@app.post("/api/diagnostics/capture/start")
async def start_capture(payload: dict):
    symbol = payload.get("symbol", "ALL")
    duration = float(payload.get("duration_sec", 60.0))
    if shared_state_ref:
        shared_state_ref.cmd_queue.put({
            "type": "start_diagnostics",
            "symbol": symbol,
            "duration_sec": duration
        })
        return {"status": "started", "symbol": symbol, "duration_sec": duration}
    return {"status": "error", "message": "SharedState is not available"}

@app.post("/api/diagnostics/capture/stop")
async def stop_capture():
    if shared_state_ref:
        shared_state_ref.cmd_queue.put({"type": "stop_diagnostics"})
        await asyncio.sleep(0.5)
        # 최신 덤프 파일 분석
        base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "captures")
        if os.path.exists(base_dir):
            files = [os.path.join(base_dir, f) for f in os.listdir(base_dir) if f.endswith(".jsonl")]
            if files:
                files.sort(key=os.path.getmtime, reverse=True)
                last_path = files[0]
                report = DataAnalyzer.analyze_file(last_path)
                return {"status": "completed", "dump_path": last_path, "report": report}
    return {"status": "completed"}

@app.get("/api/diagnostics/capture/status")
async def get_capture_status():
    if shared_state_ref and '__diag_status__' in shared_state_ref.data:
        return shared_state_ref.data['__diag_status__']
    return {"is_capturing": False}

@app.get("/api/diagnostics/report/latest")
async def get_latest_diagnostic_report():
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "captures")
    if os.path.exists(base_dir):
        files = [os.path.join(base_dir, f) for f in os.listdir(base_dir) if f.endswith(".jsonl")]
        if files:
            files.sort(key=os.path.getmtime, reverse=True)
            last_path = files[0]
            report = DataAnalyzer.analyze_file(last_path)
            return {"status": "success", "report": report, "file": os.path.basename(last_path)}

    return {"status": "error", "message": "캡처된 진단 데이터 파일이 없습니다."}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(state_broadcaster())

def run_api_server(shared_state):
    global shared_state_ref
    shared_state_ref = shared_state
    
    # 멀티프로세싱 환경에서 로깅 재설정 (파일로 기록)
    logging.basicConfig(
        filename='trade_debug.log',
        filemode='a',
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] [v1.3.0] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("--- API Server Process Start ---")
    
    # 서버 실행 (uvicorn)
    # HFT 앱이므로 로깅을 최소화하여 오버헤드 감소
    uvicorn.run(app, host="0.0.0.0", port=56789, log_level="warning", access_log=False)

if __name__ == "__main__":
    # 독립 실행 테스트용
    from core.shared_state import SharedState
    s = SharedState()
    run_api_server(s)
