"""
fwd_pe_chart.py
===============
6종 반도체 리더의 forward P/E 차트를 생성한다.

제약: forward P/E 의 과거 시계열은 무료로 구할 수 없다(과거 forward EPS 추정치 부재).
따라서 forward-validation 과 동일한 철학으로 매 실행 forward P/E 를 박제(state)하고,
시간이 지나며 추세를 누적한다.
  - 누적 날짜 < 4: 현재값 '바 차트'(스냅샷) + "추세 누적 중" 표기
  - 누적 날짜 >= 4: forward P/E '추세 라인'(로그축, P/E 스프레드 큼)

종목/색상은 memory_chart.SERIES 와 동기화.
"""
from __future__ import annotations
import os
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
STATE_DIR = os.path.join(os.path.dirname(__file__), "state")
HISTORY_PATH = os.path.join(STATE_DIR, "fwd_pe_history.json")
MIN_TREND_POINTS = 4

try:
    from memory_chart import SERIES
except Exception:
    SERIES = {
        "Samsung Elec (005930)": ("005930.KS", "#4ea1ff"),
        "SK Hynix (000660)":     ("000660.KS", "#f4a340"),
        "Micron (MU)":           ("MU",        "#22c55e"),
        "SanDisk (SNDK)":        ("SNDK",      "#ef4444"),
        "NVIDIA (NVDA)":         ("NVDA",      "#a855f7"),
        "Intel (INTC)":          ("INTC",      "#facc15"),
    }


def _fetch_fwd_pe() -> dict:
    """종목별 현재 forward P/E. 실패/결측은 skip."""
    import yfinance as yf
    out = {}
    for name, (tk, _) in SERIES.items():
        try:
            info = yf.Ticker(tk).info
            fpe = info.get("forwardPE")
            if fpe is None:
                fpe = info.get("trailingPE")
            if fpe is not None and 0 < float(fpe) < 1000:
                out[name] = round(float(fpe), 2)
        except Exception:
            continue
    return out


def _append_history(values: dict) -> list[dict]:
    os.makedirs(STATE_DIR, exist_ok=True)
    hist = []
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    today = datetime.now(KST).strftime("%Y-%m-%d")
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, "values": values})
    hist.sort(key=lambda h: h["date"])
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    return hist


def make_fwd_pe_chart(path: str = "/tmp/fwd_pe.png") -> tuple[str | None, dict]:
    """forward P/E 차트 생성. (path, {name: fwd_pe}) 반환."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as dt
    except Exception as e:
        print(f"[warn] fwd_pe chart deps unavailable: {e}")
        return None, {}

    current = _fetch_fwd_pe()
    if not current:
        return None, {}
    hist = _append_history(current)
    n_dates = len(hist)

    fig, ax = plt.subplots(figsize=(11, 6), dpi=140)
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")
    year = datetime.now(KST).year

    if n_dates >= MIN_TREND_POINTS:
        # 추세 라인 (로그축)
        for name, (tk, col) in SERIES.items():
            xs, ys = [], []
            for h in hist:
                v = h["values"].get(name)
                if v is not None:
                    xs.append(dt.strptime(h["date"], "%Y-%m-%d"))
                    ys.append(v)
            if len(xs) >= 2:
                ax.plot(xs, ys, color=col, lw=2.2, marker="o", ms=3,
                        label=f"{name}  {ys[-1]:.1f}x")
                ax.annotate(f"{ys[-1]:.1f}", (xs[-1], ys[-1]), textcoords="offset points",
                            xytext=(6, 0), fontsize=8, fontweight="bold", color=col, va="center")
        ax.set_yscale("log")
        ax.set_ylabel("Forward P/E (log)", color="#cccccc", fontsize=10)
        ax.set_title(f"Semiconductor Leaders  ·  Forward P/E Trend",
                     color="white", fontsize=14, fontweight="bold", pad=12)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.legend(loc="upper left", fontsize=8.5, facecolor="#1a1d26",
                  edgecolor="#333333", labelcolor="white", ncol=2)
    else:
        # 현재값 바 (스냅샷)
        items = sorted(current.items(), key=lambda x: x[1], reverse=True)
        names = [k for k, _ in items]
        vals = [v for _, v in items]
        cols = [SERIES[k][1] for k in names]
        short = [k.split(" (")[0] for k in names]
        bars = ax.barh(range(len(names)), vals, color=cols, alpha=0.9)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(short, color="#dddddd", fontsize=10)
        ax.invert_yaxis()
        for i, v in enumerate(vals):
            ax.text(v, i, f"  {v:.1f}x", va="center", fontsize=10, fontweight="bold", color="#ffffff")
        ax.set_xlabel("Forward P/E", color="#cccccc", fontsize=10)
        ax.set_title(f"Semiconductor Leaders  ·  Forward P/E (current, trend accumulating d{n_dates})",
                     color="white", fontsize=13, fontweight="bold", pad=12)

    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for s in ax.spines.values():
        s.set_color("#333333")
    ax.grid(True, axis="x" if n_dates < MIN_TREND_POINTS else "y",
            color="#2a2a2a", lw=0.5, alpha=0.5)
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path, current
