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
