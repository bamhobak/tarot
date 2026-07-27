# -*- coding: utf-8 -*-
"""
이로재로타로 — 제미나이 중계 서버
  타로 페이지(브라우저)  →  이 서버  →  gemini_scraper(비회원 스크래퍼)  →  제미나이

- 항상 켜두는 PC에서 이 파일만 실행해두면 됩니다.  python tarot_relay.py
- 제미나이 창은 요청이 올 때만 띄우고, 놀면 자동으로 닫습니다(인터넷 느려지는 문제 방지).
- 외부에서도 쓰려면 터널(ngrok / cloudflared)로 이 포트를 https 주소로 열면 됩니다.
"""
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 윈도우 콘솔이 cp949라 한글/기호 로그에서 죽는 것을 막는다
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import gemini_scraper as G

PORT = 8765
IDLE_STOP_SEC = 120        # 이 시간 동안 요청이 없으면 제미나이 창을 닫는다
QUERY_TIMEOUT = 180        # 제미나이 응답 대기 최대 시간(초)

_lock = threading.Lock()   # 제미나이는 한 번에 한 요청만
_last_use = 0.0

# 창 없이 돌 때(자동시작)도 기록이 남도록 화면과 파일에 같이 쓴다.
# exe 로 묶였을 때는 exe 가 있는 폴더에 남긴다(_internal 안에 숨지 않게).
_APP_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_APP_DIR, "relay.log")
_log_lock = threading.Lock()


def log(*parts):
    msg = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), " ".join(str(p) for p in parts))
    with _log_lock:
        try:
            print(msg, flush=True)
        except Exception:
            pass
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass


# ── 프롬프트 ─────────────────────────────────────────
# 이 문구만 고치면 제미나이 풀이의 성격이 바뀝니다.
PROMPT_HEAD = """너는 20년 경력의 타로 상담가야. 아래는 실제로 방금 뽑힌 스프레드야.

[상담 주제] {topic}
[질문] {question}
[스프레드] {spread}
[뽑힌 카드]
{cards}

이 카드들을 질문에 딱 맞게 풀어줘. 조건은 이래.
- 카드 하나하나가 '그 자리'에서 무슨 뜻인지 질문과 연결해서 설명해. 일반론 말고 이 질문에 대한 답으로.
- 카드끼리의 흐름(앞 카드가 뒤 카드에 어떻게 이어지는지)을 반드시 짚어줘.
- 마지막에 '그래서 결론'과 '지금 당장 할 것' 을 분명하게 말해줘. 두루뭉술하게 넘어가지 마.
- 말투는 친구한테 팩트 짚어주듯 반말로. 위로만 하지 말고 쓴소리도 해. 대신 비꼬거나 조롱하진 마.
- 전체 600자 안팎. 소제목은 짧게, 표는 쓰지 마.
"""


def build_prompt(d):
    q = (d.get("question") or "").strip() or "(질문을 따로 적지 않았음. 뽑힌 카드만 보고 지금 상황을 읽어줘)"
    topic = (d.get("topic") or "종합운").strip()
    spread = (d.get("spread") or "").strip()
    lines = []
    for c in (d.get("cards") or []):
        pos = (c.get("pos") or "").strip()
        name = (c.get("name") or "").strip()
        orient = (c.get("orient") or "").strip()
        desc = (c.get("desc") or "").strip()
        line = "- [%s] %s (%s)" % (pos, name, orient)
        if desc:
            line += " — 자리 뜻: %s" % desc
        lines.append(line)
    return PROMPT_HEAD.format(topic=topic, question=q, spread=spread,
                              cards="\n".join(lines) if lines else "- (없음)")


# ── 제미나이 호출 ────────────────────────────────────
def ask_gemini(prompt):
    global _last_use
    with _lock:
        _last_use = time.time()
        if not G.is_connected():
            log("제미나이 창 여는 중...")
            G.set_settle_before_submit(2)
            G.start()
        try:
            text = G.query(prompt, timeout_sec=QUERY_TIMEOUT, min_len=80)
        except Exception as e:
            # 세션이 상했을 수 있으니 한 번만 다시 시도
            log("1차 실패(%s) -> 재시작 후 재시도" % e)
            G.restart()
            text = G.query(prompt, timeout_sec=QUERY_TIMEOUT, min_len=80)
        _last_use = time.time()
        return text


def idle_watcher():
    """놀고 있으면 제미나이 창을 닫아서 인터넷을 돌려준다."""
    while True:
        time.sleep(10)
        try:
            if G.is_connected() and _last_use and (time.time() - _last_use) > IDLE_STOP_SEC:
                with _lock:
                    if G.is_connected() and (time.time() - _last_use) > IDLE_STOP_SEC:
                        log("놀고 있어서 제미나이 창 닫음")
                        G.stop()
        except Exception:
            pass


# ── HTTP ────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "TarotRelay/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # 크롬이 공개 사이트 → 사설망 요청을 막는 것(Private Network Access) 허용
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "connected": G.is_connected()})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/reading"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception as e:
            self._json(400, {"ok": False, "error": "요청을 읽지 못했어요: %s" % e})
            return

        prompt = build_prompt(data)
        log("요청 도착 -", (data.get("topic") or ""), "/", (data.get("question") or "(질문 없음)"))
        try:
            text = ask_gemini(prompt)
            log("응답 %d자" % len(text))
            self._json(200, {"ok": True, "text": text})
        except Exception as e:
            log("실패:", e)
            self._json(500, {"ok": False, "error": str(e)})

    def log_message(self, *a):
        pass   # 기본 접속 로그는 끔


class Server(ThreadingHTTPServer):
    # 윈도우는 SO_REUSEADDR 때문에 같은 포트에 두 번째 서버도 그냥 붙어버린다.
    # 그러면 요청이 아무 쪽으로나 가서 헷갈리니, 두 번째 실행은 대놓고 실패시킨다.
    allow_reuse_address = False
    daemon_threads = True


def main():
    threading.Thread(target=idle_watcher, daemon=True, name="IdleWatcher").start()
    try:
        srv = Server(("0.0.0.0", PORT), Handler)
    except OSError as e:
        log("포트 %d 를 못 열었습니다. 중계서버가 이미 켜져 있는 것 같아요. (%s)" % (PORT, e))
        return
    log("=" * 46)
    log("이로재로타로 제미나이 중계 서버 시작")
    log("주소 http://localhost:%d  /  확인 http://localhost:%d/health" % (PORT, PORT))
    log("제미나이 창은 요청 올 때만 뜨고 %d초 놀면 자동으로 닫힙니다" % IDLE_STOP_SEC)
    log("=" * 46)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("종료합니다")
    finally:
        try:
            G.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
