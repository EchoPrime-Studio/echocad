# 도면(DXF)에 든 엔티티를 핸들·종류·도면층으로 세고, 골라낸 엔티티만 작은 DXF 로 잘라 내는 모듈
#
# 가져오기 보고서의 기준이다. "도면에 있는 것" 을 우리가 읽은 결과가 아니라 파일 자체에서
# 센다 - 그래야 가져오다 잃은 것이 드러난다. 피처마다 붙어 있는 cad_handle(그룹 코드 5)과
# 맞대면 무엇이 빠졌는지 엔티티 단위로 정확히 나온다.
#
# 모형 공간(ENTITIES 절에서 67=1 이 아닌 것)만 센다. 배치(종이) 공간은 가져오지 않는다.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 그려지는 것이 아니라 도면 관리용인 엔티티. 세지 않는다.
NOT_GEOMETRY = {"VIEWPORT", "SEQEND", "ATTRIB", "VERTEX", "ENDBLK", "BLOCK"}


@dataclass
class Entity:
    handle: str
    kind: str
    layer: str
    start: int            # 원본 줄 번호(그룹 코드 0 줄)
    end: int              # 다음 엔티티 시작 줄(포함 안 함)
    block: str = ""       # INSERT 가 가리키는 블록 이름(그룹 코드 2)


@dataclass
class Census:
    entities: list[Entity] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)     # 잘라 낼 때 쓰는 원문
    header_version: str = ""
    blocks: dict[str, list[str]] = field(default_factory=dict)   # 블록 이름 -> 안에 든 엔티티 종류

    def by_handle(self) -> dict[str, Entity]:
        return {e.handle.upper(): e for e in self.entities if e.handle}


def read(dxf: Path, utf8: bool = True) -> Census:
    """DXF 를 한 번 훑어 모형 공간 엔티티 목록을 만든다."""
    text = Path(dxf).read_bytes().decode("utf-8" if utf8 else "cp949", errors="replace")
    lines = text.splitlines()
    out = Census(lines=lines)
    n = len(lines)
    # 헤더 버전($ACADVER)
    for i in range(min(n - 3, 400)):
        if lines[i].strip() == "$ACADVER":
            out.header_version = lines[i + 2].strip()
            break
    # ENTITIES 절 찾기
    i = 0
    while i < n - 1:
        if lines[i].strip() == "2" and lines[i + 1].strip() == "ENTITIES" and i > 0 \
                and lines[i - 1].strip() == "SECTION":
            break
        i += 1
    else:
        return out
    i += 2
    current = None
    depth_block = False           # POLYLINE 뒤의 VERTEX/SEQEND 는 앞 엔티티에 붙인다
    while i < n - 1:
        code, value = lines[i].strip(), lines[i + 1].strip()
        if code == "0":
            if value == "ENDSEC":
                if current is not None:
                    current.end = i
                break
            if value in ("VERTEX", "SEQEND", "ATTRIB") and current is not None:
                i += 2                # 앞 엔티티(폴리라인·블록 삽입)의 일부
                continue
            if current is not None:
                current.end = i
            current = Entity(handle="", kind=value, layer="0", start=i, end=i)
            current._paper = False
            out.entities.append(current)
        elif current is not None:
            if code == "5" and not current.handle:
                current.handle = value
            elif code == "8" and current.layer == "0":
                current.layer = value
            elif code == "67" and value == "1":
                current._paper = True
            elif code == "2" and current.kind == "INSERT" and not current.block:
                current.block = value
        i += 2
    out.entities = [e for e in out.entities
                    if not getattr(e, "_paper", False) and e.kind not in NOT_GEOMETRY]
    out.blocks = _read_blocks(lines)
    return out


def _read_blocks(lines: list[str]) -> dict[str, list[str]]:
    """BLOCKS 절을 훑어 블록 이름마다 안에 든 엔티티 종류를 모은다.

    블록 참조(INSERT)가 왜 안 들어왔는지 말하려면 블록 안을 봐야 한다. 속성 정의만 있고
    그릴 도형이 없는 블록이 실제로 있다 - 그때 "읽지 못함" 이라고만 하면 사용자가 원인을
    알 수 없다(2026-09-25 구매자 점검).
    """
    n = len(lines)
    i = 0
    while i < n - 1:
        if lines[i].strip() == "2" and lines[i + 1].strip() == "BLOCKS" and i > 0 \
                and lines[i - 1].strip() == "SECTION":
            break
        i += 1
    else:
        return {}
    i += 2
    out: dict[str, list[str]] = {}
    name: str | None = None
    kinds: list[str] = []
    while i < n - 1:
        code, value = lines[i].strip(), lines[i + 1].strip()
        if code == "0":
            if value == "ENDSEC":
                break
            if value == "BLOCK":
                name, kinds = None, []
            elif value == "ENDBLK":
                if name:
                    out[name] = kinds
                name = None
            elif name is not None:
                kinds.append(value)
        elif code == "2" and name is None and value != "BLOCKS":
            name = value
        i += 2
    return out


def excerpt(census: Census, handles: list[str], limit_bytes: int = 2_000_000) -> tuple[str, list[str]]:
    """골라낸 엔티티만 담은 최소 DXF 글. (글, 실제로 담은 핸들들).

    도면 전체가 아니라 가져오지 못한 엔티티의 원문만 담는다. 너무 크면 앞에서부터
    limit_bytes 까지만 담는다. 블록 정의·도면층 표는 넣지 않는다 - 재현에 필요한 것은
    엔티티 자체이고, 회사 도면의 나머지는 우리에게 오지 않아야 한다.
    """
    index = census.by_handle()
    body: list[str] = []
    taken: list[str] = []
    size = 0
    for h in handles:
        e = index.get(str(h).upper())
        if e is None:
            continue
        chunk = census.lines[e.start:e.end]
        chunk_size = sum(len(x) + 1 for x in chunk)
        if size + chunk_size > limit_bytes:
            break
        body.extend(chunk)
        taken.append(e.handle)
        size += chunk_size
    version = census.header_version or "AC1015"
    head = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", version, "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES"]
    tail = ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(head + body + tail) + "\n", taken
