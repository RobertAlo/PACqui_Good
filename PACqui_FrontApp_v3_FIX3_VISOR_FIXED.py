
import os, json, threading, tkinter as tk
import time
from datetime import datetime
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from meta_store import MetaStore
from ui_fuentes import SourcesPanel


from PACqui_FrontApp_v1b import (
    APP_NAME, DEFAULT_DB, DataAccess, ChatFrame,
    ensure_admin_password, admin_login
)
from pacqui_llm_service_FIX3 import LLMService
# --- Robust import of organizer module + auto-patch index-context ---

# ---- Carga del Organizador por ruta fija (sin ruidos) ----
def _import_organizador():
    """
    Carga el módulo del Visor aceptando dos nombres:
    - PACqui_RAG_bomba_SAFE.py
    - PACqui_RAG_bomba_SAFE_VISOR.py
    Busca junto al front y en la carpeta padre, archivo .py o paquete (__init__.py).
    """
    import importlib, importlib.util, sys, os

    primary = "PACqui_RAG_bomba_SAFE"
    alt = "PACqui_RAG_bomba_SAFE_VISOR"

    # 1) Import normal por nombre de módulo (por si ya está en sys.path)
    for name in (primary, alt):
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    # 2) Búsqueda controlada en ubicaciones conocidas (sin escanear disco)
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.path.dirname(here)]
    candidates = []
    for root in roots:
        for base in (primary, alt):
            candidates.append(os.path.join(root, f"{base}.py"))
            candidates.append(os.path.join(root, base, "__init__.py"))

    # 3) Cargar por ruta si existe alguno
    for path in candidates:
        if os.path.exists(path):
            name = alt if "SAFE_VISOR" in os.path.basename(path) else primary
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            assert spec.loader is not None
            spec.loader.exec_module(mod)  # type: ignore
            return mod

    # 4) Error claro si no se encontró ninguno
    raise ImportError(
        "No encontré el visor: acepta 'PACqui_RAG_bomba_SAFE.py' o 'PACqui_RAG_bomba_SAFE_VISOR.py' "
        "en esta carpeta o en la carpeta padre (también vale como paquete con __init__.py)."
    )


