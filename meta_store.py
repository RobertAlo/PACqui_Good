
# meta_store.py — SQLite helper for PACqui keywords & notes
from __future__ import annotations
import os, sqlite3, threading
from typing import Iterable, List, Optional
from pathlib import Path

DEFAULT_DB = "index_cache.sqlite"

def _norm(p: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(p))
    except Exception:
        return p

class MetaStore:
    """
    Pequeña capa de acceso a SQLite para:
      - doc_keywords(fullpath, keyword, source, created_at)
      - doc_notes(fullpath, note, updated_at)

    * Concurrency-friendly*: journal_mode=WAL, busy_timeout.
    * Rutas normalizadas para comparaciones case-insensitive (Windows friendly).
    """
    def __init__(self, db_path: Optional[str | os.PathLike[str]] = None):
        self.db_path = Path(db_path) if db_path else (Path(__file__).resolve().parent / DEFAULT_DB)
        self._lock = threading.RLock()
        self._ensure_schema()

    # ---------- low level ----------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA busy_timeout=3000")
        except Exception:
            pass
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            c = conn.cursor()
            # Palabras clave
            c.execute("""
                CREATE TABLE IF NOT EXISTS doc_keywords (
                    fullpath   TEXT NOT NULL,
                    keyword    TEXT NOT NULL,
                    source     TEXT DEFAULT '',
                    created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now','localtime'))
                )
            """)
            # índices + unicidad (case-insensitive) vía índice único por expresión
            c.execute("CREATE INDEX IF NOT EXISTS idx_doc_keywords_fp ON doc_keywords(fullpath)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_doc_keywords_kw ON doc_keywords(keyword)")
            # UNIQUE por (lower(fullpath), lower(keyword))
            c.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_keywords_u_expr
                ON doc_keywords( lower(fullpath), lower(keyword) )
            """)

            # Observaciones (nota libre por documento)
            c.execute("""
                CREATE TABLE IF NOT EXISTS doc_notes (
                    fullpath   TEXT PRIMARY KEY,
                    note       TEXT NOT NULL DEFAULT '',
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now','localtime'))
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_doc_notes_fp_expr ON doc_notes( lower(fullpath) )")
            conn.commit()

    # ---------- keywords ----------
    def add_keywords(self, fullpath: str, keywords: Iterable[str], source: str = "manual", replace: bool = False) -> int:
        """Inserta keywords. Si replace=True, borra antes todas las previas del doc (case-insensitive)."""
        kws = [k.strip() for k in keywords if k and k.strip()]
        if not kws:
            return 0
        with self._lock, self._connect() as conn:
            kpath = _norm(fullpath)
            if replace:
                conn.execute("DELETE FROM doc_keywords WHERE lower(fullpath) = lower(?)", (kpath,))
            n = 0
            for kw in kws:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO doc_keywords(fullpath, keyword, source) VALUES (?, ?, ?)",
                        (kpath, kw, source or "")
                    )
                    n += conn.total_changes and 1 or 0
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
            return n

    def get_keywords(self, fullpath: str) -> List[str]:
        with self._lock, self._connect() as conn:
            kpath = _norm(fullpath)
            rows = conn.execute(
                "SELECT keyword FROM doc_keywords WHERE lower(fullpath)=lower(?) ORDER BY keyword COLLATE NOCASE",
                (kpath,)
            ).fetchall()
            return [r[0] for r in rows]

    def clear_keywords(self, fullpath: str) -> int:
        with self._lock, self._connect() as conn:
            kpath = _norm(fullpath)
            cur = conn.execute("DELETE FROM doc_keywords WHERE lower(fullpath)=lower(?)", (kpath,))
            conn.commit()
            return cur.rowcount or 0

    # ---------- notes ----------
    def set_note(self, fullpath: str, note: str) -> None:
        """Upsert case-insensitive por ruta."""
        with self._lock, self._connect() as conn:
            kpath = _norm(fullpath)
            cur = conn.execute("UPDATE doc_notes SET note=?, updated_at=CURRENT_TIMESTAMP WHERE lower(fullpath)=lower(?)",
                               (note or "", kpath))
            if cur.rowcount == 0:
                conn.execute("INSERT INTO doc_notes(fullpath, note) VALUES (?, ?)", (kpath, note or ""))
            conn.commit()

    def get_note(self, fullpath: str) -> str:
        with self._lock, self._connect() as conn:
            kpath = _norm(fullpath)
            row = conn.execute("SELECT note FROM doc_notes WHERE lower(fullpath)=lower(?)", (kpath,)).fetchone()
            return row[0] if row else ""

    def delete_note(self, fullpath: str) -> bool:
        with self._lock, self._connect() as conn:
            kpath = _norm(fullpath)
            cur = conn.execute("DELETE FROM doc_notes WHERE lower(fullpath)=lower(?)", (kpath,))
            conn.commit()
            return (cur.rowcount or 0) > 0

    # ---------- util ----------
    def search_by_keyword(self, keyword_substr: str, limit: int = 1000) -> list[tuple[str, str]]:
        """Devuelve [(fullpath, keyword)] que contengan keyword_substr (case-insensitive)."""
        q = f"%{keyword_substr.strip()}%"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT fullpath, keyword FROM doc_keywords WHERE lower(keyword) LIKE lower(?) LIMIT ?",
                (q, int(limit or 1000))
            ).fetchall()
            return [(r[0], r[1]) for r in rows]

if __name__ == "__main__":
    # Pequeño CLI de apoyo:
    #   python meta_store.py add-kw "C:\ruta\doc.pdf" "kw1; kw2; kw3"
    #   python meta_store.py set-note "C:\ruta\doc.pdf" "texto de observación"
    #   python meta_store.py get-kw "C:\ruta\doc.pdf"
    #   python meta_store.py get-note "C:\ruta\doc.pdf"
    import sys
    if len(sys.argv) < 3:
        print("Uso: add-kw|set-note|get-kw|get-note <fullpath> [valor]")
        raise SystemExit(2)
    cmd = sys.argv[1].lower()
    fullpath = sys.argv[2]
    store = MetaStore()
    if cmd == "add-kw":
        vals = (sys.argv[3] if len(sys.argv) > 3 else "")
        kws = [x.strip() for x in vals.replace(",", ";").split(";") if x.strip()]
        n = store.add_keywords(fullpath, kws, source="manual")
        print(f"Añadidas {n} keywords.")
    elif cmd == "set-note":
        note = sys.argv[3] if len(sys.argv) > 3 else ""
        store.set_note(fullpath, note)
        print("Nota guardada.")
    elif cmd == "get-kw":
        print("; ".join(store.get_keywords(fullpath)))
    elif cmd == "get-note":
        print(store.get_note(fullpath))
    else:
        print("Comando no reconocido.")
