# QGIS 플러그인 본체. 메뉴·툴바 액션을 등록하고 Pro가 있으면 Processing provider도 붙인다
from __future__ import annotations

from qgis.PyQt.QtWidgets import QAction

from .ui.engine_setup import EngineSetupDialog
from .ui.import_dialog import ImportDialog

MENU = "&EchoCad"


class EchoCadPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions: list[QAction] = []

    def initGui(self):
        self._add_action("DWG 가져오기…", self._open_import)
        self._add_action("변환 엔진 설정…", self._open_engine_setup)
        # Community 빌드에는 pro/ 폴더가 없어 이 메뉴가 생기지 않는다.
        try:
            from .pro import license  # noqa: F401
        except ImportError:
            return
        self._add_action("라이선스…", self._open_license)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
            self.iface.removeToolBarIcon(action)
        self.actions.clear()

    def _add_action(self, text: str, slot):
        action = QAction(text, self.iface.mainWindow())
        action.triggered.connect(slot)
        self.iface.addPluginToMenu(MENU, action)
        self.actions.append(action)

    def _open_import(self):
        ImportDialog(self.iface.mainWindow()).exec_()

    def _open_engine_setup(self):
        EngineSetupDialog(self.iface.mainWindow()).exec_()

    def _open_license(self):
        from .pro.license_dialog import LicenseDialog

        LicenseDialog(self.iface.mainWindow()).exec_()
