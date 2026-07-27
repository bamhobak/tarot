"""
Gemini 웹 스크래퍼
- Playwright 전용 스레드를 하나 유지 (스레드 안전)
- 다른 스레드에서 query() 호출 시 큐로 전달하고 결과 대기
"""
import os
import sys
import time
import threading
import queue as _queue
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Playwright headless shell 경로를 탐색 (CCleaner 회피: chrome-headless-shell.exe 사용)
def _find_headless_shell() -> str:
    candidates = []
    # 1순위: exe 옆 ms-playwright
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(base, "ms-playwright"))
    # 2순위: AppData ms-playwright
    candidates.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"))
    for ms_dir in candidates:
        if not os.path.exists(ms_dir):
            continue
        try:
            for entry in sorted(os.listdir(ms_dir), reverse=True):
                if entry.startswith("chromium_headless_shell"):
                    for root, _, files in os.walk(os.path.join(ms_dir, entry)):
                        if "chrome-headless-shell.exe" in files:
                            return os.path.join(root, "chrome-headless-shell.exe")
        except Exception:
            pass
    return ""

_HEADLESS_SHELL = _find_headless_shell()

_URL = "https://gemini.google.com/"

# 제미나이 요청 화면을 눈으로 보려면 True(디버그용). 평소/배포는 False(헤드리스 저사양).
_VISIBLE = False

# ★ 제미나이 페이지 새로고침/새 채팅 직후, '입력창이 뜬 다음'에도 너무 빨리 제출하면 먹통이
#   된다(사용자 실측). → 입력창이 뜬 뒤 이만큼(초) 기다렸다가 입력·제출한다.
#   앱별로 다르게: 포스트봇(느린 인터넷)=5초, 블로그봇(빠른 인터넷)=1초. set_settle_before_submit로 조절.
_SETTLE_BEFORE_SUBMIT = 5

# 이 시간(초) 이상 아무 요청이 없으면 제미나이 페이지를 자동 새로고침(세션 신선 유지). 15분.
_IDLE_REFRESH_SEC = 900


def set_settle_before_submit(sec):
    """제출 전 정착 대기(초)를 앱별로 설정. 블로그봇처럼 인터넷 빠른 환경은 짧게(1)."""
    global _SETTLE_BEFORE_SUBMIT
    try:
        _SETTLE_BEFORE_SUBMIT = max(0, float(sec))
    except Exception:
        pass

_INPUT_SELS = [
    'div[contenteditable="true"][data-placeholder]',
    'rich-textarea div[contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    'p[data-placeholder]',
    'textarea',
]

# 좁은(정확한 모델 응답) 선택자를 먼저, 넓은 컨테이너('Gemini said' 라벨 포함 위험)는 뒤로.
# Gemini UI 개편으로 응답이 아래 컨테이너에 뜰 수 있어 폭넓게 둔다(실측 보강).
_RESP_SELS = [
    'message-content .markdown',
    'model-response .markdown',
    '.markdown.markdown-main-panel',
    '[data-message-author-role="model"] .markdown',
    '.model-response-text .markdown',
    '.model-response-text',
    'message-content.model-response-text',
    'message-content',
    '.response-container-content',
    'div.response-content',
    '[id^="model-response"]',
    '.conversation-container .markdown',
    '.markdown',
]

# ── 전용 스레드 상태 ──────────────────────────────────
_task_queue       = _queue.Queue()
_worker_thread    = None
_is_running       = False
_cached_input_sel = None


def start():
    """Playwright 전용 스레드를 시작합니다."""
    global _worker_thread, _is_running
    if _is_running and _worker_thread and _worker_thread.is_alive():
        return
    _is_running = True
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="PlaywrightWorker")
    _worker_thread.start()
    # 브라우저가 준비될 때까지 대기 (최대 30초)
    _submit_task("__ping__", timeout_sec=30)


