"""
signal_journal.py
=================
'결정적 신호가 켜지는 순간'을 날짜·가격과 함께 영구 박제(append-only)하고,
이후 KOSPI/SOX 경로를 추적해 forward validation 을 실제 매매 track record 로 발전시킨다.

기록 대상 이벤트(직전 상태 대비 전환):
  - 결정적 트리거 off→on (capex_decel / supply_peak_out / eps_revision_down)
  - 순환출자 구조 균열 off→on
  - 브레드스 다이버전스 off→on
  - regime 변화 (LATE_CYCLE→EUPHORIA→TOP_ZONE 등)

각 엔트리는 발생 시점 KOSPI/SOX 종가를 박제 → 이후 매 실행마다 실현 경로(%)를 갱신.
TOP 신호가 유효하면 신호 이후 KOSPI/SOX 가 하락(음수 경로)해야 한다.
"""
from __future__ import annotations
import os
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
STATE_DIR = os.path.join(os.path.dirname(__file__), "state")
JOURNAL_PATH = os.path.join(STATE_DIR, "signal_journal.json")
LASTSTATE_PATH = os.path.join(STATE_DIR, "last_state.json")


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save(path, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _fetch_closes():
    """현재 KOSPI/SOX 종가 + 시계열(경로추적용)."""
    try:
        import yfinance as yf
        out = {}
        for key, tk in {"kospi": "^KS11", "sox": "SOXX"}.items():
            s = yf.Ticker(tk).history(period="1y", auto_adjust=True)["Close"].dropna()
            s.index = s.index.tz_localize(None).normalize()
            out[key] = s
        return out
    except Exception:
        return {}


def _ret_since(series, date_str, now_val):
    """date_str 시점 종가 대비 현재 수익률(%). 실패 시 None."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        import numpy as np
        dates = np.array([x.date() for x in series.index])
        le = np.where(dates <= d)[0]
        if not len(le):
            return None
        base = float(series.iloc[le[-1]])
        return round((now_val / base - 1.0) * 100.0, 2)
    except Exception:
        return None


def run(result: dict, breadth_data: dict) -> tuple[list, list]:
    """이벤트 감지·기록 + 전체 저널 경로 갱신. (journal, new_events) 반환."""
    journal = _load(JOURNAL_PATH, [])
    last = _load(LASTSTATE_PATH, None)

    cur = {
        "triggers": dict(result.get("decisive_triggers", {})),
        "structural_crack": bool(result.get("structural_crack", False)),
        "regime": result["regime"]["name"],
        "breadth_divergence": bool(breadth_data.get("divergence")) if breadth_data else False,
    }

    new_events = []
    if last is not None:                      # 베이스라인 이후에만 이벤트 감지
        for k, v in cur["triggers"].items():
            if v and not last.get("triggers", {}).get(k, False):
                new_events.append(("TRIGGER_ON", k))
        if cur["structural_crack"] and not last.get("structural_crack", False):
            new_events.append(("STRUCTURAL_CRACK", "순환출자 균열"))
        if cur["breadth_divergence"] and not last.get("breadth_divergence", False):
            new_events.append(("BREADTH_DIVERGENCE", "브레드스 다이버전스"))
        if cur["regime"] != last.get("regime"):
            new_events.append(("REGIME_CHANGE", f"{last.get('regime')}→{cur['regime']}"))

    closes = _fetch_closes()
    kospi_now = float(closes["kospi"].iloc[-1]) if closes.get("kospi") is not None else None
    sox_now = float(closes["sox"].iloc[-1]) if closes.get("sox") is not None else None
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 새 이벤트 기록 (append-only)
    for etype, detail in new_events:
        journal.append({
            "id": f"{today}-{etype}-{detail}"[:80],
            "date": today,
            "type": etype,
            "detail": detail,
            "composite": result["composite"],
            "regime": result["regime"]["name"],
            "kospi_at": round(kospi_now, 1) if kospi_now else None,
            "sox_at": round(sox_now, 2) if sox_now else None,
        })
    if new_events:
        _save(JOURNAL_PATH, journal)

    # 전체 저널 경로 갱신 (실현 수익률)
    if closes.get("kospi") is not None:
        for e in journal:
            e["kospi_path_pct"] = _ret_since(closes["kospi"], e["date"], kospi_now) if kospi_now else None
            if closes.get("sox") is not None and sox_now:
                e["sox_path_pct"] = _ret_since(closes["sox"], e["date"], sox_now)
            d0 = datetime.strptime(e["date"], "%Y-%m-%d").date()
            e["days_elapsed"] = (datetime.now(KST).date() - d0).days

    _save(LASTSTATE_PATH, cur)
    return journal, new_events


def report_lines(journal: list, new_events: list) -> list[str]:
    lines = ["📓 <b>Signal Journal</b>"]
    if new_events:
        lines.append("🚨 <b>신규 신호 발생!</b>")
        for etype, detail in new_events:
            lines.append(f"   • {etype}: {detail}")
    if not journal:
        lines.append("   기록 없음 (트리거/regime 전환 시 자동 박제)")
        return lines
    # 최근 5건 경로
    for e in journal[-5:]:
        kp = e.get("kospi_path_pct")
        kp_s = f"KOSPI {kp:+}%" if kp is not None else "KOSPI -"
        de = e.get("days_elapsed", 0)
        lines.append(f"   {e['date']} {e['type']}({e['detail']}) D+{de} → {kp_s}")
    return lines
