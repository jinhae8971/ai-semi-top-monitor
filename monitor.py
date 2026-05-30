"""
monitor.py
==========
AI Semi Cycle-Top Monitor 메인 엔트리포인트.

흐름:
  1) 시장 데이터(yfinance) + 펀더멘털(json) 수집
  2) 6-카테고리 스코어링 -> composite 0~100 + 5-tier regime + 결정적 트리거
  3) Telegram HTML 리포트 발송 (선택: ANTHROPIC_API_KEY 있으면 Opus 내러티브)

환경변수(=GitHub Secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   (필수)
  ANTHROPIC_API_KEY                   (선택 - 있으면 AI 코멘트 추가)
"""
from __future__ import annotations
import os
import sys
import json
from datetime import datetime, timezone, timedelta

import requests

from data_sources import fetch_market_data, load_fundamentals
from scoring import compute
from history import (append_record, compute_momentum, momentum_label,
                     make_trend_chart)
from auto_fundamentals import apply_auto
import forward_validation as fv
from memory_chart import make_memory_ytd_chart
from fwd_pe_chart import make_fwd_pe_chart
import breadth as breadth_mod
import catalysts
import signal_journal

KST = timezone(timedelta(hours=9))

# regime -> hub 색상 (cycle-intelligence-hub 스타일)
REGIME_COLOR = {
    "ACCUMULATE": "#22c55e", "UPTREND": "#84cc16", "LATE_CYCLE": "#eab308",
    "EUPHORIA": "#f97316", "TOP_ZONE": "#ef4444",
}


# ----------------------------------------------------------------------------
# 설정 로드 (env -> config.json 폴백)
# ----------------------------------------------------------------------------
def load_config() -> dict:
    cfg = {
        "telegram_token":    os.environ.get("TELEGRAM_TOKEN", ""),
        "telegram_chat_id":  os.environ.get("TELEGRAM_CHAT_ID", ""),
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    }
    path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if not cfg.get(k):
                    cfg[k] = v
    return cfg


# ----------------------------------------------------------------------------
# 선택적 Opus 내러티브
# ----------------------------------------------------------------------------
def ai_narrative(result: dict, api_key: str) -> str | None:
    if not api_key:
        return None
    try:
        prompt = (
            "너는 반도체 사이클 전문 애널리스트다. 아래 AI 반도체 사이클-탑 모니터 "
            "스코어를 보고, 한국어로 3문장 이내의 핵심 코멘트만 작성하라. "
            "과장 없이, 가장 중요한 신호 1~2개와 다음에 봐야 할 트리거를 짚어라.\n\n"
            f"{json.dumps(result, ensure_ascii=False, indent=2)}"
        )
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-20250514",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=40,
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    except Exception as e:
        print(f"[warn] AI narrative skipped: {e}")
        return None


