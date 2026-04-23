# ⚡ KIS Fast Scalper V4.5

한국투자증권(KIS) OpenAPI를 활용한 고성능 스캘핑 자동매매 시스템입니다. 5틱 스코어링 시스템과 다중 익절/손절 전략을 통해 변동성이 큰 장 초반 시장을 공략합니다.

## 🛠️ 개발 환경 설정

### 1. 가상 환경 (Virtual Environment)
의존성 충돌을 방지하기 위해 가상 환경 사용을 권장합니다.

- **가상 환경 진입 (활성화):**
  ```bash
  source .venv/bin/activate
  ```
- **가상 환경 나가기 (비활성화):**
  ```bash
  deactivate
  ```

### 2. 세션 관리 (tmux)
터미널을 종료해도 프로그램이 중단되지 않도록 `tmux` 사용을 권장합니다.

- **새 세션 생성 및 시작:**
  ```bash
  tmux new -s scalper
  ```
- **세션 유지하며 나오기 (Detach):**
  `Ctrl + b` 를 누른 후 이어서 `d` 를 누릅니다.
- **기존 세션으로 복귀 (Attach):**
  ```bash
  tmux attach -t scalper
  ```
- **세션 종료:**
  ```bash
  tmux kill-session -t scalper
  ```

## 📌 주요 기능 및 전략

### 1. 세 가지 실행 모드 (`--mode`)
- `REAL`: 실거래 모드 (실제 계좌 사용)
- `PAPER`: 모의투자 모드 (한국투자증권 모의계좌 사용)
- `SIM`: 가상 시뮬레이션 (주문 없이 로깅 및 로직 검증만 수행)

### 2. 5틱 스코어링 분석 (`CandidateTracker`)
단순 급등주 포착을 넘어 최근 5틱의 흐름을 분석하여 매수 타점을 잡습니다.
- **가격 상승 + 체결 속도 급증 (>=200)**: +1.0점
- **가격 상승**: +0.5점
- **가격 유지 + 체결 속도 급증**: +0.3점
- **가격 하락 (매도세)**: -1.0점 (감점)
- **매수 신호**: 5틱 누적 점수가 **2.5점 이상**일 때 즉시 시장가 매수.

### 3. 정교한 탈출 전략 (Exit Strategy)
- **익절 (TP)**: 수익률 **+1.75%** 도달 시 즉시 매도.
- **타임컷 (Time-cut)**: 진입 후 15분 경과 시, 수익률이 **+0.5%** 이상이면 약수익 탈출.
- **손절 (SL)**: 수익률 **-2.5%** 도달 시 즉시 손절.
- **장 마감 강제 청산**: 오후 15:20 보유 중인 모든 종목 시장가 정리.

### 4. 운영 시간대
- **신규 매수**: 09:00 ~ 10:30 (시장 변동성 극대화 시간)
- **모니터링 및 매도**: 09:00 ~ 15:20
- **시스템 종료**: 15:20 이후 강제 청산 및 종료.

## 🚀 시작하기

### 1. 필수 라이브러리 설치
```bash
pip install requests python-dotenv
```

### 2. 환경 변수 설정 (`.env`)
루트 디렉토리에 `.env` 파일을 생성하고 아래 형식을 입력하세요.
```env
REAL_APP_KEY=실전_앱_키
REAL_APP_SECRET=실전_앱_시크릿
REAL_CANO=실전_계좌번호_8자리
REAL_ACNT_PRDT_CD=01

PAPER_APP_KEY=모의_앱_키
PAPER_APP_SECRET=모의_앱_시크릿
PAPER_CANO=모의_계좌번호_8자리
PAPER_ACNT_PRDT_CD=01
```

### 3. 프로그램 실행
```bash
# 가상 시뮬레이션 (기본값)
python3 fast_scalper.py --mode SIM

# 실거래 모드
python3 fast_scalper.py --mode REAL

# 모의투자 모드
python3 fast_scalper.py --mode PAPER
```

## 📝 로그 및 데이터 관리
- **실시간 로그**: `logs/scalper_{mode}.log` 파일에 모든 매매 판단 기록이 저장됩니다.
- **히스토리 기록**: 고속 체결이 발생한 종목 히스토리는 `scalper_picks_{mode}.csv`에 별도 저장됩니다.

---
**Senior Trader's Note**: 스캘핑은 찰나의 순간에 승부가 갈립니다. 반드시 `SIM` 또는 `PAPER` 모드에서 충분히 로직을 검증한 후 실전에 투입하시기 바랍니다.
