
import os, json, threading, tkinter as tk
import time
import traceback
from datetime import datetime
from tkinter import ttk, messagebox, filedialog, simpledialog
from types import MethodType
from pathlib import Path
from meta_store import MetaStore
from ui_fuentes import SourcesPanel
# ——— INSERTA / ASEGURA ESTE IMPORT ———
from tkinter import messagebox
from path_utils import open_in_explorer

#PACqui

from PACqui_FrontApp_v1b import (
    APP_NAME, DEFAULT_DB, DataAccess, ChatFrame,
    ensure_admin_password, admin_login
)
from pacqui_llm_service_FIX3 import LLMService
# --- Robust import of organizer module + auto-patch index-context ---

# ---- Carga del Organizador por ruta fija (sin ruidos) ----
# --- KW helpers (es) para Históricos ----------------------------------------
import re
SPANISH_STOPWORDS = {
    "de","la","el","los","las","y","o","u","del","al","que","como","cual","cuales",
    "en","con","por","para","entre","sobre","desde","hasta","este","esta","esto",
    "un","una","unos","unas","es","son","se","a","no","si","sin","más","menos"
}
def _kw_slug(s: str) -> str:
    s = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", "-", s.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", s) or "concepto"

def _kw_extract_es(text: str, max_terms: int = 30) -> list[str]:
    toks = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}", (text or "").lower())
    toks = [t for t in toks if t not in SPANISH_STOPWORDS and len(t) >= 3]
    # frecuencia simple
    freq = {}
    for t in toks: freq[t] = freq.get(t, 0) + 1
    out = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for (w,_) in out[:max_terms]]
# ---------------------------------------------------------------------------

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

    # 1) Import directo por nombre (preferimos el VISOR)
    for name in (alt, primary):
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    # 2) Rutas conocidas para búsqueda por fichero/paquete
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.path.dirname(here), os.getcwd()]
    # añade hasta 3 padres
    p = here
    for _ in range(3):
        p = os.path.dirname(p)
        if p and p not in roots:
            roots.append(p)

    candidates = []
    for root in roots:
        for base in (alt, primary):
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


