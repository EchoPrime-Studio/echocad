# 가져오기 보고서를 만든다 - 도면에 있는 것 / 가져온 것 / 빠진 것과 그 이유. QGIS 없이 돈다
#
# 일반 가져오기는 DXF 원문(census)과 피처의 cad_handle 을 맞대고, 3D 각도 가져오기는
# view3d.project_with_report 의 보고를 받는다. 두 보고서는 같은 틀(dict)이라 같은 창·같은
# 전송 경로를 쓴다. 이 dict 가 그대로 우리 서버로 간다(사용자가 보내기를 누를 때만).
from __future__ import annotations

import platform
from collections import Counter

from .i18n import tr

SCHEMA = 1
MAX_ITEMS = 500           # 보고서에 싣는 빠진 항목 수 상한(종류별 수는 따로 다 센다)

# 이유 코드 → (짧은 이름, 설명, 할 일). 설명은 보고서 창에만, 목록엔 짧은 이름만 쓴다.
REASONS = {
    "proxy": ("AutoCAD-only object",
              "A custom object made by another AutoCAD application. Its shape is only known to that application.",
              "Explode it in AutoCAD (or save it without proxies) and import again."),
    "unsupported": ("Kind not drawn yet",
                    "EchoCad does not draw this kind of entity yet.",
                    "Send the report - we use these to decide what to support next."),
    "infinite": ("Infinite line",
                 "Construction lines (XLINE, RAY) go on forever and cannot be stored as map geometry.",
                 "Nothing to do - these are drawing aids."),
    "external": ("External file",
                 "An image or PDF underlay is a link to another file, not part of the drawing.",
                 "Add the image or PDF to QGIS separately."),
    "hidden": ("Hidden layer (your choice)",
               "The layer is off or frozen in the drawing and you chose to skip hidden layers.",
               "Untick 'skip hidden layers' to bring it in."),
    "excluded": ("Left out by your layer rules",
                 "You unticked this layer in the layer rules.",
                 "Tick it in the layer rules to bring it in."),
    "needs_pro": ("Needs EchoCad Pro",
                  "3D solids are read by EchoCad Pro.",
                  "Open the drawing with EchoCad Pro."),
    "block_empty": ("Nothing to draw",
                    "This block reference points to a block that holds only attribute "
                    "definitions, not shapes.",
                    "Nothing is missing - the drawing has no shapes at these places."),
    "block_missing": ("Block definition missing",
                      "The block this reference points to is not defined in the drawing.",
                      "Send the report with the missing data attached so we can fix it."),
    "unreadable": ("Could not be read",
                   "The converter or reader could not turn this entity into geometry.",
                   "Send the report with the missing data attached so we can fix it."),
    "cross": ("Too oblique",
              "Seen at a very flat angle, the face folds over itself and cannot be drawn flat.",
              "Turn the view a little and import again."),
    "merge": ("Outline failed",
              "The pieces of this face could not be joined into one outline.",
              "Turn the view a little and import again."),
}

def _block_reason(census, entity) -> str | None:
    """블록 참조(INSERT)가 안 들어온 이유. 블록 안을 보고 정한다. 모르면 None.

    "읽지 못함" 만 적으면 사용자가 원인을 알 수 없다. 속성 정의만 있고 그릴 도형이 없는
    블록이 실제로 있다 - 그때는 빠진 것이 아니라 원래 그릴 게 없는 것이다
    (2026-09-25 구매자 점검, `OutdoorInfo` 블록).
    """
    from .census import NOT_GEOMETRY

    if entity.kind != "INSERT" or not entity.block:
        return None
    if entity.block not in census.blocks:
        return "block_missing"
    drawable = [k for k in census.blocks[entity.block]
                if k not in NOT_GEOMETRY and k != "ATTDEF"]
    return None if drawable else "block_empty"


KIND_REASON = {
    "ACAD_PROXY_ENTITY": "proxy", "ACAD_PROXY_OBJECT": "proxy",
    "XLINE": "infinite", "RAY": "infinite",
    "IMAGE": "external", "PDFUNDERLAY": "external", "DWFUNDERLAY": "external",
    "DGNUNDERLAY": "external", "OLE2FRAME": "external", "OLEFRAME": "external", "WIPEOUT": "external",
    "TOLERANCE": "unsupported", "MESH": "unsupported", "SHAPE": "unsupported",
    "SURFACE": "unsupported", "PLANESURFACE": "unsupported", "LIGHT": "unsupported",
    "SUN": "unsupported", "HELIX": "unsupported", "SECTIONOBJECT": "unsupported",
    "ARC_DIMENSION": "unsupported", "UNDERLAY": "external",
}

