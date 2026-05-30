# AI Semi Cycle-Top Monitor

AI 반도체 랠리의 **천장(cycle top)** 을 다중지표 composite score 로 판단하는
서버리스 모니터. `risk-regime-monitor` 패턴(0~100 composite + 5-tier regime +
Telegram 알림 + 평일 7AM KST)을 그대로 따른다.

## 핵심 설계 철학

> 단일 지표로는 천장을 못 잡는다. 선행→동행→후행의 시차가 다른 6개 카테고리를
> 묶고, **'레벨'이 아니라 '증가율의 2차 미분(가속→감속 전환)'** 을 본다.
> 센티먼트·밸류만으로는 천장을 선언하지 않는다(1년 일찍 죽음).
> **선행 트리거 2개 이상 동시 발화 시에만 regime 격상.**

## 6-카테고리 가중치

| # | 카테고리 | 가중치 | 성격 | 주요 지표 | 소스 |
|---|---|---|---|---|---|
| 1 | Capex 사이클 | 30% | 최강 선행 | capex YoY 2차미분, 가이던스, FCF | fundamentals.json |
| 2 | 수급/물량 | 25% | 신뢰 동행 | 메모리 ASP peak-out, Book-to-Bill, 재고 | fundamentals.json |
| 3 | 신용/파이낸싱 | 20% | 구조 리스크 | 순환출자 추세, 매출의 질, 채권스프레드 | fundamentals.json |
| 4 | 밸류에이션 | 10% | 동행/후행 | SOX fwd P/E percentile, 가격위치 | yfinance + json |
| 5 | 센티먼트 | 10% | 역행 | streak, 주간수익률, 200DMA, VIX, 스마트머니숏 | yfinance + json |
| 6 | 기술적/브레드스 | 5% | 확인 | RSI, 200DMA 이격, 브레드스 다이버전스 | yfinance + json |

## 5-tier Regime

| Score | Regime | 의미 |
|---|---|---|
| 0–20 | 🟢 ACCUMULATE | 사이클 초중반 — 비중 확대 |
| 20–40 | 🟢 UPTREND | 건강한 상승 — 추세 추종 |
| 40–60 | 🟡 LATE_CYCLE | 후기 사이클 — 경계/일부 익절 |
| 60–80 | 🟠 EUPHORIA | 블로우오프 — 방어 태세 |
| 80–100 | 🔴 TOP_ZONE | 천장 영역 — 청산/헤지 |

## 결정적 트리거 (선행 — 매일 확인)

천장 확정은 아래가 **2개 이상 동시** 발화할 때:
1. **Capex 성장률 감속 전환** (2차 미분 ≤ −5%p)
2. **메모리 ASP peak-out + Book-to-Bill < 1.0**
3. **EPS revision breadth 하향 전환**

+ 구조: 순환출자 균열 (단독으로는 격상 안 함, 경고 표시)

## 자동화 계층 (v1.2)

`auto_fundamentals.py`가 매 실행마다 자동 추출 가능한 펀더멘털을 갱신한다 (수동 필드는 보존):
- **Capex**: yfinance 빅4(MSFT/GOOGL/AMZN/META) 분기 cashflow → aggregate YoY 성장률 + FCF 상태 (분기태그로 dedupe, 신규 분기 자동 append)
- **신용 스프레드**: FRED HY OAS(BAMLH0A0HYM2, 키 불필요) → tight/widening 자동 판정
- **스테일니스 가드**: 수동 필드(ASP/B2B/순환출자)가 45일+ 경과 시 리포트에 경고

여전히 수동(분기/월간)으로 갱신할 것: 메모리 ASP, Book-to-Bill, 순환출자 추세, EPS revision breadth.

## 검증 철학 (v1.4) — 전향(forward) 검증 주력

현재 AI capex 슈퍼사이클은 역사적 유사 국면이 없는 regime이라, 다른 성격의 과거
사이클에 fitting하는 백테스트는 표본 대표성이 약하다. 따라서 **out-of-sample 전향
검증을 주력**으로 한다.

- **`forward_validation.py`**: 매 실행마다 박제되는 신호 스냅샷(history.json)을
  시간이 지나며 실현된 KOSPI 결과와 자동 페어링 → 현재 regime 내 5/20/60거래일
  예측력(상관·과열후 하락률·작동여부)을 `n≥12`부터 누적 집계. `data/validation.json` 발행.
  TOP 지표가 유효하면 composite↑ → 향후 KOSPI 수익률↓ (음의 상관).
- **`backtest.py`** (참고용): 2년 과거 시장신호 vs KOSPI. *"과거는 대표성 없음"의 근거
  자료*로 보존 — 강세장 표본에선 단기 타이밍 효과가 확인되지 않음(corr +0.11~+0.15).

## 생태계 통합

- **cycle-intelligence-hub**: `data/latest.json`(`asts` 스키마)을 GitHub Pages로 발행 → hub registry에 `ASTS`로 등록. CCI/ASCI/KVR/UVR와 함께 통합 대시보드 표출
- **github-actions-dashboard**: `config/systems.yaml`의 `cycle-intelligence` 그룹에 producer로 등록
- 데이터 피드: `https://jinhae8971.github.io/ai-semi-top-monitor/data/latest.json`

## 파일 구조

