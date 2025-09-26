
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

def _build_index_context(dialog, user_text: str, top_k: int = 8, max_note_chars: int = 480) -> str:
    """Usa el método existente _collect_index_hits() del propio diálogo para obtener fuentes relevantes
    y formatea un bloque textual compacto para el system."""
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
        lines.append(f"- {s.get('name')}  ·  {s.get('path')}\n  Palabras clave: {kws}\n  Observaciones: {note}")
    return "Documentos sugeridos (por palabras clave del índice):\n" + "\n".join(lines)

def _patched_worker_chat_stream(self):
    try:
        temp = float(self.var_temp.get() or 0.3)
        max_t = int(self.var_maxtok.get() or 512)
        out = []

        def _msgs_with_context():
            sys_text = (self.txt_sys.get("1.0", "end") or "").strip()
            user_text = ""
            for m in reversed(self.messages):
                if m.get("role") == "user":
                    user_text = m.get("content", "")
                    break
            # RAG del repositorio (si está disponible en la app)
            try:
                ctx_text = (self.app._retrieve_context(user_text, k=6) or "").strip()
            except Exception:
                ctx_text = ""
            # Contexto (índice) — rutas por keywords
            try:
                idx_ctx = (_build_index_context(self, user_text, top_k=8) or "").strip()
            except Exception:
                idx_ctx = ""

            policy = (
                "Si existe [Contexto (índice)], úsalo para sugerir directamente RUTAS de documentos relevantes "
                "(lista de bullets). Usa el [Contexto del repositorio] para fundamentar contenido y CITAR con "
                "corchetes [n] el fragmento usado, mostrando la ruta del fichero. Si el contexto es insuficiente, "
                "indícalo con claridad y solicita escaneo/precisión. No inventes rutas ni datos."
            )

            parts = []
            if sys_text: parts.append(sys_text)
            parts.append(policy)
            if idx_ctx:
                parts.append("[Contexto (índice)]")
                parts.append(idx_ctx)
            if ctx_text:
                parts.append("[Contexto del repositorio]")
                parts.append(ctx_text)

            sys_full = "\\n\\n".join(parts).strip()
            msgs = [{"role": "system", "content": sys_full}] if sys_full else []
            for m in self.messages:
                if m.get("role") != "system": msgs.append(m)
            return msgs, sys_full, user_text

        msgs, sys_full, user_text = _msgs_with_context()

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
                    try:
                        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                        token = delta.get("content") or ""
                    except Exception:
                        token = ""
                    if token:
                        out.append(token)
                        self.after(0, lambda t=token: self._append_stream_text(t, end_turn=False))
            except Exception:
                pass

        final = "".join(out).strip()
        if not final and stream is None:
            # Fallback a no-stream
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
                self.after(0, lambda: self._append_stream_text("\\n", end_turn=True))
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
