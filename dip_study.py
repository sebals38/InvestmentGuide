r"""
dip_study.py — 고위험 자산 '하락 시 진입' 기준 점검 (참고용 1회성 분석)

질문: 60일(≈90 달력일) 고점 대비 −X% 떨어졌을 때 샀다면, 그 뒤 3개월·6개월은 어땠나?
      그리고 산 뒤에 얼마나 더 떨어졌나? (→ 분할 진입 간격을 정하는 근거)

정의
- 하락 에피소드: 고점 대비 −X% 를 처음 뚫은 날 = 진입 1회. 같은 하락 중엔 다시 세지 않음.
  (다시 세려면 고점 대비 −2% 이내로 회복해야 함 = 새 에피소드)
- 진입가: 그날 종가
- 비교 기준(기저): 아무 날에나 샀을 때의 같은 기간 수익
- 기간은 달력일 기준 (코인은 주말에도 거래되므로)

사용:  .venv\Scripts\python.exe dip_study.py
결과:  화면 표 + report\dip_study.md
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=DeprecationWarning)

HERE = os.path.dirname(os.path.abspath(__file__))

ASSETS = {                      # 이름: (야후 티커, 시작일)
    "비트코인": ("BTC-USD", "2014-09-01"),
    "미국 소형 성장주 (IWO)": ("IWO", "2001-01-01"),
    "혁신 성장주 (ARKK)": ("ARKK", "2014-11-01"),
}
THRESHOLDS = [-0.10, -0.15, -0.20, -0.25, -0.30]
LOOKBACK = "90D"                # 60 거래일 ≈ 90 달력일
REARM = -0.02                   # 고점 대비 −2% 이내로 돌아오면 새 에피소드
HORIZONS = {"3개월": 91, "6개월": 182}


def load(ticker: str, start: str) -> pd.Series:
    import yfinance as yf
    df = yf.download(ticker, start=start, auto_adjust=True, progress=False)
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = s.dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s


def fwd_value(s: pd.Series, t: pd.Timestamp, days: int) -> float | None:
    target = t + pd.Timedelta(days=days)
    if target > s.index[-1]:
        return None
    return float(s.asof(target))


def episodes(dd: pd.Series, thr: float) -> list[pd.Timestamp]:
    out, armed = [], True
    for t, v in dd.items():
        if armed and v <= thr:
            out.append(t)
            armed = False
        elif not armed and v >= REARM:
            armed = True
    return out


def study(name: str, s: pd.Series) -> pd.DataFrame:
    peak = s.rolling(LOOKBACK).max()
    dd = s / peak - 1
    years = (s.index[-1] - s.index[0]).days / 365.25
    rows = []

    # 기저: 아무 날에나 샀을 때 (주 1회 표본)
    base = {}
    sample = s.index[::5]
    for h, d in HORIZONS.items():
        r = [fwd_value(s, t, d) / s[t] - 1 for t in sample if fwd_value(s, t, d) is not None]
        base[h] = np.array(r)
    rows.append({"자산": name, "기준": "아무 날 (기저)", "횟수": "-", "연평균": "-",
                 **{f"{h} 중앙값": f"{np.median(r):+.0%}" for h, r in base.items()},
                 **{f"{h} 플러스 확률": f"{(r > 0).mean():.0%}" for h, r in base.items()},
                 "산 뒤 추가하락 중앙값": "-", "산 뒤 추가하락 최악": "-"})

    for thr in THRESHOLDS:
        ev = episodes(dd.dropna(), thr)
        res = {h: [] for h in HORIZONS}
        mae = []
        for t in ev:
            p0 = s[t]
            for h, d in HORIZONS.items():
                v = fwd_value(s, t, d)
                if v is not None:
                    res[h].append(v / p0 - 1)
            win = s[(s.index > t) & (s.index <= t + pd.Timedelta(days=HORIZONS["6개월"]))]
            if len(win):
                mae.append(win.min() / p0 - 1)
        row = {"자산": name, "기준": f"고점 대비 {thr:.0%}", "횟수": len(ev),
               "연평균": f"{len(ev) / years:.1f}"}
        for h in HORIZONS:
            r = np.array(res[h])
            row[f"{h} 중앙값"] = f"{np.median(r):+.0%}" if len(r) else "-"
            row[f"{h} 플러스 확률"] = f"{(r > 0).mean():.0%} (n={len(r)})" if len(r) else "-"
        m = np.array(mae)
        row["산 뒤 추가하락 중앙값"] = f"{np.median(m):.0%}" if len(m) else "-"
        row["산 뒤 추가하락 최악"] = f"{m.min():.0%}" if len(m) else "-"
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    frames = []
    for name, (tk, start) in ASSETS.items():
        try:
            s = load(tk, start)
            print(f"{name} [{tk}]: {len(s)}행, {s.index[0].date()} ~ {s.index[-1].date()}")
            frames.append(study(name, s))
        except Exception as e:  # noqa: BLE001
            print(f"[경고] {name} [{tk}] 실패: {type(e).__name__} {e}", file=sys.stderr)
    if not frames:
        sys.exit("데이터를 하나도 받지 못함")
    out = pd.concat(frames, ignore_index=True)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print()
    print(out.to_string(index=False))

    os.makedirs(os.path.join(HERE, "report"), exist_ok=True)
    path = os.path.join(HERE, "report", "dip_study.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 하락 시 진입 기준 점검 ({pd.Timestamp.today().date()})\n\n")
        f.write("고점 = 최근 90 달력일(≈60 거래일) 최고 종가. 한 하락 에피소드당 첫 진입만 셈.\n")
        f.write("'산 뒤 추가하락' = 진입 후 6개월 안의 최저점 (분할 진입 간격의 근거).\n\n")
        for name, g in out.groupby("자산", sort=False):
            f.write(f"## {name}\n\n")
            t = g.drop(columns="자산").astype(str)
            f.write("| " + " | ".join(t.columns) + " |\n")
            f.write("|" + "---|" * len(t.columns) + "\n")
            for _, r in t.iterrows():
                f.write("| " + " | ".join(r.values) + " |\n")
            f.write("\n\n")
    print(f"\n저장: {path}")


if __name__ == "__main__":
    main()
