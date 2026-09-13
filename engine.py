"""
engine.py — 국면 판정 엔진 v0.2 (순수 계산, 네트워크 없음)

입력:  시장 DataFrame (index=날짜, columns: spx, vix, hy, kospi, usdkrw, [move ...])
출력:  날짜별 온도계 점수·국면·목표 노출이 담긴 DataFrame

dashboard.py(오늘 판정)와 backtest.py(과거 전체)가 같은 함수를 씁니다.
→ 대시보드가 보여주는 숫자와 백테스트가 검증한 숫자가 항상 같습니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REGIMES = ["overheated", "uptrend", "shock", "stabilized"]
REGIME_KO = {
    "overheated": "과열 상승",
    "uptrend": "일반 상승",
    "shock": "충격 진행",
    "stabilized": "안정화 침체",
}
REGIME_COLOR = {
    "overheated": "#d97706",
    "uptrend": "#16a34a",
    "shock": "#dc2626",
    "stabilized": "#2563eb",
}


# ─────────────────────────── 1. 피처 ───────────────────────────
def build_features(m: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """온도계·안정화 조건·국내 조정에 필요한 파생값을 모두 계산한다."""
    rg = cfg["regime"]
    th = cfg["thermometers"]
    kr = cfg["korea"]
    sm = int(rg["smoothing_days"])

    f = pd.DataFrame(index=m.index)

    # 온도계 1: 추세 (S&P500 vs 200일선)
    ma = int(th["trend"]["ma_days"])
    f["spx"] = m["spx"]
    f["spx_ma200"] = m["spx"].rolling(ma).mean()
    f["spx_dist"] = m["spx"] / f["spx_ma200"] - 1.0
    f["spx_dist_s"] = f["spx_dist"].rolling(sm).mean()
    f["t_trend"] = (f["spx_dist"] < 0).astype(float)

    # 온도계 2: 공포 (VIX)
    f["vix"] = m["vix"]
    f["vix_ma20"] = m["vix"].rolling(20).mean()
    f["t_fear"] = (m["vix"] >= float(th["fear"]["threshold"])).astype(float)
    # 동결: VIX 가 freeze_vix 를 넘긴 날부터 freeze_days 동안
    fz_days = int(rg.get("freeze_days", 5))
    f["vix_spike"] = (m["vix"] > float(rg.get("freeze_vix", 30))).astype(float)
    f["freeze"] = f["vix_spike"].rolling(fz_days, min_periods=1).max()

    # 온도계 3: 신용 스프레드 (원천은 config thermometers.credit.source — baa10y 또는 hy). FRED 결측은 ffill
    csrc = th["credit"].get("source", "hy")
    if csrc not in m:
        raise KeyError(f"신용 온도계 원천 '{csrc}' 열이 데이터에 없습니다 (sources.fred 확인)")
    hy = m[csrc].ffill()
    f["hy"] = hy                                        # 열 이름은 호환을 위해 hy 로 유지 (= 신용 스프레드)
    f["hy_ma20"] = hy.rolling(20).mean()
    f["hy_missing"] = hy.isna().astype(float)          # 데이터가 없는 날 (온도계 2개로만 판정)
    f["t_credit"] = (hy >= float(th["credit"]["threshold"])).astype(float)

    # 점수 0~3 과 5일 스무딩
    f["score_raw"] = f["t_trend"] + f["t_fear"] + f["t_credit"]
    f["score_s"] = f["score_raw"].rolling(sm).mean()

    # 안정화 조건 3개
    pk_days = int(rg.get("vix_peak_days", 60))
    off = float(rg.get("vix_off_peak_pct", 40)) / 100
    vix_peak = m["vix"].rolling(pk_days, min_periods=5).max()
    f["vix_peak60"] = vix_peak
    f["c_vix_below_25"] = ((m["vix"] < float(th["fear"]["threshold"])) | (m["vix"] <= vix_peak * (1 - off))).astype(float)
    calm = rg.get("calm_override", {})
    vix_calm = float(calm.get("vix", 0))
    hy_calm = float(calm.get("credit", calm.get("hy", 0)))
    f["c_vix_falling"] = ((m["vix"] < f["vix_ma20"]) | (m["vix"] < vix_calm)).astype(float)
    f["c_hy_narrowing"] = ((hy < f["hy_ma20"]) | (hy < hy_calm) | hy.isna()).astype(float)  # 결측이면 판정에서 제외
    f["stab_raw"] = f["c_vix_below_25"] + f["c_vix_falling"] + f["c_hy_narrowing"]
    f["stab_s"] = f["stab_raw"].rolling(sm).mean()
    f["stab_n"] = f["stab_s"].round().clip(0, 3)  # 5일 평균을 반올림한 "확인된 조건 개수"

    # 국내: KOSPI 200일선, 환율
    if "kospi" in m:
        kma = int(kr["kospi_ma_days"])
        f["kospi"] = m["kospi"]
        f["kospi_ma200"] = m["kospi"].rolling(kma).mean()
        f["kospi_dist"] = m["kospi"] / f["kospi_ma200"] - 1.0
        f["kospi_dist_s"] = f["kospi_dist"].rolling(sm).mean()
        f["kr_below_ma"] = (f["kospi_dist_s"] < 0).astype(float)
        f["kr_overheat"] = (f["kospi_dist_s"] >= float(kr["overheat_pct"]) / 100).astype(float)
    else:
        f["kr_below_ma"] = 0.0
        f["kr_overheat"] = 0.0

    if "usdkrw" in m:
        fx = kr["fx"]
        d = int(fx["change_days"])
        f["usdkrw"] = m["usdkrw"]
        f["fx_chg"] = m["usdkrw"].pct_change(d)
        fx_ma = m["usdkrw"].rolling(200).mean()
        f["fx_warn"] = ((f["fx_chg"] >= float(fx["warn_change_pct"]) / 100) & (m["usdkrw"] > fx_ma)).astype(float)
    else:
        f["fx_warn"] = 0.0

    # 보조: MOVE
    aux = cfg["auxiliary"]
    if "move" in m:
        f["move"] = m["move"].ffill()
        f["move_warn"] = (f["move"] >= float(aux["move_warn"])).astype(float)
        f["move_extreme"] = (f["move"] >= float(aux["move_extreme"])).astype(float)
    else:
        f["move"] = np.nan
        f["move_warn"] = 0.0
        f["move_extreme"] = 0.0

    # 시장 폭 (참고 전용)
    br = cfg.get("breadth")
    f["breadth_warn"] = 0.0
    f["rsp_spy_chg"] = np.nan
    f["near_high"] = 0.0
    if br and "rsp" in m and "spy" in m:
        near = (m["spx"] >= m["spx"].rolling(252, min_periods=60).max() * (1 - float(br["near_high_pct"]) / 100))
        ratio = (m["rsp"] / m["spy"]).ffill()
        chg = ratio.pct_change(int(br["ratio_days"]))
        f["rsp_spy_chg"] = chg
        f["near_high"] = near.astype(float)
        f["breadth_warn"] = (near & (chg <= -float(br["ratio_drop_pct"]) / 100)).astype(float)
    if "t10y2y" in m:
        f["t10y2y"] = m["t10y2y"].ffill()
    else:
        f["t10y2y"] = np.nan

    # 국내 참고 지표 (참고 전용)
    ka = cfg.get("korea_aux")
    f["kr_breadth_warn"] = 0.0
    f["kq_ks_chg"] = np.nan
    f["kr_spread"] = np.nan
    f["kr_spread_warn"] = 0.0
    f["kr_spread_widen"] = np.nan
    f["kr_adr"] = np.nan
    if ka:
        if "kospi" in m and "kosdaq" in m:
            b = ka["breadth"]
            near = (m["kospi"] >= m["kospi"].rolling(252, min_periods=60).max() * (1 - float(b["near_high_pct"]) / 100))
            ratio = (m["kosdaq"] / m["kospi"]).ffill()
            chg = ratio.pct_change(int(b["ratio_days"]))
            f["kq_ks_chg"] = chg
            f["kr_breadth_warn"] = (near & (chg <= -float(b["ratio_drop_pct"]) / 100)).astype(float)
        if "kr_corp_aa" in m and "kr_govt3" in m:
            c = ka["credit"]
            sp = (m["kr_corp_aa"] - m["kr_govt3"]).ffill()
            f["kr_spread"] = sp
            f["kr_spread_widen"] = sp.diff(int(c["widen_days"]))
            f["kr_spread_warn"] = ((sp >= float(c["warn_pp"])) | (f["kr_spread_widen"] >= float(c["widen_pp"]))).astype(float)
        if "kr_adv" in m and "kr_dec" in m:
            w = int(ka["adr"]["window"])
            f["kr_adr"] = m["kr_adv"].rolling(w).sum() / m["kr_dec"].rolling(w).sum().replace(0, np.nan) * 100

    return f


# ─────────────────────────── 2. 국면 상태기계 ───────────────────────────
def run_regime(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """피처 → 날짜별 국면·노출. 상태(이전 국면, 사다리 일수)가 있어서 루프로 돈다."""
    rg = cfg["regime"]
    ex = cfg["exposure"]
    kr = cfg["korea"]

    shock_score = float(rg["shock_score"])
    min_shock = int(rg.get("min_shock_days", 0))
    up_interval = int(rg.get("ladder_up_interval_days", 1))
    oh_in = float(rg["overheat_in_pct"]) / 100
    oh_out = float(rg["overheat_out_pct"]) / 100
    ret_score = float(rg["uptrend_return_score"])
    restrike = int(rg.get("restrike_stab_max", 1))
    step = float(rg["ladder_step_pct"])
    days_per_step = int(rg["ladder_days_per_step"])
    cut = float(ex["uptrend_warning_cut_pct"])

    valid = f["score_s"].notna() & f["spx_dist_s"].notna() & f["stab_s"].notna()
    idx = f.index[valid]

    regimes, exposures, ladder_days, ladder_steps, shock_age = [], [], [], [], []
    state = None
    days_all = 0       # 안정화 조건 3개가 모두 유지된 연속 일수
    in_shock = 0       # 현재 충격에 머문 일수
    cur_step = 0       # 실제 적용 중인 사다리 칸 (목표 칸을 천천히 따라감)
    since_up = 999     # 마지막으로 사다리를 올린 뒤 지난 일수

    for d in idx:
        r = f.loc[d]
        score_s, dist_s, stab_n = r["score_s"], r["spx_dist_s"], int(r["stab_n"])

        # 초기 상태
        if state is None:
            if score_s >= shock_score:
                state = "shock"
            elif dist_s >= oh_in:
                state = "overheated"
            elif dist_s >= 0:
                state = "uptrend"
            else:
                state = "stabilized"

        # (1) 국면 전이. VIX 급등은 국면이 아니라 '동결'만 건다
        if state == "shock":
            # 충격에서 나가는 길은 안정화 조건뿐 (온도계 점수는 긴 약세장에서 바닥 후 몇 달간 켜져 있으므로 재트리거 안 함)
            new = "stabilized" if (stab_n == 3 and in_shock >= min_shock) else "shock"
        elif state == "stabilized":
            if stab_n <= restrike and dist_s < 0:        # 안정화 조건이 무너지고 가격도 200일선 아래 → 충격 복귀
                new = "shock"
            elif score_s < ret_score and dist_s > 0:
                new = "uptrend"
            else:
                new = "stabilized"
        elif score_s >= shock_score:                     # 상승 국면에서 온도계 2개 이상 지속 → 충격
            new = "shock"
        elif state == "overheated":
            new = "uptrend" if dist_s < oh_out else "overheated"
        else:  # uptrend
            new = "overheated" if dist_s >= oh_in else "uptrend"

        in_shock = in_shock + 1 if new == "shock" else 0

        # (2) 사다리 일수: 안정화 국면에 연속으로 머문 일수 (충격으로 되돌아가면 0)
        days_all = days_all + 1 if new == "stabilized" else 0

        # (3) 목표 노출
        if new == "overheated":
            expo = float(ex["overheated"])
            lstep = 0
        elif new == "uptrend":
            warn = int(round(score_s))
            expo = max(float(ex["uptrend"]) - cut * warn, float(ex["shock"]))
            lstep = 0
        else:
            # 목표 칸: 충격 = 확인된 안정화 조건 수(0~3), 안정화 = 3 + 안정화에 머문 10일마다 1칸
            target = stab_n if new == "shock" else 3 + days_all // days_per_step
            # 실제 칸은 up_interval 일에 한 칸만 올라가고, 내려갈 땐 즉시
            since_up += 1
            if target < cur_step:
                cur_step = target
            elif target > cur_step and since_up >= up_interval:
                cur_step += 1
                since_up = 0
            lstep = cur_step
            cap = float(ex["stabilized_max"])
            if new == "stabilized" and dist_s < 0:
                cap = min(cap, float(ex.get("stabilized_below_ma_max", cap)))
            expo = min(float(ex["shock"]) + step * lstep, cap)
        if new in ("overheated", "uptrend"):
            cur_step, since_up = 0, 999
        # 동결 중에는 노출 상한을 올리지 않는다 (상승 국면에서만. 충격·안정화에서는 사다리 규칙이 우선)
        if r["freeze"] == 1 and new in ("overheated", "uptrend") and exposures and expo > exposures[-1]:
            expo = exposures[-1]

        regimes.append(new)
        exposures.append(expo)
        ladder_days.append(days_all)
        ladder_steps.append(lstep)
        shock_age.append(in_shock)
        state = new

    out = f.loc[idx].copy()
    out["regime"] = regimes
    out["exposure_us"] = exposures
    out["ladder_days"] = ladder_days
    out["ladder_step"] = ladder_steps
    out["shock_age"] = shock_age
    out["no_new_buys"] = ((out["freeze"] == 1) | (out["regime"] == "shock")).astype(float)

    # 국내 노출: 국면은 미국이 정하고 KOSPI 가 조정
    kr_expo = out["exposure_us"].copy()
    kr_expo = kr_expo - float(kr["below_ma_penalty_pct"]) * out["kr_below_ma"]
    kr_expo = kr_expo - float(kr["fx"]["penalty_pct"]) * out["fx_warn"]
    kr_expo = np.where(out["kr_overheat"] == 1, np.minimum(kr_expo, float(ex["overheated"])), kr_expo)
    out["exposure_kr"] = np.clip(kr_expo, 10, 100)

    out["trading_bucket"] = np.where((out["regime"] == "shock") | (out["freeze"] == 1),
                                     float(ex["trading_bucket"]["shock"]),
                                     float(ex["trading_bucket"]["normal"]))
    return out


def run(m: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    return run_regime(build_features(m, cfg), cfg)


# ─────────────────────────── 3. 오늘 스냅샷 (대시보드용) ───────────────────────────
def snapshot(res: pd.DataFrame, cfg: dict) -> dict:
    """마지막 날의 판정을 사람이 읽을 수 있는 dict 로."""
    r = res.iloc[-1]
    th = cfg["thermometers"]
    prev = res.iloc[-2] if len(res) > 1 else r

    def flag(v):
        return bool(v == 1)

    return {
        "date": res.index[-1].strftime("%Y-%m-%d"),
        "regime": r["regime"],
        "regime_ko": REGIME_KO[r["regime"]],
        "regime_prev": prev["regime"],
        "exposure_us": float(r["exposure_us"]),
        "exposure_kr": float(r["exposure_kr"]),
        "trading_bucket": float(r["trading_bucket"]),
        "score_raw": int(r["score_raw"]),
        "score_s": float(r["score_s"]),
        "thermometers": [
            {"id": "trend", "name": "추세", "desc": "S&P500 vs 200일선",
             "value": f"{r['spx_dist'] * 100:+.1f}%", "rule": th["trend"]["rule"], "on": flag(r["t_trend"])},
            {"id": "fear", "name": "공포", "desc": "VIX",
             "value": f"{r['vix']:.1f}", "rule": th["fear"]["rule"], "on": flag(r["t_fear"])},
            {"id": "credit", "name": "신용", "desc": {"baa10y": "Baa−10Y 스프레드", "hy": "하이일드 스프레드"}.get(th["credit"].get("source", "hy"), "신용 스프레드"),
             "value": f"{r['hy']:.2f}%p", "rule": th["credit"]["rule"], "on": flag(r["t_credit"])},
        ],
        "stabilization": [
            {"id": "vix_below_25", "rule": "VIX < 25", "ok": flag(r["c_vix_below_25"])},
            {"id": "vix_falling", "rule": "VIX < 20일 평균", "ok": flag(r["c_vix_falling"]),
             "detail": f"{r['vix']:.1f} vs {r['vix_ma20']:.1f}"},
            {"id": "credit_narrowing", "rule": "신용 스프레드 축소 중", "ok": flag(r["c_hy_narrowing"]),
             "detail": f"{r['hy']:.2f} vs {r['hy_ma20']:.2f}"},
        ],
        "freeze": flag(r["freeze"]),
        "vix_spike_today": flag(r["vix_spike"]),
        "no_new_buys": flag(r["no_new_buys"]),
        "shock_age": int(r["shock_age"]),
        "stab_n": int(r["stab_n"]),
        "ladder_days": int(r["ladder_days"]),
        "ladder_step": int(r["ladder_step"]),
        "overheat_dist": float(r["spx_dist_s"]),
        "kr": {
            "kospi_dist": float(r["kospi_dist"]) if "kospi_dist" in r and pd.notna(r["kospi_dist"]) else None,
            "below_ma": flag(r["kr_below_ma"]),
            "overheat": flag(r["kr_overheat"]),
            "fx_chg": float(r["fx_chg"]) if "fx_chg" in r and pd.notna(r["fx_chg"]) else None,
            "fx_warn": flag(r["fx_warn"]),
        },
        "breadth": {
            "warn": flag(r["breadth_warn"]),
            "near_high": flag(r["near_high"]),
            "rsp_spy_chg": float(r["rsp_spy_chg"]) if pd.notna(r["rsp_spy_chg"]) else None,
        },
        "kr_aux": {
            "kq_ks_chg": float(r["kq_ks_chg"]) if pd.notna(r["kq_ks_chg"]) else None,
            "breadth_warn": flag(r["kr_breadth_warn"]),
            "spread": float(r["kr_spread"]) if pd.notna(r["kr_spread"]) else None,
            "spread_widen": float(r["kr_spread_widen"]) if pd.notna(r["kr_spread_widen"]) else None,
            "spread_warn": flag(r["kr_spread_warn"]),
            "adr": float(r["kr_adr"]) if pd.notna(r["kr_adr"]) else None,
        },
        "aux": {
            "move": float(r["move"]) if pd.notna(r["move"]) else None,
            "move_warn": flag(r["move_warn"]),
            "move_extreme": flag(r["move_extreme"]),
        },
    }
