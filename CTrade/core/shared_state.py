import threading
from multiprocessing import Manager

class SharedState:
    def __init__(self):
        self.manager = Manager()
        # 모든 종목의 최신 상태를 담을 공유 딕셔너리
        self.data = self.manager.dict()
        # 시스템 제어용 플래그
        self.control = self.manager.dict({
            "is_running": True,
            "log_msg": "System Initialized",
            "ui_focus_symbol": "KRW-ETH",
            "restart_engine": False,
            "server_active": True,
            "trading_active": False,
            "simulation_mode": True
        })
        # UI -> Engine 명령 전달을 위한 큐 추가
        self.cmd_queue = self.manager.Queue()

    def update_symbol_state(self, symbol, state_dict):
        self.data[symbol] = state_dict

    def update_control(self, updates: dict):
        """
        control dict 부분 갱신 헬퍼.
        과거에는 전체 dict 를 복사→수정→통째로 되쓰기(read-modify-write)하여 동시 갱신이 서로의 키를
        덮어썼다. 이제 변경할 키만 기록하고, 같은 프로세스 내 동시 호출은 락으로 직렬화한다.
        (Manager dict 프록시의 update 는 서버 측에서 원자적으로 처리되며 변경 키만 전달한다)
        """
        if not updates:
            return
        lock = getattr(self, "_control_lock", None)
        if lock is None:
            lock = self._control_lock = threading.Lock()
        with lock:
            self.control.update(dict(updates))

    def get_state(self):
        return self.data

    def __getstate__(self):
        """
        multiprocessing을 통한 프로세스 생성 시 직렬화(Pickle) 대상에서 
        직렬화 불가능한 SyncManager(self.manager) 객체를 제외합니다.
        """
        state = self.__dict__.copy()
        if 'manager' in state:
            del state['manager']
        state.pop('_control_lock', None)  # threading.Lock 은 pickle 불가
        return state

    def __setstate__(self, state):
        """
        자식 프로세스 측에서 직렬화된 데이터를 복원할 때 호출됩니다.
        자식 프로세스에서는 manager 객체가 필요 없으므로 None으로 처리합니다.
        """
        self.__dict__.update(state)
        self.manager = None