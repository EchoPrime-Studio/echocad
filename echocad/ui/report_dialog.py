# 가져오기 보고서 창 - 도면에 있는 것 / 가져온 것 / 빠진 것과 이유. 보내기는 Pro 에서만 켜진다
#
# 배치는 3D 창 오른쪽 칸과 같은 말투다: 위에 숫자 칸 셋, 가운데 탭 [종류별 | 빠진 것 N],
# 단추는 모두 맨 아래 한 줄. 설명 문장은 빠진 것의 이유 칸에만 쓴다(그 외엔 숫자·색으로).
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                                 QListWidget, QListWidgetItem, QPushButton, QTableWidget,
                                 QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import importreport
from ..i18n import tr

STYLE = """
QDialog { background: #ffffff; }
QLabel#file { color: #1f2328; font-size: 16px; font-weight: 700; }
QLabel#sub { color: #6b7280; font-size: 12px; }
QFrame#card { background: #f7f8fa; border: 1px solid #eceef2; border-radius: 10px; }
QLabel#cardlabel { color: #6b7280; font-size: 12px; }
QLabel#cardvalue { color: #1f2328; font-size: 24px; font-weight: 700; }
QLabel#cardvalue[tone="ok"] { color: #146c2e; }
QLabel#cardvalue[tone="bad"] { color: #b42318; }
QLabel#cardvalue[tone="muted"] { color: #9aa1ab; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #f1f3f6; color: #4a5160; padding: 6px 16px; margin-right: 4px;
               border-radius: 6px; font-size: 12px; }
QTabBar::tab:selected { background: #e8f2fc; color: #0b5cad; font-weight: 600; }
QTableWidget { background: #ffffff; alternate-background-color: #fafbfc; color: #1f2328;
               border: 1px solid #eceef2; border-radius: 8px; gridline-color: #f0f2f5; font-size: 12px;
               selection-background-color: #e8f2fc; selection-color: #111418; }
QTableWidget::item { color: #1f2328; padding: 4px 6px; }
QTableCornerButton::section { background: #fafbfc; border: none; }
QHeaderView::section { background: #fafbfc; color: #6b7280; border: none; border-bottom: 1px solid #eceef2;
                       padding: 6px 8px; font-size: 11px; font-weight: 600; }
QListWidget { background: #ffffff; color: #1f2328; border: 1px solid #eceef2; border-radius: 8px;
              font-size: 12px; outline: none; }
QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #f3f4f6; }
QListWidget::item:selected { background: #e8f2fc; color: #111418; }
QFrame#why { background: #f7f8fa; border-radius: 8px; }
QLabel#whytext { color: #3b4250; font-size: 12px; }
QLabel#fixtext { color: #0b5cad; font-size: 12px; font-weight: 600; }
QPushButton { background: #ffffff; color: #1f2328; border: 1px solid #cfd4db; border-radius: 6px;
              padding: 8px 16px; font-size: 13px; }
QPushButton:hover { background: #f4f6f9; }
QPushButton:disabled { color: #b0b6bf; border-color: #e5e8ec; }
QPushButton#primary { background: #0696d7; color: #ffffff; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #0584bd; }
QPushButton#primary:disabled { background: #9fd3ee; color: #ffffff; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0px; }
QScrollBar::handle:vertical { background: #d3d8e0; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def _card(label: str, value: str, tone: str) -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    box = QVBoxLayout(card)
    box.setContentsMargins(14, 10, 14, 10)
    box.setSpacing(2)
    name = QLabel(label)
    name.setObjectName("cardlabel")
    number = QLabel(value)
    number.setObjectName("cardvalue")
    number.setProperty("tone", tone)
    box.addWidget(name)
    box.addWidget(number)
    return card


class ReportDialog(QDialog):
    """보고서 창. report 는 importreport 의 dict, excerpt 는 빠진 것만 잘라 낸 DXF 글."""

    def __init__(self, report: dict, excerpt: str = "", parent=None, sender=None):
        super().__init__(parent)
        self.report = report
        self.excerpt = excerpt
        self.sender = sender            # 보내기 함수(Pro). None 이면 보내기 단추가 없다
        self.setWindowTitle(tr("Import report"))
        self.setStyleSheet(STYLE)
        self.resize(760, 620)

        t = report["totals"]
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 16)
        box.setSpacing(14)

        name = QLabel(report["file"])
        name.setObjectName("file")
        box.addWidget(name)
        sub = QLabel(self._subtitle())
        sub.setObjectName("sub")
        box.addWidget(sub)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        cards.addWidget(_card(tr("Imported"), f"{t['imported']} / {t['in_drawing']}",
                              "ok" if not t["missing"] else "bad"))
        cards.addWidget(_card(tr("Missing"), str(t["missing"]), "bad" if t["missing"] else "muted"))
        cards.addWidget(_card(tr("Left out by choice"), str(t["skipped"]), "muted"))
        box.addLayout(cards)

        tabs = QTabWidget()
        tabs.addTab(self._kinds_tab(), tr("By kind"))
        # 탭 숫자는 위 '빠짐' 칸과 같게 - 선택해서 뺀 것은 목록에 회색으로만 함께 둔다
        tabs.addTab(self._missing_tab(), tr("Not imported") + (f"  {t['missing']}" if t["missing"] else ""))
        if t["missing"]:
            tabs.setCurrentIndex(1)
        box.addWidget(tabs, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        save = QPushButton(tr("Save as HTML…"))
        save.clicked.connect(self._save_html)
        row.addWidget(save)
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        if sender is not None:
            send = QPushButton(tr("Send report…"))
            send.setObjectName("primary")
            send.clicked.connect(self._send)
            row.addWidget(send)
            self.send_button = send
        box.addLayout(row)

    # ----------------------------------------------------------------------
    def _subtitle(self) -> str:
        r = self.report
        if r["type"] == "angle":
            a = r.get("angle", {})
            return f"{tr('3D angle import')} · {a.get('heading', 0):.0f}° / {a.get('pitch', 0):.0f}°"
        parts = [tr("Drawing import")]
        if r.get("elapsed_sec"):
            parts.append(f"{r['elapsed_sec']:.1f} s")
        if r["totals"].get("layers"):
            parts.append(tr("{count} layers").format(count=r["totals"]["layers"]))
        return " · ".join(parts)

    def _kinds_tab(self) -> QWidget:
        table = QTableWidget(len(self.report["kinds"]), 5)
        table.setHorizontalHeaderLabels([tr("Kind"), tr("In drawing"), tr("Imported"),
                                         tr("Missing"), tr("Left out")])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setShowGrid(False)
        for i, r in enumerate(self.report["kinds"]):
            values = [r["kind"], r["in_drawing"], r["imported"], r["missing"] or "", r["skipped"] or ""]
            for j, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if j:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if j == 3 and r["missing"]:
                    item.setForeground(QColor("#b42318"))
                if j == 4:
                    item.setForeground(QColor("#9aa1ab"))
                table.setItem(i, j, item)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for j in range(1, 5):
            header.setSectionResizeMode(j, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _missing_tab(self) -> QWidget:
        page = QWidget()
        box = QHBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        missing = self.report["missing"]
        if not missing:
            empty = QLabel("✓")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #146c2e; font-size: 40px;")
            box.addWidget(empty)
            return page

        # 왼쪽: 이유별 묶음, 오른쪽: 그 이유의 설명 + 항목
        reasons = QListWidget()
        reasons.setFixedWidth(230)
        # 빠진 이유가 먼저, 선택해서 뺀 것은 뒤에 회색으로
        codes = sorted(self.report["reasons"], key=lambda c: (c in importreport.BY_CHOICE,
                                                              -self.report["reasons"][c]))
        for code in codes:
            name = importreport.reason_text(code)[0]
            item = QListWidgetItem(f"{name}   {self.report['reasons'][code]}")
            item.setData(Qt.ItemDataRole.UserRole, code)
            if code in importreport.BY_CHOICE:
                item.setForeground(QColor("#9aa1ab"))
            reasons.addItem(item)
        box.addWidget(reasons)

        right = QVBoxLayout()
        right.setSpacing(8)
        why = QFrame()
        why.setObjectName("why")
        inner = QVBoxLayout(why)
        inner.setContentsMargins(12, 10, 12, 10)
        self.why_text = QLabel("")
        self.why_text.setObjectName("whytext")
        self.why_text.setWordWrap(True)
        self.fix_text = QLabel("")
        self.fix_text.setObjectName("fixtext")
        self.fix_text.setWordWrap(True)
        inner.addWidget(self.why_text)
        inner.addWidget(self.fix_text)
        right.addWidget(why)
        self.items = QTableWidget(0, 3)
        self.items.setHorizontalHeaderLabels([tr("Kind"), tr("Layer"), "Handle"])
        self.items.verticalHeader().setVisible(False)
        self.items.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.items.setShowGrid(False)
        self.items.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        right.addWidget(self.items, 1)
        box.addLayout(right, 1)

        def show(row):
            if row < 0:
                return
            code = reasons.item(row).data(Qt.ItemDataRole.UserRole)
            _, why_line, fix_line = importreport.reason_text(code)
            self.why_text.setText(why_line)
            self.fix_text.setText("→ " + fix_line)
            rows = [m for m in missing if m["reason"] == code]
            self.items.setRowCount(len(rows))
            for i, m in enumerate(rows):
                for j, v in enumerate((m["kind"], m["layer"] or "0", m["handle"])):
                    self.items.setItem(i, j, QTableWidgetItem(str(v)))

        reasons.currentRowChanged.connect(show)
        reasons.setCurrentRow(0)
        return page

    def _save_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Save as HTML…"),
                                              f"{self.report['file']}-report.html", "HTML (*.html)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(importreport.to_html(self.report))

    def _send(self) -> None:
        if self.sender is not None:
            self.sender(self, self.report, self.excerpt)


def sender_or_none():
    """Pro 가 열려 있으면 보내기 함수, 아니면 None(무료판·잠김)."""
    try:
        from ..pro import reporting, unlocked
    except ImportError:
        return None
    return reporting.open_send_dialog if unlocked() else None


def show(report: dict | None, excerpt: str = "", parent=None) -> None:
    if not report:
        return
    ReportDialog(report, excerpt, parent, sender_or_none()).exec()
