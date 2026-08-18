# dwg2dxf 확보를 안내하는 다이얼로그. OS별 경로가 다르고, 확보한 경로는 QSettings에 남는다
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import engine
from ..i18n import tr

SETTINGS_KEY = "echocad/dwg2dxf_path"


def saved_engine_path() -> Path | None:
    """사용자가 지정했거나 자동 확보한 경로. 없거나 사라졌으면 None."""
    value = QSettings().value(SETTINGS_KEY, "", type=str)
    path = Path(value) if value else None
    return path if path and path.exists() else None


def resolve_engine() -> Path:
    """저장된 경로를 먼저 보고, 없으면 PATH·번들 순으로 찾는다."""
    return engine.find_dwg2dxf(saved_engine_path())


class EngineSetupDialog(QDialog):
    """엔진이 없을 때 확보 방법을 안내한다. 확보에 성공하면 accept()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("EchoCad — Set up the DWG converter"))
        self.engine_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            tr("Reading DWG needs LibreDWG's dwg2dxf.")
            + "\n"
            + tr("Repository rules keep it out of the free edition — set the path once "
                 "and it is remembered.")
        ))

        hint = QLabel(engine.install_hint())
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint.setStyleSheet("QLabel { font-family: monospace; padding: 6px; }")
        layout.addWidget(hint)

        # OS를 가리지 않고 같은 흐름이다 — 페이지에서 받아 파일을 고르면 나머지는
        # 플러그인이 한다. 자동 다운로드는 공식 저장소 규칙 때문에 쓰지 않는다.
        actions = QHBoxLayout()
        download = QPushButton(tr("Open the download page"))
        download.clicked.connect(self._open_download_page)
        actions.addWidget(download)
        copy = QPushButton(tr("Copy the instructions"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(engine.install_hint()))
        actions.addWidget(copy)
        layout.addLayout(actions)

        picker = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr("If it is already installed, set the dwg2dxf path"))
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse)
        picker.addWidget(self.path_edit)
        picker.addWidget(browse)
        layout.addLayout(picker)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_path)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Choose dwg2dxf"))
        if chosen:
            self.path_edit.setText(chosen)

    def _open_download_page(self):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(engine.download_url()))

    def _accept_path(self):
        text = self.path_edit.text().strip()
        if not text:
            QMessageBox.warning(self, tr("No path"), tr("Set the path of the dwg2dxf you downloaded."))
            return
        chosen = Path(text)
        if not chosen.exists():
            QMessageBox.warning(self, tr("No path"), f"{tr('No such file')}: {chosen}")
            return
        try:
            # 권한·격리를 먼저 손봐야 verify_engine이 실행해 볼 수 있다. 맥에서
            # 격리된 채로 실행하면 응답 없이 멈춘다.
            engine.verify_engine(engine.prepare(chosen))
        except engine.EngineInstallError as err:
            QMessageBox.warning(self, tr("Unusable path"), str(err))
            return
        except OSError as err:
            QMessageBox.warning(self, tr("Setup failed"),
                                f"{tr('Cannot grant execute permission')}: {err}")
            return
        self._save(chosen)

    def _save(self, exe: Path):
        QSettings().setValue(SETTINGS_KEY, str(exe))
        self.engine_path = exe
        self.accept()
