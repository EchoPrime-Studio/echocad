# dwg2dxf 실행 파일을 찾아 DWG를 DXF로 변환하는 모듈. LibreDWG는 subprocess로만 호출한다(GPL-3 경계)
from __future__ import annotations

import shutil
# LibreDWG를 별도 프로세스로만 호출하는 것이 GPL-3 경계를 지키는 방법이다.
import subprocess  # nosec B404
import sys
import stat
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 120

_ROOT = Path(__file__).resolve().parents[2]
_EXE_NAME = "dwg2dxf.exe" if sys.platform == "win32" else "dwg2dxf"

# Finder·Dock으로 띄운 QGIS.app은 셸이 아니라 launchd의 PATH를 물려받는다.
# 2026-08-16 실측 — `launchctl getenv PATH`는 비어 있고 /etc/paths에도
# /opt/homebrew/bin이 없어, brew로 설치해도 shutil.which가 찾지 못한다.
# 그래서 관례적인 설치 위치를 PATH 다음 순서로 직접 본다.
_EXTRA_DIRS = (
    Path("/opt/homebrew/bin"),  # Homebrew (Apple Silicon)
    Path("/usr/local/bin"),     # Homebrew (Intel)·소스 빌드 기본 prefix
    Path("/opt/local/bin"),     # MacPorts
)

# QGIS는 GUI 앱이라 자식 프로세스를 그냥 띄우면 콘솔 창이 깜빡인다.
# CREATE_NO_WINDOW는 Windows에만 있으므로 다른 OS에서는 빈 인자로 둔다.
NO_CONSOLE = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32" else {}
)

# DWG 파일 선두 6바이트가 포맷 버전이다. R13(AC1012) 이하는 지원 범위 밖 (FR-002).
# 목록에 없는 버전은 dwg2dxf에 맡긴다 — 모르는 미래 버전을 미리 막지 않는다.
# 접두 비교인 이유 — 옛 버전 문자열은 5자(`MC0.0`)라 6바이트와 같지 않다.
_TOO_OLD = (
    "MC0.0", "AC1.2", "AC1.40", "AC1.50", "AC2.10",
    "AC1002", "AC1003", "AC1004", "AC1006", "AC1009", "AC1012",
)

# 엔진은 플러그인이 내려받지 않는다. 공식 저장소 규칙이 "Plugins that utilize
# binaries will not be approved"이고, 같은 부류의 승인된 플러그인들(WhiteboxTools,
# LAStools, OrfeoToolbox, CAD To GIS Convert)이 예외 없이 "사용자가 받아서 경로 지정"
# 방식이다. 자동 다운로드 사례는 확인되지 않았다 (2026-08-17 조사).
#
# 유료판은 우리 채널로 배포하므로 저장소 규칙 대상이 아니다. 그쪽은 bin/에 동봉해
# 사용자가 아무것도 안 해도 되게 한다.
DOWNLOAD_URLS = {
    "win32": "https://github.com/LibreDWG/libredwg/releases",
    "darwin": "https://github.com/EchoPrime-Studio/echocad/releases",
    "linux": "https://github.com/LibreDWG/libredwg/releases",
}

_INSTALL_HINTS = {
    "win32": "LibreDWG 공식 릴리스에서 win64 zip을 내려받아 풀고,\n"
             "그 안의 dwg2dxf.exe 경로를 아래에 지정하세요.",
    # 2026-08-16 실측 — Homebrew stable은 0.13.3이고 --HEAD도 없다. 이 버전은 표본
    # 50건 중 4건에서 출력 DXF가 잘리는데 종료 코드는 0이라 조용히 넘어간다.
    # 같은 표본이 0.14.8578에서는 4건 모두 통과한다(86% → 94%). 그래서 brew를
    # 첫 번째로 권하지 않는다.
    "darwin": "EchoCad 릴리스 페이지에서 macOS용 dwg2dxf를 내려받아 풀고,\n"
              "그 파일을 아래에 지정하세요. 실행 권한과 격리 해제는 플러그인이 처리합니다.\n"
              "(brew install libredwg도 되지만 0.13.3이라 일부 도면이 잘립니다)",
    # Linux는 v1 공식 지원 대상이 아니다(배포판 패키지 부재). 탐지·경로 지정은
    # OS 무관이라 그대로 동작하므로 코드를 막지 않고 안내만 구분한다.
    "linux": "Linux는 공식 지원 대상이 아닙니다.\n"
             "Homebrew(brew install libredwg)나 소스 빌드로 dwg2dxf를 준비한 뒤 경로를 직접 지정하면 동작합니다.",
}


class EngineNotFound(Exception):
    """dwg2dxf를 찾지 못했다. 메시지에 OS별 확보 방법이 들어 있다."""


class EngineInstallError(Exception):
    """엔진 확보나 검증에 실패했다."""


@dataclass
class ConvertResult:
    status: str  # ok | unsupported | error | timeout | empty | truncated
    dxf: Path | None
    note: str = ""


def install_hint(platform: str | None = None) -> str:
    """실행 중인 OS에 맞는 dwg2dxf 확보 방법."""
    key = platform or sys.platform
    if key.startswith("linux"):
        key = "linux"
    return _INSTALL_HINTS.get(key, _INSTALL_HINTS["linux"])


