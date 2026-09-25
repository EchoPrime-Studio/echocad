# AutoCAD 색번호(ACI) 1~255 를 RGB 로 옮기는 표
#
# 왜 표를 들고 있는가 - 플러그인은 pip 을 쓸 수 없고 ezdxf 를 항상 들고 있지도 않다.
# 255칸짜리 표는 데이터지 복잡도가 아니라서, 코드로 만들어 내려 하지 않고 그대로 담는다.
# 값은 ezdxf.colors.aci2rgb 에서 한 번 뽑아 왔다(2026-09-21, 표준 AutoCAD 팔레트).
# 251 만 ezdxf 의 #656565 대신 #333333 이다 - 250~255 는 #00·#33·#66·#99·#cc·#ff 의 회색
# 사다리이고 Autodesk 뷰어도 251 을 #333333 으로 그린다(2026-09-22 실측, 3d-slide 바닥판).
#
# 7번은 원래 흰색이지만 AutoCAD 는 밝은 바탕에서 검게 그린다. 뽑은 도면은 밝은
# 바탕에 놓이므로 여기서도 검게 둔다 - 흰색으로 두면 흰 종이 위에서 사라진다.
from __future__ import annotations

_TABLE = (
    "ff0000ffff0000ff0000ffff0000ffff00ffffffff808080c0c0c0ff0000ff7f7fa50000"
    "a552527f00007f3f3f4c00004c2626260000261313ff3f00ff9f7fa52900a567527f1f00"
    "7f4f3f4c13004c2f26260900261713ff7f00ffbf7fa55200a57c527f3f007f5f3f4c2600"
    "4c3926261300261c13ffbf00ffdf7fa57c00a591527f5f007f6f3f4c39004c4226261c00"
    "262113ffff00ffff7fa5a500a5a5527f7f007f7f3f4c4c004c4c26262600262613bfff00"
    "dfff7f7ca50091a5525f7f006f7f3f394c00424c261c26002126137fff00bfff7f52a500"
    "7ca5523f7f005f7f3f264c00394c261326001c26133fff009fff7f29a50067a5521f7f00"
    "4f7f3f134c002f4c2609260017261300ff007fff7f00a50052a552007f003f7f3f004c00"
    "264c2600260013261300ff3f7fff9f00a52952a567007f1f3f7f4f004c13264c2f002609"
    "13581700ff7f7fffbf00a55252a57c007f3f3f7f5f004c26264c3900261313581c00ffbf"
    "7fffdf00a57c52a591007f5f3f7f6f004c39264c4200261c13585800ffff7fffff00a5a5"
    "52a5a5007f7f3f7f7f004c4c264c4c00262613585800bfff7fdfff007ca55291a5005f7f"
    "3f6f7f00394c26427e001c26135858007fff7fbfff0052a5527ca5003f7f3f5f7f00264c"
    "26397e001326131c58003fff7f9fff0029a55267a5001f7f3f4f7f00134c262f7e000926"
    "1317580000ff7f7fff0000a55252a500007f3f3f7f00004c26267e0000261313583f00ff"
    "9f7fff2900a56752a51f007f4f3f7f13004c2f267e0900261713587f00ffbf7fff5200a5"
    "7c52a53f007f5f3f7f26004c39267e1300261c1358bf00ffdf7fff7c00a59152a55f007f"
    "6f3f7f39004c42264c1c0026581358ff00ffff7fffa500a5a552a57f007f7f3f7f4c004c"
    "4c264c260026581358ff00bfff7fdfa5007ca552917f005f7f3f6f4c00394c264226001c"
    "581358ff007fff7fbfa50052a5527c7f003f7f3f5f4c00264c263926001358131cff003f"
    "ff7f9fa50029a552677f001f7f3f4f4c00134c262f260009581317000000333333666666"
    "999999ccccccffffff"
)

# 도면층 색이 ByLayer(256)·ByBlock(0) 일 때 대신 쓸 색. AutoCAD 기본값이다.
DEFAULT = "#000000"


def aci_to_hex(aci) -> str:
    """색번호를 `#rrggbb` 로. 1~255 밖이면 기본색."""
    try:
        i = int(aci)
    except (TypeError, ValueError):
        return DEFAULT
    if not 1 <= i <= 255:
        return DEFAULT
    if i == 7:
        return DEFAULT          # 밝은 바탕에서는 검게. 표에는 흰색으로 들어 있다.
    at = (i - 1) * 6
    return "#" + _TABLE[at:at + 6]