def _import_rag_patch():
    """
    Importa pacqui_index_context_patch desde módulo o por ruta.
    El módulo se auto-aplica al importar.
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
        path = os.path.join(root,
                            "../../OneDrive - HIBERUS SISTEMAS INFORMATICOS S.L/Escritorio/Proyecto actual/pacqui_index_context_patch.py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            assert spec.loader is not None
            spec.loader.exec_module(mod)  # type: ignore
            print("RAG monkey-patch listo: OK")
            return mod

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
        # ← NUEVO: garantizamos que el módulo base exista con ese nombre
        _ensure_organizador_loaded()
    except Exception:
        pass
    try:
        _import_rag_patch()
        _RAG_READY = True
    except Exception as e:
        print(f"[PACqui] RAG monkey-patch omitido: {e}")


def _ensure_organizador_loaded():
    """
    Carga PACqui_RAG_bomba_SAFE (o *_VISOR) sin escanear todo el disco:
    - Import directo si ya está en sys.path
    - Busca SOLO en ubicaciones conocidas (env var + script dir + cwd + hasta 3 padres)
      en formato archivo (.py) o paquete (__init__.py).
    - Si no se encuentra, añade esas raíces a sys.path y reintenta.
    """
    import importlib, importlib.util, sys, os

    modname = "PACqui_RAG_bomba_SAFE"

    # 1) Intento directo
    try:
        import importlib
        importlib.import_module(modname)
        return
    except Exception:
        pass

    # 2) Raíces conocidas
    roots = []
    env = os.getenv("PACQUI_RAG_DIR", "")
    if env: roots.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(here)
    roots.append(os.getcwd())
    p = here
    for _ in range(3):
        p = os.path.dirname(p)
        if p and p not in roots:
            roots.append(p)

    # 3) Probar archivos directos (SAFE y SAFE_VISOR)
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
            return
        except Exception:
            continue

    # 4) Último intento: añadir raíces a sys.path y reimportar
    for r in roots:
        if r not in sys.path:
            sys.path.append(r)
    importlib.import_module(modname)



CONFIG_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "PACqui"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "settings.json"

def _cargar_dataset_evaluacion(self):
    """
    Carga casos de prueba desde SQLite. Ajusta si tu tabla/nombres difieren.
    Espera devolver [{'id': ..., 'query': ...}, ...]
    """
    # Si usas Banco de pruebas, toma 'test_cases(id, q)'
    with self.app.data._connect() as con:
        rows = con.execute("SELECT id, q FROM test_cases ORDER BY id").fetchall()
    return [{"id": r[0], "query": r[1]} for r in rows]

def _guardar_resultado_eval(self, caso: dict, respuesta: str, dt_ms: int = 0):
    """
    Persiste la respuesta del LLM en SQLite. Crea tabla si no existe.
    """
    self._ensure_eval_tables()  # ya la tienes implementada
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with self.app.data._connect() as con:
        con.execute(
            "INSERT INTO eval_llm_responses (ts, case_id, query, response, chars, dt_ms) VALUES (?,?,?,?,?,?)",
            (ts,
             caso.get("id"),
             caso.get("query", ""),
             respuesta,
             len(str(respuesta or "")),
             int(dt_ms or 0))
        )
        con.commit()

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
        self.llm.warmup_async()  # precalienta modelo/índice para evitar lag en la 1ª respuesta

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
        self.btn_help = ttk.Button(top, text="Ayuda", command=self._show_help_contextual)
        self.btn_help.pack(side="right", padx=(0, 8))

        self.stack = ttk.Frame(self)
        self.stack.pack(fill="both", expand=True)

        # Notebook del FRONT: Asistente (público) + Visor (cliente)
        self.nb_front = ttk.Notebook(self.stack)
        self.nb_front.pack(fill="both", expand=True)

        # --- Pestaña 1: Asistente (modo público) ---
        tab_chat = ttk.Frame(self.nb_front, padding=6)
        self.nb_front.add(tab_chat, text="PACqui (Asistente)")
        self.chat = ChatWithLLM(tab_chat, self.data, self.llm, app=self)
        self.chat.pack(fill="both", expand=True)

        # --- Pestaña 2: Visor (cliente) ---
        tab_visor = ttk.Frame(self.nb_front, padding=6)
        self.nb_front.add(tab_visor, text="Visor")
        # … tras self.nb_front.add(tab_visor, text="Visor")
        self.nb_front.bind("<<NotebookTabChanged>>", self._sync_help_button_visibility)
        self._sync_help_button_visibility()
        # Precarga silenciosa del monkey-patch de RAG para evitar bloqueo en el 1er envío
        threading.Thread(target=_ensure_rag_patch, daemon=True).start()

        # Referencias para poder activar la pestaña desde código
        self.tab_visor = tab_visor

        # El Organizador fue diseñado para un Toplevel con .protocol(): emulamos un no-op
        if not hasattr(tab_visor, "protocol"):
            def _noop_protocol(*_a, **_k):
                return None

            tab_visor.protocol = _noop_protocol  # mismo truco que usamos en AdminPanel. :contentReference[oaicite:1]{index=1}

        # Carga del módulo del Visor + (opcional) RAG patch
        try:
            base = _import_organizador()
            try:
                _import_rag_patch()
            except Exception:
                pass
            OrganizadorFrame = getattr(base, "OrganizadorFrame")
            # Visor en modo cliente: oculta botones de LLM/Scraper/dry-run dentro del visor. :contentReference[oaicite:2]{index=2}
            self.visor = OrganizadorFrame(tab_visor, visor_mode=True)
            self.visor.pack(fill="both", expand=True)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo cargar el Visor en la pestaña:\n{e}", parent=self)

        self.admin = None

        self.footer = ttk.Label(self, anchor="w")
        self.footer.pack(fill="x", padx=10, pady=(4, 6))
        self._refresh_footer()

        ensure_admin_password(self)
        self._autoload_model()
        try:
            if hasattr(self, "llm") and self.llm:
                self.llm.warmup_async()
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # tras self._autoload_model() o justo después de crear self.llm
        try:
            import sqlite3
            with sqlite3.connect(self.data.db_path) as con:
                c = con.cursor()
                n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                n_embs = c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            print(f"[RAG] chunks={n_chunks} embeddings={n_embs} en {self.data.db_path}")
        except Exception as e:
            print(f"[RAG] No se pudo comprobar: {e}")

        # ——— ACTIVAR MODO FRONT EN VISOR ———
        self.after(200, self._activar_modo_front_visor)

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

    def _on_close(self):
        try:
            # Cierra modelo LLM si está cargado (libera recursos nativos)
            try:
                mdl = getattr(self.llm, "model", None)
                if mdl is not None:
                    close = getattr(mdl, "close", None)
                    if callable(close):
                        close()
            except Exception:
                pass
            try:
                # Por si acaso, suelta la referencia
                if hasattr(self.llm, "model"):
                    self.llm.model = None
            except Exception:
                pass
        finally:
            try:
                self.destroy()
            except Exception:
                pass

    def events_snapshot(self):
        return list(self._events)

    def post_event(self, ev: dict):
        """Inyecta eventos en el bus central para que los vea el AdminPanel."""
        # Garantiza timestamp y añade al ring buffer
        try:
            ev.setdefault("ts", time.time())
        except Exception:
            pass
        try:
            self._events.append(ev)
            self._events = self._events[-500:]  # mantén el buffer en ~500
            # Notifica a listeners en caliente (si los hay)
            for fn in list(self._event_listeners):
                try:
                    fn(ev)
                except Exception:
                    pass
        except Exception:
            pass

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
            # tras cargar el gguf:
            self.llm.warmup_async()

            # dentro de _autoload_model(), justo después de self.llm.load(...):
            import threading
            threading.Thread(target=self.llm.warmup_async, daemon=True).start()

            # Precalentado en segundo plano (evita el “no responde” del 1er turno)
            try:
                self.after(200, self.llm.warmup_async)
            except Exception:
                pass

            self.lbl_model.config(text=f"Modelo: {Path(mp).name} (ctx={ctx})")
        except Exception as e:
            # Log a consola en vez de popup
            print(f"No pude auto-cargar el modelo: {e}")

    def _toggle_admin(self):
        if not self._is_admin:
            ensure_admin_password(self)
            if admin_login(self):
                self._is_admin = True
                self.btn_lock.configure(text="🔓 Admin (activo)")
                if self.admin is None:
                    self.admin = AdminPanel(self.stack, self)
                # Oculta el front (notebook) y muestra Admin
                if hasattr(self, "nb_front"):
                    self.nb_front.pack_forget()
                self.admin.pack(fill="both", expand=True)
                if not self.btn_help.winfo_manager():
                    self.btn_help.pack(side="right", padx=(0, 8))
        else:
            self._is_admin = False
            self.btn_lock.configure(text="🔒 Admin")
            if self.admin:
                self.admin.pack_forget()
            if hasattr(self, "nb_front"):
                self.nb_front.pack(fill="both", expand=True)
            try:
                self._sync_help_button_visibility()
            except Exception:
                pass
        self._refresh_footer()

    def _open_viewer(self):
        """Compatibilidad: si algún botón antiguo intenta 'abrir el visor',
        simplemente activa la pestaña Visor embebida."""
        try:
            # Si guardaste self.tab_visor (paso 1), úsala directamente:
            self.nb_front.select(self.tab_visor)
            return
        except Exception:
            pass
        # Fallback: localizar la pestaña por su texto ("Visor")
        try:
            for tid in self.nb_front.tabs():
                if (self.nb_front.tab(tid, "text") or "").lower().startswith("visor"):
                    self.nb_front.select(tid)
                    break
        except Exception:
            pass

    def _refresh_footer(self):
            tables, kw, notes = self.data.stats()
            self.footer.config(text=f"Índice: {Path(self.data.db_path).name} (tablas: {tables}; keywords: {kw}; notas: {notes}) | Admin: {'activo' if self._is_admin else 'bloqueado'}")
            # // ================================================================
            # // MODO FRONT PARA VISOR: capar botones de admin por texto visible
            # // ================================================================

    def _activar_modo_front_visor(self):
        """Lanza el capado cuando la UI ya está construida (con reintentos)."""
        try:
            self.unbind_all("<F5>")  # desactiva atajo de escaneo
        except Exception:
            pass

        def tick(tries=[0]):
            toolbar = self._find_toolbar_visor()
            if toolbar is not None:
                self._capar_toolbar_front(toolbar)
                #self._reconfigurar_boton_ayuda(toolbar)
            # Barrido global de respaldo SIEMPRE
            self._capar_toolbar_global()

            tries[0] += 1
            # Reintenta unas cuantas veces por si la barra aparece tarde
            if tries[0] < 6:
                self.after(350, tick)

        self.after(200, tick)

    def _iter_widgets(self, root):
        for w in root.winfo_children():
            yield w
            yield from self._iter_widgets(w)

    def _find_buttons_by_text(self, container, textos):
        encontrados = {}
        for w in self._iter_widgets(container):
            try:
                # ttk.Button o tk.Button
                if w.winfo_class() in ("TButton", "Button"):
                    t = w.cget("text").strip()
                    if t in textos:
                        encontrados[t] = w
            except Exception:
                pass
        return encontrados

    def _find_toolbar_visor(self):
        """
        Encuentra la barra del VISOR buscando un Frame con botones/menubotones cuyo
        texto normalizado contenga 'Buscar' y 'Exportar' (admite 'Exportar ▾').
        Preferimos limitar la búsqueda al contenedor de la pestaña del visor.
        """
        root = getattr(self, "tab_visor", self)

        def norm(txt: str) -> str:
            return (txt or "").replace("…", "").replace("...", "").replace("▾", "").strip().lower()

        BTN_KINDS = ("TButton", "Button", "TMenubutton", "Menubutton")
        for w in self._iter_widgets(root):
            try:
                if w.winfo_class() not in ("TFrame", "Frame", "Labelframe", "TLabelframe"):
                    continue
                labels = []
                for ch in w.winfo_children():
                    if ch.winfo_class() in BTN_KINDS:
                        labels.append(norm(ch.cget("text")))
                if any(t.startswith("exportar") for t in labels) and any(
                        t == "buscar" or t.startswith("buscar") for t in labels):
                    return w
            except Exception:
                pass
        return None

    def _capar_toolbar_front(self, toolbar):
        """
        Elimina del VISOR (front) los botones de administración.
        Mantenemos: Abrir carpeta base, Exportar, Buscar, Limpiar filtros, etc.
        """
        prefijos_a_quitar = (
            "Seleccionar carpeta base",  # admite …/...
            "Eliminar carpeta base",
            "Escanear",  # admite “(F5)”
            "Vaciar resultados",
        )
        BTN_KINDS = ("TButton", "Button", "TMenubutton", "Menubutton")

        # 1) Quita en la toolbar detectada
        for w in list(toolbar.winfo_children()):
            try:
                if w.winfo_class() in BTN_KINDS:
                    t = (w.cget("text") or "").strip()
                    if any(t.startswith(p) for p in prefijos_a_quitar):
                        w.destroy()
            except Exception:
                pass

    def _capar_toolbar_global(self):
        """
        Respaldo: barre TODA la pestaña del visor y elimina los mismos botones
        aunque no hubiéramos localizado la toolbar.
        """
        root = getattr(self, "tab_visor", self)
        prefijos_a_quitar = (
            "Seleccionar carpeta base",
            "Eliminar carpeta base",
            "Escanear",
            "Vaciar resultados",
        )
        BTN_KINDS = ("TButton", "Button", "TMenubutton", "Menubutton")
        for w in list(self._iter_widgets(root)):
            try:
                if w.winfo_class() in BTN_KINDS:
                    t = (w.cget("text") or "").strip()
                    if any(t.startswith(p) for p in prefijos_a_quitar):
                        w.destroy()
            except Exception:
                pass

        # --- Ayuda específica del VISOR (front) ---


    HELP_ASISTENTE_FRONT = """
    ASISTENTE (pestaña pública)

    • ¿Qué puedo escribir?
      Preguntas en lenguaje natural. Ej.: “circular FEAGA pagos”, “solo en pdf”, “limpiar filtros”.

    • Resultados y fuentes
      - El panel de la izquierda muestra chips con palabras clave del índice.
      - Tras enviar, verás arriba una frase breve y, debajo, la lista de fuentes (ruta + nombre).
      - Pulsa “Fuentes (n)” para abrir el panel con el detalle de hits y observaciones.

    • Filtros útiles en el texto
      - “solo en pdf” → limita a .pdf
      - “solo en docx” o “solo en doc” → limita a .docx / .doc
      - “limpiar filtros” → elimina cualquier filtro de extensión activo

    • Botones
      - - Enviar: genera respuesta breve usando el modelo cargado (si está disponible).
      - Solo índice (sin LLM): muestra sólo rutas/observaciones del índice (sin generar texto).
    """.strip()

    def _show_help_contextual(self):
        """Muestra ayuda según el contexto (pestaña actual o Admin)."""
        try:
            # Si está activo Admin, ayuda de backend
            if self._is_admin and self.admin and str(self.admin.winfo_ismapped()) == "1":
                messagebox.showinfo(
                    "Ayuda — Admin (backend)",
                    "En Admin puedes cargar el modelo (Modelo ▸ Cargar), importar/exportar el índice,\n"
                    "ver logs/estado y usar herramientas de mantenimiento.\n\n"
                    "El modelo se gestiona en Admin ▸ Modelo (backend)."
                )
                return
        except Exception:
            pass

        # Si no estamos en Admin, miramos la pestaña del FRONT
        try:
            current = self.nb_front.tab(self.nb_front.select(), "text") or ""
        except Exception:
            current = ""

        t = current.strip().lower()
        if t.startswith("visor"):
            messagebox.showinfo("Ayuda — Visor (Front)", self.HELP_VISOR_FRONT)
        elif "asistente" in t or "pacqui" in t:
            messagebox.showinfo("Ayuda — Asistente (Front)", self.HELP_ASISTENTE_FRONT)
        else:
            # Fallback muy simple
            messagebox.showinfo(
                "Ayuda",
                "Usa PACqui (Asistente) para consultar y la pestaña Visor para explorar resultados.\n"
                "Las acciones de backend (modelo, importación, mantenimiento) están en Admin."
            )

    def _sync_help_button_visibility(self, _evt=None):
        """
        Oculta el botón global 'Ayuda' cuando la pestaña activa es 'Visor',
        y lo muestra en cualquier otra pestaña del FRONT.
        """
        try:
            # Si está visible la vista Admin, el botón global debe verse
            if self._is_admin and self.admin and str(self.admin.winfo_ismapped()) == "1":
                if not self.btn_help.winfo_manager():
                    self.btn_help.pack(side="right", padx=(0, 8))
                return
        except Exception:
            pass

        # Caso FRONT: decidir por pestaña
        try:
            current = self.nb_front.tab(self.nb_front.select(), "text") or ""
        except Exception:
            current = ""

        is_visor = (current.strip().lower().startswith("visor"))
        if is_visor:
            # Ocúltalo si estuviera empaquetado
            if self.btn_help.winfo_manager():
                self.btn_help.pack_forget()
        else:
            # Muéstralo si estuviera oculto
            if not self.btn_help.winfo_manager():
                self.btn_help.pack(side="right", padx=(0, 8))

    """def _reconfigurar_boton_ayuda(self, toolbar):
        Reengancha el botón 'Ayuda' del visor para mostrar el texto capado.
        btns = self._find_buttons_by_text(toolbar, {"Ayuda"})
        btn = btns.get("Ayuda")
        if not btn:
            return
        try:
            # Quita bindings previos y fija command explícito
            btn.unbind("<Button-1>")
        except Exception:
            pass
        btn.configure(command=lambda: messagebox.showinfo("Ayuda — Visor (Front)", self.HELP_VISOR_FRONT))"""

class PinnedSourcesDialog(tk.Toplevel):
    def __init__(self, master, db_path: str, on_change=None):
        super().__init__(master)
        self.title("Fuentes grabadas")
        # geometría inicial y comportamiento de ventana
        self.geometry("1100x520+120+80")
        self.minsize(820, 360)
        self.resizable(True, True)             # ← permite maximizar/minimizar
        try:
            # aseguramos decoraciones "normales" (no toolwindow)
            self.wm_attributes("-toolwindow", False)
        except Exception:
            pass

        # si quieres seguir siendo modal respecto al master:
        #self.transient(master)
        #self.grab_set()

        self.db_path = db_path
        self.on_change = on_change

        # --- backfill de nombres en BD al abrir el visor ---
        from meta_store import MetaStore
        self.ms = MetaStore(self.db_path)             # ← sin dos puntos :)
        try:
            fixed = self.ms.backfill_pinned_names()
            if fixed:
                print(f"[pinned] Nombres corregidos en DB: {fixed}")
        except Exception:
            pass


        # ========= BARRA SUPERIOR =========
        bar = ttk.Frame(self); bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="Refrescar", command=self._reload).pack(side="left")
        ttk.Button(bar, text="Borrar seleccionadas", command=self._delete_selected).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Borrar TODAS", command=self._delete_all).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Editar peso…", command=self._edit_weight).pack(side="left", padx=(6, 0))
        # --- Buscador (% y _ como comodines estilo SQL LIKE) ---
        self.var_find = tk.StringVar()
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(bar, text="Buscar:").pack(side="left", padx=(0, 4))
        ent_find = ttk.Entry(bar, textvariable=self.var_find, width=36)
        ent_find.pack(side="left", padx=(0, 4))
        ent_find.bind("<Return>", lambda e: self._reload())  # Enter para buscar rápido
        ttk.Button(bar, text="Ir", command=self._reload).pack(side="left")
        ttk.Button(bar, text="Limpiar", command=lambda: (self.var_find.set(""), self._reload())).pack(side="left",
                                                                                                      padx=(4, 0))

        ttk.Button(bar, text="Cerrar", command=self.destroy).pack(side="right")

        # ========= ÁREA DE TABLA + SCROLLS =========
        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tv = ttk.Treeview(
            table_frame,
            columns=("name", "path", "note", "weight"),
            show="headings",
            height=18
        )
        # cabeceras
        self.tv.heading("name", text="Nombre")
        self.tv.heading("path", text="Ruta")
        self.tv.heading("note", text="Observaciones")
        self.tv.heading("weight", text="Peso")

        # anchos y estiramiento
        self.tv.column("name", width=260, anchor="w", stretch=True)
        self.tv.column("path", width=540, anchor="w", stretch=True)
        self.tv.column("note", width=260, anchor="w", stretch=True)
        self.tv.column("weight", width=70, anchor="center", stretch=False)

        # scrollbars
        ysb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tv.yview)
        xsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tv.xview)
        self.tv.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        # grid
        self.tv.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        # atajos útiles
        self.bind("<Alt-Return>", self._toggle_max_restore)  # Alt+Enter: maximizar/restaurar
        self.bind("<Control-a>", lambda e: (self.tv.selection_set(*self.tv.get_children()), "break"))

        self._reload()

    def _edit_weight(self):
        sel = self.tv.selection()
        if not sel:
            return
        # Tomamos la primera selección
        item = sel[0]
        name, path, note, cur_w = self.tv.item(item, "values")

        # Diálogo simple
        win = tk.Toplevel(self)
        win.title("Editar peso")
        ttk.Label(win, text=os.path.basename(path) or path).grid(row=0, column=0, columnspan=2, sticky="w", padx=8,
                                                                 pady=(8, 4))
        ttk.Label(win, text="Peso:").grid(row=1, column=0, sticky="e", padx=(8, 4), pady=4)
        var_w = tk.StringVar(value=str(cur_w or "1.0"))
        ent = ttk.Entry(win, textvariable=var_w, width=10);
        ent.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=4)
        ent.focus_set()

        def _ok():
            try:
                w = float(var_w.get())
                if not (0.1 <= w <= 10.0):
                    raise ValueError
            except Exception:
                messagebox.showerror("Peso inválido", "Introduce un valor numérico entre 0.1 y 10.")
                return
            try:
                # Guardamos solo path+weight: MetaStore actualiza por CONFLICT(path)
                self.ms.save_pinned_sources([{"path": path, "weight": w}])
                if callable(self.on_change):
                    self.on_change()
                self._reload()
            except Exception:
                pass
            finally:
                win.destroy()

        ttk.Button(win, text="Cancelar", command=win.destroy).grid(row=2, column=0, padx=8, pady=(4, 8))
        ttk.Button(win, text="Guardar", command=_ok).grid(row=2, column=1, padx=8, pady=(4, 8))
        win.grab_set()

    # ---- maximizar/restaurar con Alt+Enter ----
    def _toggle_max_restore(self, _evt=None):
        try:
            if self.state() == "zoomed":
                self.state("normal")
            else:
                self.state("zoomed")
        except Exception:
            # fallback en plataformas sin 'zoomed'
            w, h = self.winfo_width(), self.winfo_height()
            self.geometry(f"{max(820,w)}x{max(360,h)}+60+40")
        return "break"

    def _like_regex(self, pat: str):
        """Convierte un patrón estilo SQL LIKE (%, _) a regex (case-insensitive)."""
        import re
        p = (pat or "").strip()
        if not p:
            return None
        # 1) Escapa todo
        p = re.escape(p)
        # 2) Restaura comodines de LIKE: % -> .*   _ -> .
        p = p.replace("%", ".*").replace("_", ".")
        p = p.replace(r"\*", ".*")  # permite * como comodín adicional

        return re.compile(p, re.IGNORECASE)

    def _row_matches(self, row: dict, rx) -> bool:
        """Devuelve True si la regex casa con name/path/note."""
        if not rx:
            return True
        return (
                rx.search((row.get("name") or "")) or
                rx.search((row.get("path") or "")) or
                rx.search((row.get("note") or ""))
        )

    def _reload(self):
        # limpia
        for i in self.tv.get_children():
            self.tv.delete(i)

        # recarga desde BD (todas las fuentes)
        try:
            rows = self.ms.list_pinned_sources()
        except Exception:
            rows = []

        # --- filtro por patrón LIKE en memoria ---
        pat = (self.var_find.get() if hasattr(self, "var_find") else "").strip()
        rx = self._like_regex(pat)
        if rx:
            rows = [r for r in rows if self._row_matches(r, rx)]

        # vuelca a la tabla
        import os
        for r in rows:
            name = (r.get("name") or "").strip() or os.path.basename(r.get("path") or "")
            self.tv.insert(
                "", "end",
                values=(name, r.get("path") or "", r.get("note") or "", r.get("weight") or 1.0)
            )

    def _delete_selected(self):
        from tkinter import messagebox
        sel = self.tv.selection()
        if not sel:
            return
        paths = [self.tv.item(i, "values")[1] for i in sel]
        if not messagebox.askyesno("Borrar", f"¿Eliminar {len(paths)} fuente(s) seleccionada(s)?"):
            return
        from meta_store import MetaStore
        n = MetaStore(self.db_path).delete_pinned_sources(paths)
        messagebox.showinfo("Borrar", f"Eliminadas: {n}")
        self._reload()
        if callable(self.on_change):
            self.on_change()

    def _delete_all(self):
        from tkinter import messagebox
        if not messagebox.askyesno("Borrar TODAS", "¿Seguro que quieres borrar TODAS las fuentes grabadas?"):
            return
        from meta_store import MetaStore
        MetaStore(self.db_path).clear_pinned_sources()
        messagebox.showinfo("Borrar", "Fuentes borradas.")
        self._reload()
        if callable(self.on_change):
            self.on_change()





class AdminPanel(ttk.Notebook):
    def __init__(self, master, app: AppRoot):
        super().__init__(master); self.app = app
        # Tab índice
        t1 = ttk.Frame(self, padding=12); self.add(t1, text="Índice y herramientas")
        # === EMBED: Herramientas clásicas dentro de "Índice y herramientas" ===
        try:
            base = _import_organizador()     # Carga módulo del Organizador
            try:
                _import_rag_patch()          # Activa RAG monkey-patch (si está)
            except Exception:
                pass

            # El Organizador espera 'protocol' (propio de Toplevel). Si no existe,
            # le damos uno inofensivo o reutilizamos el del toplevel.
            # El Organizador espera 'protocol' pero t1 no es un Toplevel → no-op
            if not hasattr(t1, "protocol"):
                def _noop_protocol(*_a, **_k):
                    return None

                t1.protocol = _noop_protocol

            # Contenedor y creación del Organizador "versión backend" embebido
            self.organizador_embed = base.OrganizadorFrame(t1, visor_mode=False)
            self.organizador_embed.pack(fill="both", expand=True, pady=(8, 0))

        except Exception as e:
            # Si algo falla, no bloqueamos la app; mostramos el error.
            messagebox.showerror(APP_NAME, f"No se pudo embeber el Organizador en la pestaña:\n{e}", parent=self)


        # --- NUEVA PESTAÑA: Asistente (backend) ---
        tA = ttk.Frame(self, padding=12)
        self.add(tA, text="Asistente (backend)")

        # Barra superior: cargar índice + abrir fuentes
        bar = ttk.Frame(tA); bar.pack(fill="x")
        ttk.Button(bar, text="Cargar índice (Excel/CSV)…", command=self._import_index_sheet).pack(side="left")
        self.btn_fuentes = ttk.Button(bar, text="Fuentes (0)", command=self._open_fuentes_panel, state="disabled")
        self.btn_fuentes.pack(side="left", padx=8)
        self.btn_save_sources = ttk.Button(bar, text="Grabar fuentes", command=self._save_sources, state="disabled")
        self.btn_save_sources.pack(side="left", padx=4)

        self.btn_clear_sources = ttk.Button(bar, text="Ver/Borrar fuentes…",
                                            command=self._open_pinned_sources_viewer)
        self.btn_clear_sources.pack(side="left", padx=4)
        # Pinta el contador de grabadas al abrir la pestaña
        self._refresh_pinned_badge()

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
                st = ("normal" if n else "disabled")
                self.btn_fuentes.configure(text=f"Fuentes ({n})", state=st)
                self.btn_save_sources.configure(state=st)

            self.asst._populate_sources = _pop_and_update


        # Al enviar, refrescamos el contador de fuentes (n) del botón
        _orig_send = self.asst._send_llm
        def _send_and_update():
            _orig_send()
            hits = getattr(self.asst, "_hits", []) or []
            n = len(hits)
            self.btn_fuentes.configure(text=f"Fuentes ({n})", state=("normal" if n else "disabled"))
            st = ("normal" if n else "disabled")
            self.btn_fuentes.configure(text=f"Fuentes ({n})", state=st)
            self.btn_save_sources.configure(state=st)

        self.asst._send_llm = _send_and_update


        # Tab modelo backend
        t2 = ttk.Frame(self, padding=12); self.add(t2, text="Modelo (backend)")
        frm = ttk.Frame(t2); frm.pack(anchor="w", fill="x")
        ttk.Label(frm, text="Modelo GGUF:").grid(row=0, column=0, sticky="w")
        self.var_path = tk.StringVar(value=_load_cfg().get("model_path") or "")
        ttk.Entry(frm, textvariable=self.var_path, width=80).grid(row=0, column=1, sticky="we", padx=6, pady=2)
        ttk.Button(frm, text="Elegir…", command=self._choose_model).grid(row=0, column=2, padx=4)
        ttk.Label(frm, text="Contexto:").grid(row=1, column=0, sticky="w")
        self.var_ctx = tk.StringVar(value=str(_load_cfg().get("model_ctx") or 4096))
        ttk.Entry(frm, textvariable=self.var_ctx, width=8).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Button(frm, text="Cargar modelo (backend)", command=self._load_model).grid(row=1, column=2, padx=4)
        frm.columnconfigure(1, weight=1)

        # Tab modelo de datos (esquema SQLite)
        tSchema = ttk.Frame(self, padding=10);
        self.add(tSchema, text="Modelo de datos")
        self._build_schema_tab(tSchema)

        # Tab logs
        t3 = ttk.Frame(self, padding=10); self.add(t3, text="Logs y estado")
        self._build_logs_tab(t3)

        # Tab zona peligrosa
        t4 = ttk.Frame(self, padding=12); self.add(t4, text="Zona peligrosa")
        self._build_danger_tab(t4)

        # --- NUEVA PESTAÑA: Históricos ---
        tH = ttk.Frame(self, padding=12);
        self.add(tH, text="Históricos")
        self._build_history_tab(tH)

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
        self.txt_console = self.txt_log
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

    def _build_schema_tab(self, parent):
        """
        Explorador de esquema SQLite del índice:
        - Árbol de objetos (tablas, vistas).
        - Detalle de columnas, índices, claves foráneas.
        - SQL DDL con copiar/exportar.
        - Vista de datos (primeras N filas).
        """
        root = self.app

        cols = ttk.Panedwindow(parent, orient="horizontal");
        cols.pack(fill="both", expand=True)

        # ---- IZQUIERDA: Árbol de objetos ----
        left = ttk.Frame(cols, padding=6);
        cols.add(left, weight=1)
        barL = ttk.Frame(left);
        barL.pack(fill="x")
        ttk.Label(barL, text="Objetos").pack(side="left")
        ttk.Button(barL, text="Refrescar", command=lambda: self._schema_reload()).pack(side="right")

        self.tv_schema = ttk.Treeview(left, show="tree", height=24)
        self.tv_schema.pack(fill="both", expand=True, pady=(4, 0))

        # ---- DERECHA: Notebook de detalle ----
        right = ttk.Notebook(cols);
        cols.add(right, weight=3)

        # Pestaña Tabla (columnas + fks)
        t_tab = ttk.Frame(right, padding=6);
        right.add(t_tab, text="Tabla")
        ttk.Label(t_tab, text="Columnas").pack(anchor="w")
        self.tv_cols = ttk.Treeview(t_tab, columns=("name", "type", "notnull", "dflt", "pk"), show="headings", height=8)
        for c, t, w, a in (("name", "Nombre", 220, "w"), ("type", "Tipo", 120, "w"),
                           ("notnull", "NN", 60, "center"), ("dflt", "Defecto", 200, "w"), ("pk", "PK", 60, "center")):
            self.tv_cols.heading(c, text=t);
            self.tv_cols.column(c, width=w, anchor=a)
        self.tv_cols.pack(fill="x", expand=False, pady=(2, 8))

        ttk.Label(t_tab, text="Claves foráneas").pack(anchor="w")
        self.tv_fks = ttk.Treeview(t_tab, columns=("id", "seq", "tbl", "from", "to", "on_upd", "on_del"),
                                   show="headings", height=7)
        for c, t, w in (("id", "id", 50), ("seq", "seq", 50), ("tbl", "tabla ref", 160), ("from", "desde", 140),
                        ("to", "hacia", 140), ("on_upd", "on update", 100), ("on_del", "on delete", 100)):
            self.tv_fks.heading(c, text=t);
            self.tv_fks.column(c, width=w, anchor=("e" if c in ("id", "seq") else "w"))
        self.tv_fks.pack(fill="both", expand=True)

        # Pestaña Índices
        t_idx = ttk.Frame(right, padding=6);
        right.add(t_idx, text="Índices")
        self.tv_idx = ttk.Treeview(t_idx, columns=("name", "unique", "origin", "cols"), show="headings", height=15)
        for c, t, w in (
        ("name", "Índice", 260), ("unique", "Único", 70), ("origin", "Origen", 80), ("cols", "Columnas", 360)):
            self.tv_idx.heading(c, text=t);
            self.tv_idx.column(c, width=w, anchor=("w" if c != "unique" else "center"))
        self.tv_idx.pack(fill="both", expand=True)

        # Pestaña SQL (DDL)
        t_sql = ttk.Frame(right, padding=6);
        right.add(t_sql, text="SQL (DDL)")
        barS = ttk.Frame(t_sql);
        barS.pack(fill="x")
        ttk.Button(barS, text="Copiar", command=lambda: self._schema_copy_sql()).pack(side="left")
        ttk.Button(barS, text="Exportar .sql…", command=lambda: self._schema_export_sql()).pack(side="left", padx=6)
        self.txt_sql = tk.Text(t_sql, height=18, wrap="none", font=("Consolas", 10))
        vs = ttk.Scrollbar(t_sql, orient="vertical", command=self.txt_sql.yview)
        self.txt_sql.configure(yscrollcommand=vs.set)
        self.txt_sql.pack(side="left", fill="both", expand=True);
        vs.pack(side="left", fill="y")

        # Pestaña Datos (preview)
        t_data = ttk.Frame(right, padding=6);
        right.add(t_data, text="Datos")
        barD = ttk.Frame(t_data);
        barD.pack(fill="x")
        ttk.Label(barD, text="Filas:").pack(side="left")
        self.var_rows = tk.IntVar(value=50)
        ttk.Spinbox(barD, from_=1, to=1000, textvariable=self.var_rows, width=6).pack(side="left", padx=4)
        ttk.Button(barD, text="Mostrar", command=lambda: self._schema_show_data()).pack(side="left")
        self.tv_data = ttk.Treeview(t_data, show="headings", height=18)
        self.tv_data.pack(fill="both", expand=True, pady=(6, 0))

        # Pestaña ER (diagrama mini)
        t_er = ttk.Frame(right, padding=6); right.add(t_er, text="ER")
        barE = ttk.Frame(t_er); barE.pack(fill="x")
        ttk.Button(barE, text="Redibujar", command=lambda:self._er_draw()).pack(side="left")
        ttk.Button(barE, text="Autoajustar", command=lambda:self._er_fit()).pack(side="left", padx=6)
        ttk.Button(barE, text="Guardar (.ps)…", command=lambda:self._er_export_ps()).pack(side="left")

        self.var_er_guess = tk.BooleanVar(value=True)
        ttk.Checkbutton(barE, text="Inferir FKs", variable=self.var_er_guess,
                        command=lambda: self._er_draw()).pack(side="left", padx=(8, 0))

        # Canvas con barras de scroll
        wrap = ttk.Frame(t_er); wrap.pack(fill="both", expand=True, pady=(6,0))
        self.cv_er = tk.Canvas(wrap, background="#f8fafc", scrollregion=(0,0,2000,1500), highlightthickness=1, relief="sunken")
        vs_er = ttk.Scrollbar(wrap, orient="vertical", command=self.cv_er.yview)
        hs_er = ttk.Scrollbar(wrap, orient="horizontal", command=self.cv_er.xview)
        self.cv_er.configure(yscrollcommand=vs_er.set, xscrollcommand=hs_er.set)

        wrap.rowconfigure(0, weight=1); wrap.columnconfigure(0, weight=1)
        self.cv_er.grid(row=0, column=0, sticky="nsew")
        vs_er.grid(row=0, column=1, sticky="ns")
        hs_er.grid(row=1, column=0, sticky="ew")

        # Eventos
        self.tv_schema.bind("<<TreeviewSelect>>", lambda e: self._schema_on_select())
        # Carga inicial
        self._schema_reload()

    def _schema_reload(self):
        # Rellena árbol Tablas/Vistas a partir de sqlite_master
        for iid in self.tv_schema.get_children(): self.tv_schema.delete(iid)
        root_tbl = self.tv_schema.insert("", "end", text="Tablas", open=True)
        root_vw = self.tv_schema.insert("", "end", text="Vistas", open=True)
        with self.app.data._connect() as con:
            rows = con.execute("""
                SELECT type, name
                FROM sqlite_master
                WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
            """).fetchall()
        for t, name in rows:
            parent = root_tbl if t == "table" else root_vw
            self.tv_schema.insert(parent, "end", iid=f"{t}:{name}", text=name, open=False)

        # Redibuja el ER con las tablas actuales
        try:
            self._er_draw()
        except Exception:
            pass


    def _schema_on_select(self):
        sel = self.tv_schema.selection()
        if not sel: return
        kind, name = sel[0].split(":", 1) if ":" in sel[0] else ("table", self.tv_schema.item(sel[0], "text"))
        self._schema_fill_table(name, kind)

        # ... ya llamas a _schema_fill_table(...)
        try:
            if kind == "table":
                self._er_highlight(name)
        except Exception:
            pass


    def _schema_fill_table(self, name: str, kind: str = "table"):
        # Columnas
        for iid in self.tv_cols.get_children(): self.tv_cols.delete(iid)
        for iid in self.tv_fks.get_children(): self.tv_fks.delete(iid)
        for iid in self.tv_idx.get_children(): self.tv_idx.delete(iid)
        self.txt_sql.delete("1.0", "end")
        try:
            with self.app.data._connect() as con:
                cols = con.execute(f"PRAGMA table_info('{name}')").fetchall()  # cid, name, type, notnull, dflt, pk
                for _cid, n, t, nn, d, pk in cols:
                    self.tv_cols.insert("", "end",
                                        values=(n, t, "✓" if nn else "", d if d is not None else "", "✓" if pk else ""))
                fks = con.execute(f"PRAGMA foreign_key_list('{name}')").fetchall()
                for (fid, seq, tbl, col_from, col_to, on_upd, on_del, *_rest) in [
                    (r[0], r[1], r[2], r[3], r[4], r[5], r[6], *r[7:]) for r in fks
                ]:
                    self.tv_fks.insert("", "end", values=(fid, seq, tbl, col_from, col_to, on_upd, on_del))
                idxs = con.execute(f"PRAGMA index_list('{name}')").fetchall()  # seq, name, unique, origin, partial
                for _seq, idx_name, uniq, origin, partial in idxs:
                    cols_info = con.execute(f"PRAGMA index_info('{idx_name}')").fetchall()  # seqno, cid, name
                    col_list = ", ".join([r[2] for r in cols_info])
                    self.tv_idx.insert("", "end", values=(idx_name, "✓" if uniq else "", origin, col_list))
                ddl = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
                self.txt_sql.insert("1.0", ddl[0] or "-- (objeto sin SQL explícito)") if ddl else self.txt_sql.insert(
                    "1.0", "-- (no encontrado)")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Modelo de datos", f"Error leyendo esquema de '{name}':\n{e}", parent=self)

    def _schema_copy_sql(self):
        try:
            sql = self.txt_sql.get("1.0", "end").strip()
            if not sql: return
            self.clipboard_clear();
            self.clipboard_append(sql)
        except Exception:
            pass

    def _schema_export_sql(self):
        from tkinter import filedialog, messagebox
        path = filedialog.asksaveasfilename(parent=self, title="Guardar esquema como .sql",
                                            defaultextension=".sql", filetypes=[("SQL", "*.sql"), ("Todos", "*.*")])
        if not path: return
        try:
            with self.app.data._connect() as con:
                rows = con.execute("""
                    SELECT type, name, sql
                    FROM sqlite_master
                    WHERE type IN ('table','index','trigger','view') AND sql IS NOT NULL
                    ORDER BY type, name
                """).fetchall()
            with open(path, "w", encoding="utf-8") as f:
                for t, n, sql in rows:
                    f.write(f"-- {t}: {n}\n{sql};\n\n")
            messagebox.showinfo("Exportar esquema", "Esquema exportado correctamente.", parent=self)
        except Exception as e:
            messagebox.showerror("Exportar esquema", f"No se pudo exportar:\n{e}", parent=self)

    def _schema_show_data(self):
        # Muestra primeras N filas de la tabla seleccionada
        sel = self.tv_schema.selection()
        if not sel: return
        kind, name = sel[0].split(":", 1) if ":" in sel[0] else ("table", self.tv_schema.item(sel[0], "text"))
        if kind != "table": return
        n = int(self.var_rows.get() or 50)

        # limpia tabla de datos
        for iid in self.tv_data.get_children(): self.tv_data.delete(iid)
        # recalcula columnas
        self.tv_data["columns"] = ()
        try:
            with self.app.data._connect() as con:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info('{name}')").fetchall()]
                if not cols: return
                self.tv_data["columns"] = cols
                for c in cols:
                    self.tv_data.heading(c, text=c);
                    self.tv_data.column(c, width=max(80, int(800 / len(cols))), anchor="w")
                rows = con.execute(f"SELECT * FROM '{name}' LIMIT ?", (n,)).fetchall()
            for r in rows:
                self.tv_data.insert("", "end", values=r)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Datos", f"No pude leer datos de '{name}':\n{e}", parent=self)

    # ---------- ER helpers ----------
    def _build_history_tab(self, parent):
        root = self.app
        cols = ttk.Panedwindow(parent, orient="horizontal");
        cols.pack(fill="both", expand=True)

        # ---- IZQ: listado de QA ----
        left = ttk.Frame(cols, padding=6);
        cols.add(left, weight=2)
        bar = ttk.Frame(left);
        bar.pack(fill="x")
        ttk.Label(bar, text="Consultas").pack(side="left")
        self.var_hist_f = tk.StringVar()
        ttk.Entry(bar, textvariable=self.var_hist_f, width=28).pack(side="left", padx=6)
        ttk.Button(bar, text="Buscar", command=lambda: _reload()).pack(side="left")
        ttk.Button(bar, text="Refrescar", command=lambda: _reload(True)).pack(side="right")

        self.tv_hist = ttk.Treeview(left, columns=("id", "ts", "query", "rating"), show="headings", height=18)
        for c, t, w, a in (("id", "id", 60, "e"), ("ts", "fecha/hora", 140, "w"), ("query", "consulta", 520, "w"),
                           ("rating", "★", 60, "center")):
            self.tv_hist.heading(c, text=t);
            self.tv_hist.column(c, width=w, anchor=a)
        self.tv_hist.pack(fill="both", expand=True, pady=(6, 0))
        self.tv_hist.bind("<<TreeviewSelect>>", lambda _e: _load_detail())
        # --- Menú contextual para valorar rápidamente ---
        menu = tk.Menu(left, tearoff=0)

        def _rate_selected(rating:int|None):
            sel = self.tv_hist.selection()
            if not sel:
                return
            qa_id = int(self.tv_hist.item(sel[0], "values")[0])
            if rating is None:
                ms.set_feedback(qa_id, 0, "")  # borra poniendo 0 y nota vacía (ajusta si quieres triestado)
                with self.app.data._connect() as con:
                    con.execute("DELETE FROM qa_feedback WHERE qa_id=?", (qa_id,))
                    con.commit()
            else:
                ms.set_feedback(qa_id, int(rating), self.var_note.get().strip())
            _reload()

        # construye opciones 0..10 y “Borrar valoración”
        for r in range(0, 11):
            menu.add_command(label=f"Valorar {r}", command=lambda rr=r: _rate_selected(rr))
        menu.add_separator()
        menu.add_command(label="Borrar valoración", command=lambda: _rate_selected(None))

        def _show_menu(event):
            try:
                iid = self.tv_hist.identify_row(event.y)
                if iid:
                    self.tv_hist.selection_set(iid)
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        self.tv_hist.bind("<Button-3>", _show_menu)   # clic derecho


        # ---- DER: detalle + valoración ----
        right = ttk.Notebook(cols);
        cols.add(right, weight=3)

        t_det = ttk.Frame(right, padding=8);
        right.add(t_det, text="Detalle")
        self.txt_q = tk.Text(t_det, height=5, wrap="word");
        self.txt_q.pack(fill="x")
        self.txt_a = tk.Text(t_det, height=14, wrap="word");
        self.txt_a.pack(fill="both", expand=True, pady=(6, 0))
        # tras crear self.txt_q y self.txt_a (los Text de Detalle):
        for _t in (self.txt_q, self.txt_a):
            _t.tag_configure("kw", background="#FFF4CC")  # resaltado suave
            _t.tag_configure("kw_active", background="#FFE08A")  # al pasar/usar
            _t.bind("<Button-3>", lambda e, w=_t: self._hist_ctx_on_right_click(e, w))

        row = ttk.Frame(t_det);
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Valoración (0–10):").pack(side="left")
        self.var_rate = tk.IntVar(value=-1)
        ttk.Spinbox(row, from_=0, to=10, textvariable=self.var_rate, width=4).pack(side="left", padx=6)
        self.var_note = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_note, width=60).pack(side="left", padx=6)
        ttk.Button(
            row,
            text="Refrescar KW",
            command=lambda: getattr(self, "_hist_refresh_keywords", lambda: None)()
        ).pack(side="left", padx=(6, 0))

        ttk.Button(row, text="Guardar valoración", command=lambda: _save_rating()).pack(side="right")
        #ttk.Button(bar_detalle, text="Refrescar KW", command=self._hist_refresh_keywords).pack(side="left", padx=(6, 0))

        # ---- Subpestaña Conceptos (CRUD) ----
        t_con = ttk.Frame(right, padding=8);
        right.add(t_con, text="Conceptos")
        upper = ttk.Frame(t_con);
        upper.pack(fill="x")
        ttk.Label(upper, text="Buscar:").pack(side="left")
        self.var_con_f = tk.StringVar();
        ttk.Entry(upper, textvariable=self.var_con_f, width=30).pack(side="left", padx=6)
        ttk.Button(upper, text="Buscar", command=lambda: _reload_concepts()).pack(side="left")
        ttk.Button(upper, text="Nuevo", command=lambda: _edit_concept(None)).pack(side="left", padx=6)
        ttk.Button(upper, text="Refrescar", command=lambda: _reload_concepts()).pack(side="left", padx=6)
        ttk.Button(upper, text="Importar desde keywords…", command=lambda: _import_concepts_from_kws()).pack(
            side="left", padx=6)

        def _import_concepts_from_kws():
            ms = MetaStore(self.app.data.db_path)
            n = ms.bootstrap_concepts_from_keywords(limit=500)  # ajusta el límite si quieres
            messagebox.showinfo(APP_NAME, f"Importados {n} conceptos desde keywords.")
            _reload_concepts()

        self.tv_con = ttk.Treeview(t_con, columns=("id", "slug", "title", "tags"), show="headings", height=14)
        for c, t, w, a in (
        ("id", "id", 60, "e"), ("slug", "slug", 180, "w"), ("title", "título", 340, "w"), ("tags", "tags", 220, "w")):
            self.tv_con.heading(c, text=t);
            self.tv_con.column(c, width=w, anchor=a)
        self.tv_con.pack(fill="both", expand=True, pady=(6, 4))
        self.tv_con.bind("<Double-1>", lambda _e: _edit_concept(_sel_con()))

        # --- menú contextual en la tabla Conceptos ---
        menu_con = tk.Menu(t_con, tearoff=0)

        def _del_concept():
            cid = _sel_con()
            if not cid:
                return
            if not messagebox.askyesno(APP_NAME, "¿Eliminar el concepto seleccionado?"):
                return
            MetaStore(self.app.data.db_path).delete_concept(cid)
            _reload_concepts()

        menu_con.add_command(label="Editar…", command=lambda: _edit_concept(_sel_con()))
        menu_con.add_command(label="Eliminar", command=_del_concept)
        menu_con.add_separator()
        menu_con.add_command(label="Nuevo", command=lambda: _edit_concept(None))
        menu_con.add_command(label="Refrescar", command=lambda: _reload_concepts())

        def _show_con_menu(e):
            iid = self.tv_con.identify_row(e.y)
            if iid:
                self.tv_con.selection_set(iid)
            try:
                menu_con.tk_popup(e.x_root, e.y_root)
            finally:
                menu_con.grab_release()

        self.tv_con.bind("<Button-3>", _show_con_menu)

        # --- helpers de datos ---
        ms = MetaStore(root.data.db_path)

        def _reload(force=False):
            for iid in self.tv_hist.get_children(): self.tv_hist.delete(iid)
            rows = ms.list_qa(self.var_hist_f.get().strip() or None, limit=400)
            for r in rows:
                self.tv_hist.insert("", "end",
                                    values=(r["id"], r["ts"], r["query"], ("" if r["rating"] < 0 else r["rating"])))

        def _load_detail():
            sel = self.tv_hist.selection()
            if not sel: return
            qa_id = int(self.tv_hist.item(sel[0], "values")[0])
            qa = ms.get_qa(qa_id)
            self.txt_q.delete("1.0", "end");
            self.txt_q.insert("1.0", qa["query"] or "")
            self.txt_a.delete("1.0", "end");
            self.txt_a.insert("1.0", qa["answer"] or "")
            self.var_rate.set(-1)
            self.var_note.set("")
            try:
                with root.data._connect() as con:
                    rat = con.execute("SELECT rating, notes FROM qa_feedback WHERE qa_id=?", (qa_id,)).fetchone()
                if rat:
                    self.var_rate.set(int(rat[0]));
                    self.var_note.set(rat[1] or "")
            except Exception:
                pass
            self._hist_refresh_keywords()


        def _save_rating():
            sel = self.tv_hist.selection()
            if not sel: return
            qa_id = int(self.tv_hist.item(sel[0], "values")[0])
            ms.set_feedback(qa_id, int(self.var_rate.get()), self.var_note.get().strip())
            _reload()

        # ---- Conceptos: CRUD mínimo ----
        def _sel_con():
            s = self.tv_con.selection()
            return int(self.tv_con.item(s[0], "values")[0]) if s else None

        def _reload_concepts():
            for iid in self.tv_con.get_children(): self.tv_con.delete(iid)
            for c in ms.list_concepts(self.var_con_f.get().strip() or None, limit=500):
                self.tv_con.insert("", "end", values=(c["id"], c["slug"], c["title"], c.get("tags", "") or ""))

        def _edit_concept(cid):
            top = tk.Toplevel(self)
            top.title("Concepto")
            top.grab_set()

            frm = ttk.Frame(top, padding=10)
            frm.pack(fill="both", expand=True)

            vars = {k: tk.StringVar() for k in ("slug", "title", "tags", "aliases")}
            txt_body = tk.Text(frm, height=10, wrap="word")

            # --- Fuentes por concepto (UI simple) ---
            src_vars = {
                "path": tk.StringVar(),
                "weight": tk.StringVar(value="1.2"),
                "note": tk.StringVar(),
            }

            src_frame = ttk.Labelframe(frm, text="Fuentes (ruta + peso + nota)", padding=8)
            tv_src = ttk.Treeview(src_frame, columns=("path", "weight", "note"), show="headings", height=6)
            tv_src.heading("path", text="Ruta")
            tv_src.heading("weight", text="Peso")
            tv_src.heading("note", text="Nota")
            tv_src.column("path", width=560, anchor="w")
            tv_src.column("weight", width=60, anchor="center")
            tv_src.column("note", width=220, anchor="w")

            def _browse_path():
                p = filedialog.askopenfilename(parent=top, title="Selecciona un archivo")
                if p:
                    src_vars["path"].set(p)

            def _add_src():
                p = (src_vars["path"].get() or "").strip()
                if not p:
                    _browse_path()
                    p = (src_vars["path"].get() or "").strip()
                if not p:
                    return
                try:
                    w = float(src_vars["weight"].get() or "1.2")
                except Exception:
                    w = 1.2
                    src_vars["weight"].set("1.2")
                note = (src_vars["note"].get() or "").strip()
                # evita duplicados por ruta
                for iid in tv_src.get_children():
                    if tv_src.set(iid, "path").lower() == p.lower():
                        tv_src.set(iid, "weight", str(w))
                        tv_src.set(iid, "note", note)
                        return
                tv_src.insert("", "end", values=(p, f"{w:.2f}", note))

            def _del_src():
                sel = tv_src.selection()
                for iid in sel:
                    tv_src.delete(iid)

            # --- Carga/guardado ---
            def _load():
                if not cid:
                    return
                with root.data._connect() as con:
                    r = con.execute("SELECT slug,title,body,tags FROM concepts WHERE id=?", (cid,)).fetchone()
                vars["slug"].set(r[0])
                vars["title"].set(r[1])
                txt_body.insert("1.0", r[2])
                vars["tags"].set(r[3] or "")
                with root.data._connect() as con:
                    als = [a[0] for a in con.execute(
                        "SELECT alias FROM concept_alias WHERE concept_id=?", (cid,)).fetchall()]
                vars["aliases"].set(", ".join(als))

                # fuentes del concepto
                try:
                    from meta_store import MetaStore
                    ms = MetaStore(self.app.data.db_path)
                    for it in ms.list_concept_sources(cid):
                        tv_src.insert("", "end", values=(it["path"], f'{float(it["weight"]):.2f}', it.get("note") or ""))
                except Exception:
                    pass

            def _save():
                aliases = [a.strip() for a in (vars["aliases"].get() or "").split(",") if a.strip()]
                from meta_store import MetaStore
                ms = MetaStore(self.app.data.db_path)

                # 1) upsert del concepto (devuelve id)
                new_id = ms.upsert_concept(
                    vars["slug"].get(), vars["title"].get(),
                    txt_body.get("1.0", "end").strip(),
                    vars["tags"].get(), aliases=aliases, concept_id=cid
                )

                # 2) recopilar fuentes de la tabla y persistir (replace=True)
                items = []
                for iid in tv_src.get_children():
                    vals = tv_src.item(iid, "values")
                    items.append({
                        "path": vals[0],
                        "weight": float(vals[1]),
                        "note": vals[2],
                    })
                ms.save_concept_sources(new_id, items, replace=True)

                top.destroy()
                _reload_concepts()

            # --- Campos superiores ---
            row = ttk.Frame(frm); row.pack(fill="x")
            ttk.Label(row, text="slug:").pack(side="left")
            ttk.Entry(row, textvariable=vars["slug"], width=32).pack(side="left", padx=6)
            ttk.Label(row, text="título:").pack(side="left")
            ttk.Entry(row, textvariable=vars["title"], width=48).pack(side="left", padx=6)

            row2 = ttk.Frame(frm); row2.pack(fill="x", pady=(6, 0))
            ttk.Label(row2, text="tags:").pack(side="left")
            ttk.Entry(row2, textvariable=vars["tags"], width=40).pack(side="left", padx=6)
            ttk.Label(row2, text="alias (coma):").pack(side="left")
            ttk.Entry(row2, textvariable=vars["aliases"], width=40).pack(side="left", padx=6)

            # --- Fuentes (tabla + editor de línea) ---
            src_frame.pack(fill="both", expand=True, pady=(8, 6))
            tv_src.pack(fill="both", expand=True, side="top")

            editor = ttk.Frame(src_frame); editor.pack(fill="x", pady=(6, 0))
            ttk.Label(editor, text="Ruta:").pack(side="left")
            ttk.Entry(editor, textvariable=src_vars["path"], width=55).pack(side="left", padx=4)
            ttk.Button(editor, text="Examinar…", command=_browse_path).pack(side="left")
            ttk.Label(editor, text="Peso:").pack(side="left", padx=(12, 0))
            ttk.Entry(editor, textvariable=src_vars["weight"], width=6).pack(side="left", padx=4)
            ttk.Label(editor, text="Nota:").pack(side="left", padx=(12, 0))
            ttk.Entry(editor, textvariable=src_vars["note"], width=28).pack(side="left", padx=4)
            ttk.Button(editor, text="Añadir/Actualizar", command=_add_src).pack(side="right")
            ttk.Button(editor, text="Quitar seleccionados", command=_del_src).pack(side="right", padx=(0, 6))

            # --- Cuerpo del concepto ---
            ttk.Label(frm, text="Cuerpo").pack(anchor="w", pady=(6, 0))
            txt_body.pack(fill="both", expand=True)

            # --- Botonera ---
            btns = ttk.Frame(frm); btns.pack(fill="x", pady=(8, 0))
            ttk.Button(btns, text="Guardar", command=_save).pack(side="right")
            ttk.Button(btns, text="Cancelar", command=lambda: top.destroy()).pack(side="right", padx=8)

            _load()


        # Arranque
        _reload();
        _reload_concepts()

        # --- Históricos: extraer, resaltar y menú contextual ------------------------
        def _hist_refresh_keywords(self):
            try:
                q = self.txt_q.get("1.0", "end-1c")
                a = self.txt_a.get("1.0", "end-1c")
            except Exception:
                return
            # limpia marcas previas
            for t in (self.txt_q, self.txt_a):
                try:
                    t.tag_remove("kw", "1.0", "end")
                    t.tag_remove("kw_active", "1.0", "end")
                except Exception:
                    pass
            terms = list(dict.fromkeys(_kw_extract_es(q + " " + a, max_terms=30)))
            for widget in (self.txt_q, self.txt_a):
                start = "1.0"
                while True:
                    # buscamos cada término por separado
                    found_any = False
                    for term in terms:
                        pos = widget.search(rf"\m{term}\M", start, "end", nocase=True, regexp=True)
                        if pos:
                            end = f"{pos}+{len(term)}c"
                            widget.tag_add("kw", pos, end)
                            start = end
                            found_any = True
                            break
                    if not found_any:
                        break

        def _hist_ctx_on_right_click(self, event, widget):
            index = widget.index(f"@{event.x},{event.y}")
            tags = widget.tag_names(index)
            if "kw" not in tags:
                return  # no keyword debajo del cursor

            # delimitar palabra bajo cursor
            word_start = widget.search(r"\m", index, backwards=True, regexp=True) or index
            word_end = widget.search(r"\M", index, regexp=True) or index
            term = widget.get(word_start, word_end).strip()
            if not term:
                return

            menu = tk.Menu(widget, tearoff=0)
            menu.add_command(label=f"Crear/editar concepto «{term}»",
                             command=lambda t=term: self._hist_open_concept_from_term(t))
            menu.add_command(label="Añadir fuente (ruta → concepto)…",
                             command=lambda t=term: self._hist_add_source_to_concept(t))
            menu.add_command(label="Añadir alias…",
                             command=lambda t=term: self._hist_add_alias_to_concept(t))
            menu.add_separator()
            menu.add_command(label="Eliminar concepto…",
                             command=lambda t=term: self._hist_delete_concept(t))
            menu.add_separator()
            menu.add_command(label="Preguntar al chat (Solo índice)",
                             command=lambda t=term: self._hist_send_only_index(t))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def _hist_find_concept(self, term: str):
            ms = MetaStore(self.app.data.db_path)
            slug = _kw_slug(term)
            c = ms.get_concept_by_slug(slug)
            if c:
                return c
            found = ms.list_concepts(term, limit=1)
            return found[0] if found else None

        def _hist_open_concept_from_term(self, term: str):
            ms = MetaStore(self.app.data.db_path)
            c = self._hist_find_concept(term)
            if not c:
                slug = _kw_slug(term)
                title = term.capitalize()
                cid = ms.upsert_concept(slug, title, body="", tags=term, aliases=[], concept_id=None)
                c = ms.get_concept(cid)
            # Llama a la función local _edit_concept, no a un método inexistente:
            _edit_concept(c["id"])

        def _hist_add_source_to_concept(self, term: str):
            ms = MetaStore(self.app.data.db_path)
            c = self._hist_find_concept(term)
            if not c:
                # créalo al vuelo
                slug = _kw_slug(term);
                title = term.capitalize()
                cid = ms.upsert_concept(slug, title, body="", tags=term, aliases=[], concept_id=None)
                c = ms.get_concept(cid)
            path = filedialog.askopenfilename(title="Selecciona la fuente (documento)")
            if not path: return
            try:
                ms.save_concept_sources(
                    c["id"],
                    [{"path": path, "weight": 1.4, "note": f"desde histórico: {term}"}]
                )
                # peso alto
                messagebox.showinfo(APP_NAME, "Fuente añadida al concepto.")
                # si tienes lista de conceptos en la UI, refresca:
                if hasattr(self, "_refresh_concepts_list"): self._refresh_concepts_list()
            except Exception as e:
                messagebox.showerror(APP_NAME, f"No se pudo guardar la fuente:\n{e}")

        def _hist_add_alias_to_concept(self, term: str):
            ms = MetaStore(self.app.data.db_path)
            c = self._hist_find_concept(term)
            if not c:
                messagebox.showwarning(APP_NAME, "Primero crea el concepto.")
                return
            alias = simpledialog.askstring("Alias", "Nuevo alias (usa coma para varios):", parent=self)
            if not alias: return
            aliases = [a.strip() for a in alias.split(",") if a.strip()]
            try:
                ms.upsert_concept(c["slug"], c["title"], c.get("body", ""), c.get("tags", ""),
                                  aliases=aliases, concept_id=c["id"])
                messagebox.showinfo(APP_NAME, "Alias guardado.")
            except Exception as e:
                messagebox.showerror(APP_NAME, f"No se pudo guardar el alias:\n{e}")

        def _hist_delete_concept(self, term: str):
            ms = MetaStore(self.app.data.db_path)
            c = self._hist_find_concept(term)
            if not c:
                messagebox.showinfo(APP_NAME, "No existe concepto para ese término.")
                return
            if not messagebox.askyesno(APP_NAME, f"¿Eliminar el concepto «{c['title']}» y sus fuentes/alias?"):
                return
            try:
                ms.delete_concept(c["id"])
                messagebox.showinfo(APP_NAME, "Concepto eliminado.")
                if hasattr(self, "_refresh_concepts_list"): self._refresh_concepts_list()
            except Exception as e:
                messagebox.showerror(APP_NAME, f"No se pudo eliminar:\n{e}")

        def _hist_send_only_index(self, term: str):
            # Lanza el término al chat con “Solo índice (sin LLM)”
            try:
                self.asst.var_notes_only.set(True)  # activa el check
                self.asst.ent_input.delete(0, "end")
                self.asst.ent_input.insert(0, term)
                self.asst._on_send()
            except Exception as e:
                messagebox.showerror(APP_NAME, f"No se pudo enviar al chat:\n{e}")
        # ---------------------------------------------------------------------------
        # === ENLACE DE HELPERS A self (imprescindible) ===
        from types import MethodType
        self._hist_refresh_keywords = MethodType(_hist_refresh_keywords, self)
        self._hist_ctx_on_right_click = MethodType(_hist_ctx_on_right_click, self)
        self._hist_find_concept = MethodType(_hist_find_concept, self)
        self._hist_open_concept_from_term = MethodType(_hist_open_concept_from_term, self)
        self._hist_add_source_to_concept = MethodType(_hist_add_source_to_concept, self)
        self._hist_add_alias_to_concept = MethodType(_hist_add_alias_to_concept, self)
        self._hist_delete_concept = MethodType(_hist_delete_concept, self)
        self._hist_send_only_index = MethodType(_hist_send_only_index, self)


        # Para poder refrescar la tabla de conceptos desde esos helpers:
        self._refresh_concepts_list = _reload_concepts

    def _er_collect(self):
        """
        Devuelve:
          tables: {tabla: [{name,type,pk}, ...]}
          fks: [(src_tbl, src_col, dst_tbl, dst_col, kind)]  # kind in {"decl","guess"}
        1) Lee FKs declaradas (PRAGMA foreign_key_list)
        2) Si var_er_guess está activa, añade FKs inferidas por heurística:
           - columnas *_id -> <tabla>.id (singular/plural simple)
           - columnas fullpath -> files.fullpath (si existe)
        """

        def singular_candidates(x: str):
            x = x.lower()
            cand = {x, x + "s", x + "es"}
            if x.endswith("s"): cand.add(x[:-1])  # usuarios -> usuario
            if x.endswith("es"): cand.add(x[:-2])  # indices -> indice
            # Mapeos específicos del proyecto:
            if x == "case": cand.add("test_cases")
            if x == "chunk": cand.add("chunks")
            return list(cand)

        tables = {}
        fks = []
        with self.app.data._connect() as con:
            # Tablas (sin sqlite_*)
            rows = con.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """).fetchall()
            table_names = [r[0] for r in rows]

            # Columnas por tabla
            cols_by_tbl = {}
            for name in table_names:
                cols = con.execute(f"PRAGMA table_info('{name}')").fetchall()  # cid, name, type, notnull, dflt, pk
                tables[name] = [{"name": r[1], "type": r[2], "pk": bool(r[5])} for r in cols]
                cols_by_tbl[name] = {r[1].lower() for r in cols}

            # 1) FKs declaradas
            for name in table_names:
                fkl = con.execute(f"PRAGMA foreign_key_list('{name}')").fetchall()
                for r in fkl:
                    # r: (id, seq, table, from, to, on_update, on_delete, match)
                    fks.append((name, r[3], r[2], r[4], "decl"))

        # 2) FKs inferidas (opcional)
        if getattr(self, "var_er_guess", None) and self.var_er_guess.get():
            declared = {(a.lower(), b.lower(), c.lower(), d.lower()) for (a, b, c, d, _) in fks}
            for src_tbl, cols in cols_by_tbl.items():
                # a) patrón *_id -> <tabla>.id
                for col in list(cols):
                    if col.endswith("_id"):
                        base = col[:-3]  # quita _id
                        for target in singular_candidates(base):
                            if target in cols_by_tbl and "id" in cols_by_tbl[target]:
                                key = (src_tbl.lower(), col, target.lower(), "id")
                                if key not in declared:
                                    fks.append((src_tbl, col, target, "id", "guess"))
                                break
                # b) fullpath -> files.fullpath
                if "fullpath" in cols and "files" in cols_by_tbl and "fullpath" in cols_by_tbl["files"]:
                    key = (src_tbl.lower(), "fullpath", "files", "fullpath")
                    if key not in declared:
                        fks.append((src_tbl, "fullpath", "files", "fullpath", "guess"))

        return tables, fks

    def _er_draw(self):
        """Dibuja el mini diagrama ER en el canvas."""
        import math
        cv = getattr(self, "cv_er", None)
        if not cv:
            return
        cv.delete("all")
        self._er_nodes = {}   # table -> {"rect": id, "bbox": (x1,y1,x2,y2)}
        self._er_colpos = {}  # {tabla_lower: {col_lower: y_centro_en_canvas}}

        tables, fks = self._er_collect()
        names = list(tables.keys())
        if not names:
            cv.create_text(20, 20, text="(No hay tablas)", anchor="nw"); return

        # Auto-layout en rejilla
        n = len(names)
        cols = max(1, int(math.ceil(math.sqrt(n))))
        node_w_min, row_gap, col_gap = 240, 70, 70
        x0, y0 = 60, 60

        # Dibuja nodos
        coords = {}
        for idx, name in enumerate(names):
            col = idx % cols
            row = idx // cols
            x = x0 + col * (node_w_min + col_gap)
            y = y0 + row * (140 + row_gap)

            # Alto según nº de columnas (cap a 12 visibles)
            vis_cols = tables[name][:12]
            node_h = 28 + 18*max(1, len(vis_cols)) + 12

            # Caja
            rect = cv.create_rectangle(x, y, x+node_w_min, y+node_h,
                                       fill="#ffffff", outline="#0ea5e9", width=2, tags=(f"node:{name}", "node"))
            # Título
            cv.create_rectangle(x, y, x+node_w_min, y+26, fill="#e0f2fe", outline="#0ea5e9", width=2)
            cv.create_text(x+8, y+13, text=name, anchor="w", font=("Segoe UI", 10, "bold"),
                           tags=(f"node:{name}",))
            # Columnas
            for i, c in enumerate(vis_cols):
                y_text = y + 30 + i * 18  # Y donde pintamos el texto
                y_center = y_text + 9  # centro visual de esa fila
                label = f"{'🔑 ' if c['pk'] else ''}{c['name']} : {c['type'] or ''}".rstrip()
                cv.create_text(x + 10, y_text, text=label, anchor="w", font=("Consolas", 9), tags=(f"node:{name}",))
                # registra la Y de la columna para anclar aristas
                self._er_colpos.setdefault(name.lower(), {})[(c['name'] or '').lower()] = y_center

            self._er_nodes[name] = {"rect": rect, "bbox": (x, y, x+node_w_min, y+node_h)}
            coords[name] = (x, y, x+node_w_min, y+node_h)

        # Dibuja aristas (FKs)
        # Dibuja aristas (FKs)
        # Dibuja aristas (FKs), ancladas a la fila de la columna y con codo ortogonal
        for edge in fks:
            if len(edge) == 4:
                src_tbl, src_col, dst_tbl, dst_col = edge;
                kind = "decl"
            else:
                src_tbl, src_col, dst_tbl, dst_col, kind = edge

            if src_tbl not in coords or dst_tbl not in coords:
                continue
            sx1, sy1, sx2, sy2 = coords[src_tbl]
            dx1, dy1, dx2, dy2 = coords[dst_tbl]

            # Y exacta de las columnas (si no la tenemos, centro de la caja)
            y1 = self._er_colpos.get(src_tbl.lower(), {}).get((src_col or "").lower(), (sy1 + sy2) / 2)
            y2 = self._er_colpos.get(dst_tbl.lower(), {}).get((dst_col or "").lower(), (dy1 + dy2) / 2)

            # ¿Conectamos en horizontal (cajas no solapadas en X) o vertical?
            horiz = (sx2 <= dx1) or (dx2 <= sx1)

            M = 6  # margen en px

            if horiz:
                if sx2 <= dx1:
                    x1, x2 = sx2 + M, dx1 - M  # antes: sx2, dx1
                else:
                    x1, x2 = sx1 - M, dx2 + M  # antes: sx1, dx2
                xm = (x1 + x2) / 2.0
                pts = (x1, y1, xm, y1, xm, y2, x2, y2)
            else:
                cx1, cx2 = (sx1 + sx2) / 2.0, (dx1 + dx2) / 2.0
                if sy2 <= dy1:
                    y_top, y_bot = sy2 + M, dy1 - M  # antes: sy2, dy1
                else:
                    y_top, y_bot = sy1 - M, dy2 + M  # antes: sy1, dy2
                ym = (y_top + y_bot) / 2.0
                pts = (cx1, y1, cx1, ym, cx2, ym, cx2, y2)

            color = "#334155" if kind == "decl" else "#94a3b8"
            width = 2 if kind == "decl" else 1
            dash = None if kind == "decl" else (4, 2)

            edge_tag = f"edge:{src_tbl}.{src_col}->{dst_tbl}.{dst_col}"
            cv.create_line(
                *pts,
                arrow="last",
                fill=color,
                width=width,
                dash=dash,
                joinstyle="round",
                tags=("edge", edge_tag)  # <<— NUEVO
            )


            # Etiqueta cerca del codo
            labx = (pts[2] + pts[4]) / 2.0
            laby = (pts[3] + pts[5]) / 2.0 - 12
            cv.create_text(labx, laby, text=f"{src_col} → {dst_col}", font=("Consolas", 8), fill=color)

        # Eventos: click en caja => sincroniza árbol de tablas
        def on_click(evt):
            item = cv.find_closest(evt.x, evt.y)
            tags = cv.gettags(item)
            tname = None
            for t in tags:
                if t.startswith("node:"):
                    tname = t.split(":",1)[1]; break
            if tname:
                try:
                    self.tv_schema.selection_set(f"table:{tname}")
                except Exception:
                    # fallback por si el iid no existe
                    pass
                self._er_highlight(tname)

        cv.tag_bind("node", "<Button-1>", on_click)

        # Hover sobre aristas (grosor temporal)
        def _hover_in(_e):
            cv.itemconfig("current", width=3)

        def _hover_out(_e):
            # Si la línea es discontinua (inferida) volvemos a 1; si no, a 2
            dash = cv.itemcget("current", "dash")
            cv.itemconfig("current", width=(1 if dash else 2))

        cv.tag_bind("edge", "<Enter>", _hover_in)
        cv.tag_bind("edge", "<Leave>", _hover_out)

        cv.create_line(20, 20, 60, 20, fill="#334155", width=2)
        cv.create_text(70, 20, text="FK declarada", anchor="w", font=("Segoe UI", 8))
        cv.create_line(160, 20, 200, 20, fill="#94a3b8", width=1, dash=(4, 2))
        cv.create_text(210, 20, text="FK inferida", anchor="w", font=("Segoe UI", 8))
        self._er_fit()



    def _er_fit(self):
        """Ajusta scrollregion y centra el diagrama."""
        cv = getattr(self, "cv_er", None)
        if not cv:
            return
        try:
            bb = cv.bbox("all")
            if bb:
                cv.configure(scrollregion=bb)
                # centra en el canvas
                (x1,y1,x2,y2) = bb
                w = max(1, x2-x1); h = max(1, y2-y1)
                cw = max(1, cv.winfo_width()); ch = max(1, cv.winfo_height())
                cv.xview_moveto(max(0.0, (x1 + (w-cw)/2) / max(1, x2)))
                cv.yview_moveto(max(0.0, (y1 + (h-ch)/2) / max(1, y2)))
        except Exception:
            pass

    def _er_highlight(self, table_name: str):
        """Resalta una tabla en el ER y des-resalta el resto."""
        cv = getattr(self, "cv_er", None)
        if not cv or not getattr(self, "_er_nodes", None):
            return
        for t, meta in self._er_nodes.items():
            col = "#0ea5e9" if t == table_name else "#94a3b8"
            try:
                cv.itemconfig(meta["rect"], outline=col, width=(3 if t==table_name else 1))
            except Exception:
                pass

    def _er_export_ps(self):
        """Exporta el canvas como PostScript (.ps) sin dependencias externas."""
        from tkinter import filedialog, messagebox
        cv = getattr(self, "cv_er", None)
        if not cv:
            return
        p = filedialog.asksaveasfilename(parent=self, title="Guardar diagrama (.ps)",
                                         defaultextension=".ps",
                                         filetypes=[("PostScript", "*.ps"), ("Todos", "*.*")])
        if not p: return
        try:
            bb = cv.bbox("all") or (0,0,1200,800)
            cv.postscript(file=p, colormode="color", pagewidth=bb[2]-bb[0])
            messagebox.showinfo("Exportar", f"Diagrama guardado en:\n{p}", parent=self)
        except Exception as e:
            messagebox.showerror("Exportar", f"No se pudo exportar:\n{e}", parent=self)


    # ---------- ZONA PELIGROSA ----------
    def _build_danger_tab(self, parent):
        info = ttk.Label(parent, text="Herramientas avanzadas. ¡Pueden borrar datos! Haz copia antes.", foreground="#a61b29")
        info.pack(anchor="w", pady=(0,8))

        cols = ttk.Panedwindow(parent, orient="horizontal"); cols.pack(fill="both", expand=True)

        # Columna izquierda: backups / resets
        left = ttk.Labelframe(cols, text="Backups y resets", padding=8)
        cols.add(left, weight=1)

        ttk.Button(left, text="Hacer copia del índice (SQLite)…", command=self._danger_backup_db).pack(anchor="w", pady=2)
        ttk.Button(left, text="Restaurar índice desde copia…", command=self._danger_restore_db).pack(anchor="w", pady=2)

        ttk.Separator(left).pack(fill="x", pady=(8,4))

        # --- Backend (hilo) ---
        ttk.Separator(left).pack(fill="x", pady=(8, 4))
        grp = ttk.Labelframe(left, text="Backend (hilo)", padding=6)
        grp.pack(fill="x", expand=False, pady=(4, 0))

        btn_eval_all = ttk.Button(grp, text="Ejecutar evaluación (todos)",
                                  command=lambda: self._backend_evaluacion_todos())
        btn_eval_all.pack(anchor="w", pady=2)

        btn_cancel = ttk.Button(grp, text="Cancelar",
                                command=lambda: self.cancelar_backend())
        btn_cancel.pack(anchor="w", pady=2)

        btn_autotest = ttk.Button(grp, text="Backend ▶ Autotest",
                                  command=lambda: self._backend_selftest())
        btn_autotest.pack(anchor="w", pady=2)

        # Registra botones de backend para que _set_busy los bloquee/rehabilite
        try:
            self._backend_buttons = [btn_eval_all, btn_cancel, btn_autotest]
        except Exception:
            pass

        ttk.Button(left, text="Vaciar PALABRAS CLAVE (doc_keywords)…", command=self._danger_wipe_keywords).pack(anchor="w", pady=2)
        ttk.Button(left, text="Vaciar OBSERVACIONES (doc_notes)…", command=self._danger_wipe_notes).pack(anchor="w", pady=2)

        # Columna central: reparación / mantenimiento
        right = ttk.Labelframe(cols, text="Reparación y mantenimiento", padding=8)
        cols.add(right, weight=1)


        ttk.Button(right, text="Eliminar entradas HUÉRFANAS (ficheros que ya no existen)…", command=self._danger_cleanup_orphans).pack(anchor="w", pady=2)
        ttk.Button(right, text="Rebase de rutas… (cambia prefijo de carpeta base)",
                   command=self._danger_rebase_paths).pack(anchor="w", pady=2)

        ttk.Button(right, text="Simular limpieza HUÉRFANAS (preview)…", command=self._danger_orphans_preview).pack(
            anchor="w", pady=2)  # <— NUEVO
        ttk.Button(right, text="Deduplicar OBSERVACIONES (misma ruta)",
                   command=self._danger_dedupe_notes).pack(anchor="w", pady=2)

        ttk.Button(right, text="Crear índices recomendados", command=self._danger_create_indices).pack(anchor="w",
                                                                                                       pady=2)

        ttk.Button(right, text="Deduplicar Históricos (qa_log)", command=self._danger_dedupe_qa_log)\
           .pack(anchor="w", pady=2)

        ttk.Separator(right).pack(fill="x", pady=(8,4))
        ttk.Button(right, text="PRAGMA integrity_check + VACUUM", command=self._danger_integrity_vacuum).pack(anchor="w", pady=2)

        # Columna derecha: BANCO DE PRUEBAS
        tests = ttk.Labelframe(cols, text="Banco de pruebas (índice/RAG)", padding=8)
        cols.add(tests, weight=1)

        row = ttk.Frame(tests); row.pack(fill="x", pady=(0,6))
        ttk.Label(row, text="k (top-k):").pack(side="left")
        self.var_bp_topk = tk.IntVar(value=5)
        ttk.Spinbox(row, from_=1, to=20, textvariable=self.var_bp_topk, width=5).pack(side="left", padx=(6,0))
        ttk.Button(row, text="Listar casos", command=self._bp_list_cases).pack(side="right")
        ttk.Button(row, text="Añadir caso…", command=self._bp_add_case).pack(side="right", padx=6)

        ttk.Button(tests, text="Importar casos (JSON)…", command=self._bp_import_cases).pack(anchor="w", pady=2)
        ttk.Button(tests, text="Exportar casos (JSON)…", command=self._bp_export_cases).pack(anchor="w", pady=2)

        ttk.Separator(tests).pack(fill="x", pady=(8,4))
        ttk.Button(tests, text="Ejecutar evaluación (todos)", command=self._bp_run_all).pack(anchor="w", pady=2)
        ttk.Button(tests, text="Ejecutar evaluación (seleccionados)…", command=self._bp_run_selected).pack(anchor="w",
                                                                                                           pady=2)

        ttk.Button(tests, text="Ver últimos resultados…", command=self._bp_show_last_results).pack(anchor="w", pady=2)
        ttk.Button(tests, text="Ver detalles (último run)…", command=self._bp_show_last_details).pack(anchor="w",
                                                                                                      pady=2)

        ttk.Button(tests, text="Comparar runs…", command=self._bp_compare_runs).pack(anchor="w", pady=2)
        ttk.Button(tests, text="Exportar últimos resultados (CSV)…", command=self._bp_export_last_results_csv).pack(
            anchor="w", pady=2)  # <— NUEVO

        # Garantiza tablas de pruebas
        try:
            self._bp_ensure_tables()
        except Exception:
            pass

    def _llm_eval_invoke(self, entrada: str, contexto: str, prompt_sistema: str) -> str:
        """
        Invoca el LLM local con la MISMA configuración que usa el chat.
        Reutiliza self.app.llm (tu LLMService).
        """
        mdl = getattr(self.app, "llm", None)
        if mdl is None:
            # Si tu App autoload del modelo va por otro método, llámalo aquí:
            if hasattr(self.app, "_autoload_model"):
                self.app._autoload_model()
                mdl = getattr(self.app, "llm", None)
        if mdl is None:
            raise RuntimeError("LLM no cargado (revisa _autoload_model y self.app.llm).")

        messages = [
            {"role": "system", "content": base_sys},
            {"role": "user", "content": ucontent},
        ]

        # Tu LLMService expone .chat(...). Lo estás usando así en el banco de pruebas. :contentReference[oaicite:2]{index=2}
        if hasattr(mdl, "chat"):
            out = mdl.chat(messages=messages, temperature=0.2, stream=False)
            return out["choices"][0]["message"]["content"]

        # Compatibilidad si expusieras el objeto llama-cpp por debajo:
        llama = getattr(mdl, "model", None) or getattr(mdl, "llama", None)
        if llama and hasattr(llama, "self.app.llm.chat"):
            out = llama.self.app.llm.chat(messages=messages, temperature=0.2)
            return out["choices"][0]["message"]["content"]

        raise RuntimeError("No encuentro un método compatible para invocar el modelo.")

    def _ensure_eval_tables(self):
        with self.app.data._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS eval_llm_responses (
                    ts TEXT NOT NULL,
                    case_id INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    response TEXT,
                    chars INTEGER,
                    dt_ms INTEGER,
                    PRIMARY KEY (ts, case_id)
                )
            """)
            con.commit()
    # >>> PATCH BACKEND: infra común acciones backend (pegar íntegro)
    import threading, traceback, time

    def _set_busy(self, text=None):
        """Deshabilita botones de Backend y actualiza barra/estado si existe."""
        try:
            self._backend_busy = bool(text)
            for btn in getattr(self, "_backend_buttons", []):
                try:
                    btn.config(state=("disabled" if text else "normal"))
                except Exception:
                    pass
            if hasattr(self, "status_var"):
                self.status_var.set(text or "")
            if hasattr(self, "progress"):
                if text:
                    try:
                        self.progress.start(10)
                    except Exception:
                        pass
                else:
                    try:
                        self.progress.stop()
                    except Exception:
                        pass
        except Exception:
            pass

    def _post_event(self, level, src, msg):
        ev = {"ts": time.time(), "level": level, "src": src, "msg": msg}
        try:
            # Preferir el bus de la app si existe
            if hasattr(self, "app") and hasattr(self.app, "post_event"):
                self.app.post_event(ev)
                return
        except Exception:
            pass
        # Fallback: directo a la consola de esta vista
        try:
            self._append_console_line(ev)
        except Exception:
            pass

    def backend_action(name):
        """Decorador para unificar logs, errores y UI busy en todas las acciones de Backend."""

        def deco(fn):
            def wrapper(self, *args, **kwargs):
                if getattr(self, "_backend_busy", False):
                    from tkinter import messagebox
                    try:
                        messagebox.showinfo("PACqui",
                                            "Hay un proceso de Backend en marcha. Espera a que termine o cancélalo.")
                    except Exception:
                        pass
                    return
                self._cancel_event = threading.Event()
                self._set_busy(f"{name}…")
                self._post_event("INFO", "Backend", f"▶ {name} — INICIO")

                def run():
                    ok = True
                    err = None
                    t0 = time.perf_counter()
                    try:
                        fn(self, *args, **kwargs)
                    except Exception as e:
                        ok = False
                        err = e
                        self._post_event("ERROR", "Backend", f"{name} falló: {e!r}")
                        traceback.print_exc()
                    finally:
                        dt = time.perf_counter() - t0
                        lvl = "SUCCESS" if ok else "ERROR"
                        self._post_event(lvl, "Backend", f"■ {name} — FIN ({dt:0.2f}s)")
                        self._set_busy(None)

                threading.Thread(target=run, daemon=True).start()

            return wrapper

        return deco

    def cancelar_backend(self):
        """Puede colgarse a un botón 'Cancelar'."""
        try:
            if hasattr(self, "_cancel_event"):
                self._cancel_event.set()
                self._post_event("WARN", "Backend", "Cancelación solicitada por el usuario")
        except Exception:
            pass

    # <<< PATCH BACKEND
    @backend_action("Evaluación (todos)")
    def _backend_evaluacion_todos(self, _dry_run: bool = False):
        """
        Ejecuta la evaluación masiva de prompts/dataset en hilo, con bloqueo UI y cancelación.
        _dry_run=True → self-test (no llama al LLM).
        """
        # 1) Carga dataset
        try:
            dataset = _cargar_dataset_evaluacion(self)
        except Exception as e:
            self._post_event("ERROR", "Evaluación", f"No pude cargar el dataset: {e!r}")
            return

        n = len(dataset or [])
        if n == 0:
            self._post_event("WARN", "Evaluación", "Dataset vacío: nada que evaluar")
            return

        self._post_event("INFO", "Evaluación", f"Casos a evaluar: {n}")

        # 2) Bucle principal
        for i, caso in enumerate(dataset, 1):
            # Cancelación
            if getattr(self, "_cancel_event", None) and self._cancel_event.is_set():
                self._post_event("WARN", "Evaluación", "Proceso cancelado por el usuario")
                break

            if _dry_run:
                time.sleep(0.01)
                self._post_event("DEBUG", "Evaluación", f"[{i}/{n}] DRY-RUN {caso.get('id', i)}")
                continue

            # 3) Llamada real al LLM
            try:
                # Contexto RAG coherente con tu chat (usa lo que ya tienes)
                contexto = ""
                try:
                    # Si expones algún recuperador de contexto en la App o LLMService, úsalo aquí.
                    # En tu Banco de pruebas llamas a self.app.llm._index_hits(...) para evaluar el índice. :contentReference[oaicite:4]{index=4}
                    # Si quieres, puedes montar 'contexto' concatenando notas/hits. Si no, déjalo vacío.
                    pass
                except Exception:
                    contexto = ""

                prompt_sistema = getattr(self, "system_prompt_eval", "Responde SIEMPRE en español neutro.")
                entrada = caso.get("query", "")

                t0 = time.perf_counter()
                respuesta = self._llm_eval_invoke(entrada, contexto, prompt_sistema)
                dt_ms = int((time.perf_counter() - t0) * 1000)

                # 4) Métrica simple y log
                self._post_event("INFO", "Evaluación", f"[{i}/{n}] {caso.get('id', i)} → {len(str(respuesta))} chars")

                # 5) Persistencia de resultados
                try:
                    _guardar_resultado_eval(self, caso, respuesta, dt_ms=dt_ms)

                except Exception as e:
                    self._post_event("WARN", "Evaluación", f"No guardé resultado {caso.get('id', i)}: {e!r}")

            except Exception as e:
                self._post_event("ERROR", "Evaluación", f"[{i}/{n}] Error: {e!r}")

            # 6) Progreso visual si tienes progressbar en esta vista
            try:
                if hasattr(self, "progress"):
                    self.progress.stop();
                    self.progress["mode"] = "determinate"
                    self.progress["maximum"] = n
                    self.progress["value"] = i
                    self.progress.update_idletasks()
            except Exception:
                pass

    def _backend_selftest(self):
        """
        DRY-RUN de acciones críticas del backend para validar wiring, logs y sincronización.
        """
        pruebas = []

        # Registra tus funciones reales:
        if hasattr(self, "_backend_evaluacion_todos"):
            pruebas.append(("Evaluación (todos)", self._backend_evaluacion_todos))

        # Si tienes más acciones de backend, añádelas aquí:
        if hasattr(self, "_backend_indexar_excel"):
            pruebas.append(("Reindexar Excel", getattr(self, "_backend_indexar_excel")))
        if hasattr(self, "_backend_scrap_keywords"):
            pruebas.append(("Scrapeo de palabras clave", getattr(self, "_backend_scrap_keywords")))
        if hasattr(self, "_backend_exportar_excel"):
            pruebas.append(("Exportar Excel indexada", getattr(self, "_backend_exportar_excel")))

        self._post_event("INFO", "SelfTest", f"Pruebas encontradas: {len(pruebas)}")

        ok_count = 0
        for (nombre, fn) in pruebas:
            t0 = time.perf_counter()
            try:
                # Todas deben aceptar **kwargs y tragarse _dry_run=True
                fn(_dry_run=True)
                dt = time.perf_counter() - t0
                ok_count += 1
                self._post_event("SUCCESS", "SelfTest", f"{nombre}: OK ({dt:0.3f}s)")
            except Exception as e:
                dt = time.perf_counter() - t0
                self._post_event("ERROR", "SelfTest", f"{nombre}: FALLÓ ({dt:0.3f}s) → {e!r}")

        self._post_event("INFO", "SelfTest", f"Resumen: {ok_count}/{len(pruebas)} OK")


    # ---- Helpers de confirmación y SQL ----
    def _danger_confirm(self, title: str, msg: str, must_type: str | None = None) -> bool:
        from tkinter import simpledialog, messagebox
        if not messagebox.askyesno(title, msg + ("\n\n¿Continuar?" if not must_type else "")):
            return False
        if must_type:
            typed = simpledialog.askstring(title, f"Escribe {must_type} para continuar:")
            if (typed or "").strip().upper() != must_type.upper():
                messagebox.showinfo(title, "Operación cancelada.")
                return False
        return True

    def _danger_dedupe_qa_log(self):
        from tkinter import messagebox
        sql_dups = """
            WITH d AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                         PARTITION BY ts, query, answer, model
                         ORDER BY id DESC
                       ) AS rn
                FROM qa_log
            )
            SELECT id FROM d WHERE rn > 1
        """
        try:
            with self.app.data._connect() as con:
                dup_ids = [r[0] for r in con.execute(sql_dups).fetchall()]
                if not dup_ids:
                    messagebox.showinfo("Deduplicar", "No se han encontrado duplicados en qa_log.", parent=self)
                    return
                # Borra primero fuentes asociadas
                con.executemany("DELETE FROM qa_sources WHERE qa_id=?", [(i,) for i in dup_ids])
                # Borra los duplicados en qa_log
                con.executemany("DELETE FROM qa_log WHERE id=?", [(i,) for i in dup_ids])
                con.commit()
            self.app._log("danger_dedupe_qa_log", removed=len(dup_ids))
            messagebox.showinfo("Deduplicar", f"Eliminados {len(dup_ids)} duplicados en qa_log.", parent=self)
        except Exception as e:
            messagebox.showerror("Deduplicar", f"Error:\n{e}", parent=self)


    def _danger_create_indices(self):
        from tkinter import messagebox
        try:
            with self.app.data._connect() as con:
                con.execute("CREATE INDEX IF NOT EXISTS idx_kw_path ON doc_keywords(lower(fullpath))")
                con.execute("CREATE INDEX IF NOT EXISTS idx_kw_kw   ON doc_keywords(lower(keyword))")
                con.execute("CREATE INDEX IF NOT EXISTS idx_no_path ON doc_notes(lower(fullpath))")
                con.execute("CREATE INDEX IF NOT EXISTS idx_no_note ON doc_notes(lower(note))")
                con.commit()
            self.app._log("danger_create_indices")
            messagebox.showinfo("Índices", "Índices creados o ya existentes.", parent=self)
        except Exception as e:
            messagebox.showerror("Índices", f"Error creando índices:\n{e}", parent=self)

    def _danger_sql(self, sql: str, params: tuple = ()):
        with self.app.data._connect() as con:
            cur = con.cursor()
            cur.execute(sql, params)
            con.commit()

    # ---- Backups / Restaurar ----
    def _danger_backup_db(self):
        from tkinter import filedialog, messagebox
        import shutil, time, os
        src = self.app.data.db_path
        if not src or not os.path.exists(src):
            messagebox.showerror("Backup", "No se encontró el archivo SQLite del índice."); return
        ts = time.strftime("%Y%m%d_%H%M%S")
        dst = filedialog.asksaveasfilename(parent=self, title="Guardar copia del índice",
                                           defaultextension=".sqlite",
                                           initialfile=f"pacqui_index_backup_{ts}.sqlite",
                                           filetypes=[("SQLite","*.sqlite;*.db"),("Todos","*.*")])
        if not dst: return
        try:
            shutil.copy2(src, dst)
            self.app._log("danger_backup", src=src, dst=dst)
            messagebox.showinfo("Backup", f"Copia guardada en:\n{dst}", parent=self)
        except Exception as e:
            messagebox.showerror("Backup", f"No se pudo copiar:\n{e}", parent=self)

    def _danger_restore_db(self):
        from tkinter import filedialog, messagebox
        import shutil, os
        dst = self.app.data.db_path
        src = filedialog.askopenfilename(parent=self, title="Selecciona copia de índice",
                                         filetypes=[("SQLite","*.sqlite;*.db"),("Todos","*.*")])
        if not src: return
        if not self._danger_confirm("Restaurar índice",
                                    f"Se sobrescribirá el índice actual:\n{dst}\n\nOrigen:\n{src}",
                                    must_type="RESTAURAR"):
            return
        try:
            # Asegura que no quedan handles abiertos (conexión por operación)
            shutil.copy2(src, dst)
            self.app._log("danger_restore", src=src, dst=dst)
            messagebox.showinfo("Restaurar", "Índice restaurado.\nReabre la pestaña si no ves los cambios.", parent=self)
            self.app._refresh_footer()
        except Exception as e:
            messagebox.showerror("Restaurar", f"No se pudo restaurar:\n{e}", parent=self)

    # ---- Resets ----
    def _danger_wipe_keywords(self):
        from tkinter import messagebox
        if not self._danger_confirm("Vaciar keywords",
                                    "Vas a BORRAR TODAS las palabras clave (doc_keywords).",
                                    must_type="BORRAR"):
            return
        try:
            self._danger_sql("DELETE FROM doc_keywords")
            self.app._log("danger_wipe_keywords")
            messagebox.showinfo("Vaciar", "Palabras clave eliminadas.", parent=self)
            self.app._refresh_footer()
        except Exception as e:
            messagebox.showerror("Vaciar", f"Error:\n{e}", parent=self)

    def _danger_wipe_notes(self):
        from tkinter import messagebox
        if not self._danger_confirm("Vaciar observaciones",
                                    "Vas a BORRAR TODAS las observaciones (doc_notes).",
                                    must_type="BORRAR"):
            return
        try:
            self._danger_sql("DELETE FROM doc_notes")
            self.app._log("danger_wipe_notes")
            messagebox.showinfo("Vaciar", "Observaciones eliminadas.", parent=self)
            self.app._refresh_footer()
        except Exception as e:
            messagebox.showerror("Vaciar", f"Error:\n{e}", parent=self)

    # ---- Reparaciones ----
    def _danger_rebase_paths(self):
        from tkinter import filedialog, messagebox, simpledialog
        import os
        old_prefix = filedialog.askdirectory(parent=self, title="Prefijo ACTUAL (carpeta base antigua) o su carpeta raíz")
        if not old_prefix: return
        new_prefix = filedialog.askdirectory(parent=self, title="Prefijo NUEVO (carpeta base nueva)")
        if not new_prefix: return

        old_prefix = os.path.normpath(old_prefix)
        new_prefix = os.path.normpath(new_prefix)
        if not self._danger_confirm("Rebase de rutas",
                                    f"Reemplazar prefijo:\n\n{old_prefix}\n→\n{new_prefix}\n\nen doc_keywords y doc_notes.",
                                    must_type="REBASE"):
            return

        try:
            with self.app.data._connect() as con:
                cur = con.cursor()
                for tbl in ("doc_keywords", "doc_notes"):
                    cur.execute(f"UPDATE {tbl} SET fullpath = REPLACE(fullpath, ?, ?)", (old_prefix, new_prefix))
                con.commit()
            self.app._log("danger_rebase_paths", old=old_prefix, new=new_prefix)
            messagebox.showinfo("Rebase", "Rutas actualizadas.", parent=self)
        except Exception as e:
            messagebox.showerror("Rebase", f"Error:\n{e}", parent=self)

    def _danger_cleanup_orphans(self):
        from tkinter import messagebox
        import os, threading, time
        if not self._danger_confirm("Eliminar huérfanos",
                                    "Se eliminarán filas cuyo fichero ya NO exista en disco.\n"
                                    "Afecta a doc_keywords y doc_notes.",
                                    must_type="LIMPIAR"):
            return

        def worker():
            removed_kw = removed_no = 0
            paths = set()
            try:
                with self.app.data._connect() as con:
                    cur = con.cursor()
                    for tbl in ("doc_keywords","doc_notes"):
                        rows = cur.execute(f"SELECT DISTINCT fullpath FROM {tbl}").fetchall()
                        for (p,) in rows:
                            if p: paths.add(p)
                # Chequeo disco
                to_remove = [p for p in paths if not os.path.exists(p)]
                with self.app.data._connect() as con:
                    cur = con.cursor()
                    for p in to_remove:
                        removed_kw += cur.execute("DELETE FROM doc_keywords WHERE fullpath=?", (p,)).rowcount
                        removed_no += cur.execute("DELETE FROM doc_notes WHERE fullpath=?", (p,)).rowcount
                    con.commit()
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Huérfanos", f"Error:\n{e}", parent=self)); return
            self.app._log("danger_cleanup_orphans", removed_kw=removed_kw, removed_no=removed_no)
            self.after(0, lambda: (self.app._refresh_footer(),
                                   messagebox.showinfo("Huérfanos",
                                                       f"Eliminadas {removed_kw} keywords y {removed_no} notas huérfanas.",
                                                       parent=self)))

        threading.Thread(target=worker, daemon=True).start()

    def _danger_orphans_preview(self):
        """Muestra una vista previa (paths y nº de filas afectadas) antes de borrar huérfanos."""
        from tkinter import messagebox
        import os, csv

        # 1) Recolecta paths distintos que estén en doc_keywords/doc_notes
        paths = set();
        stats_by_path = {}
        try:
            with self.app.data._connect() as con:
                cur = con.cursor()
                for tbl in ("doc_keywords", "doc_notes"):
                    rows = cur.execute(f"SELECT DISTINCT fullpath FROM {tbl}").fetchall()
                    for (p,) in rows:
                        if p:
                            paths.add(p)
            to_remove = [p for p in paths if not os.path.exists(p)]
            if not to_remove:
                messagebox.showinfo("Simular huérfanos", "No se han encontrado huérfanos. ¡Todo OK!", parent=self)
                return

            # 2) Cuenta filas afectadas por tabla y por ruta
            with self.app.data._connect() as con:
                cur = con.cursor()
                for p in to_remove:
                    kw = cur.execute("SELECT COUNT(*) FROM doc_keywords WHERE fullpath=?", (p,)).fetchone()[0] or 0
                    no = cur.execute("SELECT COUNT(*) FROM doc_notes    WHERE fullpath=?", (p,)).fetchone()[0] or 0
                    stats_by_path[p] = (kw, no)

        except Exception as e:
            messagebox.showerror("Simular huérfanos", f"Error preparando preview:\n{e}", parent=self)
            return

        # 3) Ventana de preview
        top = tk.Toplevel(self);
        top.title("Preview: huérfanos a eliminar");
        top.geometry("1000x560")
        bar = ttk.Frame(top);
        bar.pack(fill="x", padx=8, pady=6)

        total_kw = sum(v[0] for v in stats_by_path.values())
        total_no = sum(v[1] for v in stats_by_path.values())
        ttk.Label(bar,
                  text=f"Rutas huérfanas: {len(stats_by_path)}  ·  Filas doc_keywords: {total_kw}  ·  Filas doc_notes: {total_no}").pack(
            side="left")

        def _export_csv():
            p = filedialog.asksaveasfilename(parent=top, title="Guardar lista (CSV)", defaultextension=".csv",
                                             filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
            if not p: return
            try:
                with open(p, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f);
                    w.writerow(["fullpath", "rows_doc_keywords", "rows_doc_notes"])
                    for path, (kwc, noc) in stats_by_path.items():
                        w.writerow([path, kwc, noc])
                messagebox.showinfo("Exportado", f"CSV guardado en:\n{p}", parent=top)
            except Exception as e:
                messagebox.showerror("Exportado", f"No se pudo exportar:\n{e}", parent=top)

        def _delete_now():
            if not self._danger_confirm("Eliminar huérfanos",
                                        "Se eliminarán TODAS las filas mostradas en la tabla (doc_keywords y doc_notes).",
                                        must_type="LIMPIAR"):
                return
            removed_kw = removed_no = 0
            try:
                with self.app.data._connect() as con:
                    cur = con.cursor()
                    for p in stats_by_path.keys():
                        removed_kw += cur.execute("DELETE FROM doc_keywords WHERE fullpath=?", (p,)).rowcount
                        removed_no += cur.execute("DELETE FROM doc_notes    WHERE fullpath=?", (p,)).rowcount
                    con.commit()
                self.app._log("danger_cleanup_orphans", removed_kw=removed_kw, removed_no=removed_no)
                messagebox.showinfo("Huérfanos", f"Eliminadas {removed_kw} keywords y {removed_no} notas.", parent=top)
                try:
                    self.app._refresh_footer()
                except Exception:
                    pass
                top.destroy()
            except Exception as e:
                messagebox.showerror("Huérfanos", f"Error al eliminar:\n{e}", parent=top)

        ttk.Button(bar, text="Eliminar ahora…", command=_delete_now).pack(side="right")
        ttk.Button(bar, text="Guardar lista (CSV)…", command=_export_csv).pack(side="right", padx=(0, 6))

        tv = ttk.Treeview(top, columns=("path", "kw", "no"), show="headings")
        tv.heading("path", text="Ruta");
        tv.heading("kw", text="doc_keywords");
        tv.heading("no", text="doc_notes")
        tv.column("path", width=780);
        tv.column("kw", width=110, anchor="e");
        tv.column("no", width=110, anchor="e")
        tv.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Ordena por “filas totales” descendente y limita a 10k por seguridad visual
        ordered = sorted(stats_by_path.items(), key=lambda kv: kv[1][0] + kv[1][1], reverse=True)[:10000]
        for path, (kwc, noc) in ordered:
            tv.insert("", "end", values=(path, kwc, noc))

    def _danger_dedupe_keywords(self):
        from tkinter import messagebox
        sql = """
            DELETE FROM doc_keywords
            WHERE rowid IN (
                SELECT rowid FROM (
                    SELECT rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(fullpath), lower(keyword)
                               ORDER BY rowid
                           ) AS rn
                    FROM doc_keywords
                ) t WHERE t.rn > 1
            )
        """
        try:
            with self.app.data._connect() as con:
                con.execute("PRAGMA foreign_keys=ON")
                con.execute(sql)
                con.commit()
            self.app._log("danger_dedupe_keywords")
            messagebox.showinfo("Deduplicar", "Duplicados eliminados en doc_keywords.", parent=self)
        except Exception as e:
            messagebox.showerror("Deduplicar", f"Error:\n{e}", parent=self)

    def _danger_dedupe_notes(self):
        from tkinter import messagebox
        sql = """
            DELETE FROM doc_notes
            WHERE rowid IN (
                SELECT rowid FROM (
                    SELECT rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(fullpath)
                               ORDER BY rowid DESC
                           ) AS rn
                    FROM doc_notes
                ) t WHERE t.rn > 1
            )
        """
        try:
            with self.app.data._connect() as con:
                con.execute(sql)
                con.commit()
            self.app._log("danger_dedupe_notes")
            messagebox.showinfo("Deduplicar", "Duplicados eliminados en doc_notes (por ruta).", parent=self)
        except Exception as e:
            messagebox.showerror("Deduplicar", f"Error:\n{e}", parent=self)

    def _danger_integrity_vacuum(self):
        from tkinter import messagebox
        try:
            with self.app.data._connect() as con:
                ok = (con.execute("PRAGMA integrity_check").fetchone() or [""])[0]
            with self.app.data._connect() as con:
                con.execute("VACUUM")
            self.app._log("danger_integrity_vacuum", result=ok)
            messagebox.showinfo("Integridad/VACUUM", f"integrity_check → {ok}\n\nVACUUM ejecutado.", parent=self)
        except Exception as e:
            messagebox.showerror("Integridad/VACUUM", f"Error:\n{e}", parent=self)

    # ---------- BANCO DE PRUEBAS ----------
    def _bp_ensure_tables(self):
        with self.app.data._connect() as con:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS test_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    q TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    top_k INTEGER DEFAULT 5,
                    notes TEXT,
                    created_at TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS test_results (
                    ts TEXT NOT NULL,
                    case_id INTEGER NOT NULL,
                    top_k INTEGER NOT NULL,
                    hits INTEGER NOT NULL,
                    expected INTEGER NOT NULL,
                    found INTEGER NOT NULL,
                    precision REAL NOT NULL,
                    recall REAL NOT NULL,
                    mrr REAL NOT NULL,
                    first_hit_rank INTEGER,
                    dt_ms INTEGER NOT NULL,
                    details_json TEXT,
                    PRIMARY KEY (ts, case_id)
                )
            """)
            con.commit()

    def _bp_add_case(self):
        from tkinter import simpledialog, filedialog, messagebox
        q = simpledialog.askstring("Nuevo caso", "Escribe la consulta:")
        if not q: return
        paths = filedialog.askopenfilenames(parent=self, title="Selecciona los ficheros esperados (puedes marcar varios)")
        if not paths: return
        k = int(self.var_bp_topk.get() or 5)
        rec = {
            "q": q.strip(),
            "expected": [os.path.normpath(p) for p in paths],
            "top_k": k,
            "notes": ""
        }
        try:
            self._bp_ensure_tables()
            with self.app.data._connect() as con:
                cur = con.cursor()
                cur.execute(
                    "INSERT INTO test_cases(q, expected_json, top_k, notes, created_at) VALUES(?,?,?,?,datetime('now'))",
                    (rec["q"], json.dumps(rec["expected"], ensure_ascii=False), int(rec["top_k"]), rec["notes"])
                )
                con.commit()
            self.app._log("bp_add_case", q=rec["q"], n=len(rec["expected"]), k=k)
            messagebox.showinfo("Banco de pruebas", f"Caso añadido ({len(rec['expected'])} esperados).", parent=self)
        except Exception as e:
            messagebox.showerror("Banco de pruebas", f"No se pudo guardar el caso:\n{e}", parent=self)

    def _bp_export_last_results_csv(self):
        from tkinter import messagebox
        import csv
        self._bp_ensure_tables()
        with self.app.data._connect() as con:
            row = con.execute("SELECT MAX(ts) FROM test_results").fetchone()
            last = row[0] if row else None
            if not last:
                messagebox.showinfo("Exportar CSV", "No hay ejecuciones registradas.", parent=self);
                return
            rows = con.execute("""SELECT c.id, c.q, r.top_k, r.hits, r.expected, r.found, r.precision, r.recall,
                                         r.mrr, r.first_hit_rank, r.dt_ms
                                  FROM test_results r
                                  JOIN test_cases c ON c.id=r.case_id
                                  WHERE r.ts=? ORDER BY c.id""", (last,)).fetchall()
        p = filedialog.asksaveasfilename(parent=self, title="Guardar CSV (últimos resultados)",
                                         defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if not p: return
        try:
            with open(p, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(
                    ["ts", "case_id", "query", "top_k", "hits", "expected", "found", "precision", "recall", "mrr",
                     "first_hit_rank", "dt_ms"])
                for (cid, q, k, hits, exp, found, pv, rv, mrr, fr, ms) in rows:
                    w.writerow(
                        [last, cid, q, k, hits, exp, found, f"{pv:.4f}", f"{rv:.4f}", f"{mrr:.4f}", fr or "", ms])
            messagebox.showinfo("Exportado", f"CSV guardado en:\n{p}", parent=self)
        except Exception as e:
            messagebox.showerror("Exportado", f"No se pudo exportar:\n{e}", parent=self)

    def _bp_list_cases(self):
        from tkinter import messagebox
        self._bp_ensure_tables()
        rows=[]
        with self.app.data._connect() as con:
            rows = con.execute("SELECT id, q, top_k, expected_json, created_at FROM test_cases ORDER BY id").fetchall()
        top = tk.Toplevel(self); top.title("Casos de prueba"); top.geometry("900x480")
        bar = ttk.Frame(top); bar.pack(fill="x")
        ttk.Button(bar, text="Eliminar seleccionado", command=lambda:self._bp_delete_selected(tv)).pack(side="right")
        tv = ttk.Treeview(top, columns=("id","q","k","exp","ts"), show="headings")
        for c,t,w in (("id","ID",60),("q","Consulta",520),("k","k",40),("exp","Esperados",90),("ts","Creado",140)):
            tv.heading(c, text=t); tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=True)
        for (cid,q,k,js,ts) in rows:
            try: exp = len(json.loads(js))
            except Exception: exp = "?"
            tv.insert("", "end", values=(cid, q, k, exp, ts or ""))
        tv.focus_set()

    def _bp_delete_selected(self, tv):
        from tkinter import messagebox
        sel = tv.selection()
        if not sel: return
        cid = tv.item(sel[0], "values")[0]
        if not messagebox.askyesno("Eliminar", f"¿Borrar el caso {cid}?"): return
        with self.app.data._connect() as con:
            con.execute("DELETE FROM test_cases WHERE id=?", (cid,))
            con.commit()
        self.app._log("bp_del_case", id=cid)
        tv.delete(sel[0])

    def _bp_import_cases(self):
        from tkinter import filedialog, messagebox
        p = filedialog.askopenfilename(parent=self, title="Importar JSON de casos",
                                       filetypes=[("JSON","*.json"),("Todos","*.*")])
        if not p: return
        try:
            arr = json.loads(Path(p).read_text(encoding="utf-8"))
            self._bp_ensure_tables()
            n=0
            with self.app.data._connect() as con:
                cur = con.cursor()
                for rec in arr:
                    q = (rec.get("q") or "").strip()
                    if not q: continue
                    exp = rec.get("expected") or []
                    k = int(rec.get("top_k") or self.var_bp_topk.get() or 5)
                    cur.execute(
                        "INSERT INTO test_cases(q, expected_json, top_k, notes, created_at) VALUES(?,?,?,?,datetime('now'))",
                        (q, json.dumps(exp, ensure_ascii=False), k, rec.get("notes") or "")
                    )
                    n+=1
                con.commit()
            self.app._log("bp_import", n=n, path=p)
            messagebox.showinfo("Importar", f"Importados {n} casos.", parent=self)
        except Exception as e:
            messagebox.showerror("Importar", f"No se pudo importar:\n{e}", parent=self)

    def _bp_export_cases(self):
        from tkinter import filedialog, messagebox
        p = filedialog.asksaveasfilename(parent=self, title="Exportar JSON de casos",
                                         defaultextension=".json",
                                         filetypes=[("JSON","*.json"),("Todos","*.*")])
        if not p: return
        try:
            with self.app.data._connect() as con:
                rows = con.execute("SELECT q, expected_json, top_k, notes FROM test_cases ORDER BY id").fetchall()
            arr = []
            for q, js, k, notes in rows:
                try: exp = json.loads(js)
                except Exception: exp = []
                arr.append({"q": q, "expected": exp, "top_k": k, "notes": notes or ""})
            Path(p).write_text(json.dumps(arr, indent=2, ensure_ascii=False), encoding="utf-8")
            self.app._log("bp_export", n=len(arr), path=p)
            messagebox.showinfo("Exportar", f"Exportados {len(arr)} casos a:\n{p}", parent=self)
        except Exception as e:
            messagebox.showerror("Exportar", f"No se pudo exportar:\n{e}", parent=self)

    def _bp_run_all(self):
        from tkinter import messagebox
        import math

        self._bp_ensure_tables()
        with self.app.data._connect() as con:
            cases = con.execute("SELECT id, q, top_k, expected_json FROM test_cases ORDER BY id").fetchall()
        if not cases:
            messagebox.showinfo("Banco de pruebas", "No hay casos definidos."); return

        # Ventana de progreso + tabla live
        top = tk.Toplevel(self); top.title("Ejecutando evaluación…"); top.geometry("980x520")
        lab = ttk.Label(top, text="Preparando…"); lab.pack(anchor="w", padx=10, pady=(10,6))
        pb = ttk.Progressbar(top, mode="determinate", maximum=len(cases)); pb.pack(fill="x", padx=10)
        tv = ttk.Treeview(top, columns=("id","ok","p","r","mrr","rank","ms","hits","exp","q"), show="headings", height=16)
        headers = [("id","ID",50),("ok","OK",50),("p","Prec@",70),("r","Rec@",70),("mrr","MRR",70),
                   ("rank","1ª pos",70),("ms","ms",70),("hits","hits",60),("exp","exp",60),("q","Consulta",500)]
        for c,t,w in headers:
            tv.heading(c, text=t); tv.column(c, width=w, anchor=("e" if c in ("p","r","mrr","rank","ms","hits","exp") else "w"))
        tv.pack(fill="both", expand=True, padx=10, pady=8)

        ts = datetime.now().isoformat(timespec="seconds")
        k_global = int(self.var_bp_topk.get() or 5)

        def norm(p):
            return os.path.normcase(os.path.normpath(p or ""))

        def worker():
            done = 0
            acc_p = acc_r = acc_mrr = 0.0
            try:
                with self.app.data._connect() as con:
                    cur = con.cursor()
                    for (cid, q, k_case, js) in cases:
                        k = int(k_case or k_global or 5)
                        try: expected = [norm(p) for p in json.loads(js)]
                        except Exception: expected = []
                        t0 = time.time()
                        try:
                            hits = self.app.llm._index_hits(q, top_k=k, max_note_chars=220) or []
                        except Exception:
                            hits = []
                        dt_ms = int((time.time() - t0)*1000)

                        got = [norm(h.get("path")) for h in hits if h.get("path")]
                        found_set = set(expected).intersection(set(got))
                        found = len(found_set)

                        # rank del primer acierto y MRR
                        first_rank = None
                        for idx, p in enumerate(got, start=1):
                            if p in expected:
                                first_rank = idx; break
                        mrr = (1.0/first_rank) if first_rank else 0.0
                        precision = (found / max(1, k))
                        recall = (found / max(1, len(expected)))
                        ok = 1 if found > 0 else 0

                        # Persistir resultado
                        det = {"expected": expected, "hits": got}
                        cur.execute("""INSERT OR REPLACE INTO test_results
                                       (ts, case_id, top_k, hits, expected, found, precision, recall, mrr, first_hit_rank, dt_ms, details_json)
                                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (ts, cid, k, len(got), len(expected), found, precision, recall, mrr, first_rank, dt_ms,
                                     json.dumps(det, ensure_ascii=False)))
                        con.commit()

                        acc_p += precision; acc_r += recall; acc_mrr += mrr
                        done += 1
                        # pinta fila live
                        self.after(0, lambda cid=cid, ok=ok, p=precision, r=recall, mrr=mrr, fr=first_rank, ms=dt_ms, h=len(got), e=len(expected), q=q:
                                   tv.insert("", "end", values=(cid, "✓" if ok else "✗", f"{p:.2f}", f"{r:.2f}", f"{mrr:.2f}", fr or "-", ms, h, e, q)))
                        self.after(0, lambda: (pb.config(value=done), lab.config(text=f"Ejecutando… {done}/{len(cases)}")))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Evaluación", f"Error: {e}", parent=self))
                return

            # Resumen
            n = max(1, len(cases))
            mean_p = acc_p/n; mean_r = acc_r/n; mean_mrr = acc_mrr/n
            self.app._log("bp_eval_run", ts=ts, n=n, p=round(mean_p,3), r=round(mean_r,3), mrr=round(mean_mrr,3), k=k_global)
            self.after(0, lambda: lab.config(text=f"Finalizado. Prec@k={mean_p:.2f} · Rec@k={mean_r:.2f} · MRR={mean_mrr:.2f} (n={n})"))

        threading.Thread(target=worker, daemon=True).start()

    def _bp_run_selected(self):
        """Permite elegir casos y ejecutar la evaluación SOLO para esos casos."""
        from tkinter import messagebox

        self._bp_ensure_tables()
        with self.app.data._connect() as con:
            cases = con.execute("SELECT id, q, top_k, expected_json FROM test_cases ORDER BY id").fetchall()
        if not cases:
            messagebox.showinfo("Banco de pruebas", "No hay casos definidos.", parent=self);
            return

        # Ventana de selección (multi-selección)
        top = tk.Toplevel(self);
        top.title("Selecciona casos a evaluar");
        top.geometry("900x520")
        bar = ttk.Frame(top);
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="Mantén Ctrl/Shift para seleccionar varios.").pack(side="left")
        tv = ttk.Treeview(top, columns=("id", "q", "k", "nexp"), show="headings", selectmode="extended", height=18)
        for c, t, w in (("id", "ID", 60), ("q", "Consulta", 560), ("k", "k", 40), ("nexp", "Esperados", 90)):
            tv.heading(c, text=t);
            tv.column(c, width=w, anchor=("w" if c in ("id", "q") else "e"))
        tv.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for cid, q, k, js in cases:
            try:
                nexp = len(json.loads(js))
            except Exception:
                nexp = "?"
            tv.insert("", "end", values=(cid, q, int(k or self.var_bp_topk.get() or 5), nexp))

        def run_now():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("Banco de pruebas", "Selecciona al menos un caso.", parent=top);
                return
            sel_ids = [int(tv.item(i, "values")[0]) for i in sel]
            top.destroy()

            # Reutiliza la lógica de _bp_run_all, pero filtrando por ids
            ts = datetime.now().isoformat(timespec="seconds")
            k_global = int(self.var_bp_topk.get() or 5)

            def norm(p):
                return os.path.normcase(os.path.normpath(p or ""))

            # Ventana de progreso
            win = tk.Toplevel(self);
            win.title("Ejecutando evaluación (seleccionados)…");
            win.geometry("980x520")
            lab = ttk.Label(win, text="Preparando…");
            lab.pack(anchor="w", padx=10, pady=(10, 6))
            pb = ttk.Progressbar(win, mode="determinate", maximum=len(sel_ids));
            pb.pack(fill="x", padx=10)
            tv2 = ttk.Treeview(win, columns=("id", "ok", "p", "r", "mrr", "rank", "ms", "hits", "exp", "q"),
                               show="headings", height=16)
            for c, t, w in (
            ("id", "ID", 50), ("ok", "OK", 50), ("p", "Prec@", 70), ("r", "Rec@", 70), ("mrr", "MRR", 70),
            ("rank", "1ª pos", 70), ("ms", "ms", 70), ("hits", "hits", 60), ("exp", "exp", 60), ("q", "Consulta", 500)):
                tv2.heading(c, text=t);
                tv2.column(c, width=w, anchor=("e" if c in ("p", "r", "mrr", "rank", "ms", "hits", "exp") else "w"))
            tv2.pack(fill="both", expand=True, padx=10, pady=8)

            def worker():
                acc_p = acc_r = acc_mrr = 0.0;
                done = 0
                try:
                    with self.app.data._connect() as con:
                        cur = con.cursor()
                        qmap = {cid: (q, k, js) for cid, q, k, js in cases}
                        for cid in sel_ids:
                            q, k_case, js = qmap[cid]
                            k = int(k_case or k_global or 5)
                            try:
                                expected = [norm(p) for p in json.loads(js)]
                            except Exception:
                                expected = []
                            t0 = time.time()
                            try:
                                hits = self.app.llm._index_hits(q, top_k=k, max_note_chars=220) or []
                            except Exception:
                                hits = []
                            dt_ms = int((time.time() - t0) * 1000)

                            got = [norm(h.get("path")) for h in hits if h.get("path")]
                            found = len(set(expected).intersection(set(got)))
                            first_rank = next((i for i, p in enumerate(got, start=1) if p in expected), None)
                            mrr = (1.0 / first_rank) if first_rank else 0.0
                            precision = (found / max(1, k))
                            recall = (found / max(1, len(expected)))
                            ok = 1 if found > 0 else 0

                            det = {"expected": expected, "hits": got}
                            cur.execute("""INSERT OR REPLACE INTO test_results
                                           (ts, case_id, top_k, hits, expected, found, precision, recall, mrr, first_hit_rank, dt_ms, details_json)
                                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                        (ts, cid, k, len(got), len(expected), found, precision, recall, mrr, first_rank,
                                         dt_ms,
                                         json.dumps(det, ensure_ascii=False)))
                            con.commit()

                            acc_p += precision;
                            acc_r += recall;
                            acc_mrr += mrr;
                            done += 1
                            self.after(0,
                                       lambda cid=cid, ok=ok, p=precision, r=recall, mrr=mrr, fr=first_rank, ms=dt_ms,
                                              h=len(got), e=len(expected), q=q:
                                       tv2.insert("", "end", values=(
                                       cid, "✓" if ok else "✗", f"{p:.2f}", f"{r:.2f}", f"{mrr:.2f}", fr or "-", ms, h,
                                       e, q)))
                            self.after(0, lambda: (
                            pb.config(value=done), lab.config(text=f"Ejecutando… {done}/{len(sel_ids)}")))
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror("Evaluación", f"Error: {e}", parent=win));
                    return

                n = max(1, len(sel_ids))
                mean_p = acc_p / n;
                mean_r = acc_r / n;
                mean_mrr = acc_mrr / n
                self.app._log("bp_eval_run", ts=ts, n=n, p=round(mean_p, 3), r=round(mean_r, 3), mrr=round(mean_mrr, 3),
                              k=k_global)
                self.after(0, lambda: lab.config(
                    text=f"Finalizado. Prec@k={mean_p:.2f} · Rec@k={mean_r:.2f} · MRR={mean_mrr:.2f} (n={n})"))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(bar, text="Ejecutar", command=run_now).pack(side="right")

    def _bp_show_last_results(self):
        from tkinter import messagebox
        self._bp_ensure_tables()
        with self.app.data._connect() as con:
            row = con.execute("SELECT MAX(ts) FROM test_results").fetchone()
            last = row[0] if row else None
            if not last:
                messagebox.showinfo("Resultados", "No hay ejecuciones registradas.", parent=self); return
            rows = con.execute("""SELECT c.id, c.q, r.top_k, r.hits, r.expected, r.found, r.precision, r.recall,
                                         r.mrr, r.first_hit_rank, r.dt_ms
                                  FROM test_results r
                                  JOIN test_cases c ON c.id=r.case_id
                                  WHERE r.ts=? ORDER BY c.id""", (last,)).fetchall()

        top = tk.Toplevel(self); top.title(f"Resultados: {last}"); top.geometry("1000x540")
        tv = ttk.Treeview(top, columns=("id","p","r","mrr","rank","ms","hits","exp","found","q"), show="headings")
        for c,t,w in (("id","ID",50),("p","Prec@",70),("r","Rec@",70),("mrr","MRR",70),("rank","1ª pos",70),
                      ("ms","ms",70),("hits","hits",60),("exp","exp",60),("found","aciertos",80),("q","Consulta",560)):
            tv.heading(c, text=t); tv.column(c, width=w, anchor=("e" if c not in ("id","q") else "w"))
        tv.pack(fill="both", expand=True)

        acc_p=acc_r=acc_mrr=0.0
        for (cid,q,k,hits,exp,found,p,r,mrr,fr,ms) in rows:
            acc_p+=p; acc_r+=r; acc_mrr+=mrr
            tv.insert("", "end", values=(cid, f"{p:.2f}", f"{r:.2f}", f"{mrr:.2f}", fr or "-", ms, hits, exp, found, q))

        n = max(1, len(rows))
        lab = ttk.Label(top, text=f"Medias  Prec@k={acc_p/n:.2f} · Rec@k={acc_r/n:.2f} · MRR={acc_mrr/n:.2f}  (n={n}, k variable por caso)")
        lab.pack(anchor="w", padx=8, pady=6)

    def _bp_compare_runs(self):
        from tkinter import messagebox
        self._bp_ensure_tables()
        with self.app.data._connect() as con:
            tss = [r[0] for r in con.execute("SELECT DISTINCT ts FROM test_results ORDER BY ts DESC").fetchall()]
        if len(tss) < 2:
            messagebox.showinfo("Comparar runs", "Necesitas al menos dos ejecuciones para comparar.", parent=self);
            return

        # UI selección de ts
        top = tk.Toplevel(self);
        top.title("Comparar runs");
        top.geometry("1040x560")
        row = ttk.Frame(top);
        row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Run A:").pack(side="left");
        cbA = ttk.Combobox(row, values=tss, state="readonly", width=24);
        cbA.current(0);
        cbA.pack(side="left", padx=(6, 20))
        ttk.Label(row, text="Run B:").pack(side="left");
        cbB = ttk.Combobox(row, values=tss, state="readonly", width=24);
        cbB.current(1);
        cbB.pack(side="left", padx=(6, 20))
        out = ttk.Label(row, text="");
        out.pack(side="left", padx=10)
        tv = ttk.Treeview(top, columns=(
        "id", "pA", "pB", "dP", "rA", "rB", "dR", "mA", "mB", "dM", "rankA", "rankB", "dRank", "q"), show="headings",
                          height=18)
        headers = [("id", "ID", 50), ("pA", "PrecA", 70), ("pB", "PrecB", 70), ("dP", "ΔPrec", 70),
                   ("rA", "RecA", 70), ("rB", "RecB", 70), ("dR", "ΔRec", 70),
                   ("mA", "MRRA", 70), ("mB", "MRRB", 70), ("dM", "ΔMRR", 70),
                   ("rankA", "1ªA", 60), ("rankB", "1ªB", 60), ("dRank", "Δ1ª", 60),
                   ("q", "Consulta", 520)]
        for c, t, w in headers:
            tv.heading(c, text=t);
            tv.column(c, width=w, anchor=("e" if c not in ("id", "q") else "w"))
        tv.pack(fill="both", expand=True, padx=8, pady=8)

        def run_diff():
            tsA, tsB = cbA.get(), cbB.get()
            if not tsA or not tsB or tsA == tsB:
                messagebox.showinfo("Comparar runs", "Elige dos runs distintos.", parent=top);
                return
            with self.app.data._connect() as con:
                A = {r[0]: r for r in con.execute(
                    """SELECT c.id, c.q, r.precision, r.recall, r.mrr, r.first_hit_rank
                       FROM test_results r JOIN test_cases c ON c.id=r.case_id WHERE r.ts=?""", (tsA,)).fetchall()}
                B = {r[0]: r for r in con.execute(
                    """SELECT c.id, c.q, r.precision, r.recall, r.mrr, r.first_hit_rank
                       FROM test_results r JOIN test_cases c ON c.id=r.case_id WHERE r.ts=?""", (tsB,)).fetchall()}
            for iid in tv.get_children(): tv.delete(iid)

            # acumula medias de deltas
            dP = dR = dM = dRank = 0.0;
            n = 0
            for cid in sorted(set(A.keys()) | set(B.keys())):
                qa = A.get(cid);
                qb = B.get(cid)
                q = (qa or qb)[1]
                pA, rA, mA, rkA = (qa[2], qa[3], qa[4], qa[5]) if qa else (0.0, 0.0, 0.0, None)
                pB, rB, mB, rkB = (qb[2], qb[3], qb[4], qb[5]) if qb else (0.0, 0.0, 0.0, None)
                d_p = (pB - pA);
                d_r = (rB - rA);
                d_m = (mB - mA)
                # Δ rank (positivo = mejora si B tiene rank menor que A)
                if rkA and rkB:
                    d_rank = (rkA - rkB)
                elif rkA and not rkB:
                    d_rank = -rkA
                elif rkB and not rkA:
                    d_rank = rkB
                else:
                    d_rank = 0
                tv.insert("", "end", values=(cid, f"{pA:.2f}", f"{pB:.2f}", f"{d_p:+.2f}",
                                             f"{rA:.2f}", f"{rB:.2f}", f"{d_r:+.2f}",
                                             f"{mA:.2f}", f"{mB:.2f}", f"{d_m:+.2f}",
                                             rkA or "-", rkB or "-",
                                             f"{d_rank:+d}" if isinstance(d_rank, int) else d_rank, q))
                dP += d_p;
                dR += d_r;
                dM += d_m
                try:
                    dRank += (d_rank if isinstance(d_rank, (int, float)) else 0)
                except Exception:
                    pass
                n += 1
            if n:
                out.config(
                    text=f"ΔMedias  Prec={dP / n:+.3f} · Rec={dR / n:+.3f} · MRR={dM / n:+.3f} · Δ1ªpos={(dRank / n):+.2f }  (A→B)")
            else:
                out.config(text="(sin datos)")

        ttk.Button(row, text="Comparar", command=run_diff).pack(side="right")

    def _bp_show_last_details(self):
        from tkinter import messagebox
        import sys, subprocess

        self._bp_ensure_tables()
        with self.app.data._connect() as con:
            row = con.execute("SELECT MAX(ts) FROM test_results").fetchone()
            last = row[0] if row else None
            if not last:
                messagebox.showinfo("Detalles", "No hay ejecuciones registradas.", parent=self);
                return
            rows = con.execute("""SELECT c.id, c.q, r.details_json
                                  FROM test_results r
                                  JOIN test_cases c ON c.id=r.case_id
                                  WHERE r.ts=? ORDER BY c.id""", (last,)).fetchall()

        top = tk.Toplevel(self);
        top.title(f"Detalles del run: {last}");
        top.geometry("1100x620")
        left = ttk.Frame(top, padding=6);
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Casos").pack(anchor="w")
        tv = ttk.Treeview(left, columns=("id", "q"), show="headings", height=22)
        tv.heading("id", text="ID");
        tv.heading("q", text="Consulta")
        tv.column("id", width=60);
        tv.column("q", width=340)
        tv.pack(fill="y", expand=False)

        mid = ttk.Frame(top, padding=6);
        mid.pack(side="left", fill="both", expand=True)
        ttk.Label(mid, text="Esperados").pack(anchor="w")
        tv_exp = ttk.Treeview(mid, columns=("p",), show="headings", height=12)
        tv_exp.heading("p", text="Ruta");
        tv_exp.column("p", width=450)
        tv_exp.pack(fill="x", expand=False, pady=(0, 8))
        ttk.Label(mid, text="Hits").pack(anchor="w")
        tv_hit = ttk.Treeview(mid, columns=("p",), show="headings", height=12)
        tv_hit.heading("p", text="Ruta");
        tv_hit.column("p", width=450)
        tv_hit.pack(fill="x", expand=False)

        bar = ttk.Frame(top, padding=6);
        bar.pack(side="right", fill="y")

        def _open_selected(tree):
            sel = tree.selection()
            if not sel: return
            p = tree.item(sel[0], "values")[0]
            try:
                if os.name == "nt":
                    os.startfile(p)  # Windows
                elif sys.platform == "darwin":
                    subprocess.call(["open", p])
                else:
                    subprocess.call(["xdg-open", p])
            except Exception as e:
                messagebox.showerror("Abrir", f"No pude abrir la ruta:\n{e}", parent=top)

        ttk.Button(bar, text="Abrir esperado", command=lambda: _open_selected(tv_exp)).pack(pady=4)
        ttk.Button(bar, text="Abrir hit", command=lambda: _open_selected(tv_hit)).pack(pady=4)

        data = {}
        for cid, q, js in rows:
            data[cid] = (q, js)
            tv.insert("", "end", values=(cid, q))

        def on_sel(_e=None):
            sel = tv.selection()
            if not sel: return
            cid = int(tv.item(sel[0], "values")[0])
            q, js = data[cid]
            try:
                det = json.loads(js or "{}")
            except Exception:
                det = {}
            exp = det.get("expected") or []
            hits = det.get("hits") or []
            for t in (tv_exp, tv_hit):
                for iid in t.get_children(): t.delete(iid)
            for p in exp: tv_exp.insert("", "end", values=(p,))
            for p in hits: tv_hit.insert("", "end", values=(p,))

        tv.bind("<<TreeviewSelect>>", on_sel)

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

    # >>> PATCH LOGS: helpers de eventos (pegar íntegro)
    import time
    from datetime import datetime
    import traceback

    @staticmethod
    def _parse_ts_to_epoch(ts_val):
        """Devuelve epoch (float). Acepta int/float, string ISO, string numérica, o None."""
        if ts_val is None:
            return time.time()
        if isinstance(ts_val, (int, float)):
            return float(ts_val)
        if isinstance(ts_val, str):
            s = ts_val.strip()
            # 1) ¿número en texto?
            try:
                return float(s)
            except ValueError:
                pass
            # 2) ISO estándar (admite 'YYYY-MM-DD HH:MM:SS' y 'YYYY-MM-DDTHH:MM:SS[.mmm][Z|±hh:mm]')
            try:
                iso = s[:-1] if s.endswith("Z") else s
                return datetime.fromisoformat(iso).timestamp()
            except Exception:
                pass
            # 3) Algunos formatos comunes
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%H:%M:%S"):
                try:
                    return datetime.strptime(s, fmt).timestamp()
                except Exception:
                    continue
        # Fallback
        return time.time()

    @staticmethod
    def _coerce_event(ev):
        """Normaliza un evento a dict con claves ts(float), level, src, msg."""
        if not isinstance(ev, dict):
            return {
                "ts": time.time(),
                "level": "INFO",
                "src": "app",
                "msg": str(ev),
            }
        ts = _parse_ts_to_epoch(ev.get("ts"))
        level = (ev.get("level") or ev.get("lvl") or "INFO").upper()
        if level not in ("DEBUG", "INFO", "WARN", "WARNING", "ERROR", "SUCCESS"):
            level = "INFO"
        if level == "WARNING":
            level = "WARN"
        src = ev.get("src") or ev.get("where") or ev.get("origin") or ""
        msg = ev.get("msg") or ev.get("message") or repr(ev)
        return {"ts": ts, "level": level, "src": src, "msg": msg}

    def _ensure_console_tags(self):
        """Configura los tags si no existen (colores opcionales). Llamar una vez."""
        txt = self.txt_console
        try:
            txt.tag_config("lvl_DEBUG", foreground="#888888")
            txt.tag_config("lvl_INFO", foreground="#222222")   # o "#000000"
            txt.tag_config("lvl_WARN", foreground="#E6A700")
            txt.tag_config("lvl_ERROR", foreground="#FF5555", underline=1)
            txt.tag_config("lvl_SUCCESS", foreground="#2ECC71")
        except Exception:
            pass

    # <<< PATCH LOGS: helpers de eventos
    # >>> PATCH LOGS: recarga segura de consola (pegar íntegro)
    def _append_console_line(self, ev):
        ev = self._coerce_event(ev)
        ts_str = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        level = ev["level"]
        src = f"{ev['src']}: " if ev.get("src") else ""
        line = f"{ts_str} [{level}] {src}{ev['msg']}\n"
        tag = f"lvl_{level}"
        try:
            self.txt_console.insert("end", line, (tag,))
            self.txt_console.see("end")
        except Exception:
            # último recurso
            self.txt_console.insert("end", line)
            self.txt_console.see("end")

    def _reload_console(self, events):
        if not hasattr(self, "_console_tags_ready"):
            self._ensure_console_tags()
            self._console_tags_ready = True
        for ev in events:
            try:
                self._append_console_line(ev)
            except Exception as e:
                # Nunca romper el loop por un mal evento
                try:
                    self._append_console_line({
                        "level": "ERROR",
                        "src": "LOGS",
                        "msg": f"Evento inválido: {e!r} // {ev!r}"
                    })
                except Exception:
                    pass

    def _refresh_logs_tab(self):
        # Obtiene snapshot y además refresca la columna izquierda (estado y top keywords)
        try:
            # --- Estado del índice (lee del SQLite) ---
            try:
                st = self.app.data.stats()  # devuelve (tablas, keywords, notas) o dict según tu impl.
                # Soporta ambos formatos (tu footer usa tupla):
                if isinstance(st, tuple):
                    tables, kw, notes = st
                else:
                    tables = st.get("tables", "?");
                    kw = st.get("keywords", "?");
                    notes = st.get("notes", "?")
                try:
                    from pathlib import Path
                    dbname = Path(self.app.data.db_path).name
                except Exception:
                    dbname = str(getattr(self.app.data, "db_path", "index_cache.sqlite"))
                self.lbl_idx.config(text=f"{dbname} (tablas: {tables}; keywords: {kw}; notas: {notes})")
            except Exception:
                pass

            # --- Estado del modelo (recicla el texto que ya pintas en la barra superior) ---
            try:
                self.lbl_llm.config(text=self.app.lbl_model.cget("text"))
            except Exception:
                try:
                    # Fallback muy simple
                    mdl_ok = bool(getattr(getattr(self.app, "llm", None), "model", None))
                    self.lbl_llm.config(text="Modelo: cargado" if mdl_ok else "Modelo: (no cargado)")
                except Exception:
                    pass

            # --- Top keywords (50 primeras) ---
            try:
                # Vacía el árbol y recarga desde SQLite
                for it in self.tv_top.get_children():
                    self.tv_top.delete(it)
                top = []
                try:
                    # Si tu método admite 'limit', úsalo; si no, deja la llamada simple
                    top = self.app.data.keywords_top(limit=50)
                except TypeError:
                    top = self.app.data.keywords_top()
                # Espera pares (keyword, n)
                for kw, n in (top or []):
                    self.tv_top.insert("", "end", values=(kw, n))
            except Exception:
                pass

            # --- Consola (lo que ya tenías) ---
            snap = []
            try:
                snap = self.app.events_snapshot()
            except Exception as e:
                snap = [{"level": "WARN", "src": "LOGS", "msg": f"Sin snapshot: {e!r}", "ts": time.time()}]
            self._reload_console(snap)

        except Exception as e:
            self._append_console_line({"level": "ERROR", "src": "LOGS", "msg": f"_refresh_logs_tab falló: {e!r}"})

    # <<< PATCH LOGS
    # >>> PATCH LOGS: temporizador a prueba de bombas (pegar íntegro)
    def _tick_logs(self):
        """Temporiza la recarga de logs sin permitir que una excepción mate el loop."""
        try:
            self._refresh_logs_tab()
        except Exception as e:
            # Capturamos todo para evitar bucles de callback fallidos
            try:
                self._append_console_line({"level": "ERROR", "src": "LOGS", "msg": f"_tick_logs: {e!r}"})
                traceback.print_exc()
            except Exception:
                pass
        finally:
            # Reprograma el siguiente tick
            try:
                self.after(500, self._tick_logs)
            except Exception:
                pass

    # <<< PATCH LOGS

    def on_new_event(self, ev):
        try:
            self.txt_log.config(state="normal")
            self._append_console_line(ev)
            self.txt_log.config(state="disabled"); self.txt_log.see("end")
        except Exception:
            pass



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
        tv = ttk.Treeview(top, columns=("path", "kw"), show="headings")
        tv.heading("path", text="Ruta")
        tv.heading("kw", text="Keyword")
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

        try:
            _import_rag_patch()
        except Exception:
            pass

        top = tk.Toplevel(self)
        top.title("PACqui — Admin (privado)")
        top.geometry("1400x820")
        app = base.OrganizadorFrame(top)
        app.pack(fill="both", expand=True)

        def _on_close():
            # Al cerrar el Admin, refrescamos el visor del FRONT con la base elegida.
            try:
                cfg = _load_cfg()  # mismo helper del front
                base_path = cfg.get("base_path")
                v = getattr(self.master, "visor", None)
                if base_path and v:
                    v.base_path = Path(base_path)
                    try:
                        v.lbl_base.configure(text=v._base_label())
                    except Exception:
                        pass
                    try:
                        v._build_dir_tree()
                    except Exception:
                        pass
            finally:
                top.destroy()

        top.protocol("WM_DELETE_WINDOW", _on_close)

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
            self.app.llm.load(mp, ctx=ctx)
            self.app.lbl_model.config(text=f"Modelo: {Path(mp).name} (ctx={ctx})")
            cfg = _load_cfg();
            cfg["model_path"] = mp;
            cfg["model_ctx"] = ctx;
            _save_cfg(cfg)
            messagebox.showinfo(APP_NAME, "Modelo cargado en backend.")
            self.app._log("model_loaded",
                          model=os.path.basename(mp),
                          ctx=ctx,
                          threads=self.app.llm.threads,
                          batch=self.app.llm.n_batch)

            # Calienta en background: embedder, SQLite RAG y mini chat (compila grafo/KV)
            self.app.llm.warmup_async()


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
                # 3) pie de estado + aviso
                try:
                    self.after(0, self.app._refresh_footer)
                    self.after(0, self._refresh_logs_tab)  # refresca la pestaña “Logs y estado”
                except Exception:
                    pass

                self.after(0, lambda: messagebox.showinfo(
                    APP_NAME,
                    "Índice importado correctamente.\n\n"
                    f"Filas: {stats.get('rows', 0)}  ·  Docs: {stats.get('docs', 0)}  ·  "
                    f"Kws añadidas: {stats.get('kws_added', 0)}  ·  Notas: {stats.get('notes_set', 0)}",
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
        hits = getattr(self.asst, "_hits", []) or []
        if not hits:
            messagebox.showinfo(APP_NAME, "Todavía no hay fuentes para mostrar. Lanza una consulta primero.")
            return
        try:
            pan = SourcesPanel(self)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo abrir el panel de fuentes:\n{e}")
            return

        # Intento principal + fallback seguro a estructura simple (UNA sola vez)
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

    def _save_sources(self):
        """Graba TODAS las fuentes del índice (sin límite de 100)."""
        from tkinter import messagebox
        import os, sqlite3, time

        t0 = time.time()

        # 1) Recojo TODAS las rutas candidatas directamente del índice
        #    (unión de doc_keywords y doc_notes), sin pasar por el ranking.
        all_paths = []
        notes = {}
        try:
            con = sqlite3.connect(self.app.data.db_path)
            cur = con.cursor()

            # Todas las rutas conocidas por el índice
            rows = cur.execute("""
                SELECT LOWER(fullpath) FROM doc_keywords
                UNION
                SELECT LOWER(fullpath) FROM doc_notes
            """).fetchall()
            all_paths = sorted({r[0] for r in rows})

            # Notas (si existen) por ruta
            notes = dict(cur.execute("""
                SELECT LOWER(fullpath), MAX(note)
                FROM doc_notes
                WHERE note IS NOT NULL AND TRIM(note) <> ''
                GROUP BY LOWER(fullpath)
            """).fetchall())
            con.close()
        except Exception:
            all_paths, notes = [], {}

        # 2) Si por lo que sea el índice no devuelve nada, uso fallback:
        #    reconsulta ancha del índice con la última query y top_k muy alto.
        if not all_paths:
            try:
                q_last = (getattr(self.asst, "_last_query", "") or "").strip()
            except Exception:
                q_last = ""
            try:
                big = self.asst.llm._index_hits(q_last, top_k=50000, max_note_chars=0, prefer_only=None) or []
                # normalizo a la misma estructura
                all_paths = []
                for h in big:
                    p = (h.get("path") or "").strip()
                    if p:
                        all_paths.append(p.lower())
                        if h.get("note"):
                            notes[p.lower()] = h["note"]
                all_paths = sorted(set(all_paths))
            except Exception:
                pass

        if not all_paths:
            messagebox.showinfo("PACqui", "No hay fuentes para grabar. Lanza una consulta primero.", parent=self)
            return

        # 3) Construyo items únicos con nombre (=basename) y nota (si hay)
        items = [{"path": p, "name": os.path.basename(p), "note": notes.get(p, ""), "weight": 1.0}
                 for p in all_paths]

        # 4) Guardo TODO en pinned_sources (sin ningún corte)
        from meta_store import MetaStore
        ms = MetaStore(self.app.data.db_path)
        n = ms.save_pinned_sources(items)
        try:
            fixed = ms.backfill_pinned_names()
            if fixed:
                print(f"[pinned] Nombres corregidos en DB: {fixed}")
        except Exception:
            pass

        dt = int((time.time() - t0) * 1000)
        messagebox.showinfo("PACqui", f"Fuentes grabadas: {n} (de {len(items)} candidatas) · {dt} ms", parent=self)

        # refresco badge
        try:
            self._refresh_pinned_badge()
        except Exception:
            pass

    def _open_pinned_sources_viewer(self):
        """Abre el visor/gestor de 'Fuentes grabadas'."""
        try:
            PinnedSourcesDialog(self, self.app.data.db_path, on_change=self._refresh_pinned_badge)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo abrir el visor:\n{e}", parent=self)

    def _clear_sources(self):
        if not messagebox.askyesno(APP_NAME, "¿Borrar TODAS las fuentes grabadas?", parent=self):
            return
        try:
            ms = MetaStore(self.app.data.db_path)
            ms.clear_pinned_sources()
            messagebox.showinfo(APP_NAME, "Fuentes borradas.", parent=self)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudieron borrar las fuentes:\n{e}", parent=self)

    def _refresh_pinned_badge(self):
        try:
            ms = MetaStore(self.app.data.db_path)
            m = ms.count_pinned_sources()
        except Exception:
            m = 0
        try:
            # Muestra el nº de FUENTES GRABADAS (persistentes)
            self.btn_fuentes.configure(text=f"Fuentes ({m})")
        except Exception:
            pass


class ChatWithLLM(ChatFrame):
    def __init__(self, master, data: DataAccess, llm: LLMService, app=None):
        self.llm = llm
        self.app = app or master.winfo_toplevel()  # <— referencia al AppRoot
        self.notes_only = False  # usamos saludo LLM + observaciones/rutas deterministas
        self._last_choice = None  # {"hit": {...}, "reasons": "texto", "query": "…"}
        self._last_query = None
        self._ext_filter = set()  # {".pdf"} | {".doc",".docx"} | set()
        #Timeouts de primer token (configurables)
        self.FIRST_TOKEN_TIMEOUT_MIN_WARM_S = 45.0  # modelo “caliente” (primer token)
        self.FIRST_TOKEN_TIMEOUT_MIN_COLD_S = 90.0  # modelo “frío” (primer token)
        self.FIRST_TOKEN_TIMEOUT_MAX_S = 480.0  # techo duro
        self.FIRST_TOKEN_TIMEOUT_PER_IN_TOKEN_S = 0.12  # factor por token de entrada

        def _update_ext_filter(qlow: str):
            import re
            # Activa filtro persistente
            if re.search(r"\bsolo\s+(en\s+)?pdfs?\b", qlow):
                self._ext_filter = {".pdf"}
            elif re.search(r"\bsolo\s+(en\s+)?docx?\b", qlow):
                self._ext_filter = {".docx", ".doc"}
            elif re.search(r"\bsolo\s+(en\s+)?docs?\b", qlow):
                self._ext_filter = {".doc", ".docx"}
            # Limpia filtro
            elif re.search(r"\b(limpiar|quitar|sin)\s+filtro(s)?\b", qlow):
                self._ext_filter = set()
            # Nada: mantiene el último filtro
            return self._ext_filter
        self._update_ext_filter = _update_ext_filter


        super().__init__(master, data)

    def _log_qa_if_possible(self, full_answer: str):
        try:
            from meta_store import MetaStore
            dbp = self.app.data.db_path if hasattr(self.app, "data") else getattr(self.llm, "db_path", None)
            if not dbp:
                return
            ms = MetaStore(dbp)
            q = getattr(self, "_last_query", "") or ""

            srcs = []
            for h in (getattr(self, "_last_hits", None) or [])[:5]:
                if not isinstance(h, dict):
                    continue
                srcs.append({
                    "path": (h.get("path") or "").strip(),
                    "name": h.get("name"),
                    "note": h.get("note"),
                    "score": float(h.get("score") or 0.0),
                })

            took_ms = None
            try:
                import time
                t0 = getattr(self, "_turn_start_ts", None)
                if t0:
                    took_ms = int((time.time() - t0) * 1000)
            except Exception:
                pass

            ms.log_qa(query=q, answer=(full_answer or ""), model="PACqui-LLM",
                      sources=srcs, tokens_in=None, tokens_out=None, took_ms=took_ms)
        except Exception:
            pass

    def _user_asks_why_this(self, q: str) -> bool:
        import re
        ql = (q or "").lower()
        return bool(
            re.search(r"\bpor\s*qué\b", ql)
            and (
                    re.search(r"\b(este|esta|estos|estas|éste|ésta|ésos?|esas?|eso)\b", ql)
                    or re.search(r"\b(documento|documentos|fuente|fuentes|recomendaci[oó]n|elecci[oó]n)\b", ql)
                    or re.search(r"\b(has\s+elegido|elegiste|has\s+seleccionado|seleccionaste|elegid[oa]s?)\b", ql)
            )
        )

    def _user_wants_choice(self, q: str) -> bool:
        import re
        ql = (q or "").lower()
        patterns = [
            r"\b(elige|escoge|selecciona)\b",
            r"\b(recomiend[ao]s?|recomendar[ií]as?)\b",
            r"\b(cu[aá]l\s+me\s+recomiendas?)\b",
            r"\b(cu[aá]l\s+(de\s+estos|de\s+estas)|cu[aá]l\s+es\s+mejor)\b",
        ]
        return any(re.search(p, ql) for p in patterns)

    def _choice_reasons(self, hit: dict, q: str) -> str:
        import os
        ql = (q or "").lower()
        title = (hit.get("name") or os.path.basename(hit.get("path", "")) or "").lower()
        note = (hit.get("note") or "").lower()
        ext = (os.path.splitext(hit.get("path", ""))[1] or "").lower()

        kws = set()
        for t in ("feader", "feaga", "pago", "pagos", "pepac", "circular", "fichero", "ficheros"):
            if t in title or t in note or t in ql:
                kws.add(t)

        reasons = []
        if {"feader", "feaga"} & kws:
            reasons.append("menciona **FEADER/FEAGA** en el título o la nota")
        if {"pago", "pagos"} & kws:
            reasons.append("se centra en **pagos**")
        if "pepac" in kws:
            reasons.append("está alineado con **PEPAC**")
        if "circular" in kws:
            reasons.append("es **circular** normativa/procedimental")
        if "fichero" in kws or "ficheros" in kws:
            reasons.append("trata sobre **ficheros de intercambio**")

        if hit.get("note"):
            reasons.append("tiene **observaciones** en el índice")
        if ext == ".pdf":
            reasons.append("es **PDF** (suele ser la fuente maestra)")

        if not reasons:
            import re
            ql2 = (q or "").lower()
            toks = re.findall(r"[a-z0-9]{3,}", ql2)
            GENERIC = {
                "documento", "documentos", "doc", "docs", "pdf", "docx", "archivo", "archivos",
                "base", "datos", "repositorio", "sistema", "proceso", "procesos",
                "nuevo", "nueva", "tecnico", "tecnicos", "incorporacion", "onboarding",
                "proyecto", "proyectos", "lanzadera", "ticketing", "severidad", "analisis",
                "requerimiento", "requerimientos"
            }
            rare = [t for t in toks if t not in GENERIC and len(t) >= 5]
            blob = " ".join([
                title,
                (hit.get("path", "") or "").lower(),
                note
            ])
            if rare and not any(t in blob for t in rare):
                reasons.append("no contiene **" + " / ".join(sorted(set(rare))[:2]) + "**; candidato débil")
            else:
                reasons.append("tiene **alta similitud** con tu consulta")

        # Junta bonito
        txt = "; ".join(reasons)
        # Limpia duplicados por si acaso
        parts = []
        seen = set()
        for r in [p.strip() for p in txt.split(";") if p.strip()]:
            if r not in seen:
                parts.append(r);
                seen.add(r)
        return "; ".join(parts)

    def _build_ui(self):
        # Construye la UI base del ChatFrame (incluye Entry + botón "Enviar")
        super()._build_ui()
        # Sustituye el Entry (una línea) por un Text multilínea
        row = self.ent_input.master
        try:
            self.ent_input.destroy()
        except Exception:
            pass

        # Caja de texto grande (ajusta height si la quieres más/menos alta)
        self.txt_input = tk.Text(row, height=4, wrap="word")
        self.txt_input.configure(font=("Segoe UI", 12))
        self.txt_input.pack(side="left", fill="x", expand=True, padx=(6, 8), pady=(4, 6))

        # --- Estilos y tamaño (Entry más alto, botón más grande) ---
        try:
            st = ttk.Style()
            # Aumenta tamaño global del estilo del botón grande
            st.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=(18, 10))
            # Nuevo estilo para el Entry
            st.configure("Big.TEntry", padding=(8, 6))

            # Aplica estilo y fuente 12 al Entry; sube la altura visual
            self.ent_input.configure(style="Big.TEntry", font=("Segoe UI", 12))
            self.ent_input.pack_configure(ipady=10, padx=(6, 8), fill="x", expand=True)
        except Exception:
            pass

        # --- Reencaminar ENTER y el botón "Enviar" al LLM ---
        # Reencaminar ENTER y el botón "Enviar" al LLM (Shift+Enter = nueva línea)
        try:
            def _on_return(e):
                # Shift+Enter -> nueva línea
                if (e.state & 0x0001):  # Shift
                    return
                self._send_llm()
                return "break"

            self.txt_input.bind("<Return>", _on_return)

            input_row = self.txt_input.master
            self.btn_send = None
            for ch in input_row.winfo_children():
                try:
                    if str(getattr(ch, "cget", lambda x: "")("text")).strip().lower() == "enviar":
                        ch.configure(command=self._send_llm, style="Big.TButton")
                        try:
                            ch.configure(width=max(10, ch.cget("width")))
                        except Exception:
                            pass
                        self.btn_send = ch
                        break
                except Exception:
                    continue
        except Exception:
            pass

        parent = self.ent_input.master



        # ... (spinner y checkbox ya están) ...


        # --- Spinner indeterminado (oculto por defecto) ---
        self.pb = ttk.Progressbar(parent, mode="indeterminate", length=120)
        self.pb.pack(side="left", padx=(8, 0))
        self.pb.stop()
        self.pb.pack_forget()
        # Evento de parada para el streaming
        import threading as _threading  # ya importado arriba, pero esto no molesta
        self.stop_event = getattr(self, "stop_event", None) or _threading.Event()
        self.stop_event.clear()

        # Botón Detener (queda deshabilitado hasta que empiece un stream)
        def _do_stop():
            self.stop_event.set()
            try:
                canc = getattr(self.llm, "cancel", None)
                if callable(canc): canc()
            except Exception:
                pass
            try:
                self._spinner_stop()
            except Exception:
                pass

        self.btn_stop = ttk.Button(parent, text="Detener", state="disabled", command=_do_stop)
        self.btn_stop.pack(side="left", padx=(8, 0))

        # --- Checkbox "Solo índice (sin LLM)" ---
        self.var_notes_only = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(
            parent,
            text="Solo índice (sin LLM)",
            variable=self.var_notes_only,
            command=lambda: setattr(self, "notes_only", bool(self.var_notes_only.get()))
        )
        chk.pack(side="left", padx=(8, 0))

    def _open_file_os(self, path: str):
        try:
            open_in_explorer(Path(path))
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Abrir", f"No se pudo abrir:\n{path}\n\n{e}", parent=self)

    def _open_folder_os(self, path: str):
        try:
            from pathlib import Path as _P
            open_in_explorer(_P(path).parent)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Abrir carpeta", f"No se pudo abrir la carpeta de:\n{path}\n\n{e}", parent=self)

    # --- NUEVO: helpers para el spinner ---
    # --- NUEVO: helpers para el spinner ---
    def _spinner_start(self):
        try:
            self.stop_event.clear()
        except Exception:
            pass
        try:
            # Usar SOLO pack para evitar conflictos grid/pack
            self.pb.pack_forget()
            self.pb.pack(side="left", padx=4)
            self.pb.start(12)
        except Exception:
            pass
        for w in ("ent_input", "txt_input", "btn_send"):
            try:
                getattr(self, w).configure(state="disabled")
            except Exception:
                pass
        try:
            self.btn_stop.configure(state="normal")
        except Exception:
            pass

    def _spinner_stop(self):
        try:
            self.pb.stop()
            self.pb.pack_forget()
        except Exception:
            pass
        try:
            self.btn_stop.configure(state="disabled")
        except Exception:
            pass
        for w in ("ent_input", "txt_input", "btn_send"):
            try:
                getattr(self, w).configure(state="normal")
            except Exception:
                pass
        # foco en el área de entrada multilinea si existe
        try:
            (getattr(self, "txt_input", None) or self.ent_input).focus_set()
        except Exception:
            pass

    def _get_user_text(self) -> str:
        try:
            w = getattr(self, "txt_input", None) or self.ent_input
            if isinstance(w, tk.Text):
                return w.get("1.0", "end-1c").strip()
            return (w.get() or "").strip()
        except Exception:
            return ""

    def _clear_user_text(self):
        try:
            w = getattr(self, "txt_input", None) or self.ent_input
            if isinstance(w, tk.Text):
                w.delete("1.0", "end")
            else:
                w.delete(0, "end")
        except Exception:
            pass

    def _import_index_sheet(self):
        from tkinter import messagebox
        messagebox.showinfo(APP_NAME,
                            "Para importar el índice, usa Admin ▸ Índice y herramientas ▸ Importar índice (Excel/CSV)…",
                            parent=self)

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



    def _persona_line_from_llm(self, query_text: str, titles: list[str], n: int) -> str:
        """
        Devuelve UNA frase breve y neutra (cortesía/ayuda). PROHIBIDO aportar datos.
        Si falla el modelo, devuelve un fallback fijo.
        """
        self.after(0, lambda: self.progress("Stream no abrió en tiempo → llamo al modelo sin streaming."))

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
        # Política estricta: no inventar si no hay contexto
        base_sys = (
            "Eres PACqui, asistente experto en PAC/PEPAC/SICOP. Responde SIEMPRE en español neutro. "
            "Usa EXCLUSIVAMENTE los FRAGMENTOS adjuntos como base factual. "
            "CITA cada afirmación relevante con [n] (n es el índice del fragmento) y NO inventes. "
            "Estructura la respuesta en: 1) Definición, 2) Para qué sirve en PAC/SICOP, "
            "3) Estructura o campos/formatos clave si aparecen, 4) Validaciones/requisitos si aparecen, "
            "5) Fuentes (lista de [n] con ruta). Si los fragmentos no contienen la información, dilo explícitamente."
        )

        # Contexto inicial (auto-escala con el ctx del modelo)
        ctx = int(getattr(self.llm, "ctx", 2048) or 2048)
        idx_top, idx_note_chars = 3, 180  # un poco más generoso
        rag_k, rag_frag_chars = 3, 260  # más fragmentos y algo más largos

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

        # 1) Construcción de contextos
        idx_ctx, hits = self.llm.build_index_context(user_text, top_k=idx_top, max_note_chars=idx_note_chars)
        rag_q = user_text
        if getattr(self, "_ext_filter", None):
            rag_q = user_text + " " + " ".join(ext.lstrip(".") for ext in self._ext_filter)
        rag_ctx = self.llm._rag_retrieve(rag_q, k=rag_k, max_chars=rag_frag_chars)

        # 2) Bloques secundarios
        try:
            obs_block, rutas_block, _ = self._build_obs_and_routes_blocks(hits, max_items=3, max_obs_chars=200)
        except Exception:
            obs_block, rutas_block = "", ""

        try:
            concept_block = self.llm.concept_context(user_text, max_chars=420, top_k=5) or ""
        except Exception:
            concept_block = ""

        # 3) Ensamblado con etiquetas que entiende el recortador del LLMService
        def build_system_text():
            parts = [base_sys]
            if concept_block: parts += ["[CONCEPTOS]", concept_block]
            if obs_block:     parts += ["[OBSERVACIONES]", obs_block]
            if rutas_block:   parts += ["[RUTAS]", rutas_block]
            if idx_ctx:       parts += ["[ÍNDICE]", idx_ctx]
            if rag_ctx:       parts += ["[FRAGMENTOS]", rag_ctx]
            return "\n\n".join(parts).strip()

        sys_full = build_system_text()

        # 4) Presupuesto: baja el overhead y sube la salida efectiva
        def used_effective():
            # margen prudente (chat-format) + pequeño fijo
            return int(1.15 * (toklen(sys_full) + toklen(user_text))) + 10

        used = used_effective()
        resp_budget = int(max_tokens)

        # 5) Ajuste iterativo hasta encajar en ctx
        for _ in range(10):
            if used + resp_budget <= ctx - 64:
                break
            if rag_ctx and len(rag_ctx) > 180:
                rag_frag_chars = max(180, int(rag_frag_chars * 0.80));
                rag_ctx = shrink(rag_ctx, rag_frag_chars)
            elif hits and idx_top > 1:
                idx_top = max(1, idx_top - 1)
                idx_note_chars = max(120, int(idx_note_chars * 0.85))
                idx_ctx, hits = self.llm.build_index_context(user_text, top_k=idx_top, max_note_chars=idx_note_chars)
            elif resp_budget > 96:
                resp_budget = max(96, int(resp_budget * 0.85))
            else:
                break
            sys_full = build_system_text()
            used = used_effective()

        # ⬅️ Aquí estaba el corte: quita el min(80, …) y dalo dinámico
        max_final = max(160, min(resp_budget, ctx - used - 64, 384))
        msgs = [{"role": "system", "content": sys_full},
                {"role": "user", "content": user_text}]
        return msgs, hits, max_final

    def _log_qa_if_possible(self, full_answer: str):
        """Guarda la Q/A en históricos (MetaStore) usando la DB del front."""
        try:
            from meta_store import MetaStore
            dbp = self.app.data.db_path if hasattr(self.app, "data") else getattr(self.llm, "db_path", None)
            if not dbp:
                return
            ms = MetaStore(dbp)
            q = getattr(self, "_last_query", "") or ""

            # mapear hits -> objetos compatibles con qa_sources (path, name, note, score)
            srcs = []
            for h in (getattr(self, "_last_hits", None) or [])[:5]:
                if not isinstance(h, dict):
                    continue
                srcs.append({
                    "path": (h.get("path") or "").strip(),
                    "name": h.get("name"),
                    "note": h.get("note"),
                    "score": float(h.get("score") or 0.0),
                })

            # tokens y tiempo si los tenemos
            took_ms = None
            try:
                import time
                t0 = getattr(self, "_turn_start_ts", None)
                if t0:
                    took_ms = int((time.time() - t0) * 1000)
            except Exception:
                pass

            # ¡OJO!: la firma es query=..., no "question"
            ms.log_qa(query=q, answer=(full_answer or ""), model="PACqui-LLM",
                      sources=srcs, tokens_in=None, tokens_out=None, took_ms=took_ms)
        except Exception:
            pass

    def _send_llm(self):
        from tkinter import messagebox
        import os, re, time, threading

        q = self._get_user_text()

        if not q:
            try:
                messagebox.showinfo(APP_NAME, "Escribe algo para enviar.")
            except Exception:
                pass
            return

        # eco del usuario + limpiar caja
        self._append_chat("Tú", q)
        try:
            self._clear_user_text()

        except Exception:
            pass

        qlow = q.lower().strip()
        qlow_clean = re.sub(r"\b(pacqui|pac|assistant)\b", "", qlow).strip()

        # saludos / small-talk sin índice
        if re.match(r"^(hola|buenas(?:\s+(tardes|noches))?|buenos\s+dias|hey|hello|gracias|ok|vale)\b", qlow_clean):
            self._append_chat("PACqui",
                              "¡Hola! 👋 Puedo buscar en tu repositorio y priorizar **PDF/DOCX**. "
                              "Dime una palabra clave (p. ej., *pagos FEADER*) o escribe *solo pdf* / *solo docx* para filtrar."
                              )
            return
        if re.search(r"\b(cómo\s+estás|como\s+estas|qué\s+tal|que\s+tal|cómo\s+te\s+va|como\s+te\s+va)\b", qlow_clean):
            self._append_chat("PACqui",
                              "¡Todo bien! 🙂 ¿En qué te ayudo del repositorio (puedo priorizar **PDF/DOCX**)?")
            return

        # actualizar filtro por extensión si el usuario dice "solo pdf/docx"
        try:
            if hasattr(self, "_update_ext_filter"): self._update_ext_filter(qlow)
        except Exception:
            pass

        # pintar fuentes sugeridas YA (ligero)
        hits = self._collect_hits(
            q, top_k=5, note_chars=220,
            prefer_only=(sorted(self._ext_filter) if getattr(self, "_ext_filter", None) else None)
        ) or []
        self._hits = hits
        self._last_query = q
        self._last_hits = hits
        try:
            self._fill_sources_tree(hits)
        except Exception:
            pass

        # Sincroniza el flag con el checkbox por si hubiera quedado desfasado
        try:
            self.notes_only = bool(self.var_notes_only.get())
        except Exception:
            pass

        # modo “Solo índice” o sin modelo → respuesta determinista
        if (not self.llm.is_loaded()) or self.notes_only:
            try:
                why = "modelo no cargado" if (not self.llm.is_loaded()) else "modo 'Solo índice' activo"
                self.progress(f"Bypass LLM → {why}. Respondo con observaciones/rutas.")
            except Exception:
                pass
            try:
                self._reply_with_observations(q)
            except Exception:
                self._append_chat("PACqui", "No hay modelo o está en modo 'Solo índice'.")
            return

        self.progress_reset("Preparando respuesta…")
        self.progress("Buscando en el índice…")
        # Bloquea la UI desde ya (aunque aún no haya stream)
        self._spinner_start()

        # worker: construye contexto corto y lanza streaming
        # worker: construye contexto corto y lanza streaming (versión unificada SIN has_idx/has_rag)
        def worker():


            try:
                # 1) Construye mensajes + presupuesto con el helper centralizado
                messages, hits, tok_out = self._compose_system_budgeted(q, max_tokens=768)

                # 2) Rutas debajo de la respuesta para coexistir con el modelo
                try:
                    obs_block, rutas_block, _ = self._build_obs_and_routes_blocks(hits, max_items=3, max_obs_chars=180)
                except Exception:
                    obs_block, rutas_block = "", ""
                suffix = ("Rutas sugeridas:\n\n" + rutas_block) if rutas_block else ""

                # 3) Lanzar streaming real (pintado progresivo + fallback integrados)
                self._turn_start_ts = time.time()  # métrica para históricos
                self.after(0, lambda: self._stream_llm_with_fallback(
                    messages, max_tokens=tok_out, temperature=0.1, suffix=suffix
                ))

            except Exception as e:
                _msg = f"[error] {type(e).__name__}: {e}"
                self.after(0, lambda m=_msg: self._append_chat("PACqui", m))
                self.after(0, self._spinner_stop)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _fill_sources_tree(self, hits):
        tv = getattr(self, "tv", None)
        if not tv: return
        tv.delete(*tv.get_children()); self.txt_note.delete("1.0", "end")
        for h in (hits or []):
            keyword_hint = (h.get("keywords") or "").split(";")[0].strip()
            name = os.path.basename(h["path"])
            tv.insert("", "end", values=(keyword_hint, name, h["path"]))

    def _collect_hits(self, query_text: str, top_k: int = 5, note_chars: int = 240, prefer_only=None):
        try:
            return self.llm._index_hits(query_text, top_k=top_k, max_note_chars=note_chars,
                                        prefer_only=prefer_only) or []
        except Exception:
            return []



    def _build_obs_and_routes_blocks(self, hits, max_items: int = 3, max_obs_chars: int = 220):
        """Devuelve (obs_block, rutas_block, hits_usados).
        - obs_block: solo entradas con nota (recortada)
        - rutas_block: SIEMPRE incluye hasta max_items rutas, aunque no tengan nota
        """
        used, obs_lines, ruta_lines = [], [], []
        for s in hits:
            if len(used) >= max_items:
                break
            name = s.get("name") or s.get("path") or "(sin nombre)"
            path = s.get("path") or ""
            note = (s.get("note") or "").strip()
            if note:
                if len(note) > max_obs_chars:
                    note = note[:max_obs_chars].rstrip() + "…"
                obs_lines.append(f"- {name}\n  Observaciones: {note}")
            ruta_lines.append(f"- {name}\n  Ruta: {path}")
            used.append(s)
        obs_block = "\n".join(obs_lines).strip()
        rutas_block = "\n".join(ruta_lines).strip()
        return obs_block, rutas_block, used

    # --- NUEVO: intención de selección ("elige el mejor") y rankeo determinista ---

    def _user_wants_choice(self, q: str) -> bool:
        import re
        ql = (q or "").lower()
        # elige/escoge/selecciona/recomiend(a|ame)/cuál es mejor/qué es más adecuado…
        return bool(re.search(
            r"\b(elige|escoge|selecciona|recomiend[aoeá]|recomiénd[aoeá]|"
            r"(cu[aá]l|que|qué).{0,14}(mejor|adecuad[oa]|m[aá]s\s+relevante))\b", ql))

    # --- pesos de 'pinned_sources' para sesgo positivo en el ranking ---
    try:
        from meta_store import MetaStore
        _pinned = {
            (r.get("path") or "").lower(): float(r.get("weight") or 1.0)
            for r in MetaStore(self.app.data.db_path).list_pinned_sources()
        }
    except Exception:
        _pinned = {}

    def _score_choice(self, hit: dict, q: str, _pinned: dict) -> tuple:
        """Devuelve una tupla de score para ordenar (mayor es mejor)."""
        import os, re
        from pathlib import Path
        qlow = (q or "").lower()
        toks = re.findall(r"[a-z0-9]{3,}", qlow)

        name = (hit.get("name") or os.path.basename(hit.get("path","")) or "").lower()
        path = (hit.get("path") or "").lower()
        kws  = (hit.get("keywords") or "").lower()
        note = (hit.get("note") or "").lower()
        ext  = Path(path).suffix.lower()

        # preferencia de formato (coherente con tu RAG): PDF/DOCX mejor que otros
        ext_bonus = {".pdf": 30, ".docx": 28, ".doc": 20}.get(ext, 0)

        # tokens en nombre/ruta/keywords (ponderación decreciente)
        token_bonus = sum(3 for t in toks if t in name) \
                    + sum(2 for t in toks if t in path) \
                    + sum(1 for t in toks if t in kws)

        # sesgo específico si preguntan por FEAGA/FEADER
        feaga_bias = 0
        if "feaga" in qlow:
            feaga_bias += (5 if "feaga" in name else 3 if "feaga" in path or "feaga" in kws else 0)
            feaga_bias -= 2 if ("feader" in name or "feader" in path or "feader" in kws) else 0
        if "feader" in qlow and "feaga" not in qlow:
            feaga_bias += (5 if "feader" in name else 3 if "feader" in path or "feader" in kws else 0)

        # ligera preferencia si hay observación en el índice
        note_bonus = 4 if note else 0

        # tu _index_hits ya devuelve un "score" proxy; lo usamos como parte del ranking
        base = float(hit.get("score", 0.0))

        # último tie-break: rutas más cortas (más cercanas a raíz tienden a ser “oficiales”)
        tiebreak_shorter_path = -len(path)

        # --- BONUS por frase exacta "control de coherencia" ---
        phrase_bonus = 0
        if re.search(r"control\s+(de\s+)?coherencia", qlow):
            if re.search(r"control\s+(de\s+)?coherencia", name):
                # Frase en el TÍTULO del fichero → boost fuerte
                phrase_bonus += 50
            elif re.search(r"control\s+(de\s+)?coherencia", path):
                # Frase en la RUTA (carpeta) → boost medio
                phrase_bonus += 30

        # --- BONUS por estar fijado como fuente ---
        pin_boost = 0.0
        w = _pinned.get(path.lower())  # usa la 'path' ya normalizada arriba
        if w is not None:
            # presencia: +12; ponderación: +8*(w-1)  (w=1 → +0 adicional)
            pin_boost = 12.0 + max(0.0, 8.0 * (w - 1.0))

        # ÚLTIMO tie-break: rutas más cortas tienden a ser “oficiales”
        tiebreak_shorter_path = -len(path)

        return (ext_bonus + token_bonus + feaga_bias + note_bonus + phrase_bonus + pin_boost,
                base,
                tiebreak_shorter_path)


    def _pick_best_hit(self, hits: list[dict], q: str) -> dict | None:
        import re
        ql = (q or "").lower()

        # 1) Si preguntan por "control de coherencia", restringe a los que tengan la frase en
        #    título o ruta (si hay alguno). Esto evita que gane "calidad..." por acumulación de puntos.
        if re.search(r"control\s+(de\s+)?coherencia", ql):
            exact = []
            for h in (hits or []):
                blob = ((h.get("name") or "") + " " + (h.get("path") or "")).lower()
                if re.search(r"control\s+(de\s+)?coherencia", blob):
                    exact.append(h)
            if exact:
                hits = exact

        from meta_store import MetaStore
        try:
            ms = MetaStore(self.app.data.db_path)
            _pinned = {(r.get("path") or "").lower(): float(r.get("weight") or 1.0)
                       for r in ms.list_pinned_sources()}
        except Exception:
            _pinned = {}

        # 2) Puntúa y ordena con el resto de criterios
        scored = [(self._score_choice(h, q, _pinned), h) for h in (hits or [])]

        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _format_choice_answer(self, best: dict, hits: list[dict], dt_ms: int, q: str) -> str:
        """Mensaje final: 1 recomendación + motivo corto + pie '— Origen: índice …'."""
        from pathlib import Path
        import os, re
        name = best.get("name") or best.get("path") or "(sin nombre)"
        path = best.get("path") or ""
        ext  = os.path.splitext(path)[1].upper()[1:] if os.path.splitext(path)[1] else ""
        kws  = (best.get("keywords") or "")
        note = (best.get("note") or "").strip()

        # Motivos (cortos y verificables)
        reasons = []
        ql = (q or "").lower()
        if ext in ("PDF","DOCX","DOC"):
            reasons.append(f"formato {ext}")
        if "feaga" in ql and re.search(r"\bfeaga\b", (name + " " + path + " " + kws).lower()):
            reasons.append("contiene “FEAGA”")
        if "feader" in ql and re.search(r"\bfeader\b", (name + " " + path + " " + kws).lower()):
            reasons.append("contiene “FEADER”")
        # overlap de palabras clave (máx 3 para no alargar)
        toks = re.findall(r"[a-z0-9]{3,}", ql)
        common = [t for t in toks if t in (kws.lower())]
        if common:
            reasons.append("palabras clave: " + ", ".join(sorted(set(common))[:3]))
        if not reasons and note:
            reasons.append("observaciones del índice coinciden")

        # Motivo explícito por frase exacta
        if re.search(r"control\s+(de\s+)?coherencia", ql):
            if re.search(r"control\s+(de\s+)?coherencia", (name or "").lower()):
                reasons.append('contiene la frase **"control de coherencia"** en el título')
            elif re.search(r"control\s+(de\s+)?coherencia", (path or "").lower()):
                reasons.append('contiene **"control de coherencia"** en la ruta')

        # pie '— Origen: índice …' coherente con tu UI
        try:
            db_name = Path(self.app.data.db_path).name
        except Exception:
            db_name = "index_cache.sqlite"
        foot = f"\n\n— Origen: índice {db_name} · {len(hits)} aciertos · {dt_ms} ms."

        motivo = ("Motivo: " + "; ".join(reasons) + ".") if reasons else ""
        rec = f"Te recomiendo **{name}**.\nRuta: {path}\n{motivo}".rstrip()
        return rec + foot


    def _compose_persona_from_obs(self, user_text: str, max_tokens: int = 192):
        """
        Prepara los mensajes para el LLM usando EXCLUSIVAMENTE las observaciones del índice.
        Si no hay observaciones, el que llama debe caer a _reply_with_observations().
        """
        # 1) Recogemos hits y construimos bloques OBS + RUTAS
        all_hits = self._collect_hits(
            user_text, top_k=5, note_chars=240,
            prefer_only=(sorted(self._ext_filter) if getattr(self, "_ext_filter", None) else None)
        )

        # El treeview se actualiza fuera, pero devolvemos también los hits
        obs_block, rutas_block, used_hits = self._build_obs_and_routes_blocks(all_hits, max_items=3, max_obs_chars=220)

        # 2) System: tono persona + prohibición de inventar
        base_sys = (
            "Eres PACqui, asistente experto en PAC/PEPAC/SICOP. Responde SIEMPRE en español neutro. "
            "Usa EXCLUSIVAMENTE los FRAGMENTOS adjuntos como base factual. "
            "CITA cada afirmación relevante con [n] (n es el índice del fragmento) y NO inventes. "
            "Estructura la respuesta en: 1) Definición, 2) Para qué sirve en PAC/SICOP, "
            "3) Estructura o campos/formatos clave si aparecen, 4) Validaciones/requisitos si aparecen, "
            "5) Fuentes (lista de [n] con ruta). Si los fragmentos no contienen la información, dilo explícitamente."
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

    def _append_stream_text(self, txt: str, end_turn: bool = False):
        # 1) Validaciones e instrumentación
        try:
            assert hasattr(self, "txt_chat"), "txt_chat no existe en este frame"
            assert self.txt_chat is not None, "txt_chat es None"
        except Exception as e:
            try:
                self.progress(f"_append_stream_text: {e}")
            except Exception:
                pass
            return

        # Normaliza (si tu AppRoot tiene normalizador)
        try:
            norm = self.app._normalize_text(txt) if hasattr(self.app, "_normalize_text") else str(txt)
        except Exception as e:
            try:
                self.progress(f"_append_stream_text normalize: {e}")
            except Exception:
                pass
            norm = str(txt)

        # (debajo de self.txt_chat.insert(...))
        try:
            # rango del último bloque insertado
            start_idx = self.txt_chat.index("end-1c linestart")
            end_idx = self.txt_chat.index("end-1c")
            self._linkify_paths(start_idx, end_idx)
        except Exception as e:
            try:
                self.progress(f"_append_stream_text linkify: {e}")
            except Exception:
                pass

        # Abrir el Text para edición
        try:
            self.txt_chat.configure(state="normal")
        except Exception as e:
            try:
                self.progress(f"_append_stream_text configure(normal): {e}")
            except Exception:
                pass

        # Etiqueta de turno (solo la primera vez por respuesta)
        try:
            if not hasattr(self, "_in_stream"):
                self._in_stream = False
            if not self._in_stream:
                # marca “estamos en stream” y pinta el encabezado de PACqui
                self._in_stream = True
                self.txt_chat.insert("end", "PACqui:\n", ("who",))
                self.txt_chat.tag_configure("who", font=("Segoe UI", 9, "bold"))
        except Exception as e:
            try:
                self.progress(f"_append_stream_text header: {e}")
            except Exception:
                pass

        # Cuerpo
        try:
            self.txt_chat.insert("end", norm)
            if end_turn:
                self.txt_chat.insert("end", "\n")
                self._in_stream = False
            self.txt_chat.see("end")
        except Exception as e:
            try:
                self.progress(f"_append_stream_text insert: {e}")
            except Exception:
                pass

        # Cerrar el Text
        try:
            self.txt_chat.configure(state="disabled")
        except Exception as e:
            try:
                self.progress(f"_append_stream_text configure(disabled): {e}")
            except Exception:
                pass

    def _open_path_at_cursor(self, widget, x, y):
        try:
            idx = widget.index(f"@{x},{y}")
            tags = widget.tag_names(idx)
            if "pathlink" not in tags:
                return
            # Recupera la ruta del tag
            ranges = widget.tag_prevrange("pathlink", idx + "+1c")
            if not ranges:
                ranges = widget.tag_nextrange("pathlink", "1.0")
            if not ranges:
                return
            start, end = ranges
            path = widget.get(start, end)
            import os, subprocess, sys
            # Abre con el visor del SO
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            try:
                self.progress(f"_open_path_at_cursor: {e}")
            except Exception:
                pass

    def _linkify_paths(self, start_idx, end_idx):
        try:
            t = self.txt_chat
            t.tag_configure("pathlink", underline=1, foreground="#0645AD")
            # Dos patrones típicos que mostramos en el chat
            for label in ("Ruta:", "Fuente:"):
                pos = start_idx
                while True:
                    pos = t.search(label, pos, stopindex=end_idx)
                    if not pos:
                        break
                    line_end = t.search("\n", pos, stopindex=end_idx) or end_idx
                    # extrae el texto tras "Ruta:" / "Fuente:" hasta el fin de línea
                    after = f"{pos}+{len(label)}c"
                    path = t.get(after, line_end).strip()
                    if path:
                        # calcula los índices exactos del path y taggéalo
                        t.tag_add("pathlink", after, f"{after}+{len(path)}c")
                    pos = line_end
            # Bind de click
            t.tag_bind("pathlink", "<Button-1>", lambda e: self._open_path_at_cursor(e.widget, e.x, e.y))
        except Exception as e:
            try:
                self.progress(f"_linkify_paths: {e}")
            except Exception:
                pass

    def _stream_llm_with_fallback(self, messages, max_tokens=768, temperature=0.2, suffix: str = ""):
        import threading, time, queue
        # UI: arranque
        self._spinner_start()
        if getattr(self, "stop_event", None):
            self.stop_event.clear()
        q = queue.Queue()
        state = {"tokens": 0, "last_ts": time.time(), "timed_out": False, "done": False, "watchdog_strikes": 0}
        out_buf = []

        """
        RAG
        MESSAGE
        START == ="""
        # Pregunta del usuario
        user_q = self.entry.get().strip() if hasattr(self, "entry") else (user_text if 'user_text' in locals() else "")

        # 1) Contextos: Índice + RAG (top-k y tamaño de fragmentos)
        idx_ctx, idx_hits = self.llm.build_index_context(user_q, top_k=6, max_note_chars=280)
        # --- Respeta el filtro de extensiones de la UI para el RAG ---
        q_for_rag = user_q
        try:
            extf = set(getattr(self, "_ext_filter", set()) or set())
        except Exception:
            extf = set()
        # El servicio RAG activa el filtro si ve estos tokens en la query
        if ".pdf" in extf or "pdf" in extf:
            q_for_rag += " pdf"
        if ".docx" in extf or "docx" in extf:
            q_for_rag += " docx"
        if ".doc" in extf or "doc" in extf:
            q_for_rag += " doc"

        rag_ctx = self.llm._rag_retrieve(q_for_rag, k=8, max_chars=1200)

        # 2) Cortafuegos anti-alucinación: si no hay fragmentos, no llamamos al LLM
        if not rag_ctx or not rag_ctx.strip():
            aviso = (
                "No encuentro fragmentos relevantes en el repositorio para responder con garantías. "
                "Prueba a afinar la búsqueda (p. ej., 'MIC', 'fichero de pago', 'SICOP', 'condicionalidad', "
                "o usa 'solo pdf' / 'solo docx')."
            )
            try:
                self._append_stream_text("PACqui: " + aviso, end_turn=True)
            except Exception:
                pass
            try:
                self._spinner_stop()
            except Exception:
                pass
            return

        # 3) System estricto (con RAG)
        base_sys = (
            "Eres PACqui, asistente experto en PAC/PEPAC/SICOP. Responde SIEMPRE en español neutro. "
            "Usa EXCLUSIVAMENTE los FRAGMENTOS adjuntos como base factual. "
            "CITA cada afirmación relevante con [n] (n es el índice del fragmento) y NO inventes. "
            "Estructura la respuesta en: 1) Definición, 2) Para qué sirve en PAC/SICOP, "
            "3) Estructura o campos/formatos clave si aparecen, 4) Validaciones/requisitos si aparecen, "
            "5) Fuentes (lista de [n] con ruta). Si los fragmentos no contienen la información, dilo explícitamente."
        )

        # 4) Mensaje de usuario (plantilla cerrada)
        ucontent = (
            f"Pregunta del usuario:\n{user_q}\n\n"
            "=== FRAGMENTOS (cítalos como [1], [2], ...) ===\n"
            f"{rag_ctx}\n\n"
            "=== ÍNDICE (observaciones/rutas del índice) ===\n"
            f"{idx_ctx}\n\n"
            "Instrucciones de uso:\n"
            "- Responde a la PREGUNTA usando exclusivamente los FRAGMENTOS; el ÍNDICE es solo apoyo contextual.\n"
            "- No pidas más datos ni hagas listas genéricas. Responde directamente.\n"
            "- Cita con [n] cada punto que derives de un fragmento.\n"
            "- Si no hay datos suficientes en los fragmentos para algún apartado, dilo."
        )

        # 5) Sobrescribe los 'messages' reales que enviaremos al modelo
        messages = [
            {"role": "system", "content": base_sys},
            {"role": "user", "content": ucontent},
        ]
        # === PATCH: forzar el prompt con RAG/ÍNDICE ===

        import os

        # System fuerte para prohibir alucinar
        base_sys = (
            "Eres PACqui, asistente experto en PAC/PEPAC/SICOP. Responde SIEMPRE en español neutro. "
            "Usa EXCLUSIVAMENTE los FRAGMENTOS adjuntos como base factual. "
            "CITA cada afirmación relevante con [n] (n es el índice del fragmento) y NO inventes. "
            "Estructura la respuesta en: 1) Definición, 2) Para qué sirve en PAC/SICOP, "
            "3) Estructura o campos/formatos clave si aparecen, 4) Validaciones/requisitos si aparecen, "
            "5) Fuentes (lista de [n] con ruta). Si los fragmentos no contienen la información, dilo explícitamente."
        )

        # Sustituimos los 'messages' reales que enviaremos al modelo
        messages = [
            {"role": "system", "content": base_sys},
            {"role": "user", "content": ucontent},
        ]

        # Respeto variable de entorno para el máximo de salida si existe
        try:
            mt_env = int(os.getenv("PACQUI_MAX_TOKENS", "0") or "0")

        except Exception:
            pass

        # Traza de presupuesto real (prompt + salida)
        try:
            ctx = int(getattr(self.llm, "ctx", 4096) or 4096)
            tin = sum(self.llm.count_tokens((m.get("content") or "")) for m in messages)
            print(f"[TOKENS] ctx={ctx}  in≈{tin}  out_max={int(max_tokens)}")
        except Exception:
            pass
        # === FIN PATCH ===

        # 3) Traza útil
        try:
            tin = self.llm.count_tokens(ucontent)
            print(f"[RAG] frags={(rag_ctx.count('[', 0) if rag_ctx else 0)}  idx_len={len(idx_ctx)}  in_tokens≈{tin}")
        except Exception:
            pass

        # === PATCH RAG MESSAGE END ===

        def _p(msg: str):
            try:
                self.after(0, lambda: self.progress(msg))
            except Exception:
                pass

        # Pintado en el hilo principal (drain de la queue)
        def _paint_loop():
            painted = False
            try:
                while True:
                    tok = q.get_nowait()
                    if tok is None:
                        state["done"] = True
                        break
                    out_buf.append(tok)
                    try:
                        self._append_stream_text(tok)
                        painted = True
                    except Exception:
                        # último recurso
                        try:
                            self.txt_chat.configure(state="normal")
                            self.txt_chat.insert("end", tok);
                            self.txt_chat.see("end")
                            self.txt_chat.configure(state="disabled")
                            painted = True
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                if not state["done"]:
                    self.after(15, _paint_loop)
                else:
                    # cierre UI
                    try:
                        if suffix:
                            self._append_stream_text("\n" + suffix)
                        self._append_stream_text("", end_turn=True)

                    except Exception:
                        pass
                    try:
                        self._spinner_stop()
                    except Exception:
                        pass
                    try:
                        t0 = getattr(self, "_turn_start_ts", None)
                        if t0: self.progress(f"Listo ({int((time.time() - t0) * 1000)} ms).")
                    except Exception:
                        pass
                    try:
                        self._log_qa_if_possible("".join(out_buf))
                    except Exception:
                        pass

        self.after(0, _paint_loop)

        # Fallback sin streaming (bloquea SOLO el worker)
        # Fallback preferente: streaming con completion (modo instruct) para sacar 1er token MUY pronto
        def _fallback(
                reason: str,
                _messages=messages,
                _max_tokens=max_tokens,
                _temperature=temperature
        ):

            _p(f"Fallback preferente (completion stream) — {reason}…")
            import threading, time

            # Estimación para deadline y logging
            try:
                est_in = sum(self.llm.count_tokens((m.get("content") or "")) for m in (messages or []))
            except Exception:
                est_in = 0
            DEADLINE = max(60.0, min(240.0, 15.0 + 0.10 * float(est_in)))
            _p(f"Fallback: tokens_in≈{int(est_in)}, deadline≈{int(DEADLINE)}s.")

            result = {"txt": "", "err": None, "done": False}

            def _to_instruct(_msgs: list[dict]) -> str:
                # Instruct simple y robusto (modelo instruct o genérico)
                sys_part = "\n".join([m.get("content", "") for m in _msgs if m.get("role") == "system"]).strip()
                user_parts = [m.get("content", "") for m in _msgs if m.get("role") != "system"]
                user_text = user_parts[-1] if user_parts else ""
                pre = f"<<SYS>>{sys_part}<<SYS>>\n" if sys_part else ""
                return f"{pre}Usuario: {user_text}\nAsistente:"

            def _call():
                try:
                    # 1) completion STREAMING (prioritario por latencia de 1er token)
                    prompt = _to_instruct(_messages)
                    model = getattr(self.llm, "model", None)
                    if model is None:
                        raise RuntimeError("model no disponible")

                    out_chunks = []
                    for chunk in model.create_completion(prompt=prompt, temperature=_temperature,
                                                         max_tokens=_max_tokens, stream=True):

                        if getattr(self, "stop_event", None) and self.stop_event.is_set():
                            break
                        delta = ""
                        try:
                            delta = (chunk.get("choices") or [{}])[0].get("text", "")
                        except Exception:
                            pass
                        if delta:
                            t = self.app._normalize_text(delta) if hasattr(self.app, "_normalize_text") else delta
                            out_chunks.append(t)
                            # Pintado progresivo
                            try:
                                self.after(0, lambda tt=t: self._append_stream_text(tt))
                            except Exception:
                                pass
                            # Aunque no haya terminado, guarda progreso parcial por si corta el DEADLINE
                            result["txt"] = "".join(out_chunks)

                    # 2) Si por cualquier motivo no hay texto, probamos chat no-stream como último recurso
                    if not result["txt"]:
                        try:
                            resp = self.llm.chat(messages=_messages, max_tokens=_max_tokens,
                                                 temperature=_temperature, stream=False)

                            ch0 = (resp.get("choices") or [{}])[0]
                            txt = (ch0.get("message") or {}).get("content", "") or ch0.get("text", "") or ""
                            result["txt"] = txt
                        except Exception as e2:
                            result["err"] = str(e2)
                except Exception as e:
                    result["err"] = str(e)
                finally:
                    result["done"] = True

            t0 = time.time()
            t = threading.Thread(target=_call, daemon=True)
            t.start()

            while not result["done"] and (time.time() - t0) < DEADLINE:
                time.sleep(0.25)

            if not result["done"]:
                # Cancelación y texto amable (dejando lo ya pintado)
                try:
                    if hasattr(self.llm, "cancel") and callable(self.llm.cancel):
                        self.llm.cancel()
                except Exception:
                    pass
                result["err"] = (result["err"] or "timeout")
                result["done"] = True

            # Cierre amable
            txt = (result["txt"] or "").strip()
            if not txt:
                txt = "Ahora mismo no he podido generar respuesta del modelo."
            else:
                # cierre de pintado (no duplicar: el paint-loop añade el sufijo)
                try:
                    # añade el fragmento final al buffer para el log
                    out_buf.append("")
                    self._log_qa_if_possible("".join(out_buf) + txt)
                except Exception:
                    pass
            try:
                q.put(None)  # cerrar paint loop
            except Exception:
                pass

            _p("Fallback completado.")

        # --- Fallback guardado: evita dobles lanzamientos y lo ejecuta en hilo aparte ---
        _fallback_lock = threading.Lock()

        def _start_fallback(reason: str):
            with _fallback_lock:
                if state.get("fallback_started"):
                    return
                state["fallback_started"] = True
            threading.Thread(target=lambda: _fallback(reason), daemon=True).start()

        # Watchdog primer token
        def _watchdog():
            # 1) Estimar tokens de entrada para dar margen realista al 1er token (CPU)
            try:
                est_in = sum(self.llm.count_tokens((m.get("content") or "")) for m in (messages or []))
            except Exception:
                est_in = 0

            # 2) Timeout dinámico: base + k*s por token   [min 45/75 .. max 300]  (config. en self.*)

            #    - si está “caliente” (self.llm._warmed): mínimo 35s
            #    - si está “frío”: mínimo 60s
            base_min = (self.FIRST_TOKEN_TIMEOUT_MIN_WARM_S
                        if getattr(self.llm, "_warmed", False)
                        else self.FIRST_TOKEN_TIMEOUT_MIN_COLD_S)

            TIMEOUT = max(
                base_min,
                min(self.FIRST_TOKEN_TIMEOUT_MAX_S,
                    15.0 + self.FIRST_TOKEN_TIMEOUT_PER_IN_TOKEN_S * float(est_in))
            )

            _p(f"Watchdog: tokens_in≈{est_in}, timeout≈{int(TIMEOUT)}s.")

            # 3) Vigilancia con dos fases: ampliación y, si persiste, cancelación
            while not self.stop_event.is_set():
                time.sleep(1.0)

                # si ya llegó algún token, el watchdog no hace nada
                if state["tokens"] > 0:
                    continue

                elapsed = time.time() - state["last_ts"]
                if elapsed <= TIMEOUT:
                    continue

                # Strike 1: con contexto alto o modelo "frío", ampliamos margen una vez
                if state["watchdog_strikes"] == 0 and (est_in >= 350 or not getattr(self.llm, "_warmed", False)):
                    state["watchdog_strikes"] = 1
                    # ampliación: 60 s extra + 60% del TIMEOUT actual, acotado por 2×MAX
                    extra = max(45.0, TIMEOUT * 0.60 + 60.0)
                    TIMEOUT = min(self.FIRST_TOKEN_TIMEOUT_MAX_S * 2.0, TIMEOUT + extra)
                    state["last_ts"] = time.time()
                    _p(f"[Watchdog] Sin tokens tras {int(elapsed)}s → ampliando margen a ≈{int(TIMEOUT)}s "
                       f"(contexto≈{int(est_in)}, warmed={getattr(self.llm, '_warmed', False)}).")
                    continue

                # Strike 2: ahora sí, cancelamos y lanzamos fallback
                state["timed_out"] = True
                _p(f"Timeout {int(elapsed)}s sin tokens → cancelando stream (fallback).")
                try:
                    if hasattr(self.llm, "cancel") and callable(self.llm.cancel):
                        self.llm.cancel()
                except Exception:
                    pass
                try:
                    self.stop_event.set()
                except Exception:
                    pass
                _start_fallback("timeout")
                break

        threading.Thread(target=_watchdog, daemon=True).start()

        # Worker del stream
        def _worker(
                _messages=messages,
                _max_tokens=max_tokens,
                _temperature=temperature
        ):

            # --- FIX max_tokens shadowing (no tocar _max_tokens) ---
            eff_max = _max_tokens
            try:
                mt_env = int(os.getenv("PACQUI_MAX_TOKENS", "0") or "0")
                if mt_env > 0:
                    eff_max = mt_env
            except Exception:
                pass

            try:
                _p("Abriendo stream del modelo…")
                state["last_ts"] = time.time()
                _p(f"[CHAT] out_max={int(eff_max)}  ctx={getattr(self.llm, 'ctx', None)}")
                try:
                    print(f"[CHAT] out_max={int(eff_max)}  ctx={getattr(self.llm, 'ctx', None)}")
                except Exception:
                    pass



                try:
                    # Ajusta el máximo efectivo de salida: env > parámetro > 768
                    eff_max = int(mt_env) if (isinstance(mt_env, int) and mt_env > 0) else int(max_tokens)
                    stream = self.llm.chat(messages=messages, max_tokens=eff_max, temperature=float(temperature),
                                           stream=True)

                    _p("Stream abierto ✅. Esperando primer token…")
                except Exception as e:
                    _p(f"No se pudo abrir el stream: {e}")
                    _start_fallback("apertura fallida")
                    return

                for chunk in stream:
                    if getattr(self, "stop_event", None) and self.stop_event.is_set():
                        if state["tokens"] == 0:
                            _start_fallback("cancel sin tokens")
                            return
                        _p("Cancelado por usuario (tras recibir tokens).");
                        break

                    try:
                        ch0 = (chunk or {}).get("choices", [{}])[0]
                        delta = ch0.get("delta") or {}
                        tok = delta.get("content") or ch0.get("text") or ""
                    except Exception:
                        tok = ""
                    if not tok:
                        continue

                    if state["tokens"] == 0:
                        dt_first = int((time.time() - state["last_ts"]) * 1000)
                        _p(f"Primer token recibido ✅ ({dt_first} ms)")
                        setattr(self.llm, "_warmed", True)

                    state["tokens"] += 1
                    state["last_ts"] = time.time()
                    q.put(tok)

                if state["tokens"] == 0 and not state["timed_out"]:
                    _start_fallback("stream sin tokens")
                    return
                q.put(None)  # fin normal

            except Exception as e:
                _p(f"[stream] {type(e).__name__}: {e}")
                _fallback(f"error: {e}")

        threading.Thread(target=_worker, daemon=True).start()


if __name__ == "__main__":
    AppRoot().mainloop()