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
import subprocess
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

# 인터넷에 열리므로(터널) 아무나 막 쓰지 못하게 최소한의 빗장
ALLOWED_ORIGINS = ("https://bamhobak.github.io",)   # 타로 페이지
COOLDOWN_SEC = 20          # 같은 사람이 연달아 조를 수 없게
DAILY_LIMIT = 300          # 하루 전체 요청 상한

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
{mbti_line}[스프레드] {spread}
[뽑힌 카드]
{cards}

이 카드들을 질문에 딱 맞게 풀어줘. 조건은 이래.
{mbti_rule}- 카드 하나하나가 '그 자리'에서 무슨 뜻인지 질문과 연결해서 설명해. 일반론 말고 이 질문에 대한 답으로.
- 카드끼리의 흐름(앞 카드가 뒤 카드에 어떻게 이어지는지)을 반드시 짚어줘.
- 카드마다 근거를 두 문장 이상 붙여줘. 왜 그렇게 읽히는지(그림·상징·정역방향) 짚어주고 넘어가.
- 마지막에 '그래서 결론'과 '지금 당장 할 것' 을 분명하게 말해줘. 두루뭉술하게 넘어가지 마.
  '지금 당장 할 것' 은 두세 가지로 구체적으로 적어줘.
- 말투는 다정하고 따뜻한 반말로. 편들어 주는 친한 언니·오빠처럼 상냥하게 말해줘.
  · 겁주거나 나무라거나 비꼬지 마. 단정적으로 나쁜 일을 예언하지도 마.
  · 어려운 카드가 나와도 "이래서 큰일이다" 가 아니라 "이런 마음일 수 있어, 그래서 이렇게 해보자" 로 풀어줘.
  · 조심할 점은 부드럽게 돌려서 말하고, 끝은 늘 응원과 희망으로 마무리해.
- 전체 900자 안팎. 너무 짧게 끝내지 마. 소제목은 짧게.
- 맨 마지막은 반드시 아래 형식의 요약 표로 끝내. 칸 안의 글은 25자 이내로 짧게 써.

| 자리 | 카드 | 한 줄 정리 |
| --- | --- | --- |
| (자리 이름) | (카드 이름, 정/역) | (핵심 한 줄) |

  표 아래에 마지막 한 줄로 `한 줄 결론: ...` 만 덧붙여. 그 뒤엔 아무것도 쓰지 마.
"""


# 오늘의 운세는 형식이 다르다 — 카드 한 장으로 하루를 읽는다
TODAY_HEAD = """너는 20년 경력의 타로 상담가야. 오늘 하루의 운세를 보려고 카드 한 장을 뽑았어.

[오늘 뽑은 카드]
{cards}

이 카드 한 장으로 오늘 하루의 타로운세 읽어줘. 조건은 이래.
- 먼저 '오늘의 한마디'로 하루 분위기를 한 문장으로 요약해.
- 그다음 카드 그림과 정/역방향을 근거로 오늘 하루가 어떻게 흘러갈지 풀어줘.
- 카드 한 장으로 보는 총운이야. 애정·일·돈·건강 같은 분야별로 쪼개서 말하지 마.
  분야 이야기는 그 카드가 특별히 그쪽을 가리킬 때만 자연스럽게 한 번 언급하는 정도로만.
- 오늘 조심할 것 하나와 챙기면 좋은 것 하나를 분명하게 말해줘.
- 말투는 다정하고 따뜻한 반말로. 편들어 주는 친한 언니·오빠처럼 상냥하게 말해줘.
  · 겁주거나 나무라거나 비꼬지 마. 단정적으로 나쁜 일을 예언하지도 마.
  · 조심할 것도 부드럽게 돌려서 말하고, 끝은 늘 응원과 희망으로 마무리해.
