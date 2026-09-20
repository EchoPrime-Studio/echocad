# QGIS 플러그인 본체. 메뉴·툴바 액션을 등록하고 Pro가 있으면 Processing provider도 붙인다
from __future__ import annotations

from qgis.PyQt.QtWidgets import QAction

from .i18n import tr
from .ui.import_dialog import ImportDialog

# 무료판은 DXF 만 읽으므로 변환기 설정 화면이 아예 없다.
try:
    from .ui.engine_setup import EngineSetupDialog
except ImportError:      # 무료판
    EngineSetupDialog = None

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
        self._add_action(tr("Import drawing…"), self._open_import)
        if EngineSetupDialog is not None:
            self._add_action(tr("Converter setup…"), self._open_engine_setup)
        # Community 빌드에는 pro/ 폴더가 없어 이 메뉴와 알고리즘이 생기지 않는다.
        # 대신 Pro 안내를 띄운다 - 이게 없으면 Pro 가 있다는 것조차 알 길이 없다.
        try:
            from .pro.algorithms import EchoCadProvider
        except ImportError:
            self._add_action(tr("What Pro adds…"), self._open_pro_info)
            return
        self._add_action(tr("Licence…"), self._open_license)

        # 새 버전이 나왔는지 여기서 확인한다. 네트워크는 비동기라 시작을 막지 않는다.
        from .pro import updates

        updates.start(self.iface)

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
        ImportDialog(self.iface.mainWindow()).exec()

    def _open_engine_setup(self):
        EngineSetupDialog(self.iface.mainWindow()).exec()

    def _open_license(self):
        from .pro.license_dialog import LicenseDialog

        LicenseDialog(self.iface.mainWindow()).exec()

    def _open_pro_info(self):
        """무료판에만 있는 메뉴. pro/ 가 없어도 떠야 하므로 ui/ 에 둔다."""
        from .ui.pro_info import ProInfoDialog

        ProInfoDialog(self.iface.mainWindow()).exec()
