"""
indicators.py
=============
6개 카테고리를 각각 0~100 의 "TOP 근접도(top-proximity)" 점수로 환산한다.
  - 점수가 높을수록 = 사이클 천장에 가깝다 (위험)
  - 점수가 낮을수록 = 사이클 초중반 (안전)

설계 원칙 (영길님 프레임워크):
  * Capex/Supply 는 '레벨'이 아니라 '증가율의 2차 미분(가속->감속 전환)'을 본다.
  * 가격이 극단적으로 비싸도 '아직 상승률이 가속 중'이면 천장 아님 -> 점수 억제.
  * '상승률이 꺾이는 순간'에 점수가 급등하도록 설계.
"""
from __future__ import annotations
import numpy as np


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _second_derivative(series: list[float]) -> float:
    """
    증가율 시계열의 최근 변화(2차 미분 프록시, %p).
    음수 = 감속(천장 신호), 양수 = 가속(아직 상승).
    """
    if not series or len(series) < 2:
        return 0.0
    return float(series[-1] - series[-2])


# ----------------------------------------------------------------------------
# Cat 1. CAPEX 사이클 (가중치 30%) - 최강 선행지표
# ----------------------------------------------------------------------------
def score_capex(f: dict) -> dict:
    cap = f.get("capex", {})
    hist = cap.get("yoy_growth_history_pct", [])
    d2 = _second_derivative(hist)  # 성장률 변화(%p)

    # 2차 미분: 감속할수록 천장 근접
    if d2 <= -15:
        s_d2 = 92
    elif d2 <= -5:
        s_d2 = 72
    elif d2 < 5:
        s_d2 = 50          # 정체(플래토) = 1차 경보
    elif d2 < 15:
        s_d2 = 30
    else:
        s_d2 = 12          # 강한 가속 = 사이클 한창

    guide = cap.get("guidance_direction", "held")
    s_guide = {"raised": 20, "held": 55, "cut": 90}.get(guide, 55)

    fcf = cap.get("fcf_status", "compressing")
    s_fcf = {"positive": 22, "compressing": 55, "negative": 85}.get(fcf, 55)

    score = 0.50 * s_d2 + 0.30 * s_guide + 0.20 * s_fcf
    return {
        "score": _clamp(score),
        "detail": {
            "capex_growth_2nd_deriv_pp": round(d2, 1),
            "latest_yoy_growth_pct": hist[-1] if hist else None,
            "guidance_direction": guide,
            "fcf_status": fcf,
        },
        "trigger_decel": d2 <= -5,   # 결정적 트리거 #1: capex 감속 전환
    }


# ----------------------------------------------------------------------------
# Cat 2. 수급/물량 사이클 (가중치 25%) - 신뢰도 최상 동행
# ----------------------------------------------------------------------------
def score_supply_demand(f: dict) -> dict:
    sd = f.get("supply_demand", {})
    asp_hist = sd.get("asp_qoq_history_pct", [])
    d2 = _second_derivative(asp_hist)  # ASP 상승률의 변화(%p)

    # ASP 상승률이 '꺾이면(peak-out)' 천장 신호
    if d2 <= -15:
        s_asp = 88
    elif d2 <= -5:
        s_asp = 70          # peak-out 형성 중
    elif d2 < 5:
        s_asp = 52
    elif d2 < 15:
        s_asp = 32
    else:
        s_asp = 18          # 여전히 가속 (가격 극단이어도 천장 아님)

    b2b = sd.get("book_to_bill", 1.05)
    if b2b is None:
        s_b2b = 50
    elif b2b < 0.95:
        s_b2b = 92
    elif b2b < 1.0:
        s_b2b = 75
    elif b2b < 1.1:
        s_b2b = 50
    else:
        s_b2b = 28

    inv = sd.get("inventory_trend", "tight")
    s_inv = {"tight": 25, "stabilizing": 55, "rising": 85}.get(inv, 50)

    score = 0.40 * s_b2b + 0.35 * s_asp + 0.25 * s_inv
    # 결정적 트리거 #2: ASP peak-out + B/B 1.0 하회 동시
    trig = (d2 <= -5) and (b2b is not None and b2b < 1.0)
    return {
        "score": _clamp(score),
        "detail": {
            "asp_qoq_2nd_deriv_pp": round(d2, 1),
            "latest_asp_qoq_pct": asp_hist[-1] if asp_hist else None,
            "book_to_bill": b2b,
            "inventory_trend": inv,
            "asp_peak_out_forming": d2 <= -5,
        },
        "trigger_supply_peak": trig,
    }


