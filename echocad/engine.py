# dwg2dxf 실행 파일을 찾아 DWG를 DXF로 변환하는 모듈. LibreDWG는 subprocess로만 호출한다(GPL-3 경계)
from __future__ import annotations

import hashlib
import shutil
# LibreDWG를 별도 프로세스로만 호출하는 것이 GPL-3 경계를 지키는 방법이다.
import subprocess  # nosec B404
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 120

_ROOT = Path(__file__).resolve().parents[2]
_EXE_NAME = "dwg2dxf.exe" if sys.platform == "win32" else "dwg2dxf"

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

# macOS·Linux용 공식 바이너리는 배포되지 않는다. 우리가 빌드해 호스팅하면
# GPL-3 바이너리 재배포가 되므로 사용자가 확보하도록 안내한다 (research R2).
# 2026-08-11 실측 — Homebrew에만 포뮬러가 있고(0.13.3), Debian·Ubuntu·Fedora·Arch
# 공식 저장소에는 LibreDWG가 없다. Linux에 "명령 한 줄" 경로는 존재하지 않는다.
_INSTALL_HINTS = {
    "win32": "LibreDWG 공식 릴리스를 내려받거나 dwg2dxf.exe 경로를 직접 지정하세요.",
    "darwin": "brew install libredwg",
    # Linux는 v1 공식 지원 대상이 아니다(배포판 패키지 부재). 탐지·경로 지정은
    # OS 무관이라 그대로 동작하므로 코드를 막지 않고 안내만 구분한다.
    "linux": "Linux는 공식 지원 대상이 아닙니다.\n"
             "Homebrew(brew install libredwg)나 소스 빌드로 dwg2dxf를 준비한 뒤 경로를 직접 지정하면 동작합니다.",
}

# 공식 win64 릴리스를 버전 고정으로 가리킨다. 릴리스 자산에 딸린 dist.sha256은
# 소스 tarball만 덮으므로 zip 해시는 2026-08-11에 직접 받아 계산한 값이다.
WIN64_URL = "https://github.com/LibreDWG/libredwg/releases/download/0.14.8578/libredwg-0.14.8578-win64.zip"
WIN64_SHA256 = "e5dbe20803f63641c98356af8301a344a2cdb492b9eb574de3be953d763419c6"
# 11.5MB zip에서 실제로 필요한 것만 꺼낸다. 나머지 도구는 쓰지 않는다.
_WIN64_MEMBERS = (
    "dwg2dxf.exe",
    "libredwg-0.dll",
    "libiconv-2.dll",
    "libpcre2-8-0.dll",
    "libpcre2-16-0.dll",
)


class EngineNotFound(Exception):
    """dwg2dxf를 찾지 못했다. 메시지에 OS별 확보 방법이 들어 있다."""


class EngineInstallError(Exception):
    """엔진 확보나 검증에 실패했다."""


@dataclass
class ConvertResult:
    status: str  # ok | unsupported | error | timeout | empty
    dxf: Path | None
    note: str = ""


def install_hint(platform: str | None = None) -> str:
    """실행 중인 OS에 맞는 dwg2dxf 확보 방법."""
    key = platform or sys.platform
    if key.startswith("linux"):
        key = "linux"
    return _INSTALL_HINTS.get(key, _INSTALL_HINTS["linux"])


def find_dwg2dxf(explicit: Path | None = None) -> Path:
    """사용자 지정 경로 → PATH → 저장소 tools/ 순으로 찾는다."""
    if explicit and Path(explicit).exists():
        return Path(explicit)

    on_path = shutil.which("dwg2dxf")
    if on_path:
        return Path(on_path)

    bundled = _ROOT / "tools" / "libredwg" / _EXE_NAME
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


def install_windows_engine(dest_dir: Path) -> Path:
    """공식 win64 릴리스를 내려받아 dwg2dxf와 필요한 DLL만 dest_dir에 푼다.

    macOS·Linux에는 이 경로가 없다. 공식 바이너리가 없어 우리가 빌드해 호스팅하면
    GPL-3 바이너리 재배포가 되기 때문이다 (research R2).
    """
    # ponytail: 진행률 콜백 없이 통째로 받는다. 11.5MB라 대기 표시로 충분하다.
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        archive = Path(tmp.name)

    try:
        # 주소를 https로 못 박는다. 상수가 잘못 바뀌어도 file:/ 같은 스킴이 열리지 않는다.
        if not WIN64_URL.startswith("https://"):
            raise EngineInstallError("다운로드 주소가 https가 아닙니다")

        digest = hashlib.sha256()
        with urllib.request.urlopen(WIN64_URL, timeout=60) as src, archive.open("wb") as dst:  # nosec B310
            for chunk in iter(lambda: src.read(1 << 16), b""):
                digest.update(chunk)
                dst.write(chunk)

        if digest.hexdigest() != WIN64_SHA256:
            raise EngineInstallError("내려받은 파일의 해시가 일치하지 않아 폐기했습니다.")

        with zipfile.ZipFile(archive) as z:
            for name in _WIN64_MEMBERS:
                with z.open(name) as member, (dest_dir / name).open("wb") as out:
                    shutil.copyfileobj(member, out)
    finally:
        archive.unlink(missing_ok=True)

    exe = dest_dir / "dwg2dxf.exe"
    verify_engine(exe)
    return exe


def dwg_version(dwg: Path) -> str:
    """DWG 선두 6바이트의 포맷 버전 문자열. 읽지 못하면 빈 문자열."""
    try:
        with Path(dwg).open("rb") as f:
            return f.read(6).decode("ascii", "replace")
    except OSError:
        return ""


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

    return ConvertResult("ok", out)
