"""
history.py
==========
composite score 시계열을 영속화(state/history.json)하고,
모멘텀(velocity)을 계산하며, 추세 차트(PNG)를 생성한다.

천장 판단의 본질은 '점수의 레벨'이 아니라 '점수가 천장으로 향하는 속도'.
-> 7일/30일 delta 와 velocity 를 추적해 '가속 중인지'를 본다.
"""
from __future__ import annotations
import os
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
STATE_DIR = os.path.join(os.path.dirname(__file__), "state")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")


def load_history() -> list[dict]:
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def append_record(result: dict) -> list[dict]:
    """오늘 기록을 추가(같은 날짜면 덮어쓰기). 정렬된 리스트 반환."""
    os.makedirs(STATE_DIR, exist_ok=True)
    hist = load_history()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    rec = {
        "date": today,
        "composite": result["composite"],
        "regime": result["regime"]["name"],
        "decisive_count": result["decisive_count"],
        "structural_crack": result.get("structural_crack", False),
        "categories": {k: v["score"] for k, v in result["categories"].items()},
    }
    hist = [h for h in hist if h.get("date") != today]
    hist.append(rec)
    hist.sort(key=lambda h: h["date"])
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    return hist


def compute_momentum(hist: list[dict]) -> dict:
    """최근 대비 7일/30일 delta 및 velocity(점/주) 산출."""
    if not hist:
        return {}
    latest = hist[-1]["composite"]

    def _nearest(days_back: int):
        target = datetime.strptime(hist[-1]["date"], "%Y-%m-%d") - timedelta(days=days_back)
        best, bestdiff = None, 1e9
        for h in hist[:-1]:
            d = datetime.strptime(h["date"], "%Y-%m-%d")
            diff = abs((d - target).days)
            if diff < bestdiff:
                best, bestdiff = h, diff
        # 7일 윈도우 안의 실제 데이터만 인정
        return best if best and bestdiff <= max(3, days_back // 2) else None

    out = {"latest": latest, "n_points": len(hist)}
    p7 = _nearest(7)
    p30 = _nearest(30)
    if p7:
        out["delta_7d"] = round(latest - p7["composite"], 1)
    if p30:
        out["delta_30d"] = round(latest - p30["composite"], 1)
    # velocity: 직전 기록 대비 일변화 → 주 환산
    if len(hist) >= 2:
        prev = hist[-2]
        days = max(1, (datetime.strptime(hist[-1]["date"], "%Y-%m-%d")
                       - datetime.strptime(prev["date"], "%Y-%m-%d")).days)
        out["velocity_per_week"] = round((latest - prev["composite"]) / days * 7, 1)
    return out


def momentum_label(mom: dict) -> str:
    v = mom.get("velocity_per_week")
    d7 = mom.get("delta_7d")
    ref = v if v is not None else d7
    if ref is None:
        return "📊 추세 누적 중 (데이터 부족)"
    if ref >= 5:
        return f"🚀 천장 향해 가속 (+{ref}/주)"
    if ref >= 1.5:
        return f"↗️ 상승 (+{ref}/주)"
    if ref > -1.5:
        return f"➡️ 횡보 ({ref:+}/주)"
    if ref > -5:
        return f"↘️ 둔화 ({ref}/주)"
    return f"📉 급랭 ({ref}/주)"


def make_trend_chart(hist: list[dict], path: str = "/tmp/asm_trend.png") -> str | None:
    """composite 추세 차트(regime 밴드 포함) 생성. 점이 1개여도 그린다."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as dt
    except Exception as e:
        print(f"[warn] matplotlib unavailable: {e}")
        return None

    if not hist:
        return None

    dates = [dt.strptime(h["date"], "%Y-%m-%d") for h in hist]
    comp = [h["composite"] for h in hist]

    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")

    # regime 밴드
    bands = [
        (0, 20, "#1b3a2a"), (20, 40, "#234d35"),
        (40, 60, "#5c4a1a"), (60, 80, "#6e3d1a"), (80, 100, "#6e1f1f"),
    ]
    for lo, hi, c in bands:
        ax.axhspan(lo, hi, color=c, alpha=0.45, zorder=0)

    labels = [(10, "ACCUMULATE"), (30, "UPTREND"), (50, "LATE"), (70, "EUPHORIA"), (90, "TOP")]
    for y, t in labels:
        ax.text(0.995, y, t, transform=ax.get_yaxis_transform(),
                ha="right", va="center", fontsize=7, color="#aaaaaa", alpha=0.8)

    # 추세선
    ax.plot(dates, comp, color="#4ea1ff", lw=2.2, marker="o", ms=5,
            mfc="#ffffff", mec="#4ea1ff", zorder=3, label="Composite")

    # KOSPI 누적 오버레이 (보조축) - 검증용
    try:
        import yfinance as yf
        ks = yf.Ticker("^KS11").history(period="3mo", auto_adjust=True)["Close"]
        ks.index = ks.index.tz_localize(None).normalize()
        ks = ks[ks.index >= dates[0]] if len(dates) > 1 else ks.tail(20)
        if len(ks) >= 2:
            ks_cum = ks / ks.iloc[0] * 100.0
            ax_k = ax.twinx()
            ax_k.plot(ks_cum.index, ks_cum.values, color="#f4a340", lw=1.6,
                      alpha=0.9, zorder=2, label="KOSPI (cum, start=100)")
            ax_k.set_ylabel("KOSPI cumulative", color="#f4a340", fontsize=8)
            ax_k.tick_params(axis="y", colors="#f4a340", labelsize=7)
            for s in ax_k.spines.values():
                s.set_color("#333333")
            lk, lbk = ax_k.get_legend_handles_labels()
            lc, lbc = ax.get_legend_handles_labels()
            ax.legend(lc + lk, lbc + lbk, loc="upper left", fontsize=7,
                      facecolor="#1a1d26", edgecolor="#333333", labelcolor="white")
    except Exception as e:
        print(f"[warn] KOSPI overlay skipped: {e}")

    # 최신 포인트 강조
    last_c = comp[-1]
    last_color = "#ff4d4d" if last_c >= 60 else ("#ffcc44" if last_c >= 40 else "#4dff88")
    ax.scatter([dates[-1]], [last_c], s=140, color=last_color, zorder=4, edgecolor="white", lw=1.2)
    ax.annotate(f"{last_c}", (dates[-1], last_c), textcoords="offset points",
                xytext=(8, 8), fontsize=11, fontweight="bold", color="white")

    ax.set_ylim(0, 100)
    ax.set_title("AI Semi Cycle-Top  ·  Composite Trend", color="white", fontsize=13, fontweight="bold", pad=12)
    ax.set_ylabel("Composite TOP Score", color="#cccccc", fontsize=9)
    ax.tick_params(colors="#aaaaaa", labelsize=8)
    for s in ax.spines.values():
        s.set_color("#333333")
    if len(dates) > 1:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        fig.autofmt_xdate(rotation=0, ha="center")
    else:
        ax.set_xticks(dates)
        ax.set_xticklabels([d.strftime("%m/%d") for d in dates])

    ax.grid(True, axis="y", color="#2a2a2a", lw=0.5, alpha=0.5)
    plt.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path