# ---- Carga del patch del índice y log “RAG monkey…” ----
def _import_rag_patch():
    """
    Importa pacqui_index_context_patch desde módulo o por ruta (junto al front o en la carpeta padre).
    El módulo se auto-aplica al importar. Imprime el log de compatibilidad.
    """
    import importlib, importlib.util, sys, os
    name = "pacqui_index_context_patch"
    try:
        mod = importlib.import_module(name)
        print("RAG monkey-patch listo: OK")
        return mod
    except Exception:
        pass

    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.path.dirname(here)]
    for root in roots:
        path = os.path.join(root, "pacqui_index_context_patch.py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            assert spec.loader is not None
            spec.loader.exec_module(mod)  # type: ignore
            print("RAG monkey-patch listo: OK")
            return mod

    # Si no existe, no bloqueamos el visor
    print("RAG monkey-patch: SKIPPED (no encontrado)")
    return None

# --- Activación diferida del RAG monkey-patch (solo cuando haga falta) ---
_RAG_READY = False

def _ensure_rag_patch():
    """Activa el RAG monkey-patch solo una vez y solo cuando se necesite."""
    global _RAG_READY
    if _RAG_READY:
        return
    try:
        _import_rag_patch()
        _RAG_READY = True
    except Exception as e:
        print(f"[PACqui] RAG monkey-patch omitido: {e}")


def _ensure_organizador_loaded():
    """
    Carga PACqui_RAG_bomba_SAFE sin escanear todo el disco:
    - Import directo si ya está en sys.path
    - Busca SOLO en ubicaciones conocidas (env var + script dir + cwd + hasta 3 padres)
      en formato archivo (.py) o paquete (__init__.py).
    - Si no se encuentra, añade esas raíces a sys.path y reintenta.
    """
    import importlib, importlib.util, sys, os

    modname = "PACqui_RAG_bomba_SAFE"

    # 1) Intento directo
    try:
        return importlib.import_module(modname)
    except Exception:
        pass

    # 2) Raíces candidatas (no recursivo para evitar cuelgues)
    roots = []
    env = os.getenv("PACQUI_ORG_PATH")
    if env and os.path.exists(env):
        roots.append(env)

    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(here)
    roots.append(os.getcwd())
    # hasta 3 padres
    p = here
    for _ in range(3):
        p = os.path.dirname(p)
        if p and p not in roots:
            roots.append(p)

    # 3) Probar archivos directos
    # 3) Probar archivos directos
    candidates = []
    for root in roots:
        for rel in (
            "PACqui_RAG_bomba_SAFE.py",
            os.path.join("PACqui_RAG_bomba_SAFE", "__init__.py"),
            "PACqui_RAG_bomba_SAFE_VISOR.py",
            os.path.join("PACqui_RAG_bomba_SAFE_VISOR", "__init__.py"),
        ):
            cand = os.path.join(root, rel)
            if os.path.exists(cand):
                candidates.append(cand)


    for cand in candidates:
        try:
            spec = importlib.util.spec_from_file_location(modname, cand)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            assert spec.loader is not None
            spec.loader.exec_module(mod)  # type: ignore
            return mod
        except Exception:
            continue

    # 4) Último intento: añadir raíces al sys.path y reimportar
    for root in roots:
        if root not in sys.path:
            sys.path.append(root)
    return importlib.import_module(modname)  # si falla, lanzará ImportError con detalle
# Preload organizer and apply the index-context patch


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
        try:
            self.call("tk", "scaling", 1.25)
        except Exception:
            pass

        self._is_admin = False
        self.data = DataAccess(db_path or DEFAULT_DB)
        self.llm = LLMService(self.data.db_path)

        # --- Event bus (ring buffer de 500 eventos) ---
        self._events = []
        self._event_listeners = []

        # --- UI PRINCIPAL (estaba dentro de subscribe_events por error) ---
        top = ttk.Frame(self, padding=(10, 10, 10, 6))
        top.pack(fill="x")
        ttk.Label(top, text="PACqui", font=("Segoe UI", 16, "bold")).pack(side="left")
        self.lbl_model = ttk.Label(top, text="Modelo: (no cargado)")
        self.lbl_model.pack(side="right")
        self.btn_lock = ttk.Button(top, text="🔒 Admin", command=self._toggle_admin)
        self.btn_lock.pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Visor", command=self._open_viewer).pack(side="right", padx=(0, 8))
        ttk.Button(
            top, text="Ayuda",
            command=lambda: messagebox.showinfo(
                APP_NAME, "El modelo se gestiona en Admin › Modelo (backend)."
            )
        ).pack(side="right", padx=(0, 8))

        self.stack = ttk.Frame(self)
        self.stack.pack(fill="both", expand=True)

        # Nota: pasamos app=self (aunque tu __init__ ya hace fallback al toplevel)
        self.chat = ChatWithLLM(self.stack, self.data, self.llm, app=self)
        self.chat.pack(fill="both", expand=True)
        self.admin = None

        self.footer = ttk.Label(self, anchor="w")
        self.footer.pack(fill="x", padx=10, pady=(4, 6))
        self._refresh_footer()

        ensure_admin_password(self)
        self._autoload_model()

    def _log(self, typ: str, **data):
        ev = {"ts": time.time(), "type": typ}
        ev.update(data or {})
        self._events.append(ev)
        self._events = self._events[-500:]
        for fn in list(self._event_listeners):
            try:
                fn(ev)
            except Exception:
                pass

    def events_snapshot(self):
        return list(self._events)

    def subscribe_events(self, callback):
        self._event_listeners.append(callback)


    def _autoload_model(self):
        cfg = _load_cfg()
        mp = cfg.get("model_path")
        try:
            ctx = int(cfg.get("model_ctx") or 2048)
        except Exception:
            ctx = 2048

        # No hay modelo configurado o no existe
        if not mp or not Path(mp).exists():
            return

        # Si no está instalado llama_cpp en este intérprete, no molestamos al usuario
        try:
            import importlib.util
            if importlib.util.find_spec("llama_cpp") is None:
                print("Auto-carga omitida: 'llama_cpp' no está instalado en este intérprete.")
                return
        except Exception:
            # Si el check falla por cualquier motivo, seguimos con el intento normal
            pass

        try:
            self.llm.load(mp, ctx=ctx)
            self.lbl_model.config(text=f"Modelo: {Path(mp).name} (ctx={ctx})")
        except Exception as e:
            # Log a consola en vez de popup
            print(f"No pude auto-cargar el modelo: {e}")

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

    def _open_viewer(self):
        """Abre el organizador en modo cliente (búsqueda/árbol/preview) y deshabilita 3 controles."""
        import tkinter as tk
        from tkinter import messagebox

        try:
            base = _import_organizador()
            OrganizadorFrame = getattr(base, "OrganizadorFrame")
            # (opcional) aplicar el patch de índice si está presente
            # Aplica el patch (si está); busca junto al front o en la carpeta padre


        except Exception as e:
            messagebox.showerror(APP_NAME, f"No puedo abrir el Visor:\n{e}")
            return

        top = tk.Toplevel(self)
        top.title("PACqui — Visor")
        top.geometry("1400x820")
        visor = OrganizadorFrame(top)
        visor.pack(fill="both", expand=True)

        # Deshabilitar: “PACqui (Asistente)”, “Scraper”, “Simular (dry-run)”
        for name in ("btn_llm", "btn_scraper", "chk_dry_bot"):
            w = getattr(visor, name, None)
            if not w:
                continue
            try:
                w.configure(state="disabled")
            except Exception:
                # Por si no soporta 'state', los ocultamos sin romper layout
                try:
                    w.grid_remove()
                except Exception:
                    try:
                        w.pack_forget()
                    except Exception:
                        pass

        # --- Fallback: desactivar por texto visible (por si cambian los nombres de widget) ---
        try:
            import tkinter as _tk

            def _walk(w):
                for ch in w.winfo_children():
                    yield ch
                    yield from _walk(ch)

            to_disable_btn = ("escanear", "seleccionar carpeta", "eliminar carpeta", "exportar")
            to_disable_chk = ("buscar también en ruta", "dry")

            for w in _walk(visor):
                cls = w.__class__.__name__.lower()
                if "button" in cls:
                    try:
                        txt = (w.cget("text") or "").strip().lower()
                        if any(kw in txt for kw in to_disable_btn):
                            w.configure(state="disabled")
                    except Exception:
                        pass
                elif "checkbutton" in cls:
                    try:
                        txt = (w.cget("text") or "").strip().lower()
                        if any(kw in txt for kw in to_disable_chk):
                            w.configure(state="disabled")
                    except Exception:
                        pass
        except Exception:
            pass


        try:
            top.title("PACqui — Visor (cliente)")
        except Exception:
            pass

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

        # --- NUEVA PESTAÑA: Asistente (backend) ---
        tA = ttk.Frame(self, padding=12)
        self.add(tA, text="Asistente (backend)")

        # Barra superior: cargar índice + abrir fuentes
        bar = ttk.Frame(tA); bar.pack(fill="x")
        ttk.Button(bar, text="Cargar índice (Excel/CSV)…", command=self._import_index_sheet).pack(side="left")
        self.btn_fuentes = ttk.Button(bar, text="Fuentes (0)", command=self._open_fuentes_panel, state="disabled")
        self.btn_fuentes.pack(side="left", padx=8)

        # Asistente embebido (reutiliza ChatWithLLM con el modelo backend)
        self.asst = ChatWithLLM(tA, self.app.data, self.app.llm)
        self.asst.pack(fill="both", expand=True)
        # --- NUEVO: cargar chips al abrir la pestaña y sincronizar el botón "Fuentes (n)" también para búsquedas por chip ---
        try:
            self.asst._load_chips()  # rellena los chips a la izquierda con las keywords del índice
        except Exception:
            pass

        # Cuando el usuario usa chips o "Buscar palabra clave…", se llama a _populate_sources:
        if hasattr(self.asst, "_populate_sources"):
            _orig_pop = self.asst._populate_sources
            def _pop_and_update(text: str):
                _orig_pop(text)
                hits = getattr(self.asst, "_hits", []) or []
                n = len(hits)
                self.btn_fuentes.configure(text=f"Fuentes ({n})", state=("normal" if n else "disabled"))
            self.asst._populate_sources = _pop_and_update


        # Al enviar, refrescamos el contador de fuentes (n) del botón
        _orig_send = self.asst._send_llm
        def _send_and_update():
            _orig_send()
            hits = getattr(self.asst, "_hits", []) or []
            n = len(hits)
            self.btn_fuentes.configure(text=f"Fuentes ({n})", state=("normal" if n else "disabled"))
        self.asst._send_llm = _send_and_update


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
        # Tab logs
        t3 = ttk.Frame(self, padding=10); self.add(t3, text="Logs y estado")
        self._build_logs_tab(t3)

        # Tab zona peligrosa
        t4 = ttk.Frame(self, padding=12); self.add(t4, text="Zona peligrosa")
        ttk.Label(t4, text="(Próximo) Reset de configuración, vaciados, etc.").pack(anchor="w")

    # ---------- LOGS TAB ----------
    def _build_logs_tab(self, parent):
        root = self.app

        cols = ttk.Panedwindow(parent, orient="horizontal"); cols.pack(fill="both", expand=True)

        # IZQUIERDA: Estado índice + modelo + top keywords
        left = ttk.Frame(cols, padding=6); cols.add(left, weight=1)
        ttk.Label(left, text="Estado del índice", style="Header.TLabel").pack(anchor="w")
        self.lbl_idx = ttk.Label(left, text="–"); self.lbl_idx.pack(anchor="w", pady=(0,6))

        ttk.Label(left, text="Estado del modelo", style="Header.TLabel").pack(anchor="w", pady=(6,0))
        self.lbl_llm = ttk.Label(left, text="–"); self.lbl_llm.pack(anchor="w", pady=(0,6))

        ttk.Label(left, text="Top keywords", style="Header.TLabel").pack(anchor="w", pady=(6,0))
        self.tv_top = ttk.Treeview(left, columns=("kw","cnt"), show="headings", height=10)
        self.tv_top.heading("kw", text="keyword"); self.tv_top.heading("cnt", text="n")
        self.tv_top.column("kw", width=220); self.tv_top.column("cnt", width=40, anchor="e")
        self.tv_top.pack(fill="both", expand=False, pady=(2,6))
        self.tv_top.bind("<Double-1>", lambda e: self._open_kw_from_top())

        # CENTRO: consola de eventos
        center = ttk.Frame(cols, padding=6); cols.add(center, weight=3)
        bar = ttk.Frame(center); bar.pack(fill="x")
        ttk.Button(bar, text="Refrescar", command=self._refresh_logs_tab).pack(side="left")
        ttk.Button(bar, text="Exportar log…", command=self._export_log).pack(side="left", padx=6)
        ttk.Button(bar, text="Exportar .jsonl", command=self._export_log_jsonl).pack(side="left", padx=6)
        ttk.Button(bar, text="Limpiar", command=self._clear_log).pack(side="left")
        self.txt_log = tk.Text(center, height=20, wrap="none", font=("Consolas", 10))
        vs = ttk.Scrollbar(center, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=vs.set)
        self.txt_log.pack(side="left", fill="both", expand=True); vs.pack(side="left", fill="y")

        # DERECHA: diagnóstico RAG
        right = ttk.Frame(cols, padding=6); cols.add(right, weight=1)
        ttk.Label(right, text="Diagnóstico rápido", style="Header.TLabel").pack(anchor="w")
        self.var_diag = tk.StringVar()
        ttk.Entry(right, textvariable=self.var_diag, width=28).pack(anchor="w", pady=(2,4))
        ttk.Button(right, text="Probar recuperación", command=self._run_diag).pack(anchor="w")
        # ---- Auto refresh ----
        self.var_auto_logs = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Auto", variable=self.var_auto_logs).pack(anchor="w", pady=(8, 0))
        self.after(2000, self._tick_logs)  # primer “tick” a los 2s

        ttk.Label(right, text="Huecos del índice", style="Header.TLabel").pack(anchor="w", pady=(10,0))
        ttk.Button(right, text="Docs sin observaciones", command=self._list_docs_without_notes).pack(anchor="w")

        # Suscripción a eventos
        try:
            root.subscribe_events(self.on_new_event)
        except Exception:
            pass

        self._refresh_logs_tab()

    def _export_log_jsonl(self):
        p = filedialog.asksaveasfilename(parent=self, title="Exportar log (.jsonl)",
                                         defaultextension=".jsonl",
                                         filetypes=[("JSONL", "*.jsonl"), ("Todos", "*.*")])
        if not p:
            return
        try:
            import json
            with open(p, "w", encoding="utf-8") as f:
                for ev in self.app.events_snapshot():
                    ev2 = dict(ev)
                    ev2["ts_iso"] = datetime.fromtimestamp(ev["ts"]).isoformat(timespec="seconds")
                    f.write(json.dumps(ev2, ensure_ascii=False) + "\n")
            messagebox.showinfo(APP_NAME, f"JSONL exportado en:\n{p}", parent=self)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo exportar:\n{e}", parent=self)

    def _tick_logs(self):
        try:
            if self.var_auto_logs.get():
                self._refresh_logs_tab()
        finally:
            # reprograma el siguiente tick aunque haya habido excepción
            self.after(2000, self._tick_logs)

    def _refresh_logs_tab(self):
        # 1) Estado del índice
        try:
            tables, kw, notes = self.app.data.stats()
            with self.app.data._connect() as con:
                cur = con.cursor()
                cur.execute("SELECT COUNT(DISTINCT lower(fullpath)) FROM doc_keywords")
                docs_kw = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(DISTINCT lower(fullpath)) FROM doc_notes")
                docs_note = cur.fetchone()[0] or 0
            cov_pct = 100.0 * docs_note / max(1, docs_kw)
            cov = f"{cov_pct:.1f}%"
            self.lbl_idx.config(
                text=f"tablas={tables} · keywords={kw} · notas={notes} · docs_kw={docs_kw} · docs_con_nota={docs_note} ({cov})",
                foreground=("red" if cov_pct < 30 else "orange" if cov_pct < 60 else "green")
            )

        except Exception as e:
            self.lbl_idx.config(text=f"(error índice: {e})")

        # 2) Estado del modelo
        try:
            llm = self.app.llm
            if llm and llm.is_loaded():
                mp = Path(getattr(llm, "model_path", "") or "").name
                self.lbl_llm.config(text=f"{mp or '(desconocido)'} · ctx={llm.ctx} · th={llm.threads} · batch={llm.n_batch}")
            else:
                self.lbl_llm.config(text="(no cargado)")
        except Exception as e:
            self.lbl_llm.config(text=f"(error modelo: {e})")

        # 3) Top keywords
        for iid in self.tv_top.get_children():
            self.tv_top.delete(iid)
        try:
            for kw, cnt in (self.app.data.keywords_top(limit=20) or []):
                self.tv_top.insert("", "end", values=(kw, cnt))
        except Exception:
            pass

        # 4) Volcar eventos actuales
        self._reload_console(self.app.events_snapshot())

    def _reload_console(self, events):
        self.txt_log.config(state="normal"); self.txt_log.delete("1.0","end")
        for ev in events[-200:]:
            self._append_console_line(ev)
        self.txt_log.config(state="disabled"); self.txt_log.see("end")

    def on_new_event(self, ev):
        try:
            self.txt_log.config(state="normal")
            self._append_console_line(ev)
            self.txt_log.config(state="disabled"); self.txt_log.see("end")
        except Exception:
            pass

    def _append_console_line(self, ev):
        ts = datetime.fromtimestamp(ev.get("ts", time.time())).strftime("%H:%M:%S")
        typ = ev.get("type","evt")
        detail = {k:v for k,v in ev.items() if k not in ("ts","type")}
        self.txt_log.insert("end", f"[{ts}] {typ}: {detail}\n")

    def _export_log(self):
        p = filedialog.asksaveasfilename(parent=self, title="Exportar log", defaultextension=".txt",
                                         filetypes=[("Texto","*.txt"),("Todos","*.*")])
        if not p: return
        try:
            with open(p,"w",encoding="utf-8") as f:
                for ev in self.app.events_snapshot():
                    ts = datetime.fromtimestamp(ev.get("ts", time.time())).isoformat(timespec="seconds")
                    f.write(f"{ts}  {ev.get('type')}  { {k:v for k,v in ev.items() if k not in ('ts','type')} }\n")
            messagebox.showinfo(APP_NAME, f"Log exportado a:\n{p}", parent=self)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo exportar:\n{e}", parent=self)

    def _clear_log(self):
        self.app._events.clear()
        self._reload_console([])

    def _run_diag(self):
        q = (self.var_diag.get() or "").strip()
        if not q:
            messagebox.showinfo(APP_NAME, "Escribe una consulta de prueba.", parent=self); return
        t0 = time.time()
        try:
            hits = self.app.llm._index_hits(q, top_k=8, max_note_chars=240) or []
        except Exception as e:
            hits = []
            messagebox.showerror(APP_NAME, f"Error en recuperación:\n{e}", parent=self)
        dt = time.time() - t0
        con_nota = sum(1 for h in hits if (h.get("note") or "").strip())
        self.app._log("diag", query=q, hits=len(hits), con_nota=con_nota, ms=int(dt*1000))
        self._refresh_logs_tab()

    def _open_kw_from_top(self):
        sel = self.tv_top.selection()
        if not sel:
            return
        kw = self.tv_top.item(sel[0], "values")[0]
        try:
            # abre la búsqueda en el asistente backend del panel
            self.asst._chip_click(kw)
            self.app._log("diag_kw_open", kw=kw)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No pude abrir la keyword:\n{e}", parent=self)

    def _list_docs_without_notes(self):
        rows=[]
        try:
            with self.app.data._connect() as con:
                cur = con.cursor()
                cur.execute("""
                    SELECT lower(k.fullpath) AS path, MIN(k.keyword)
                    FROM doc_keywords k
                    LEFT JOIN doc_notes n ON lower(n.fullpath)=lower(k.fullpath)
                    WHERE n.fullpath IS NULL
                    GROUP BY lower(k.fullpath)
                    ORDER BY path
                    LIMIT 500
                """)
                rows = cur.fetchall()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"SQL error:\n{e}", parent=self); return
        if not rows:
            messagebox.showinfo(APP_NAME, "¡Todo tiene observaciones! 💪", parent=self); return
        top = tk.Toplevel(self); top.title("Docs sin observaciones"); top.geometry("900x500")
        tv = ttk.Treeview(top, columns=("path","kw"), show="headings")
        tv.heading("path","text"); tv.heading("kw","text")
        tv.column("path", width=700); tv.column("kw", width=160)
        tv.pack(fill="both", expand=True)
        for path, kw in rows:
            tv.insert("", "end", values=(path, kw or ""))


    def _open_legacy(self):
        try:
            base = _import_organizador()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No puedo abrir Admin (Organizador):\n{e}")
            return

        # (opcional) aplicar el mismo patch aquí también
        try:
            _import_rag_patch()
        except Exception:
            pass

        top = tk.Toplevel(self)
        top.title("PACqui — Admin (privado)")
        top.geometry("1400x820")
        app = base.OrganizadorFrame(top)
        app.pack(fill="both", expand=True)
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
            self.app._log("model_loaded",
                          model=os.path.basename(mp),
                          ctx=ctx,
                          threads=self.app.llm.threads,
                          batch=self.app.llm.n_batch)

        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo cargar el modelo:\n{e}")


    # --- ÍNDICE: importar Excel/CSV a SQLite (doc_keywords + doc_notes) ---
    def _import_index_sheet(self):
        from meta_store import MetaStore
        from tkinter import filedialog, messagebox, ttk
        import tkinter as tk

        p = filedialog.askopenfilename(
            parent=self,
            title="Importar índice (Excel/CSV)",
            filetypes=[("Excel/CSV", "*.xlsx *.csv"), ("Excel", "*.xlsx"), ("CSV", "*.csv"), ("Todos", "*.*")]
        )
        if not p:
            return

        # Merge vs Replace
        r = messagebox.askyesnocancel(
            APP_NAME,
            "¿Quieres REEMPLAZAR (Sí) las palabras clave/observaciones de los documentos que aparezcan en el archivo,\n"
            "o MEZCLAR (No) añadiendo las nuevas sin borrar las existentes?\n\n"
            "Sí = REEMPLAZAR  ·  No = MEZCLAR  ·  Cancelar = Abortar",
            parent=self
        )
        if r is None:
            return
        replace_mode = "replace" if r else "merge"

        # Diálogo de progreso
        top = tk.Toplevel(self); top.title("Importando índice…")
        top.transient(self); top.grab_set()
        frm = ttk.Frame(top, padding=12); frm.pack(fill="both", expand=True)
        lbl = ttk.Label(frm, text="Preparando…"); lbl.pack(anchor="w")
        pb = ttk.Progressbar(frm, mode="indeterminate", length=380); pb.pack(fill="x", pady=(8, 0))
        pb.start(12)
        top.update_idletasks()

        # --- helpers definidos ANTES del worker ---
        def _close_top():
            try: pb.stop()
            except Exception: pass
            try: top.destroy()
            except Exception: pass

        def _progress(ev, **kw):
            # callbacks de MetaStore.import_index_sheet
            if ev == "text":
                lbl.config(text=str(kw.get("text", "")))
            elif ev == "total":
                try:
                    total = int(kw.get("total") or 0)
                    if total > 0:
                        pb.config(mode="determinate", maximum=total, value=0)
                        lbl.config(text="Importando filas…")
                except Exception:
                    pass
            elif ev == "tick":
                try:
                    pb.config(mode="determinate")
                    pb["value"] = int(kw.get("done") or 0)
                except Exception:
                    pass

        # --- IMPORTACIÓN EN HILO: capturamos TODO como args por defecto ---
        def worker(replace_mode=replace_mode, progress_cb=_progress, close_top=_close_top, path=p):
            try:
                store = MetaStore(self.app.data.db_path)
                stats = store.import_index_sheet(path, replace_mode=replace_mode,
                                                 progress=progress_cb, progress_every=250)

                # 1) refrescar chips
                self.after(0, lambda: getattr(self, "asst", None) and self.asst._load_chips())

                # 2) auto-demo: lanzar primera keyword top para ver Fuentes al instante
                def _auto_demo():
                    try:
                        klist = self.app.data.keywords_top(limit=1)
                        if klist:
                            self.asst._chip_click(klist[0][0])
                    except Exception:
                        pass
                self.after(0, _auto_demo)
                # 2.5) logging del evento de importación
                try:
                    self.app._log(
                        "index_import",
                        path=path,
                        rows=stats.get("rows", 0),
                        docs=stats.get("docs", 0),
                        kws_added=stats.get("kws_added", 0),
                        notes_set=stats.get("notes_set", 0)
                    )
                except Exception:
                    pass


                # 3) pie de estado + aviso
                self.after(0, self.app._refresh_footer)
                self.after(0, lambda: messagebox.showinfo(
                    APP_NAME,
                    "Índice importado correctamente.\n\n"
                    f"Filas: {stats.get('rows',0)}  ·  Docs: {stats.get('docs',0)}  ·  "
                    f"Kws añadidas: {stats.get('kws_added',0)}  ·  Notas: {stats.get('notes_set',0)}",
                    parent=self
                ))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_NAME, f"Error importando índice:\n{e}", parent=self))
            finally:
                self.after(0, close_top)

        threading.Thread(target=worker, daemon=True).start()



    # --- ÍNDICE: exportar Excel/CSV masivo desde una carpeta base ---
    def _export_index(self):
        from export_massive import run_export_ui
        from tkinter import filedialog, messagebox

        base = filedialog.askdirectory(parent=self, title="Selecciona la CARPETA BASE a exportar", mustexist=True)
        if not base:
            return

        ok, out_path = run_export_ui(self, base_path=base, db_path=self.app.data.db_path, prefer_xlsx=True)
        if ok:
            messagebox.showinfo(APP_NAME, f"Índice exportado a:\n{out_path}", parent=self)
        else:
            messagebox.showerror(APP_NAME, f"No se pudo exportar:\n{out_path}", parent=self)

    def _open_fuentes_panel(self):
        """Abre la ventana de Fuentes usando los hits de ESTE chat."""
        hits = getattr(self, "_hits", []) or []
        if not hits:
            messagebox.showinfo(APP_NAME, "Todavía no hay fuentes para mostrar. Lanza una consulta primero.")
            return

        try:
            pan = SourcesPanel(self)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo abrir el panel de fuentes:\n{e}")
            return

        try:
            pan.update_sources(hits)
        except Exception:
            safe = []
            for h in hits or []:
                safe.append({
                    "path": h.get("path", ""),
                    "name": h.get("name") or (h.get("path") and os.path.basename(h["path"])) or "(sin nombre)",
                    "note": h.get("note", ""),
                    "keywords": h.get("keywords", "")
                })
            try:
                pan.update_sources(safe)
            except Exception:
                pass

        # Intento principal
        try:
            pan.update_sources(hits)
            return
        except Exception:
            pass

        # Fallback si la estructura de 'hits' difiere
        safe = []
        for h in hits:
            safe.append({
                "path": h.get("path", ""),
                "name": h.get("name") or (h.get("path") and os.path.basename(h["path"])) or "(sin nombre)",
                "note": h.get("note", ""),
                "keywords": h.get("keywords", "")
            })
        try:
            pan.update_sources(safe)
        except Exception:
            # Si aun así falla, no rompemos la ventana
            pass

