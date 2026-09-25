# DWG 한 장을 QGIS로 가져오는 다이얼로그. 파일·좌표계·출력만 받고 나머지는 importer가 한다
from __future__ import annotations

import uuid
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsRectangle
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPalette
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import importer
from ..i18n import tr
from ..importer import DRAWING_KEY, IMPORT_KEY
from ..links import PRO_URL

# 변환 엔진은 무료판 빌드에 없다. 무료판은 DXF 만 읽으므로 엔진을 찾을 일도,
# 설정 화면을 띄울 일도 없다.
try:
    from .. import engine
    from .engine_setup import EngineSetupDialog, resolve_engine
except ImportError:      # 무료판
    engine = None
    EngineSetupDialog = None
    resolve_engine = None

# 상태 코드마다 사용자가 다음에 무엇을 해야 하는지 알려 준다 (SC-003).
_STATUS_MESSAGE = {
    "unsupported": "Unsupported drawing format. Only DWG R14 and newer can be read.",
    "timeout": "Conversion timed out. The drawing may be very large or damaged.",
    "empty": "Converted, but there is nothing to draw. This may be a metadata-only drawing.",
    "truncated": "Conversion stopped part way, so the drawing content is missing.",
    "error": "Conversion failed.",
}


def _is_dark() -> bool:
    """지금 테마가 어두운가. 창 바탕의 밝기로 가른다."""
    window = QApplication.palette().color(QPalette.ColorRole.Window)
    return 0.299 * window.red() + 0.587 * window.green() + 0.114 * window.blue() < 128


def _theme_style() -> str:
    """창 안 모든 위젯의 글자색을 한 번에 정한다.

    QGIS 의 어두운 테마는 **스타일시트**로 칠한다. 그래서 앱 팔레트의 글자색은 검게
    남고, 테마가 닿지 않는 위젯(네이티브 단추, 좌표계 위젯, 꺼진 칸)이 어두운 바탕
    위에 검은 글자로 그려져 안 읽힌다. 하나씩 잡으면 계속 빠뜨린다
    (2026-09-19 지시 "모두 확인해서 처리하라"). 여기서 한 번에 정한다.

    팔레트가 아니라 스타일시트로 주는 이유 - macOS 네이티브 단추는 팔레트를 무시한다.
    """
    text = "#e8e8e8" if _is_dark() else "#1c1c1c"
    dim = "#a8a8a8" if _is_dark() else "#6f6f6f"
    # 어두운 테마에서 체크 칸이 바탕과 같은 색이라 안 보였다(2026-09-23 지시). 테두리를 밝게 긋고,
    # 체크하면 파란 칸에 흰 표시를 그린다. 밝은 테마는 기본 모양이 잘 보여 건드리지 않는다.
    check = ""
    if _is_dark():
        mark = (Path(__file__).parent / "check.svg").as_posix()
        check = (
            "QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #9aa0a8; "
            "border-radius: 3px; background: #1b1c1f; }"
            "QCheckBox::indicator:hover { border-color: #c8ccd2; }"
            f"QCheckBox::indicator:checked {{ background: #4c8dff; border-color: #4c8dff; image: url({mark}); }}"
            "QCheckBox::indicator:disabled { border-color: #555a61; }"
            # 입력 칸도 테두리가 바탕에 묻혔다(허용오차·기준점 칸, 2026-09-23 지시)
            "QLineEdit { border: 1px solid #6b7078; border-radius: 4px; background: #1b1c1f; padding: 3px 6px; }"
            "QLineEdit:hover { border-color: #8a9099; }"
            "QLineEdit:focus { border-color: #4c8dff; }"
            "QLineEdit:disabled { border-color: #45484e; }"
        )
    return check + (
        f"QLabel, QCheckBox, QGroupBox, QRadioButton {{ color: {text}; }}"
        f"QLineEdit, QComboBox, QAbstractSpinBox {{ color: {text}; }}"
        f"QPushButton, QToolButton {{ color: {text}; }}"
        f"QgsProjectionSelectionWidget {{ color: {text}; }}"
        f"QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled,"
        f"QToolButton:disabled, QCheckBox:disabled, QLabel:disabled {{ color: {dim}; }}"
    )


# Pro 가 더하는 것. **이 판이 무엇을 하는지는 앞줄(free_note)에서 먼저 말한다.**
# QGIS 를 깎아내리지 않는다 - 우리가 얹혀 사는 판이고, 읽는 사람도 QGIS 를 좋아한다
# (2026-09-19 지시 "비방조는 안 좋다").
_PITCH = tr(
    "Pro adds:\n"
    "\n"
    "  \u2713 DWG drawings opened directly \u2014 R14 to 2018, offline\n"
    "  \u2713 3D solids \u2014 faces with their curved surfaces and holes\n"
    "  \u2713 3D view \u2014 turn the model, then take a flat drawing from that angle\n"
    "  \u2713 Elevations, text, block attributes, georeference, whole folders\n"
    "\n"
    "{url}"
)


