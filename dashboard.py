#!/usr/bin/env python3
"""
dashboard.py — 매일 실행: 데이터 수집 → 국면 판정 → HTML + history.csv

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
import html
import os
import sys

import pandas as pd
import yaml

import data as datamod
import engine

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


# ─────────────────────────── HTML ───────────────────────────
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d23;--muted:#6b7280;--line:#e5e7eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626;--info:#2563eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,"Malgun Gothic","Apple SD Gothic Neo",Segoe UI,Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 10px;color:#374151}
.sub{color:var(--muted);font-size:12px;margin-bottom:18px}
.hero{display:grid;grid-template-columns:1.3fr 1fr 1fr 1fr;gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.regime{color:#fff;border:0}.regime .big{font-size:26px;font-weight:700;line-height:1.2}.regime .small{opacity:.9;font-size:12px;margin-top:6px}
.kpi .lbl{color:var(--muted);font-size:12px}.kpi .val{font-size:26px;font-weight:700}.kpi .note{color:var(--muted);font-size:12px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.th .name{font-weight:600}.th .val{font-size:22px;font-weight:700;margin:2px 0}.th .rule{color:var(--muted);font-size:12px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.on{background:#fee2e2;color:var(--bad)}.off{background:#dcfce7;color:var(--ok)}.neutral{background:#e5e7eb;color:#374151}.warn{background:#fef3c7;color:var(--warn)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{background:#f3f4f6;color:#374151;font-weight:600;font-size:12px}td:first-child,th:first-child{text-align:left}
.pos{color:var(--ok)}.neg{color:var(--bad)}
.ladder{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.step{padding:4px 10px;border-radius:6px;background:#e5e7eb;font-size:12px}.step.done{background:#dbeafe;color:var(--info);font-weight:600}
ul.rules{margin:0;padding-left:18px}ul.rules li{margin:3px 0}
.foot{color:var(--muted);font-size:12px;margin-top:28px}
@media(max-width:720px){.hero,.grid3{grid-template-columns:1fr}}
"""


def _pct(v, digits=1, plus=True):
    if v is None or pd.isna(v):
        return "–"
    s = f"{v * 100:+.{digits}f}%" if plus else f"{v * 100:.{digits}f}%"
    return s


def _cls(v):
    if v is None or pd.isna(v):
        return ""
    return "pos" if v > 0 else "neg" if v < 0 else ""