class ChatWithLLM(ChatFrame):
    def __init__(self, master, data: DataAccess, llm: LLMService, app=None):
        self.llm = llm
        self.app = app or master.winfo_toplevel()  # <— referencia al AppRoot
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

    def _import_index_sheet(self):
        """Importa un índice Excel/CSV a SQLite (doc_keywords/doc_notes)."""
        p = filedialog.askopenfilename(
            title="Importar índice (Excel/CSV)",
            filetypes=[("Excel", "*.xlsx *.xls"), ("CSV", "*.csv"), ("Todos", "*.*")]
        )
        if not p:
            return
        # Pequeña ventana de progreso indeterminado
        top = tk.Toplevel(self); top.title("Importando índice…")
        ttk.Label(top, text="Importando índice a la base de datos…").pack(padx=12, pady=(12, 6))
        pb = ttk.Progressbar(top, mode="indeterminate"); pb.pack(fill="x", padx=12, pady=(0, 12)); pb.start(60)
        top.update_idletasks()

        def worker():
            try:
                store = MetaStore(self.app.data.db_path)
                stats = store.import_index_sheet(p, replace_mode=replace_mode, progress=_progress, progress_every=250)

                # --- NUEVO: refrescar chips tras importar
                self.after(0, lambda: getattr(self, "asst", None) and self.asst._load_chips())

                # --- NUEVO: auto-demo → lanza la primera keyword top para rellenar 'Fuentes sugeridas'
                def _auto_demo():
                    try:
                        klist = self.app.data.keywords_top(limit=1)
                        if klist:
                            # simula un clic de chip → rellena tabla de la derecha y actualiza "Fuentes (n)"
                            self.asst._chip_click(klist[0][0])
                    except Exception:
                        pass
                self.after(0, _auto_demo)

                # refresca pie y avisa
                self.after(0, self.app._refresh_footer)
                self.after(0, lambda: messagebox.showinfo(
                    APP_NAME,
                    "Índice importado correctamente.\n\n"
                    f"Filas: {stats.get('rows',0)}  ·  Docs: {stats.get('docs',0)}  ·  "
                    f"Kws añadidas: {stats.get('kws_added',0)}  ·  Notas: {stats.get('notes_set',0)}",
                    parent=self
                ))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_NAME, f"Error importando índice:\n{e}", parent=self))
            finally:
                self.after(0, _close_top)


        threading.Thread(target=worker, daemon=True).start()

    def _open_fuentes_panel(self):
        """Abre la ventana de Fuentes usando los hits de ESTE chat."""
        hits = getattr(self, "_hits", []) or []
        if not hits:
            messagebox.showinfo(APP_NAME, "Todavía no hay fuentes para mostrar. Lanza una consulta primero.")
            return

        try:
            pan = SourcesPanel(self)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo abrir el panel de fuentes:\n{e}")
            return

        try:
            pan.update_sources(hits)
        except Exception:
            safe = []
            for h in hits or []:
                safe.append({
                    "path": h.get("path", ""),
                    "name": h.get("name") or (h.get("path") and os.path.basename(h["path"])) or "(sin nombre)",
                    "note": h.get("note", ""),
                    "keywords": h.get("keywords", "")
                })
            try:
                pan.update_sources(safe)
            except Exception:
                pass

        try:
            pan.update_sources(hits)
        except Exception:
            safe = []
            for h in hits or []:
                safe.append({
                    "path": h.get("path", ""),
                    "name": h.get("name") or (h.get("path") and os.path.basename(h["path"])) or "(sin nombre)",
                    "note": h.get("note", ""),
                    "keywords": h.get("keywords", "")
                })
            try:
                pan.update_sources(safe)
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

        _ensure_rag_patch()  # activa el monkey-patch si aún no está activo

        # 1) Recupera hits ya (y pinta el panel)
        hits = self._collect_hits(text, top_k=5, note_chars=240)
        self._hits = hits
        try:
            self._fill_sources_tree(hits)
        except Exception:
            pass

        # --- LOG de la consulta del asistente (hits, hits con nota, tokens usuario, ctx, etc.) ---
        try:
            tok_user = self.llm.count_tokens(text)
        except Exception:
            tok_user = len(text)

        try:
            # self.app se añade en el punto 5
            if getattr(self, "app", None):
                self.app._log(
                    "assistant_reply",
                    query=text,
                    hits=len(hits or []),
                    hits_con_nota=sum(1 for h in (hits or []) if (h.get('note') or '').strip()),
                    tok_user=tok_user,
                    ctx=self.llm.ctx,
                    llm=self.llm.is_loaded()
                )
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