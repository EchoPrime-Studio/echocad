# DWG 한 장을 QGIS로 가져오는 다이얼로그. 파일·좌표계·출력만 받고 나머지는 importer가 한다
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsRectangle
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from .. import importer
from ..i18n import tr

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
        self.setWindowTitle(tr("EchoCad — Import DWG"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        source = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(tr("DWG file"))
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse_source)
        source.addWidget(self.source_edit)
        source.addWidget(browse)
        layout.addWidget(QLabel(tr("Drawing to import")))
        layout.addLayout(source)

        layout.addWidget(QLabel(tr("Coordinate system")))
        # 프로젝트 좌표계로 채워 둔다. 이미 있는 데이터에 얹는 것이 보통이라
        # 같은 좌표계를 쓰는 것이 맞다. 사용자가 바꾸는 것은 그대로 열려 있다.
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsProject.instance().crs())
        layout.addWidget(self.crs_widget)

        self.save_check = QCheckBox(tr("Save to GeoPackage (unchecked: temporary layers)"))
        layout.addWidget(self.save_check)

        # 두 판 모두에 있다. 가져오기 자체를 다듬는 것이라 유료로 둘 이유가 없다.
        self.hidden_check = QCheckBox(tr("Skip layers switched off or frozen in the drawing"))
        layout.addWidget(self.hidden_check)

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
        layout.addLayout(output)

        self._profile_row(layout)
        self._text_row(layout)
        self._georef_row(layout)

        self.pro_note = QLabel("")
        self.pro_note.setWordWrap(True)
        self.pro_note.setStyleSheet("QLabel { color: #b06000; }")
        self.pro_note.setVisible(False)
        layout.addWidget(self.pro_note)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._run)
        self.buttons.rejected.connect(self._cancel)
        layout.addWidget(self.buttons)

        self._feedback: _DialogFeedback | None = None
        self._lock_pro_widgets()

    def _lock_pro_widgets(self):
        """라이선스가 잠겨 있으면 유료 칸을 못 쓰게 한다.

        모듈이 임포트된다고 쓸 수 있는 것이 아니다. Pro 빌드에서는 항상 임포트되므로,
        만료된 뒤에도 체크박스가 켜져 있어 사용자가 켜고 돌리면 조용히 무시됐다.
        """
        try:
            from .. import pro
        except ImportError:
            return
        if pro.unlocked():
            return

        from ..pro import license as licensing

        why = licensing.decide(pro.build_date()).reason
        for name in ("profile_edit", "text_check", "z_check", "close_edit"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip(why)
        for edit in (getattr(self, "georef_edits", None) or []):
            edit.setEnabled(False)
            edit.setToolTip(why)
        if getattr(self, "pro_note", None) is not None:
            self.pro_note.setText(
                tr("Paid features are locked. {reason}").format(reason=why))
            self.pro_note.setVisible(True)

    def _text_row(self, layout):
        """문자를 도형 속성으로. Community 빌드에는 아예 만들지 않는다."""
        try:
            from ..pro import textjoin  # noqa: F401
        except ImportError:
            # 셋 다 None 으로 둔다. 하나만 두면 "없음"을 확인하는 쪽이 속성 없음과
            # None 을 구분하지 못해, 유료 위젯이 남은 것처럼 읽힌다.
            self.text_check = None
            self.z_check = None
            self.close_edit = None
            return

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
        """도면좌표를 실좌표로 옮길 기준점 두 쌍. Community 빌드에는 만들지 않는다."""
        try:
            from ..pro import georef  # noqa: F401
        except ImportError:
            self.georef_edits = None
            return

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
        """매핑 프로파일 선택. Community 빌드에는 아예 만들지 않는다."""
        try:
            from ..pro import profile as profiles
        except ImportError:
            self.profile_edit = None
            return
        self._profiles = profiles

        layout.addWidget(QLabel(tr("Mapping profile (optional)")))
        row = QHBoxLayout()
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText(tr("Rule file that maps CAD layer names onto your GIS schema"))
        pick = QPushButton(tr("Open…"))
        pick.clicked.connect(self._browse_profile)
        make = QPushButton(tr("Create example…"))
        make.clicked.connect(self._write_example_profile)
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
        if self.profile_edit is None:
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
        # 무료판은 DXF 만 읽는다. DWG 를 고를 수 있게 두면 고르고 나서야 거절당한다.
        title = tr("Choose drawing") if engine is not None else tr("Choose DXF")
        wanted = (tr("CAD drawing (*.dwg *.dxf)") if engine is not None
                  else tr("DXF drawing (*.dxf)"))
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
