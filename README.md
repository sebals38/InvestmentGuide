# InvestmentGuide — 투자 프레임워크 대시보드

내 매매 판단의 확률과 질을 높이기 위한 개인용 도구입니다. 매수·매도는 내가 결정하고,
이 저장소는 **생존 규칙(1층)** 과 **국면·리스크 예산 신호(2층)** 를 매일 기계적으로 계산해 보여줍니다.

```
config.yaml     규칙·기준값 전부 (숫자를 바꾸려면 여기만)
engine.py       국면 판정 엔진 (순수 계산) — 대시보드와 백테스트가 공유
data.py         Yahoo Finance + FRED 수집, data/market.csv 캐시, --demo 합성 데이터
dashboard.py    매일 실행 → docs/index.html (공개) + report/history.csv (누적)
backtest.py     과거 전체에 엔진을 돌려 3가지 상식 점검 → report/backtest.md / .png
.github/workflows/daily.yml     매일 07:30 KST 자동 실행·커밋  (처음엔 setup/workflows/ 에 있음 → 2절 참고)
.github/workflows/backtest.yml  수동 실행 (Actions 탭)
CHANGELOG.md    규칙 변경 이력 (왜 바꿨는지)
account.example.yaml            계좌 값 예시 → account.yaml 로 복사 (git 제외)
```

## 1. 내 PC 에서 처음 실행하기 (Windows, PowerShell)

```powershell
cd C:\Users\배세현\Documents\GitHub\InvestmentGuide
.\.venv\Scripts\Activate.ps1          # 가상환경 (이미 만들어 둔 것)
pip install -r requirements.txt

python dashboard.py --demo            # 1) 네트워크 없이 레이아웃·로직 확인 → docs/index.html 열어보기
python dashboard.py                   # 2) 실데이터 (Yahoo + FRED). history.csv 에 오늘 판정 1줄 추가
python backtest.py                    # 3) 과거 전체 점검 → report/backtest.md 읽기
```

`Activate.ps1` 이 실행 정책 때문에 막히면 한 번만: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

계좌 손실 한도 판정까지 보려면 `account.example.yaml` 을 `account.yaml` 로 복사해 숫자를 넣으면
`local/dashboard.html` 이 추가로 생깁니다 (git 에 올라가지 않음). 또는 `python dashboard.py --equity 50000000 --peak 55000000`.

### FRED API 키 (필수)

https://fred.stlouisfed.org/docs/api/api_key.html 에서 무료 발급.

**키는 모두 프로젝트 폴더의 `.env` 한 파일에 둡니다** (git 제외). `.env.example` 을 복사해 `.env` 로 만들고 값을 채우세요:
```
copy .env.example .env
notepad .env
```
형식: `이름=값` 한 줄씩, 따옴표·공백 없이.

GitHub Actions 에서는 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에
`FRED_API_KEY` 를 넣으면 워크플로가 자동으로 씁니다.

### 국내 참고 지표용 키 (선택)

- **ECOS (한국은행, 국내 신용 스프레드)**: https://ecos.bok.or.kr → 로그인 → 마이페이지 → 인증키 신청 (무료, 즉시) →
  `.env` 의 `ECOS_API_KEY=` 에 입력. 첫 실행 로그에 항목명(예: '회사채(3년, AA-)')이 찍히니 config.yaml 의 코드가 맞는지 확인.
- **KRX (등락 종목수 ADR)**: https://data.krx.co.kr 회원가입(무료) → `.env` 의 `KRX_ID=` / `KRX_PW=` 에 입력.
  둘 다 없어도 대시보드는 돌아가고 해당 칸만 '미수집'으로 표시됩니다. GitHub Actions 에서는 Secrets 에 `ECOS_API_KEY`, `KRX_ID`, `KRX_PW`.

## 2. GitHub 자동화 켜기 (한 번만)

1. 워크플로 파일을 제자리로 옮기고 (`setup/workflows/` 는 원격 도구가 `.github/` 에 직접 쓸 수 없어서 둔 임시 위치) 커밋·푸시:
   ```powershell
   New-Item -ItemType Directory -Force .github\workflows | Out-Null
   Move-Item setup\workflows\*.yml .github\workflows\
   Remove-Item -Recurse setup
   git add -A
   git commit -m "v0.2: regime engine, backtest, actions"
   git push
   ```
