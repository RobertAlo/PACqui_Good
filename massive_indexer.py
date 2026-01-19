from __future__ import annotations
import os, csv, time
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple
#PACqui_1.3.0
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

# ============================
# NEW: export_massive_tree_index
# ============================

from typing import Sequence

DOC_EXTS_DEFAULT = {
    ".pdf",
    ".doc", ".docx",
    ".xls", ".xlsx", ".xlsm",
    ".ppt", ".pptx",
    ".odt", ".ods", ".odp",
    ".rtf", ".txt", ".md", ".csv",
    ".sql",
}

def export_massive_tree_index(
    base_path: str | os.PathLike[str],
    out_path: Optional[str] = None,
    prefer_xlsx: bool = True,
    meta_provider: Optional[Callable[[str], tuple[str, str]]] = None,
    progress_cb: Optional[Callable[[str, str], None]] = None,
    include_dirs: Optional[Sequence[str]] = None,
    exclude_dirs: Optional[Sequence[str]] = None,
    docs_only: bool = True,
    doc_exts: Optional[set[str]] = None,
    pre_total: Optional[int] = None,
    pre_max_depth: Optional[int] = None,
    tick_every: int = 50,
) -> str:
    """
    Exportación MASIVA en vista jerárquica:
      CARPETA BASE | CARPETA | SUBCARPETA 1..N | FICHERO | LINK | RUTA | LOCALIZACION | PALABRAS CLAVE | OBSERVACIONES

    - include_dirs: lista de carpetas (ABS o relativas a base) a incluir.
    - exclude_dirs: lista de carpetas (ABS o relativas a base) a excluir (podan el os.walk).
    - docs_only: si True, filtra a extensiones documentales (PDF/Office/ODF/SQL/etc.).
    - pre_total/pre_max_depth: si el caller ya ha hecho pre-scan, pásalos para evitar doble recorrido.
    """

    base = Path(base_path).resolve()
    out = Path(out_path).resolve() if out_path else _default_out_path(base, prefer_xlsx)

    # extensiones documentales
    doc_exts = set(x.lower() for x in (doc_exts or DOC_EXTS_DEFAULT))

    def _norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    def _as_abs(p: str) -> str:
        p = str(p or "").strip()
        if not p:
            return ""
        pp = Path(p)
        if not pp.is_absolute():
            pp = (base / p).resolve()
        return str(pp)

    # roots a caminar
    roots: list[Path] = []
    if include_dirs:
        for d in include_dirs:
            a = _as_abs(d)
            if a:
                roots.append(Path(a))
    if not roots:
        roots = [base]

    # excludes normalizados (para poda por prefijo)
    ex_norm: list[str] = []
    if exclude_dirs:
        for d in exclude_dirs:
            a = _as_abs(d)
            if a:
                ex_norm.append(_norm(a))

    def _is_excluded_dir(path_str: str) -> bool:
        k = _norm(path_str)
        for ex in ex_norm:
            if k == ex:
                return True
            if k.startswith(ex + os.sep):
                return True
        return False

    def _iter_paths():
        for r in roots:
            if not r.exists():
                continue
            for root, dirs, files in os.walk(r):
                # si el root está excluido, poda entera
                if _is_excluded_dir(root):
                    dirs[:] = []
                    continue

                # poda hijos excluidos (evita bajar)
                kept = []
                for d in dirs:
                    child = os.path.join(root, d)
                    if _is_excluded_dir(child):
                        continue
                    kept.append(d)
                dirs[:] = kept

                for fn in files:
                    try:
                        yield Path(root) / fn
                    except Exception:
                        continue

    def _emit_tick(n: int):
        if progress_cb and (n % tick_every == 0):
            progress_cb("progress", str(n))

    # Pre-scan (total + max depth) para crear headers jerárquicos deterministas
    if pre_total is None or pre_max_depth is None:
        total = 0
        max_depth = 0
        for p in _iter_paths():
            try:
                # filtro docs_only también en el pre-scan para que el total sea real
                ext = (p.suffix or "").lower()
                if docs_only and ext and (ext not in doc_exts):
                    continue
                total += 1
                try:
                    rel_parent = p.parent.resolve().relative_to(base)
                    depth = len(rel_parent.parts)
                    if depth > max_depth:
                        max_depth = depth
                except Exception:
                    pass
            except Exception:
                continue
        pre_total = total
        pre_max_depth = max_depth

    total = int(pre_total or 0)
    max_depth = int(pre_max_depth or 0)

    # Headers: CARPETA = 1er nivel, SUBCARPETA i = niveles 2..N
    sub_cols = max(0, max_depth - 1)
    headers = (
        ["CARPETA BASE", "CARPETA"]
        + [f"SUBCARPETA {i}" for i in range(1, sub_cols + 1)]
        + ["FICHERO", "LINK", "RUTA", "LOCALIZACION", "PALABRAS CLAVE", "OBSERVACIONES"]
    )

    if not (prefer_xlsx and xlsxwriter):
        # fallback CSV (sin hipervínculo real; se guarda URL en LINK)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(headers)
            n = 0
            for p in _iter_paths():
                ext = (p.suffix or "").lower()
                if docs_only and ext and (ext not in doc_exts):
                    continue

                ruta_abs = str(p)
                loc = f"<BASE>\\{rel_from_base(ruta_abs, str(base))}"

                # carpeta + subcarpetas
                try:
                    rel_parent = p.parent.resolve().relative_to(base)
                    parts = list(rel_parent.parts)
                except Exception:
                    parts = []
                carpeta = parts[0] if len(parts) >= 1 else ""
                subs = parts[1:] if len(parts) > 1 else []
                subs = subs + [""] * (sub_cols - len(subs))

                # meta
                if meta_provider:
                    try:
                        palabras, obs = meta_provider(ruta_abs) or ("", "")
                    except Exception:
                        palabras, obs = "", ""
                else:
                    palabras, obs = "", ""

                link = _fileurl_windows(ruta_abs)
                row = [base.name, carpeta] + subs + [p.name, link, ruta_abs, loc, palabras, obs]
                w.writerow(row)

                n += 1
                _emit_tick(n)

        return str(out)

    # XLSXwriter (hipervínculos reales + constant_memory)
    wb = xlsxwriter.Workbook(str(out), {
        "constant_memory": True,
        "strings_to_numbers": False,
        "strings_to_formulas": False,
    })
    bold = wb.add_format({"bold": True})
    ws = wb.add_worksheet("Índice")
    for col, h in enumerate(headers):
        ws.write(0, col, h, bold)
    ws.freeze_panes(1, 0)

    row_idx = 1
    n = 0
    for p in _iter_paths():
        ext = (p.suffix or "").lower()
        if docs_only and ext and (ext not in doc_exts):
            continue

        ruta_abs = str(p)
        loc = f"<BASE>\\{rel_from_base(ruta_abs, str(base))}"

        try:
            rel_parent = p.parent.resolve().relative_to(base)
            parts = list(rel_parent.parts)
        except Exception:
            parts = []

        carpeta = parts[0] if len(parts) >= 1 else ""
        subs = parts[1:] if len(parts) > 1 else []
        subs = subs + [""] * (sub_cols - len(subs))

        if meta_provider:
            try:
                palabras, obs = meta_provider(ruta_abs) or ("", "")
            except Exception:
                palabras, obs = "", ""
        else:
            palabras, obs = "", ""

        # Escribimos: base, carpeta/subs, fichero, link(url), ruta, localización, meta
        c = 0
        ws.write(row_idx, c, base.name); c += 1
        ws.write(row_idx, c, carpeta); c += 1
        for s in subs:
            ws.write(row_idx, c, s); c += 1

        ws.write(row_idx, c, p.name); c += 1

        url = _fileurl_windows(ruta_abs)
        # LINK: hiperlink con texto limpio (evita “caracteres raros”)
        ws.write_url(row_idx, c, url, string="Abrir"); c += 1

        ws.write(row_idx, c, ruta_abs); c += 1
        ws.write(row_idx, c, loc); c += 1
        ws.write(row_idx, c, palabras); c += 1
        ws.write(row_idx, c, obs); c += 1

        row_idx += 1
        n += 1
        _emit_tick(n)

    wb.close()
    return str(out)
