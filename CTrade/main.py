import os, asyncio, logging, json, argparse, sys
from logging.handlers import RotatingFileHandler
from multiprocessing import Process, Manager
from dotenv import load_dotenv
from core.shared_state import SharedState
from core.engine import STradeEngine
from ui.api_server import run_api_server
from utils.telegram_bot import TelegramBot

VERSION = "v1.3.0"

def _setup_logging():
    """자식 프로세스에서 호출. 기존 핸들러를 모두 제거하고 RotatingFileHandler를 새로 등록."""
    root = logging.getLogger()
    # fork 시 상속된 핸들러 전체 제거 (이중 기록 방지)
    root.handlers.clear()
    root.setLevel(logging.INFO)
    _h = RotatingFileHandler(
        'trade_debug.log',
        mode='a',
        maxBytes=20 * 1024 * 1024,  # 20MB 차단
        backupCount=3,
        encoding='utf-8'
    )
    _h.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] [v1.3.0] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    root.addHandler(_h)

def run_engine_process(config, shared_state):
    _setup_logging()
    logging.info("--- Engine Process Start ---")

    # 엔진 자식 프로세스에서도 봇 초기화 (알림 전용 — 폴링 없음)
    # 메인 프로세스의 _bot_instance는 멀티프로세싱 경계를 넘지 못하므로
    # 이 프로세스에서 별도 인스턴스를 생성하여 buy/sell 알림 전송에만 사용
    try:
        from utils.telegram_bot import TelegramBot
        _engine_bot = TelegramBot(shared_state=None)
        # start() 미호출 → 폴링 스레드 없음 (알림 전송만 가능)
    except Exception as e:
        logging.warning(f"[Engine] TelegramBot 초기화 실패 (계속 진행): {e}")

    engine = STradeEngine(config)
    try:
        asyncio.run(engine.run(shared_state))
    except Exception as e:
        logging.error(f"Engine Process Crash: {e}", exc_info=True)


