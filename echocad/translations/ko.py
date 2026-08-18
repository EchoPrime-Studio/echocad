# 한국어 번역표. 키는 소스의 영어 원문 그대로여야 한다 — 한 글자만 달라도 안 걸린다
# 중괄호 자리표시자({count} 등)는 번역에서도 그대로 남겨야 format이 동작한다.

TABLE = {
    # 메뉴
    "Import DWG…": "DWG 가져오기…",
    "Converter setup…": "변환 엔진 설정…",
    "Licence…": "라이선스…",

    # 가져오기 창
    "EchoCad — Import DWG": "EchoCad — DWG 가져오기",
    "Drawing to import": "가져올 도면",
    "DWG file": "DWG 파일",
    "Browse…": "찾아보기…",
    "Coordinate system": "좌표계",
        "Save to GeoPackage (unchecked: temporary layers)":
        "GeoPackage로 저장 (체크 해제 시 임시 레이어)",
    "Path of the .gpkg to write": "저장할 .gpkg 경로",
    "Save location…": "저장 위치…",
    "Mapping profile (optional)": "매핑 프로파일 (선택)",
    "Rule file that maps CAD layer names onto your GIS schema":
        "CAD 레이어명을 GIS 스키마로 옮기는 규칙 파일",
    "Open…": "열기…",
    "Create example…": "예시 만들기…",
    "Choose DWG": "DWG 선택",
    "DWG drawing (*.dwg)": "DWG 도면 (*.dwg)",
    "Save GeoPackage": "GeoPackage 저장",
    "Choose mapping profile": "매핑 프로파일 선택",
    "Save example profile": "예시 프로파일 저장",
    "Profile (*.json)": "프로파일 (*.json)",
    "Example": "예시",
    "match is a glob pattern. The first rule that matches wins.":
        "match는 glob 패턴입니다. 위에서부터 첫 매칭이 적용됩니다.",

    # 가져오기 결과·오류
    "No file": "파일 없음",
    "Choose a DWG file first.": "먼저 DWG 파일을 지정하세요.",
    "Choose the DWG file to import.": "가져올 DWG 파일을 지정하세요.",
    "No converter": "변환 엔진 없음",
    "No coordinate system": "좌표계 없음",
    "Choose the coordinate system of the drawing.": "도면의 좌표계를 지정하세요.",
    "No save location": "저장 위치 없음",
    "Set the GeoPackage path.": "GeoPackage 경로를 지정하세요.",
    "Could not read it": "읽지 못했습니다",
    "Could not save": "저장 실패",
    "Profile error": "프로파일 오류",
    "Import failed": "가져오기 실패",
    "Import finished": "가져오기 완료",
    "Imported {layers} layers and {features} features.":
        "레이어 {layers}개, 피처 {features}개를 가져왔습니다.",
    "CAD layers no profile rule matched": "프로파일 규칙에 걸리지 않은 CAD 레이어",
    " and {count} more": " 외 {count}개",
    "Could not import it.": "가져오지 못했습니다.",

    # 변환 결과 상태
    "Unsupported drawing format. Only DWG R14 and newer can be read.":
        "지원하지 않는 도면 포맷입니다. R14 이상 DWG만 읽을 수 있습니다.",
    "Conversion timed out. The drawing may be very large or damaged.":
        "변환이 제한 시간을 넘겼습니다. 도면이 매우 크거나 손상됐을 수 있습니다.",
    "Converted, but there is nothing to draw. This may be a metadata-only drawing.":
        "변환은 됐지만 그릴 엔티티가 없습니다. 메타데이터 전용 도면일 수 있습니다.",
    "Conversion stopped part way, so the drawing content is missing.":
        "변환이 중간에 끊겨 도면 내용이 통째로 빠졌습니다.",
    "Conversion failed.": "변환에 실패했습니다.",

    # 임포터
    "Could not read the DXF": "DXF를 읽지 못했습니다",
    "Cancelled": "사용자가 취소했습니다",
    "No entities": "엔티티가 없습니다",
    "Dropped {count} features with broken coordinates":
        "좌표가 깨진 피처 {count}개를 제외했습니다",
    "No pattern": "채움 없음",
    "could not save": "저장 실패",
    "External reference files are missing, so their content is absent":
        "외부 참조 파일이 없어 그 내용은 빠졌습니다",

    # 변환 엔진
    "EchoCad — Set up the DWG converter": "EchoCad — DWG 변환 엔진 준비",
    "Reading DWG needs LibreDWG's dwg2dxf.": "DWG를 읽으려면 LibreDWG의 dwg2dxf가 필요합니다.",
    "Repository rules keep it out of the free edition — set the path once "
    "and it is remembered.":
        "공식 저장소 규칙상 무료판에는 동봉하지 않습니다 — 한 번만 지정하면 계속 쓰입니다.",
    "Open the download page": "다운로드 페이지 열기",
    "Copy the instructions": "안내 복사",
    "If it is already installed, set the dwg2dxf path":
        "이미 설치돼 있다면 dwg2dxf 경로를 지정하세요",
    "Choose dwg2dxf": "dwg2dxf 선택",
    "No path": "경로 없음",
    "Set the path of the dwg2dxf you downloaded.": "내려받은 dwg2dxf 경로를 지정하세요.",
    "No such file": "그런 파일이 없습니다",
    "Unusable path": "사용할 수 없는 경로",
    "Setup failed": "준비 실패",
    "Cannot grant execute permission": "실행 권한을 줄 수 없습니다",
    "dwg2dxf was not found.": "dwg2dxf를 찾을 수 없습니다.",
    "Cannot run it": "실행할 수 없습니다",
    "This is not dwg2dxf": "dwg2dxf가 아닙니다",
    "Format {version}. Only R14 and newer are supported.":
        "{version} 포맷입니다. R14 이상만 지원합니다.",
    "The DXF was not written to the end. dwg2dxf 0.14 or newer is required.":
        "결과 DXF가 끝까지 쓰이지 않았습니다. dwg2dxf 0.14 이상이 필요합니다.",

    # 엔진 확보 안내 (OS별)
    "Download the win64 zip from the LibreDWG releases page and unpack it,\n"
    "then point the field below at dwg2dxf.exe inside it.":
        "LibreDWG 공식 릴리스에서 win64 zip을 내려받아 풀고,\n"
        "그 안의 dwg2dxf.exe 경로를 아래에 지정하세요.",
    "Download the macOS build of dwg2dxf from the EchoCad releases page\n"
    "and unpack it, then point the field below at it. Execute permission\n"
    "and the quarantine flag are handled for you.\n"
    "(brew install libredwg works too, but ships 0.13.3, which truncates\n"
    "some drawings.)":
        "EchoCad 릴리스 페이지에서 macOS용 dwg2dxf를 내려받아 풀고,\n"
        "그 파일을 아래에 지정하세요. 실행 권한과 격리 해제는 플러그인이 처리합니다.\n"
        "(brew install libredwg도 되지만 0.13.3이라 일부 도면이 잘립니다)",
    "Linux is not an officially supported platform.\n"
    "It works if you obtain dwg2dxf yourself — Homebrew\n"
    "(brew install libredwg) or a source build — and set the path.":
        "Linux는 공식 지원 대상이 아닙니다.\n"
        "Homebrew(brew install libredwg)나 소스 빌드로 dwg2dxf를 준비한 뒤\n"
        "경로를 직접 지정하면 동작합니다.",

    # 라이선스
    "EchoCad — Licence": "EchoCad — 라이선스",
    "Licence key": "라이선스 키",
    "Paste the key you were given when you bought it": "구매 시 받은 키를 붙여 넣으세요",
    "Activate": "활성화",
    "Pro features": "Pro 기능",
    "available": "사용 가능",
    "locked": "잠김",
    "Update subscription ends": "업데이트 구독 만료",
    "No key": "키 없음",
    "Enter the licence key.": "라이선스 키를 입력하세요.",
    "Build error": "빌드 오류",
    "Check failed": "확인 실패",
    "Could not verify the licence.": "라이선스를 확인하지 못했습니다.",
    "Check your internet connection and try again.": "인터넷 연결을 확인한 뒤 다시 시도하세요.",
    "The licence is not activated.": "라이선스가 활성화되지 않았습니다.",
    "The licence is not valid.": "라이선스가 유효하지 않습니다.",
    "This licence has no expiry.": "무기한 라이선스입니다.",
    "The offline grace period ({days} days) has passed. "
    "Connect to the internet and check again.":
        "오프라인 유예 기간({days}일)이 지났습니다. 인터넷에 연결해 다시 확인해 주세요.",
    "This version is inside your update subscription (expires {expiry}).":
        "업데이트 구독 범위 안의 버전입니다 (만료 {expiry}).",
    "This version was released after your update subscription expired "
    "({expiry}). Earlier versions keep working.":
        "이 버전은 업데이트 구독 만료({expiry}) 이후에 배포됐습니다. "
        "이전 버전은 계속 사용할 수 있습니다.",
    "Could not read the expiry date": "만료일을 읽지 못했습니다",
    "This build carries no licence server details. It was packaged "
    "incorrectly — please contact where you bought it.":
        "이 빌드에는 라이선스 서버 정보가 들어 있지 않습니다. "
        "잘못 만들어진 배포본이니 판매처에 문의해 주세요.",

    # 매핑 프로파일
    "A profile must be an object.": "프로파일은 객체여야 합니다.",
    "A rule must be an object.": "규칙은 객체여야 합니다.",
    "A rule needs a match pattern.": "규칙에는 match 패턴이 있어야 합니다.",
    "A rule has unknown keys: {unknown}. Allowed keys are {allowed}.":
        "규칙에 모르는 항목이 있습니다: {unknown}. 쓸 수 있는 항목은 {allowed}입니다.",
    "match must be a string.": "match는 문자열이어야 합니다.",
    "layer_name must be a string.": "layer_name은 문자열이어야 합니다.",
    "geometry must be one of {allowed}: {value}":
        "geometry는 {allowed} 중 하나여야 합니다: {value}",
    "rules must be an array.": "rules는 배열이어야 합니다.",
    "Unsupported format version": "지원하지 않는 형식 버전입니다",
    "Could not read the JSON": "JSON을 읽지 못했습니다",
    "Could not read the file": "파일을 읽지 못했습니다",
    "Could not read the mapping profile": "매핑 프로파일을 읽지 못했습니다",
    "unnamed": "이름 없음",

    # 좌표계 후보 창
    "EchoCad — Coordinate system candidates": "EchoCad — 좌표계 후보",
    "Coordinate systems that fit the drawing's extents.":
        "도면의 좌표 범위에 부합하는 좌표계입니다.",
    "The extents do not narrow down a coordinate system.":
        "좌표 범위로는 좌표계를 추정할 수 없습니다.",
    "Several coordinate systems share this value range, so the extents "
    "alone cannot pick one. Choose by where the drawing came from.":
        "여러 좌표계가 같은 값 범위를 쓰므로 좌표만으로는 하나로 좁혀지지 않습니다. "
        "도면 출처를 보고 고르세요.",
    "These look like local coordinates near the origin. "
    "Set the coordinate system yourself.":
        "원점 근처의 국소 좌표로 보입니다. 좌표계를 직접 지정하세요.",

    # 프로세싱 알고리즘
    "Import DWG": "DWG 가져오기",
    "Imports one DWG drawing as QGIS layers. Each CAD layer becomes "
    "its own layer, and colour, line type, line width and text are "
    "carried over as they are in the drawing.":
        "DWG 도면 한 장을 QGIS 레이어로 가져옵니다. CAD 레이어마다 레이어가 하나씩 "
        "만들어지고 색·선종류·선폭·글자가 원본대로 옮겨집니다.",
    "Batch import DWG": "일괄 DWG 가져오기",
    "Converts every DWG in a folder and writes a per-file result CSV. "
    "One file failing does not stop the rest.":
        "폴더 안의 DWG를 모두 변환하고 파일별 결과를 CSV로 남깁니다. "
        "한 파일이 실패해도 나머지는 계속 처리합니다.",
    "Mapping profile (JSON)": "매핑 프로파일 (JSON)",
    "Extract block attributes into fields": "블록 속성을 필드로 추출",
    "Output folder": "결과 폴더",
    "DWG folder": "DWG 폴더",
    "Include subfolders": "하위 폴더 포함",
    "Conversion report": "변환 리포트",
    "Batch conversion": "일괄 변환",
    "No DWG files found": "DWG 파일이 없습니다",
    "Cancelled — the report covers what finished before that.":
        "취소됨 — 여기까지의 결과로 리포트를 씁니다.",
    "{layers} layers, {features} features": "레이어 {layers}개, 피처 {features}개",
    "{ok} of {total} succeeded — report {report}":
        "성공 {ok} / 전체 {total} — 리포트 {report}",
    "{feature} is a paid feature.": "{feature}은(는) 유료판 기능입니다.",
    "Activate your key in the Licence window of the plugin menu.":
        "플러그인 메뉴의 라이선스 창에서 키를 활성화해 주세요.",

    # 좌표계 지역 선택
    "Region": "지역",
    "— choose —": "— 선택 —",
    "Choose the region the drawing comes from. "
    "Coordinate values alone cannot identify a system.":
        "도면이 어느 지역 것인지 고르세요. 좌표값만으로는 좌표계를 특정할 수 없습니다.",
    "No coordinate system in this region fits the drawing's extents.":
        "이 지역 좌표계 중 도면 범위에 맞는 것이 없습니다.",
    "These may be local coordinates near the origin, or the drawing may use "
    "a system from another region.":
        "원점 근처의 국소 좌표이거나, 다른 지역 좌표계를 쓴 도면일 수 있습니다.",
    "Used in {area}": "사용 지역 — {area}",
}
