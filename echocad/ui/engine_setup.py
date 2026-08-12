# dwg2dxf 확보를 안내하는 다이얼로그. OS별 경로가 다르고, 확보한 경로는 QSettings에 남는다
from __future__ import annotations

import sys
from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import engine

SETTINGS_KEY = "echocad/dwg2dxf_path"


def saved_engine_path() -> Path | None:
    """사용자가 지정했거나 자동 확보한 경로. 없거나 사라졌으면 None."""
    value = QSettings().value(SETTINGS_KEY, "", type=str)
    path = Path(value) if value else None
    return path if path and path.exists() else None


def resolve_engine() -> Path:
    """저장된 경로를 먼저 보고, 없으면 PATH·번들 순으로 찾는다."""
    return engine.find_dwg2dxf(saved_engine_path())


def _install_dir() -> Path:
    return Path(QgsApplication.qgisSettingsDirPath()) / "echocad" / "bin"


class EngineSetupDialog(QDialog):
    """엔진이 없을 때 확보 방법을 안내한다. 확보에 성공하면 accept()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EchoCad — DWG 변환 엔진 준비")
        self.engine_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "DWG를 읽으려면 LibreDWG의 dwg2dxf가 필요합니다.\n"
            "GPL-3 라이선스라 플러그인에 동봉하지 않습니다."
        ))

        hint = QLabel(engine.install_hint())
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint.setStyleSheet("QLabel { font-family: monospace; padding: 6px; }")
        layout.addWidget(hint)

        if sys.platform == "win32":
            download = QPushButton("공식 릴리스 내려받기 (약 11MB)")
            download.clicked.connect(self._download)
            layout.addWidget(download)
        else:
            copy = QPushButton("위 명령 복사")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(engine.install_hint()))
            layout.addWidget(copy)

        picker = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("이미 설치돼 있다면 dwg2dxf 경로를 지정하세요")
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self._browse)
        picker.addWidget(self.path_edit)
        picker.addWidget(browse)
        layout.addLayout(picker)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_path)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "dwg2dxf 선택")
        if chosen:
            self.path_edit.setText(chosen)

    def _download(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            exe = engine.install_windows_engine(_install_dir())
        except Exception as err:  # 네트워크·해시·압축 실패를 한 문구로 보여준다
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "내려받기 실패", str(err))
            return
        QApplication.restoreOverrideCursor()
        self._save(exe)

    def _accept_path(self):
        text = self.path_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "경로 없음", "경로를 지정하거나 내려받기를 실행하세요.")
            return
        try:
            engine.verify_engine(Path(text))
        except engine.EngineInstallError as err:
            QMessageBox.warning(self, "사용할 수 없는 경로", str(err))
            return
        self._save(Path(text))

    def _save(self, exe: Path):
        QSettings().setValue(SETTINGS_KEY, str(exe))
        self.engine_path = exe
        self.accept()