def render_html(snap: dict, res: pd.DataFrame, m: pd.DataFrame, cfg: dict, source: str,
                account: dict | None = None) -> str:
    ex = cfg["exposure"]
    rules = cfg["rules"]
    color = engine.REGIME_COLOR[snap["regime"]]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    src_label = {"live": "Yahoo Finance + FRED", "cache": "캐시(data/market.csv) — 수집 실패", "demo": "합성 데모 데이터"}[source]

    def pill(on, on_txt="경고", off_txt="정상", on_cls="on", off_cls="off"):
        return f'<span class="pill {on_cls if on else off_cls}">{on_txt if on else off_txt}</span>'

    # 온도계
    th_cards = ""
    for t in snap["thermometers"]:
        th_cards += f"""<div class="card th"><div class="name">{t['name']} <span class="rule">— {t['desc']}</span></div>
<div class="val">{t['value']}</div><div class="rule">기준: {html.escape(t['rule'])}</div>{pill(t['on'])}</div>"""

    # 안정화 조건 + 사다리
    stab_rows = ""
    for c in snap["stabilization"]:
        det = f' <span class="rule">({c["detail"]})</span>' if c.get("detail") else ""
        stab_rows += f"<li>{c['rule']}{det} {pill(not c['ok'], '미충족', '충족')}</li>"
    steps = [f'<span class="step {"done" if i <= snap["ladder_step"] else ""}">{int(ex["shock"] + cfg["regime"]["ladder_step_pct"] * i)}%</span>'
             for i in range(0, int((ex["stabilized_max"] - ex["shock"]) / cfg["regime"]["ladder_step_pct"]) + 1)]
    ladder_html = '<div class="ladder">' + " → ".join(steps) + "</div>"
    ladder_note = ""
    if snap["regime"] in ("shock", "stabilized"):
        ladder_note = (f"확인된 안정화 조건 {snap['stab_n']}/3, 3개 모두 유지 {snap['ladder_days']}일째. "
                       f"사다리는 {cfg['regime']['ladder_up_interval_days']}거래일에 한 칸(+{cfg['regime']['ladder_step_pct']}%p)만 올리고, "
                       f"안정화 후에는 {cfg['regime']['ladder_days_per_step']}일마다 한 칸 추가. 내려갈 땐 즉시.")
    else:
        ladder_note = "충격·안정화 국면에서만 작동합니다."

    # 국내·보조
    kr = snap["kr"]
    aux = snap["aux"]
    kr_html = f"""<div class="grid3">
<div class="card"><div class="kpi"><div class="lbl">KOSPI vs 200일선</div><div class="val {_cls(kr['kospi_dist'])}">{_pct(kr['kospi_dist'])}</div>
<div class="note">{'200일선 아래 → 국내 노출 -' + str(cfg['korea']['below_ma_penalty_pct']) + '%p' if kr['below_ma'] else ('과열(+' + str(cfg['korea']['overheat_pct']) + '% 이상) → 국내 상한 ' + str(ex['overheated']) + '%' if kr['overheat'] else '200일선 위 — 조정 없음')}</div></div></div>
<div class="card"><div class="kpi"><div class="lbl">USD/KRW {cfg['korea']['fx']['change_days']}일 변화</div><div class="val {_cls(kr['fx_chg'])}">{_pct(kr['fx_chg'])}</div>
<div class="note">{pill(kr['fx_warn'], '원화 급약세 경고', '정상', 'warn')} 참고용 (국면 변경 없음)</div></div></div>
<div class="card"><div class="kpi"><div class="lbl">MOVE (채권 변동성)</div><div class="val">{'–' if aux['move'] is None else f"{aux['move']:.0f}"}</div>
<div class="note">{pill(aux['move_warn'], '장기채 위험 ≥' + str(cfg['auxiliary']['move_warn']), '정상', 'warn')} 보조 지표</div></div></div>
</div>"""

    # 참고 패널
    ref_rows = ""
    for item in cfg["reference"]:
        k = item["key"]
        if k not in m.columns:
            continue
        s = m[k].dropna()
        if len(s) < 2:
            continue
        last = float(s.iloc[-1])
        d1 = s.iloc[-1] / s.iloc[-2] - 1 if len(s) > 1 else None
        d20 = s.iloc[-1] / s.iloc[-21] - 1 if len(s) > 21 else None
        ma200 = s.rolling(200).mean().iloc[-1] if len(s) >= 200 else None
        dma = last / ma200 - 1 if ma200 and pd.notna(ma200) else None
        fmt = f"{last:,.2f}" if last < 1000 else f"{last:,.0f}"
        ref_rows += (f"<tr><td>{item['name']}</td><td>{fmt}</td>"
                     f"<td class='{_cls(d1)}'>{_pct(d1)}</td><td class='{_cls(d20)}'>{_pct(d20)}</td>"
                     f"<td class='{_cls(dma)}'>{_pct(dma)}</td></tr>")

    # 최근 30일 판정
    hist_rows = ""
    for d, r in res.tail(30).iloc[::-1].iterrows():
        hist_rows += (f"<tr><td>{d.strftime('%Y-%m-%d')}</td>"
                      f"<td style='color:{engine.REGIME_COLOR[r['regime']]};font-weight:600;text-align:left'>{engine.REGIME_KO[r['regime']]}</td>"
                      f"<td>{r['exposure_us']:.0f}%</td><td>{r['exposure_kr']:.0f}%</td>"
                      f"<td>{int(r['score_raw'])} / {r['score_s']:.1f}</td><td>{r['vix']:.1f}</td><td>{r['hy']:.2f}</td>"
                      f"<td>{r['spx_dist'] * 100:+.1f}%</td><td>{int(r['stab_n'])}</td></tr>")

    # 계좌 (로컬 전용)
    acct_html = ""
    if account and account.get("equity"):
        eq = float(account["equity"])
        peak = float(account.get("peak") or eq)
        dd = eq / peak - 1
        hard = -rules["account_drawdown_stop_pct"] / 100
        soft = -rules["account_soft_drawdown_pct"] / 100
        status = ("<b style='color:var(--bad)'>손실 한도 도달 — 신규 진입 중단·전면 재점검</b>" if dd <= hard
                  else "<b style='color:var(--warn)'>경고선 — 신규 리스크 절반</b>" if dd <= soft
                  else "<b style='color:var(--ok)'>정상</b>")
        acct_html = f"""<h2>0. 내 계좌 (로컬 전용 — 공개 페이지에는 없음)</h2>
<div class="grid3">
<div class="card kpi"><div class="lbl">계좌 총액</div><div class="val">{eq:,.0f}</div><div class="note">고점 {peak:,.0f}</div></div>
<div class="card kpi"><div class="lbl">고점 대비</div><div class="val {_cls(dd)}">{_pct(dd)}</div><div class="note">경고 {soft*100:.0f}% / 한도 {hard*100:.0f}%</div></div>
<div class="card kpi"><div class="lbl">1층 판정</div><div class="val" style="font-size:16px">{status}</div>
<div class="note">목표 노출 {snap['exposure_us']:.0f}% = {eq * snap['exposure_us'] / 100:,.0f} / 포지션당 상한 {rules['position_weight_cap_pct']}% = {eq * rules['position_weight_cap_pct'] / 100:,.0f}</div></div>
</div>"""

    def fmt(v, f):
        return "–" if v is None else f.format(v)

    bd = snap["breadth"]
    breadth_html = (f'<div class="card" style="margin-bottom:12px;font-size:13px"><b>시장 폭</b> — 동일가중/시총가중 S&amp;P {cfg.get("breadth",{}).get("ratio_days",60)}일 변화 '
                    f'<b class="{_cls(bd["rsp_spy_chg"])}">{_pct(bd["rsp_spy_chg"])}</b>, S&amp;P500 고점 근처: {"예" if bd["near_high"] else "아니오"}'
                    f'{" · 소수 종목이 지수를 떠받치는 중" if bd["warn"] else ""}'
                    f'<span style="color:var(--muted)"> (참고 전용. 2004~2026 검증에서 앞서는 힘 미미 — 행동 근거 아님)</span></div>')
    ka = snap["kr_aux"]
    kac = cfg.get("korea_aux", {})
    adr_txt = "–" if ka["adr"] is None else f"{ka['adr']:.0f}"
    adr_note = ("수집 누적 중 (20일 이상 쌓이면 표시)" if ka["adr"] is None else
                ("과매도 (반등 여지)" if ka["adr"] < kac.get("adr", {}).get("oversold", 75) else
                 "과열" if ka["adr"] > kac.get("adr", {}).get("overheated", 120) else "중립"))
    sp_txt = "–" if ka["spread"] is None else f"{ka['spread']:.2f}%p"
    sp_note = ("ECOS 키 없음 — 미수집" if ka["spread"] is None else
               f"20일 변화 {fmt(ka['spread_widen'], '{:+.2f}%p')} · " + ("경고 수준" if ka["spread_warn"] else "정상"))
    kr_aux_html = f"""<div class="grid3">
<div class="card kpi"><div class="lbl">국내 시장 폭 대용 (KOSDAQ/KOSPI {kac.get('breadth',{}).get('ratio_days',60)}일)</div><div class="val {_cls(ka['kq_ks_chg'])}">{_pct(ka['kq_ks_chg'])}</div>
<div class="note">{"KOSPI 고점 근처인데 소형·성장주 이탈" if ka['breadth_warn'] else "특이 없음"}</div></div>
<div class="card kpi"><div class="lbl">등락비율 ADR ({kac.get('adr',{}).get('window',20)}일)</div><div class="val">{adr_txt}</div><div class="note">{adr_note}</div></div>
<div class="card kpi"><div class="lbl">국내 신용 스프레드 (회사채 AA- − 국고채 3y)</div><div class="val">{sp_txt}</div><div class="note">{sp_note}</div></div>
</div>"""
    changed = "" if snap["regime"] == snap["regime_prev"] else f' <span class="pill warn">전일 {engine.REGIME_KO[snap["regime_prev"]]} → 변경</span>'
    freeze_html = ""
    if snap["freeze"]:
        freeze_html = (f'<div class="card" style="border-color:var(--warn);background:#fffbeb;margin-top:12px">'
                       f'<b style="color:var(--warn)">동결 중</b> — 최근 {cfg["regime"]["freeze_days"]}거래일 안에 VIX 가 {cfg["regime"]["freeze_vix"]} 을 넘었습니다. '
                       f'국면은 바꾸지 않습니다. <b>신규 매수 금지, 트레이딩 버킷 {ex["trading_bucket"]["shock"]}%</b>. '
                       f'실제 축소는 5일 평균 점수가 {cfg["regime"]["shock_score"]} 을 넘길 때만.</div>')
    elif snap["regime"] == "shock":
        freeze_html = (f'<div class="card" style="margin-top:12px;color:var(--muted);font-size:12px">충격 {snap["shock_age"]}일째 '
                       f'(최소 {cfg["regime"]["min_shock_days"]}일 유지 후 안정화 조건 3개가 확인되면 전환). 신규 매수 금지.</div>')

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>투자 프레임워크 대시보드 — {snap['date']}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>투자 프레임워크 대시보드 <span style="font-weight:400;color:var(--muted)">v{cfg['version']}</span></h1>
<div class="sub">기준일 {snap['date']} (미국 종가) · 생성 {now} · 데이터: {src_label}</div>
{acct_html}
<h2>1. 오늘의 국면과 목표 노출</h2>
<div class="hero">
<div class="card regime" style="background:{color}"><div class="small">현재 국면{changed}</div><div class="big">{snap['regime_ko']}</div>
<div class="small">온도계 점수 {snap['score_raw']}/3 (5일 평균 {snap['score_s']:.1f}) · S&amp;P500 200일선 대비 {_pct(snap['overheat_dist'])}</div></div>
<div class="card kpi"><div class="lbl">총 노출 상한 (미국/글로벌)</div><div class="val">{snap['exposure_us']:.0f}%</div><div class="note">나머지는 현금(≤2년 채권·MMF)</div></div>
<div class="card kpi"><div class="lbl">국내 노출 상한</div><div class="val">{snap['exposure_kr']:.0f}%</div><div class="note">KOSPI 200일선·환율로 조정</div></div>
<div class="card kpi"><div class="lbl">트레이딩 버킷</div><div class="val">{snap['trading_bucket']:.0f}%</div><div class="note">평시 {ex['trading_bucket']['normal']}% / 충격·동결 {ex['trading_bucket']['shock']}%</div></div>
</div>
{freeze_html}