def _text_color() -> str:
    """본문 글자색. 안내문도 이 색으로 쓴다.

    주황으로 칠했더니 문단 전체가 경고처럼 보였다(2026-09-19). 파는 글은 본문으로
    읽혀야 한다 - 주황은 정말 주의가 필요한 곳에만 남긴다.
    """
    return "#e8e8e8" if _is_dark() else "#1c1c1c"


def _warn_color() -> str:
    """정말 주의가 필요한 곳에만 쓰는 주황. 좌표계가 어긋날 때 쓴다."""
    return "#ffb020" if _is_dark() else "#b45309"


# DXF $INSUNITS 중 야드파운드법. 이것만 미터법 경고에서 뺀다 (importer.UNIT_NAMES 참조).
_IMPERIAL_UNITS = {1, 2, 3, 8, 9, 10}


def _hline() -> QFrame:
    """구역을 가르는 가느다란 줄. 위젯 사이에 무엇이 한 덩이인지 보이게 한다."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class _DialogFeedback:
    """importer가 기대하는 setProgress/isCanceled만 갖춘 최소 구현."""

    def __init__(self, bar: QProgressBar):
        self._bar = bar
        self.canceled = False

    def setProgress(self, percent: int):
        # 변환 단계는 얼마나 걸릴지 모른다 - 그동안은 지나가는 막대(range 0,0)로 두고,
        # 읽기 단계에 들어와 처음 숫자가 오면 그때 퍼센트 막대로 바꾼다.
        if self._bar.maximum() == 0:
            self._bar.setRange(0, 100)
        self._bar.setValue(percent)
        QApplication.processEvents()

    def isCanceled(self) -> bool:
        return self.canceled


class ImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 무료판은 DXF 만 읽는다. 제목과 안내문에 DWG 라고 쓰면 못 여는 파일을
        # 약속하는 셈이다 (2026-09-19 지시).
        self.setWindowTitle(tr("EchoCad — Import drawing"))
        self.setMinimumWidth(660)
        # 창 전체에 한 번에 건다. 위젯마다 따로 주면 계속 빠뜨린다.
        self.setStyleSheet(_theme_style())

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        # 창을 늘 내용 크기에 맞춘다. 이게 없으면 남는 자리를 위젯들이 나눠 가져
        # 유료 구역을 접어도 빈 공백이 남는다(2026-09-19 지시 "접으니 공백이잖아").
        # adjustSize() 만으로는 부족했다 - 배치가 남는 자리를 계속 나눠 갖는다.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        # 라벨을 왼쪽 한 줄로 맞춘다. 칸마다 라벨을 위에 얹으면 눈이 세로로 튄다.
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        source = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(tr("Drawing file"))
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse_source)
        source.addWidget(self.source_edit)
        source.addWidget(browse)
        form.addRow(tr("Drawing to import"), source)

        # 프로젝트 좌표계로 채워 둔다. 이미 있는 데이터에 얹는 것이 보통이라
        # 같은 좌표계를 쓰는 것이 맞다. 사용자가 바꾸는 것은 그대로 열려 있다.
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsProject.instance().crs())
        form.addRow(tr("Coordinate system"), self.crs_widget)
        # 도(degree) 단위 좌표계에 미터 도면을 그대로 넣는 것을 막는다. 새 프로젝트의 기본이
        # EPSG:4326 이라 미숙한 사용자는 그냥 [OK] 를 누르고, 그러면 평면도가 도 단위 좌표계에
        # 들어가 축척이 1:10,000,000 처럼 나오고 지도에 맞추기도 뜻이 없어진다
        # (2026-09-25 구매자 점검에서 실제로 그랬다).
        self.crs_warning = QLabel(tr(
            "This project is in degrees, but drawings are in metres. Choose the coordinate "
            "system the drawing was made in — otherwise it lands in the wrong place."))
        self.crs_warning.setWordWrap(True)
        self.crs_warning.setStyleSheet(f"QLabel {{ color: {_warn_color()}; }}")
        self.crs_warning.setVisible(False)
        form.addRow("", self.crs_warning)
        self.crs_widget.crsChanged.connect(self._refresh_crs_warning)

        # 파일(GeoPackage)로 저장하는 칸은 두지 않는다. 가져온 레이어는 QGIS 에서 바로 내보낼 수 있고,
        # 여러 도면을 파일로 한꺼번에 바꾸는 일은 처리 도구(Batch import DWG)가 한다(2026-09-23 지시).
        # 두 판 모두에 있다. 가져오기 자체를 다듬는 것이라 유료로 둘 이유가 없다.
        self.hidden_check = QCheckBox(tr("Skip layers switched off or frozen in the drawing"))
        form.addRow(tr("Layers"), self.hidden_check)
        layout.addLayout(form)
        self._top_form = form

        layout.addWidget(_hline())

        # 이 판이 무엇을 하는지 먼저 말한다. Pro 부터 말하면 무료판이 무엇인지가
        # 안 보이고, DWG 를 약속한 것으로 읽힌다 (2026-09-19 지시).
        self.free_note = QLabel(tr(
            "This edition reads DXF drawings exactly as drawn — layers, colours, "
            "line types, hatches and text."))
        self.free_note.setWordWrap(True)
        layout.addWidget(self.free_note)

        # 유료 칸을 한 덩이로 묶는다. 평평하게 늘어놓으면 기본 기능과 뒤섞여
        # 무엇이 잠긴 것인지 읽히지 않는다. 잠겨 있을 때만 제목과 테두리를 보인다.
        self.pro_box = QGroupBox(tr("EchoCad Pro"))
        pro = QVBoxLayout(self.pro_box)
        pro.setSpacing(6)

        # 잠겼을 때 보여 줄 값어치. 못 쓰는 설정 칸은 감추고 이것만 남긴다 -
        # "Pro 에 이런 단추가 있다" 로는 아무도 안 산다(2026-09-19 지시).
        self.pro_note = QLabel("")
        self.pro_note.setWordWrap(True)
        self.pro_note.setStyleSheet(f"QLabel {{ color: {_text_color()}; }}")
        self.pro_note.setVisible(False)
        pro.addWidget(self.pro_note)

        # 고른 도면에서 읽은 숫자. 남의 설명이 아니라 자기 파일 이야기라 가장 세다.
        self.pro_count = QLabel("")
        self.pro_count.setWordWrap(True)
        self.pro_count.setStyleSheet(
            f"QLabel {{ color: {_text_color()}; font-weight: bold; }}")
        self.pro_count.setVisible(False)
        pro.addWidget(self.pro_count)

        self.pro_see = QPushButton(tr("See Pro"))
        self.pro_see.clicked.connect(self._open_pro)
        self.pro_see.setVisible(False)
        pro.addWidget(self.pro_see, alignment=Qt.AlignmentFlag.AlignLeft)

        # 실제 조작 칸. 잠기면 통째로 감춘다.
        self._pro_locked = False       # _lock_pro_widgets 가 정한다
        self.pro_controls = QWidget()
        controls = QVBoxLayout(self.pro_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self._profile_row(controls)
        # 나머지 Pro 옵션은 늘 쓰는 것이 아니다. 접어 두고 필요할 때 편다(2026-09-23 목업 ①).
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText(tr("Advanced options"))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setAutoRaise(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        controls.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QVBoxLayout(self.advanced)
        advanced.setContentsMargins(18, 0, 0, 0)
        advanced.setSpacing(6)
        self._text_row(advanced)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self._show_advanced)
        controls.addWidget(self.advanced)
        pro.addWidget(self.pro_controls)

        layout.addWidget(self.pro_box)

        # 파일을 고르면 그 자리에서 3차원 솔리드 개수를 세어 보여 준다.
        self.source_edit.textChanged.connect(self._refresh_pro_count)
        self.source_edit.textChanged.connect(self._refresh_rule_summary)
        self.source_edit.textChanged.connect(self._refresh_crs_warning)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.setStyleSheet("QPushButton { padding: 4px 18px; }")
        self.buttons.accepted.connect(self._run)
        self.buttons.rejected.connect(self._cancel)
        layout.addWidget(self.buttons)

        # 접었다 펼 때 창 너비가 흔들리지 않게 최소 너비를 못 박는다. SetFixedSize 는
        # setMinimumWidth 를 무시하므로, 폭만 가진 빈 자리를 하나 깔아 둔다.
        keeper = QWidget()
        keeper.setFixedSize(660, 0)
        layout.addWidget(keeper)

        self._feedback: _DialogFeedback | None = None
        self._lock_pro_widgets()
        self._align_rule_label(pro)

    def _align_rule_label(self, pro_layout):
        """'레이어 이름' 라벨의 오른쪽 끝을 위 칸들의 라벨과 맞춘다(Pro 테두리 안이라 그만큼 뺀다)."""
        widths = [self._top_form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget().sizeHint().width()
                  for i in range(self._top_form.rowCount())
                  if self._top_form.itemAt(i, QFormLayout.ItemRole.LabelRole) is not None]
        inset = pro_layout.contentsMargins().left() + self.pro_box.contentsMargins().left()
        if widths:
            self._rules_label.setMinimumWidth(max(max(widths) - inset, 0))
            self._rules_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _lock_pro_widgets(self):
        """유료 칸을 못 쓰게 하고 이유를 보여 준다.

        모듈이 임포트된다고 쓸 수 있는 것이 아니다. Pro 빌드에서는 항상 임포트되므로,
        만료된 뒤에도 체크박스가 켜져 있어 사용자가 켜고 돌리면 조용히 무시됐다.

        무료판(Community 빌드)에서도 같은 자리에서 잠근다. 칸을 아예 안 그리면 그런
        기능이 있는 줄도 모른다 - 못 쓰는 것이 **보이는** 편이 훨씬 세다
        (2026-09-19 지시).
        """
        try:
            from .. import pro
        except ImportError:
            pro = None

        if pro is not None and pro.unlocked():
            # 이미 산 사람에게는 테두리와 제목을 지우고 값어치 글을 감춘다.
            # 그때는 "Pro 기능" 이 아니라 그냥 기능이고, 파는 글은 군더더기다.
            self.pro_box.setTitle("")
            self.pro_box.setFlat(True)
            self.free_note.setVisible(False)
            self.pro_note.setVisible(False)
            self.pro_count.setVisible(False)
            self.pro_see.setVisible(False)
            self.pro_controls.setVisible(True)
            self._pro_locked = False
            return
        # 값어치는 두 경우 모두 보여 준다. 만료된 사람에게도 팔아야 한다 -
        # 예전에는 만료자에게 "잠겼습니다" 한 줄만 보여 줘서 살 이유를 못 봤다.
        hint = _PITCH.format(url=PRO_URL)
        if pro is None:
            why = tr("EchoCad Pro only")
        else:
            from ..pro import license as licensing

            why = licensing.decide(pro.build_date()).reason
            hint = f"{hint}\n\n{why}"

        for name in ("profile_combo", "text_check", "z_check", "close_edit"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip(why)
        for button in (getattr(self, "profile_buttons", None) or []):
            button.setEnabled(False)
            button.setToolTip(why)
        if getattr(self, "pro_note", None) is not None:
            self.pro_note.setText(hint)
            self.pro_note.setVisible(True)
        # 잠겼다 - 값어치만 남기고 못 쓰는 칸은 감춘다. "Pro 에 이런 단추가 있다" 로는
        # 아무도 안 산다(2026-09-19 지시).
        self.pro_box.setTitle(tr("EchoCad Pro"))
        self.pro_box.setFlat(False)
        self._pro_locked = True
        self.free_note.setVisible(True)
        self.pro_controls.setVisible(False)
        self.pro_note.setVisible(True)
        self.pro_see.setVisible(True)
        self._refresh_pro_count()

    def _open_pro(self):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(PRO_URL))

    def _refresh_pro_count(self):
        """고른 도면의 3차원 솔리드 수를 세어 보여 준다.

        **이름만 센다.** ACIS 를 푸는 코드는 유료판에만 있고, 무료판은 개수만 안다.
        남의 설명이 아니라 자기 파일 이야기라 이 한 줄이 가장 세다.
        """
        # 라벨의 표시 여부로 판단하면 안 된다. 파일을 고르는 순간에는 아직
        # 숨겨져 있어(개수가 0이었으므로) 첫 입력을 놓친다 (2026-09-19).
        if not self._pro_locked:
            return
        path = Path(self.source_edit.text().strip())
        count = 0
        if path.suffix.lower() == ".dxf" and path.is_file():
            count = importer.acis_entity_count(path)
        self.pro_count.setText(
            tr("This drawing holds {count} 3D solids. Pro brings them in.").format(count=count)
            if count else "")
        self.pro_count.setVisible(bool(count))

    def _text_row(self, layout):
        """문자를 도형 속성으로. 무료판에도 그리되 잠가 둔다.

        예전에는 Community 빌드에서 아예 안 그렸다. 그러면 사용자는 Z 살리기나 문자
        붙이기가 있는 줄도 모른다. 이제 그리고 흐리게 둔다 - _lock_pro_widgets 참조.
        """
        self.close_edit = QLineEdit()
        self.close_edit.setPlaceholderText(tr(
            "Close almost-closed polylines into polygons - the gap in mm, decimals allowed "
            "(empty: off)"))
        layout.addWidget(QLabel(tr("Polygon tolerance (optional)")))
        # 칸 뒤에 mm 를 붙인다. 가져올 때 도면의 단위로 바꿔 쓴다(importer close_tolerance_mm).
        self.close_unit = QLabel("mm")
        row = QHBoxLayout()
        row.addWidget(self.close_edit, 1)
        row.addWidget(self.close_unit)
        layout.addLayout(row)

        self.z_check = QCheckBox(tr("Keep elevations (Z) from the drawing"))
        self.z_check.setToolTip(tr(
            "3D drawings such as piping isometrics carry a height on every vertex. "
            "Without this the layers are flattened."))
        layout.addWidget(self.z_check)

        self.text_check = QCheckBox(tr("Put text inside a shape into that shape's attribute (label)"))
        self.text_check.setToolTip(tr(
            "Text inside a closed shape goes into that shape's label attribute. Text outside any "
            "shape goes into the label attribute of the nearest shape."))
        layout.addWidget(self.text_check)

    def _close_tolerance(self) -> float:
        """비어 있거나 숫자가 아니면 0 - 기능을 끈다. 여기서 오류를 띄우지 않는다."""
        widget = getattr(self, "close_edit", None)
        if widget is None:
            return 0.0
        try:
            return max(float(widget.text().strip().replace(",", ".")), 0.0)   # 소수점 허용(0.5, 0,5)
        except ValueError:
            return 0.0

    # ---- 레이어 이름 규칙 --------------------------------------------------------------
    # 예전에는 매핑 프로파일(JSON) 파일을 메모장으로 고쳐 골랐다. 사람이 하는 일인데 사람이 하기
    # 어려웠다(2026-09-23 지시). 이제 저장해 둔 규칙을 이름으로 고르고, [편집…] 에서 도면 레이어를
    # 오른쪽 새 이름에 넣어 만든다(pro/layer_rules_dialog.py). 저장 형식은 같은 JSON 이다.
    _FROM_FILE = "\0file"

    def _profile_row(self, layout):
        """레이어 이름 규칙 고르기와 편집. 무료판에는 그리되 잠가 둔다."""
        try:
            from ..pro import profile as profiles

            self._profiles = profiles
        except ImportError:
            # 무료판. 칸은 그리되 눌리지 않게 둔다 - 있는 줄 알아야 산다.
            self._profiles = None
        self._layers_cache: tuple[str, float, dict] | None = None   # (경로, 수정 시각, drawing_info)

        row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(300)
        self.profile_combo.activated.connect(self._profile_chosen)
        self.profile_combo.currentIndexChanged.connect(self._refresh_rule_summary)
        edit = QPushButton(tr("Edit…"))
        edit.clicked.connect(self._edit_rules)
        self.profile_buttons = [edit]
        row.addWidget(self.profile_combo, 1)
        row.addWidget(edit)
        # 위의 칸들과 같은 모양(왼쪽 라벨)으로 둔다
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._rules_label = QLabel(tr("Layer names"))
        form.addRow(self._rules_label, row)
        # 고른 규칙이 이 도면에서 어떻게 되는지 한 줄("16개 → 14개로 가져옴")
        self.rule_summary = QLabel("")
        self.rule_summary.setObjectName("pill")
        self.rule_summary.setStyleSheet(
            "QLabel#pill { background: rgba(76,141,255,40); color: palette(link); "
            "border-radius: 9px; padding: 2px 9px; }")
        self.rule_summary.setVisible(False)
        form.addRow("", self.rule_summary)
        layout.addLayout(form)
        self._fill_profiles()

    def _show_advanced(self, shown: bool):
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.advanced.setVisible(shown)

    def _fill_profiles(self, select: str = ""):
        combo = self.profile_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("As in the drawing"), "")
        for name in (self._profiles.list_saved() if self._profiles is not None else []):
            combo.addItem(name, name)
        combo.insertSeparator(combo.count())
        combo.addItem(tr("Open a rules file…"), self._FROM_FILE)
        index = combo.findData(select) if select else 0
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)
        self._refresh_rule_summary()

    def _profile_chosen(self, index):
        """'파일 불러오기' 를 고르면 JSON 을 읽어 이름 붙여 저장해 두고 그것을 고른다."""
        if self.profile_combo.itemData(index) != self._FROM_FILE:
            return
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Open a rules file"), "", tr("Rules (*.json)"))
        if not chosen:
            self.profile_combo.setCurrentIndex(0)
            return
        try:
            loaded = self._profiles.load(Path(chosen))
            self._profiles.save_named(loaded)
        except (self._profiles.ProfileError, OSError) as err:
            QMessageBox.warning(self, tr("Profile error"), str(err))
            self.profile_combo.setCurrentIndex(0)
            return
        self._fill_profiles(select=loaded.name)

    def _chosen_rules(self):
        """고른 규칙(없으면 None). 읽을 수 없으면 알리고 None."""
        if self._profiles is None:
            return None
        name = self.profile_combo.currentData()
        if not name or name == self._FROM_FILE:
            return None
        try:
            return self._profiles.load_named(name)
        except self._profiles.ProfileError as err:
            QMessageBox.warning(self, tr("Profile error"), str(err))
            return None

    def _engine_for(self, source: str):
        """(쓸 수 있나, 변환기 경로). DXF 는 변환기가 필요 없다."""
        if engine is None or Path(source).suffix.lower() == ".dxf":
            return True, None
        try:
            return True, resolve_engine()
        except engine.EngineNotFound:
            if EngineSetupDialog(self).exec() != QDialog.DialogCode.Accepted:
                return False, None
            try:
                # 설정 직후에도 실패할 수 있다 — 네트워크 드라이브나 USB가 빠지는 경우.
                return True, resolve_engine()
            except engine.EngineNotFound as err:
                QMessageBox.warning(self, tr("No converter"), str(err))
                return False, None

    def _drawing_info(self, source: str, exe=None) -> dict:
        """고른 도면의 레이어와 단위. 같은 파일이면 한 번만 읽는다(DWG 는 변환이 몇 초 걸린다)."""
        path = Path(source)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return {"layers": [], "units": None}
        if self._layers_cache and self._layers_cache[:2] == (str(path), stamp):
            return self._layers_cache[2]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = importer.drawing_info(path, exe=exe)
        finally:
            QApplication.restoreOverrideCursor()
        self._layers_cache = (str(path), stamp, info)
        return info

    def _drawing_layers(self, source: str, exe=None) -> list:
        return self._drawing_info(source, exe)["layers"]

    def _drawing_is_metric(self) -> bool:
        """고른 도면이 미터법인가. 아직 못 읽었으면 미터법으로 본다 - CAD 도면의 대부분이다."""
        cache = self._layers_cache
        if cache is None:
            return True
        return (cache[2].get("units") or 4) not in _IMPERIAL_UNITS

    def _refresh_crs_warning(self, *_):
        """도 단위 좌표계에 미터 도면을 넣으려 하면 알린다. 도면을 새로 읽지는 않는다."""
        label = getattr(self, "crs_warning", None)
        if label is None:
            return
        crs = self.crs_widget.crs()
        label.setVisible(bool(crs.isValid() and crs.isGeographic() and self._drawing_is_metric()))

    def _edit_rules(self):
        if self._profiles is None:      # 무료판 - 버튼이 잠겨 있어 여기 오지 않는다
            return
        source = self.source_edit.text().strip()
        if not source or not Path(source).is_file():
            QMessageBox.information(self, tr("No file"), tr("Choose the drawing first - the rules are made from its layers."))
            return
        ok, exe = self._engine_for(source)
        if not ok:
            return
        layers = self._drawing_layers(source, exe)
        if not layers:
            QMessageBox.warning(self, tr("Could not read the layers"),
                                tr("The layers of this drawing could not be read."))
            return
        from ..pro.layer_rules_dialog import LayerRulesDialog

        dialog = LayerRulesDialog(layers, self._chosen_rules(), Path(source).name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_name:
            self._fill_profiles(select=dialog.saved_name)

    def _refresh_rule_summary(self, *_):
        """고른 규칙이 이 도면에서 어떻게 되는지 한 줄로. 레이어를 이미 읽은 도면에서만."""
        label = getattr(self, "rule_summary", None)
        if label is None:
            return
        source = self.source_edit.text().strip() if hasattr(self, "source_edit") else ""
        cache = self._layers_cache
        if source.lower().endswith(".dxf") and Path(source).is_file() and self._profiles is not None \
                and (cache is None or cache[0] != str(Path(source))):
            self._drawing_layers(source)          # DXF 는 바로 읽힌다 - 변환이 필요 없다
            cache = self._layers_cache
        if not source or cache is None or cache[0] != str(Path(source)):
            label.setVisible(False)
            return
        from ..pro.layer_rules import LayerRulesModel

        model = LayerRulesModel.from_profile(cache[2]["layers"], self._chosen_rules())
        total, out, off = model.summary()
        label.setText(tr("{total} drawing layers → {out} layers to import").format(total=total, out=out)
                      + (" · " + tr("{n} left out").format(n=off) if off else ""))
        label.setVisible(True)
        self._refresh_crs_warning()      # 단위를 읽었으니 좌표계 경고를 다시 본다

    def _browse_source(self):
        # 무료판도 DWG 를 고를 수 있게 둔다. 고르고 나서 거절당하는 자리가 곧 영업
        # 자리다 - 거기서 "이 DWG 는 R2000 형식입니다. Pro 에서 열립니다" 가 나온다.
        # 목록에서 아예 빼 두면 사용자는 이 플러그인이 자기에게 쓸모없다고 여기고 떠난다.
        title = tr("Choose drawing")
        wanted = tr("CAD drawing (*.dwg *.dxf)")
        chosen, _ = QFileDialog.getOpenFileName(self, title, "", wanted)
        if chosen:
            self.source_edit.setText(chosen)

    def _cancel(self):
        if self._feedback is not None:
            self._feedback.canceled = True
        else:
            self.reject()

    def _run(self):
        source = self.source_edit.text().strip()
        if not source:
            QMessageBox.warning(self, tr("No file"), tr("Choose the drawing to import."))
            return
        if not Path(source).is_file():
            # 없는 경로를 그대로 변환기에 넘기면 "변환에 실패했습니다 / READ ERROR 0x1000"
            # 이라는, 사용자가 알 수 없는 오류가 뜬다. 경로를 손으로 고치다 한 글자
            # 틀리는 일은 흔하다(2026-09-25 구매자 점검에서 실제로 그렇게 막혔다).
            QMessageBox.warning(self, tr("No file"),
                                tr("There is no file at {path}").format(path=source))
            return

        ok, exe = self._engine_for(source)
        if not ok:
            return

        if not self.crs_widget.crs().isValid():
            # 좌표계 없이 만들면 레이어가 다른 데이터와 맞지 않는 자리에 놓인다.
            # 프로젝트에도 좌표계가 없는 새 프로젝트에서 실제로 생긴다.
            QMessageBox.warning(self, tr("No coordinate system"),
                                tr("Choose the coordinate system of the drawing."))
            return

        self.progress.setVisible(True)
        # DWG 는 여기서부터 변환기가 도는데 그 구간에는 진행 보고가 없다. 얼마나 걸릴지
        # 모르니 지나가는 막대로 두고, 한 번 processEvents 를 돌려 막대가 실제로 그려지게
        # 한다. 이게 없으면 제일 오래 걸리는 구간 내내 화면에 아무것도 안 보인다
        # (2026-09-25 "가져올 때 왜 프로그레스바가 없나").
        self.progress.setRange(0, 0)
        QApplication.processEvents()
        self._feedback = _DialogFeedback(self.progress)
        # 진행률 표시가 processEvents를 부르므로 확인 버튼을 잠그지 않으면
        # 변환 중에 또 눌러 두 번째 임포트가 겹쳐 시작된다. 취소는 살려 둔다.
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result, layers = importer.import_dwg(
                Path(source), output_gpkg=None,
                crs=self.crs_widget.crs().authid() or None,
                exe=exe, feedback=self._feedback,
                profile=self._chosen_rules(),
                join_text=bool(self.text_check and self.text_check.isChecked()),
                close_tolerance_mm=self._close_tolerance(),
                keep_z=bool(getattr(self, "z_check", None) and self.z_check.isChecked()),
                skip_hidden=self.hidden_check.isChecked(),
            )
        except Exception as err:  # 예상 못 한 실패도 QGIS를 멈추게 두지 않는다
            result, layers = None, []
            QMessageBox.critical(self, tr("Import failed"), str(err))
        finally:
            QApplication.restoreOverrideCursor()
            self._feedback = None
            self.progress.setVisible(False)
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

        if result is None:
            return
        if result.status == "locked":
            # 변환 실패가 아니라 라이선스 문제다. 빨간 실패로 띄우면 원인을 못 찾는다.
            QMessageBox.warning(self, tr("Licence not active"), result.note)
            return
        if result.status != "ok":
            detail = tr(_STATUS_MESSAGE.get(result.status, "Could not import it."))
            body = "\n\n".join(part for part in (detail, result.note) if part)
            QMessageBox.warning(self, tr("Import failed"), body)
            return

        # 한 번 가져온 레이어들을 한 묶음으로 표시한다 - '지도에 맞추기' 가 이 묶음을 함께 옮긴다
        import_id = uuid.uuid4().hex[:12]
        for layer in layers:
            layer.setCustomProperty(DRAWING_KEY, result.file)
            layer.setCustomProperty(IMPORT_KEY, import_id)
        QgsProject.instance().addMapLayers(layers)
        _hide_attribute_layers(layers)
        _show_drawing(result.view_extent)
        lines = [tr("Imported {layers} layers and {features} features.").format(
            layers=len(result.layers), features=f"{result.total_features:,}")]
        if result.note:
            lines.append(result.note)
        if result.unmatched_layers:
            shown = ", ".join(result.unmatched_layers[:8])
            more = (tr(" and {count} more").format(count=len(result.unmatched_layers) - 8)
                    if len(result.unmatched_layers) > 8 else "")
            lines.append("\n" + tr("CAD layers no profile rule matched")
                         + f" — {shown}{more}")
        solids = [l for l in layers if "3DSOLID" in l.name()]
        if solids:
            # 솔리드는 2D 지도에 바로 얹지 않는다. 3D 창에서 돌려 각도를 정하고 '이 각도로
            # 가져오기'로 확정하면 그때 그 각도의 평면 도형이 지도에 얹힌다(2026-09-22).
            #
            # 다만 **무조건 열지 않는다.** 평면 도형만 필요한 사람도 있고, 원본 솔리드
            # 레이어가 지도에 남아 뽑은 평면과 겹쳐 보이는 원인이었다 - 가져올 때 고르게
            # 한다(2026-09-25 지시 "사용자에게 선택하도록 해야지").
            root = QgsProject.instance().layerTreeRoot()
            for solid in solids:
                node = root.findLayer(solid.id())
                if node is not None:
                    node.setItemVisibilityChecked(False)
                # 3D 각도 가져오기 보고서가 어느 도면에서 왔는지 알 수 있게 적어 둔다
                solid.setCustomProperty("echocad/source_file", result.file)
            self._show_report(result, lines)
            turn_it = self._ask_about_solids(len(solids))
            self.accept()
            if not turn_it:
                # 평면만 원한다 - 3D 창의 재료로만 쓰던 원본을 지도에서 뺀다.
                QgsProject.instance().removeMapLayers([s.id() for s in solids])
                self._start_align()
                return
            from qgis.utils import iface

            from . import view3d

            view3d.open_3d_view(iface, self.parent())
            return
        self._show_report(result, lines)
        self.accept()
        self._start_align()

    def _ask_about_solids(self, count: int) -> bool:
        """3차원 솔리드가 있을 때 어떻게 가져올지 묻는다. 3차원으로 보겠다면 True.

        돌려서 각도를 정해 평면으로 뽑는 것이 이 도면의 값어치지만, 평면 도형만 필요한
        사람에게는 군더더기다. 무엇보다 원본 솔리드 레이어가 지도에 남아 뽑은 평면과
        겹쳐 보였다(2026-09-25 지적).
        """
        box = QMessageBox(self)
        box.setWindowTitle(tr("EchoCad — 3D solids"))
        box.setText(tr("This drawing has {count} 3D solids.").format(count=count))
        box.setInformativeText(tr(
            "Turn the model and take a flat drawing from the angle you like, or bring in "
            "the flat shapes only."))
        turn = box.addButton(tr("Turn it in 3D"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Flat shapes only"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(turn)
        box.exec()
        return box.clickedButton() is turn

    def _start_align(self):
        """보고서를 닫으면 지도에 맞추기를 이어서 연다.

        가져오기는 레이어가 만들어진 데서 끝나지 않는다 - 도면이 지도 위 제자리에 놓여야
        쓸 수 있는 데이터가 된다(2026-09-25 지시 "정확하게 맞추는 작업까지가 임포트의 끝").
        메뉴로 찾아 들어가게 두면 순서가 뒤집힌다. 맞추기 창의 [닫기] 가 이 흐름의 끝이다.

        Pro 에만 있다(Community 에는 pro/ 폴더가 없다). 없으면 조용히 넘긴다.
        """
        try:
            from ..pro import align_tool
        except ImportError:
            return
        from qgis.utils import iface

        # 창 없이(시험·배치) 도는 자리에서는 iface 가 없다. 지도가 없으면 맞출 것도 없다.
        if iface is None:
            return
        align_tool.start(iface)

    def _show_report(self, result, lines) -> None:
        """완료 알림 대신 가져오기 보고서(도면에 있는 것 / 가져온 것 / 빠진 것과 이유)."""
        if not result.report:
            QMessageBox.information(self, tr("Import finished"), "\n".join(lines))
            return
        result.report["notes"] = [line.strip() for line in lines[1:] if line.strip()]
        from . import report_dialog

        report_dialog.show(result.report, result.excerpt, self)


def _show_drawing(bounds) -> None:
    """가져온 직후 도면이 보이게 화면을 잡는다.

    QGIS 기본 동작(전체 범위)에 맡기면, 본체에서 멀리 떨어진 엔티티가 하나만 있어도
    도면이 구석에 콩알만 하게 나온다. 2026-08-23 실측 `mech-iso.dwg` 가 그렇다 —
    도면은 `3247,1230 : 3667,1527` 인데 원점(0,0)에 점이 하나 있다.
    그 점은 진짜 데이터라 지우지 않고 보기에서만 뺀다 (importer 가 view_extent 로 준다).

    화면을 못 잡아도 가져오기 자체는 성공이므로 조용히 넘어간다.
    """
    if not bounds:
        return
    try:
        from qgis.utils import iface

        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is None:
            return
        xmin, ymin, xmax, ymax = bounds
        rect = QgsRectangle(xmin, ymin, xmax, ymax)
        if rect.isEmpty():
            return
        rect.scale(1.05)  # 도면이 화면 가장자리에 딱 붙지 않게 여백을 준다
        canvas.setExtent(rect)
        canvas.refresh()
    except Exception:
        return


def _hide_attribute_layers(layers) -> None:
    """속성만 담은 레이어는 지도에서 꺼 둔다. 목록에는 그대로 남는다.

    블록 참조 레이어(`<레이어>_blocks`)는 블록 속성을 담으려고 만든 것이다.
    블록 도형 자체는 이미 선으로 그려지므로 이 점을 켜 두면 시각 정보는 하나도
    안 더하면서 도면만 가린다 (2026-08-23 실측 — `autodesk_blocks_tables_imperial`
    에서 점 77 개가 평면도 위에 찍혔다). 값어치는 속성 테이블에 있다.
    """
    try:
        root = QgsProject.instance().layerTreeRoot()
        for layer in layers:
            if not layer.customProperty("echocad/attributes_only"):
                continue
            node = root.findLayer(layer.id())
            if node is not None:
                node.setItemVisibilityChecked(False)
    except Exception:
        return
