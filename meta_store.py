
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

    # --- NUEVO: importar índice Excel/CSV ---

    def import_index_sheet(self, sheet_path: str, replace_mode: str = "merge",
                           progress=None, progress_every: int = 200) -> dict:
        """
        Importa un índice (XLSX o CSV) con columnas:
          - RUTA            (ruta absoluta)
          - PALABRAS CLAVE  (separadas por ; , o saltos de línea)
          - OBSERVACIONES   (texto libre)
        replace_mode: "merge" | "replace"
        progress(ev, **kw): callback opcional con ev in {"total","tick","text"}.
        """
        from pathlib import Path
        import csv

        def emit(ev, **kw):
            try:
                if progress: progress(ev, **kw)
            except Exception:
                pass

        sheet = Path(sheet_path)
        if not sheet.exists():
            raise FileNotFoundError(sheet)

        def _norm_header(s: str) -> str:
            return (s or "").strip().lower()

        def _split_kws(s: str) -> list[str]:
            if not s: return []
            s = s.replace("\n", ";").replace(",", ";")
            return [x.strip() for x in s.split(";") if x.strip()]

        stats = {"rows": 0, "docs": 0, "kws_added": 0, "notes_set": 0, "replaced_docs": 0}
        seen_docs = set()

        # --- AUTOKW: genera palabras clave a partir de la ruta si no vienen en el Excel/CSV ---
        import re as _re
        def _auto_kws_from_path(ruta: str) -> list[str]:
            try:
                base = os.path.splitext(os.path.basename(ruta))[0]
                folder = os.path.basename(os.path.dirname(ruta))
                toks = set()
                for s in (base, folder):
                    for t in _re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{3,}", s or ""):
                        toks.add(t.lower())
                toks = [t for t in toks if len(t) >= 3]
                # Limita a 12 términos útiles para no ensuciar la DB
                return toks[:12]
            except Exception:
                return []


        def _upsert_row(ruta: str, kws_str: str, note: str):
            nonlocal stats
            if not ruta:
                return
            ruta = os.path.normcase(os.path.normpath(ruta))

            # 1) keywords del Excel/CSV
            kws = _split_kws(kws_str)

            # 2) si no hay, AUTOKW por nombre de archivo/carpeta
            from_excel = bool(kws)
            if not kws:
                kws = _auto_kws_from_path(ruta)

            # 3) replace_mode → borra previas solo 1ª vez que aparece el doc
            if replace_mode == "replace" and ruta not in seen_docs:
                self.clear_keywords(ruta)
                stats["replaced_docs"] += 1

            # 4) inserta keywords (marca origen)
            if kws:
                src = "import" + (":excel" if from_excel else ":auto")
                stats["kws_added"] += self.add_keywords(ruta, kws, source=src, replace=False)

            # 5) observaciones
            if note and str(note).strip():
                self.set_note(ruta, str(note).strip())
                stats["notes_set"] += 1

            # 6) contadores de docs únicos
            if ruta not in seen_docs:
                stats["docs"] += 1
                seen_docs.add(ruta)


        # ---------- XLSX ----------
        if sheet.suffix.lower() == ".xlsx":
            emit("text", text="Abriendo Excel…")
            try:
                import openpyxl
            except Exception as e:
                raise RuntimeError(f"Para .xlsx necesitas openpyxl: {e}")
            wb = openpyxl.load_workbook(str(sheet), read_only=True, data_only=True)
            ws = wb.active
            # total de filas (sin cabecera)
            total = max(0, int((ws.max_row or 1) - 1))
            emit("total", total=total)
            headers = [_norm_header(str(c.value or "")) for c in next(ws.iter_rows(min_row=1, max_row=1))]
            idx = {h: i for i, h in enumerate(headers)}

            def _get(row, name):
                i = idx.get(_norm_header(name))
                if i is None: return ""
                v = row[i].value
                return "" if v is None else str(v)

            done = 0
            emit("text", text="Importando filas…")
            for row in ws.iter_rows(min_row=2, values_only=False):
                ruta = _get(row, "ruta") or _get(row, "RUTA")
                palabras = _get(row, "palabras clave")
                obs = _get(row, "observaciones")
                if ruta:
                    _upsert_row(ruta, palabras, obs)
                    stats["rows"] += 1
                    done += 1
                    if done % max(1, progress_every) == 0:
                        emit("tick", done=done)
            emit("tick", done=done)
            return stats

        # ---------- CSV ----------
        emit("text", text="Contando filas (CSV)…")
        # cuenta líneas (sin cabecera)
        with open(sheet, "r", encoding="utf-8-sig", newline="") as f:
            total = sum(1 for _ in f)
        total = max(0, total - 1)
        emit("total", total=total)

        emit("text", text="Importando filas (CSV)…")
        with open(sheet, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096);
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            r = csv.DictReader(f, dialect=dialect)
            done = 0
            for rec in r:
                rec_l = {_norm_header(k): (v or "") for k, v in rec.items()}
                ruta = rec_l.get("ruta", "") or rec_l.get("path", "")
                palabras = rec_l.get("palabras clave", "")
                obs = rec_l.get("observaciones", "")
                if ruta:
                    _upsert_row(ruta, palabras, obs)
                    stats["rows"] += 1
                    done += 1
                    if done % max(1, progress_every) == 0:
                        emit("tick", done=done)
            emit("tick", done=done)
        return stats

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            c = conn.cursor()

            # Base (se crea si no existe; no rompe si ya existe con más columnas)
            c.execute("""
                CREATE TABLE IF NOT EXISTS doc_keywords (
                    fullpath   TEXT NOT NULL,
                    keyword    TEXT NOT NULL,
                    source     TEXT DEFAULT '',
                    created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now','localtime'))
                )
            """)

            # --- MIGRACIÓN: añadir columnas que falten en BDs antiguas ---
            cols = {row[1].lower() for row in c.execute("PRAGMA table_info(doc_keywords)")}
            if "source" not in cols:
                c.execute("ALTER TABLE doc_keywords ADD COLUMN source TEXT DEFAULT ''")
            if "created_at" not in cols:
                c.execute("""ALTER TABLE doc_keywords
                             ADD COLUMN created_at TEXT
                             DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now','localtime'))""")

            # Índices (case-insensitive). El UNIQUE puede fallar si ya hay duplicados → fallback no único.
            c.execute("CREATE INDEX IF NOT EXISTS idx_doc_keywords_fp ON doc_keywords(fullpath)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_doc_keywords_kw ON doc_keywords(keyword)")
            try:
                c.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_keywords_u_expr
                    ON doc_keywords( lower(fullpath), lower(keyword) )
                """)
            except sqlite3.OperationalError:
                # Si existen duplicados ya, al menos dejamos un índice normal para acelerar
                c.execute("CREATE INDEX IF NOT EXISTS idx_doc_keywords_fp_kw ON doc_keywords(fullpath, keyword)")

            # Observaciones
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
