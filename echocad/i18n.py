# 사용자에게 보이는 문자열의 번역. 소스는 영어이고 다른 언어는 여기서 찾는다
from __future__ import annotations

import json
from pathlib import Path

# 왜 Qt의 .ts/.qm이 아닌가 —
# QGIS 규약은 i18n/<plugin>_<lang>.qm이지만 .qm을 만들려면 lrelease가 필요하고,
# QGIS 설치와 PyQt5 어디에도 들어 있지 않다(pylupdate5만 있다). 사전 파일 하나가
# 도구 사슬 전체보다 싸고, 번역을 보태는 쪽도 JSON 하나만 두면 된다.
#
# ponytail: 외부 번역자가 Qt Linguist를 요구하면 그때 .ts/.qm으로 옮긴다.

TRANSLATIONS_DIR = Path(__file__).with_name("translations")

#: 언어코드 → {영어 원문: 번역}. 처음 필요할 때 읽는다.
TRANSLATIONS: dict[str, dict[str, str]] = {}
_loaded = False


def _load() -> None:
    """translations/<lang>.json을 전부 읽는다. 깨진 파일 하나가 나머지를 막지 않는다."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        files = sorted(TRANSLATIONS_DIR.glob("*.json"))
    except OSError:
        return
    for path in files:
        try:
            table = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(table, dict):
            TRANSLATIONS.setdefault(path.stem.lower(), {}).update(
                {key: value for key, value in table.items() if isinstance(value, str)})


def available() -> list[str]:
    """번역이 있는 언어코드. 영어는 원문이라 목록에 없다."""
    _load()
    return sorted(TRANSLATIONS)


def _locale() -> str:
    """QGIS가 설정한 UI 언어의 두 글자 코드. QGIS가 없으면 빈 문자열."""
    try:
        from qgis.PyQt.QtCore import QSettings
    except ImportError:
        return ""
    value = QSettings().value("locale/userLocale") or ""
    return str(value)[:2].lower()


def tr(text: str) -> str:
    """영어 원문을 현재 언어로. 번역이 없으면 원문 그대로 돌려준다.

    원문을 키로 쓰는 것은 번역이 빠졌을 때 빈 화면이 아니라 영어가 나오게 하려는
    것이다. 사용자에게 영어는 불편이지만 빈칸은 고장이다.
    """
    _load()
    table = TRANSLATIONS.get(_locale())
    if not table:
        return text
    return table.get(text, text)


def missing(locale: str, texts) -> list[str]:
    """등록된 표에 없는 원문들. 번역 누락을 테스트로 잡으려고 둔다."""
    _load()
    table = TRANSLATIONS.get(locale.lower(), {})
    return [text for text in texts if text not in table]
