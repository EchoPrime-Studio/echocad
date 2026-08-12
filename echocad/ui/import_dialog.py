# DWG 한 장을 QGIS로 가져오는 다이얼로그. 파일·좌표계·출력만 받고 나머지는 importer가 한다
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem, QgsProject
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from .. import engine, importer
from .engine_setup import EngineSetupDialog, resolve_engine

# 상태 코드마다 사용자가 다음에 무엇을 해야 하는지 알려 준다 (SC-003).
_STATUS_MESSAGE = {
    "unsupported": "지원하지 않는 도면 포맷입니다. R14 이상 DWG만 읽을 수 있습니다.",
    "timeout": "변환이 제한 시간을 넘겼습니다. 도면이 매우 크거나 손상됐을 수 있습니다.",
    "empty": "변환은 됐지만 그릴 엔티티가 없습니다. 메타데이터 전용 도면일 수 있습니다.",
    "error": "변환에 실패했습니다.",
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
        self.setWindowTitle("EchoCad — DWG 가져오기")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        source = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("DWG 파일")
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self._browse_source)
        source.addWidget(self.source_edit)
        source.addWidget(browse)
        layout.addWidget(QLabel("가져올 도면"))
        layout.addLayout(source)

        layout.addWidget(QLabel("좌표계"))
        crs_row = QHBoxLayout()
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsProject.instance().crs())
        crs_row.addWidget(self.crs_widget)
        self._add_crs_suggest_button(crs_row)
        layout.addLayout(crs_row)

        self.save_check = QCheckBox("GeoPackage로 저장 (체크 해제 시 임시 레이어)")
        layout.addWidget(self.save_check)

        output = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("저장할 .gpkg 경로")
        self.output_edit.setEnabled(False)
        output_browse = QPushButton("저장 위치…")
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

    def _add_crs_suggest_button(self, row):
        """좌표계 추천 버튼. Community 빌드에는 만들지 않는다."""
        try:
            from ..pro import crs as crs_rules
            from ..pro.crs_dialog import CrsChoiceDialog
        except ImportError:
            return
        self._crs_rules = crs_rules
        self._crs_dialog = CrsChoiceDialog

        button = QPushButton("추천…")
        button.setToolTip("도면 좌표 범위로 좌표계 후보를 찾습니다")
        button.clicked.connect(self._suggest_crs)
        row.addWidget(button)

    def _suggest_crs(self):
        source = self.source_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "파일 없음", "먼저 DWG 파일을 지정하세요.")
            return
        try:
            exe = resolve_engine()
        except engine.EngineNotFound as err:
            QMessageBox.warning(self, "변환 엔진 없음", str(err))
            return

        # 좌표 범위는 변환된 DXF 헤더에만 있다. 사용자가 버튼을 눌러 요청한
        # 것이므로 한 번 더 변환하는 비용은 감수한다.
        QApplication.setOverrideCursor(Qt.WaitCursor)
        work = Path(tempfile.mkdtemp(prefix="echocad-crs-"))
        try:
            converted = engine.convert(Path(source), work / "probe.dxf", exe=exe)
            extents = self._crs_rules.read_extents(converted.dxf) if converted.status == "ok" else None
        finally:
            QApplication.restoreOverrideCursor()
            shutil.rmtree(work, ignore_errors=True)

        if converted.status != "ok":
            QMessageBox.warning(self, "읽지 못했습니다", converted.note or converted.status)
            return

        dialog = self._crs_dialog(self._crs_rules.suggest(extents), self)
        if dialog.exec_() == QDialog.Accepted and dialog.chosen:
            self.crs_widget.setCrs(QgsCoordinateReferenceSystem(dialog.chosen))

    def _profile_row(self, layout):
        """매핑 프로파일 선택. Community 빌드에는 아예 만들지 않는다."""
        try:
            from ..pro import profile as profiles
        except ImportError:
            self.profile_edit = None
            return
        self._profiles = profiles

        layout.addWidget(QLabel("매핑 프로파일 (선택)"))
        row = QHBoxLayout()
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("CAD 레이어명을 GIS 스키마로 옮기는 규칙 파일")
        pick = QPushButton("열기…")
        pick.clicked.connect(self._browse_profile)
        make = QPushButton("예시 만들기…")
        make.clicked.connect(self._write_example_profile)
        row.addWidget(self.profile_edit)
        row.addWidget(pick)
        row.addWidget(make)
        layout.addLayout(row)

    def _browse_profile(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self, "매핑 프로파일 선택", "", "프로파일 (*.json)"
        )
        if chosen:
            self.profile_edit.setText(chosen)

    def _write_example_profile(self):
        """규칙을 손으로 짜기 전에 형태를 보여 준다. 편집은 텍스트 에디터로 한다."""
        chosen, _ = QFileDialog.getSaveFileName(
            self, "예시 프로파일 저장", "echocad-profile.json", "프로파일 (*.json)"
        )
        if not chosen:
            return
        example = self._profiles.MappingProfile(
            name="예시",
            description="match는 glob 패턴입니다. 위에서부터 첫 매칭이 적용됩니다.",
            rules=[
                self._profiles.Rule(match="A-WALL-*", layer_name="building_wall", geometry="polygon"),
                self._profiles.Rule(match="*-TEXT", layer_name="annotation", geometry="point"),
            ],
        )
        try:
            self._profiles.save(example, Path(chosen))
        except OSError as err:
            QMessageBox.warning(self, "저장 실패", str(err))
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
            QMessageBox.warning(self, "프로파일 오류", str(err))
            return None

    def _browse_source(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "DWG 선택", "", "DWG 도면 (*.dwg)")
        if chosen:
            self.source_edit.setText(chosen)
            if not self.output_edit.text():
                self.output_edit.setText(str(Path(chosen).with_suffix(".gpkg")))

    def _browse_output(self):
        chosen, _ = QFileDialog.getSaveFileName(self, "GeoPackage 저장", "", "GeoPackage (*.gpkg)")
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
            QMessageBox.warning(self, "파일 없음", "가져올 DWG 파일을 지정하세요.")
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
                QMessageBox.warning(self, "변환 엔진 없음", str(err))
                return

        output = Path(self.output_edit.text().strip()) if self.save_check.isChecked() else None
        if self.save_check.isChecked() and not self.output_edit.text().strip():
            QMessageBox.warning(self, "저장 위치 없음", "GeoPackage 경로를 지정하세요.")
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
            QMessageBox.critical(self, "가져오기 실패", str(err))
        finally:
            QApplication.restoreOverrideCursor()
            self._feedback = None
            self.progress.setVisible(False)
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

        if result is None:
            return
        if result.status != "ok":
            detail = _STATUS_MESSAGE.get(result.status, "가져오지 못했습니다.")
            QMessageBox.warning(self, "가져오기 실패", f"{detail}\n\n{result.note}".strip())
            return

        QgsProject.instance().addMapLayers(layers)
        lines = [f"레이어 {len(result.layers)}개, 피처 {result.total_features:,}개를 가져왔습니다."]
        if result.note:
            lines.append(result.note)
        if result.unmatched_layers:
            shown = ", ".join(result.unmatched_layers[:8])
            more = f" 외 {len(result.unmatched_layers) - 8}개" if len(result.unmatched_layers) > 8 else ""
            lines.append(f"\n프로파일 규칙에 걸리지 않은 CAD 레이어 — {shown}{more}")
        QMessageBox.information(self, "가져오기 완료", "\n".join(lines))
        self.accept()