def find_dwg2dxf(explicit: Path | None = None) -> Path:
    """사용자 지정 경로 → PATH → 패키지 관리자 경로 → 저장소 tools/ 순으로 찾는다."""
    if explicit and Path(explicit).exists():
        return Path(explicit)

    on_path = shutil.which("dwg2dxf")
    if on_path:
        return Path(on_path)

    # 유료판이 동봉한 엔진. 패키지 안이라 설치 위치와 무관하게 잡힌다.
    # _ROOT 기준으로 찾으면 설치된 플러그인에서는 플러그인 바깥을 가리켜 절대 못 찾는다.
    packaged = Path(__file__).resolve().parent / "bin" / _EXE_NAME
    if packaged.exists():
        return prepare(packaged)

    for directory in _EXTRA_DIRS:
        candidate = directory / _EXE_NAME
        if candidate.exists():
            return candidate

    bundled = _ROOT / "tools" / "libredwg" / _EXE_NAME  # 개발 중 체크아웃
    if bundled.exists():
        return bundled

    raise EngineNotFound(f"dwg2dxf를 찾을 수 없습니다. {install_hint()}")


def verify_engine(exe: Path) -> str:
    """실행해 보고 dwg2dxf가 맞는지 확인한다. 맞으면 버전 문자열을 돌려준다."""
    try:
        # 사용자가 지정한 엔진 경로를 실행한다. 인자를 리스트로 넘기고 셸을 쓰지
        # 않으므로 셸 주입 경로는 없다.
        proc = subprocess.run(  # nosec B603
            [str(exe), "--version"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace", **NO_CONSOLE,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise EngineInstallError(f"실행할 수 없습니다: {err}") from err

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if "dwg2dxf" not in output:
        raise EngineInstallError(f"dwg2dxf가 아닙니다: {exe}")
    return output.splitlines()[0]


def download_url(platform: str | None = None) -> str:
    """이 OS용 엔진을 받을 수 있는 페이지."""
    key = platform or sys.platform
    if key.startswith("linux"):
        key = "linux"
    return DOWNLOAD_URLS.get(key, DOWNLOAD_URLS["linux"])


def prepare(exe: Path) -> Path:
    """사용자가 고른(또는 동봉된) 파일을 실제로 실행할 수 있게 만든다.

    두 가지를 손봐야 한다. 둘 다 2026-08-17 맥에서 실측했다.

    - 실행 권한: 파이썬 zipfile은 권한 비트를 잃어버려 644로 풀린다.
    - 격리 딱지: 브라우저로 받아 Finder로 풀면 com.apple.quarantine이 붙고, 그러면
      바이너리 실행이 통째로 막힌다(응답 없이 SIGKILL). 사용자가 직접 고른 파일에
      한해 떼어 낸다.
    """
    exe = Path(exe)
    if sys.platform != "win32":
        mode = exe.stat().st_mode
        if not mode & stat.S_IXUSR:
            exe.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if sys.platform == "darwin":
        # xattr는 macOS 기본 제공이다. 없거나 실패해도 치명적이지 않으므로 넘어간다.
        subprocess.run(  # nosec B603
            ["/usr/bin/xattr", "-d", "com.apple.quarantine", str(exe)],
            capture_output=True, check=False, **NO_CONSOLE,
        )
    return exe


def dwg_version(dwg: Path) -> str:
    """DWG 선두 6바이트의 포맷 버전 문자열. 읽지 못하면 빈 문자열."""
    try:
        with Path(dwg).open("rb") as f:
            return f.read(6).decode("ascii", "replace")
    except OSError:
        return ""


def is_complete(dxf: Path) -> bool:
    """DXF가 EOF 마커로 끝나면 True. 꼬리 몇 바이트만 읽으므로 파일 크기와 무관하다.

    dwg2dxf 0.13.3은 쓰다가 실패해도 종료 코드 0으로 끝난다 (2026-08-16 실측,
    표본 50건 중 4건). 그래서 끝까지 쓰였는지는 이 마커로만 알 수 있다.
    EOF는 ASCII라 도면이 CP949든 UTF-8이든 같은 바이트로 나온다.
    """
    try:
        with Path(dxf).open("rb") as f:
            f.seek(max(0, Path(dxf).stat().st_size - 64))
            return f.read().rstrip().endswith(b"EOF")
    except OSError:
        return False


def convert(dwg: Path, out: Path, exe: Path | None = None, timeout: int = TIMEOUT) -> ConvertResult:
    """DWG 하나를 DXF로 변환한다. 예외를 던지지 않고 status로 결과를 알린다."""
    dwg, out = Path(dwg), Path(out)

    version = dwg_version(dwg)
    if version.startswith(_TOO_OLD):
        return ConvertResult("unsupported", None, f"{version} 포맷입니다. R14 이상만 지원합니다")

    if exe is None:
        exe = find_dwg2dxf()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    try:
        # encoding을 명시하지 않으면 Windows에서 cp949 디코드로 죽는다 (PoC 실측 수정분).
        # 리스트 인자 + 셸 미사용. verify_engine과 같은 이유로 안전하다.
        proc = subprocess.run(  # nosec B603
            [str(exe), "-o", str(out), str(dwg)],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", **NO_CONSOLE,
        )
    except subprocess.TimeoutExpired:
        return ConvertResult("timeout", None, f">{timeout}s")

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip()
        note = message.splitlines()[-1][:200] if message else f"exit {proc.returncode}"
        return ConvertResult("error", None, note)

    if not out.exists() or out.stat().st_size == 0:
        return ConvertResult("empty", None, "no output file")

    # 종료 코드가 0이어도 결과가 잘려 있을 수 있다. 그대로 넘기면 ENTITIES 섹션이
    # 통째로 빠진 DXF를 정상으로 읽어 "메타데이터 전용 도면"이라고 오안내한다.
    if not is_complete(out):
        return ConvertResult(
            "truncated", None,
            "결과 DXF가 끝까지 쓰이지 않았습니다. dwg2dxf 0.14 이상이 필요합니다.",
        )

    return ConvertResult("ok", out)
