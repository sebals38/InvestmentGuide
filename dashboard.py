#!/usr/bin/env python3
"""
dashboard.py — 매일 실행: 데이터 수집 → 국면 판정 + 주요 종목·섹터 → HTML 한 장 + history.csv

사용법:
    python dashboard.py                    # 실데이터 → docs/index.html (공개용, 계좌 숫자 없음)
    python dashboard.py --demo             # 네트워크 없이 합성 데이터
    python dashboard.py --equity 50000000 --peak 55000000
                                           # 계좌 값을 주면 local/dashboard.html 에 1층 판정 추가 (git 제외)
    python dashboard.py --no-refresh       # 수집 생략, data/market.csv 캐시로만 실행

GitHub Actions 는 매일 `python dashboard.py` 만 실행하고 docs/ report/ data/ 를 커밋합니다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import pandas as pd
import yaml

import data as datamod
import engine
import render
import stocks as stockmod

HERE = os.path.dirname(os.path.abspath(__file__))


def _p(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def load_cfg(path="config.yaml") -> dict:
    with open(_p(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─────────────────────────── history ───────────────────────────
HISTORY_COLS = [
    "date", "regime", "freeze", "exposure_us", "exposure_kr", "trading_bucket",
    "score_raw", "score_s", "spx_dist", "vix", "hy", "move",
    "stab_n", "ladder_days", "kr_below_ma", "fx_warn", "source",
]


def append_history(res: pd.DataFrame, cfg: dict, source: str) -> pd.DataFrame:
    path = _p(cfg["output"]["history_csv"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = res.iloc[-1]
    row = {
        "date": res.index[-1].strftime("%Y-%m-%d"), "regime": r["regime"], "freeze": int(r["freeze"]),
        "exposure_us": r["exposure_us"], "exposure_kr": r["exposure_kr"], "trading_bucket": r["trading_bucket"],
        "score_raw": int(r["score_raw"]), "score_s": round(float(r["score_s"]), 2),
        "spx_dist": round(float(r["spx_dist"]) * 100, 2), "vix": round(float(r["vix"]), 2),
        "hy": round(float(r["hy"]), 2), "move": round(float(r["move"]), 1) if pd.notna(r["move"]) else "",
        "stab_n": int(r["stab_n"]), "ladder_days": int(r["ladder_days"]),
        "kr_below_ma": int(r["kr_below_ma"]), "fx_warn": int(r["fx_warn"]), "source": source,
    }
    new = pd.DataFrame([row], columns=HISTORY_COLS)

    if os.path.exists(path):
        old = pd.read_csv(path, encoding="utf-8-sig")
        if list(old.columns) != HISTORY_COLS:  # v0.1 스키마면 백업 후 새로 시작
            old.to_csv(path.replace(".csv", "_v01.csv"), index=False, encoding="utf-8-sig")
            old = pd.DataFrame(columns=HISTORY_COLS)
        old = old[old["date"] != row["date"]]  # 같은 날 재실행이면 덮어씀
        hist = pd.concat([old, new], ignore_index=True)
    else:
        hist = new
    hist = hist.sort_values("date")
    hist.to_csv(path, index=False, encoding="utf-8-sig")
    return hist


# ─────────────────────────── main ───────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="합성 데이터")
    ap.add_argument("--no-refresh", action="store_true", help="수집 생략, 캐시 사용")
    ap.add_argument("--equity", type=float, help="계좌 총액 (로컬 HTML 에만 반영)")
    ap.add_argument("--peak", type=float, help="계좌 고점")
    ap.add_argument("--no-history", action="store_true", help="history.csv 에 기록하지 않음")
    args = ap.parse_args(argv)

    cfg = load_cfg()
    m, source = datamod.load_market(cfg, demo=args.demo, refresh=not args.no_refresh)
    if not args.demo:
        adr = datamod.update_kr_adr(cfg) if not args.no_refresh else datamod.load_kr_adr(cfg)
        if adr is not None and len(adr):
            m = m.join(adr.rename(columns={"adv": "kr_adv", "dec": "kr_dec"})[["kr_adv", "kr_dec"]], how="left")
    res = engine.run(m, cfg)
    snap = engine.snapshot(res, cfg)

    # 주요 종목(켈리) · 섹터 강약
    px = stockmod.demo_prices(cfg) if args.demo else stockmod.fetch_prices(cfg, refresh=not args.no_refresh)
    stk = stockmod.kelly_table(px, cfg)
    sec = stockmod.sector_table(px, cfg)

    # 공개 페이지 (계좌 숫자 없음)
    pub = _p(cfg["output"]["public_html"])
    os.makedirs(os.path.dirname(pub), exist_ok=True)
    with open(pub, "w", encoding="utf-8") as fh:
        fh.write(render.render(snap, res, m, cfg, source, stk, sec))
    open(os.path.join(os.path.dirname(pub), ".nojekyll"), "a").close()

    # 계좌 값이 있으면 로컬 페이지 (git 제외)
    account = None
    acct_file = _p("account.yaml")
    if args.equity:
        account = {"equity": args.equity, "peak": args.peak}
    elif os.path.exists(acct_file):
        with open(acct_file, encoding="utf-8") as fh:
            account = yaml.safe_load(fh) or None
    if account:
        loc = _p(cfg["output"]["local_html"])
        os.makedirs(os.path.dirname(loc), exist_ok=True)
        with open(loc, "w", encoding="utf-8") as fh:
            fh.write(render.render(snap, res, m, cfg, source, stk, sec, account))
        print(f"로컬 페이지: {loc}")

    if not args.no_history and source != "demo":
        append_history(res, cfg, source)

    print(f"[{snap['date']}] 국면: {snap['regime_ko']}{' (동결)' if snap['freeze'] else ''}  노출 미국 {snap['exposure_us']:.0f}% / 국내 {snap['exposure_kr']:.0f}%  "
          f"점수 {snap['score_raw']}/3 (5일 {snap['score_s']:.1f})  안정화 {snap['stab_n']}/3  데이터: {source}")
    print(f"공개 페이지: {pub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
