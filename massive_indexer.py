from __future__ import annotations
import os, csv, time
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple
#PACqui
try:
    import xlsxwriter  # pip install xlsxwriter
except Exception:
    xlsxwriter = None  # fallback a CSV

from path_utils import norm_ext, file_url_windows as _fileurl_windows, rel_from_base

Row = Tuple[str, str, int, float, str, str, str]  # (nombre, ext, size, mtime, carpeta, ruta_abs, localizacion)

def _iter_files(base: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(base):
        for fn in files:
            try:
                yield Path(root) / fn
            except Exception:
                continue

def _file_info(p: Path, base: Path) -> Row:
    try:
        stat = p.stat()
        size = int(getattr(stat, "st_size", 0) or 0)
        mtime = float(getattr(stat, "st_mtime", 0.0) or 0.0)
    except Exception:
        size = 0
        mtime = 0.0
    nombre = p.name
    ext = norm_ext(nombre)
    carpeta = str(p.parent)
    ruta_abs = str(p)
    localizacion = f"<BASE>\\{rel_from_base(ruta_abs, str(base))}"
    return (nombre, ext, size, mtime, carpeta, ruta_abs, localizacion)

def _default_out_path(base: Path, prefer_xlsx: bool) -> Path:
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"Indice_{base.name}_{ts}" + (".xlsx" if (prefer_xlsx and xlsxwriter) else ".csv")
    return Path.home() / "Desktop" / name

def export_massive_index(base_path: str | os.PathLike[str],
                         out_path: Optional[str] = None,
                         prefer_xlsx: bool = True,
                         meta_provider: Optional[Callable[[str], tuple[str, str]]] = None,
                         progress_cb: Optional[Callable[[str, str], None]] = None,
                         tick_every: int = 50) -> str:
    """
    Exporta un índice masivo a Excel (si hay xlsxwriter y prefer_xlsx=True) o CSV.
    Añade SIEMPRE dos columnas nuevas al final:
      - PALABRAS CLAVE
      - OBSERVACIONES
    Si se proporciona meta_provider(abs_path) -> (palabras_clave, observaciones),
    las rellenará; en caso contrario, quedarán en blanco.

    tick_every: cada cuántos ficheros se emite el progreso (por defecto 50).
    """
    base = Path(base_path).resolve()
    out = Path(out_path).resolve() if out_path else _default_out_path(base, prefer_xlsx)
    rows_iter = _iter_files(base)

    tick_every = max(1, int(tick_every or 50))

    def _emit_tick(n: int):
        if progress_cb and (n % tick_every == 0):
            progress_cb("progress", str(n))

    headers = ["NOMBRE","EXT","TAMANO","MODIFICADO","CARPETA","RUTA","LOCALIZACION","PALABRAS CLAVE","OBSERVACIONES"]

    if prefer_xlsx and xlsxwriter:
        wb = xlsxwriter.Workbook(str(out), {
            "constant_memory": True,
            "strings_to_numbers": False,
            "strings_to_formulas": False,
        })
        bold = wb.add_format({"bold": True})
        date_fmt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})
        ws = wb.add_worksheet("Índice")
        for col, h in enumerate(headers):
            ws.write(0, col, h, bold)
        ws.freeze_panes(1, 0)
        ws.set_column(0, 0, 46)
        ws.set_column(1, 1, 8)
        ws.set_column(2, 2, 12)
        ws.set_column(3, 3, 18)
        ws.set_column(4, 4, 48)
        ws.set_column(5, 5, 72)
        ws.set_column(6, 6, 40)
        ws.set_column(7, 7, 36)
        ws.set_column(8, 8, 42)

        from datetime import datetime
        row_idx = 1
        for p in rows_iter:
            nombre, ext, size, mtime, carpeta, ruta_abs, localizacion = _file_info(p, base)
            mod = ""
            if mtime > 0:
                mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

            if meta_provider:
                try:
                    palabras, obs = meta_provider(ruta_abs) or ("","")
                except Exception:
                    palabras, obs = "",""
            else:
                palabras, obs = "",""

            ws.write(row_idx, 0, nombre)
            ws.write(row_idx, 1, ext)
            ws.write_number(row_idx, 2, size)
            ws.write(row_idx, 3, mod, date_fmt if mod else None)
            ws.write(row_idx, 4, carpeta)
            try:
                ws.write_url(row_idx, 5, _fileurl_windows(ruta_abs), string=ruta_abs)
            except Exception:
                ws.write(row_idx, 5, ruta_abs)
            ws.write(row_idx, 6, localizacion)
            ws.write(row_idx, 7, palabras)
            ws.write(row_idx, 8, obs)
            row_idx += 1
            _emit_tick(row_idx - 1)

        try:
            ws.autofilter(0, 0, max(1, row_idx-1), len(headers)-1)
        except Exception:
            pass
        wb.close()
        if progress_cb:
            progress_cb("status", f"Excel guardado en {out}")
        return str(out)

    out = Path(out).with_suffix(".csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        n = 0
        from datetime import datetime
        for p in rows_iter:
            nombre, ext, size, mtime, carpeta, ruta_abs, localizacion = _file_info(p, base)
            mod = ""
            if mtime > 0:
                mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            if meta_provider:
                try:
                    palabras, obs = meta_provider(ruta_abs) or ("","")
                except Exception:
                    palabras, obs = "",""
            else:
                palabras, obs = "",""
            w.writerow([nombre, ext, size, mod, carpeta, ruta_abs, localizacion, palabras, obs])
            n += 1
            _emit_tick(n)
    if progress_cb:
        progress_cb("status", f"CSV guardado en {out}")
    return str(out)
