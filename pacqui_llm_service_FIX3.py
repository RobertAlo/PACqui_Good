
from __future__ import annotations
import os, math, sqlite3, struct, re
from pathlib import Path

class LLMService:
    """Servicio LLM sin UI para PACqui. Carga GGUF (llama-cpp) y expone chat().
       Incluye utilidades para contar tokens (aprox) y compactar contexto."""
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path))
        self.model = None
        self.model_path = ""
        self.ctx = 2048
        self.threads = max(2, (os.cpu_count() or 4) - 1)
        self.n_batch = 256

    # --------- carga ---------
    def load(self, model_path: str, ctx: int = 2048):
        from llama_cpp import Llama
        name = Path(model_path).name.lower()
        # Detecta formato de chat para reducir overhead del prompt
        chat_fmt = "llama-2"
        if "mistral" in name:
            chat_fmt = "mistral-instruct"
        elif "qwen" in name:
            chat_fmt = "qwen2"
        elif "phi" in name:
            chat_fmt = "phi3"

        self.model_path = model_path
        self.ctx = int(ctx or 2048)
        # Reserva CPU para la UI
        self.threads = max(2, (os.cpu_count() or 4) - 2)
        self.model = Llama(
            model_path=self.model_path,
            n_ctx=self.ctx,
            n_threads=self.threads,
            n_batch=self.n_batch,
            n_gpu_layers=0,
            f16_kv=True,
            use_mmap=True,
            vocab_only=False,
            chat_format=chat_fmt,
        )

    def is_loaded(self) -> bool:
        return self.model is not None

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
                self._embedder_cached = {"encode": lambda t: self._hash_embedder(t, dim=256),
                                         "dim": 256, "backend": "hash", "sig": "hash:256",
                                         "warn": f"[aviso] El índice usa {sig} pero no se pudo cargar el modelo local. Instala sentence-transformers o define PACQUI_EMBED_DIR."}
                return self._embedder_cached

        # Fallback por defecto (hash)
        self._embedder_cached = {"encode": lambda t: self._hash_embedder(t, dim=256),
                                 "dim": 256, "backend": "hash", "sig": "hash:256"}
        return self._embedder_cached

    # --------- índice (keywords/observaciones) ---------
    def _index_hits(self, query: str, top_k: int = 8, max_note_chars: int = 240, prefer_only=None):
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
        toks = list(expanded)

        EXT_BONUS = {".pdf": 3, ".docx": 3, ".doc": 2, ".pptx": 1}
        EXT_MALUS = {".png": -2, ".jpg": -2, ".jpeg": -2, ".gif": -2, ".py": -2, ".java": -1, ".sql": -1}

        # --- Reglas de obligación/exclusión sacadas de la consulta normalizada ---
        # Grupos "must_any": de cada grupo, debe cumplirse al menos 1 término.
        must_any = []
        if "feader" in qnorm:
            must_any.append({"feader"})
        if "feaga" in qnorm:
            # FEAGA suele aparecer también como FEGA → cualquiera de los dos vale
            must_any.append({"feaga", "fega"})

        # Términos prohibidos (si el usuario pide explícitamente "no sigc", o "sin sigc")
        must_not = set()
        if "no sigc" in qnorm or "sin sigc" in qnorm:
            must_not.add("sigc")

        con = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            cur = con.cursor()
            from collections import defaultdict
            acc = defaultdict(lambda: {"kw": 0, "fname": 0, "notes": 0})

            # 1) keywords
            for t in toks:
                cur.execute("SELECT fullpath FROM doc_keywords WHERE lower(keyword) LIKE lower(?) LIMIT 5000",
                            (f"%{t}%",))
                for (fp,) in cur.fetchall():
                    acc[os.path.normcase(os.path.normpath(fp))]["kw"] += 1

            # 2) observaciones
            for t in toks:
                cur.execute("SELECT fullpath FROM doc_notes WHERE lower(note) LIKE lower(?) LIMIT 5000", (f"%{t}%",))
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
                                       LIMIT 5000""", (like, like))
                        for (fp,) in cur.fetchall():
                            acc[os.path.normcase(os.path.normpath(fp))]["fname"] += 1
            except Exception:
                pass

            if not acc:
                return []

            ranked = []
            for fp, sc in acc.items():
                ext = Path(fp).suffix.lower()

                # Filtro “prefer_only” (desde el front: solo pdf/docx si se pide)
                if prefer_only and ext not in set(prefer_only):
                    continue

                # “must terms”: p.ej. si pregunta contiene “feader”, exige que aparezca
                # --- Filtrado por obligación (must_any) y exclusiones (must_not) ---
                # Construye 1 sola vez el "blob" normalizado con kws + nota + nombre fichero
                blob = " ".join(self._get_keywords(cur, fp) +
                                [self._get_note(cur, fp), os.path.basename(fp)])
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
                ranked.append((rank, sc["kw"], sc["notes"], sc["fname"], fp))

            if not ranked:
                return []

            ranked.sort(key=lambda x: (-x[0], -x[1], -x[2], -x[3], x[4]))
            ranked = ranked[:max(1, int(top_k))]

            # Si entre los top_k hay PDF/DOC/DOCX, nos quedamos SOLO con esos
            prefer_exts = {".pdf", ".docx", ".doc"}
            prefer_only_list = [t for t in ranked if Path(t[4]).suffix.lower() in prefer_exts]
            if prefer_only_list:
                ranked = prefer_only_list[:max(1, int(top_k))]

            out = []
            for _rank, _kw, _notes, _fn, fp in ranked:
                name = os.path.basename(fp)
                kws = "; ".join(self._get_keywords(cur, fp))
                note = self._get_note(cur, fp)
                if len(note) > max_note_chars: note = note[:max_note_chars] + "…"
                out.append({"path": fp, "name": name, "score": _kw + _notes + _fn, "keywords": kws, "note": note})
            return out
        finally:
            con.close()

    def _get_keywords(self, cur, fullpath: str):
        cur.execute("SELECT keyword FROM doc_keywords WHERE lower(fullpath)=lower(?) ORDER BY keyword COLLATE NOCASE", (fullpath,))
        return [r[0] for r in cur.fetchall()]

    def _get_note(self, cur, fullpath: str) -> str:
        try:
            cur.execute("SELECT note FROM doc_notes WHERE lower(fullpath)=lower(?)", (fullpath,))
            row = cur.fetchone()
            return row[0] if row else ""
        except Exception:
            return ""

    def build_index_context(self, query: str, top_k: int = 5, max_note_chars: int = 240):
        hits = self._index_hits(query, top_k=top_k, max_note_chars=max_note_chars)
        if not hits: return "", []
        lines = []
        for s in hits:
            lines.append(f"- {s['name']}  ·  {s['path']}\n  Palabras clave: {s['keywords']}\n  Observaciones: {s['note']}")
        return "Documentos sugeridos (por palabras clave del índice):\n" + "\n".join(lines), hits

    # --------- RAG simplificado (hash-embeddings) ---------
    def _rag_rows(self):
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            cur = con.cursor()
            try:
                rows = cur.execute(
                    "SELECT c.id, c.text, c.file_path, e.vec FROM chunks c JOIN embeddings e ON e.chunk_id=c.id"
                ).fetchall()
            except Exception:
                rows = []
            return rows
        finally:
            con.close()

    def _rag_retrieve(self, query: str, k: int = 4, max_chars: int = 700) -> str:
        """
        Recupera k fragmentos re-ordenando por similitud + preferencia de extensión (PDF/DOCX).
        Si el usuario pide PDF/DOCX explícitamente en la consulta, se filtra a esas extensiones.
        """
        import os, re
        from pathlib import Path

        qlow = (query or "").lower()
        ext_filter = set()
        if "pdf" in qlow: ext_filter.add(".pdf")
        if "docx" in qlow: ext_filter.add(".docx")
        if "doc" in qlow and ".docx" not in qlow: ext_filter.add(".doc")

        EXT_BONUS = {".pdf": 3, ".docx": 3, ".doc": 2, ".pptx": 1}
        EXT_MALUS = {".png": -2, ".jpg": -2, ".jpeg": -2, ".gif": -2, ".py": -2, ".java": -1, ".sql": -1}

        try:
            hits = self.rag.search(query, top_k=int(max(k * 3, 8)))
        except Exception:
            hits = []
        if not hits:
            return ""

        toks = re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{3,}", qlow)

        def _score(h):
            path = (h.get("path") or h.get("file") or "").strip()
            ext = Path(path).suffix.lower()
            base = float(h.get("score", 0.0))
            fname = os.path.basename(path).lower()
            fname_bonus = sum(1 for t in toks if t.lower() in fname)
            ext_adj = EXT_BONUS.get(ext, 0) + EXT_MALUS.get(ext, 0)
            return base * 10 + ext_adj * 5 + fname_bonus * 2

        scored = []
        for h in hits:
            path = (h.get("path") or h.get("file") or "").strip()
            ext = Path(path).suffix.lower()
            if ext_filter and ext not in ext_filter:
                continue
            scored.append((_score(h), h))
        if not scored:
            return ""

        scored.sort(key=lambda x: -x[0])
        picked = [h for _, h in scored[:max(1, int(k))]]

        frags, used = [], 0
        for i, h in enumerate(picked, start=1):
            text = (h.get("text") or "").strip()
            path = (h.get("path") or h.get("file") or "").strip()
            if not text:
                continue
            if len(text) > 600:
                text = text[:600] + "…"
            frag = f"[{i}] {text}\n    Fuente: {path}"
            if used + len(frag) > max_chars * k:
                break
            frags.append(frag)
            used += len(frag)
        return "\n\n".join(frags)

    def _hash_embedder(self, text, dim=256):
        import hashlib
        v=[0.0]*dim
        if text:
            for tok in re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{2,}", str(text).lower()):
                h=int(hashlib.md5(tok.encode("utf-8")).hexdigest(),16)
                i=h%dim; s=1.0 if (h>>1)&1 else -1.0; v[i]+=s
        n=(sum(x*x for x in v)**0.5) or 1.0
        return [x/n for x in v]

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

    def chat(self, messages, temperature=0.3, max_tokens=256, stream=True):
        if not self.model:
            raise RuntimeError("Modelo no cargado. Elige un .gguf antes de chatear.")
        try:
            # Algunos builds no aceptan cache_prompt
            return self.model.create_chat_completion(
                messages=messages, temperature=float(temperature), max_tokens=int(max_tokens),
                stream=bool(stream), cache_prompt=True
            )
        except TypeError:
            # build de llama-cpp sin cache_prompt
            return self.model.create_chat_completion(
                messages=messages, temperature=float(temperature), max_tokens=int(max_tokens),
                stream=bool(stream)
            )
        except (RuntimeError, ValueError) as e:
            # Si es desbordamiento de contexto → recortamos y reintentamos
            msg = str(e)
            if "context window" in msg or "exceed context" in msg:
                # Presupuesto:  ctx - salida - margen
                out_tok = max(128, min(256, int(self.ctx * 0.12)))
                budget_in = max(256, self.ctx - out_tok - 64)
                safe_msgs = self._shrink_messages(messages, budget_in_tokens=budget_in)
                try:
                    return self.model.create_chat_completion(
                        messages=safe_msgs, temperature=float(temperature),
                        max_tokens=int(out_tok), stream=bool(stream)
                    )
                except Exception:
                    return {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "[aviso] El contexto era demasiado largo y fue recortado automáticamente. "
                                           "Vuelve a preguntar con una frase más concreta."
                            }
                        }]
                    }
            # Otros errores: propaga
            raise



