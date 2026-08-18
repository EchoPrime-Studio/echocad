# DWG 한 장을 QGIS로 가져오는 다이얼로그. 파일·좌표계·출력만 받고 나머지는 importer가 한다
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem, QgsProject
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from .. import engine, importer
from ..i18n import tr
from .engine_setup import EngineSetupDialog, resolve_engine

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

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._run)
        self.buttons.rejected.connect(self._cancel)
        layout.addWidget(self.buttons)

        self._feedback: _DialogFeedback | None = None

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
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Choose DWG"), "", tr("DWG drawing (*.dwg)"))
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
            QMessageBox.warning(self, tr("No file"), tr("Choose the DWG file to import."))
            return

        try:
            exe = resolve_engine()
        except engine.EngineNotFound:
            if EngineSetupDialog(self).exec_() != QDialog.Accepted:
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
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result, layers = importer.import_dwg(
                Path(source), output_gpkg=output,
                crs=self.crs_widget.crs().authid() or None,
                exe=exe, feedback=self._feedback,
                profile=self._load_profile(),
            )
        except Exception as err:  # 예상 못 한 실패도 QGIS를 멈추게 두지 않는다
            result, layers = None, []
            QMessageBox.critical(self, tr("Import failed"), str(err))
        finally:
            QApplication.restoreOverrideCursor()
            self._feedback = None
            self.progress.setVisible(False)
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

        if result is None:
            return
        if result.status != "ok":
            detail = tr(_STATUS_MESSAGE.get(result.status, "Could not import it."))
            body = "\n\n".join(part for part in (detail, result.note) if part)
            QMessageBox.warning(self, tr("Import failed"), body)
            return

        QgsProject.instance().addMapLayers(layers)
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