def stop():
    """전용 스레드를 종료하고, 워커가 '완전히 죽을 때까지' 기다린다(브라우저 창 닫힘까지).
    ★ join 안 하면: stop 직후 start 할 때 죽어가던 옛 워커의 finally가 새 워커의
      _is_running 을 꺼버려 → 새 제미나이 창이 떴다가 바로 닫히고, ping이 30초 타임아웃까지
      대기(=엔진 준비가 30초 지연). 그래서 여기서 join + 큐 비움으로 깨끗이 종료한다."""
    global _is_running, _worker_thread
    _is_running = False
    _task_queue.put(None)  # 종료 신호
    t = _worker_thread
    if t is not None and t is not threading.current_thread() and t.is_alive():
        t.join(timeout=15)  # 브라우저 창이 닫히고 워커가 끝날 때까지 대기
    _worker_thread = None
    # 남아있을 수 있는 종료 신호(None) 등을 큐에서 비워 다음 start 를 깨끗하게 시작
    try:
        while True:
            _task_queue.get_nowait()
    except _queue.Empty:
        pass


def restart():
    """기존에 열어둔 브라우저 창을 완전히 닫고 새로 시작합니다.
    (기존 워커 스레드가 완전히 종료될 때까지 기다린 뒤 시작해야
     같은 큐를 두 스레드가 동시에 읽는 경쟁 상태를 피할 수 있음)"""
    global _worker_thread, _is_running
    if _is_running or (_worker_thread is not None and _worker_thread.is_alive()):
        stop()
        t = _worker_thread
        if t is not None:
            t.join(timeout=15)  # 브라우저 창이 닫힐 때까지 대기
    # 남아있을 수 있는 종료 신호를 큐에서 비움
    try:
        while True:
            _task_queue.get_nowait()
    except _queue.Empty:
        pass
    _worker_thread = None
    _is_running = False
    start()


def is_connected() -> bool:
    return _is_running and _worker_thread is not None and _worker_thread.is_alive()


def query(prompt: str, timeout_sec: int = 150, cancel_event=None, min_len: int = 40,
          paste: bool = False) -> str:
    """Gemini에 프롬프트를 전송하고 응답을 반환합니다.
    min_len: '유효 응답' 최소 길이(짧은 댓글은 낮게).
    paste: True면 '붙여넣기(클립보드+Ctrl+V)'로 제출(카페댓글 전용). False(기본)면 블로그/카페
           글쓰기의 '원래 방식(fill+Enter)'으로 제출 — 그쪽은 원래 잘 되던 방식 그대로 둔다."""
    if not is_connected():
        raise RuntimeError("브라우저가 연결되지 않았습니다. 크롬 로드 버튼을 눌러 주세요.")
    return _submit_task(prompt, timeout_sec, cancel_event=cancel_event,
                        min_len=min_len, paste=paste)


# ── 내부 구현 ─────────────────────────────────────────

def _submit_task(prompt: str, timeout_sec: int, cancel_event=None, min_len: int = 40,
                 paste: bool = False) -> str:
    """태스크를 큐에 넣고 결과를 기다립니다."""
    result_event = threading.Event()
    result_box   = [None, None]  # [status, value]
    _task_queue.put((prompt, timeout_sec, min_len, paste, result_event, result_box))
    deadline = time.time() + timeout_sec + 30
    # cancel_event를 0.3초마다 확인하며 대기
    while time.time() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            result_box[0], result_box[1] = "err", "중단됨"
            break
        if result_event.wait(timeout=0.3):
            break
    status, value = result_box
    if status == "err":
        raise RuntimeError(value)
    return value or ""


