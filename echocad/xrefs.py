# 외부 참조(XREF)를 찾아 빠진 파일을 알린다. 조용히 빠지면 사용자는 도면이 왜 비었는지 모른다
from __future__ import annotations

from pathlib import Path

from .i18n import tr

# LibreDWG는 BLOCK의 그룹코드 1에 참조 경로뿐 아니라 블록 설명도 넣는다.
# 파일 확장자가 붙은 값만 참조로 본다.
_DRAWING_SUFFIXES = (".dwg", ".dxf")


def referenced_files(dxf: Path) -> list[str]:
    """도면이 참조하는 외부 파일 경로. BLOCK 섹션만 훑는다."""
    found: list[str] = []
    in_block = False
    with Path(dxf).open("r", encoding="utf-8", errors="replace") as handle:
        code = None
        for raw in handle:
            value = raw.strip()
            if code is None:
                code = value
                continue
            group, code = code, None

            if group == "0":
                in_block = value == "BLOCK"
                if value == "ENDSEC" and found:
                    break
            elif in_block and group == "1" and value:
                if value.lower().endswith(_DRAWING_SUFFIXES) and value not in found:
                    found.append(value)
    return found


def _local_path(reference: str) -> Path:
    """도면에 적힌 참조 경로를 지금 OS의 경로로 바꾼다.

    AutoCAD는 구분자를 역슬래시로 쓴다. POSIX에서 역슬래시는 구분자가 아니라
    파일명의 일부라 '.\\STAIR.dwg'가 통째로 한 덩어리 이름이 되고, 도면 바로 옆에
    파일이 있어도 없다고 판정한다 (2026-08-17 맥 실측 — XREF를 쓴 모든 도면에서
    오경보가 나 진짜 누락을 무시하게 만든다).

    슬래시는 Windows에서도 구분자로 통하므로 양쪽 OS에서 같은 결과가 나온다.
    """
    return Path(reference.replace("\\", "/"))


def missing(dxf: Path, source: Path) -> list[str]:
    """참조 중 실제로 없는 파일. 경로는 원본 도면 위치를 기준으로 푼다."""
    base = Path(source).parent
    absent = []
    for reference in referenced_files(dxf):
        candidate = _local_path(reference)
        resolved = candidate if candidate.is_absolute() else base / candidate
        if not resolved.exists():
            # 사용자에게는 도면에 적힌 원래 표기를 그대로 보여 준다.
            absent.append(reference)
    return absent


def note(absent: list[str]) -> str:
    """사용자에게 보여 줄 한 문장. 없으면 빈 문자열."""
    if not absent:
        return ""
    shown = ", ".join(absent[:3])
    more = (tr(" and {count} more").format(count=len(absent) - 3)
            if len(absent) > 3 else "")
    return (tr("External reference files are missing, so their content is absent")
            + f" — {shown}{more}")
