# massive_indexer.py
from __future__ import annotations
import os, csv, time
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

try:
    import xlsxwriter  # pip install xlsxwriter
except Exception:
    xlsxwriter = None  # fallback a CSV

from path_utils import norm_ext, file_url_windows as _fileurl_windows, rel_from_base  # :contentReference[oaicite:0]{index=0}

Row = Tuple[str, str, int, float, str, str, str]  # (nombre, ext, size, mtime, carpeta, ruta_abs, localizacion)

def _iter_files(base: Path) -> Iterable[Path]:
    # Iterador no recursivo con scandir (rápido y memoria constante)
    stack = [base]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        yield Path(e.path)
        except PermissionError:
            continue

def _count_files(base: Path) -> int:
    n = 0
    for _ in _iter_files(base):
        n += 1
    return n

def _file_info(p: Path, base: Path) -> Row:
    try:
        st = p.stat()
        size = int(st.st_size)
        mtime = float(st.st_mtime)
    except Exception:
        size, mtime = 0, 0.0
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
                         progress_cb: Optional[Callable[[str, object], None]] = None) -> str:
    """
    Recorre la carpeta base y exporta un índice masivo en XLSX (si hay xlsxwriter)
    o CSV en su defecto. Devuelve la ruta del fichero generado.

    progress_cb(event, value):
      - ("total", int)   -> número total de ficheros (estimado exacto)
      - ("inc",   int)   -> incrementar progreso (típicamente 1)
      - ("status", str)  -> texto de estado opcional
    """
    base = Path(base_path)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Carpeta base no válida: {base_path!r}")

    out = Path(out_path) if out_path else _default_out_path(base, prefer_xlsx)
    headers = ["NOMBRE", "EXT", "TAMAÑO", "MODIFICADO", "CARPETA", "RUTA", "LOCALIZACION"]

    # 1) contamos primero para tener barra determinista
    t0 = time.time()
    total = _count_files(base)
    if progress_cb:
        progress_cb("total", total)
        progress_cb("status", f"Encontrados {total:,} ficheros. Preparando salida…")

    # 2) escritor segun formato
    rows_iter = _iter_files(base)

    def _emit_tick(n_done: int):
        if progress_cb:
            progress_cb("inc", 1)
            if n_done % 2000 == 0:
                elapsed = time.time() - t0
                speed = (n_done / elapsed) if elapsed > 0 else 0.0
                progress_cb("status", f"{n_done:,}/{total:,} · {speed:,.0f} filas/seg")

    if xlsxwriter and prefer_xlsx and str(out).lower().endswith(".xlsx"):
        wb = xlsxwriter.Workbook(str(out), {
            "constant_memory": True,
            "strings_to_numbers": False,
            "strings_to_formulas": False,
        })
        ws = wb.add_worksheet("Índice")
        bold = wb.add_format({"bold": True})
        date_fmt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})

        for col, h in enumerate(headers):
            ws.write(0, col, h, bold)
        ws.freeze_panes(1, 0)

        row_idx = 1
        for p in rows_iter:
            nombre, ext, size, mtime, carpeta, ruta_abs, localizacion = _file_info(p, base)
            ws.write(row_idx, 0, nombre)
            ws.write(row_idx, 1, ext)
            ws.write_number(row_idx, 2, size)
            if mtime > 0:
                from datetime import datetime
                ws.write(row_idx, 3, datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"), date_fmt)
            else:
                ws.write(row_idx, 3, "")
            ws.write(row_idx, 4, carpeta)
            try:
                ws.write_url(row_idx, 5, _fileurl_windows(ruta_abs), string=ruta_abs)
            except Exception:
                ws.write(row_idx, 5, ruta_abs)
            ws.write(row_idx, 6, localizacion)

            row_idx += 1
            _emit_tick(row_idx - 1)

        # Anchos razonables
        ws.set_column(0, 0, 46)
        ws.set_column(1, 1, 8)
        ws.set_column(2, 2, 12)
        ws.set_column(3, 3, 18)
        ws.set_column(4, 4, 48)
        ws.set_column(5, 5, 72)
        ws.set_column(6, 6, 40)

        wb.close()
        if progress_cb:
            progress_cb("status", f"Excel guardado en {out}")
        return str(out)

    # CSV (fallback ultra-compatible)
    if not str(out).lower().endswith(".csv"):
        out = out.with_suffix(".csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        n = 0
        for p in rows_iter:
            nombre, ext, size, mtime, carpeta, ruta_abs, localizacion = _file_info(p, base)
            mod = ""
            if mtime > 0:
                from datetime import datetime
                mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            w.writerow([nombre, ext, size, mod, carpeta, ruta_abs, localizacion])
            n += 1
            _emit_tick(n)
    if progress_cb:
        progress_cb("status", f"CSV guardado en {out}")
    return str(out)
