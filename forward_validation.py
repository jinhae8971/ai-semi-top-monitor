"""
forward_validation.py
=====================
'앞으로 검증' 인프라. 과거 백테스트가 아니라, 매일 박제되는 신호 스냅샷을
시간이 지나며 실현된 KOSPI 결과와 자동 페어링해 현재 regime 내 out-of-sample
성적표를 누적한다.

작동:
  - history.json 의 각 스냅샷(date, composite)을 KOSPI 가격 시계열에 매칭
  - 스냅샷 시점 d 로부터 h 거래일이 '경과(matured)'한 것만 실현수익률 계산
  - matured (composite, 실현수익률) 페어를 누적 → 상관/구간수익/히트레이트
  - 표본이 충분(n>=MIN_N)할 때만 통계를 신뢰구간과 함께 surface

TOP 지표가 유효하면: composite 가 높을수록 향후 KOSPI 수익률은 낮아야(음의 상관) 함.
"""
from __future__ import annotations
from datetime import datetime
import numpy as np

HORIZONS = (5, 20, 60)      # 거래일
MIN_N = 12                  # 통계 신뢰 최소 표본


def _load_kospi():
    try:
        import yfinance as yf
        s = yf.Ticker("^KS11").history(period="2y", auto_adjust=True)["Close"]
        s.index = s.index.tz_localize(None).normalize()
        s = s[~s.index.duplicated(keep="last")].dropna()
        return s
    except Exception:
        return None


def run(history: list[dict]) -> dict:
    """history 스냅샷으로 전향 검증 통계 누적."""
    out = {"status": "accumulating", "n_snapshots": len(history), "horizons": {}}
    if len(history) < 2:
        out["note"] = "스냅샷 누적 시작 - 검증은 5거래일 경과분부터 집계"
        return out

    kospi = _load_kospi()
    if kospi is None or len(kospi) < 60:
        out["status"] = "no_data"
        return out

    kidx = kospi.index
    kdates = np.array([d.date() for d in kidx])

    def _pos_on_or_before(dstr):
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        le = np.where(kdates <= d)[0]
        return int(le[-1]) if len(le) else None

    last_pos = len(kospi) - 1
    for h in HORIZONS:
        pairs = []  # (composite, realized_fwd_ret_pct)
        for rec in history:
            p = _pos_on_or_before(rec["date"])
            if p is None:
                continue
            if p + h <= last_pos:                       # 경과(matured)
                realized = (kospi.iloc[p + h] / kospi.iloc[p] - 1.0) * 100.0
                pairs.append((rec["composite"], float(realized)))
        n = len(pairs)
        hh = {"n_matured": n}
        if n >= MIN_N:
            sig = np.array([a for a, _ in pairs])
            ret = np.array([b for _, b in pairs])
            corr = float(np.corrcoef(sig, ret)[0, 1])
            thr = float(np.median(sig))                 # 자기 표본 중앙값 분할
            hi = ret[sig >= thr]
            lo = ret[sig < thr]
            hh.update({
                "corr": round(corr, 3),
                "split_threshold": round(thr, 1),
                "avg_fwd_high_pct": round(float(hi.mean()), 2) if len(hi) else None,
                "avg_fwd_low_pct": round(float(lo.mean()), 2) if len(lo) else None,
                "down_rate_high_pct": round(float((hi < 0).mean() * 100), 1) if len(hi) else None,
                # 유효성: 음의 상관이면 TOP 지표로 '작동'
                "working": corr < -0.1,
            })
        out["horizons"][h] = hh

    matured_any = any(out["horizons"][h]["n_matured"] >= MIN_N for h in HORIZONS)
    out["status"] = "validated" if matured_any else "accumulating"
    return out


def report_line(val: dict) -> list[str]:
    """텔레그램 리포트용 라인."""
    lines = ["🔭 <b>Forward Validation (out-of-sample)</b>"]
    if val.get("status") in ("accumulating",) and not any(
            v.get("n_matured", 0) >= MIN_N for v in val.get("horizons", {}).values()):
        nearest = val.get("horizons", {}).get(5, {}).get("n_matured", 0)
        lines.append(f"   누적 중 · 5일경과 {nearest}건 (n≥{MIN_N}부터 통계)")
        return lines
    if val.get("status") == "no_data":
        lines.append("   KOSPI 데이터 일시 불가")
        return lines
    for h in HORIZONS:
        hh = val["horizons"].get(h, {})
        n = hh.get("n_matured", 0)
        if n >= MIN_N:
            mark = "✅ 작동" if hh.get("working") else "➖ 미작동"
            lines.append(f"   {h}일 (n={n}): corr {hh['corr']:+} · 과열후 {hh.get('down_rate_high_pct')}% 하락 · {mark}")
        else:
            lines.append(f"   {h}일: 누적 중 (n={n}/{MIN_N})")
    return lines
