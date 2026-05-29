"""
data_sources.py
================
AI Semi Cycle-Top Monitor 의 데이터 수집 계층.

두 종류의 입력을 다룬다:
  1) MARKET DATA  : yfinance 로 매일 자동 수집 (가격/거래량/RSI/200DMA/연속상승 등)
                    -> Valuation / Sentiment / Technical 카테고리에 사용
  2) FUNDAMENTALS : fundamentals.json 으로 분기/월간 수동 갱신
                    -> Capex / Supply-Demand / Financing 카테고리에 사용
                    (실적 시즌·TrendForce·SEMI 리포트 발표 후 영길님이 직접 업데이트)

모든 fetch 는 실패해도 죽지 않고 neutral(50) 폴백 + data_gap 플래그를 남긴다.
"""
from __future__ import annotations
import os
import json
import math
from datetime import datetime

import numpy as np

try:
    import yfinance as yf
    _HAS_YF = True
except Exception:  # pragma: no cover
    _HAS_YF = False


# ----------------------------------------------------------------------------
# 추적 티커
# ----------------------------------------------------------------------------
TICKERS = {
    "sox_etf": "SOXX",     # iShares Semiconductor (SOX 추종)
    "smh": "SMH",          # VanEck Semiconductor
    "nvda": "NVDA",        # 단일 집중 리스크 대표주
    "vix": "^VIX",         # 변동성/안주(complacency)
}


def _safe_history(ticker: str, period: str = "2y", interval: str = "1d"):
    """yfinance 다운로드. 실패 시 None."""
    if not _HAS_YF:
        return None
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty or "Close" not in df:
            return None
        return df
    except Exception:
        return None


def _rsi(close, period: int = 14) -> float | None:
    """단순 Wilder RSI."""
    try:
        c = np.asarray(close, dtype=float)
        if len(c) < period + 1:
            return None
        delta = np.diff(c)
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = gain[-period:].mean()
        avg_loss = loss[-period:].mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))
    except Exception:
        return None


def fetch_market_data() -> dict:
    """
    시장 데이터를 수집/가공해 지표 계산에 바로 쓸 수 있는 dict 반환.
    네트워크/티커 실패는 None 으로 채워지고 indicators 단에서 neutral 처리.
    """
    out: dict = {"data_gaps": []}

    # --- SOX 추종 ETF (메인) ---
    df = _safe_history(TICKERS["sox_etf"], period="2y")
    if df is None:
        out["data_gaps"].append("sox_etf")
        out["sox"] = None
    else:
        close = df["Close"].values
        last = float(close[-1])
        ma200 = float(np.mean(close[-200:])) if len(close) >= 200 else float(np.mean(close))
        # 200일선 이격도 (%)
        dist_200 = (last / ma200 - 1.0) * 100.0 if ma200 else 0.0

        # 최근 23거래일 상승일 수 (센티먼트 streak)
        rets = np.diff(close)
        win23 = int(np.sum(rets[-23:] > 0)) if len(rets) >= 23 else int(np.sum(rets > 0))

        # 주간(5거래일) 수익률
        wk_ret = (close[-1] / close[-6] - 1.0) * 100.0 if len(close) >= 6 else 0.0

        # 52주 가격 percentile (밸류에이션 프록시 - forward P/E 추정 어려우므로 가격위치 사용)
        lookback = close[-252:] if len(close) >= 252 else close
        pctile = float((np.sum(lookback <= last) / len(lookback)) * 100.0)

        out["sox"] = {
            "last": last,
            "ma200": ma200,
            "dist_200_pct": dist_200,
            "win_days_23": win23,
            "weekly_return_pct": wk_ret,
            "price_percentile_52w": pctile,
            "rsi14": _rsi(close, 14),
        }

    # --- NVDA (집중 리스크) ---
    dfn = _safe_history(TICKERS["nvda"], period="2y")
    if dfn is None:
        out["data_gaps"].append("nvda")
        out["nvda"] = None
    else:
        c = dfn["Close"].values
        last = float(c[-1])
        lb = c[-252:] if len(c) >= 252 else c
        out["nvda"] = {
            "last": last,
            "price_percentile_52w": float((np.sum(lb <= last) / len(lb)) * 100.0),
            "rsi14": _rsi(c, 14),
        }

    # --- VIX (안주/공포) ---
    dfv = _safe_history(TICKERS["vix"], period="6mo")
    if dfv is None:
        out["data_gaps"].append("vix")
        out["vix"] = None
    else:
        out["vix"] = {"last": float(dfv["Close"].values[-1])}

    # --- SMH/SOXX 상대강도 (브레드스/리더십 확산 프록시) ---
    dsm = _safe_history(TICKERS["smh"], period="3mo")
    if dsm is not None and df is not None:
        try:
            smh_3m = (dsm["Close"].values[-1] / dsm["Close"].values[0] - 1) * 100
            sox_3m = (df["Close"].values[-1] / df["Close"].values[-len(dsm):][0] - 1) * 100
            out["breadth_proxy"] = {"smh_3m": smh_3m, "sox_3m": sox_3m}
        except Exception:
            out["breadth_proxy"] = None
    else:
        out["breadth_proxy"] = None

    out["fetched_at"] = datetime.utcnow().isoformat() + "Z"
    return out


def load_fundamentals(path: str | None = None) -> dict:
    """
    fundamentals.json 로드. 영길님이 분기/월간으로 갱신하는 수동 입력.
    파일이 없으면 안전한 neutral 기본값을 반환.
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "fundamentals.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # 폴백 (모두 neutral)
    return {
        "_note": "fundamentals.json not found - using neutral defaults",
        "capex": {
            "yoy_growth_history_pct": [50, 60, 70, 70],
            "guidance_direction": "held",
            "fcf_status": "compressing",
            "eps_revision_breadth": "flat",
        },
        "supply_demand": {
            "asp_qoq_history_pct": [40, 50, 60, 60],
            "book_to_bill": 1.05,
            "inventory_trend": "tight",
        },
        "financing": {
            "circular_financing_trend": "plateauing",
            "revenue_quality": "some_vendor_financing",
            "dc_credit_spread": "tight",
        },
        "valuation": {"sox_fwd_pe_percentile": None},
        "sentiment": {"etf_flow_state": "neutral", "smart_money_short": False},
        "technical": {"breadth_divergence": False},
    }
