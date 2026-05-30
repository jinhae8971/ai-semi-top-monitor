"""
memory_chart.py
===============
반도체 리더 종목들의 연초대비(YTD) 수익률 트렌드 차트를 생성한다.
(삼성전자·SK하이닉스·Micron·SanDisk·NVIDIA·Intel)
연초 기준은 실행 연도 기준으로 자동 계산.

monitor 가 매 실행마다 호출 → 텔레그램으로 추세차트와 함께 발송.
종목 추가/교체는 아래 SERIES dict 만 수정.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

SERIES = {
    "Samsung Elec (005930)": ("005930.KS", "#4ea1ff"),
    "SK Hynix (000660)":     ("000660.KS", "#f4a340"),
    "Micron (MU)":           ("MU",        "#22c55e"),
    "SanDisk (SNDK)":        ("SNDK",      "#ef4444"),
    "NVIDIA (NVDA)":         ("NVDA",      "#a855f7"),
    "Intel (INTC)":          ("INTC",      "#facc15"),
}


def make_memory_ytd_chart(path: str = "/tmp/memory_ytd.png") -> tuple[str | None, dict]:
    """4종 YTD 수익률 차트 생성. (path, {name: ytd_pct}) 반환. 실패 종목은 skip."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import yfinance as yf
    except Exception as e:
        print(f"[warn] memory chart deps unavailable: {e}")
        return None, {}

    year = datetime.now(KST).year
    start = f"{year}-01-01"

    fig, ax = plt.subplots(figsize=(11, 6), dpi=140)
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")
    ax.axhline(0, color="#666", lw=0.8, ls="--", alpha=0.7)

    summary, last, drawn = {}, [], 0
    for name, (tk, col) in SERIES.items():
        try:
            c = yf.Ticker(tk).history(start=start, auto_adjust=True)["Close"].dropna()
            if len(c) < 2:
                continue
            c.index = c.index.tz_localize(None).normalize()
            ytd = (c / c.iloc[0] - 1.0) * 100.0
            ax.plot(ytd.index, ytd.values, color=col, lw=2.2,
                    label=f"{name}  {ytd.iloc[-1]:+.0f}%")
            last.append((ytd.index[-1], float(ytd.iloc[-1]), col, f"{ytd.iloc[-1]:+.0f}%"))
            summary[name] = round(float(ytd.iloc[-1]), 1)
            drawn += 1
        except Exception:
            continue

    if drawn == 0:
        plt.close(fig)
        return None, {}

    for x, y, col, t in last:
        ax.annotate(t, (x, y), textcoords="offset points", xytext=(6, 0),
                    fontsize=9, fontweight="bold", color=col, va="center")

    ax.set_title(f"Semiconductor Leaders  ·  YTD {year} Return (Year-to-Date)",
                 color="white", fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("YTD Return (%)", color="#cccccc", fontsize=10)
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    for s in ax.spines.values():
        s.set_color("#333333")
    ax.grid(True, axis="y", color="#2a2a2a", lw=0.5, alpha=0.5)
    ax.legend(loc="upper left", fontsize=9.5, facecolor="#1a1d26",
              edgecolor="#333333", labelcolor="white")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path, summary