def load_trading_config():
    """설정 파일을 디스크에서 다시 읽어옵니다."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'trading_config.json')
        with open(config_path, 'r') as f:
            cfg = json.load(f)
        
        # 환경 변수 바인딩
        def get_env(key, default=""):
            val = os.getenv(key)
            if val is not None:
                return val.strip("'").strip('"').strip()
            return default

        cfg.update({
            'KIS_APP_KEY': get_env('KIS_APP_KEY'),
            'KIS_APP_SECRET': get_env('KIS_APP_SECRET'),
            'KIS_ACC_NO': get_env('KIS_ACC_NO'),
            'KIS_ACC_CODE': get_env('KIS_ACC_CODE', '01'),
            'KIS_URL_BASE': get_env('KIS_URL_BASE') or get_env('URL_BASE'),
            'UPBIT_ACCESS_KEY': get_env('UPBIT_ACCESS_KEY'),
            'UPBIT_SECRET_KEY': get_env('UPBIT_SECRET_KEY')
        })
        return cfg
    except Exception as e:
        logging.error(f"Failed to load trading_config.json: {e}")
        return {}

def _cleanup_process(proc, name="Process", timeout=1.5):
    """프로세스를 안전하게 종료하고, 응답하지 않을 경우 강제 종료(SIGKILL)"""
    if proc and proc.is_alive():
        proc.terminate()
        proc.join(timeout=timeout)
        if proc.is_alive():
            logging.warning(f"⚠️ [Supervisor] {name} (PID: {proc.pid}) did not terminate gracefully. Sending SIGKILL...")
            try:
                proc.kill()
                proc.join(timeout=1.0)
            except Exception as e:
                logging.error(f"[Supervisor] Failed to kill {name}: {e}")


if __name__ == "__main__":
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="CTrade HFT Engine")
    parser.add_argument("--no-ui", action="store_true", help="UI 서버를 구동하지 않고 엔진만 실행합니다")
    args = parser.parse_args()

    shared = SharedState()
    config = load_trading_config()
    
    # UI 포커스 및 초기 대상 설정
    global_strat = config.get("GLOBAL_STRATEGY", {})
    simulation_mode = global_strat.get("SIMULATION_MODE", False)

    # 저장된 매매 및 서버 활성화 상태 복원 (영속성 보장)
    saved_trading_active = global_strat.get("TRADING_ACTIVE")
    if saved_trading_active is not None:
        trading_active = bool(saved_trading_active)
        logging.info(f"[Supervisor] 💾 저장된 매매 상태 복원: TRADING_ACTIVE = {trading_active}")
    elif simulation_mode:
        trading_active = True
        logging.info("[Supervisor] SIMULATION_MODE=true detected → trading_active 기본 활성화")
    else:
        trading_active = False
        logging.info("[Supervisor] SIMULATION_MODE=false → trading_active 기본 비활성화")

    saved_server_active = global_strat.get("SERVER_ACTIVE", True)

    initial_control = {
        "ui_focus_symbol": global_strat.get("UI_FOCUS_SYMBOL", "KRW-ETH"),
        "target_symbols": [t['symbol'] for t in config.get("SCALPING_TARGETS", []) if t.get("active", True)],
        "simulation_mode": simulation_mode,
        "radar_active": global_strat.get("RADAR_ACTIVE", False),
        "trading_active": trading_active,
        "server_active": bool(saved_server_active)
    }

    shared.update_control(initial_control)

    # version.json 로드
    ver_info = {"version": VERSION, "commit": "dev", "deployed_at": "local"}
    try:
        ver_file = os.path.join(os.path.dirname(__file__), 'version.json')
        if not os.path.exists(ver_file):
            ver_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'version.json')
        if os.path.exists(ver_file):
            with open(ver_file, 'r', encoding='utf-8') as f:
                ver_info = json.load(f)
    except Exception:
        pass

    logging.info("=" * 80)
    logging.info(f"🚀 [Supervisor] CTrade Engine Start | Ver: {ver_info.get('version', VERSION)} | Commit: {ver_info.get('commit', 'dev')} | Deployed: {ver_info.get('deployed_at', 'local')}")
    logging.info(f"🚀 [Supervisor] Mode: {'Engine-Only' if args.no_ui else 'Full'}")
    logging.info("=" * 80)

    # 1. 텔레그램 봇 초기화 및 폴링 스레드 시작 (엔진보다 먼저)
    telegram_bot = TelegramBot(shared)
    telegram_bot.start()

    # 2. 엔진 프로세스 초기 실행
    engine_proc = Process(target=run_engine_process, args=(config, shared))
    engine_proc.start()

    # 시작 알림
    active_symbols = [t['symbol'] for t in config.get("SCALPING_TARGETS", []) if t.get("active", True)]
    telegram_bot.notify_start(
        version=ver_info.get('version', VERSION),
        mode=str(simulation_mode),
        active_symbols=active_symbols
    )

    # 2. UI 서버 프로세스 실행
    ui_proc = None
    if not args.no_ui:
        ui_proc = Process(target=run_api_server, args=(shared,))
        ui_proc.start()

    exit_code = 0
    try:
        import time
        while True:
            # 1. 재시작 신호 최우선 확인 (엔진 생존 여부와 관계없이 즉시 처리)
            if shared.control.get("restart_engine"):
                logging.info("♻️ [Supervisor] Critical Restart Signal Received. Forcing Cleanup...")
                shared.update_control({"restart_engine": False}) # 플래그 초기화
                
                # 모든 자식 프로세스 즉시 강제 종료 (Kill 포함)
                _cleanup_process(ui_proc, "UI Process")
                _cleanup_process(engine_proc, "Engine Process")
                
                logging.info(f"🚀 [Supervisor] System Respawning now: {sys.executable} {' '.join(sys.argv)}")
                
                # 자기 자신을 처음 명령 그대로 다시 실행 (포트 해제 대기를 위해 짧게 sleep)
                time.sleep(0.5)
                os.execv(sys.executable, [sys.executable] + sys.argv)
                break # (execv가 성공하면 이 아래는 실행되지 않음)

            # 2. 예기치 못한 엔진 종료 감시
            if not engine_proc.is_alive():
                logging.warning("⚠️ [Supervisor] Engine process died unexpectedly. Breaking loop.")
                telegram_bot.notify_error("Engine process died unexpectedly — 재시작 필요")
                exit_code = 1
                break
            
            # 3. UI 프로세스 생존 확인 및 복구
            if ui_proc and not ui_proc.is_alive():
                logging.error("❌ [Supervisor] UI Process died. Restarting UI...")
                ui_proc = Process(target=run_api_server, args=(shared,))
                ui_proc.start()

            time.sleep(1.0)
            
    except KeyboardInterrupt:
        logging.info("🛑 [Supervisor] Shutdown by User (SIGINT)")
        try:
            telegram_bot.notify_stop("사용자 종료 (SIGINT)")
        except (Exception, KeyboardInterrupt):  # 두 번째 Ctrl+C도 차단
            pass
    finally:
        # Graceful cleanup
        shared.update_control({"is_running": False})
        _cleanup_process(ui_proc, "UI Process")
        _cleanup_process(engine_proc, "Engine Process")
        logging.info("✨ [Supervisor] All processes cleaned up. Bye.")
        logging.shutdown()
        if exit_code != 0:
            sys.exit(exit_code)