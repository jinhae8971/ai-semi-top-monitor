"""
catalysts.py
============
다가오는 카탈리스트 캘린더. 천장은 보통 특정 capex 가이던스/매크로 이벤트에서
꺾이므로, 미리 포지셔닝하도록 향후 N일 이벤트를 요약한다.

  - 실적일: yfinance 자동 추출 (워치리스트)
  - 매크로: FOMC 결정일(확정) + CPI 발표일 (Jun 10 확정, 이후 ~근사 표기)
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

WATCHLIST = {
    "NVDA": "NVIDIA", "MU": "Micron", "AVGO": "Broadcom", "AMD": "AMD",
    "TSM": "TSMC", "ASML": "ASML", "INTC": "Intel", "SNDK": "SanDisk",
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
}

# 매크로 이벤트 (date, label, confirmed). FOMC 결정일=회의 2일차.
MACRO_EVENTS = [
    ("2026-06-10", "CPI (5월)", True),
    ("2026-06-17", "FOMC 결정", True),
    ("2026-07-14", "CPI (6월)", False),
    ("2026-07-29", "FOMC 결정", True),
    ("2026-08-12", "CPI (7월)", False),
    ("2026-09-11", "CPI (8월)", False),
    ("2026-09-16", "FOMC 결정", True),
    ("2026-10-13", "CPI (9월)", False),
    ("2026-10-28", "FOMC 결정", True),
    ("2026-11-13", "CPI (10월)", False),
    ("2026-12-09", "FOMC 결정", True),
    ("2026-12-10", "CPI (11월)", False),
]


def _today():
    return datetime.now(KST).date()


def get_earnings(days_ahead: int = 14) -> list[dict]:
    """워치리스트 실적일 중 향후 days_ahead 이내."""
    try:
        import yfinance as yf
    except Exception:
        return []
    today = _today()
    horizon = today + timedelta(days=days_ahead)
    out = []
    for tk, name in WATCHLIST.items():
        try:
            ed = yf.Ticker(tk).get_earnings_dates(limit=12)
            if ed is None or ed.empty:
                continue
            for d in ed.index:
                dd = d.to_pydatetime().date()
                if today <= dd <= horizon:
                    out.append({"date": dd.isoformat(), "label": f"{name} 실적", "type": "earnings"})
                    break
        except Exception:
            continue
    return out


def get_macro(days_ahead: int = 14) -> list[dict]:
    today = _today()
    horizon = today + timedelta(days=days_ahead)
    out = []
    for ds, label, confirmed in MACRO_EVENTS:
        dd = datetime.strptime(ds, "%Y-%m-%d").date()
        if today <= dd <= horizon:
            out.append({"date": ds, "label": label + ("" if confirmed else " ~"), "type": "macro"})
    return out


def get_upcoming(days_ahead: int = 14) -> list[dict]:
    ev = get_earnings(days_ahead) + get_macro(days_ahead)
    ev.sort(key=lambda e: e["date"])
    return ev


def report_lines(days_ahead: int = 14) -> list[str]:
    ev = get_upcoming(days_ahead)
    lines = [f"📅 <b>다가오는 카탈리스트 ({days_ahead}일)</b>"]
    if not ev:
        lines.append("   향후 일정 없음")
        return lines
    today = _today()
    icon = {"earnings": "🏢", "macro": "🏛️"}
    for e in ev[:8]:
        dd = datetime.strptime(e["date"], "%Y-%m-%d").date()
        dleft = (dd - today).days
        when = "오늘" if dleft == 0 else f"D-{dleft}"
        lines.append(f"   {icon.get(e['type'],'•')} {e['date']} ({when}) {e['label']}")
    return lines
