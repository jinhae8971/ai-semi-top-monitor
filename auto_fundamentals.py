"""
auto_fundamentals.py
====================
fundamentals.json 의 '자동 추출 가능한' 필드를 매 실행마다 갱신한다.
수동 필드(ASP, Book-to-Bill, 순환출자 등 - 비공개/페이월)는 보존한다.

자동화 범위:
  * Capex 사이클  : yfinance 빅4(MSFT/GOOGL/AMZN/META) 분기 cashflow로
                    aggregate capex YoY 성장률 + FCF 상태 산출
  * 신용 스프레드 : FRED HY OAS(BAMLH0A0HYM2) CSV(키 불필요)로 tight/widening 판정
  * 스테일니스    : 수동 필드의 _last_updated 가 오래되면 경고 플래그
"""
from __future__ import annotations
import io
from datetime import datetime, timezone

import requests

BIG4 = ["MSFT", "GOOGL", "AMZN", "META"]
FRED_HY_OAS = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2"


def _quarter_tag(ts) -> str:
    try:
        dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        return f"{dt.year}Q{(dt.month - 1) // 3 + 1}"
    except Exception:
        return "NA"


def fetch_hyperscaler_capex() -> dict | None:
    """빅4 aggregate capex YoY(%) + FCF 상태 + 분기태그."""
    try:
        import yfinance as yf
    except Exception:
        return None

    yoys, latest_fcf, prior_fcf, tag = [], 0.0, 0.0, "NA"
    ok = 0
    for t in BIG4:
        try:
            cf = yf.Ticker(t).quarterly_cashflow
            cap_rows = [r for r in cf.index if "Capital Expenditure" in r]
            ocf_rows = [r for r in cf.index if "Operating Cash Flow" in r]
            if not cap_rows:
                continue
            cap = cf.loc[cap_rows[0]].dropna()
            if len(cap) < 5:
                continue
            c0, c4 = abs(cap.iloc[0]), abs(cap.iloc[4])
            if c4 > 0:
                yoys.append((c0 / c4 - 1.0) * 100.0)
                ok += 1
            if tag == "NA":
                tag = _quarter_tag(cap.index[0])
            # FCF = OCF - |capex|
            if ocf_rows:
                ocf = cf.loc[ocf_rows[0]].dropna()
                if len(ocf) >= 5:
                    latest_fcf += float(ocf.iloc[0]) - c0
                    prior_fcf += float(ocf.iloc[4]) - c4
        except Exception:
            continue

    if ok < 2 or not yoys:
        return None

    agg_yoy = float(sum(yoys) / len(yoys))
    if not (0 < agg_yoy < 200):   # 이상치 가드
        return None

    latest_fcf = float(latest_fcf)
    prior_fcf = float(prior_fcf)
    if latest_fcf < 0:
        fcf_status = "negative"
    elif prior_fcf > 0 and latest_fcf < prior_fcf * 0.6:
        fcf_status = "compressing"
    else:
        fcf_status = "positive" if latest_fcf >= prior_fcf else "compressing"

    return {
        "agg_yoy_pct": round(agg_yoy, 1),
        "fcf_status": fcf_status,
        "quarter_tag": tag,
        "companies_ok": ok,
    }


def fetch_credit_spread() -> dict | None:
    """FRED HY OAS 최근값 + 90영업일 평균. widening/tight 판정."""
    try:
        r = requests.get(FRED_HY_OAS, timeout=20)
        r.raise_for_status()
        rows = [ln.split(",") for ln in r.text.strip().splitlines()[1:]]
        vals = []
        for d, v in rows:
            try:
                vals.append(float(v))
            except ValueError:
                continue
        if len(vals) < 30:
            return None
        latest = vals[-1]
        window = vals[-90:] if len(vals) >= 90 else vals
        avg = sum(window) / len(window)
        recent_low = min(window)
        # 절대 위험권(>4.5%) 또는 최근저점 대비 +35% 급등 시 widening
        widening = latest > 4.5 or latest > recent_low * 1.35
        return {
            "latest_pct": round(latest, 2),
            "avg90_pct": round(avg, 2),
            "state": "widening" if widening else "tight",
        }
    except Exception:
        return None


def _staleness_days(fundamentals: dict) -> int | None:
    ts = fundamentals.get("_last_updated")
    if not ts:
        return None
    try:
        d = datetime.strptime(ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return None


def apply_auto(fundamentals: dict) -> dict:
    """
    fundamentals 를 in-place 로 자동 필드 갱신하고, 적용 내역 dict 반환.
    실패한 소스는 조용히 건너뛰고 수동값 유지.
    """
    applied = {"capex": False, "credit_spread": False, "stale_warning": False, "notes": []}

    # --- Capex ---
    cap = fetch_hyperscaler_capex()
    if cap:
        cnode = fundamentals.setdefault("capex", {})
        hist = cnode.get("yoy_growth_history_pct", [])
        last_tag = cnode.get("_auto_last_quarter")
        if not hist:
            hist = [cap["agg_yoy_pct"]]
        elif last_tag is None:
            hist[-1] = cap["agg_yoy_pct"]          # 시드 말단을 현재분기로 교체
        elif last_tag == cap["quarter_tag"]:
            hist[-1] = cap["agg_yoy_pct"]          # 동일분기 → 갱신
        else:
            hist.append(cap["agg_yoy_pct"])        # 신규분기 → 추가
        cnode["yoy_growth_history_pct"] = hist[-8:]
        cnode["_auto_last_quarter"] = cap["quarter_tag"]
        cnode["fcf_status"] = cap["fcf_status"]
        applied["capex"] = True
        applied["notes"].append(
            f"capex {cap['quarter_tag']} YoY {cap['agg_yoy_pct']}% (n={cap['companies_ok']}), fcf={cap['fcf_status']}"
        )

    # --- 신용 스프레드 ---
    cs = fetch_credit_spread()
    if cs:
        fundamentals.setdefault("financing", {})["dc_credit_spread"] = cs["state"]
        applied["credit_spread"] = True
        applied["notes"].append(f"HY OAS {cs['latest_pct']}% → {cs['state']}")

    # --- 스테일니스 (수동 필드) ---
    days = _staleness_days(fundamentals)
    if days is not None and days > 45:
        applied["stale_warning"] = True
        applied["notes"].append(f"수동 펀더멘털 {days}일 경과 - ASP/B2B/순환출자 갱신 권장")

    return applied