# ----------------------------------------------------------------------------
# Telegram 리포트 빌드
# ----------------------------------------------------------------------------
def build_report(result: dict, narrative: str | None, momentum: dict | None = None) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    reg = result["regime"]
    comp = result["composite"]

    # 점수 게이지
    filled = int(round(comp / 10))
    gauge = "█" * filled + "░" * (10 - filled)

    lines = []
    lines.append("🔬 <b>AI Semi Cycle-Top Monitor</b>")
    lines.append(f"<i>{now}</i>")
    lines.append("")
    lines.append(f"{reg['emoji']} <b>Regime: {reg['name']}</b>")
    lines.append(f"<b>Composite TOP Score: {comp}/100</b>")
    lines.append(f"<code>{gauge}</code>")
    lines.append(f"<i>{reg['desc']}</i>")

    # 모멘텀(추세)
    if momentum:
        lines.append("")
        lines.append(f"📐 <b>모멘텀:</b> {momentum_label(momentum)}")
        bits = []
        if momentum.get("delta_7d") is not None:
            bits.append(f"7일 {momentum['delta_7d']:+}")
        if momentum.get("delta_30d") is not None:
            bits.append(f"30일 {momentum['delta_30d']:+}")
        if bits:
            lines.append(f"   <i>{' · '.join(bits)} (n={momentum.get('n_points')})</i>")

    if result.get("escalated"):
        lines.append("")
        lines.append(f"⚠️ <b>결정적 트리거 {result['decisive_count']}개 발화 → regime 격상</b>")
        lines.append(f"   (기준: {result['base_regime']['name']})")

    # 카테고리 분해
    lines.append("")
    lines.append("📊 <b>카테고리 분해</b>")
    cats = result["categories"]
    order = ["capex", "supply_demand", "financing", "valuation", "sentiment", "technical"]
    for k in order:
        c = cats[k]
        sc = c["score"]
        light = "🔴" if sc >= 70 else ("🟡" if sc >= 45 else "🟢")
        w = int(c["weight"] * 100)
        lines.append(f"{light} {c['label']} <b>{sc}</b> <i>({w}%)</i>")

    # 결정적 트리거 상태
    lines.append("")
    lines.append("🎯 <b>결정적 트리거 (선행)</b>")
    dt = result["decisive_triggers"]
    tmap = {
        "capex_decel": "Capex 성장률 감속",
        "supply_peak_out": "수급 ASP peak-out + B/B&lt;1.0",
        "eps_revision_down": "EPS revision 하향 전환",
    }
    for key, label in tmap.items():
        mark = "🔴 ON" if dt.get(key) else "⚪ off"
        lines.append(f"  {mark} — {label}")
    crack = "🔴 ON" if result.get("structural_crack") else "⚪ off"
    lines.append(f"  {crack} — 순환출자 구조 균열 <i>(구조)</i>")

    # 주요 수치
    lines.append("")
    lines.append("📈 <b>핵심 수치</b>")
    cap = cats["capex"]["detail"]
    sd = cats["supply_demand"]["detail"]
    sen = cats["sentiment"]["detail"]
    if cap.get("latest_yoy_growth_pct") is not None:
        lines.append(f"  • Capex YoY: {cap['latest_yoy_growth_pct']}% (Δ{cap['capex_growth_2nd_deriv_pp']:+}%p)")
    if sd.get("latest_asp_qoq_pct") is not None:
        lines.append(f"  • 메모리 ASP QoQ: {sd['latest_asp_qoq_pct']}% (Δ{sd['asp_qoq_2nd_deriv_pp']:+}%p)")
    if sd.get("book_to_bill") is not None:
        lines.append(f"  • Book-to-Bill: {sd['book_to_bill']}")
    if sen.get("dist_above_200dma_pct") is not None:
        lines.append(f"  • SOX 200일선 이격: {sen['dist_above_200dma_pct']}%")
    if sen.get("win_days_in_23") is not None:
        lines.append(f"  • 최근 23일 상승: {sen['win_days_in_23']}일")

    if narrative:
        lines.append("")
        lines.append("🧠 <b>AI 코멘트</b>")
        lines.append(narrative)

    if result.get("data_gaps"):
        lines.append("")
        lines.append(f"⚙️ <i>data gap: {', '.join(result['data_gaps'])}</i>")

    lines.append("")
    lines.append("<i>본 리포트는 정보 제공용이며 투자 권유가 아닙니다.</i>")
    return "\n".join(lines)


def send_telegram(text: str, token: str, chat_id: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }, timeout=25)
    r.raise_for_status()


def send_telegram_photo(photo_path: str, caption: str, token: str, chat_id: str):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(photo_path, "rb") as ph:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ph}, timeout=40,
        )
    r.raise_for_status()


# ----------------------------------------------------------------------------
def write_latest_json(result: dict, momentum: dict) -> str:
    """cycle-intelligence-hub 가 읽는 data/latest.json 발행 (asts 스키마)."""
    reg = result["regime"]
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_kst": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "asts": {
            "composite": result["composite"],
            "phase": reg["name"],
            "emoji": reg["emoji"],
            "color": REGIME_COLOR.get(reg["name"], "#9ca3af"),
            "dimensions": {k: v["score"] for k, v in result["categories"].items()},
            "decisive_triggers": result["decisive_triggers"],
            "decisive_count": result["decisive_count"],
            "structural_crack": result.get("structural_crack", False),
            "momentum": momentum,
        },
    }
    d = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return path


