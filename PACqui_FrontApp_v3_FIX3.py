
import os, json, threading, tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

from PACqui_FrontApp_v1b import (
    APP_NAME, DEFAULT_DB, DataAccess, ChatFrame,
    ensure_admin_password, admin_login
)
from pacqui_llm_service_FIX3 import LLMService

CONFIG_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "PACqui"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "settings.json"

def _load_cfg():
    if CONFIG_PATH.exists():
        try: return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception: return {}
    return {}

def _save_cfg(d: dict):
    CONFIG_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

class AppRoot(tk.Tk):
    def __init__(self, db_path=None):
        super().__init__()
        self.title(f"{APP_NAME} — PAC QUestioning Inference")
        self.geometry("1180x760")
        try: self.call("tk", "scaling", 1.25)
        except Exception: pass

        self._is_admin = False
        self.data = DataAccess(db_path or DEFAULT_DB)
        self.llm = LLMService(self.data.db_path)

        top = ttk.Frame(self, padding=(10,10,10,6)); top.pack(fill="x")
        ttk.Label(top, text="PACqui", font=("Segoe UI", 16, "bold")).pack(side="left")
        self.lbl_model = ttk.Label(top, text="Modelo: (no cargado)"); self.lbl_model.pack(side="right")
        self.btn_lock = ttk.Button(top, text="🔒 Admin", command=self._toggle_admin); self.btn_lock.pack(side="right", padx=(0,8))
        ttk.Button(top, text="Ayuda", command=lambda: messagebox.showinfo(APP_NAME, "El modelo se gestiona en Admin › Modelo (backend).")).pack(side="right", padx=(0,8))

        self.stack = ttk.Frame(self); self.stack.pack(fill="both", expand=True)
        self.chat = ChatWithLLM(self.stack, self.data, self.llm); self.chat.pack(fill="both", expand=True)
        self.admin = None

        self.footer = ttk.Label(self, anchor="w"); self.footer.pack(fill="x", padx=10, pady=(4,6))
        self._refresh_footer()

        ensure_admin_password(self)
        self._autoload_model()

    def _autoload_model(self):
        cfg = _load_cfg()
        mp = cfg.get("model_path"); ctx = int(cfg.get("model_ctx") or 2048)
        if mp and Path(mp).exists():
            try:
                self.llm.load(mp, ctx=ctx)
                self.lbl_model.config(text=f"Modelo: {Path(mp).name} (ctx={ctx})")
            except Exception as e:
                messagebox.showwarning(APP_NAME, f"No pude auto-cargar el modelo:\n{e}")

    def _toggle_admin(self):
        if not self._is_admin:
            if admin_login(self):
                self._is_admin = True
                self.btn_lock.configure(text="🔓 Admin (activo)")
                if self.admin is None:
                    self.admin = AdminPanel(self.stack, self)
                self.chat.pack_forget(); self.admin.pack(fill="both", expand=True)
        else:
            self._is_admin = False
            self.btn_lock.configure(text="🔒 Admin")
            if self.admin: self.admin.pack_forget()
            self.chat.pack(fill="both", expand=True)
        self._refresh_footer()

    def _refresh_footer(self):
        tables, kw, notes = self.data.stats()
        self.footer.config(text=f"Índice: {Path(self.data.db_path).name} (tablas: {tables}; keywords: {kw}; notas: {notes}) | Admin: {'activo' if self._is_admin else 'bloqueado'}")