2. GitHub 저장소 → **Settings → Actions → General → Workflow permissions** → *Read and write permissions* 선택 → Save
   (봇이 결과를 커밋하려면 필요)
3. **Settings → Pages → Build and deployment** → Source: *Deploy from a branch*, Branch: `master` / 폴더: `/docs` → Save
   → 몇 분 뒤 `https://sebals38.github.io/InvestmentGuide/` 에서 대시보드가 열립니다.
4. **Actions 탭 → daily-dashboard → Run workflow** 로 한 번 수동 실행해 초록불 확인.
   이후 평일 07:30 KST 마다 자동 실행 → `docs/`, `report/history.csv`, `data/market.csv` 가 매일 커밋됩니다.
5. 기준값을 바꾼 뒤에는 **Actions 탭 → backtest → Run workflow** 로 백테스트를 다시 돌리고 `report/backtest.md` 를 확인.

주의: Actions 가 매일 커밋하므로, PC 에서 작업하기 전에 `git pull` 부터 하세요.

## 3. 판정 로직 요약 (자세한 숫자는 config.yaml)

| | 조건 | 노출 상한 |
|---|---|---|
| 과열 상승 | S&P500 이 200일선 대비 +12% 이상 (5일 평균) | 50% |
| 일반 상승 | 그 외, 온도계 꺼짐 | 65% (온도계 1개당 -10%p) |
| 충격 진행 | 온도계 3개 점수의 5일 평균 ≥ 1.5. 최소 15거래일 유지 | 30% (+안정화 조건 1개당 5%p, 5거래일에 한 칸) |
| 안정화 침체 | 충격 후 VIX 진정 · VIX 하락 · 신용 스프레드 축소 세 조건 확인 | 45% → 10일마다 +5%p → 200일선 아래 50% / 위 70% |
| (동결) | VIX 가 하루라도 30 초과 → 5거래일간. 국면은 안 바뀜 | 노출 그대로. 신규 매수 금지, 트레이딩 버킷 10% |

온도계 3개: **추세** S&P500 < 200일선 · **공포** VIX ≥ 25 · **신용** Baa−10Y 스프레드 ≥ 3.0%p (FRED BAA10Y; HY OAS 는 FRED 가 3년치만 줘서 참고용).
국면은 미국 지표가 정하고, KOSPI 200일선은 국내 노출만 조정(-15%p). 환율·MOVE 는 참고용.

1층(고정): 포지션 손절 10% · 포지션 비중 상한 15% · 계좌 고점 대비 -15% 에서 전면 중단 ·
현금은 만기 2년 이하 채권·MMF 만 인정 · 손실 중 규칙 변경 금지.

## 4. 백테스트는 무엇을 보나

수익률이 아니라 규칙이 상식적으로 움직였는지 3가지만 봅니다.
(1) 위기 초기에 줄였나 — 고점 후 며칠 만에, 낙폭 얼마에서 충격 판정이 났나.
(2) 바닥 근처에서 다시 늘렸나 — 바닥 시점과 60거래일 후 노출.
(3) 왔다갔다 했나 — 연간 국면 전환 횟수, 10일 미만 짧은 충격 횟수.
결과가 이상하면 `config.yaml` 의 기준을 바꾸고 `CHANGELOG.md` 에 이유를 적은 뒤 다시 돌립니다.

## 5. 다음 할 일
- [ ] HY 포함 실데이터 백테스트로 기준값 확정 (shock_score 1.5, HY 5.0, min_shock_days 15, 2008년처럼 긴 충격에서 VIX<25 조건이 너무 늦지 않은지)
- [x] 취약도 점수 검증 → 승격 없음 (시장 폭만 참고로 유지, CHANGELOG v0.6)
- [x] 국내 참고 지표 추가 (v0.7). 시장 폭 대용은 앞서지 않음 확인. 신용 스프레드는 ECOS 키 후 검증, ADR 은 1년 누적 후
- [ ] 보류된 논점: 실현이익 절반의 고위험 배분 / 트레일링 스톱 (반론 정리 후 결정)
