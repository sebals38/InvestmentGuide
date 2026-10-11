"""
check_sources.py — 시세 소스 점검 (Yahoo / Stooq 둘 다, 열마다 마지막 날짜 표시)
사용:  VS Code ▶ 로 실행 (인자 없음)

GitHub Actions 에서 Yahoo 가 막혔을 때 Stooq 대체가 제대로 동작하는지 미리 확인하는 용도.
'실패' 로 나오는 Stooq 열은 config.yaml 의 sources.stooq 심볼을 고치거나 비워 두면 됨
(비워 두면 그 열은 캐시 값으로 보충됨 — 핵심 열 spx, vix 는 반드시 되어야 함).
"""
import io
import sys

import pandas as pd
import requests
import yaml

import data as datamod

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
src = cfg["sources"]
yah, stq = src["yahoo"], src.get("stooq", {})

print("=== Yahoo Finance ===")
try:
    y = datamod.fetch_yahoo(yah, "2026-01-01")
    for col in yah:
        s = y[col].dropna() if col in y else pd.Series(dtype=float)
        print(f"  {col:7s} {yah[col]:10s} {'OK  ~' + str(s.index.max().date()) if len(s) else '실패'}")
except Exception as e:  # noqa: BLE001
    print(f"  전체 실패: {type(e).__name__} {str(e)[:200]}")

print("\n=== FRED 시세 (Yahoo 대체 1순위) ===")
try:
    f = datamod.fetch_fred_prices(yah, "2026-01-01", src.get("fred_prices"))
    for col in yah:
        if col in f:
            s = f[col].dropna()
            print(f"  {col:7s} {src['fred_prices'][col]:10s} OK  ~{s.index.max().date()}  마지막 {s.iloc[-1]:,.2f}")
        else:
            print(f"  {col:7s} (매핑 없음 → 캐시로 보충)")
except Exception as e:  # noqa: BLE001
    print(f"  전체 실패: {type(e).__name__} {str(e)[:200]}")

print("\n=== Stooq (Yahoo 대체 2순위) ===")
for col in yah:
    sym = stq.get(col)
    if not sym:
        print(f"  {col:7s} {'(매핑 없음 → 캐시로 보충)'}")
        continue
    try:
        r = requests.get(datamod.STOOQ_CSV.format(sym=sym), headers=datamod.UA, timeout=30)
        df = pd.read_csv(io.StringIO(r.text))
        if "Close" not in df or df.empty:
            raise RuntimeError(r.text[:60].replace("\n", " "))
        s = datamod._to_series(df["Date"], df["Close"])
        print(f"  {col:7s} {sym:10s} OK  {len(s)}행  {s.index.min().date()} ~ {s.index.max().date()}  마지막 {s.iloc[-1]:,.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"  {col:7s} {sym:10s} 실패  {type(e).__name__} {str(e)[:100]}")

print("\n핵심 열(spx, vix)이 FRED 시세에서 OK 면 GitHub Actions 에서도 수집이 이어집니다.", file=sys.stderr)
