
# pacqui_index_context_patch.py
# Monkey-patch para LLMChatDialog._worker_chat_stream:
# - Añade [Contexto (índice)] (derivado de doc_keywords/doc_notes) al system real
# - Ajusta la policy para usar índice (rutas) + RAG (citas)
# - No toca tu UI ni tu _send(); actúa justo en el worker de streaming

import os, re
from pathlib import Path

try:
    import PACqui_RAG_bomba_SAFE as base
except Exception as e:
    raise RuntimeError(f"No se pudo importar PACqui_RAG_bomba_SAFE: {e}")

LLMChatDialog = base.LLMChatDialog

# --- REEMPLAZO: compone un [ÍNDICE] compacto y ajusta al presupuesto de tokens ---

def _build_index_context(dialog, user_text: str, top_k: int = 5, max_note_chars: int = 240) -> str:
    """Usa el índice (keywords/observaciones) y devuelve un bloque breve."""
    try:
        hits = dialog._collect_index_hits(user_text, top_k=top_k) or []
    except Exception:
        hits = []
    if not hits:
        return ""
    lines = []
    for s in hits:
        note = (s.get("note") or "").strip()
        if len(note) > max_note_chars:
            note = note[:max_note_chars] + "…"
        kws = (s.get("keywords") or "").strip()
        lines.append(f"- {s.get('name')} · {s.get('path')}\n  Palabras clave: {kws}\n  Observaciones: {note}")
    return "Documentos sugeridos (por palabras clave del índice):\n" + "\n".join(lines)

