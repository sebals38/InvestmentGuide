"""
stocks.py — 주요 종목 권장 최대 비중(켈리) + 섹터 강약

- 구글 시트 '켈리공식_자동계산_시트 v2' 와 '글로벌 마켓 대시보드' 의 섹터 탭을 대신합니다.
- 켈리 비중은 과거 3년 수익률로 추정해서 흔들림이 큽니다 → "이만큼 사라"가 아니라
  "이보다 많이 사지 마라"는 상한으로만 씁니다. 1층 종목당 상한(15%)을 절대 넘지 않습니다.

계산 (시트와 같은 방식)
  기대 초과수익 = 베타 × 시장 위험프리미엄            (CAPM)
  켈리 비중     = 기대 초과수익 / 변동성²
  적용 비중     = 켈리 × (변동성 높음이면 1/4, 아니면 1/2)  → 1층 상한으로 자름
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def _p(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def _all_tickers(cfg: dict) -> list[str]:
    s = cfg["stocks"]
    t = [x["ticker"] for x in s["list"]] + [s["benchmark"]["KR"], s["benchmark"]["US"]]
    for grp in cfg.get("sectors", {}).values():
        t += [x["ticker"] for x in grp]
    return list(dict.fromkeys(t))


def fetch_prices(cfg: dict, refresh: bool = True) -> pd.DataFrame:
    """종가 표 (열=티커). 실패한 티커는 캐시로 보충, 캐시도 없으면 빠짐."""
    path = _p(cfg["output"]["stocks_cache"])
    cache = pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else pd.DataFrame()
    if not refresh:
        return cache
    tickers = _all_tickers(cfg)
    start = (dt.date.today() - dt.timedelta(days=int(cfg["stocks"]["years"] * 365 + 400))).isoformat()
    try:
        import yfinance as yf
        raw = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        close.index = pd.to_datetime(close.index).tz_localize(None)
        close = close.dropna(how="all", axis=1)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 종목 시세 수집 실패: {type(e).__name__} {e} → 캐시 사용", file=sys.stderr)
        return cache
    missing = [t for t in tickers if t not in close.columns]
    if missing:
        print(f"[경고] 시세 없음(티커 확인 필요): {', '.join(missing)}", file=sys.stderr)
    if len(cache):
        close = close.combine_first(cache)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    close.sort_index().to_csv(path)
    return close


def kelly_table(px: pd.DataFrame, cfg: dict) -> list[dict]:
    s = cfg["stocks"]
    cap = float(cfg["rules"]["position_weight_cap_pct"])
    win = int(s["years"] * 252)
    hi_vol = float(s["high_vol_pct"]) / 100
    rows = []
    for it in s["list"]:
        t, mk = it["ticker"], it["market"]
        bm = s["benchmark"][mk]
        row = {"name": it["name"], "ticker": t, "market": mk, "ok": False}
        if t not in px.columns or bm not in px.columns:
            row["note"] = "시세 없음"
            rows.append(row)
            continue
        p = px[t].dropna()
        both = pd.DataFrame({"s": px[t], "b": px[bm]}).dropna()
        r = both.pct_change().dropna().tail(win)
        if len(r) < 200 or len(p) < 200:
            row["note"] = "기간 부족"
            rows.append(row)
            continue
        vol = float(r["s"].std() * np.sqrt(252))
        beta = 1.0 if t == bm else float(r["s"].cov(r["b"]) / r["b"].var())
        excess = beta * float(s["market_premium_pct"][mk]) / 100
        kelly = excess / vol ** 2 if vol > 0 else 0.0
        frac = 0.25 if vol >= hi_vol else 0.5
        rec = max(0.0, min(kelly * frac * 100, cap))
        ma200 = p.rolling(200).mean().iloc[-1]
        row.update({
            "ok": True, "price": float(p.iloc[-1]), "ma_dist": float(p.iloc[-1] / ma200 - 1),
            "chg20": float(p.iloc[-1] / p.iloc[-21] - 1) if len(p) > 21 else None,
            "vol": vol, "beta": beta, "kelly": kelly, "frac": frac, "rec": rec,
            "vol_lv": "높음" if vol >= hi_vol else ("중간" if vol >= float(s["mid_vol_pct"]) / 100 else "낮음"),
        })
        rows.append(row)
    return rows


def sector_table(px: pd.DataFrame, cfg: dict, days: int = 20) -> dict:
    out = {}
    for grp, items in cfg.get("sectors", {}).items():
        lst = []
        for it in items:
            t = it["ticker"]
            if t not in px.columns:
                continue
            p = px[t].dropna()
            if len(p) <= days:
                continue
            lst.append({"name": it["name"], "chg": float(p.iloc[-1] / p.iloc[-1 - days] - 1),
                        "chg5": float(p.iloc[-1] / p.iloc[-6] - 1)})
        out[grp] = sorted(lst, key=lambda x: x["chg"], reverse=True)
    return out


def demo_prices(cfg: dict, days: int = 1100, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=dt.date.today(), periods=days)
    cols = {}
    for i, t in enumerate(_all_tickers(cfg)):
        vol = 0.01 + 0.03 * ((i * 7) % 10) / 10
        cols[t] = 100 * np.exp(np.cumsum(rng.normal(0.0004, vol, days)))
    return pd.DataFrame(cols, index=idx)