<h2>2. 핵심 온도계 3개 (미국 → 국면 결정)</h2>
<div class="grid3">{th_cards}</div>

<h2>3. 안정화 조건과 노출 사다리</h2>
<div class="card"><ul class="rules">{stab_rows}</ul><div style="margin:10px 0 6px">{ladder_html}</div><div class="rule" style="color:var(--muted);font-size:12px">{ladder_note}</div></div>

<h2>4. 국내 조정 · 보조 지표 (참고, 국면을 바꾸지 않음)</h2>
{kr_html}

<h2>5. 국내 참고 지표 (참고 전용 — 국면·노출을 바꾸지 않음)</h2>
{kr_aux_html}

<h2>6. 참고 패널</h2>
{breadth_html}
<table><thead><tr><th>지표</th><th>종가</th><th>1일</th><th>20일</th><th>vs 200일선</th></tr></thead><tbody>{ref_rows}</tbody></table>

<h2>7. 최근 30일 판정</h2>
<table><thead><tr><th>날짜</th><th style="text-align:left">국면</th><th>노출(미)</th><th>노출(국내)</th><th>점수 raw/5일</th><th>VIX</th><th>신용</th><th>S&amp;P vs 200일</th><th>안정화</th></tr></thead><tbody>{hist_rows}</tbody></table>

