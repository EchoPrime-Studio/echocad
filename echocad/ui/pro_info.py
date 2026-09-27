# 무료판에서 Pro 가 무엇을 더하는지 보여 주는 창
#
# 왜 필요한가 - 무료판(Community 빌드)에는 pro/ 폴더가 없어 "Licence…" 메뉴가 아예
# 만들어지지 않는다. 그러면 사용자는 Pro 가 있다는 것조차 모른다. DWG 를 떨어뜨리거나
# 3차원 솔리드를 만나야만 알게 되는데, 그 전에 물어볼 자리가 있어야 한다.
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPushButton,
                                 QVBoxLayout)

from ..i18n import tr
from ..links import BUY_PAGE_URL as BUY_URL

# 한 덩어리로 둔다. 항목마다 tr() 을 걸면 번역할 문장이 아홉 배로 늘어난다.
_FEATURES = tr(
    "DWG drawings — R14 to 2018, offline, with no other converter needed\n"
    "3D solids — faces with their curved surfaces and holes\n"
    "3D view — turn the model, then take a flat drawing from that angle\n"
    "Elevations — keep the drawing's Z values instead of flattening\n"
    "Block attributes — pull attribute values out into a table\n"
    "Text attaching — turn labels beside a shape into that shape's attributes\n"
    "Polygon closing — close nearly-closed boundaries into areas\n"
    "Georeferencing — move drawing coordinates onto a real CRS\n"
    "Layer mapping — rename CAD layers to your own convention\n"
    "Folder conversion — convert a whole folder in one go"
)


class ProInfoDialog(QDialog):
    """무료판에서 띄우는 안내. 라이선스 입력칸은 없다 - 그건 Pro 빌드의 몫이다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("EchoCad — What Pro adds"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        head = QLabel(tr("DWG drawings open straight in QGIS."))
        head.setWordWrap(True)
        layout.addWidget(head)

        title = QLabel(tr("This free edition reads DXF drawings. Pro adds:"))
        title.setWordWrap(True)
        layout.addWidget(title)

        body = QLabel(_FEATURES)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)

        note = QLabel(tr("Everything runs on your machine. Drawings are never uploaded."))
        note.setWordWrap(True)
        note.setStyleSheet("QLabel { color: #666; }")
        layout.addWidget(note)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buy = QPushButton(tr("See Pro"))
        self.buttons.addButton(buy, QDialogButtonBox.ButtonRole.AcceptRole)
        buy.clicked.connect(self._open_buy)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)
        layout.addWidget(self.buttons)

    def _open_buy(self):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(BUY_URL))
        self.accept()