def _worker_loop():
    """Playwright 전용 스레드 메인 루프."""
    global _is_running
    page = None

    _LAUNCH_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        # ── 저사양/저RAM·느린 인터넷 경량화 ──
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--mute-audio",
        # 이미지 렌더/다운로드를 엔진 레벨에서 끔 (route 인터셉트보다 가볍고 안전)
        "--blink-settings=imagesEnabled=false",
    ]

    with sync_playwright() as pw:
        browser = None
        # ⚠️ TEMP: _VISIBLE 이면 창이 보이는 시스템 크롬/엣지로 띄운다(헤드리스 셸은 창 못 띄움).
        if _VISIBLE:
            for channel in ("chrome", "msedge"):
                try:
                    browser = pw.chromium.launch(channel=channel, headless=False, args=_LAUNCH_ARGS)
                    break
                except Exception:
                    continue
            if browser is None:
                try:
                    browser = pw.chromium.launch(headless=False, args=_LAUNCH_ARGS)
                except Exception:
                    pass
        # 1순위(배포): chrome-headless-shell = '완전 무창'(창·작업표시줄 아예 없음). 사용자 요청 —
        #  진짜 안 보이게. (풀 크롬보다 느리지만 창이 절대 안 뜬다.)
        if browser is None and _HEADLESS_SHELL:
            try:
                browser = pw.chromium.launch(
                    executable_path=_HEADLESS_SHELL,
                    headless=True, args=_LAUNCH_ARGS)
            except Exception:
                pass
        # 2순위(폴백): 시스템 크롬/엣지 헤드리스(headless-shell 이 없을 때만)
        if browser is None:
            for channel in ("chrome", "msedge"):
                try:
                    browser = pw.chromium.launch(channel=channel, headless=True, args=_LAUNCH_ARGS)
                    break
                except Exception:
                    continue
        try:
            page = _open_gemini(browser)
            _last_activity = time.time()   # 유휴 자동 새로고침 판단용

            while _is_running:
                try:
                    task = _task_queue.get(timeout=0.5)
                except _queue.Empty:
                    # 15분 이상 아무 요청이 없으면 제미나이를 자동 새로고침해 세션을 신선하게 유지
                    # (오래 방치되면 페이지가 죽거나 첫 요청이 느려지던 문제 예방).
                    if time.time() - _last_activity > _IDLE_REFRESH_SEC:
                        try:
                            page = _open_gemini(browser)
                        except Exception:
                            pass
                        _last_activity = time.time()
                    continue

                if task is None:  # 종료 신호
                    break

                _last_activity = time.time()   # 요청 수신 = 활동(유휴 타이머 리셋)
                prompt, timeout_sec, min_len, paste, result_event, result_box = task

                if prompt == "__ping__":
                    result_box[0], result_box[1] = "ok", "ready"
                    result_event.set()
                    _last_activity = time.time()
                    continue

                try:
                    _new_chat(page)
                    text = _do_query(page, prompt, timeout_sec, min_len, paste)
                    result_box[0], result_box[1] = "ok", text
                except Exception as e:
                    result_box[0], result_box[1] = "err", str(e)
                    # 페이지 리셋 시도
                    try:
                        page = _open_gemini(browser)
                    except Exception:
                        pass
                finally:
                    result_event.set()
                    _last_activity = time.time()   # 처리 완료 시각 기준으로 유휴 카운트

        finally:
            try:
                browser.close()
            except Exception:
                pass
            _is_running = False


def _open_gemini(browser) -> object:
    """새 컨텍스트로 Gemini 페이지를 엽니다.
    (이미지는 실행 플래그 imagesEnabled=false 로 끔 — route 인터셉트는 느린 VM에서
     요청마다 부하/간섭을 줘 응답이 늦어지는 문제가 있어 사용하지 않음.)"""
    # 기존 컨텍스트(창)를 먼저 모두 닫아 창 누적 방지 — 오류 리셋 때마다 새 창이 쌓이던 문제.
    try:
        for _c in list(browser.contexts):
            try:
                _c.close()
            except Exception:
                pass
    except Exception:
        pass
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    # 붙여넣기(navigator.clipboard.writeText + Ctrl+V) 위해 클립보드 권한 부여
    try:
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=_URL)
    except Exception:
        pass
    page = ctx.new_page()
    page.goto(_URL, wait_until="domcontentloaded", timeout=60000)
    _wait(3.5)  # 저사양/VM·느린 인터넷 렌더링 대기
    _dismiss_popups(page)
    return page  # 제출 직전 정착 대기(_SETTLE_BEFORE_SUBMIT)는 _do_query 에서 공통 처리


