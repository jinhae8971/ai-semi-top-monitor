"""
scoring.py
==========
카테고리 점수를 가중 합산해 0~100 composite TOP score 를 만들고,
5-tier regime 으로 분류한다.

핵심 로직 — 결정적 트리거 오버레이:
  센티먼트/밸류만으로는 천장을 선언하지 않는다(1년 일찍 죽음).
  '선행지표(capex 감속, 수급 peak-out, EPS revision 하향)' 가 2개 이상
  동시에 켜질 때만 regime 을 한 단계 격상한다.
"""
from __future__ import annotations
from indicators import run_all_indicators, CATEGORY_WEIGHTS, CATEGORY_LABELS


# 5-tier regime 정의 (risk-regime-monitor 패턴)
REGIMES = [
    (0,  20, "ACCUMULATE",  "🟢", "사이클 초중반 - 비중 확대 구간"),
    (20, 40, "UPTREND",     "🟢", "건강한 상승 추세 - 보유/추세 추종"),
    (40, 60, "LATE_CYCLE",  "🟡", "후기 사이클 - 경계/일부 익절"),
    (60, 80, "EUPHORIA",    "🟠", "유포리아/블로우오프 - 방어 태세"),
    (80, 101, "TOP_ZONE",   "🔴", "사이클 천장 영역 - 청산/헤지"),
]


def classify_regime(score: float) -> dict:
    for lo, hi, name, emoji, desc in REGIMES:
        if lo <= score < hi:
            return {"name": name, "emoji": emoji, "desc": desc}
    return {"name": "TOP_ZONE", "emoji": "🔴", "desc": "사이클 천장 영역 - 청산/헤지"}


def compute(market: dict, fundamentals: dict) -> dict:
    cats = run_all_indicators(market, fundamentals)

    # 가중 합산
    composite = sum(cats[k]["score"] * CATEGORY_WEIGHTS[k] for k in CATEGORY_WEIGHTS)

    # --- 결정적 트리거 수집 ---
    eps_breadth = fundamentals.get("capex", {}).get("eps_revision_breadth", "flat")
    decisive = {
        "capex_decel":       cats["capex"].get("trigger_decel", False),
        "supply_peak_out":   cats["supply_demand"].get("trigger_supply_peak", False),
        "eps_revision_down": eps_breadth == "falling",
    }
    structural_crack = cats["financing"].get("trigger_financing_crack", False)
    n_decisive = sum(1 for v in decisive.values() if v)

    base_regime = classify_regime(composite)
    escalated = False
    final_regime = base_regime
    if n_decisive >= 2:
        # regime 한 단계 격상
        escalated = True
        idx = next(i for i, r in enumerate(REGIMES) if r[2] == base_regime["name"])
        idx = min(idx + 1, len(REGIMES) - 1)
        lo, hi, name, emoji, desc = REGIMES[idx]
        final_regime = {"name": name, "emoji": emoji, "desc": desc}

    return {
        "composite": round(composite, 1),
        "regime": final_regime,
        "base_regime": base_regime,
        "escalated": escalated,
        "categories": {
            k: {
                "score": round(cats[k]["score"], 1),
                "weight": CATEGORY_WEIGHTS[k],
                "label": CATEGORY_LABELS[k],
                "detail": cats[k]["detail"],
            } for k in CATEGORY_WEIGHTS
        },
        "decisive_triggers": decisive,
        "decisive_count": n_decisive,
        "structural_crack": structural_crack,
        "data_gaps": market.get("data_gaps", []),
    }
