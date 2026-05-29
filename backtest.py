"""
backtest.py
===========
composite 의 '시장 신호 부분'(밸류+센티+기술, 가격기반·단기성)을 2년치로 역산해
KOSPI 누적과 대조하고, 단기(5/20/60거래일) 예측력을 검증한다.

검증 논리:
  지표가 '천장 신호'로 유효하다면, 시장신호가 높을수록(과열)
  KOSPI 의 향후 수익률은 낮아야(음의 상관) 한다.
  - 펀더멘털 3종(capex/수급/파이낸싱)은 분기 데이터라 단기엔 불변 → 시장 3종으로 단기 검증.

산출:
  - 듀얼축 차트(시장신호 vs KOSPI 누적)  -> /tmp/asm_backtest.png
  - 상관계수 / 구간별 향후수익률 / 히트레이트 통계
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd


def _clamp(s):
    return s.clip(0, 100)


def _percentile_rank(series: pd.Series, window: int = 252) -> pd.Series:
    return series.rolling(window).apply(lambda x: (x <= x[-1]).mean() * 100.0, raw=True)


def load_panel(period: str = "2y") -> pd.DataFrame:
    import yfinance as yf
    cols = {}
    for name, tk in {"sox": "SOXX", "nvda": "NVDA", "vix": "^VIX", "kospi": "^KS11"}.items():
        df = yf.Ticker(tk).history(period=period, auto_adjust=True)
        s = df["Close"].copy()
        # tz 제거 + 날짜 정규화 (KOSPI=KST vs US=ET 인덱스 정렬)
        s.index = s.index.tz_localize(None).normalize()
        s = s[~s.index.duplicated(keep="last")]
        cols[name] = s
    panel = pd.DataFrame(cols).sort_index()
    # 미국/한국 거래일 차이 → ffill 후 dropna (소규모 휴장 보정)
    panel = panel.ffill(limit=2).dropna()
    return panel


def build_market_signal(panel: pd.DataFrame) -> pd.DataFrame:
    """indicators.py 와 동일한 임계값으로 밸류/센티/기술 시장신호 역산."""
    sox = panel["sox"]
    nvda = panel["nvda"]
    vix = panel["vix"]

    ma200 = sox.rolling(200).mean()
    dist200 = (sox / ma200 - 1.0) * 100.0
    ret = sox.diff()
    win23 = (ret > 0).rolling(23).sum()
    wk = (sox / sox.shift(5) - 1.0) * 100.0
    px_pct = _percentile_rank(sox, 252)
    nvda_pct = _percentile_rank(nvda, 252)

    # RSI14 (simple mean, indicators.py 와 동일)
    delta = sox.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(100)

    # --- 밸류에이션 (fwd_pe 수동값 미존재 → 가격 percentile) ---
    valuation = (px_pct + nvda_pct * 0.9) / 2.0

    # --- 센티먼트 (smart_money_short=neutral40, etf_flow=neutral50) ---
    s_streak = _clamp((win23 - 12) / (22 - 12) * 100).where(win23 >= 12, 10)
    s_wk = _clamp(wk / 10.0 * 100)
    s_dist = _clamp(dist200 / 30.0 * 100)
    s_vix = _clamp((20 - vix) / (20 - 12) * 100).where(vix <= 20, 20)
    sentiment = (0.22 * s_streak + 0.20 * s_wk + 0.18 * s_dist +
                 0.12 * s_vix + 0.14 * 40 + 0.14 * 50)

    # --- 기술적 (breadth_divergence=neutral45) ---
    s_rsi = _clamp((rsi - 50) / (80 - 50) * 100).where(rsi >= 50, 20)
    technical = 0.40 * s_rsi + 0.30 * s_dist + 0.30 * 45

    # 시장신호 = (10*val + 10*sent + 5*tech)/25  → 0~100 정규화
    market_signal = (10 * valuation + 10 * sentiment + 5 * technical) / 25.0

    out = pd.DataFrame({
        "kospi": panel["kospi"],
        "market_signal": market_signal,
        "valuation": valuation,
        "sentiment": sentiment,
        "technical": technical,
    }).dropna()
    out["kospi_cum"] = out["kospi"] / out["kospi"].iloc[0] * 100.0
    return out


def validate(df: pd.DataFrame, horizons=(5, 20, 60)) -> dict:
    """단기 예측력 통계: 상관 / 구간별 향후수익 / 히트레이트."""
    res = {"n_days": len(df), "horizons": {}}
    sig = df["market_signal"]
    kospi = df["kospi"]
    hi_thr = float(sig.quantile(0.75))   # 상위 25% = 과열 구간
    res["high_threshold_p75"] = round(hi_thr, 1)

    for h in horizons:
        fwd = kospi.shift(-h) / kospi - 1.0   # h거래일 향후 수익률
        valid = (~fwd.isna())
        s, f = sig[valid], fwd[valid] * 100.0
        corr = float(np.corrcoef(s, f)[0, 1]) if len(s) > 10 else np.nan

        hi = f[s >= hi_thr]
        lo = f[s < hi_thr]
        # 히트레이트: 과열구간 진입 후 하락(음수)한 비율
        hit = float((hi < 0).mean() * 100) if len(hi) else np.nan
        res["horizons"][h] = {
            "corr_signal_vs_fwdret": round(corr, 3),
            "avg_fwd_ret_high_pct": round(float(hi.mean()), 2) if len(hi) else None,
            "avg_fwd_ret_low_pct": round(float(lo.mean()), 2) if len(lo) else None,
            "avg_fwd_ret_all_pct": round(float(f.mean()), 2),
            "down_hit_rate_high_pct": round(hit, 1) if not np.isnan(hit) else None,
            "n_high_days": int(len(hi)),
        }
    return res


def make_chart(df: pd.DataFrame, path: str = "/tmp/asm_backtest.png") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax1 = plt.subplots(figsize=(11, 5.5), dpi=130)
    fig.patch.set_facecolor("#0f1117")
    ax1.set_facecolor("#0f1117")

    for lo, hi, c in [(0, 40, "#1f3a2a"), (40, 60, "#5c4a1a"), (60, 80, "#6e3d1a"), (80, 100, "#6e1f1f")]:
        ax1.axhspan(lo, hi, color=c, alpha=0.35, zorder=0)

    ax1.plot(df.index, df["market_signal"], color="#4ea1ff", lw=1.8, label="Market Signal (val+sent+tech)", zorder=3)
    ax1.set_ylim(0, 100)
    ax1.set_ylabel("Market Signal (0-100)", color="#4ea1ff", fontsize=9)
    ax1.tick_params(axis="y", colors="#4ea1ff", labelsize=8)
    ax1.tick_params(axis="x", colors="#aaaaaa", labelsize=8)

    ax2 = ax1.twinx()
    ax2.plot(df.index, df["kospi_cum"], color="#f4a340", lw=2.0, label="KOSPI (cumulative, start=100)", zorder=3)
    ax2.set_ylabel("KOSPI cumulative (start=100)", color="#f4a340", fontsize=9)
    ax2.tick_params(axis="y", colors="#f4a340", labelsize=8)

    ax1.set_title("AI Semi Cycle-Top  ·  Market Signal vs KOSPI (2Y Backtest)",
                  color="white", fontsize=13, fontweight="bold", pad=12)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
    for s in ax1.spines.values():
        s.set_color("#333333")
    for s in ax2.spines.values():
        s.set_color("#333333")
    ax1.grid(True, axis="y", color="#2a2a2a", lw=0.4, alpha=0.4)

    l1, lb1 = ax1.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax1.legend(l1 + l2, lb1 + lb2, loc="upper left", fontsize=8,
               facecolor="#1a1d26", edgecolor="#333333", labelcolor="white")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


if __name__ == "__main__":
    import json
    print("downloading 2y panel ...")
    panel = load_panel("2y")
    df = build_market_signal(panel)
    print(f"signal days: {len(df)}  ({df.index[0].date()} ~ {df.index[-1].date()})")
    stats = validate(df)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    p = make_chart(df)
    print("chart:", p)
