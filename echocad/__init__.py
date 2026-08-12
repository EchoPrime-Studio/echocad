# QGIS 플러그인 진입점. QGIS가 classFactory를 호출해 플러그인 인스턴스를 만든다


def classFactory(iface):
    from .plugin import EchoCadPlugin

    return EchoCadPlugin(iface)
