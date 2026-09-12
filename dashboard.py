#!/usr/bin/env python3
"""
투자 프레임워크 대시보드 v0.1 (스켈레톤)

사용법:
    pip install yfinance pandas pyyaml
    python dashboard.py                 # 실데이터 (Yahoo Finance)
    python dashboard.py --demo          # 네트워크 없이 합성 데이터로 레이아웃 확인
    python dashboard.py --equity 50000000 --peak 55000000   # 계좌 현재값/고점 넣으면 1층 규칙 판정

구조:
    config.yaml  → 규칙·지표 설정 (숫자는 전부 여기)
    dashboard.py → 데이터 수집 → 지표 계산 → 국면 판정 → HTML 렌더
    report/      → dashboard.html (매일 덮어씀), history.csv (누적)
"""
import argparse
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────── 데이터 ───────────────────────────
def fetch_prices(tickers, days=400, demo=False):
    """종가 DataFrame (index=date, columns=ticker)을 돌려준다."""
    if demo:
        rng = np.random.default_rng(7)
        idx = pd.bdate_range(end=dt.date.today(), periods=days)
        days = len(idx)
        data = {}
        for i, t in enumerate(tickers):
            drift = rng.normal(0.0003, 0.0002)
            vol = 0.006 + 0.004 * (i % 4)
            path = 100 * np.exp(np.cumsum(rng.normal(drift, vol, days)))
            data[t] = path
        return pd.DataFrame(data, index=idx)

    import yfinance as yf
    raw = yf.download(tickers, period=f"{days + 30}d", progress=False, auto_adjust=False)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close.dropna(how="all").ffill()


# ─────────────────────────── 지표 ───────────────────────────
def compute_metrics(close: pd.Series, cfg_trend: dict, kind: str = "price") -> dict:
    """한 자산의 국면 지표. kind: price | vol | rate"""
    s = close.dropna()
    if len(s) < cfg_trend["long_ma"] + 5:
        return {"error": f"데이터 부족 ({len(s)}일)"}
    last = float(s.iloc[-1])
    ma_l = float(s.rolling(cfg_trend["long_ma"]).mean().iloc[-1])
    ma_s = float(s.rolling(cfg_trend["short_ma"]).mean().iloc[-1])
    mom = last / float(s.iloc[-cfg_trend["momentum_days"]]) - 1
    ret = np.log(s).diff().dropna()
    rv = float(ret.tail(cfg_trend["vol_window"]).std() * math.sqrt(252))
    rv_1y = ret.tail(252).rolling(cfg_trend["vol_window"]).std() * math.sqrt(252)
    vol_pctile = float((rv_1y < rv).mean())  # 최근 1년 대비 현재 변동성 백분위
    dd = last / float(s.tail(252).max()) - 1
    chg_1d = last / float(s.iloc[-2]) - 1
    chg_1w = last / float(s.iloc[-6]) - 1

    # 추세 상태: 가격 vs 200일선 & 50일선 정렬
    if kind == "price":
        if last > ma_l and ma_s > ma_l:
            state = "상승추세"
        elif last < ma_l and ma_s < ma_l:
            state = "하락추세"
        else:
            state = "중립/전환"
    elif kind == "vol":  # VIX: 높을수록 위험
        state = "공포" if last > 25 else ("경계" if last > 18 else "안정")
    else:  # rate: 200일선 위면 긴축 압력
        state = "상승" if last > ma_l else "하락"

    return dict(
        last=last, ma50=ma_s, ma200=ma_l, dist_ma200=last / ma_l - 1,
        momentum=mom, rvol=rv, vol_pctile=vol_pctile, dd_52w=dd,
        chg_1d=chg_1d, chg_1w=chg_1w, state=state,
    )


def risk_budget(rows: list[dict]) -> dict:
    """2층 요약: 위험자산 중 상승추세 비율과 VIX로 리스크 예산 산출 (초안 로직)."""
    price_rows = [r for r in rows if r.get("kind", "price") == "price" and "state" in r
                  and r["group"] in ("글로벌 주식", "국내 주식")]
    up = sum(r["state"] == "상승추세" for r in price_rows)
    breadth = up / len(price_rows) if price_rows else float("nan")
    vix_rows = [r for r in rows if r.get("kind") == "vol" and "last" in r]
    vix = vix_rows[0]["last"] if vix_rows else float("nan")

    if breadth >= 0.75 and (math.isnan(vix) or vix < 20):
        level, mult = "정상", 1.00
    elif breadth >= 0.5 and (math.isnan(vix) or vix < 25):
        level, mult = "축소", 0.50
    else:
        level, mult = "방어", 0.25
    return dict(breadth=breadth, vix=vix, level=level, multiplier=mult)


