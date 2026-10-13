# CCommander (Vultr 원격 서버 관제 및 CTrade 제어 모듈)

스마트폰 텔레그램을 통해 PC 없이 Vultr 서버의 상태를 실시간 모니터링하고, `CTrade` 매매 엔진의 가동/종료/재기동 및 KIS 토큰 관리, 서버 시스템 자원을 안전하게 원격 제어하는 독립 모듈입니다.

---

## 1. 주요 기능

* **서비스 제어**: `CTrade` 매매 엔진 시작, 종료, 재기동
* **인프라 모니터링**: CPU, RAM, 디스크 사용량, 서버 업타임 실시간 조회
* **실시간 로그 스트리밍**: `journalctl` 최근 20줄 로그 즉시 수신
* **인증 관리**: KIS OAuth 토큰 만료 시간 점검
* **안전 재부팅**: Vultr 원격 서버 안전 재부팅 (15초 이중 확인)
* **상시 가동 & 자동 복구**: `systemd` 데몬 등록으로 서버 재부팅 시 100% 자동 실행 및 알림 전송

---

## 2. 텔레그램 명령어 요약

| 명령어 | 설명 | 예시 |
| :--- | :--- | :--- |
| `/balance` | CTrade 운용 잔고, 누적 실현손익, 승률 및 보유 포지션 조회 | `/balance` |
| `/trades` | 최근 체결 내역 상세 및 최근 10건 승률 통계 확인 | `/trades` 또는 `/trades 10` |
| `/status` | 서버 자원, CTrade 상태, 트레이딩 누적 성과, KIS 토큰 종합 현황 | `/status` |
| `/start` | CTrade 매매 엔진 가동 | `/start` 또는 `/start ctrade` |
| `/stop` | CTrade 엔진 완전 종료 (로컬 세션 해제) | `/stop` 또는 `/stop ctrade` |
| `/restart` | CTrade 엔진 재기동 | `/restart` |
| `/logs` | 실시간 저널 로그 확인 | `/logs` 또는 `/logs 30` |
| `/token` | KIS 토큰 만료 시간 점검 | `/token` |
| `/security` | 2FA 웹 보안 활성 세션 및 차단된 IP 목록 조회 | `/security` |
| `/unblock` | 차단된 특정 IP 즉시 잠금 해제 | `/unblock 1.2.3.4` |
| `/reboot` | Vultr 서버 원격 재부팅 (15초 내 재입력) | `/reboot` |
| `/help` | 명령어 도움말 표시 | `/help` |

---

## 3. Vultr 서비스 등록 및 실행

```bash
# Vultr 서버 내에서 실행
./CCommander/setup_service.sh
```
