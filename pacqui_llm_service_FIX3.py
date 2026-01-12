
from __future__ import annotations
import os, math, sqlite3, struct, re
from pathlib import Path

#PACqui 1.3.0
def _cpu_autotune(ctx_tokens: int):
    import os, multiprocessing
    cores = multiprocessing.cpu_count() or 4
    # Hilos: usa todos menos 1 si hay >=6; si no, todos
    n_threads = int(os.getenv("PACQUI_THREADS", str(cores - 1 if cores >= 6 else cores)))
    # Batch de CPU (alto → puede disparar bugs en Q8_0 si te pasas)
    n_batch = min(ctx_tokens, int(os.getenv("PACQUI_N_BATCH", "192")))
    # Micro-batch bajo evita 'invalid logits id' en algunas builds Windows
    n_ubatch = int(os.getenv("PACQUI_N_UBATCH", "32"))
    # CPU-only
    n_gpu_layers = 0
    # I/O rápido
    use_mmap = True
    # mlock opcional
    use_mlock = bool(int(os.getenv("PACQUI_MLOCK", "0")))
    return dict(n_threads=n_threads, n_batch=n_batch, n_ubatch=n_ubatch,
                n_gpu_layers=n_gpu_layers, use_mmap=use_mmap, use_mlock=use_mlock)




def _get_keywords(cur, fullpath: str):
    cur.execute("SELECT keyword FROM doc_keywords WHERE lower(fullpath)=lower(?) ORDER BY keyword COLLATE NOCASE", (fullpath,))
    return [r[0] for r in cur.fetchall()]


def _get_note(cur, fullpath: str) -> str:
    try:
        cur.execute("SELECT note FROM doc_notes WHERE lower(fullpath)=lower(?)", (fullpath,))
        row = cur.fetchone()
        return row[0] if row else ""
    except Exception:
        return ""


def _hash_embedder(text, dim=256):
    import hashlib
    v=[0.0]*dim
    if text:
        for tok in re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{2,}", str(text).lower()):
            h=int(hashlib.md5(tok.encode("utf-8")).hexdigest(),16)
            i=h%dim; s=1.0 if (h>>1)&1 else -1.0; v[i]+=s
    n=(sum(x*x for x in v)**0.5) or 1.0
    return [x/n for x in v]


