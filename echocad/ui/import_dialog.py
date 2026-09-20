# DWG 한 장을 QGIS로 가져오는 다이얼로그. 파일·좌표계·출력만 받고 나머지는 importer가 한다
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsRectangle
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPalette
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox,
    QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from .. import importer
from ..i18n import tr
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
    return (
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

        self.save_check = QCheckBox(tr("Save to GeoPackage (unchecked: temporary layers)"))
        form.addRow("", self.save_check)

        output = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText(tr("Path of the .gpkg to write"))
        self.output_edit.setEnabled(False)
        output_browse = QPushButton(tr("Save location…"))
        output_browse.setEnabled(False)
        output_browse.clicked.connect(self._browse_output)
        self.save_check.toggled.connect(self.output_edit.setEnabled)
        self.save_check.toggled.connect(output_browse.setEnabled)
        output.addWidget(self.output_edit)
        output.addWidget(output_browse)
        form.addRow(tr("GeoPackage"), output)

        # 두 판 모두에 있다. 가져오기 자체를 다듬는 것이라 유료로 둘 이유가 없다.
        self.hidden_check = QCheckBox(tr("Skip layers switched off or frozen in the drawing"))
        form.addRow(tr("Layers"), self.hidden_check)
        layout.addLayout(form)

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
        self._text_row(controls)
        self._georef_row(controls)
        pro.addWidget(self.pro_controls)

        layout.addWidget(self.pro_box)

        # 파일을 고르면 그 자리에서 3차원 솔리드 개수를 세어 보여 준다.
        self.source_edit.textChanged.connect(self._refresh_pro_count)

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

        for name in ("profile_edit", "text_check", "z_check", "close_edit"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip(why)
        for edit in (getattr(self, "georef_edits", None) or []):
            edit.setEnabled(False)
            edit.setToolTip(why)
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
            "Close almost-closed polylines into polygons - gap tolerance in drawing units "
            "(empty: off)"))
        layout.addWidget(QLabel(tr("Polygon tolerance (optional)")))
        layout.addWidget(self.close_edit)

        self.z_check = QCheckBox(tr("Keep elevations (Z) from the drawing"))
        self.z_check.setToolTip(tr(
            "3D drawings such as piping isometrics carry a height on every vertex. "
            "Without this the layers are flattened."))
        layout.addWidget(self.z_check)

        self.text_check = QCheckBox(tr("Attach drawing text to the shapes it labels"))
        self.text_check.setToolTip(tr(
            "A text inside a polygon becomes that polygon's label. Otherwise it goes to the "
            "nearest shape. The value lands in a 'label' field."))
        layout.addWidget(self.text_check)

    def _georef_row(self, layout):
        """도면좌표를 실좌표로 옮길 기준점 두 쌍. 무료판에는 잠가 둔다."""
        layout.addWidget(QLabel(tr("Georeference from two reference points (optional)")))
        grid = QGridLayout()
        for column, title in enumerate(
                [tr("drawing X"), tr("drawing Y"), tr("real X"), tr("real Y")]):
            grid.addWidget(QLabel(title), 0, column + 1)

        self.georef_edits = []
        for row in range(2):
            grid.addWidget(QLabel(tr("Point {number}").format(number=row + 1)), row + 1, 0)
            for column in range(4):
                edit = QLineEdit()
                grid.addWidget(edit, row + 1, column + 1)
                self.georef_edits.append(edit)
        layout.addLayout(grid)

    def _georef(self):
        """여덟 칸이 다 차 있으면 변환을 만든다. 비어 있으면 None - 정합을 안 한다.

        값이 이상하면 여기서 세운다. 엉뚱한 자리에 놓인 결과를 주는 것보다
        왜 안 되는지 말하는 것이 낫다.
        """
        if not self.georef_edits:
            return None
        values = [edit.text().strip() for edit in self.georef_edits]
        if not any(values):
            return None
        if not all(values):
            raise ValueError(tr("Fill in all eight reference numbers, or leave them all empty."))

        from ..pro import georef

        return georef.solve((values[0], values[1]), (values[2], values[3]),
                            (values[4], values[5]), (values[6], values[7]))

    def _close_tolerance(self) -> float:
        """비어 있거나 숫자가 아니면 0 - 기능을 끈다. 여기서 오류를 띄우지 않는다."""
        widget = getattr(self, "close_edit", None)
        if widget is None:
            return 0.0
        try:
            return max(float(widget.text().strip()), 0.0)
        except ValueError:
            return 0.0

    def _profile_row(self, layout):
        """매핑 프로파일 선택. 무료판에는 그리되 잠가 둔다."""
        try:
            from ..pro import profile as profiles

            self._profiles = profiles
        except ImportError:
            # 무료판. 칸은 그리되 눌리지 않게 둔다 - 있는 줄 알아야 산다.
            self._profiles = None

        layout.addWidget(QLabel(tr("Mapping profile (optional)")))
        row = QHBoxLayout()
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText(tr("Rule file that maps CAD layer names onto your GIS schema"))
        pick = QPushButton(tr("Open…"))
        pick.clicked.connect(self._browse_profile)
        make = QPushButton(tr("Create example…"))
        make.clicked.connect(self._write_example_profile)
        self.profile_buttons = [pick, make]
        row.addWidget(self.profile_edit)
        row.addWidget(pick)
        row.addWidget(make)
        layout.addLayout(row)

    def _browse_profile(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("Choose mapping profile"), "", tr("Profile (*.json)")
        )
        if chosen:
            self.profile_edit.setText(chosen)

    def _write_example_profile(self):
        """규칙을 손으로 짜기 전에 형태를 보여 준다. 편집은 텍스트 에디터로 한다."""
        if self._profiles is None:      # 무료판 - 버튼이 잠겨 있어 여기 오지 않는다
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, tr("Save example profile"), "echocad-profile.json", tr("Profile (*.json)")
        )
        if not chosen:
            return
        example = self._profiles.MappingProfile(
            name=tr("Example"),
            description=tr("match is a glob pattern. The first rule that matches wins."),
            rules=[
                self._profiles.Rule(match="A-WALL-*", layer_name="building_wall", geometry="polygon"),
                self._profiles.Rule(match="*-TEXT", layer_name="annotation", geometry="point"),
            ],
        )
        try:
            self._profiles.save(example, Path(chosen))
        except OSError as err:
            QMessageBox.warning(self, tr("Could not save"), str(err))
            return
        self.profile_edit.setText(chosen)

    def _load_profile(self):
        """선택된 프로파일. 없으면 None, 형식이 틀리면 알리고 None."""
        if self.profile_edit is None or self._profiles is None:
            return None
        text = self.profile_edit.text().strip()
        if not text:
            return None
        try:
            return self._profiles.load(Path(text))
        except self._profiles.ProfileError as err:
            QMessageBox.warning(self, tr("Profile error"), str(err))
            return None

    def _browse_source(self):
        # 무료판도 DWG 를 고를 수 있게 둔다. 고르고 나서 거절당하는 자리가 곧 영업
        # 자리다 - 거기서 "이 DWG 는 R2000 형식입니다. Pro 에서 열립니다" 가 나온다.
        # 목록에서 아예 빼 두면 사용자는 이 플러그인이 자기에게 쓸모없다고 여기고 떠난다.
        title = tr("Choose drawing")
        wanted = tr("CAD drawing (*.dwg *.dxf)")
        chosen, _ = QFileDialog.getOpenFileName(self, title, "", wanted)
        if chosen:
            self.source_edit.setText(chosen)
            if not self.output_edit.text():
                self.output_edit.setText(str(Path(chosen).with_suffix(".gpkg")))

    def _browse_output(self):
        chosen, _ = QFileDialog.getSaveFileName(self, tr("Save GeoPackage"), "", "GeoPackage (*.gpkg)")
        if chosen:
            self.output_edit.setText(chosen)

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

        exe = None
        # DXF 는 변환기를 안 탄다. 무료판에는 변환기가 아예 없다.
        if engine is not None and Path(source).suffix.lower() != ".dxf":
            try:
                exe = resolve_engine()
            except engine.EngineNotFound:
                if EngineSetupDialog(self).exec() != QDialog.DialogCode.Accepted:
                    return
                try:
                    # 설정 직후에도 실패할 수 있다 — 네트워크 드라이브나 USB가 빠지는 경우.
                    exe = resolve_engine()
                except engine.EngineNotFound as err:
                    QMessageBox.warning(self, tr("No converter"), str(err))
                    return

        if not self.crs_widget.crs().isValid():
            # 좌표계 없이 만들면 레이어가 다른 데이터와 맞지 않는 자리에 놓인다.
            # 프로젝트에도 좌표계가 없는 새 프로젝트에서 실제로 생긴다.
            QMessageBox.warning(self, tr("No coordinate system"),
                                tr("Choose the coordinate system of the drawing."))
            return

        output = Path(self.output_edit.text().strip()) if self.save_check.isChecked() else None
        if self.save_check.isChecked() and not self.output_edit.text().strip():
            QMessageBox.warning(self, tr("No save location"), tr("Set the GeoPackage path."))
            return

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._feedback = _DialogFeedback(self.progress)
        # 진행률 표시가 processEvents를 부르므로 확인 버튼을 잠그지 않으면
        # 변환 중에 또 눌러 두 번째 임포트가 겹쳐 시작된다. 취소는 살려 둔다.
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result, layers = importer.import_dwg(
                Path(source), output_gpkg=output,
                crs=self.crs_widget.crs().authid() or None,
                exe=exe, feedback=self._feedback,
                profile=self._load_profile(),
                join_text=bool(self.text_check and self.text_check.isChecked()),
                close_tolerance=self._close_tolerance(),
                georef=self._georef(),
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
        QMessageBox.information(self, tr("Import finished"), "\n".join(lines))
        self.accept()


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
