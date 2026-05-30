"""
breadth.py
==========
반도체 바스켓의 시장 폭(breadth)을 자동 계산한다. 천장은 거의 항상
breadth divergence(지수는 신고가인데 참여 종목 감소)로 먼저 온다.

산출:
  - pct_above_200dma / pct_above_50dma  (바스켓 중 이동평균 상회 비율)
  - net_new_highs_pct                    (52주 신고가 - 신저가, % of basket)
  - index_near_high                      (SOXX 가 52주 고점 대비 -3% 이내)
  - divergence                           (지수 신고가권 & 200일선 상회비율 < 60%)
  - breadth_score (0~100 top-proximity)  : 좁을수록(divergence) 높음
"""
from __future__ import annotations
import numpy as np

BASKET = [
    "NVDA", "AMD", "AVGO", "MU", "INTC", "QCOM", "TXN", "ADI", "MRVL", "NXPI",
    "ON", "MCHP", "ARM", "SNDK", "WDC", "TSM", "ASML", "AMAT", "LRCX", "KLAC",
    "SMCI", "DELL", "ANET", "005930.KS", "000660.KS",
]
INDEX = "SOXX"


def _dl(tickers, period="1y"):
    import yfinance as yf
    try:
        df = yf.download(tickers, period=period, interval="1d",
                         auto_adjust=True, progress=False, group_by="ticker")
        return df
    except Exception:
        return None


def compute() -> dict:
    """바스켓 breadth 지표 산출. 실패 시 빈 dict."""
    import yfinance as yf
    closes = {}
    for t in BASKET + [INDEX]:
        try:
            s = yf.Ticker(t).history(period="1y", auto_adjust=True)["Close"].dropna()
            if len(s) >= 200:
                closes[t] = s
        except Exception:
            continue
    if len(closes) < 10:
        return {}

    above200 = above50 = newhigh = newlow = n = 0
    for t in BASKET:
        s = closes.get(t)
        if s is None:
            continue
        n += 1
        last = float(s.iloc[-1])
        if last > float(s.iloc[-200:].mean()):
            above200 += 1
        if last > float(s.iloc[-50:].mean()):
            above50 += 1
        hi52 = float(s.max())
        lo52 = float(s.min())
        if last >= hi52 * 0.99:
            newhigh += 1
        if last <= lo52 * 1.02:
            newlow += 1
    if n == 0:
        return {}

    pct200 = above200 / n * 100
    pct50 = above50 / n * 100
    net_nh = (newhigh - newlow) / n * 100

    # 지수 신고가권 여부
    idx = closes.get(INDEX)
    index_near_high = False
    if idx is not None:
        index_near_high = float(idx.iloc[-1]) >= float(idx.max()) * 0.97

    # 다이버전스: 지수 신고가권인데 200일선 상회비율 낮음
    divergence = index_near_high and pct200 < 60

    # breadth_score (top-proximity): 좁을수록 높음
    # 200일 상회비율이 낮을수록 + 다이버전스면 가산
    s_narrow = max(0.0, min(100.0, (100 - pct200) * 1.1))
    if divergence:
        s_narrow = min(100.0, s_narrow + 25)
    breadth_score = round(s_narrow, 1)

    return {
        "basket_n": n,
        "pct_above_200dma": round(pct200, 1),
        "pct_above_50dma": round(pct50, 1),
        "net_new_highs_pct": round(net_nh, 1),
        "index_near_high": index_near_high,
        "divergence": divergence,
        "breadth_score": breadth_score,
    }


def report_lines(b: dict) -> list[str]:
    if not b:
        return ["📡 <b>Breadth</b>: 데이터 일시 불가"]
    div = "🔴 다이버전스 ON" if b["divergence"] else "🟢 정상"
    return [
        "📡 <b>Breadth (반도체 바스켓)</b>",
        f"   200일선 상회 {b['pct_above_200dma']}% · 50일선 {b['pct_above_50dma']}%",
        f"   순신고가 {b['net_new_highs_pct']:+}% · 지수신고가권 {'예' if b['index_near_high'] else '아니오'}",
        f"   {div} (score {b['breadth_score']})",
    ]
