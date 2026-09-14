# EchoCad

A QGIS plugin that imports **DXF** drawings the way they were drawn — CAD layers, colours, line types, line widths, hatch patterns and text.

Everything runs on your machine. Drawings are never uploaded anywhere.

## What you get

- One QGIS layer per CAD layer, split by geometry type
- Entity colour, line type (solid / dashed / dash-dot) and line width carried over
- Text drawn at its original size, rotation and colour
- Hatch patterns rebuilt from the drawing's own definitions, not approximated
- Optional GeoPackage output
- Clear failure messages instead of silence — unsupported format, empty drawing, timeout are told apart

## DWG

QGIS itself cannot open DWG files newer than the 2000 format. This free edition reads **DXF** — save the drawing as DXF from the CAD you use, then import it here.

To open DWG directly (R14 to 2018 and newer) without a conversion step, see [EchoCad Pro](https://echocad.pages.dev) below.

## Installing

Install from the QGIS Plugin Manager, or download `echocad-<version>.zip` from the [releases](https://github.com/EchoPrime-Studio/echocad/releases) and use *Plugins → Manage and Install Plugins → Install from ZIP*.

Requires QGIS 3.34 LTR or newer. Windows and macOS are the supported platforms; nothing else needs installing.

The interface follows your QGIS language. Ten are shipped — English, German, Spanish,
French, Italian, Japanese, Korean, Polish, Portuguese and Chinese (Simplified).
Anything not translated falls back to English rather than showing a blank.
Corrections and new languages are welcome: one JSON file per language in `echocad/translations/`.

## Known limits

- Hatch patterns are rebuilt from the line families the drawing carries, so hatches read correctly. Patterns built from dots or filled shapes rather than lines fall back to a solid fill.
- External references (XREF) whose target file is missing are skipped — the import still completes and the missing file names are listed in the result, so you know why part of the drawing is absent.
- Features with obviously broken coordinates are dropped and the count is reported. Some drawings carry a handful of entities at absurd coordinates (10¹⁴ and beyond) that would otherwise make the whole drawing render as an empty page.
- Line weight is only applied where the drawing actually specifies it. Many drawings leave it at "default".

## EchoCad Pro

The paid edition opens **DWG directly in QGIS** and adds the parts that carry a drawing's *meaning* into GIS:

- DWG (R14 to 2018 and newer) opened without a conversion step — the converter is bundled, nothing to install, works offline on closed networks
- Drawings older than R14 that other tools reject are salvaged where possible
- Block attributes (`ATTRIB`) extracted into queryable attribute fields
- Mapping profiles — rules that merge several CAD layers into one of your feature classes, saved as a JSON file you can share and version
- Batch folder conversion with a per-file report

One office, 10 users, perpetual licence; one year of updates included. Details and pricing: <https://echocad.pages.dev>.

Pro installs as a **separate plugin** (`echocad_pro`), so it never collides with this one on updates. You can keep both installed; the menus are named differently.