```
ai-semi-top-monitor/
├── monitor.py            # 엔트리: 수집→스코어링→Telegram(리포트+차트)
├── data_sources.py       # yfinance + fundamentals.json 로더
├── indicators.py         # 6 카테고리 0~100 스코어링
├── scoring.py            # composite + regime + 트리거 오버레이
├── history.py            # state 영속화 + 7d/30d momentum + 추세 차트
├── fundamentals.json     # ★수동 갱신 계층 (분기/월간)
├── state/history.json    # composite 시계열 (워크플로우가 자동 커밋백)
├── requirements.txt
├── config.json.example   # 로컬 테스트용
├── setup_github.ps1      # 원클릭 배포
└── .github/workflows/monitor.yml   # 평일 7AM KST
```

## 배포

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_github.ps1
```
gh CLI 없으면 화면 안내대로 Secrets 수동 등록 후 Enter.

## 로컬 테스트

```bash
pip install -r requirements.txt
python monitor.py        # config.json 없으면 dry-run(콘솔 출력)
```

## 갱신 운영 (★중요)

`fundamentals.json` 만 주기적으로 갱신하면 된다. 시장데이터는 자동.

- **분기 (실적 시즌 후)**: `capex.yoy_growth_history_pct` 끝에 신규 분기 YoY 추가,
  `guidance_direction`, `fcf_status`, `eps_revision_breadth` 갱신
- **월간 (TrendForce/SEMI)**: `supply_demand.asp_qoq_history_pct` 추가,
  `book_to_bill`, `inventory_trend` 갱신
- **수시 (뉴스)**: `financing.*`, `sentiment.smart_money_short` 갱신

## github-actions-dashboard 등록

`github-actions-dashboard/orchestrator.py` 에 추가:

```python
REPOS.append("ai-semi-top-monitor")

REPORT_MAP["ai-semi-top-monitor"] = {
    "title": "AI Semi Cycle-Top Monitor",
    "category": "market-intelligence",
    "schedule": "평일 07:00 KST",
    "workflow": "monitor.yml",
}
```

이후 `secrets-vault/setup-new-repo.yml` 로 신규 레포 Secrets 주입,
`update-data.yml` 수동 트리거로 대시보드 동기화.

---
*본 모니터는 정보 제공용이며 투자 권유가 아닙니다.*

---

## CHANGELOG — 2026-05-30 구축 기록

하루에 v1.0 → v1.9 로 구축. 진행 순서:

| 버전 | 내용 |
|---|---|
| v1.0 | 초기 배포. 6카테고리 composite + 5-tier regime + 결정적 트리거 오버레이, yfinance + fundamentals.json, Telegram 리포트, 평일 7AM KST |
| v1.1 | history 영속화(`state/history.json`) + composite 추세 차트(velocity 모멘텀), Telegram 사진 발송 |
| v1.2 | 자동 펀더멘털(`auto_fundamentals.py` — 빅4 capex YoY + FRED HY OAS), `data/latest.json` hub 피드. 레포 public 전환 + GitHub Pages |
| v1.3 | 추세 차트에 KOSPI 누적 오버레이 + `backtest.py`(2년 시장신호 vs KOSPI 단기 검증) |
| v1.4 | 전향(out-of-sample) 검증(`forward_validation.py`) — 신호 스냅샷 ↔ 실현 KOSPI 페어링. 과거 백테스트 대신 전향 검증 주력 |
| v1.5 | 메모리 4강 YTD 수익률 차트(`memory_chart.py`) Telegram 발송 |
| v1.6 | NVIDIA·Intel 추가 → "Semiconductor Leaders" 6종 |
| v1.7 | forward P/E 차트(`fwd_pe_chart.py` — 현재값 바 → 누적되면 로그 추세), `state/fwd_pe_history.json` |
| v1.8 | 브레드스 자동화(`breadth.py` — 25종 바스켓, divergence 탐지 → technical 주입) + 카탈리스트 캘린더(`catalysts.py` — 실적일 + FOMC/CPI) |
| v1.9 | 시그널 저널(`signal_journal.py` — 트리거/regime 전환 영구 박제 + 사후 KOSPI/SOX 경로 추적). git add 글롭 버그 수정(last_state 영속) |

### 생태계 통합
- `cycle-intelligence-hub/registry.yaml` 에 `ASTS` 등록 → hub_summary.json 표출 (CCI·ASCI·KVR·UVR와 함께 5번째 시스템)
- `github-actions-dashboard/config/systems.yaml` 의 `cycle-intelligence` 그룹에 producer 등록

### 매일 발송물 (평일 7AM KST · 텔레그램 "그래이프스" DM, chat_id 954137156)
- 리포트: composite · regime · 결정적 트리거 · forward validation · breadth · signal journal · 카탈리스트
- 차트 3종: composite 추세(+KOSPI) / Semiconductor Leaders YTD 6종 / forward P/E 6종

### 2026-05-30 기준 판독
Composite 58.7 / 100 · LATE_CYCLE. 선행 트리거 0/3(순환출자 균열만 ON), breadth 200일선 상회 100%(다이버전스 없음) → "과열이나 아직 꼭지 아님".

### 다음 후보 작업
- 드로다운/트레일링 스탑 알림(매매 트리거 성격 → 알림만)
- fundamentals.json 추가 자동화(TrendForce ASP / SEMI B/B)
- 다음 달: forward validation + signal journal 경로로 중간 점검