def _new_chat(page):
    """새 채팅을 시작합니다."""
    clicked = False
    for sel in [
        'button[aria-label*="New chat"]',
        'button[aria-label*="새 채팅"]',
        'mat-icon:has-text("edit_square")',
    ]:
        try:
            page.locator(sel).first.click(timeout=1000)
            clicked = True
            _wait(0.3)
            break
        except PWTimeout:
            continue

    if clicked:
        # 확인 팝업 처리: "현재 채팅을 지우고 새 채팅을 만드시겠습니까?"
        for confirm_sel in [
            'button:has-text("새 채팅")',
            'button:has-text("New chat")',
        ]:
            try:
                btn = page.locator(confirm_sel).last
                if btn.is_visible(timeout=800):
                    btn.click()
                    _wait(0.3)
                    break
            except Exception:
                continue
        return

    # 버튼 못 찾으면 URL 재접속
    try:
        page.goto(_URL, wait_until="domcontentloaded", timeout=45000)
        _wait(0.5)
        _dismiss_popups(page)
    except Exception:
        pass


def _do_query(page, prompt: str, timeout_sec: int, min_len: int = 40, paste: bool = False) -> str:
    """실제 쿼리 실행.
    paste=False(블로그/카페 글쓰기 — 원래 방식): fill(prompt) → Enter.
    paste=True(카페댓글): 클립보드 붙여넣기(Ctrl+V) → 보내기 버튼/Enter."""
    input_el = _find_input(page, total_timeout=20.0)
    if input_el is None:
        # 저사양/VM·느린 인터넷에서 SPA 렌더링이 느린 경우 대비: 재접속 후 더 길게 재시도
        for _ in range(2):
            try:
                page.goto(_URL, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            _wait(2)
            _dismiss_popups(page)
            input_el = _find_input(page, total_timeout=20.0)
            if input_el is not None:
                break
        if input_el is None:
            raise RuntimeError("Gemini 입력창을 찾을 수 없습니다.")

    # ★ 입력창이 떴어도 '너무 빨리' 제출하면 먹통 → 여기서 정착 대기 후 입력·제출한다(공통).
    _wait(_SETTLE_BEFORE_SUBMIT)

    if not paste:
        # ── 블로그/카페 글쓰기: 원래 방식(fill → Enter) 그대로. ──
        input_el.click()
        _wait(0.05)
        input_el.fill(prompt)
        _wait(0.1)
        page.keyboard.press("Enter")
        return _await_and_extract(page, timeout_sec, min_len)

    # ── 카페댓글: 붙여넣기(클립보드 + Ctrl+V) → 보내기 버튼/Enter. ──
    input_el.click()
    _wait(0.1)
    try:
        input_el.fill("")          # 기존 내용만 비움
    except Exception:
        pass
    _entered = False
    try:
        page.evaluate("(t) => navigator.clipboard.writeText(t)", prompt)
        _wait(0.2)
        input_el.click()
        _wait(0.1)
        page.keyboard.press("Control+V")
        _entered = True
    except Exception:
        _entered = False
    if not _entered:
        # 폴백: 실제 키 입력(줄바꿈 Shift+Enter)
        try:
            _lines = str(prompt).split("\n")
            for _i, _line in enumerate(_lines):
                if _i > 0:
                    page.keyboard.down("Shift")
                    page.keyboard.press("Enter")
                    page.keyboard.up("Shift")
                if _line:
                    page.keyboard.type(_line, delay=3)
        except Exception:
            try:
                input_el.fill(prompt)
            except Exception:
                pass
    _wait(0.4)
    _sent = False
    for _sel in ('button[aria-label*="보내기"]', 'button[aria-label*="Send"]',
                 'button.send-button', 'button[mattooltip*="보내기"]',
                 'button[mattooltip*="Send"]'):
        try:
            _b = page.locator(_sel).first
            if _b.count() and _b.is_visible():
                _b.click(timeout=3000)
                _sent = True
                break
        except Exception:
            continue
    if not _sent:
        page.keyboard.press("Enter")

    return _await_and_extract(page, timeout_sec, min_len)


def _find_input(page, total_timeout: float = 12.0):
    """입력창 탐색 — 느린 환경 대비, 마감시각까지 짧은 간격으로 반복 폴링."""
    global _cached_input_sel
    # 캐시된 셀렉터 먼저 시도
    if _cached_input_sel:
        try:
            el = page.locator(_cached_input_sel).first
            if el.is_visible():
                return el
        except Exception:
            _cached_input_sel = None

    deadline = time.time() + total_timeout
    while time.time() < deadline:
        for sel in _INPUT_SELS:
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    _cached_input_sel = sel
                    return el
            except Exception:
                continue
        _wait(0.4)
    return None


def _dismiss_popups(page):
    for text in ["동의", "I agree", "Accept", "Agree", "확인", "OK"]:
        try:
            page.locator(f"button:has-text('{text}')").first.click(timeout=250)
            _wait(0.2)
        except PWTimeout:
            pass


_STOP_SEL = 'button[aria-label*="Stop"], button[aria-label*="중지"]'

# Gemini 페이지 메뉴/푸터 등 '응답이 아닌' 텍스트 표식 (느린 PC에서 잘못 수집되는 것들)
_JUNK_MARKERS = (
    "opens in a new window", "get gemini app", "about gemini",
    "for business", "google apps", "sign in",
)


def _is_junk(text: str) -> bool:
    low = (text or "").lower()
    return sum(1 for m in _JUNK_MARKERS if m in low) >= 2


def _looks_valid(md: str, min_len: int = 40) -> bool:
    t = (md or "").strip()
    return len(t) >= min_len and not _is_junk(t)


def _stop_visible(page) -> bool:
    try:
        return page.locator(_STOP_SEL).first.is_visible()
    except Exception:
        return False


def _await_and_extract(page, timeout_sec: int, min_len: int = 40) -> str:
    """응답 시작(Stop 버튼) 대기 → 완료(Stop 사라짐) 대기 → 모델 응답이 '유효 내용'으로
    채워질 때까지 폴링 추출. 저사양 PC에서 응답이 늦게 시작돼 메뉴/푸터가 잘못
    수집되던 문제를 막는다(시작 대기 시간↑, body 폴백 제거, 메뉴 검출).
    min_len: 유효 응답 최소 길이(짧은 댓글은 낮게 줘 감지 안정화)."""
    overall = time.time() + timeout_sec
    # 짧은 응답 모드(min_len<20 = 댓글)면 HTML 길이 가드도 낮춰(짧은 응답이 버려지지 않게).
    _mh = 3 if min_len < 20 else 20
    # 1) 응답 시작 대기 (Stop 버튼 또는 유효 텍스트가 보일 때까지)
    appear_deadline = time.time() + min(45, timeout_sec)
    while time.time() < appear_deadline:
        if _stop_visible(page) or _looks_valid(_extract_model_md(page, _mh), min_len):
            break
        _wait(0.4)
    # 2) 완료 대기: '생성중(Stop)'이 아니고 '텍스트 길이가 더 안 늘 때'까지 폴링.
    #    (예전엔 40자만 넘으면 바로 반환해, 느린 PC에서 상단 일부만 가져오는 문제가 있었음)
    best = ""
    last_len = -1
    stable = 0          # 생성중 아님 + 길이 고정
    stable_any = 0      # (생성중 여부 무관) 길이 고정 — Stop 감지가 멈춰도 탈출
    while time.time() < overall:
        md = _extract_model_md(page, _mh)
        if md:
            _check_gemini_error(md)
            if len(md) >= len(best):
                best = md
        cur_len = len(md)
        streaming = _stop_visible(page)
        if cur_len > 0 and cur_len == last_len:
            stable_any += 1
            if not streaming:
                stable += 1
            # 완료 판정: 생성중 아니고 길이 고정 ~1초, 또는 (Stop 감지 불안정) 길이 고정 ~3.5초
            if stable >= 2 or stable_any >= 7:
                break
        else:
            stable = 0
            stable_any = 0
        last_len = cur_len
        _wait(0.5)
    if best and not _is_junk(best):
        return best
    # 진단: 응답이 화면엔 있는데 못 긁는 경우, 실제 응답이 담긴 요소의 태그·클래스를 남긴다.
    dump = ""
    try:
        dump = page.evaluate(r"""() => {
            const out = [];
            const nodes = document.querySelectorAll(
                'message-content, .markdown, [class*="response"], [class*="model"], [id*="response"]');
            for (const n of nodes) {
                const t = (n.innerText || '').trim();
                if (t.length > 15) {
                    out.push(n.tagName.toLowerCase() + '.' +
                        String(n.className || '').slice(0, 45) + '=[' + t.slice(0, 25) + ']');
                }
                if (out.length >= 4) break;
            }
            return out.join(' ; ');
        }""") or ""
    except Exception:
        pass
    raise RuntimeError(f"응답 추출 실패(셀렉터 불일치 의심). 응답요소후보: {dump}")


_GEMINI_ERR_PATTERNS = [
    "Something went wrong",
    "문제가 발생했습니다",
    "오류가 발생했어요",
]


def _check_gemini_error(text: str):
    for pat in _GEMINI_ERR_PATTERNS:
        if pat.lower() in text.lower():
            raise RuntimeError("gemini_server_error")


# 모델 응답 앞에 붙는 라벨(헤딩/스크린리더용) — 본문에서 제거
_LABEL_TEXTS = (
    "gemini said", "gemini", "gemini가 말했습니다", "gemini가 답했습니다",
    "gemini의 답변", "모델 응답", "model response",
)


def _strip_label(md: str) -> str:
    """본문 맨 앞의 'Gemini said' 같은 라벨 줄을 제거."""
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].strip().lstrip("#").strip().rstrip(":：").strip().lower()
        if s == "":
            i += 1
            continue
        if s in _LABEL_TEXTS:
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def _extract_model_md(page, min_html: int = 20) -> str:
    """모델 응답 영역(_RESP_SELS)에서만 추출. 못 찾으면 '' 반환.
    (page.inner_text('body') 폴백은 메뉴/푸터를 긁어오므로 제거.)
    min_html: 응답으로 인정할 최소 HTML 길이. 기본 20은 긴 글 기준이라 '짧은 댓글'
    (HTML 20자 미만)을 통째로 버리는 문제가 있어, 짧은 응답 모드에선 낮춰서 부른다.
    _RESP_SELS는 메뉴가 아닌 '모델 응답 컨테이너'만 노리므로 낮춰도 안전하다."""
    for sel in _RESP_SELS:
        try:
            els = page.locator(sel).all()
            if els:
                html = els[-1].inner_html(timeout=5000).strip()
                if len(html) > min_html:
                    return _strip_label(_html_to_markdown(html))
        except Exception:
            continue
    return ""


