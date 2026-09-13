#!/usr/bin/env python3
"""
backtest.py — 국면 엔진을 과거 전체에 돌려 "상식적으로 움직였나" 3가지를 점검

  점검 1. 위기 전/초기에 노출을 줄였는가   (고점 → 충격 판정까지 며칠, 그때 S&P 낙폭은?)
  점검 2. 바닥 근처에서 다시 늘렸는가       (바닥 시점 노출, 바닥 후 60거래일 노출)
  점검 3. 왔다갔다 하지 않았는가           (국면 전환 횟수/년, 10일 미만 짧은 충격 횟수)

수익률 최적화는 목적이 아닙니다. 기준을 바꿔 성과를 맞추는 순간 규칙은 과거에 과적합됩니다.

사용법:
    python backtest.py            # data/market.csv 캐시 (없으면 수집) → report/backtest.md + .png
    python backtest.py --refresh  # 강제 재수집
    python backtest.py --demo     # 합성 데이터로 스크립트 점검
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import yaml

import data as datamod
import engine

HERE = os.path.dirname(os.path.abspath(__file__))


def _p(path):
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def _nearest(idx: pd.DatetimeIndex, date: str) -> pd.Timestamp:
    ts = pd.Timestamp(date)
    pos = idx.searchsorted(ts)
    return idx[min(pos, len(idx) - 1)]


def episode_report(res: pd.DataFrame, ep: dict, cfg: dict) -> dict:
    idx = res.index
    peak = _nearest(idx, ep["peak"])
    bottom = _nearest(idx, ep["bottom"])
    seg = res.loc[peak:bottom]
    spx = res["spx"]

    # 점검 1: 고점 이후 처음 충격 판정
    shock_days = seg.index[seg["regime"] == "shock"]
    if len(shock_days):
        first = shock_days[0]
        lag = int(idx.get_loc(first) - idx.get_loc(peak))
        dd_at_shock = spx.loc[first] / spx.loc[peak] - 1
    else:
        first, lag, dd_at_shock = None, None, None
    # 충격 이전(고점 시점) 노출 vs 구간 최소 노출
    expo_peak = float(res.loc[peak, "exposure_us"])
    expo_min = float(seg["exposure_us"].min())
    expo_min_day = seg["exposure_us"].idxmin()

    # 점검 2: 바닥 시점과 바닥 후 60일 노출
    b_pos = idx.get_loc(bottom)
    after = idx[min(b_pos + 60, len(idx) - 1)]
    expo_bottom = float(res.loc[bottom, "exposure_us"])
    expo_after = float(res.loc[after, "exposure_us"])
    regime_after = res.loc[after, "regime"]
    max_dd = float((spx.loc[peak:bottom] / spx.loc[peak] - 1).min())

    # 평균 노출로 본 "이 구간에서 얼마나 맞았나" (단순 참고: 노출×S&P 일수익)
    ret = spx.pct_change()
    strat = (res["exposure_us"].shift(1) / 100 * ret).loc[peak:bottom]
    strat_dd = float((1 + strat).cumprod().min() - 1)

    return {
        "name": ep["name"], "peak": peak.date(), "bottom": bottom.date(), "spx_dd": max_dd,
        "expo_peak": expo_peak, "first_shock": first.date() if first is not None else None,
        "lag_days": lag, "dd_at_shock": dd_at_shock, "expo_min": expo_min, "expo_min_day": expo_min_day.date(),
        "expo_bottom": expo_bottom, "expo_after60": expo_after, "regime_after60": regime_after,
        "strat_dd": strat_dd,
    }


def flipflop_report(res: pd.DataFrame, cfg: dict) -> dict:
    reg = res["regime"]
    change = reg != reg.shift()
    n_changes = int(change.sum() - 1)
    years = (res.index[-1] - res.index[0]).days / 365.25
    # 충격 에피소드 길이
    runs = []
    cur, start, prev = None, None, None
    for d, r in reg.items():
        if r != cur:
            if cur == "shock":
                runs.append((start, prev, int(reg.loc[start:prev].shape[0])))
            cur, start = r, d
        prev = d
    if cur == "shock":
        runs.append((start, prev, int(reg.loc[start:prev].shape[0])))
    short = [r for r in runs if r[2] < int(cfg["backtest"]["short_shock_days"])]

    # 동결 에피소드: 시작 후 followup 일 안에 충격이 왔는지
    fz = res["freeze"]
    follow = int(cfg["backtest"]["freeze_followup_days"])
    freezes = []
    i = 0
    vals = fz.to_numpy()
    while i < len(vals):
        if vals[i] == 1 and (i == 0 or vals[i - 1] == 0):
            j = i
            while j + 1 < len(vals) and vals[j + 1] == 1:
                j += 1
            already = reg.iloc[i] == "shock"
            window = reg.iloc[i:min(i + follow, len(vals))]
            led = bool((window == "shock").any()) and not already
            freezes.append((res.index[i], res.index[j], j - i + 1, "이미 충격" if already else ("충격으로 이어짐" if led else "해제(경보만)")))
            i = j + 1
        else:
            i += 1
    return {
        "freezes": freezes,
        "years": years, "changes": n_changes, "changes_per_year": n_changes / years if years else float("nan"),
        "shock_runs": runs, "short_shocks": short,
        "share": reg.value_counts(normalize=True).to_dict(),
        "avg_exposure": float(res["exposure_us"].mean()),
    }


def breadth_report(res: pd.DataFrame, cfg: dict) -> dict:
    """시장 폭 경고가 앞섰는지: 켜진 날 뒤 lead 일 안 충격 확률 vs 기저율 (참고용. 2026-09 검증에서 22% vs 19%)"""
    br = cfg.get("breadth", {})
    lead = int(br.get("lead_days", 60))
    idx = res.index
    is_shock = (res["regime"].to_numpy() == "shock").astype(int)
    fut = pd.Series(is_shock[::-1], index=idx[::-1]).rolling(lead, min_periods=1).max()[::-1].shift(-1).fillna(0)
    not_in_shock = res["regime"] != "shock"
    base = float(fut[not_in_shock].mean())
    sel = not_in_shock & (res["breadth_warn"] == 1)
    n = int(sel.sum())
    eps = []
    for ep in cfg["backtest"]["episodes"]:
        pk = _nearest(idx, ep["peak"])
        if pk < idx[0]:
            continue
        pos = idx.get_loc(pk)
        win = res.iloc[max(0, pos - lead):pos + 1]
        eps.append((ep["name"], int(win["breadth_warn"].max())))
    return {"base": base, "n": n, "p": float(fut[sel].mean()) if n else float("nan"), "episodes": eps, "lead": lead}


def plot(res: pd.DataFrame, cfg: dict, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["axes.unicode_minus"] = False  # 그림 라벨은 영문 (Actions 러너에 한글 폰트 없음)

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True, gridspec_kw={"height_ratios": [3, 1.4, 1.6]})
    ax = axes[0]
    ax.plot(res.index, res["spx"], color="#111", lw=0.9, label="S&P 500")
    ax.plot(res.index, res["spx_ma200"], color="#888", lw=0.8, ls="--", label="200DMA")
    ax.set_yscale("log")
    # 국면 배경색
    reg = res["regime"]
    start = res.index[0]
    for i in range(1, len(res) + 1):
        if i == len(res) or reg.iloc[i] != reg.iloc[i - 1]:
            end = res.index[i - 1] if i == len(res) else res.index[i]
            ax.axvspan(start, end, color=engine.REGIME_COLOR[reg.iloc[i - 1]], alpha=0.13, lw=0)
            if i < len(res):
                start = res.index[i]
    for ep in cfg["backtest"]["episodes"]:
        for key in ("peak", "bottom"):
            t = pd.Timestamp(ep[key])
            if res.index[0] <= t <= res.index[-1]:
                ax.axvline(t, color="#999", lw=0.6, ls=":")
    from matplotlib.patches import Patch
    en = {"overheated": "Overheated", "uptrend": "Uptrend", "shock": "Shock", "stabilized": "Stabilized"}
    handles = [Patch(color=engine.REGIME_COLOR[k], alpha=0.4, label=en[k]) for k in engine.REGIMES]
    ax.legend(handles=handles + ax.get_legend_handles_labels()[0], loc="upper left", fontsize=8, ncol=3)
    ax.set_title("Regime engine v%s — S&P 500 with regime bands" % cfg["version"])

    ax = axes[1]
    ax.step(res.index, res["exposure_us"], where="post", color="#111", lw=1, label="Target exposure US (%)")
    ax.step(res.index, res["exposure_kr"], where="post", color="#2563eb", lw=0.8, alpha=0.7, label="Target exposure KR (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper left", fontsize=8)

    ax = axes[2]
    ax.plot(res.index, res["vix"], color="#dc2626", lw=0.7, label="VIX")
    ax.axhline(cfg["thermometers"]["fear"]["threshold"], color="#dc2626", lw=0.5, ls=":")
    ax.set_ylabel("VIX")
    ax2 = ax.twinx()
    ax2.plot(res.index, res["hy"], color="#7c3aed", lw=0.7, label="Credit spread (%)")
    ax2.axhline(cfg["thermometers"]["credit"]["threshold"], color="#7c3aed", lw=0.5, ls=":")
    ax2.set_ylabel("Credit spread")
    ax.legend(loc="upper left", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def write_markdown(eps: list, ff: dict, res: pd.DataFrame, cfg: dict, path: str, source: str, warns=()):
    L = []
    L.append(f"# 백테스트 점검 — 엔진 v{cfg['version']}")
    hy_first = res["hy"].first_valid_index()
    csrc = cfg["thermometers"]["credit"].get("source", "hy")
    L.append(f"기간 {res.index[0].date()} ~ {res.index[-1].date()} ({ff['years']:.1f}년) · 데이터: {source} · "
             f"신용 스프레드({csrc}) 커버리지 {res['hy'].notna().mean()*100:.0f}% (첫 값 {hy_first.date() if hy_first is not None else '없음'})\n")
    for w in (warns or []):
        L.append(f"> ⚠️ {w}\n")
    L.append("목적은 수익률이 아니라 **규칙이 상식적으로 움직였는지** 확인하는 것입니다.\n")

    L.append("## 점검 1·2. 위기별: 언제 줄였고, 언제 다시 늘렸나\n")
    L.append("| 위기 | 고점 | S&P 최대낙폭 | 고점 시 노출 | 첫 충격 판정 | 고점→충격 (일) | 충격 시 S&P 낙폭 | 구간 최소 노출 | 바닥 | 바닥 시 노출 | 바닥+60일 노출(국면) | 노출 반영 낙폭 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for e in eps:
        dd_shock = "" if e["dd_at_shock"] is None else "%.0f%%" % (e["dd_at_shock"] * 100)
        L.append(
            f"| {e['name']} | {e['peak']} | {e['spx_dd']*100:.0f}% | {e['expo_peak']:.0f}% | "
            f"{e['first_shock'] or '없음'} | {e['lag_days'] if e['lag_days'] is not None else '-'} | "
            f"{dd_shock} | "
            f"{e['expo_min']:.0f}% ({e['expo_min_day']}) | {e['bottom']} | {e['expo_bottom']:.0f}% | "
            f"{e['expo_after60']:.0f}% ({engine.REGIME_KO[e['regime_after60']]}) | {e['strat_dd']*100:.0f}% |")
    L.append("")
    L.append("읽는 법: '고점→충격'이 짧고 '충격 시 낙폭'이 작을수록 일찍 줄인 것. "
             "'바닥+60일 노출'이 바닥 시점보다 높으면 바닥 근처에서 다시 늘린 것. "
             "'노출 반영 낙폭'은 목표 노출대로 S&P를 들고 있었을 때의 구간 낙폭(단순 참고).\n")

    L.append("## 점검 3. 왔다갔다\n")
    L.append(f"- 국면 전환 {ff['changes']}회 / {ff['years']:.1f}년 = **연 {ff['changes_per_year']:.1f}회**")
    L.append(f"- 충격 판정 {len(ff['shock_runs'])}회, 그중 {cfg['backtest']['short_shock_days']}일 미만 짧은 충격 **{len(ff['short_shocks'])}회**")
    for s in ff["short_shocks"]:
        L.append(f"  - {s[0].date()} ~ {s[1].date()} ({s[2]}일)")
    L.append("- 국면 비중: " + " · ".join(f"{engine.REGIME_KO[k]} {v*100:.0f}%" for k, v in ff["share"].items()))
    L.append(f"- 평균 목표 노출 {ff['avg_exposure']:.0f}%\n")

    fzs = ff["freezes"]
    n_led = sum(1 for x in fzs if x[3] == "충격으로 이어짐")
    n_false = sum(1 for x in fzs if x[3] == "해제(경보만)")
    n_in = sum(1 for x in fzs if x[3] == "이미 충격")
    L.append("## 점검 4. 동결(VIX 급등 경보)은 얼마나 맞았나\n")
    L.append(f"- 동결 {len(fzs)}회: {cfg['backtest']['freeze_followup_days']}일 안에 충격으로 이어짐 **{n_led}회**, 경보만으로 끝남 **{n_false}회**, 이미 충격 중 {n_in}회")
    L.append("- 동결의 비용은 '며칠간 신규 매수 안 함' 뿐이므로, 경보만으로 끝난 횟수가 많아도 규칙을 바꿀 이유는 아닙니다.\n")
    L.append("| 동결 시작 | 끝 | 일수 | 결과 |")
    L.append("|---|---|---|---|")
    for x in fzs:
        L.append(f"| {x[0].date()} | {x[1].date()} | {x[2]} | {x[3]} |")
    L.append("")

    bg = ff.get("breadth")
    if bg:
        L.append("## 점검 5. 시장 폭 경고(참고)는 앞섰나\n")
        hit = [e[0] for e in bg["episodes"] if e[1] == 1]
        L.append(f"- 위기 고점 전 {bg['lead']}일 안에 켜진 위기: {', '.join(hit) if hit else '없음'} ({len(hit)}/{len(bg['episodes'])})")
        L.append(f"- 기저율 {bg['base']*100:.0f}% vs 경고 켜진 날({bg['n']}일) {bg['p']*100:.0f}%"
                 if bg["n"] else f"- 경고 켜진 날 없음 (기저율 {bg['base']*100:.0f}%)")
        L.append("- 기저율과 비슷하면 행동 근거가 아님. 2026-09 검증: 신용 엇갈림·금리 역전은 차이 없어 제거, 시장 폭만 참고로 유지.\n")
    L.append("## 충격 판정 전체 목록\n")
    L.append("| 시작 | 끝 | 일수 |")
    L.append("|---|---|---|")
    for s in ff["shock_runs"]:
        L.append(f"| {s[0].date()} | {s[1].date()} | {s[2]} |")
    L.append("")
    L.append(f"![backtest]({os.path.basename(cfg['output']['backtest_png'])})\n")
    L.append("## 다음 판단거리\n")
    L.append("1. 짧은 충격이 많으면 → min_shock_days 나 smoothing_days 를 늘린다 (단, 빠른 위기 대응은 늦어진다).")
    L.append("2. 충격이 늦으면 → shock_score(1.5) 나 HY 기준(5.0)을 낮춘다. VIX 급등은 동결(경보)만 걸고 국면은 바꾸지 않는다.")
    L.append("3. 바닥 후 노출 회복이 느리면 → ladder_days_per_step 을 줄인다.")
    L.append("4. 기준을 바꿨으면 CHANGELOG.md 에 이유를 적고 다시 돌려 세 점검을 반복한다.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="캐시 대신 재수집")
    ap.add_argument("--allow-partial", action="store_true", help="핵심 데이터가 부족해도 결과를 만든다")
    args = ap.parse_args(argv)

    with open(_p("config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    m, source = datamod.load_market(cfg, demo=args.demo, refresh=args.refresh)
    warns = datamod.check_coverage(m, datamod.core_cols(cfg), verbose=False) if not args.demo else []
    if warns and not args.allow_partial:
        print("[중단] 데이터가 불완전해 백테스트를 만들지 않습니다. `python backtest.py --refresh` 로 다시 받거나, "
              "그래도 보려면 --allow-partial 을 붙이세요.", file=sys.stderr)
        return 2
    res = engine.run(m, cfg)
    res = res[res.index >= pd.Timestamp(cfg["backtest"]["start"])]

    eps = []
    for ep in cfg["backtest"]["episodes"]:
        if pd.Timestamp(ep["peak"]) < res.index[0] or pd.Timestamp(ep["bottom"]) > res.index[-1]:
            continue
        eps.append(episode_report(res, ep, cfg))
    ff = flipflop_report(res, cfg)
    ff["breadth"] = breadth_report(res, cfg)

    png = _p(cfg["output"]["backtest_png"])
    md = _p(cfg["output"]["backtest_md"])
    plot(res, cfg, png)
    write_markdown(eps, ff, res, cfg, md, source, warns)
    res.to_csv(_p("report/backtest_daily.csv"), float_format="%.4f")

    print(open(md, encoding="utf-8").read())
    print(f"\n→ {md}\n→ {png}\n→ report/backtest_daily.csv (날짜별 판정 전체)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
