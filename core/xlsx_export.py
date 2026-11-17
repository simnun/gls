from __future__ import annotations

import math
from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _col_name(idx: int) -> str:
    name = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


# Stili di cella: 0 normale, 1 intestazione, 2 data e ora, 3 data, 4 importo
# con due decimali. Con i colori di riga gli stessi cinque si ripetono per
# ogni colore: lo stile di una cella e' base + 5 * numero del colore.
STILE_DATA_ORA = 2
STILE_DATA = 3
STILE_IMPORTO = 4
_BASI = 5
_ORIGINE_EXCEL = datetime(1899, 12, 30)


def _testo_cella(valore: object) -> str:
    """Come appare il valore, per stimare la larghezza della colonna."""
    if isinstance(valore, datetime):
        return "00/00/0000 00:00"
    if isinstance(valore, date):
        return "00/00/0000"
    return "" if valore is None else str(valore)


def _cella(ref: str, valore: object, intestazione: bool, colore: int = 0) -> str:
    """Numeri e date restano numeri e date, cosi' in Excel si ordinano e si sommano."""
    def s(base: int) -> str:
        indice = base + _BASI * colore
        return f' s="{indice}"' if indice else ""

    if not intestazione and not isinstance(valore, bool):
        if isinstance(valore, (int, float)) and math.isfinite(valore):
            base = STILE_IMPORTO if isinstance(valore, float) and not valore.is_integer() else 0
            return f'<c r="{ref}"{s(base)}><v>{valore}</v></c>'
        if isinstance(valore, datetime):
            seriale = (valore.replace(tzinfo=None) - _ORIGINE_EXCEL).total_seconds() / 86400
            return f'<c r="{ref}"{s(STILE_DATA_ORA)}><v>{seriale:.10f}</v></c>'
        if isinstance(valore, date):
            seriale = (datetime(valore.year, valore.month, valore.day) - _ORIGINE_EXCEL).days
            return f'<c r="{ref}"{s(STILE_DATA)}><v>{seriale}</v></c>'
    if isinstance(valore, bool):
        valore = "Si" if valore else "No"
    testo = escape("" if valore is None else str(valore)).replace("\n", "&#10;")
    return f'<c r="{ref}" t="inlineStr"{s(1 if intestazione else 0)}><is><t xml:space="preserve">{testo}</t></is></c>'


def build_xlsx(headers: list[str], rows: list[list[object]], sheet_name: str = "Incongruenze",
               row_colors: list[str | None] | None = None) -> bytes:
    """Crea un XLSX semplice usando solo la standard library.

    Le celle di testo restano testo; numeri, date e date con ora vengono
    scritti come tali, con il formato italiano. row_colors da' a ogni riga di
    dati un colore di sfondo ARGB (per esempio "FFC6EFCE"), oppure None.
    """
    colori = []
    for c in row_colors or []:
        if c and c not in colori:
            colori.append(c)
    numero_colore = {c: i + 1 for i, c in enumerate(colori)}
    all_rows: list[list[object]] = [list(headers)] + [list(row) for row in rows]
    widths = []
    for c in range(len(headers)):
        max_len = max((len(_testo_cella(r[c])) if c < len(r) else 0 for r in all_rows), default=8)
        widths.append(min(max(max_len + 2, 10), 55))

    row_xml = []
    for r_idx, row in enumerate(all_rows, start=1):
        colore = 0
        if r_idx > 1 and row_colors and r_idx - 2 < len(row_colors):
            colore = numero_colore.get(row_colors[r_idx - 2], 0)
        cells = [_cella(f"{_col_name(c_idx)}{r_idx}", value, r_idx == 1, colore)
                 for c_idx, value in enumerate(row, start=1)]
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths, start=1)
    )
    last_col = _col_name(len(headers))
    auto_filter = f'A1:{last_col}{max(1, len(all_rows))}'
    safe_sheet = escape(sheet_name[:31] or "Foglio1")
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>{cols}</cols>
  <sheetData>{''.join(row_xml)}</sheetData>
  <autoFilter ref="{auto_filter}"/>
</worksheet>'''
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="{safe_sheet}" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    # Per ogni colore: un riempimento e i cinque stili di base su quello sfondo.
    riempimenti = "".join(
        f'<fill><patternFill patternType="solid"><fgColor rgb="{c}"/><bgColor indexed="64"/></patternFill></fill>'
        for c in colori)
    basi = [(0, 0), (0, 1), (164, 0), (165, 0), (4, 0)]   # (formato numero, carattere)
    stili_colore = "".join(
        f'<xf numFmtId="{fmt}" fontId="{font}" fillId="{k + 2}" borderId="0" xfId="0" applyFill="1"'
        f'{" applyNumberFormat=" + chr(34) + "1" + chr(34) if fmt else ""}{" applyFont=" + chr(34) + "1" + chr(34) if font else ""}/>'
        for k in range(len(colori)) for fmt, font in basi)
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="2"><numFmt numFmtId="164" formatCode="dd/mm/yyyy hh:mm"/><numFmt numFmtId="165" formatCode="dd/mm/yyyy"/></numFmts>
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="{2 + len(colori)}"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>{riempimenti}</fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="{_BASI * (1 + len(colori))}"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>{stili_colore}</cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    wb_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()
