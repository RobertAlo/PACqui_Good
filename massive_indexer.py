# -*- coding: utf-8 -*-
"""
massive_indexer.py
Exportador masivo en streaming para carpetas hipermasivas (90 GB / 250k+ ficheros).
- Memoria constante (stream): usa XlsxWriter con constant_memory o CSV.
- Columnas: NOMBRE, EXT, TAMAÑO, MODIFICADO, CARPETA, RUTA, LOCALIZACION
- Hipervínculo en la columna RUTA (si XLSX).
"""

from __future__ import annotations
import os
import sys
import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

try:
    import xlsxwriter  # type: ignore
except Exception:
    xlsxwriter = None  # type: ignore


def _iter_files(base: Path) -> Iterator[Path]:
    """Itera todos los ficheros bajo 'base' de forma robusta y orden estable."""
    # os.walk es más rápido que Path.rglob en grandes jerarquías
    for root, _dirs, files in os.walk(str(base)):
        # ordenar para resultados deterministas sin comer RAM
        files.sort()
        for fname in files:
            yield Path(root) / fname


def _file_info(p: Path, base: Path) -> tuple[str, str, int, float, str, str, str]:
    """
    Devuelve: (nombre, ext, size, mtime_ts, carpeta, ruta_abs, localizacion)
    - localizacion: "<BASE>\\sub\\sub" relativa.
    """
    try:
        st = p.stat()
        size = int(st.st_size)
        mtime = float(st.st_mtime)
    except Exception:
        size = 0
        mtime = 0.0
    nombre = p.name
    ext = p.suffix.lower().lstrip(".")
    carpeta = str(p.parent)
    ruta_abs = str(p)
    base_name = base.name
    try:
        rel_parent = p.parent.relative_to(base)
        rel_str = "." if str(rel_parent) == "." else str(rel_parent).replace("/", "\\")
    except Exception:
        rel_str = ""
    localizacion = base_name if rel_str in ("", ".") else f"{base_name}\\{rel_str}"
    return nombre, ext, size, mtime, carpeta, ruta_abs, localizacion


def _fileurl_windows(path: str) -> str:
    """
    Convierte ruta de Windows a file:/// con barras forward, escapando espacios.
    """
    # Reemplazar backslashes por forward-slashes
    p = path.replace("\\", "/")
    # Añadir prefijo file:///
    if not p.startswith("file:///"):
        p = "file:///" + p
    return p


def _default_out_path(base: Path, prefer_xlsx: bool) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"Indice_{base.name}_{ts}." + ("xlsx" if prefer_xlsx and xlsxwriter else "csv")
    try:
        desktop = Path(os.path.join(os.path.expanduser("~"), "Desktop"))
        if desktop.exists():
            return desktop / name
    except Exception:
        pass
    return Path.cwd() / name


def export_massive_index(base_path: str | os.PathLike[str],
                         out_path: Optional[str] = None,
                         prefer_xlsx: bool = True) -> str:
    """
    Recorre la carpeta base y exporta un índice masivo en XLSX (si hay xlsxwriter)
    o en CSV en su defecto. Devuelve la ruta del fichero generado.
    """
    base = Path(base_path)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Carpeta base no válida: {base_path!r}")

    out = Path(out_path) if out_path else _default_out_path(base, prefer_xlsx)

    # Cabeceras
    headers = ["NOMBRE", "EXT", "TAMAÑO", "MODIFICADO", "CARPETA", "RUTA", "LOCALIZACION"]

    if xlsxwriter and prefer_xlsx and str(out).lower().endswith(".xlsx"):
        # ---------- XLSX (streaming, memoria constante) ----------
        wb = xlsxwriter.Workbook(str(out), {
            "constant_memory": True,
            "strings_to_numbers": False,
            "strings_to_formulas": False,
        })
        ws = wb.add_worksheet("Índice")
        # Formatos
        bold = wb.add_format({"bold": True})
        date_fmt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})
        # Escribir cabecera
        for col, h in enumerate(headers):
            ws.write(0, col, h, bold)
        ws.freeze_panes(1, 0)

        row_idx = 1
        t0 = time.time()
        pushed = 0
        for p in _iter_files(base):
            nombre, ext, size, mtime, carpeta, ruta_abs, localizacion = _file_info(p, base)
            ws.write(row_idx, 0, nombre)
            ws.write(row_idx, 1, ext)
            ws.write_number(row_idx, 2, size)
            # Fecha como datetime (si disponible) o texto vacío
            if mtime > 0:
                # Excel almacena fechas como serial numbers; escribimos texto con formato
                dt_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                ws.write(row_idx, 3, dt_str, date_fmt)
            else:
                ws.write(row_idx, 3, "")
            ws.write(row_idx, 4, carpeta)

            # Hipervínculo (file:///...); el texto mostrado será la ruta absoluta
            try:
                ws.write_url(row_idx, 5, _fileurl_windows(ruta_abs), string=ruta_abs)
            except Exception:
                ws.write(row_idx, 5, ruta_abs)

            # LOCALIZACION: "<BASE>\\rel\path"
            ws.write(row_idx, 6, localizacion)

            row_idx += 1
            pushed += 1
            if pushed % 2000 == 0:
                # pequeño respiro al GC y al sistema de archivos
                wb.flush_row_data()

        # Anchos de columna razonables
        ws.set_column(0, 0, 46)   # NOMBRE
        ws.set_column(1, 1, 8)    # EXT
        ws.set_column(2, 2, 12)   # TAMAÑO
        ws.set_column(3, 3, 18)   # MODIFICADO
        ws.set_column(4, 4, 48)   # CARPETA
        ws.set_column(5, 5, 72)   # RUTA
        ws.set_column(6, 6, 40)   # LOCALIZACION

        wb.close()
        return str(out)

    # ---------- CSV (ultra compatible) ----------
    if not str(out).lower().endswith(".csv"):
        out = out.with_suffix(".csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        for p in _iter_files(base):
            nombre, ext, size, mtime, carpeta, ruta_abs, localizacion = _file_info(p, base)
            mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else ""
            w.writerow([nombre, ext, size, mod, carpeta, ruta_abs, localizacion])
    return str(out)


# CLI rápido:  python -m massive_indexer "C:\Base" --out "D:\indice.xlsx"
def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Exportador masivo de índice de ficheros (XLSX/CSV).")
    ap.add_argument("base", help="Carpeta base a indexar")
    ap.add_argument("--out", help="Ruta de salida (.xlsx o .csv). Si no se indica, Desktop.")
    ap.add_argument("--csv", action="store_true", help="Forzar salida CSV aunque exista xlsxwriter.")
    args = ap.parse_args()

    path = export_massive_index(args.base, out_path=args.out, prefer_xlsx=not args.csv)
    print(f"OK → {path}")


if __name__ == "__main__":
    _cli()
