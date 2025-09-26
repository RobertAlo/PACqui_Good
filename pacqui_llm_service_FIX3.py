
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
        if not text: return 0
        try:
            toks = self.model.tokenize(text.encode("utf-8"), add_bos=False) if self.model else []
            return len(toks)
        except Exception:
            # heurística: ~4 chars por token
            return max(1, int(len(text) / 4))

    # --------- índice (keywords/observaciones) ---------
    def _index_hits(self, query: str, top_k: int = 8, max_note_chars: int = 240):
        toks = [t.lower() for t in re.findall(r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ0-9]{3,}", query or "")]
        if not toks: return []
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            cur = con.cursor()
            from collections import defaultdict
            score = defaultdict(int)
            for t in toks:
                cur.execute("SELECT fullpath FROM doc_keywords WHERE lower(keyword) LIKE lower(?) LIMIT 1000", (f"%{t}%",))
                for (fp,) in cur.fetchall():
                    score[os.path.normcase(os.path.normpath(fp))] += 1
            if not score: return []
            top = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1,int(top_k))]
            out = []
            for fp, sc in top:
                name = os.path.basename(fp)
                kws = "; ".join(self._get_keywords(cur, fp))
                note = self._get_note(cur, fp)
                if len(note) > max_note_chars: note = note[:max_note_chars] + "…"
                out.append({"path": fp, "name": name, "score": sc, "keywords": kws, "note": note})
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

    def _rag_retrieve(self, query: str, k: int = 2, max_chars: int = 600) -> str:
        rows = self._rag_rows()
        if not rows: return ""
        def blob_to_vec(b):
            n = len(b) // 4
            return list(struct.unpack('<'+'f'*n, b)) if n>0 else []
        def norm(a):
            s = math.sqrt(sum((x*x for x in a))) or 1.0
            return [x/s for x in a]
        def dot(a,b): return sum((x*y for x,y in zip(a,b)))
        vq = norm(self._hash_embedder(query or "", dim=256))
        scored=[]
        for _cid, txt, fp, blob in rows:
            vv = norm(blob_to_vec(blob)); s = dot(vq, vv)
            frag = (txt or "").strip()
            if len(frag)>max_chars: frag = frag[:max_chars] + "…"
            scored.append((s, frag, fp))
        scored.sort(reverse=True, key=lambda t: t[0])
        top = scored[:max(1,int(k))]
        partes=[]
        for i,(_,frag,fp) in enumerate(top, start=1):
            partes.append(f"[{i}] {fp}\n\"\"\"\n{frag}\n\"\"\"")
        return "\n\n".join(partes) if partes else ""

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
    def chat(self, messages, temperature=0.3, max_tokens=256, stream=True):
        if not self.model:
            raise RuntimeError("Modelo no cargado. Elige un .gguf antes de chatear.")
        try:
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
        except RuntimeError as e:
            # Segunda oportunidad: presupuesto ultra-conservador
            msg = str(e)
            if "Requested tokens" in msg and "context window" in msg:
                safe = max(48, min(96, self.ctx // 16))  # 48–96 como red de seguridad
                try:
                    return self.model.create_chat_completion(
                        messages=messages, temperature=float(temperature), max_tokens=int(safe),
                        stream=bool(stream)
                    )
                except Exception as e2:
                    # Último recurso: devolvemos un mensaje corto para no dejar al usuario "en blanco"
                    return {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "[aviso] El contexto era demasiado largo y fue recortado automáticamente. "
                                           "Vuelve a preguntar con una frase más concreta."
                            }
                        }]
                    }
            raise


