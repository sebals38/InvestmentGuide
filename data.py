"""
data.py — 시장 데이터 수집·캐시 (Yahoo Finance + FRED)

- 매일 전체 기간을 다시 받아 data/market.csv 에 저장 → 저장소에 커밋되므로
  Yahoo 가 막히거나 데이터가 바뀌어도 그날의 원천이 남습니다 (백테스트 재현용).
- 수집 실패 시 캐시로 대체하고 경고를 출력합니다.
- --demo: 네트워크 없이 합성 데이터 (위기 구간 포함) 로 레이아웃·로직 점검.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import sys

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))

# cosd/coed 를 안 주면 FRED 가 최근 몇 년만 돌려준다 → 반드시 시작일을 명시
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}&coed={end}"


def _p(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(HERE, path)


# ─────────────────────────── 원천 수집 ───────────────────────────
def fetch_yahoo(tickers: dict, start: str) -> pd.DataFrame:
    import yfinance as yf

    symbols = list(tickers.values())
    raw = yf.download(symbols, start=start, auto_adjust=False, progress=False, group_by="column", threads=True)
    if raw.empty:
        raise RuntimeError("Yahoo Finance 응답이 비어 있음")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    inv = {v: k for k, v in tickers.items()}
    close = close.rename(columns=inv)
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


FRED_TXT = "https://fred.stlouisfed.org/data/{sid}.txt"                                  # 전체 이력 (텍스트)
FRED_DL = "https://fred.stlouisfed.org/series/{sid}/downloaddata/{sid}.csv"               # '데이터 다운로드' 링크
FRED_API = ("https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={key}"
            "&file_type=json&observation_start={start}")                                    # 무료 API 키 필요
UA = {"User-Agent": "Mozilla/5.0 (InvestmentGuide dashboard; personal use)"}


def _to_series(dates, values) -> pd.Series:
    s = pd.Series(pd.to_numeric(pd.Series(values), errors="coerce").values, index=pd.to_datetime(dates))
    return s[~s.index.isna()].sort_index()


def _fred_api(sid: str, start: str, key: str) -> pd.Series:
    r = requests.get(FRED_API.format(sid=sid, key=key, start=start), headers=UA, timeout=30)
    r.raise_for_status()
    obs = r.json()["observations"]
    return _to_series([o["date"] for o in obs], [o["value"] for o in obs])


def _fred_download(sid: str) -> pd.Series:
    r = requests.get(FRED_DL.format(sid=sid), headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    if df.shape[1] < 2:
        raise RuntimeError("csv 형식 아님: " + r.text[:120].replace("\n", " "))
    return _to_series(df.iloc[:, 0], df.iloc[:, 1])


def _fred_txt(sid: str) -> pd.Series:
    r = requests.get(FRED_TXT.format(sid=sid), headers=UA, timeout=30)
    r.raise_for_status()
    lines = r.text.splitlines()
    hdr = [k for k, ln in enumerate(lines) if ln.strip().upper().startswith("DATE")]
    if not hdr:
        raise RuntimeError("DATE 헤더 없음: " + r.text[:120].replace("\n", " "))
    rows = [ln.split() for ln in lines[hdr[0] + 1:] if ln.strip()]
    return _to_series([x[0] for x in rows], [x[1] if len(x) > 1 else "." for x in rows])


def _fred_csv(sid: str, start: str) -> pd.Series:
    url = FRED_CSV.format(sid=sid, start=start, end=dt.date.today().isoformat())
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return _to_series(df.iloc[:, 0], df.iloc[:, 1])


def fetch_fred(series: dict, start: str) -> pd.DataFrame:
    """여러 경로를 차례로 시도해 가장 긴 이력을 쓴다. FRED_API_KEY 환경변수가 있으면 API 를 최우선."""
    key = _fred_key()
    if key:
        print(f"FRED API 키 감지: {key[:4]}…{key[-2:]} ({len(key)}자)")
    else:
        print("[안내] FRED_API_KEY 가 없습니다 (환경변수 또는 fred_api_key.txt). 공개 경로는 최근 3년만 줄 수 있습니다.",
              file=sys.stderr)
    out = {}
    for sid_key, sid in series.items():
        attempts = []
        if key:
            attempts.append(("api", lambda: _fred_api(sid, start, key)))
        attempts += [("graph-csv", lambda: _fred_csv(sid, start))]
        best, best_name = None, None
        for name, fn in attempts:
            try:
                s = fn()
                s = s[s.index >= pd.Timestamp(start)]
                print(f"FRED {sid} [{name}]: {len(s)}행, {s.index.min().date()} ~ {s.index.max().date()}")
                if best is None or len(s) > len(best):
                    best, best_name = s, name
                if len(s) > 2000:      # 8년 이상이면 충분 → 더 안 봄
                    break
            except Exception as e:  # noqa: BLE001
                print(f"[경고] FRED {sid} [{name}] 실패: {type(e).__name__} {str(e)[:160]}", file=sys.stderr)
        if best is None:
            raise RuntimeError(f"FRED {sid} 를 어떤 경로로도 받지 못함")
        print(f"FRED {sid}: [{best_name}] 사용")
        out[sid_key] = best
    return pd.DataFrame(out)


# ─────────────────────────── 한국은행 ECOS ───────────────────────────
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100000/{stat}/D/{start}/{end}/{item}"


def _read_key(env: str, fname: str) -> str:
    key = os.environ.get(env, "").strip().strip('"')
    if not key:
        f = _p(fname)
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                key = fh.read().strip().strip('"')
    return key


def _fred_key() -> str:
    """환경변수 FRED_API_KEY, 없으면 프로젝트 폴더의 fred_api_key.txt (git 제외)."""
    return _read_key("FRED_API_KEY", "fred_api_key.txt")


def fetch_ecos(cfg: dict, start: str) -> pd.DataFrame:
    """ECOS 일별 시장금리. 키가 없거나 실패하면 빈 DataFrame (참고 지표라 치명적이지 않음)."""
    ec = cfg["sources"].get("ecos")
    if not ec:
        return pd.DataFrame()
    key = _read_key("ECOS_API_KEY", "ecos_api_key.txt")
    if not key:
        print("[안내] ECOS_API_KEY 없음 → 국내 신용 스프레드 생략 (ecos_api_key.txt 에 키를 넣으면 수집)", file=sys.stderr)
        return pd.DataFrame()
    out = {}
    for col in ("kr_govt3", "kr_corp_aa"):
        item = ec[col]
        url = ECOS_URL.format(key=key, stat=ec["stat"], start=start.replace("-", ""),
                              end=dt.date.today().strftime("%Y%m%d"), item=item)
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            js = r.json()
            rows = js.get("StatisticSearch", {}).get("row")
            if not rows:
                raise RuntimeError(str(js)[:200])
            name = rows[0].get("ITEM_NAME1", "?")
            ser = pd.Series([float(x["DATA_VALUE"]) for x in rows],
                            index=pd.to_datetime([x["TIME"] for x in rows], format="%Y%m%d"))
            print(f"ECOS {ec['stat']}/{item} = '{name}': {len(ser)}행, {ser.index.min().date()} ~ {ser.index.max().date()}")
            out[col] = ser
        except Exception as e:  # noqa: BLE001
            print(f"[경고] ECOS {col}({item}) 실패: {type(e).__name__} {str(e)[:160]}", file=sys.stderr)
    return pd.DataFrame(out)


# ─────────────────────────── KRX 등락 종목수 (pykrx, 누적) ───────────────────────────
def update_kr_adr(cfg: dict) -> pd.DataFrame | None:
    """오늘(및 최근 누락 영업일 최대 10일)의 KOSPI 상승/하락 종목수를 data/kr_adr.csv 에 누적. 실패해도 None."""
    if not cfg["sources"].get("krx_adr"):
        return None
    path = _p(cfg["output"]["kr_adr_csv"])
    # KRX 로그인 (pykrx 최신 버전은 KRX 정보데이터시스템 계정 필요)
    if not os.environ.get("KRX_ID"):
        f = _p("krx_login.txt")
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                parts = [x.strip() for x in fh.read().splitlines() if x.strip()]
            if len(parts) >= 2:
                os.environ["KRX_ID"], os.environ["KRX_PW"] = parts[0], parts[1]
    try:
        from pykrx import stock  # noqa: WPS433
    except Exception as e:  # noqa: BLE001
        print(f"[안내] pykrx 없음 → ADR 생략 ({e})", file=sys.stderr)
        return None

    old = pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else pd.DataFrame(columns=["adv", "dec", "unch"])
    days = pd.bdate_range(end=dt.date.today(), periods=10)
    todo = [d for d in days if d not in old.index]
    rows = {}
    for d in todo:
        try:
            df = stock.get_market_ohlcv_by_ticker(d.strftime("%Y%m%d"), market="KOSPI")
            if df is None or df.empty or "등락률" not in df:
                continue
            chg = df["등락률"]
            if (df["거래량"] == 0).all():   # 휴장일
                continue
            rows[d] = {"adv": int((chg > 0).sum()), "dec": int((chg < 0).sum()), "unch": int((chg == 0).sum())}
        except Exception as e:  # noqa: BLE001
            print(f"[경고] KRX {d.date()} 실패: {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
            break
    if rows:
        add = pd.DataFrame.from_dict(rows, orient="index")
        new = (pd.concat([old, add]) if len(old) else add).sort_index()
        new.index.name = "date"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new.to_csv(path)
        print(f"KRX 등락 종목수: {len(rows)}일 추가 → 누적 {len(new)}일")
        return new
    return old if len(old) else None


def load_kr_adr(cfg: dict) -> pd.DataFrame | None:
    path = _p(cfg["output"]["kr_adr_csv"])
    return pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else None


def fetch_market(cfg: dict) -> pd.DataFrame:
    src = cfg["sources"]
    start = src["start"]
    y = fetch_yahoo(src["yahoo"], start)
    f = fetch_fred(src["fred"], start)
    m = y.join(f, how="outer").sort_index()
    e = fetch_ecos(cfg, start)
    if not e.empty:
        m = m.join(e, how="outer").sort_index()
    # 미국 거래일 기준으로 정렬: S&P500 이 있는 날만 남기고 나머지는 앞값 채움
    m = m[m["spx"].notna()]
    m = m.ffill()
    check_coverage(m, core_cols(cfg))
    return m


def check_coverage(m: pd.DataFrame, cols=("spx", "vix", "baa10y"), verbose: bool = True) -> list[str]:
    """핵심 열이 기간 대부분을 덮는지 확인. 부족하면 경고 문자열을 돌려주고 (verbose 면) stderr 에도 출력."""
    warns = []
    for col in cols:
        if col not in m:
            raise RuntimeError(f"핵심 열 {col} 이 없습니다")
        cov = m[col].notna().mean()
        first = m[col].first_valid_index()
        if cov < 0.8:
            w = (f"{col} 데이터가 기간의 {cov*100:.0f}% 만 있음 (첫 값 {first.date() if first is not None else '없음'}) "
                 f"— 비어 있는 날은 이 온도계를 빼고 판정합니다. 백테스트 결과를 믿지 마세요.")
            warns.append(w)
            if verbose:
                print("[경고] " + w, file=sys.stderr)
    return warns


def core_cols(cfg: dict) -> tuple:
    return ("spx", "vix", cfg["thermometers"]["credit"].get("source", "hy"))


# ─────────────────────────── 캐시 ───────────────────────────
def load_market(cfg: dict, demo: bool = False, refresh: bool = True) -> tuple[pd.DataFrame, str]:
    """(DataFrame, 출처 문자열). 출처는 'live' | 'cache' | 'demo'."""
    if demo:
        return demo_market(), "demo"

    cache = _p(cfg["output"]["market_cache"])
    if refresh:
        try:
            m = fetch_market(cfg)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            m.to_csv(cache, float_format="%.4f")
            return m, "live"
        except Exception as e:  # noqa: BLE001
            print(f"[경고] 실시간 수집 실패 → 캐시 사용: {e}", file=sys.stderr)

    if os.path.exists(cache):
        m = pd.read_csv(cache, index_col=0, parse_dates=True)
        check_coverage(m, core_cols(cfg))
        return m, "cache"
    raise RuntimeError("실시간 수집도 실패했고 캐시(data/market.csv)도 없습니다.")


# ─────────────────────────── 데모 ───────────────────────────
def demo_market(days: int = 3000, seed: int = 7) -> pd.DataFrame:
    """위기 두 번(급락·완만한 약세)을 심어 둔 합성 데이터. 국면이 실제로 바뀌는지 보는 용도."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=dt.date.today(), periods=days)
    n = len(idx)

    # 일별 변동성 경로: 평소 0.8%, 위기 구간에서 3~4%
    vol = np.full(n, 0.008)
    drift = np.full(n, 0.0004)
    crash1 = slice(int(n * 0.35), int(n * 0.35) + 45)     # 급락 (코로나형)
    bear2 = slice(int(n * 0.70), int(n * 0.70) + 200)     # 완만한 약세 (2022형)
    vol[crash1] = 0.04
    drift[crash1] = -0.012
    vol[bear2] = 0.016
    drift[bear2] = -0.0012
    rets = rng.normal(drift, vol)
    spx = 2000 * np.exp(np.cumsum(rets))

    rv = pd.Series(rets).rolling(10).std().bfill().to_numpy()
    vix = np.clip(11 + 900 * rv + rng.normal(0, 1.2, n), 9, 85)
    hy = np.clip(3.3 + 250 * rv + rng.normal(0, 0.08, n), 2.5, 20)
    baa10y = np.clip(1.8 + 120 * rv + rng.normal(0, 0.05, n), 1.2, 8)
    spy = spx / 10
    rsp = spy * 0.4 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    t10y2y = np.cumsum(rng.normal(0, 0.02, n)) * 0.3 + 0.5
    kr_govt3 = np.clip(3.0 + np.cumsum(rng.normal(0, 0.02, n)) * 0.2, 0.5, 6)
    kr_corp_aa = kr_govt3 + np.clip(0.6 + 60 * rv + rng.normal(0, 0.03, n), 0.3, 5)
    move = np.clip(70 + 3000 * rv + rng.normal(0, 4, n), 45, 200)

    kospi = 2500 * np.exp(np.cumsum(rets * 1.1 + rng.normal(0, 0.006, n)))
    usdkrw = 1250 * np.exp(np.cumsum(-rets * 0.4 + rng.normal(0, 0.003, n)))
    ndx = spx * 8 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    kosdaq = kospi * 0.3 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    tnx = np.clip(3.5 + np.cumsum(rng.normal(0, 0.03, n)) * 0.2, 0.5, 8)
    dxy = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    gold = 2000 * np.exp(np.cumsum(rng.normal(0.0002, 0.009, n)))
    oil = 75 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))

    return pd.DataFrame({
        "spx": spx, "ndx": ndx, "vix": vix, "move": move, "hy": hy, "baa10y": baa10y,
        "kospi": kospi, "kosdaq": kosdaq, "usdkrw": usdkrw,
        "tnx": tnx, "dxy": dxy, "gold": gold, "oil": oil,
        "spy": spy, "rsp": rsp, "t10y2y": t10y2y, "kr_govt3": kr_govt3, "kr_corp_aa": kr_corp_aa,
    }, index=idx)