# ----------------------------------------------------------------------------
# Cat 3. 신용/파이낸싱 구조 (가중치 20%) - late-cycle 구조 리스크
# ----------------------------------------------------------------------------
def score_financing(f: dict) -> dict:
    fin = f.get("financing", {})
    circ = fin.get("circular_financing_trend", "plateauing")
    # 순환출자 '축소/균열' = 인위적 수요 소멸 = 약세 신호
    s_circ = {"expanding": 42, "plateauing": 65, "contracting": 90}.get(circ, 65)

    rq = fin.get("revenue_quality", "some_vendor_financing")
    s_rq = {"clean": 25, "some_vendor_financing": 60, "high_vendor_dependence": 85}.get(rq, 60)

    spread = fin.get("dc_credit_spread", "tight")
    s_sp = {"tight": 30, "widening": 78}.get(spread, 50)

    score = 0.45 * s_circ + 0.35 * s_rq + 0.20 * s_sp
    return {
        "score": _clamp(score),
        "detail": {
            "circular_financing_trend": circ,
            "revenue_quality": rq,
            "dc_credit_spread": spread,
        },
        "trigger_financing_crack": circ == "contracting",  # 구조적 균열 플래그
    }


# ----------------------------------------------------------------------------
# Cat 4. 밸류에이션 (가중치 10%) - 동행/후행, "비싸다"만 알려줌
# ----------------------------------------------------------------------------
def score_valuation(market: dict, f: dict) -> dict:
    val = f.get("valuation", {})
    fwd_pe_pct = val.get("sox_fwd_pe_percentile")  # 수동 입력 우선

    sox = market.get("sox")
    nvda = market.get("nvda")
    price_pct = sox["price_percentile_52w"] if sox else None
    nvda_pct = nvda["price_percentile_52w"] if nvda else None

    parts = []
    if fwd_pe_pct is not None:
        parts.append(fwd_pe_pct)            # forward P/E percentile (가장 정확)
    if price_pct is not None:
        parts.append(price_pct)             # 가격 위치 프록시
    if nvda_pct is not None:
        parts.append(nvda_pct * 0.9)        # 집중 리스크 (소폭 디스카운트)

    score = float(np.mean(parts)) if parts else 50.0
    return {
        "score": _clamp(score),
        "detail": {
            "sox_fwd_pe_percentile": fwd_pe_pct,
            "sox_price_percentile_52w": round(price_pct, 1) if price_pct is not None else None,
            "nvda_price_percentile_52w": round(nvda_pct, 1) if nvda_pct is not None else None,
        },
    }