<h2>8. 1층 생존 규칙 (고정)</h2>
<div class="card"><ul class="rules">
<li>포지션별 고정 손절 <b>{rules['position_stop_pct']}%</b> — 충격이라고 넓히지 않는다</li>
<li>단일 포지션 비중 상한 <b>{rules['position_weight_cap_pct']}%</b> → 실수 1건의 손실 = 계좌의 1~1.5%</li>
<li>계좌 고점 대비 <b>-{rules['account_drawdown_stop_pct']}%</b> 에서 어떤 경우에도 신규 진입 중단·전면 재점검 (경고선 -{rules['account_soft_drawdown_pct']}%)</li>
<li>현금 정의: {rules['cash_definition']}</li>
<li>국면별 총 노출 상한: 과열 {ex['overheated']}% · 일반 상승 {ex['uptrend']}% · 충격 {ex['shock']}% · 안정화 최대 {ex['stabilized_max']}%</li>
<li>규칙 변경은 {rules['rule_change_cooldown_days']}일 간격, 손실 중에는 금지 (변경은 config.yaml + CHANGELOG.md)</li>
</ul></div>
<div class="foot">이 페이지는 판단 보조용입니다. 매수·매도 결정은 본인이 합니다. 소스: github.com/sebals38/InvestmentGuide</div>
</div></body></html>"""


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

    # 공개 페이지 (계좌 숫자 없음)
    pub = _p(cfg["output"]["public_html"])
    os.makedirs(os.path.dirname(pub), exist_ok=True)
    with open(pub, "w", encoding="utf-8") as fh:
        fh.write(render_html(snap, res, m, cfg, source))
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
            fh.write(render_html(snap, res, m, cfg, source, account))
        print(f"로컬 페이지: {loc}")

    if not args.no_history and source != "demo":
        append_history(res, cfg, source)

    print(f"[{snap['date']}] 국면: {snap['regime_ko']}{' (동결)' if snap['freeze'] else ''}  노출 미국 {snap['exposure_us']:.0f}% / 국내 {snap['exposure_kr']:.0f}%  "
          f"점수 {snap['score_raw']}/3 (5일 {snap['score_s']:.1f})  안정화 {snap['stab_n']}/3  데이터: {source}")
    print(f"공개 페이지: {pub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
