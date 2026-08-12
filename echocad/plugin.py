# QGIS 플러그인 본체. 메뉴·툴바 액션을 등록하고 Pro가 있으면 Processing provider도 붙인다
from __future__ import annotations

from qgis.PyQt.QtWidgets import QAction

from .ui.engine_setup import EngineSetupDialog
from .ui.import_dialog import ImportDialog

def _menu_label() -> str:
    """무료판과 유료판은 플러그인 ID가 달라 함께 설치될 수 있다. 메뉴로 구분한다."""
    try:
        import importlib

        importlib.import_module(f"{__package__}.pro")
    except ImportError:
        return "&EchoCad"
    return "&EchoCad Pro"


MENU = _menu_label()


class EchoCadPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions: list[QAction] = []
        self.provider = None

    def initGui(self):
        self._add_action("DWG 가져오기…", self._open_import)
        self._add_action("변환 엔진 설정…", self._open_engine_setup)
        # Community 빌드에는 pro/ 폴더가 없어 이 메뉴와 알고리즘이 생기지 않는다.
        try:
            from .pro.algorithms import EchoCadProvider
        except ImportError:
            return
        self._add_action("라이선스…", self._open_license)

        from qgis.core import QgsApplication

        self.provider = EchoCadProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
            self.iface.removeToolBarIcon(action)
        self.actions.clear()

        if self.provider is not None:
            from qgis.core import QgsApplication

            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

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