def main():
    cfg = load_config()
    print("[1/4] fetching market data ...")
    market = fetch_market_data()
    print("[2/4] loading fundamentals (+auto refresh) ...")
    fundamentals = load_fundamentals()
    try:
        applied = apply_auto(fundamentals)
        for n in applied.get("notes", []):
            print("   auto:", n)
    except Exception as e:
        applied = {"notes": [], "stale_warning": False}
        print(f"   [warn] auto_fundamentals skipped: {e}")

    # 브레드스 자동 계산 → technical 카테고리 divergence 주입
    try:
        breadth_data = breadth_mod.compute()
        if breadth_data:
            fundamentals.setdefault("technical", {})["breadth_divergence"] = breadth_data["divergence"]
            print(f"   breadth: 200dma {breadth_data['pct_above_200dma']}% · div {breadth_data['divergence']}")
    except Exception as e:
        breadth_data = {}
        print(f"   [warn] breadth skipped: {e}")

    print("[3/4] scoring ...")
    result = compute(market, fundamentals)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # history 영속화 + 모멘텀 + hub 피드
    hist = append_record(result)
    momentum = compute_momentum(hist)
    chart_path = make_trend_chart(hist)
    latest_path = write_latest_json(result, momentum)
    print(f"   wrote {latest_path}")

    # 전향(out-of-sample) 검증
    try:
        validation = fv.run(hist)
        d = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "validation.json"), "w", encoding="utf-8") as vf:
            json.dump(validation, vf, ensure_ascii=False, indent=2)
        print(f"   validation: {validation.get('status')}")
    except Exception as e:
        validation = None
        print(f"   [warn] forward_validation skipped: {e}")

    narrative = ai_narrative(result, cfg.get("anthropic_api_key", ""))
    report = build_report(result, narrative, momentum)
    if validation:
        report += "\n\n" + "\n".join(fv.report_line(validation))
    if breadth_data:
        report += "\n\n" + "\n".join(breadth_mod.report_lines(breadth_data))
    # 시그널 저널 (이벤트 박제 + 경로 추적)
    try:
        journal, new_events = signal_journal.run(result, breadth_data)
        report += "\n\n" + "\n".join(signal_journal.report_lines(journal, new_events))
    except Exception as e:
        print(f"   [warn] signal_journal skipped: {e}")
    try:
        report += "\n\n" + "\n".join(catalysts.report_lines(14))
    except Exception as e:
        print(f"   [warn] catalysts skipped: {e}")
    if applied.get("stale_warning"):
        report += "\n⏳ <i>수동 펀더멘털 갱신 권장 (45일+ 경과)</i>"

    if not cfg["telegram_token"] or not cfg["telegram_chat_id"]:
        print("[4/4] no telegram creds - dry run only\n")
        print(report)
        print(f"\n[chart] {chart_path}")
        print(f"[momentum] {momentum}")
        return

    print("[4/4] sending telegram ...")
    send_telegram(report, cfg["telegram_token"], cfg["telegram_chat_id"])
    if chart_path:
        cap = f"📈 Composite {result['composite']}/100 · {result['regime']['emoji']} {result['regime']['name']}"
        try:
            send_telegram_photo(chart_path, cap, cfg["telegram_token"], cfg["telegram_chat_id"])
        except Exception as e:
            print(f"[warn] photo send skipped: {e}")
    # 메모리 4강 YTD 차트
    try:
        mem_path, mem_sum = make_memory_ytd_chart()
        if mem_path:
            top = max(mem_sum, key=mem_sum.get) if mem_sum else None
            cap = "🔬 Semi Leaders · YTD"
            if top:
                cap += f" (top: {top.split(' (')[0]} {mem_sum[top]:+.0f}%)"
            send_telegram_photo(mem_path, cap, cfg["telegram_token"], cfg["telegram_chat_id"])
    except Exception as e:
        print(f"[warn] memory chart skipped: {e}")
    # 반도체 리더 forward P/E 차트
    try:
        pe_path, pe_sum = make_fwd_pe_chart()
        if pe_path:
            cheapest = min(pe_sum, key=pe_sum.get) if pe_sum else None
            cap = "🏷️ Semi Leaders · Forward P/E"
            if cheapest:
                cap += f" (lowest: {cheapest.split(' (')[0]} {pe_sum[cheapest]:.1f}x)"
            send_telegram_photo(pe_path, cap, cfg["telegram_token"], cfg["telegram_chat_id"])
    except Exception as e:
        print(f"[warn] fwd_pe chart skipped: {e}")
    print("done.")


if __name__ == "__main__":
    sys.exit(main())
