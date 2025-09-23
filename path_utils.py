
from __future__ import annotations
import os, sys, urllib.parse
from pathlib import Path

def norm_ext(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return ext

def file_url_windows(path: str) -> str:
    # file:///C:/Users/...   or for UNC: file://///server/share/dir/file
    p = Path(path)
    s = str(p).replace("\\", "/")
    if s.startswith("//") or s.startswith("\\\\"):
        s = s.replace("\\\\", "//")
        return "file:" + urllib.parse.quote("////" + s.lstrip("/"))
    if len(s) > 1 and s[1] == ":":
        return "file:///" + urllib.parse.quote(s)
    return "file:///" + urllib.parse.quote(s)

def rel_from_base(abs_path: str, base: str) -> str:
    try:
        return str(Path(abs_path).relative_to(base))
    except Exception:
        # diferente unidad/disco: devolvemos el nombre base
        try:
            return Path(abs_path).name
        except Exception:
            return abs_path