class AdminPanel(ttk.Notebook):
    def __init__(self, master, app: AppRoot):
        super().__init__(master); self.app = app
        # Tab índice
        t1 = ttk.Frame(self, padding=12); self.add(t1, text="Índice y herramientas")
        ttk.Label(t1, text="Panel de administración", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Button(t1, text="Abrir herramientas clásicas (Carpeta base…)", command=self._open_legacy).pack(anchor="w", pady=(6,0))

        # Tab modelo backend
        t2 = ttk.Frame(self, padding=12); self.add(t2, text="Modelo (backend)")
        frm = ttk.Frame(t2); frm.pack(anchor="w", fill="x")
        ttk.Label(frm, text="Modelo GGUF:").grid(row=0, column=0, sticky="w")
        self.var_path = tk.StringVar(value=_load_cfg().get("model_path") or "")
        ttk.Entry(frm, textvariable=self.var_path, width=80).grid(row=0, column=1, sticky="we", padx=6, pady=2)
        ttk.Button(frm, text="Elegir…", command=self._choose_model).grid(row=0, column=2, padx=4)
        ttk.Label(frm, text="Contexto:").grid(row=1, column=0, sticky="w")
        self.var_ctx = tk.StringVar(value=str(_load_cfg().get("model_ctx") or 2048))
        ttk.Entry(frm, textvariable=self.var_ctx, width=8).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Button(frm, text="Cargar modelo (backend)", command=self._load_model).grid(row=1, column=2, padx=4)
        frm.columnconfigure(1, weight=1)

        # Tab logs
        t3 = ttk.Frame(self, padding=12); self.add(t3, text="Logs y estado")
        ttk.Label(t3, text="(Próximo) Estado del índice y RAG.").pack(anchor="w")

        # Tab zona peligrosa
        t4 = ttk.Frame(self, padding=12); self.add(t4, text="Zona peligrosa")
        ttk.Label(t4, text="(Próximo) Reset de configuración, vaciados, etc.").pack(anchor="w")

    def _open_legacy(self):
        try:
            import PACqui_RAG_bomba_SAFE as base
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No puedo abrir Admin (Organizador):\n{e}"); return
        top = tk.Toplevel(self); top.title("PACqui — Admin (privado)"); top.geometry("1400x820")
        app = base.OrganizadorFrame(top); app.pack(fill="both", expand=True)
        top.protocol("WM_DELETE_WINDOW", top.destroy)

    def _choose_model(self):
        p = filedialog.askopenfilename(title="Selecciona modelo GGUF", filetypes=[("GGUF","*.gguf"), ("Todos","*.*")])
        if p: self.var_path.set(p)

    def _load_model(self):
        mp = self.var_path.get().strip()
        try: ctx = int(self.var_ctx.get() or "2048")
        except Exception: ctx = 2048
        if not mp:
            messagebox.showinfo(APP_NAME, "Selecciona primero un archivo .gguf"); return
        try:
            self.app.llm.load(mp, ctx=ctx)
            self.app.lbl_model.config(text=f"Modelo: {Path(mp).name} (ctx={ctx})")
            cfg = _load_cfg(); cfg["model_path"]=mp; cfg["model_ctx"]=ctx; _save_cfg(cfg)
            messagebox.showinfo(APP_NAME, "Modelo cargado en backend.")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo cargar el modelo:\n{e}")

class ChatWithLLM(ChatFrame):
    def __init__(self, master, data: DataAccess, llm: LLMService):
        self.llm = llm
        self.notes_only = False  # usamos saludo LLM + observaciones/rutas deterministas
        super().__init__(master, data)

    def _build_ui(self):
        super()._build_ui()
        parent = self.ent_input.master

        # Botón de envío al LLM
        self.btn_llm = ttk.Button(parent, text="Responder con LLM", command=self._send_llm)
        self.btn_llm.pack(side="left", padx=(8, 0))

        # *** NUEVO: spinner indeterminado (se oculta al inicio) ***
        self.pb = ttk.Progressbar(parent, mode="indeterminate", length=120)
        self.pb.pack(side="left", padx=(8, 0))
        self.pb.stop()
        self.pb.pack_forget()

    # --- NUEVO: helpers para el spinner ---
    def _spinner_start(self):
        try:
            self.pb.pack(side="left", padx=(8, 0))
            self.pb.start(12)  # velocidad del spinner
            # Si tu ChatFrame tiene set_status, lo aprovechamos:
            try:
                self.set_status("Generando con el LLM…")
            except Exception:
                pass
        except Exception:
            pass

    def _spinner_stop(self):
        try:
            self.pb.stop()
            self.pb.pack_forget()
            try:
                self.set_status("")  # o restaura tu estado base si lo prefieres
            except Exception:
                pass
        except Exception:
            pass

    def _persona_line_from_llm(self, query_text: str, titles: list[str], n: int) -> str:
        """
        Devuelve UNA frase breve y neutra (cortesía/ayuda). PROHIBIDO aportar datos.
        Si falla el modelo, devuelve un fallback fijo.
        """
        try:
            if not self.llm.is_loaded():
                raise RuntimeError("modelo no cargado")
            # Mensajes: prohibimos contenido factual explícitamente
            system = (
                "Eres un asistente amable. TAREA: redácta UNA sola frase breve de cortesía "
                "para acompañar una lista de rutas que ya se mostrarán aparte. "
                "PROHIBIDO: definiciones, procedimientos, cifras o hechos. "
                "No agregues contenido técnico. No inventes nada. "
                "Longitud máxima: 140 caracteres. Idioma: español."
            )
            user = (
                f"Consulta del usuario: «{query_text}». "
                f"Voy a mostrar {n} fuente(s): {', '.join(titles[:3])}. "
                "Escribe solo la frase de cortesía (sin puntos extra al final)."
            )
            msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
            resp = self.llm.chat(msgs, temperature=0.2, max_tokens=64, stream=False)
            line = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
            line = line.strip().replace("\n", " ")
            # Saneado final: si el modelo se pasa, recortamos.
            if len(line) > 160:
                line = line[:160].rstrip() + "…"
            # Evita que se meta en definiciones por si acaso
            forbid = ("es", "son", "consiste", "significa", "se refiere", "defin")
            if any(w in line.lower() for w in forbid) and "ruta" not in line.lower():
                line = "Aquí tienes las fuentes que encajan. ¿Te abro alguna?"
            return line or "Aquí tienes las fuentes que encajan. ¿Te abro alguna?"
        except Exception:
            return "Aquí tienes las fuentes que encajan. ¿Te abro alguna?"

    def _compose_system_budgeted(self, user_text: str, max_tokens: int = 256):
        base_sys = (
            "Eres PACqui. PRIMERO ofrece 2–3 frases claras para el usuario. "
            "DESPUÉS, si hay [Contexto (índice)], lista rutas (bullets) con nombre y ruta. "
            "Si hay [Contexto del repositorio], explica y cita con [n] y muestra la ruta. "
            "Si el contexto es insuficiente, dilo. No inventes rutas ni datos."
        )

        # Contexto conservador inicial
        idx_top, idx_note_chars = 3, 150
        rag_k, rag_frag_chars = 1, 320

        def shrink(text: str, max_chars: int) -> str:
            t = (text or "").strip()
            return t if len(t) <= max_chars else t[:max_chars].rstrip() + "…"

        def toklen(s: str) -> int:
            try:
                return len(self.llm.model.tokenize(s.encode("utf-8"), add_bos=False)) if self.llm.model else max(1,
                                                                                                                 int(len(
                                                                                                                     s) / 4))
            except Exception:
                return max(1, int(len(s) / 4))

        # Construcción de contextos
        idx_ctx, hits = self.llm.build_index_context(user_text, top_k=idx_top, max_note_chars=idx_note_chars)
        rag_ctx = self.llm._rag_retrieve(user_text, k=rag_k, max_chars=rag_frag_chars)

        def build_system_text():
            parts = [base_sys]
            if idx_ctx: parts += ["[Contexto (índice)]", idx_ctx]
            if rag_ctx: parts += ["[Contexto del repositorio]", rag_ctx]
            return "\n\n".join(parts).strip()

        ctx = int(getattr(self.llm, "ctx", 2048) or 2048)
        sys_full = build_system_text()

        # --- CLAVE: overhead del render de mensajes de llama-cpp ---
        def used_effective():
            # 1.35x de margen + 20 tokens fijos por roles/plantilla
            return int(1.35 * (toklen(sys_full) + toklen(user_text))) + 20

        used = used_effective()
        resp_budget = int(max_tokens)

        # Recorte en bucle hasta que prompt + respuesta <= ctx - 64
        for _ in range(10):
            if used + resp_budget <= ctx - 64:
                break
            # (1) Acorta RAG
            if rag_ctx and len(rag_ctx) > 180:
                rag_frag_chars = max(200, int(rag_frag_chars * 0.7))
                rag_ctx = shrink(rag_ctx, rag_frag_chars)
            # (2) Menos rutas / notas más cortas
            elif hits and idx_top > 1:
                idx_top = max(1, idx_top - 1)
                idx_note_chars = max(100, int(idx_note_chars * 0.8))
                idx_ctx, hits = self.llm.build_index_context(user_text, top_k=idx_top, max_note_chars=idx_note_chars)
            # (3) Baja tokens de respuesta
            elif resp_budget > 64:
                resp_budget = max(64, int(resp_budget * 0.75))
            else:
                break
            sys_full = build_system_text()
            used = used_effective()

        max_final = max(64, min(resp_budget, ctx - used - 64))
        msgs = [{"role": "system", "content": sys_full}, {"role": "user", "content": user_text}]
        return msgs, hits, max_final

    def _send_llm(self):
        text = self.ent_input.get().strip()
        if not text:
            messagebox.showinfo(APP_NAME, "Escribe algo para enviar.")
            return

        # Echo del usuario
        self._append_chat("Tú", text)
        self.ent_input.delete(0, "end")

        # 1) Recupera hits ya (y pinta el panel)
        hits = self._collect_hits(text, top_k=5, note_chars=240)
        self._hits = hits
        try:
            self._fill_sources_tree(hits)
        except Exception:
            pass

        # 2) Construye bloques deterministas (observaciones + rutas)
        obs_block, rutas_block, used_hits = self._build_obs_and_routes_blocks(hits, max_items=3, max_obs_chars=220)

        # Si no hay observaciones, mantenemos tu modo determinista de antes
        if not obs_block:
            self._reply_with_observations(text)
            return

        # 3) Frase humana sólo si el modelo está cargado (y no notes_only)
        if self.llm.is_loaded() and not getattr(self, "notes_only", False):
            self._spinner_start()

            def worker():
                try:
                    titles = [(h.get("name") or h.get("path") or "").strip() for h in used_hits or hits]
                    persona = self._persona_line_from_llm(text, titles, len(titles))
                    final = f"{persona}\n\nObservaciones (índice):\n\n{obs_block}\n\nRutas sugeridas:\n\n{rutas_block}"
                    self.after(0, lambda: self._append_chat("PACqui", final))
                finally:
                    self.after(0, self._spinner_stop)

            threading.Thread(target=worker, daemon=True).start()
            return

        # 4) Si no hay modelo, salida determinista
        final = f"Aquí tienes las fuentes que encajan. ¿Te abro alguna?\n\nObservaciones (índice):\n\n{obs_block}\n\nRutas sugeridas:\n\n{rutas_block}"
        self._append_chat("PACqui", final)

    def _fill_sources_tree(self, hits):
        tv = getattr(self, "tv", None)
        if not tv: return
        tv.delete(*tv.get_children()); self.txt_note.delete("1.0", "end")
        for h in (hits or []):
            keyword_hint = (h.get("keywords") or "").split(";")[0].strip()
            name = os.path.basename(h["path"])
            tv.insert("", "end", values=(keyword_hint, name, h["path"]))

    def _collect_hits(self, query_text: str, top_k: int = 5, note_chars: int = 240):
        try:
            return self.llm._index_hits(query_text, top_k=top_k, max_note_chars=note_chars) or []
        except Exception:
            return []

    def _build_obs_and_routes_blocks(self, hits, max_items: int = 3, max_obs_chars: int = 220):
        """Devuelve (obs_block, rutas_block, hits_usados). Solo coge items con 'note'."""
        used = []
        obs_lines = []
        ruta_lines = []
        for s in hits:
            note = (s.get("note") or "").strip()
            if not note:
                continue
            if len(used) >= max_items:
                break
            if len(note) > max_obs_chars:
                note = note[:max_obs_chars].rstrip() + "…"
            name = s.get("name") or s.get("path") or "(sin nombre)"
            path = s.get("path") or ""
            obs_lines.append(f"- {name}\n  Observaciones: {note}")
            ruta_lines.append(f"- {name}\n  Ruta: {path}")
            used.append(s)
        obs_block = "\n".join(obs_lines).strip()
        rutas_block = "\n".join(ruta_lines).strip()
        return obs_block, rutas_block, used

    def _compose_persona_from_obs(self, user_text: str, max_tokens: int = 192):
        """
        Prepara los mensajes para el LLM usando EXCLUSIVAMENTE las observaciones del índice.
        Si no hay observaciones, el que llama debe caer a _reply_with_observations().
        """
        # 1) Recogemos hits y construimos bloques OBS + RUTAS
        all_hits = self._collect_hits(user_text, top_k=5, note_chars=240)
        # El treeview se actualiza fuera, pero devolvemos también los hits
        obs_block, rutas_block, used_hits = self._build_obs_and_routes_blocks(all_hits, max_items=3, max_obs_chars=220)

        # 2) System: tono persona + prohibición de inventar
        base_sys = (
            "Eres PACqui. Ayudas al usuario con un tono cercano y profesional. "
            "TU ÚNICA FUENTE permitida son las OBSERVACIONES que verás a continuación. "
            "Si algo NO está en esas observaciones, NO lo inventes. "
            "FORMATO DE RESPUESTA: (1) 2–3 frases que respondan a la pregunta usando esas observaciones; "
            "(2) un bloque 'Rutas sugeridas:' con viñetas (nombre y ruta). "
            "Si las observaciones no bastan, dilo explícitamente."
        )

        # 3) Ensamblamos contextos
        parts = [base_sys]
        if obs_block:
            parts += ["[OBSERVACIONES]", obs_block]
        if rutas_block:
            parts += ["[RUTAS]", rutas_block]
        sys_full = "\n\n".join(parts).strip()

        # 4) Presupuesto de tokens (márgen por formato de mensajes)
        def toklen(s: str) -> int:
            try:
                return len(self.llm.model.tokenize(s.encode("utf-8"), add_bos=False)) if self.llm.model else max(1,
                                                                                                                 int(len(
                                                                                                                     s) / 4))
            except Exception:
                return max(1, int(len(s) / 4))

        ctx = int(getattr(self.llm, "ctx", 2048) or 2048)

        def used_effective(sf: str):
            # Overhead de plantillas de llama-cpp (roles, separadores)
            return int(1.30 * (toklen(sf) + toklen(user_text))) + 16

        resp_budget = int(max_tokens)
        used = used_effective(sys_full)

        # 5) Recortamos hasta que quepa: menos obs y más cortas
        max_items, max_obs_chars = 3, 220
        for _ in range(8):
            if used + resp_budget <= ctx - 64:
                break
            if max_obs_chars > 140:
                max_obs_chars = int(max_obs_chars * 0.8)
            elif max_items > 1:
                max_items -= 1
            elif resp_budget > 64:
                resp_budget = max(64, int(resp_budget * 0.75))
            # Recompone bloques con los nuevos límites
            obs_block, rutas_block, used_hits = self._build_obs_and_routes_blocks(all_hits, max_items=max_items,
                                                                                  max_obs_chars=max_obs_chars)
            parts = [base_sys]
            if obs_block: parts += ["[OBSERVACIONES]", obs_block]
            if rutas_block: parts += ["[RUTAS]", rutas_block]
            sys_full = "\n\n".join(parts).strip()
            used = used_effective(sys_full)

        max_final = max(64, min(resp_budget, ctx - used - 64))
        msgs = [{"role": "system", "content": sys_full},
                {"role": "user", "content": user_text}]
        return msgs, used_hits, max_final

    def _reply_with_observations(self, query_text: str):
        """Muestra solo el campo 'observaciones' del índice para los mejores matches.
           No usa el LLM; salida determinista."""
        # 1) Recuperar hits desde el índice (lo mismo que usamos para el panel derecho)
        try:
            hits = self.llm._index_hits(query_text, top_k=5,
                                        max_note_chars=600)  # usa las tablas doc_keywords/doc_notes
        except Exception:
            hits = []

        # 2) Pintar/actualizar 'Fuentes sugeridas'
        try:
            self._fill_sources_tree(hits)
        except Exception:
            pass

        # 3) Construir la respuesta "solo observaciones"
        lines = []
        for s in (hits or []):
            note = (s.get("note") or "").strip()
            if not note:
                continue  # si no hay observación, no lo mostramos
            name = s.get("name") or s.get("path")
            path = s.get("path") or ""
            lines.append(f"• {name}\n  Observaciones: {note}\n  Ruta: {path}")

        if lines:
            msg = "Observaciones (índice):\n\n" + "\n\n".join(lines)
        else:
            # Si no hay observaciones, al menos devolvemos las rutas sugeridas
            alt = []
            for s in (hits or [])[:3]:
                name = s.get("name") or s.get("path")
                path = s.get("path") or ""
                alt.append(f"• {name}\n  Ruta: {path}")
            msg = "No hay observaciones relacionadas en el índice para tu consulta." + \
                  ("\n\nRutas sugeridas:\n\n" + "\n\n".join(alt) if alt else "")

        # 4) Volcar al chat
        self._append_chat("PACqui", msg)

    def _stream_llm_with_fallback(self, messages, max_tokens=256):
        self._append_chat("PACqui", "")
        self._spinner_start()  # <<< arranca el spinner

        def push(txt):
            self.txt_chat.insert("end", txt)
            self.txt_chat.see("end")

        def worker():
            try:
                stream = self.llm.chat(messages, temperature=0.3, max_tokens=max_tokens, stream=True)
                for chunk in stream:
                    token = ""
                    try:
                        ch0 = (chunk.get("choices") or [{}])[0]
                        token = ch0.get("delta", {}).get("content", "") or ch0.get("text", "") or ""
                    except Exception:
                        token = ""
                    if token:
                        self.after(0, push, token)
                self.after(0, push, "\n")
            except Exception as e:
                # Fallback sin stream (y siempre devolvemos "algo")
                try:
                    resp = self.llm.chat(messages, temperature=0.3, max_tokens=max_tokens, stream=False)
                    txt = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "") or \
                          (resp.get("choices") or [{}])[0].get("text", "") or ""
                    if not txt:
                        txt = f"[error] {e}"
                    self.after(0, push, txt + "\n")
                except Exception as ee:
                    self.after(0, push, f"\n[error] {ee}\n")
            finally:
                self.after(0, self._spinner_stop)  # <<< detiene el spinner

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    AppRoot().mainloop()