def _patched_worker_chat_stream(self):
    try:
        temp  = float(self.var_temp.get() or 0.2)
        max_t = int(self.var_maxtok.get() or 160)   # respuesta contenida
        out   = []

        # ---- helpers ----
        def _tok(s: str) -> int:
            try:
                return len(self.model.tokenize(s.encode("utf-8"), add_bos=False))
            except Exception:
                return len(s) // 3  # estimación grosera como último recurso

        def _trim_to_budget(ctx_text: str, budget_tokens: int) -> str:
            """Recorta priorizando el bloque [FRAGMENTOS]."""
            if _tok(ctx_text) <= budget_tokens:
                return ctx_text
            # separa bloques (si hay)
            parts = []
            head, sep, rest = ctx_text.partition("[FRAGMENTOS]")
            if sep:  # hay fragmentos
                frag = "[FRAGMENTOS]" + rest
                # recorta FRAGMENTOS de atrás hacia adelante
                lines = frag.splitlines()
                while lines and _tok(head + "\n".join(lines)) > budget_tokens:
                    lines = lines[:-4]
                parts = [head, "\n".join(lines)]
                ctx_text = "".join(parts)
            # si aún se pasa, recorta líneas del final
            while _tok(ctx_text) > budget_tokens and "\n" in ctx_text:
                ctx_text = "\n".join(ctx_text.splitlines()[:-4])
            return ctx_text

        # ---- construye mensajes con contexto (ÍNDICE + RAG) ----
        def _msgs_with_context():
            # último user
            user_text = ""
            for m in reversed(self.messages):
                if m.get("role") == "user":
                    user_text = m.get("content", "").strip()
                    break

            # RAG del repositorio (si tu app lo expone)
            try:
                rag_ctx = (self.app._retrieve_context(user_text, k=4) or "").strip()
            except Exception:
                rag_ctx = ""

            # ÍNDICE (rutas + notas)
            idx_ctx = ""
            try:
                idx_ctx = (_build_index_context(self, user_text, top_k=5, max_note_chars=240) or "").strip()
            except Exception:
                pass

            # Política clara: citar cuando haya fragmentos; si NO hay fragmentos pero SÍ índice, sugerir rutas;
            # si no hay ni una cosa ni la otra, devolver el mensaje fijo.
            policy = (
                "Eres el asistente de PACqui. Responde SIEMPRE en español. "
                "Usa el CONTEXTO adjunto. Si hay [FRAGMENTOS], fundamenta y CITA con [n]. "
                "Si NO hay [FRAGMENTOS] pero hay [ÍNDICE], limita la respuesta a **sugerir rutas** (lista de viñetas) "
                "sin inventar contenido. Si no hay contexto de ningún tipo, responde exactamente:\n"
                "\"No tengo suficiente contexto en el repositorio. Prueba otra búsqueda o abre una de las rutas sugeridas.\""
            )

            # Ensamble bruto
            contexto = ""
            if idx_ctx:
                contexto += "[ÍNDICE]\n" + idx_ctx + "\n\n"
            if rag_ctx:
                contexto += "[FRAGMENTOS]\n" + rag_ctx + "\n\n"

            # Presupuesto (entrada = ctx_total - salida - margen)
            ctx_max = getattr(self, "ctx", None) or getattr(self.model, "n_ctx", 2048) or 2048
            tok_out = max(128, min(256, int(ctx_max * 0.12)))
            tok_in  = max(256, ctx_max - tok_out - 64)
            contexto = _trim_to_budget(contexto, tok_in)

            # compone mensajes
            msgs = [{"role": "system", "content": policy}]
            msgs += [m for m in self.messages if m.get("role") != "system"]
            # prepend contexto al user actual
            if user_text:
                msgs[-1] = {"role": "user", "content": f"{contexto}PREGUNTA: {user_text}"}
            return msgs, bool(rag_ctx), bool(idx_ctx)

        msgs, has_rag, has_idx = _msgs_with_context()

        # Si no hay NINGÚN contexto, devuelve el mensaje fijo sin llamar al LLM.
        if not has_rag and not has_idx:
            final = "No tengo suficiente contexto en el repositorio. Prueba otra búsqueda o abre una de las rutas sugeridas."
            self.messages.append({"role": "assistant", "content": final})
            fin = self.app._normalize_text(final) if hasattr(self.app, "_normalize_text") else final
            self.after(0, lambda t=fin: self._append_stream_text(t, end_turn=True))
            return

        # Llamada en streaming (tolerando builds sin cache_prompt)
        stream = None
        try:
            try:
                stream = self.model.create_chat_completion(
                    messages=msgs, temperature=temp, max_tokens=max_t, stream=True, cache_prompt=True
                )
            except TypeError:
                stream = self.model.create_chat_completion(
                    messages=msgs, temperature=temp, max_tokens=max_t, stream=True
                )
        except Exception:
            stream = None

        if stream is not None:
            try:
                for chunk in stream:
                    if self.stop_event.is_set():
                        break
                    token = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                    if token:
                        out.append(token)
                        self.after(0, lambda t=token: self._append_stream_text(t, end_turn=False))
            except Exception:
                pass

        final = "".join(out).strip()
        if not final and stream is None:
            try:
                resp = self.model.create_chat_completion(
                    messages=msgs, temperature=temp, max_tokens=max_t, stream=False
                )
                final = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            except Exception:
                final = ""

        if final:
            self.messages.append({"role": "assistant", "content": final})
            fin = self.app._normalize_text(final) if hasattr(self.app, "_normalize_text") else final
            self.after(0, lambda t=fin: self._append_stream_text(t, end_turn=True))
        else:
            if out:
                self.after(0, lambda: self._append_stream_text("\n", end_turn=True))
    except Exception as e:
        self.after(0, lambda: base.messagebox.showerror(base.APP_NAME, f"Error en inferencia:\n{e}"))
    finally:
        self.after(0, lambda: self.btn_stop.configure(state="disabled"))


def apply_index_context_patch():
    # Sustituir el worker de streaming por el parcheado
    LLMChatDialog._worker_chat_stream = _patched_worker_chat_stream
    print("Index-context patch listo: OK")

# Auto-aplica al importar
try:
    apply_index_context_patch()
except Exception as e:
    print("Index-context patch fallo:", e)
