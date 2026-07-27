# -*- coding: utf-8 -*-
"""
중계서버를 파이썬 없이 도는 exe 로 묶는다. (블로그봇 빌드와 같은 방식)

  python build_relay.py

결과: 바탕화면\이로재로타로_중계서버.zip
      (안에 TarotRelay.exe + 무창 크롬 + 실행용 배치들)
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).parent
NAME = "TarotRelay"
OUT_DIR = BASE / "dist" / NAME
PKG_NAME = "이로재로타로중계"
TUNNEL_DOMAIN = "identify-stimuli-reapply.ngrok-free.dev"   # ngrok 고정주소(공개용)


def step(n, msg):
    print("\n[%s] %s" % (n, msg), flush=True)


def rmtree_hard(p, tries=6):
    """백신·탐색기가 붙들고 있으면 지워지지 않으므로 몇 번 재시도하고,
       그래도 안 되면 이름만 바꿔 비켜둔다."""
    import time
    for _ in range(tries):
        if not p.exists():
            return
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(1.5)
    try:
        p.rename(p.with_name(p.name + "_old%d" % int(time.time())))
        print("  (%s 를 못 지워서 옆으로 치워뒀습니다)" % p.name)
    except Exception as e:
        print("  [경고] %s 정리 실패: %s" % (p.name, e))


def main():
    # ── 1. PyInstaller ────────────────────────────────
    step(1, "exe 빌드 중... (2~4분)")
    for d in (BASE / "build", BASE / "dist"):
        rmtree_hard(d)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--console",
        "--collect-all", "playwright",
        "--hidden-import", "gemini_scraper",
        "--distpath", str(BASE / "dist"),
        "--workpath", str(BASE / "build"),
        "--specpath", str(BASE / "build"),
        str(BASE / "tarot_relay.py"),
    ]
    r = subprocess.run(cmd, cwd=str(BASE))
    if r.returncode != 0 or not (OUT_DIR / (NAME + ".exe")).exists():
        print("\n[오류] 빌드 실패")
        return 1

    # ── 2. 무창 크롬 동봉 ─────────────────────────────
    step(2, "무창 크롬(headless shell) 동봉 중...")
    ms = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    src = None
    if ms.exists():
        for e in sorted(ms.iterdir(), reverse=True):
            if e.is_dir() and e.name.startswith("chromium_headless_shell"):
                src = e
                break
    if src:
        dst = OUT_DIR / "ms-playwright" / src.name
        shutil.copytree(src, dst)
        # 헤드리스에 필요 없는 것 정리
        for pat in ("libGLESv2.dll", "libEGL.dll", "LICENSE.headless_shell", "*.d.ts"):
            for f in dst.rglob(pat):
                try:
                    f.unlink()
                except Exception:
                    pass
        print("  동봉 완료:", src.name)
    else:
        print("  [경고] headless shell 을 못 찾았습니다. 중계컴에 크롬/엣지가 있어야 합니다.")

    # ── 2-2. 터널 설정 넣기 ───────────────────────────
    # ngrok.exe 는 넣지 않는다. 윈도우 디펜더가 원격접속 도구로 보고 지워버려서,
    # 중계컴에서 '터널 준비.bat' 이 예외 등록 후 직접 내려받게 한다.
    step("2-2", "터널 설정(주소·토큰) 넣는 중...")
    (OUT_DIR / "tunnel.txt").write_text(TUNNEL_DOMAIN + "\n", encoding="utf-8")
    print("  고정주소:", TUNNEL_DOMAIN)
    secret = BASE / "secrets" / "ngrok.yml"
    if secret.exists():
        shutil.copy2(secret, OUT_DIR / "ngrok.yml")
        print("  인증토큰 포함 (ngrok.yml) — 저장소에는 올라가지 않음")
    else:
        print("  [경고] secrets/ngrok.yml 이 없어 토큰 없이 나갑니다")

    # ── 3. 실행용 파일 복사 ───────────────────────────
    step(3, "실행용 배치 파일 복사 중...")
    for f in ("중계서버 실행.bat", "자동시작 등록.bat", "자동시작 해제.bat",
              "상태확인.bat", "터널 준비.bat", "_백신예외.ps1", "읽어보세요.txt"):
        p = BASE / "exe용" / f
        if p.exists():
            shutil.copy2(p, OUT_DIR / f)
            print("  ", f)

    # ── 4. ZIP ────────────────────────────────────────
    step(4, "ZIP 만드는 중...")
    desktop = Path(os.environ.get("USERPROFILE", "~")) / "Desktop"
    zip_path = desktop / "이로재로타로_중계서버.zip"
    if zip_path.exists():
        zip_path.unlink()
    total = 0
    import time as _t
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(OUT_DIR):
            for fn in files:
                fp = Path(root) / fn
                # 방금 만든 파일을 백신이 붙들고 있으면 잠깐 뒤 다시 시도
                for attempt in range(4):
                    try:
                        z.write(fp, Path(PKG_NAME) / fp.relative_to(OUT_DIR))
                        total += fp.stat().st_size
                        break
                    except OSError as e:
                        if attempt == 3:
                            raise
                        print("  (%s 잠김 — 재시도 %d)" % (fn, attempt + 1))
                        _t.sleep(2)

    print("\n" + "=" * 52)
    print(" 완성:", zip_path)
    print(" 원본 %.0f MB → 압축 %.0f MB" % (total / 1048576, zip_path.stat().st_size / 1048576))
    print("=" * 52)
    return 0


if __name__ == "__main__":
    sys.exit(main())
