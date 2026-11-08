"""
render.py — 하나로 합친 대시보드 HTML (v0.9)

첫 화면에서 끝나게: ① 오늘의 결론 → ② 위험 판단 → ③ 주요 종목 → ④ 시장 한눈에
나머지(참고 지표·30일 이력·규칙·바로가기)는 맨 아래 '자세히 보기' 에 접어 둔다.
"""
from __future__ import annotations

import datetime as dt
import html

import pandas as pd

import engine

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#16181d;--muted:#667085;--line:#e4e7ec;
--green:#15803d;--green-bg:#dcfce7;--yellow:#b45309;--yellow-bg:#fef3c7;--red:#b91c1c;--red-bg:#fee2e2;--blue:#1d4ed8;--blue-bg:#dbeafe}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e6e8ec;--muted:#9aa3b2;--line:#2a2f3a;
--green:#4ade80;--green-bg:#123322;--yellow:#fbbf24;--yellow-bg:#3a2c0b;--red:#f87171;--red-bg:#3b1414;--blue:#93c5fd;--blue-bg:#14284b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Malgun Gothic","Apple SD Gothic Neo","Segoe UI",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:20px 16px 48px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
h1{font-size:18px;margin:0}.sub{color:var(--muted);font-size:12px}
h2{font-size:15px;margin:28px 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.verdict{display:flex;gap:16px;align-items:center;margin-top:14px;border-width:2px}
.light{width:56px;height:56px;border-radius:50%;flex:0 0 56px}
.v-title{font-size:24px;font-weight:700;line-height:1.25}
.v-nums{font-size:15px;margin-top:2px}
.v-todo{margin-top:8px;font-weight:600}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.th{display:flex;flex-direction:column;gap:2px}
.th .n{font-size:13px;color:var(--muted)}.th .v{font-size:22px;font-weight:700}.th .r{font-size:12px;color:var(--muted)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.next{margin-top:10px;font-size:14px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:12px;color:var(--muted);font-weight:600}
td:first-child,th:first-child{text-align:left}
tr:last-child td{border-bottom:0}
.tbl{padding:4px 12px;overflow-x:auto}
.pos{color:var(--green)}.neg{color:var(--red)}.mut{color:var(--muted)}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}
.t-g{background:var(--green-bg);color:var(--green)}.t-y{background:var(--yellow-bg);color:var(--yellow)}.t-r{background:var(--red-bg);color:var(--red)}
.bar{display:inline-block;height:8px;border-radius:4px;background:var(--blue);vertical-align:middle;margin-right:6px}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.sec h3{font-size:13px;margin:0 0 6px}
.sec .row{display:flex;justify-content:space-between;font-size:14px;padding:2px 0}
.strip{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px;margin-top:10px}
details{margin-top:28px}summary{cursor:pointer;font-weight:600;font-size:15px;padding:8px 0}
details h3{font-size:14px;margin:18px 0 8px}
ul{margin:0;padding-left:18px}li{margin:3px 0}
a{color:var(--blue)}
.foot{color:var(--muted);font-size:12px;margin-top:28px}
@media(max-width:640px){.hm{display:none}.g3,.g2{grid-template-columns:1fr}.verdict{align-items:flex-start}.light{width:40px;height:40px;flex-basis:40px}}
"""


def _pct(v, d=1, plus=True):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    return f"{v * 100:+.{d}f}%" if plus else f"{v * 100:.{d}f}%"


def _cls(v):
    if v is None or pd.isna(v):
        return ""
    return "pos" if v > 0 else "neg" if v < 0 else ""


# ─────────────────────────── 판정 문장 ───────────────────────────
def verdict(snap: dict, res: pd.DataFrame, cfg: dict) -> dict:
    """신호등 색, 제목, 오늘 할 일, 다음 국면까지 남은 것."""
    ex, rg = cfg["exposure"], cfg["regime"]
    reg = snap["regime"]
    on = [t["name"] for t in snap["thermometers"] if t["on"]]
    prev_us = float(res["exposure_us"].iloc[-2]) if len(res) > 1 else snap["exposure_us"]
    prev_kr = float(res["exposure_kr"].iloc[-2]) if len(res) > 1 else snap["exposure_kr"]
    down = snap["exposure_us"] < prev_us or snap["exposure_kr"] < prev_kr
    up = snap["exposure_us"] > prev_us or snap["exposure_kr"] > prev_kr

    if reg == "shock":
        color, todo = "red", f"신규 매수 금지. 총 노출을 {snap['exposure_us']:.0f}% 이하로 줄입니다."
    elif reg == "stabilized":
        color, todo = "blue", f"노출 상한 {snap['exposure_us']:.0f}%까지 천천히 다시 채울 수 있습니다."
    elif snap["freeze"]:
        color, todo = "yellow", f"동결: VIX가 {rg['freeze_vix']}을 넘었습니다. 며칠간 신규 매수 금지, 보유분은 그대로."
    elif on or reg == "overheated":
        color, todo = "yellow", "신규 매수는 천천히. 상한을 넘는 부분만 줄입니다."
    else:
        color, todo = "green", "평소대로. 상한 안에서 계획한 매매만 합니다."
    if down and reg != "shock":
        todo = f"상한이 내려갔습니다 (미국 {prev_us:.0f}→{snap['exposure_us']:.0f}%, 국내 {prev_kr:.0f}→{snap['exposure_kr']:.0f}%). 넘는 만큼 줄입니다."
    elif up and reg == "stabilized":
        todo = f"상한이 올라갔습니다 (미국 {prev_us:.0f}→{snap['exposure_us']:.0f}%). 한 번에 다 채우지 않습니다."

    cut = ex["uptrend_warning_cut_pct"]
    if reg in ("uptrend", "overheated"):
        if not on:
            nxt = (f"켜진 경고 없음. 경고 1개가 며칠 이어지면 상한 −{cut}%p, "
                   f"2개 이상이 이어지면 충격 국면(상한 {ex['shock']}%)입니다.")
        else:
            nxt = (f"켜진 경고 {len(on)}개 ({', '.join(on)}). 이대로 며칠 이어지면 상한이 경고 1개당 −{cut}%p 줄고, "
                   f"경고가 2개 이상 이어지면 충격 국면(상한 {ex['shock']}%)입니다.")
        if reg == "overheated":
            nxt = (f"S&P500이 200일선보다 {_pct(snap['overheat_dist'])} 위(5일 평균). "
                   f"+{rg['overheat_out_pct']}% 아래로 내려오면 일반 상승(상한 {ex['uptrend']}%)으로 돌아갑니다. ") + nxt
    elif reg == "shock":
        nxt = (f"충격 {snap['shock_age']}일째. 안정화 조건 {snap['stab_n']}/3 확인. "
               f"최소 {rg['min_shock_days']}일이 지나고 3개가 모두 확인되면 안정화 국면으로 넘어가 상한을 5%p씩 올립니다.")
    else:
        nxt = (f"안정화 {snap['ladder_days']}일째. 경고가 줄고 S&P500이 200일선 위로 올라오면 일반 상승(상한 {ex['uptrend']}%)입니다. "
               f"안정화 조건이 무너지고 200일선 아래면 다시 충격입니다.")
    return {"color": color, "todo": todo, "next": nxt}


# ─────────────────────────── HTML ───────────────────────────
def render(snap: dict, res: pd.DataFrame, m: pd.DataFrame, cfg: dict, source: str,
           stocks: list[dict], sectors: dict, account: dict | None = None) -> str:
    ex, rules = cfg["exposure"], cfg["rules"]
    vd = verdict(snap, res, cfg)
    col = {"green": "var(--green)", "yellow": "var(--yellow)", "red": "var(--red)", "blue": "var(--blue)"}[vd["color"]]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    src_ok = source.startswith("live")
    src_note = "" if src_ok else f' · <b style="color:var(--red)">데이터 수집 실패 — 이전 값({html.escape(source)})</b>'

    # ① 결론
    kr_why = ""
    if snap["kr"]["below_ma"]:
        kr_why = f" (KOSPI 200일선 아래 −{cfg['korea']['below_ma_penalty_pct']}%p)"
    elif snap["kr"]["overheat"]:
        kr_why = " (KOSPI 과열)"
    hero = f"""<div class="card verdict" style="border-color:{col}">
<div class="light" style="background:{col}"></div>
<div><div class="v-title">{snap['regime_ko']}</div>
<div class="v-nums">노출 상한 미국 <b>{snap['exposure_us']:.0f}%</b> · 국내 <b>{snap['exposure_kr']:.0f}%</b>{kr_why} · 나머지는 현금</div>
<div class="v-todo">오늘 할 일: {html.escape(vd['todo'])}</div></div></div>"""

    # ② 위험 판단
    ths = ""
    for t in snap["thermometers"]:
        c = "var(--red)" if t["on"] else "var(--green)"
        ths += (f'<div class="card th"><div class="n"><span class="dot" style="background:{c}"></span>{t["name"]} · {t["desc"]}</div>'
                f'<div class="v">{t["value"]}</div><div class="r">경고 기준: {html.escape(t["rule"])}</div></div>')
    risk = f'<div class="g3">{ths}</div><div class="next">👉 {html.escape(vd["next"])}</div>'

    # ③ 주요 종목
    cap = rules["position_weight_cap_pct"]
    srows = ""
    for r in stocks:
        if not r["ok"]:
            srows += f'<tr><td>{html.escape(r["name"])}</td><td colspan="4" class="mut">{r.get("note", "")}</td></tr>'
            continue
        trend = '<span class="tag t-g">위</span>' if r["ma_dist"] >= 0 else '<span class="tag t-r">아래</span>'
        vt = {"낮음": "t-g", "중간": "t-y", "높음": "t-r"}[r["vol_lv"]]
        w = r["rec"]
        srows += (f'<tr><td>{html.escape(r["name"])}</td>'
                  f'<td><span class="bar hm" style="width:{w / cap * 50:.0f}px"></span><b>{w:.0f}%</b></td>'
                  f'<td>{trend} <span class="mut hm">{_pct(r["ma_dist"], 0)}</span></td>'
                  f'<td><span class="tag {vt}">{r["vol_lv"]}</span> <span class="mut hm">{r["vol"] * 100:.0f}%</span></td>'
                  f'<td class="hm {_cls(r["chg20"])}">{_pct(r["chg20"])}</td></tr>')
    stock_html = f"""<div class="card tbl"><table><thead><tr><th>종목</th><th>최대 비중</th><th>200일선</th><th>변동성(연)</th><th class="hm">20일</th></tr></thead>
<tbody>{srows}</tbody></table></div>
<div class="note">권장 최대 비중 = 켈리 비중의 절반(변동성 높음이면 1/4), 종목당 상한 {cap}%로 자름. 과거 {cfg['stocks']['years']}년 수익률로 추정해 흔들림이 크므로 <b>"이보다 많이 사지 않는다"는 상한</b>으로만 씁니다. 200일선 아래 종목은 1층 손절 {rules['position_stop_pct']}%를 먼저 확인하세요.</div>"""

    # ④ 시장 한눈에
    sec_html = ""
    for grp, lst in sectors.items():
        if not lst:
            continue
        top, bot = lst[:3], lst[-3:][::-1]
        rows = "".join(f'<div class="row"><span>▲ {x["name"]}</span><span class="{_cls(x["chg"])}">{_pct(x["chg"])}</span></div>' for x in top)
        rows += "".join(f'<div class="row"><span>▼ {x["name"]}</span><span class="{_cls(x["chg"])}">{_pct(x["chg"])}</span></div>' for x in bot)
        sec_html += f'<div class="card sec"><h3>{grp} 섹터 · 20일 강한 3 / 약한 3</h3>{rows}</div>'

    def last(k, f="{:,.0f}"):
        if k not in m.columns or m[k].dropna().empty:
            return None, None
        s = m[k].dropna()
        c = s.iloc[-1] / s.iloc[-21] - 1 if len(s) > 21 else None
        return f.format(s.iloc[-1]), c

    strip = ""
    for k, name, f in [("usdkrw", "원/달러", "{:,.0f}"), ("tnx", "미 10년물", "{:.2f}%"), ("gold", "금", "{:,.0f}"),
                       ("oil", "WTI", "{:.1f}"), ("kospi", "KOSPI", "{:,.0f}"), ("ndx", "나스닥100", "{:,.0f}")]:
        v, c = last(k, f)
        if v:
            strip += f'<span>{name} <b>{v}</b> <span class="{_cls(c)}">{_pct(c)}</span></span>'
    market = f'<div class="g2">{sec_html}</div><div class="card strip">{strip}<span class="mut">옆 숫자 = 20일 변화</span></div>'

    # 자세히: 참고 지표
    ref_rows = ""
    for item in cfg["reference"]:
        k = item["key"]
        if k not in m.columns:
            continue
        s = m[k].dropna()
        if len(s) < 22:
            continue
        lv = float(s.iloc[-1])
        d20 = s.iloc[-1] / s.iloc[-21] - 1
        ma = s.rolling(200).mean().iloc[-1] if len(s) >= 200 else None
        dma = lv / ma - 1 if ma and pd.notna(ma) else None
        fm = f"{lv:,.2f}" if lv < 1000 else f"{lv:,.0f}"
        ref_rows += f"<tr><td>{item['name']}</td><td>{fm}</td><td class='{_cls(d20)}'>{_pct(d20)}</td><td class='{_cls(dma)}'>{_pct(dma)}</td></tr>"
    ka, bd = snap["kr_aux"], snap["breadth"]
    adr_t = "–" if ka["adr"] is None else f"{ka['adr']:.0f}"
    spr_t = "–" if ka["spread"] is None else f"{ka['spread']:.2f}%p"
    mv = snap["aux"]["move"]
    move_t = "–" if mv is None else f"{mv:.0f}"
    aux_rows = (f"<tr><td>시장 폭 (동일가중/시총가중 S&amp;P 60일)</td><td>{_pct(bd['rsp_spy_chg'])}</td></tr>"
                f"<tr><td>국내 시장 폭 대용 (KOSDAQ/KOSPI 60일)</td><td>{_pct(ka['kq_ks_chg'])}</td></tr>"
                f"<tr><td>국내 등락비율 ADR (20일)</td><td>{adr_t}</td></tr>"
                f"<tr><td>국내 신용 스프레드 (회사채 AA- − 국고채 3년)</td><td>{spr_t}</td></tr>"
                f"<tr><td>MOVE (채권 변동성)</td><td>{move_t}</td></tr>")

    hist_rows = ""
    for d, r in res.tail(30).iloc[::-1].iterrows():
        hist_rows += (f"<tr><td>{d.strftime('%m-%d')}</td><td style='text-align:left;color:{engine.REGIME_COLOR[r['regime']]}'>{engine.REGIME_KO[r['regime']]}</td>"
                      f"<td>{r['exposure_us']:.0f}% / {r['exposure_kr']:.0f}%</td><td>{int(r['score_raw'])}</td>"
                      f"<td>{r['vix']:.1f}</td><td>{r['hy']:.2f}</td><td>{r['spx_dist'] * 100:+.1f}%</td></tr>")

    links = " · ".join(f'<a href="{html.escape(x["url"])}" target="_blank" rel="noopener">{html.escape(x["name"])}</a>'
                       for x in cfg.get("links", []))

    acct = ""
    if account and account.get("equity"):
        eq = float(account["equity"])
        peak = float(account.get("peak") or eq)
        dd = eq / peak - 1
        hard, soft = -rules["account_drawdown_stop_pct"] / 100, -rules["account_soft_drawdown_pct"] / 100
        st = ("손실 한도 도달 — 신규 진입 중단·전면 재점검" if dd <= hard else
              "경고선 — 신규 위험 절반" if dd <= soft else "정상")
        acct = (f'<h2>내 계좌 (이 PC 전용)</h2><div class="card">총액 <b>{eq:,.0f}</b> · 고점 대비 <b class="{_cls(dd)}">{_pct(dd)}</b> → {st}. '
                f'목표 노출 {eq * snap["exposure_us"] / 100:,.0f} · 종목당 상한 {eq * cap / 100:,.0f}</div>')

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>투자 대시보드 {snap['date']}</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><h1>투자 대시보드</h1><div class="sub">{snap['date']} 미국 종가 기준 · {now} 갱신{src_note}</div></div>
{hero}
{acct}
<h2>위험 판단 — 미국 온도계 3개가 국면을 정합니다</h2>
{risk}
<h2>주요 종목</h2>
{stock_html}
<h2>시장 한눈에</h2>
{market}
<details><summary>자세히 보기 (참고 지표 · 최근 30일 · 규칙 · 바로가기)</summary>
<h3>참고 지표 — 국면을 바꾸지 않음</h3>
<div class="card tbl"><table><thead><tr><th>지표</th><th>현재</th><th>20일</th><th>200일선 대비</th></tr></thead><tbody>{ref_rows}</tbody></table></div>
<div class="card tbl" style="margin-top:10px"><table><tbody>{aux_rows}</tbody></table></div>
<div class="note">시장 폭·국내 지표·MOVE는 과거 검증에서 앞서 알려주는 힘이 없어 숫자로만 둡니다.</div>
<h3>최근 30일 판정</h3>
<div class="card tbl"><table><thead><tr><th>날짜</th><th style="text-align:left">국면</th><th>상한 미/국내</th><th>경고 수</th><th>VIX</th><th>신용</th><th>S&amp;P vs 200일</th></tr></thead><tbody>{hist_rows}</tbody></table></div>
<h3>1층 생존 규칙</h3>
<div class="card"><ul>
<li>종목마다 고정 손절 {rules['position_stop_pct']}% — 충격이라고 넓히지 않는다</li>
<li>종목당 비중 상한 {cap}%</li>
<li>계좌 고점 대비 −{rules['account_drawdown_stop_pct']}%면 신규 진입 중단·전면 재점검 (경고선 −{rules['account_soft_drawdown_pct']}%)</li>
<li>국면별 총 노출 상한: 과열 {ex['overheated']}% · 일반 상승 {ex['uptrend']}% · 충격 {ex['shock']}% · 안정화 최대 {ex['stabilized_max']}%</li>
<li>고위험 몫(비트코인·성장주 ETF만): 계좌의 5% 상한, 하락 시 3번에 나눠 진입, 손절 없음</li>
<li>현금 = 만기 2년 이하 채권·MMF만</li>
<li>규칙 변경은 {rules['rule_change_cooldown_days']}일 간격, 손실 중에는 금지</li>
</ul></div>
<h3>바로가기</h3><div class="card">{links}</div>
</details>
<div class="foot">판단 보조용입니다. 매수·매도 결정은 본인이 합니다. · github.com/sebals38/InvestmentGuide</div>
</div></body></html>"""