# 우리 잘못이 아니거나 사용자가 고른 것 - '빠짐' 이 아니라 '뺌' 으로 센다.
BY_CHOICE = {"hidden", "infinite", "external", "excluded"}


def reason_text(code: str) -> tuple[str, str, str]:
    name, why, fix = REASONS.get(code, REASONS["unreadable"])
    return tr(name), tr(why), tr(fix)


def _meta(extra: dict | None = None) -> dict:
    meta = {"os": f"{platform.system()} {platform.release()}", "python": platform.python_version()}
    try:
        from qgis.core import Qgis
        meta["qgis"] = Qgis.version()
    except Exception:
        meta["qgis"] = ""       # QGIS 밖에서 돌 때. 빈 값으로 두고 계속한다
    try:
        from pathlib import Path
        text = (Path(__file__).resolve().parent / "metadata.txt").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("version="):
                meta["plugin"] = line.split("=", 1)[1].strip()
            if line.startswith("name="):
                meta["edition"] = line.split("=", 1)[1].strip()
    except OSError:
        meta["plugin"] = ""     # metadata.txt 를 못 읽으면 판 번호만 빈 값으로 둔다
    meta.update(extra or {})
    return meta


def build_import_report(file_name: str, census, imported_handles, *, hidden_layers=(),
                        excluded_layers=(), pro: bool = True, elapsed: float = 0.0, layers: int = 0,
                        features: int = 0, dropped: int = 0, notes=(), dxf_version: str = "") -> dict:
    """일반 가져오기 보고서. census 는 census.read 의 결과, imported_handles 는 가져온 피처의 핸들들."""
    got = {str(h).upper() for h in imported_handles if h}
    hidden = set(hidden_layers or ())
    excluded = set(excluded_layers or ())
    kinds: Counter = Counter()
    kinds_in: Counter = Counter()
    missing = []
    for e in census.entities:
        kinds_in[e.kind] += 1
        if e.handle.upper() in got:
            kinds[e.kind] += 1
            continue
        if e.layer in excluded:
            code = "excluded"
        elif e.layer in hidden:
            code = "hidden"
        elif e.kind == "3DSOLID" and not pro:
            code = "needs_pro"
        else:
            code = _block_reason(census, e) or KIND_REASON.get(e.kind, "unreadable")
        missing.append({"handle": e.handle, "kind": e.kind, "layer": e.layer, "reason": code})
    lost = [m for m in missing if m["reason"] not in BY_CHOICE]
    rows = []
    for kind, n in sorted(kinds_in.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append({"kind": kind, "in_drawing": n, "imported": kinds[kind],
                     "missing": sum(1 for m in lost if m["kind"] == kind),
                     "skipped": sum(1 for m in missing if m["kind"] == kind and m["reason"] in BY_CHOICE)})
    by_reason = Counter(m["reason"] for m in missing)
    return {
        "schema": SCHEMA, "type": "import", "file": file_name,
        "totals": {"in_drawing": len(census.entities), "imported": len(census.entities) - len(missing),
                   "missing": len(lost), "skipped": len(missing) - len(lost),
                   "layers": layers, "features": features, "dropped_geometries": dropped},
        "kinds": rows,
        "reasons": dict(by_reason),
        "missing": missing[:MAX_ITEMS],
        "missing_total": len(missing),
        "notes": [n for n in notes if n],
        "elapsed_sec": round(elapsed, 2),
        "meta": _meta({"dxf_version": dxf_version}),
    }


def build_angle_report(file_name: str, report3d: dict, heading: float, pitch: float) -> dict:
    """3D 각도 가져오기 보고서. report3d 는 view3d.project_with_report 의 보고."""
    missing = [{"handle": str(item.get("handle", "")), "kind": "3DSOLID face", "layer": item["cad_layer"],
                "reason": item["reason"], "surface": item["surface"], "solid": item["solid"],
                "partial": item["partial"]} for item in report3d["missing"]]
    return {
        "schema": SCHEMA, "type": "angle", "file": file_name,
        "angle": {"heading": round(heading, 1), "pitch": round(pitch, 1)},
        "totals": {"in_drawing": report3d["expected"], "imported": report3d["done"],
                   "missing": len(missing), "skipped": 0},
        "kinds": [{"kind": "3DSOLID face", "in_drawing": report3d["expected"],
                   "imported": report3d["done"], "missing": len(missing), "skipped": 0}],
        "reasons": dict(Counter(m["reason"] for m in missing)),
        "missing": missing[:MAX_ITEMS],
        "missing_total": len(missing),
        "notes": [],
        "meta": _meta(),
    }


def missing_handles(report: dict, include_choice: bool = False) -> list[str]:
    """첨부로 잘라 낼 핸들. 사용자가 고른 뺌(숨긴 도면층 등)은 보내지 않는다."""
    return [m["handle"] for m in report.get("missing", [])
            if m.get("handle") and (include_choice or m["reason"] not in BY_CHOICE)]


def to_html(report: dict) -> str:
    """보고서를 한 장짜리 HTML 로. 파일로 저장하거나 메일에 붙이는 용도(바깥 파일 없음)."""
    import html

    t = report["totals"]
    ok = t["missing"] == 0
    rows = "".join(
        f"<tr><td>{html.escape(r['kind'])}</td><td class=n>{r['in_drawing']}</td>"
        f"<td class=n>{r['imported']}</td><td class='n {'bad' if r['missing'] else ''}'>{r['missing'] or ''}</td>"
        f"<td class=n muted>{r['skipped'] or ''}</td>"
        f"<td><div class=bar><i style='width:{(100 * r['imported'] / r['in_drawing']) if r['in_drawing'] else 0:.0f}%'></i></div></td></tr>"
        for r in report["kinds"])
    items = "".join(
        f"<tr><td>{html.escape(m['kind'])}</td><td>{html.escape(m['layer'] or '0')}</td>"
        f"<td class=mono>{html.escape(m['handle'])}</td><td>{html.escape(reason_text(m['reason'])[0])}</td></tr>"
        for m in report["missing"])
    why = "".join(
        f"<li><b>{html.escape(reason_text(code)[0])}</b> · {n}<br><span>{html.escape(reason_text(code)[1])}</span>"
        f"<br><em>→ {html.escape(reason_text(code)[2])}</em></li>"
        for code, n in sorted(report["reasons"].items(), key=lambda kv: -kv[1]))
    meta = " · ".join(f"{k} {html.escape(str(v))}" for k, v in report.get("meta", {}).items())
    title = tr("Import report")
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{html.escape(title)} — {html.escape(report['file'])}</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;background:#f6f7f9;margin:0;padding:32px}}
.wrap{{max-width:880px;margin:auto;background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:28px 32px}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#6b7280;font-size:13px;margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}}
.card{{background:#f7f8fa;border:1px solid #eceef2;border-radius:10px;padding:14px 16px}}
.card small{{color:#6b7280}} .card b{{display:block;font-size:26px}}
.ok b{{color:#146c2e}} .bad b{{color:#b42318}}
table{{width:100%;border-collapse:collapse;margin-bottom:24px}} th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid #f0f2f5}}
th{{color:#6b7280;font-weight:600;font-size:12px}} td.n{{text-align:right;font-variant-numeric:tabular-nums}} td.bad{{color:#b42318;font-weight:600}}
.bar{{height:6px;background:#eceef2;border-radius:3px;min-width:80px}} .bar i{{display:block;height:6px;background:#0696d7;border-radius:3px}}
.mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px}} ul{{padding-left:18px}} li{{margin-bottom:10px}} li span{{color:#3b4250}} li em{{color:#0b5cad;font-style:normal}}
h2{{font-size:14px;margin:0 0 8px}} footer{{color:#9aa1ab;font-size:12px}}
</style></head><body><div class=wrap>
<h1>{html.escape(title)} — {html.escape(report['file'])}</h1>
<div class=sub>{html.escape(report['type'])}{(' · ' + str(report['angle'])) if report.get('angle') else ''}</div>
<div class=cards>
<div class="card {'ok' if ok else 'bad'}"><small>{html.escape(tr('Imported'))}</small><b>{t['imported']} / {t['in_drawing']}</b></div>
<div class="card {'bad' if t['missing'] else ''}"><small>{html.escape(tr('Missing'))}</small><b>{t['missing']}</b></div>
<div class=card><small>{html.escape(tr('Left out by choice'))}</small><b>{t['skipped']}</b></div>
</div>
<h2>{html.escape(tr('By kind'))}</h2>
<table><tr><th>{html.escape(tr('Kind'))}</th><th>{html.escape(tr('In drawing'))}</th><th>{html.escape(tr('Imported'))}</th><th>{html.escape(tr('Missing'))}</th><th>{html.escape(tr('Left out'))}</th><th></th></tr>{rows}</table>
{('<h2>' + html.escape(tr('Why')) + '</h2><ul>' + why + '</ul>') if why else ''}
{('<h2>' + html.escape(tr('Not imported')) + '</h2><table><tr><th>' + html.escape(tr('Kind')) + '</th><th>' + html.escape(tr('Layer')) + '</th><th>Handle</th><th>' + html.escape(tr('Why')) + '</th></tr>' + items + '</table>') if items else ''}
<footer>{meta}</footer></div></body></html>"""