class LLMService:
    """Servicio LLM sin UI para PACqui. Carga GGUF (llama-cpp) y expone chat().
       Incluye utilidades para contar tokens (aprox) y compactar contexto."""

    def __init__(self, db_path: str):
        from pathlib import Path
        self.db_path = str(Path(db_path))
        self.model = None
        self.model_path = ""
        self.ctx = 2048

        # Reserva 1–2 cores para la UI

        self.threads = max(2, (os.cpu_count() or 4) - 2)

        # Más conservador para primer run (evita picos)
        self.n_batch = 256

        # --- NUEVO: sincronización y flags de warmup ---
        import threading
        self._model_lock = threading.Lock()  # un solo hilo puede usar el modelo a la vez
        self._warming = False  # warmup en curso
        self._warmed = False  # warmup finalizado

        # cache opcional del embedder
        self._embedder_cached = None

    # --------- carga ---------

    def load(self, model_path: str, ctx: int = 8192):
        from llama_cpp import Llama
        import os
        name = Path(model_path).name.lower()
        chat_fmt = "llama-2"
        if "mistral" in name:
            chat_fmt = "mistral-instruct"
        elif "qwen" in name:
            chat_fmt = "qwen2"
        elif "phi" in name:
            chat_fmt = "phi3"

        # Idempotencia
        if self.model and self.model_path == model_path and int(self.ctx) == int(
                ctx or os.getenv("PACQUI_CTX", "8192")):
            return

        self._warmed = False;
        self._warming = False
        self.model_path = model_path
        # ↑↑ ctx por variable de entorno, default 8192 (antes 2048)
        self.ctx = int(ctx or int(os.getenv("PACQUI_CTX", "8192")))

        perf = _cpu_autotune(self.ctx)
        self.model = Llama(
            model_path=self.model_path,
            n_ctx=self.ctx,
            n_threads=perf["n_threads"],
            n_batch=perf["n_batch"],
            n_ubatch=perf["n_ubatch"],
            n_gpu_layers=perf["n_gpu_layers"],
            f16_kv=True,
            use_mmap=perf["use_mmap"],
            vocab_only=False,
            chat_format=chat_fmt,
            use_mlock=perf["use_mlock"],
        )

    def is_loaded(self) -> bool:
        return self.model is not None

    def cancel(self):
        """Cancelación best-effort: si la build de llama-cpp expone cancel/reset, intentalo."""
        try:
            if hasattr(self.model, "cancel") and callable(self.model.cancel):
                self.model.cancel()
            elif hasattr(self.model, "reset") and callable(self.model.reset):
                self.model.reset()
        except Exception:
            pass

    # --------- token count util ---------
    def count_tokens(self, text: str) -> int:
        try:
            return len(self.model.tokenize(text.encode("utf-8"), add_bos=False))
        except Exception:
            return max(1, len(text) // 3)

    def _rag_meta_get(self, key: str, default=None):
        try:
            con = sqlite3.connect(self.db_path, check_same_thread=False)
            cur = con.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS rag_meta(key TEXT PRIMARY KEY, value TEXT)")
            row = cur.execute("SELECT value FROM rag_meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
        except Exception:
            return default
        finally:
            try:
                con.close()
            except Exception:
                pass

    def _get_embedder(self):
        import os
        if os.getenv("PACQUI_FORCE_HASH", "1") == "1":
            # siempre hash-embedder
            def _encode_hash(text: str, _dim=256):
                return _hash_embedder(text, dim=_dim)

            self._embedder_cached = {"encode": _encode_hash, "dim": 256, "backend": "hash", "sig": "hash:256"}
            return self._embedder_cached

        # cache
        if hasattr(self, "_embedder_cached") and self._embedder_cached:
            return self._embedder_cached

        sig = self._rag_meta_get("embedding_sig", None)

        # Si el índice fue creado con Sentence-Transformers (st:dim:model)
        if sig and sig.startswith("st:"):
            try:
                parts = sig.split(":", 2)
                dim = int(parts[1]) if len(parts) > 1 else 384
                model_path = parts[2] if len(parts) > 2 else os.getenv("PACQUI_EMBED_MODEL",
                                                                       "sentence-transformers/all-MiniLM-L6-v2")
                # Permitir override por variable de entorno PACQUI_EMBED_DIR
                model_override = os.getenv("PACQUI_EMBED_DIR", "")
                if model_override:
                    model_path = model_override
                from sentence_transformers import SentenceTransformer
                st_model = SentenceTransformer(model_path)

                def _encode_st(text: str):
                    v = st_model.encode(text or "", normalize_embeddings=True)
                    return v.tolist() if hasattr(v, "tolist") else list(map(float, v))

                self._embedder_cached = {"encode": _encode_st, "dim": dim, "backend": "st", "sig": sig}
                return self._embedder_cached
            except Exception:
                # Si el modelo no está instalado → caeremos a hash y anotaremos aviso en el contexto
                self._embedder_cached = {"encode": lambda t: _hash_embedder(t, dim=256),
                                         "dim": 256, "backend": "hash", "sig": "hash:256",
                                         "warn": f"[aviso] El índice usa {sig} pero no se pudo cargar el modelo local. Instala sentence-transformers o define PACQUI_EMBED_DIR."}
                return self._embedder_cached

        # Fallback por defecto (hash)
        self._embedder_cached = {"encode": lambda t: _hash_embedder(t, dim=256),
                                 "dim": 256, "backend": "hash", "sig": "hash:256"}
        return self._embedder_cached

    # --------- índice (keywords/observaciones) ---------
    def _index_hits(self, query: str, top_k: int = 8, max_note_chars: int = 240, prefer_only=None,
                        prefer_pdf_doc: bool = True):

        """
        Búsqueda en índice con:
        - normalización (acentos, minúsculas)
        - expansión de sinónimos (FEADER/FEAGA/pago/ayuda/anticipo…)
        - stopwords básicas
        - fallback a nombres de fichero (tabla files) y a doc_notes
        - re-ranking por extensión (PDF/DOCX primero) y nombre
        - filtro explícito prefer_only (p.ej. [".pdf"] o [".docx",".doc"])
        - “must term” si la consulta incluye FEADER
        """
        import os, sqlite3, re, unicodedata
        from pathlib import Path

        def _norm(s: str) -> str:
            if not s: return ""
            s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
            return s.lower()

        qnorm = _norm(query or "")
        toks = [t for t in re.findall(r"[a-z0-9]{3,}", qnorm)]

        # Stopwords / saludos / nombre del bot
        STOP = {
            "hola", "buenas", "buenos", "dias", "tardes", "noches", "gracias",
            "ok", "vale", "de", "la", "el", "los", "las", "un", "una", "y", "o",
            "pacqui", "assistant", "ayuda"
        }
        toks = [t for t in toks if t not in STOP]
        if not toks:
            return []

        # Sinónimos mínimos del dominio
        syn = {
            "feader": ["feader", "eafrd", "pdr", "desarrollorural", "desarrollo", "rural"],
            "feaga": ["feaga", "fega"],
            "pago": ["pago", "pagos", "abono", "abonar", "anticipos", "anticipo", "liquidacion", "transferencia",
                     "ordenpago"],
            "ayuda": ["ayuda", "ayudas", "subvencion", "subvenciones", "expediente", "beneficiario"],
            "calendario": ["calendario", "planificacion", "cronograma"],
        }
        expanded = set(toks)
        for t in list(toks):
            if t in syn:
                expanded.update(syn[t])
            if t.endswith("s"):
                expanded.add(t[:-1])
            else:
                expanded.add(t + "s")
        toks = list(expanded)[:8]   # máx 8 términos efectivos (acelera consultas)


        EXT_BONUS = {".pdf": 3, ".docx": 3, ".doc": 2, ".pptx": 1}
        EXT_MALUS = {".png": -2, ".jpg": -2, ".jpeg": -2, ".gif": -2, ".py": -2, ".java": -1, ".sql": -1}

        # --- Reglas de obligación/exclusión sacadas de la consulta normalizada ---
        # Grupos "must_any": de cada grupo, debe cumplirse al menos 1 término.
        # --- Reglas de obligación/exclusión sacadas de la consulta normalizada ---
        # Grupos "must_any": de cada grupo, debe cumplirse al menos 1 término.
        must_any = []
        if "feader" in qnorm:
            must_any.append({"feader"})
        if "feaga" in qnorm:
            # FEAGA suele aparecer también como FEGA → cualquiera de los dos vale
            must_any.append({"feaga", "fega"})

        # NEW: fuerza presencia de "términos fuertes" (evita ruido de "documento/base/datos...")
        GENERIC = {
            "documento", "documentos", "doc", "docs", "pdf", "docx", "archivo", "archivos",
            "base", "datos", "repositorio", "sistema", "proceso", "procesos",
            "nuevo", "nueva", "tecnico", "tecnicos", "incorporacion", "onboarding",
            "proyecto", "proyectos", "lanzadera", "ticketing", "severidad", "analisis",
            "requerimiento", "requerimientos"
        }
        strong = [t for t in toks if t not in GENERIC and len(t) >= 5]
        # Si hay términos fuertes (p.ej. "seresco"), exige que aparezca al menos uno:
        if strong:
            must_any.append(set(strong))


        # Términos prohibidos (si el usuario pide explícitamente "no sigc", o "sin sigc")
        must_not = set()
        if "no sigc" in qnorm or "sin sigc" in qnorm:
            must_not.add("sigc")

        con = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            cur = con.cursor()
            from collections import defaultdict
            acc = defaultdict(lambda: {"kw": 0, "fname": 0, "notes": 0})

            # fuentes pinneadas (persistentes) → dict normalizado: path -> weight
            pinned = {}
            try:
                cur.execute("SELECT path, COALESCE(weight,1.0) FROM pinned_sources")
                pinned = {os.path.normcase(os.path.normpath(p)): float(w or 1.0) for (p, w) in cur.fetchall()}
            except Exception:
                pinned = {}

            # --- BONUS/MALUS por feedback histórico (qa_feedback + qa_sources) ---
            fb_by_path = {}
            try:
                cur.execute("""
                    SELECT lower(S.path) AS p,
                           AVG(CASE
                                 WHEN F.rating >= 8 THEN 1.0
                                 WHEN F.rating <= 3 THEN -1.0
                                 ELSE 0.0
                               END) AS fb
                      FROM qa_sources S
                      JOIN qa_feedback F ON F.qa_id = S.qa_id
                     GROUP BY lower(S.path)
                """)
                fb_by_path = {
                    os.path.normcase(os.path.normpath(p or "")): float(fb or 0.0)
                    for (p, fb) in cur.fetchall()
                    if p
                }
            except Exception:
                fb_by_path = {}

            # 1) keywords
            for t in toks:
                cur.execute("SELECT fullpath FROM doc_keywords WHERE lower(keyword) LIKE lower(?) LIMIT 1500",
                            (f"%{t}%",))
                for (fp,) in cur.fetchall():
                    acc[os.path.normcase(os.path.normpath(fp))]["kw"] += 1

            # 2) observaciones
            for t in toks:
                cur.execute("SELECT fullpath FROM doc_notes WHERE lower(note) LIKE lower(?) LIMIT 1500", (f"%{t}%",))
                for (fp,) in cur.fetchall():
                    acc[os.path.normcase(os.path.normpath(fp))]["notes"] += 1

            # 3) nombres y carpetas (tabla files si existe)
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
                if cur.fetchone():
                    for t in toks:
                        like = f"%{t}%"
                        cur.execute("""SELECT fullpath FROM files
                                       WHERE lower(name) LIKE ? OR lower(dir) LIKE ?
                                       LIMIT 1500""", (like, like))
                        for (fp,) in cur.fetchall():
                            acc[os.path.normcase(os.path.normpath(fp))]["fname"] += 1
            except Exception:
                pass

            if not acc:
                return []

            # --- BOOST por "concept_sources" (cuando la consulta encaja con conceptos) ---
            concept_boost = {}
            try:
                from meta_store import MetaStore
                ms_ = MetaStore(self.db_path)

                # Usa la consulta normalizada (qnorm) para buscar conceptos relevantes
                q_for_concepts = qnorm  # título/cuerpo/alias
                concepts = ms_.list_concepts(q_for_concepts, limit=5)
                import os
                def _nrm(p):
                    return os.path.normcase(os.path.normpath(p or ""))

                for c in concepts:
                    for cs in ms_.list_concept_sources(c["id"]):
                        p = _nrm(cs["path"])
                        w = float(cs.get("weight") or 1.2)
                        # Conserva el mayor peso si aparece varias veces
                        if p:
                            concept_boost[p] = max(concept_boost.get(p, 0.0), w)
            except Exception:
                concept_boost = {}

            ranked = []
            for fp, sc in acc.items():
                ext = Path(fp).suffix.lower()

                # Filtro “prefer_only” (desde el front: solo pdf/docx si se pide)
                if prefer_only and ext not in set(prefer_only):
                    continue

                # “must terms”: p.ej. si pregunta contiene “feader”, exige que aparezca
                # --- Filtrado por obligación (must_any) y exclusiones (must_not) ---
                # Construye 1 sola vez el "blob" normalizado con kws + nota + nombre fichero
                blob = " ".join(_get_keywords(cur, fp) +
                                [_get_note(cur, fp), os.path.basename(fp)])
                blob_norm = _norm(blob)

                # Cada grupo de must_any debe tener AL MENOS 1 término presente
                if must_any and not all(any(t in blob_norm for t in group) for group in must_any):
                    continue
                # Ningún término prohibido debe estar presente
                if must_not and any(t in blob_norm for t in must_not):
                    continue

                ext_adj = EXT_BONUS.get(ext, 0) + EXT_MALUS.get(ext, 0)

                # Penaliza coincidencias SOLO por nombre de fichero/carpeta
                only_fname = (sc["kw"] == 0 and sc["notes"] == 0)
                if only_fname and len(toks) <= 2:
                    continue

                rank = sc["kw"] * 12 + sc["notes"] * 8 + sc["fname"] * 2 + ext_adj * 8
                if only_fname:
                    rank -= 10

                # --- BOOST por fuentes pinneadas ---
                try:
                    if fp in pinned:
                        rank += int(round(10.0 * pinned[fp]))
                except Exception:
                    pass

                # --- BONUS/MALUS por feedback histórico ---
                try:
                    fb = fb_by_path.get(fp)
                    if fb is not None:
                        # Factor configurable por env (por defecto 6.0 → ±6 puntos)
                        k = float(os.getenv("PACQUI_FB_BOOST", "6.0"))
                        rank += int(round(k * fb))
                except Exception:
                    pass


                # --- BOOST por "concept_sources" (más fuerte que pinned) ---
                try:
                    if fp in concept_boost:
                        rank += int(round(12.0 * concept_boost[fp]))
                except Exception:
                    pass

                # Agrega al ranking con el score FINAL
                ranked.append((rank, sc["kw"], sc["notes"], sc["fname"], fp))

            if not ranked:
                return []

            ranked.sort(key=lambda x: (-x[0], -x[1], -x[2], -x[3], x[4]))
            ranked = ranked[:max(1, int(top_k))]

            # Si entre los top_k hay PDF/DOC/DOCX, nos quedamos SOLO con esos
            # Si se desea priorizar PDF/DOC/DOCX, filtra (por defecto: True)
            if prefer_pdf_doc:
                prefer_exts = {".pdf", ".docx", ".doc"}
                prefer_only_list = [t for t in ranked if Path(t[4]).suffix.lower() in prefer_exts]
                if prefer_only_list:
                    ranked = prefer_only_list[:max(1, int(top_k))]

            out = []
            for _rank, _kw, _notes, _fn, fp in ranked:
                name = os.path.basename(fp)
                kws = "; ".join(_get_keywords(cur, fp))
                note = _get_note(cur, fp)
                if len(note) > max_note_chars: note = note[:max_note_chars] + "…"
                out.append({"path": fp, "name": name, "score": float(_rank), "keywords": kws, "note": note})

            return out
        finally:
            con.close()



    def build_index_context(self, query: str, top_k: int = 5, max_note_chars: int = 240):
        hits = self._index_hits(query, top_k=top_k, max_note_chars=max_note_chars)
        if not hits: return "", []
        lines = []
        for s in hits:
            lines.append(f"- {s['name']}  ·  {s['path']}\n  Palabras clave: {s['keywords']}\n  Observaciones: {s['note']}")
        return "Documentos sugeridos (por palabras clave del índice):\n" + "\n".join(lines), hits

    # --------- RAG simplificado (hash-embeddings) ---------
    def _rag_rows(self, query: str | None = None, max_candidates: int = 600):
        import re, sqlite3
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            cur = con.cursor()
            if not (query or "").strip():
                return []

            # tokens significativos de la query
            toks = [t.lower() for t in re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{3,}", (query or ""))]
            STOP = {"para", "con", "por", "unos", "unas", "este", "esta", "esto", "sobre", "desde", "hasta",
                    "entre", "que", "como", "cual", "cuales", "de", "la", "el", "los", "las", "y", "o", "u",
                    "del", "al"}
            toks = [t for t in toks if t not in STOP][:6]  # un poco más laxo y hasta 6 términos

            # ⚑ whitelists útiles del dominio
            qlow = (query or "").lower()
            if "mic" in qlow and "mic" not in toks:
                toks.append("mic")
            if "fega" in qlow and "feaga" not in toks:
                toks.append("feaga")
            if "feader" in qlow and "feader" not in toks:
                toks.append("feader")

            if toks:
                clauses, params = [], []
                for tok in toks:
                    pat = f"%{tok}%"
                    clauses.append("c.text LIKE ?");
                    params.append(pat)
                    clauses.append("c.file_path LIKE ?");
                    params.append(pat)
                where = " WHERE " + " OR ".join(clauses)
                sql = ("SELECT c.id, c.text, c.file_path, e.vec "
                       "FROM chunks c JOIN embeddings e ON e.chunk_id=c.id" + where +
                       " ORDER BY c.id DESC LIMIT ?")
                params.append(int(max_candidates))
                rows = cur.execute(sql, params).fetchall()
            else:
                rows = cur.execute(
                    "SELECT c.id, c.text, c.file_path, e.vec "
                    "FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                    "ORDER BY c.id DESC LIMIT ?", (int(max_candidates),)
                ).fetchall()

            return rows
        except Exception:
            return []
        finally:
            con.close()



    def _rag_retrieve(self, query: str, k: int = 8, max_chars: int = 1200) -> str:
        """
        Recupera k fragmentos re-ordenando por similitud + preferencia de extensión (PDF/DOCX),
        SIN depender de self.rag (usa chunks/embeddings del SQLite).
        """
        import re, os, struct
        from pathlib import Path

        rows = self._rag_rows(query, max_candidates=900)

        if not rows:
            return ""

        emb = self._get_embedder()
        qv = emb["encode"](query or "")

        def cos(a, b):
            s = 0.0
            m = min(len(a), len(b))
            for i in range(m):
                s += a[i] * b[i]
            return s

        qlow = (query or "").lower()
        ext_filter = set()
        if "pdf" in qlow: ext_filter.add(".pdf")
        if "docx" in qlow: ext_filter.add(".docx")
        if "doc" in qlow and ".docx" not in qlow: ext_filter.add(".doc")

        EXT_BONUS = {".pdf": 3, ".docx": 3, ".doc": 2, ".pptx": 1}
        EXT_MALUS = {".png": -2, ".jpg": -2, ".jpeg": -2, ".gif": -2, ".py": -2, ".java": -1, ".sql": -1}
        toks = re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{3,}", qlow)


        scored = []
        for _cid, text, path, vec_blob in rows:
            try:
                vec = list(struct.unpack(f"{len(vec_blob) // 4}f", vec_blob)) if isinstance(vec_blob, (
                bytes, bytearray)) else list(vec_blob)
            except Exception:
                continue
            ext = Path(path or "").suffix.lower()
            if ext_filter and ext not in ext_filter:
                continue
            txt_low = (text or "").lower()
            # si hay términos de la consulta y ninguno aparece en el texto -> descarta (ruido)
            if toks and not any(t in txt_low for t in toks):
                continue

            base = cos(qv, vec)
            fname = os.path.basename(path or "").lower()
            fname_bonus = sum(1 for t in toks if t in fname)
            ext_adj = EXT_BONUS.get(ext, 0) + EXT_MALUS.get(ext, 0)

            txt_low = (text or "").lower()

            # --- BOOST dominio PAC/MIC/SICOP/PEPAC ---
            mic_terms = (
            " mic ", " ficheros de pago ", " fichero de pago ", " sicop ", " pepac ", " feaga ", " feader ")
            dom_boost = 1.5 if (
                        any(t in txt_low for t in mic_terms) or any(t.strip() in fname for t in mic_terms)) else 0.0

            # --- PENALIZA chunks de metadatos/cabeceras (suelen dar respuestas tipo "Ref./Versión/Pág.") ---
            meta_terms = (
            "ref.:", "ref.", "versión", "version", "pág.", "pag.", "control del documento", "índice", "indice")
            meta_hits = sum(1 for mt in meta_terms if mt in txt_low)
            meta_penalty = -8.0 * meta_hits

            # penaliza también fragmentos demasiado cortos (suelen ser cabeceras sueltas)
            if len((text or "").strip()) < 120:
                meta_penalty -= 6.0

            score = (base * 10) + (ext_adj * 5) + (fname_bonus * 2) + dom_boost + meta_penalty
            scored.append((score, text or "", path or ""))

        if not scored:
            return ""

        scored.sort(key=lambda x: -x[0])
        picked = scored[:max(1, int(k))]

        frags, used = [], 0
        for i, (_, text, path) in enumerate(picked, start=1):
            t = (text or "").strip()
            if not t:
                continue
            if len(t) > 420:
                t = t[:420] + "…"

            frag = f"[{i}] {t}\n    Fuente: {path}"
            if used + len(frag) > max_chars * k:
                break
            frags.append(frag)
            used += len(frag)

        return "\n\n".join(frags)

    def concept_context(self, query_text: str, max_chars: int = 600, top_k: int = 5) -> str:
        try:
            from meta_store import MetaStore
            ms = MetaStore(self.db_path)
            return ms.concept_context_for(query_text, max_chars=max_chars, top_k=top_k)
        except Exception:
            return ""

    # --------- chat ---------
    # pacqui_llm_service_FIX3.py  (dentro de class LLMService)

    def _trim_to_tokens(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0 or not text:
            return ""
        toks = self.count_tokens(text)
        if toks <= max_tokens:
            return text
        # recorte por líneas conservador
        lines = text.splitlines()
        out = []
        for ln in lines:
            out.append(ln)
            if self.count_tokens("\n".join(out)) > max_tokens:
                out.pop()
                break
        if not out:  # último recurso: por caracteres
            ratio = max_tokens / max(1, toks)
            cut = max(64, int(len(text) * ratio))
            return text[:cut]
        return "\n".join(out)

    def _shrink_messages(self, messages, budget_in_tokens: int):
        """Intenta recortar SOLO el bloque de contexto del 'user'.
        Busca [FRAGMENTOS] primero y luego [ÍNDICE]."""
        if not messages:
            return messages
        msgs = list(messages)
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if m.get("role") == "user":
                content = m.get("content", "")
                # prioridad: recortar fragmentos RAG
                parts = content.split("[FRAGMENTOS]")
                if len(parts) > 1:
                    head = parts[0]
                    rest = "[FRAGMENTOS]".join(parts[1:])
                    # recorta primero FRAGMENTOS
                    keep = rest
                    while self.count_tokens(head + "[FRAGMENTOS]" + keep) > budget_in_tokens and "\n" in keep:
                        keep = "\n".join(keep.splitlines()[:-4])  # quita 4 líneas por iteración
                    content = head + "[FRAGMENTOS]" + keep
                # si sigue pasando, recorta todo el 'user' al presupuesto
                if self.count_tokens(content) > budget_in_tokens:
                    content = self._trim_to_tokens(content, budget_in_tokens)
                msgs[i] = {"role": "user", "content": content}
                break
        return msgs

    # --- warmup no bloqueante (añadir dentro de LLMService) ---
    def warmup_async(self):
        """Lanza un warmup una sola vez y nunca en paralelo."""
        import threading
        if self._warmed or self._warming or not self.model:
            return
        self._warming = True
        threading.Thread(target=self._warmup, daemon=True).start()

    def _warmup(self):
        """Calienta embedder, SQLite y compila el grafo con un chat mínimo, BAJO LOCK."""
        try:
            # 1) embedder
            try:
                _ = self._get_embedder()
            except Exception:
                pass

            # 2) tocar RAG (SQLite)
            try:
                _ = self._rag_rows()[:1]
            except Exception:
                pass

            # 3) micro-chat para compilar grafo/KV — SIEMPRE bajo lock
            # 3) micro-warmup SIN chat-format para compilar grafo/KV — SIEMPRE bajo lock
            try:
                if self.model is not None:
                    with self._model_lock:
                        try:
                            _ = self.model.create_completion(
                                prompt="ok", max_tokens=1, temperature=0.0
                            )
                        except Exception:
                            # Si tu build no expone 'create_completion', ignora el warmup
                            pass
            except Exception:
                pass

        finally:
            self._warmed = True
            self._warming = False

    # === PEGAR DENTRO DE class LLMService, sustituyendo a los métodos existentes ===

    def chat(self, messages, temperature: float = 0.3, max_tokens: int = 768, stream: bool = True, stop=None):

        if not self.model:
            raise RuntimeError("Modelo no cargado. Elige un .gguf antes de chatear.")

        ES_POLICY = ("Eres el asistente de PACqui. Responde SIEMPRE en español neutro. "
                     "Si el usuario escribe en otro idioma, traduce mentalmente y contesta en español.")

        # Max tokens por ENV si viene definido
        try:
            import os
            if max_tokens is None or int(max_tokens) <= 0:
                max_tokens = int(os.getenv("PACQUI_MAX_TOKENS", "768"))
        except Exception:
            pass

        # Stops por defecto: evitamos cortar por "Pregunta..." u ordinales
        default_stops = ["=== FRAGMENTOS", "=== ÍNDICE"]
        stops_list = default_stops if not stop else list(stop)

        # Inyecta política ES si no está
        has_es = any((m.get("role") == "system" and "español" in (m.get("content", "").lower()))
                     for m in (messages or []))
        if not has_es:
            messages = [{"role": "system", "content": ES_POLICY}] + list(messages or [])

        # Traza de tokens de entrada
        try:
            user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            in_tok = self.count_tokens(user_blob)
            print(f"[TOKENS] ctx={self.ctx}  in≈{in_tok}  out_max={int(max_tokens)}")
        except Exception:
            pass

        out_tok = int(max_tokens)
        try:
            with self._model_lock:
                import inspect

                # Opciones comunes (sin 'cache_prompt')
                opts = dict(
                    messages=messages,
                    temperature=float(temperature),
                    max_tokens=int(out_tok),
                    stream=bool(stream),
                    # Cortafuegos contra listas/enum repetitivas y clones de apertura
                    repeat_penalty=1.15,
                    stop=stops_list,


                )

                # Solo añadimos 'cache_prompt' si la build lo soporta
                try:
                    sig = inspect.signature(self.model.create_chat_completion)
                    if "cache_prompt" in sig.parameters:
                        opts["cache_prompt"] = False  # desactivado adrede
                except Exception:
                    pass

                try:
                    return self.model.create_chat_completion(**opts)
                except TypeError:
                    # Retry ultra-compatible: sin 'cache_prompt' por si el wrapper no lo admite
                    opts.pop("cache_prompt", None)
                    return self.model.create_chat_completion(**opts)

        except (RuntimeError, ValueError) as e:
            raise

    # --- justo bajo LLMService.chat(...) ---
    def stream_chat(self, *, messages, temperature=0.2, max_tokens=256, stop=None, **kw):

        """
        Azúcar sintáctico: garantiza stream=True y delega en chat().
        Permite que el front llame self.llm.stream_chat(...) sin romper.
        """
        kw.pop("stream", None)
        return self.chat(messages=messages,
                         temperature=temperature,
                         max_tokens=max_tokens,
                         stream=True,
                         stop=stop,
                         **kw)


    # === FIN BLOQUE ===





