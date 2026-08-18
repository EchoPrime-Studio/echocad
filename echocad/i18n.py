# 사용자에게 보이는 문자열의 번역. 소스는 영어이고 다른 언어는 여기서 찾는다
from __future__ import annotations

# 왜 Qt의 .ts/.qm이 아닌가 —
# QGIS 규약은 i18n/<plugin>_<lang>.qm이지만 .qm을 만들려면 lrelease가 필요하고,
# QGIS 설치와 PyQt5 어디에도 들어 있지 않다(pylupdate5만 있다). 언어가 둘이고
# 번역자가 한 명인 동안은 사전 하나가 도구 사슬 전체보다 싸다.
#
# ponytail: 언어가 셋 이상 되거나 외부 번역자가 붙으면 .ts/.qm으로 옮긴다.
# 그때는 tr() 호출부를 QCoreApplication.translate로 바꾸고 pylupdate5를 돌리면 된다.

#: 언어코드 → {영어 원문: 번역}
TRANSLATIONS: dict[str, dict[str, str]] = {}


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
    table = TRANSLATIONS.get(_locale())
    if not table:
        return text
    return table.get(text, text)


def register(locale: str, table: dict[str, str]) -> None:
    """번역표를 등록한다. 같은 언어를 여러 번 등록하면 합친다."""
    TRANSLATIONS.setdefault(locale.lower(), {}).update(table)


def missing(locale: str, texts) -> list[str]:
    """등록된 표에 없는 원문들. 번역 누락을 테스트로 잡으려고 둔다."""
    table = TRANSLATIONS.get(locale.lower(), {})
    return [text for text in texts if text not in table]


from .translations import ko as _ko  # noqa: E402  표를 싣기 위해 마지막에 읽는다

register("ko", _ko.TABLE)