- 전체 500자 안팎. 소제목은 짧게. 표는 쓰지 마.
- 맨 마지막은 한 줄로 `오늘의 조언: ...` 만 덧붙이고 끝내. 그 뒤엔 아무것도 쓰지 마.
"""

MBTI_RULE = (
    "- 상담받는 사람은 {m} 유형이야. {m} 가 납득하는 방식으로 설명해줘.\n"
    "  · 생각하는 방식, 결정 내리는 방식, 감정을 다루는 방식이 그 유형답다는 전제로 읽어.\n"
    "  · 이 사람이 이 상황에서 빠지기 쉬운 {m} 특유의 함정을 한 번은 콕 집어줘.\n"
    "  · 조언은 {m} 가 실제로 실행할 수 있는 걸로 줘. 성향상 절대 못 할 일은 시키지 마.\n"
    "  · 다만 MBTI 설명서를 읊지는 마. 어디까지나 카드 풀이가 중심이야.\n"
)


def build_prompt(d):
    q = (d.get("question") or "").strip() or "(질문을 따로 적지 않았음. 뽑힌 카드만 보고 지금 상황을 읽어줘)"
    topic = (d.get("topic") or "종합운").strip()
    spread = (d.get("spread") or "").strip()
    mbti = (d.get("mbti") or "").strip().upper()[:4]
    if mbti and len(mbti) == 4 and all(c.isalpha() for c in mbti):
        mbti_line = "[MBTI] %s\n" % mbti
        mbti_rule = MBTI_RULE.format(m=mbti)
    else:
        mbti_line = mbti_rule = ""
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
    cards_txt = "\n".join(lines) if lines else "- (없음)"
    if "오늘의 운세" in topic:
        return TODAY_HEAD.format(cards=cards_txt)
    return PROMPT_HEAD.format(topic=topic, question=q, spread=spread,
                              mbti_line=mbti_line, mbti_rule=mbti_rule,
                              cards=cards_txt)


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


# ── ngrok 터널 (밖에서도 쓰게 하기) ───────────────────
_ngrok_proc = None
_public_url = ""


def tunnel_domain():
    """터널 설정.bat 이 만들어 둔 tunnel.txt 에서 고정 주소를 읽는다."""
    p = os.path.join(_APP_DIR, "tunnel.txt")
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line.replace("https://", "").replace("http://", "").rstrip("/")
    except Exception:
        pass
    return ""


def start_tunnel():
    """ngrok.exe 가 옆에 있고 고정 주소가 설정돼 있으면 터널을 연다."""
    global _ngrok_proc, _public_url
    exe = os.path.join(_APP_DIR, "ngrok.exe")
    dom = tunnel_domain()
    if not os.path.exists(exe):
        log("ngrok.exe 가 없어서 터널 없이 갑니다 — '터널 준비.bat' 을 한 번 실행하세요 (지금은 이 PC에서만 사용 가능)")
        return
    if not dom:
        log("터널 주소가 설정되지 않았습니다. '터널 설정.bat' 을 한 번 실행하세요 (이 PC에서만 사용 가능)")
        return
    try:
        flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
        args = [exe, "http", str(PORT), "--domain=" + dom, "--log=stdout"]
        cfg = os.path.join(_APP_DIR, "ngrok.yml")         # 인증토큰이 든 설정(있으면 사용)
        if os.path.exists(cfg):
            args += ["--config", cfg]
        _ngrok_proc = subprocess.Popen(
            args, cwd=_APP_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags)
        _public_url = "https://" + dom
        log("터널 열림:", _public_url)
    except Exception as e:
        log("터널을 못 열었습니다:", e)
        if "225" in str(e) or "바이러스" in str(e):
            log("→ 백신이 ngrok 을 막았습니다. '터널 준비.bat' 을 실행해 예외를 등록한 뒤 다시 켜주세요.")


def stop_tunnel():
    global _ngrok_proc
    if _ngrok_proc is not None:
        try:
            _ngrok_proc.terminate()
        except Exception:
            pass
        _ngrok_proc = None


# ── 남용 방지 ────────────────────────────────────────
_seen = {}          # ip -> 마지막 요청 시각
_today = ["", 0]    # [날짜, 오늘 처리한 수]


def gate(ip):
    """통과하면 None, 막으면 (코드, 사유)"""
    now = time.time()
    day = time.strftime("%Y-%m-%d")
    if _today[0] != day:
        _today[0], _today[1] = day, 0
    if _today[1] >= DAILY_LIMIT:
        return 429, "오늘은 여기까지예요. 내일 다시 봐주세요."
    last = _seen.get(ip, 0)
    if now - last < COOLDOWN_SEC:
        return 429, "조금만 천천히요. %d초 뒤에 다시 눌러주세요." % int(COOLDOWN_SEC - (now - last) + 1)
    _seen[ip] = now
    _today[1] += 1
    return None


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
        # 타로 페이지 · 로컬에서 연 파일만 허용
        origin = self.headers.get("Origin") or ""
        ok = (origin in ALLOWED_ORIGINS or origin == "null"
              or origin.startswith("http://localhost")
              or origin.startswith("http://127.0.0.1"))
        self.send_header("Access-Control-Allow-Origin", origin if ok else "https://bamhobak.github.io")
        # ngrok 무료 계정의 경고 페이지를 건너뛰는 헤더도 허용해야 한다
        self.send_header("Access-Control-Allow-Headers", "Content-Type, ngrok-skip-browser-warning")
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
            self._json(200, {"ok": True, "connected": G.is_connected(),
                             "public": _public_url, "today": _today[1]})
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

        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        blocked = gate(ip)
        if blocked:
            log("막음(%s): %s" % (ip, blocked[1]))
            self._json(blocked[0], {"ok": False, "error": blocked[1]})
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
    start_tunnel()
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
        stop_tunnel()
        try:
            G.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