def _html_to_markdown(html: str) -> str:
    """Gemini HTML 응답을 마크다운으로 변환합니다."""
    from html.parser import HTMLParser

    class _Conv(HTMLParser):
        def __init__(self):
            super().__init__()
            self.out = []
            self._stack = []
            self._bq_depth = 0
            self._li_depth = 0
            self._list_type_stack = []  # 'ol' | 'ul' per nesting level
            self._ol_counters     = []  # item counter per ol level
            self._prev_ol_counter = 0   # 이전 <ol> 닫힐 때의 카운터 (연속 <ol> 대응)
            # 표(table) 변환용 — 제미나이가 요약 표를 주면 마크다운 표로 바꾼다
            self._table = None   # 행 목록
            self._row = None     # 현재 행
            self._cell = None    # 현재 칸

        def _in_bq(self): return self._bq_depth > 0
        def _in_li(self): return self._li_depth > 0
        def _in_ol(self):
            return bool(self._list_type_stack) and self._list_type_stack[-1] == 'ol'

        def _reset_ol_seq(self):
            self._prev_ol_counter = 0

        def handle_starttag(self, tag, attrs):
            self._stack.append(tag)
            t = tag.lower()

            # ── 표 안에서는 별도 수집 ──
            if t == 'table':
                self._table = []
                return
            if self._table is not None:
                if t == 'tr':
                    self._row = []
                elif t in ('td', 'th'):
                    self._cell = []
                return

            if t == 'h1':
                self._reset_ol_seq(); self.out.append('\n# ')
            elif t == 'h2':
                self._reset_ol_seq(); self.out.append('\n## ')
            elif t == 'h3':
                self._reset_ol_seq(); self.out.append('\n### ')
            elif t == 'h4':
                self._reset_ol_seq(); self.out.append('\n#### ')
            elif t == 'hr':
                self._reset_ol_seq(); self.out.append('\n---\n')
            elif t == 'br': self.out.append('\n')
            elif t in ('strong', 'b'): self.out.append('**')
            elif t in ('em', 'i'):     self.out.append('*')
            elif t == 'blockquote':
                self._reset_ol_seq()
                self._bq_depth += 1
            elif t in ('ul', 'ol'):
                self._list_type_stack.append(t)
                if t == 'ol':
                    # start 속성 직접 읽기 (없으면 1)
                    try:
                        start_val = int(next((v for k, v in attrs if k == 'start'), 1))
                    except (ValueError, TypeError):
                        start_val = 1
                    self._ol_counters.append(start_val - 1)  # li에서 +1하므로 -1
                else:
                    self._ol_counters.append(0)
                    self._reset_ol_seq()
            elif t == 'li':
                self._li_depth += 1
                if self._in_ol():
                    self._ol_counters[-1] += 1
                    self.out.append(f'\n{self._ol_counters[-1]}. ')
                else:
                    self.out.append('\n- ')
            elif t == 'p':
                if self._in_bq():
                    self.out.append('\n> ')
                elif self._in_li():
                    pass
                else:
                    self._reset_ol_seq()
                    self.out.append('\n')
            elif t == 'div':
                if not self._in_bq() and not self._in_li():
                    self._reset_ol_seq()
                    self.out.append('\n')
            elif t == 'code': self.out.append('`')

        def handle_endtag(self, tag):
            if self._stack and self._stack[-1] == tag:
                self._stack.pop()
            t = tag.lower()

            # ── 표 닫을 때 마크다운 표로 뱉는다 ──
            if self._table is not None:
                if t in ('td', 'th'):
                    if self._cell is not None and self._row is not None:
                        self._row.append(' '.join(''.join(self._cell).split()))
                    self._cell = None
                    return
                if t == 'tr':
                    if self._row:
                        self._table.append(self._row)
                    self._row = None
                    return
                if t == 'table':
                    rows, self._table = self._table, None
                    if rows:
                        w = max(len(r) for r in rows)
                        rows = [r + [''] * (w - len(r)) for r in rows]
                        self.out.append('\n\n| ' + ' | '.join(rows[0]) + ' |')
                        self.out.append('\n| ' + ' | '.join(['---'] * w) + ' |')
                        for r in rows[1:]:
                            self.out.append('\n| ' + ' | '.join(r) + ' |')
                        self.out.append('\n')
                    return
                return
            if t in ('h1', 'h2', 'h3', 'h4'): self.out.append('\n')
            elif t in ('strong', 'b'): self.out.append('**')
            elif t in ('em', 'i'):     self.out.append('*')
            elif t == 'code':          self.out.append('`')
            elif t == 'blockquote':
                self._bq_depth = max(0, self._bq_depth - 1)
                self.out.append('\n')
            elif t in ('ul', 'ol'):
                if self._list_type_stack:
                    closed_type = self._list_type_stack.pop()
                    if self._ol_counters:
                        if closed_type == 'ol':
                            self._prev_ol_counter = self._ol_counters[-1]
                        else:
                            self._prev_ol_counter = 0
                        self._ol_counters.pop()
                self.out.append('\n')
            elif t == 'li':
                self._li_depth = max(0, self._li_depth - 1)
                self.out.append('\n')
            elif t in ('p', 'div'): self.out.append('\n')

        def handle_data(self, data):
            if self._cell is not None:
                self._cell.append(data)
                return
            if self._table is not None:
                return          # 표 안의 자투리 공백은 버린다
            self.out.append(data)

        def handle_entityref(self, name):
            import html as _h
            self.handle_data(_h.unescape(f'&{name};'))

        def handle_charref(self, name):
            import html as _h
            self.handle_data(_h.unescape(f'&#{name};'))

    conv = _Conv()
    conv.feed(html)
    md = ''.join(conv.out)
    # 연속 빈 줄 정리
    import re as _re
    md = _re.sub(r'\n{3,}', '\n\n', md)
    return md.strip()


def _wait(sec: float):
    time.sleep(sec)