def survival_check(rules: dict, equity, peak) -> dict:
    """1층 판정: 계좌 낙폭에 따른 규칙 발동."""
    if not equity or not peak:
        return {"status": "미입력", "dd": None, "note": "--equity / --peak 를 넘기면 계좌 낙폭 규칙을 판정합니다."}
    dd = equity / peak - 1
    if dd <= -rules["max_account_drawdown_pct"] / 100:
        return {"status": "정지", "dd": dd, "note": "최대 낙폭 도달 → 신규 진입 금지, 전 포지션 재점검"}
    if dd <= -rules["soft_drawdown_pct"] / 100:
        return {"status": "감속", "dd": dd, "note": "소프트 한도 → 신규 포지션 리스크 50%"}
    return {"status": "정상", "dd": dd, "note": "규칙 범위 내"}


# ─────────────────────────── HTML ───────────────────────────
CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;
--up:#1f7a4d;--dn:#b23a3a;--mid:#8a6d1f;--acc:#2c4f8f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,"Malgun Gothic","Apple SD Gothic Neo",sans-serif;padding:24px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:24px 0 8px;color:var(--muted);font-weight:600}
.meta{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{font-size:12px;color:var(--muted)}.card .v{font-size:24px;font-weight:700;margin:2px 0}
.card .s{font-size:12px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;color:var(--muted);font-weight:600;background:#fafaf8}
td:first-child,th:first-child{text-align:left}
tr.group td{background:#f2f2ee;font-weight:600;text-align:left;color:var(--muted);font-size:12px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff}
.up{background:var(--up)}.dn{background:var(--dn)}.mid{background:var(--mid)}.acc{background:var(--acc)}
.pos{color:var(--up)}.neg{color:var(--dn)}
.rules{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:6px 20px;font-size:13px}
.rules div{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line);padding:4px 0}
.rules b{font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto}
footer{margin-top:28px;color:var(--muted);font-size:12px}
"""

PILL = {"상승추세": "up", "하락추세": "dn", "중립/전환": "mid", "안정": "up", "경계": "mid", "공포": "dn",
        "상승": "mid", "하락": "acc", "정상": "up", "축소": "mid", "방어": "dn", "감속": "mid", "정지": "dn", "미입력": "acc"}


def pct(x, d=1, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    s = f"{x*100:+.{d}f}%" if sign else f"{x*100:.{d}f}%"
    return s


def cls(x):
    return "pos" if x > 0 else ("neg" if x < 0 else "")


def render(cfg, rows, budget, surv, asof, demo):
    r = cfg["rules"]
    rule_items = [
        ("계좌 최대 낙폭 한도", f"{r['max_account_drawdown_pct']}%"),
        ("소프트 낙폭 (리스크 반감)", f"{r['soft_drawdown_pct']}%"),
        ("포지션당 리스크 (현물)", f"{r['risk_per_trade_pct']}%"),
        ("포지션당 리스크 (선물·옵션)", f"{r['risk_per_trade_pct_derivatives']}%"),
        ("열린 리스크 합계 상한", f"{r['max_open_risk_pct']}%"),
        ("동일 테마 동시 보유", f"{r['max_correlated_positions']}개"),
        ("켈리 분수", f"{r['kelly_fraction']:.2f} × Kelly"),
        ("최소 보상/위험", f"{r['min_reward_risk']:.1f} : 1"),
        ("규칙 변경 냉각기간", f"{r['rule_change_cooldown_days']}일"),
    ]
    eff_risk = r["risk_per_trade_pct"] * budget["multiplier"] * (0.5 if surv["status"] == "감속" else 1.0)
    if surv["status"] == "정지":
        eff_risk = 0.0

    h = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>투자 대시보드</title>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style></head><body>",
         f"<h1>투자 프레임워크 대시보드 <span class='meta'>v0.1</span></h1>",
         f"<div class='meta'>기준일 {asof} · {'합성 데이터(데모)' if demo else 'Yahoo Finance'} · 매매 판단은 직접, 이 화면은 위험 예산만 말해줍니다</div>"]

    # 상단 요약
    h.append("<h2>오늘의 위험 예산</h2><div class='grid'>")
    h.append(f"<div class='card'><div class='k'>2층 국면 판정</div><div class='v'><span class='pill {PILL[budget['level']]}'>{budget['level']}</span></div>"
             f"<div class='s'>주식 지수 상승추세 비율 {pct(budget['breadth'],0,False)} · VIX {budget['vix']:.1f}</div></div>")
    h.append(f"<div class='card'><div class='k'>1층 계좌 낙폭 규칙</div><div class='v'><span class='pill {PILL[surv['status']]}'>{surv['status']}</span></div>"
             f"<div class='s'>{('고점 대비 ' + pct(surv['dd'])) if surv['dd'] is not None else ''} {surv['note']}</div></div>")
    h.append(f"<div class='card'><div class='k'>오늘 신규 포지션당 허용 리스크 (현물)</div><div class='v'>{eff_risk:.2f}%</div>"
             f"<div class='s'>기본 {r['risk_per_trade_pct']}% × 국면 {budget['multiplier']:.2f}"
             f"{' × 낙폭 0.5' if surv['status']=='감속' else ''}</div></div>")
    h.append("</div>")

    # 국면 지표 테이블
    h.append("<h2>2층 · 국면 지표</h2><div class='wrap'><table><thead><tr>"
             "<th>지표</th><th>현재</th><th>1일</th><th>1주</th><th>200일선 대비</th><th>6개월 모멘텀</th>"
             "<th>52주 고점 대비</th><th>실현변동성(20d)</th><th>변동성 백분위(1y)</th><th>상태</th></tr></thead><tbody>")
    cur = None
    for row in rows:
        if row["group"] != cur:
            cur = row["group"]
            h.append(f"<tr class='group'><td colspan='10'>{cur}</td></tr>")
        if "error" in row:
            h.append(f"<tr><td>{row['name']}</td><td colspan='9' style='text-align:left;color:var(--muted)'>{row['error']}</td></tr>")
            continue
        h.append(
            f"<tr><td>{row['name']} <span class='meta'>{row['ticker']}</span></td>"
            f"<td>{row['last']:,.2f}</td>"
            f"<td class='{cls(row['chg_1d'])}'>{pct(row['chg_1d'])}</td>"
            f"<td class='{cls(row['chg_1w'])}'>{pct(row['chg_1w'])}</td>"
            f"<td class='{cls(row['dist_ma200'])}'>{pct(row['dist_ma200'])}</td>"
            f"<td class='{cls(row['momentum'])}'>{pct(row['momentum'])}</td>"
            f"<td class='neg'>{pct(row['dd_52w'])}</td>"
            f"<td>{pct(row['rvol'],0,False)}</td>"
            f"<td>{pct(row['vol_pctile'],0,False)}</td>"
            f"<td><span class='pill {PILL.get(row['state'],'acc')}'>{row['state']}</span></td></tr>")
    h.append("</tbody></table></div>")

    # 1층 규칙 패널
    h.append("<h2>1층 · 생존 규칙 (고정값, config.yaml)</h2><div class='card'><div class='rules'>")
    for k, v in rule_items:
        h.append(f"<div><span>{k}</span><b>{v}</b></div>")
    h.append("</div></div>")

    # 3층 자리
    h.append("<h2>3층 · 진입 체크리스트 / 매매일지</h2><div class='card meta'>다음 단계에서 붙입니다. "
             "(진입 근거 · 무효화 조건 · 손절가 · 목표가 · 보상/위험 · 포지션 크기 계산기)</div>")
    h.append("<footer>규칙은 손실 중에 바꾸지 않는다. 바꾸려면 냉각기간이 지난 뒤, 수익 중일 때, 기록을 남기고.</footer>")
    h.append("</body></html>")
    return "\n".join(h)


# ─────────────────────────── main ───────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--demo", action="store_true", help="네트워크 없이 합성 데이터로 실행")
    ap.add_argument("--equity", type=float, help="현재 계좌 평가액")
    ap.add_argument("--peak", type=float, help="계좌 역사적 고점 평가액")
    a = ap.parse_args()

    with open(a.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    inds = cfg["indicators"]
    tickers = [i["ticker"] for i in inds]
    close = fetch_prices(tickers, demo=a.demo)

    rows = []
    for i in inds:
        m = compute_metrics(close[i["ticker"]], cfg["trend"], i.get("kind", "price")) if i["ticker"] in close else {"error": "다운로드 실패"}
        rows.append({**i, **m})

    budget = risk_budget(rows)
    surv = survival_check(cfg["rules"], a.equity or cfg["account"].get("equity"), a.peak)
    asof = str(close.index[-1].date())

    out = os.path.join(HERE, cfg["output"]["html_path"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(cfg, rows, budget, surv, asof, a.demo))

    # 판정 이력 누적 (나중에 "이 규칙이 실제로 도움이 됐나" 검증용)
    hist = os.path.join(HERE, cfg["output"]["history_csv"])
    rec = {"date": asof, "level": budget["level"], "multiplier": budget["multiplier"],
           "breadth": round(budget["breadth"], 3) if not math.isnan(budget["breadth"]) else None,
           "vix": round(budget["vix"], 2) if not math.isnan(budget["vix"]) else None,
           "account_status": surv["status"]}
    for r_ in rows:
        if "state" in r_:
            rec[f"{r_['ticker']}_state"] = r_["state"]
    pd.DataFrame([rec]).to_csv(hist, mode="a", header=not os.path.exists(hist), index=False, encoding="utf-8-sig")

    print(f"[ok] {out}\n국면: {budget['level']} (multiplier {budget['multiplier']}) · 계좌: {surv['status']}")


if __name__ == "__main__":
    sys.exit(main())