# ----------------------------------------------------------------------------
# Cat 5. 센티먼트/포지셔닝 (가중치 10%) - contrarian 역행지표
# ----------------------------------------------------------------------------
def score_sentiment(market: dict, f: dict) -> dict:
    sen = f.get("sentiment", {})
    sox = market.get("sox")
    vix = market.get("vix")

    # 연속/고빈도 상승 (23일 중 상승일)
    if sox and sox.get("win_days_23") is not None:
        w = sox["win_days_23"]
        s_streak = _clamp((w - 12) / (22 - 12) * 100) if w >= 12 else 10
    else:
        s_streak = 50

    # 주간 수익률(파라볼릭)
    if sox and sox.get("weekly_return_pct") is not None:
        wk = sox["weekly_return_pct"]
        s_wk = _clamp(wk / 10.0 * 100)  # +10%/주 = 100
    else:
        s_wk = 50

    # 200일선 이격도 (과열)
    if sox and sox.get("dist_200_pct") is not None:
        d = sox["dist_200_pct"]
        s_dist = _clamp(d / 30.0 * 100)  # +30% 이격 = 100
    else:
        s_dist = 50

    # VIX 저변동(안주)
    if vix and vix.get("last") is not None:
        v = vix["last"]
        s_vix = _clamp((20 - v) / (20 - 12) * 100) if v <= 20 else 20
    else:
        s_vix = 50

    # 스마트머니 공매도 진입 (수동 플래그, 예: Burry)
    short_flag = bool(sen.get("smart_money_short", False))
    s_short = 85 if short_flag else 40

    # ETF 자금유입 상태
    flow = sen.get("etf_flow_state", "neutral")
    s_flow = {"outflow": 25, "neutral": 50, "record_inflow": 88}.get(flow, 50)

    score = (0.22 * s_streak + 0.20 * s_wk + 0.18 * s_dist +
             0.12 * s_vix + 0.14 * s_short + 0.14 * s_flow)
    return {
        "score": _clamp(score),
        "detail": {
            "win_days_in_23": sox.get("win_days_23") if sox else None,
            "weekly_return_pct": round(sox["weekly_return_pct"], 1) if sox else None,
            "dist_above_200dma_pct": round(sox["dist_200_pct"], 1) if sox else None,
            "vix": round(vix["last"], 1) if vix else None,
            "smart_money_short": short_flag,
            "etf_flow_state": flow,
        },
    }


# ----------------------------------------------------------------------------
# Cat 6. 기술적/브레드스 (가중치 5%) - 확인용 동행
# ----------------------------------------------------------------------------
def score_technical(market: dict, f: dict) -> dict:
    tech = f.get("technical", {})
    sox = market.get("sox")

    # RSI 과매수
    if sox and sox.get("rsi14") is not None:
        rsi = sox["rsi14"]
        s_rsi = _clamp((rsi - 50) / (80 - 50) * 100) if rsi >= 50 else 20
    else:
        s_rsi = 50

    # 200일선 이격도 (재사용)
    if sox and sox.get("dist_200_pct") is not None:
        s_dist = _clamp(sox["dist_200_pct"] / 30.0 * 100)
    else:
        s_dist = 50

    # 브레드스 다이버전스 (수동 플래그 우선)
    div_flag = bool(tech.get("breadth_divergence", False))
    s_div = 85 if div_flag else 45

    score = 0.40 * s_rsi + 0.30 * s_dist + 0.30 * s_div
    return {
        "score": _clamp(score),
        "detail": {
            "rsi14": round(sox["rsi14"], 1) if sox and sox.get("rsi14") else None,
            "dist_above_200dma_pct": round(sox["dist_200_pct"], 1) if sox else None,
            "breadth_divergence": div_flag,
        },
    }


# ----------------------------------------------------------------------------
# 통합 실행
# ----------------------------------------------------------------------------
CATEGORY_WEIGHTS = {
    "capex": 0.30,
    "supply_demand": 0.25,
    "financing": 0.20,
    "valuation": 0.10,
    "sentiment": 0.10,
    "technical": 0.05,
}

CATEGORY_LABELS = {
    "capex": "Capex 사이클",
    "supply_demand": "수급/물량",
    "financing": "신용/파이낸싱",
    "valuation": "밸류에이션",
    "sentiment": "센티먼트",
    "technical": "기술적/브레드스",
}


def run_all_indicators(market: dict, fundamentals: dict) -> dict:
    return {
        "capex": score_capex(fundamentals),
        "supply_demand": score_supply_demand(fundamentals),
        "financing": score_financing(fundamentals),
        "valuation": score_valuation(market, fundamentals),
        "sentiment": score_sentiment(market, fundamentals),
        "technical": score_technical(market, fundamentals),
    }
