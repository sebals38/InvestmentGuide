"""
check_krx.py — KRX 로그인 진단 (1회 시도, 비밀번호는 출력하지 않음)
사용:  .venv\Scripts\python.exe check_krx.py
KRX 서버가 돌려준 오류 코드/메시지를 그대로 보여준다.
  CD001 = 정상, CD010 = 비밀번호 변경 필요, CD011 = 중복 로그인, 그 외 = 메시지 참고
"""
import os

import requests

import data  # noqa: F401  (.env 로드)

from pykrx.website.comm.auth import LOGIN_PAGE, LOGIN_URL, USER_AGENT, warmup_krx_session

uid, pw = os.getenv("KRX_ID", ""), os.getenv("KRX_PW", "")
print(f"ID: {uid}  / PW 길이: {len(pw)}")

s = requests.Session()
warmup_krx_session(s)
r = s.post(LOGIN_URL, headers={"User-Agent": USER_AGENT, "Referer": LOGIN_PAGE},
           data={"mbrNm": "", "telNo": "", "di": "", "certType": "", "mbrId": uid, "pw": pw}, timeout=15)
print("HTTP", r.status_code)
try:
    js = r.json()
    print("오류 코드   :", js.get("_error_code"))
    print("오류 메시지 :", js.get("_error_message"))
except ValueError:
    print("JSON 아님 (앞부분):", r.text[:300])
