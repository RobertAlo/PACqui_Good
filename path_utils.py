# paths — utilidades comunes de rutas/archivos (Windows/macOS/Linux)
from __future__ import annotations

import os
import sys
import subprocess
import urllib.parse
from pathlib import Path
#PACqui_
def norm_ext(name: str) -> str:
    """Devuelve la extensión en minúsculas sin el punto (e.g., 'pdf')."""
    return Path(name).suffix.lower().lstrip(".")

def file_url_windows(path: str) -> str:
    """
    Construye una URL file:// válida para Windows (incluido UNC).
    Ej.: file:///C:/Users/...  o  file://///servidor/compartida/dir/file.txt
    """
    p = Path(path)
    s = str(p).replace("\\", "/")
    if s.startswith("//") or s.startswith("\\\\"):
        s = s.replace("\\\\", "//")
        return "file:" + urllib.parse.quote("////" + s.lstrip("/"))
    if len(s) > 1 and s[1] == ":":
        return "file:///" + urllib.parse.quote(s)
    return "file:///" + urllib.parse.quote(s)

def rel_from_base(abs_path: str, base: str) -> str:
    """Ruta relativa a base; si está en otra unidad, devuelve el nombre de archivo."""
    try:
        return str(Path(abs_path).relative_to(base))
    except Exception:
        try:
            return Path(abs_path).name
        except Exception:
            return abs_path

def open_in_explorer(p: str | Path) -> None:
    """
    Abre el archivo/carpeta en el explorador del SO.
    - Windows: si es archivo, lo selecciona en el Explorer.
    - macOS:   open / open -R
    - Linux:   xdg-open
    """
    p = Path(p)
    try:
        if os.name == "nt":  # Windows
            if p.is_file():
                subprocess.run(["explorer", "/select,", str(p)], check=False)
            else:
                os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":  # macOS
            if p.is_file():
                subprocess.run(["open", "-R", str(p)], check=False)
            else:
                subprocess.run(["open", str(p)], check=False)
        else:  # Linux/Unix
            target = p if p.is_dir() else p.parent
            subprocess.run(["xdg-open", str(target)], check=False)
    except Exception:
        # Último recurso para no romper la UI en Windows
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception:
            pass

__all__ = ["norm_ext", "file_url_windows", "rel_from_base", "open_in_explorer"]
