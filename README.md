# EchoCad

A QGIS plugin that imports AutoCAD **DWG** drawings, keeping the things that make a drawing readable — CAD layers, colours, line types, line widths and text.

Everything runs on your machine. Drawings are never uploaded anywhere.

## What you get

- One QGIS layer per CAD layer, split by geometry type
- Entity colour, line type (solid / dashed / dash-dot) and line width carried over
- Text drawn at its original size, rotation and colour
- Optional GeoPackage output
- Clear failure messages instead of silence — unsupported format, empty drawing, timeout are told apart

Supported drawing formats are **R14 and newer**. R13 and older are rejected up front with a message rather than failing halfway.

## Installing

Install from the QGIS Plugin Manager, or download a release and use *Plugins → Manage and Install Plugins → Install from ZIP*.

## The DWG converter

Reading DWG needs [LibreDWG](https://www.gnu.org/software/libredwg/)'s `dwg2dxf`. It is **not bundled** with this plugin: LibreDWG is GPL-3 and this plugin only calls it as a separate process, so redistributing its binaries here would be wrong.

On first run the plugin shows you how to get it:

| Platform | How |
|---|---|
| Windows | One click — the plugin downloads the official release and verifies its SHA-256 |
| macOS | `brew install libredwg` |
| Anywhere | Point the plugin at an existing `dwg2dxf` |

Linux is not an officially supported platform for now, because LibreDWG is not in the major distribution repositories. It works if you build or install `dwg2dxf` yourself and set the path.

## Known limits

- Hatch **patterns** are not reproduced; hatches come through as solid fills, because the underlying reader only exposes the fill colour.
- External references (XREF) to missing files are skipped.
- Features with obviously broken coordinates are dropped, and the count is reported. Some drawings contain a handful of entities at absurd coordinates that would otherwise make the whole drawing appear empty.

## EchoCad Pro

A paid edition adds the parts that map a drawing's *meaning* into a GIS schema:

- Block attributes (`ATTRIB`) extracted into queryable attribute fields
- Mapping profiles — rules that merge CAD layers into your own feature classes
- Coordinate system suggestions, batch conversion with a report

It is sold as a perpetual licence with a separate annual update subscription. See the [project homepage](https://gitlab.com/spiegel/echocad).

## Licence

GPL-2.0-or-later, the same licence as QGIS. The `echocad/` directory in this repository is the complete source of the plugin.
