
# PACqui Front Redesign — v1b
# - Chat-first público con UI mejorada (chips scrollables, Treeview de fuentes, abrir archivo/carpeta)
# - Área Admin protegida por contraseña; las herramientas clásicas se abren en una ventana Toplevel
#   (soluciona el error 'Frame' object has no attribute 'protocol' del embed).
# - Integración opcional con PACqui_RAG_bomba_SAFE (OrganizadorFrame, LLMChatDialog) y meta_store.MetaStore.
import os, sys, json, time, hashlib, binascii, secrets, sqlite3, threading, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

APP_NAME = "PACqui"
CONFIG_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "PACqui"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "settings.json"

DEFAULT_DB = "index_cache.sqlite"

# -------- Optional imports from existing project --------
OrganizadorFrame = None
LLMChatDialog = None
MetaStore = None
try:
    from PACqui_RAG_bomba_SAFE import OrganizadorFrame as _Org
    OrganizadorFrame = _Org
except Exception:
    pass

try:
    from PACqui_RAG_bomba_SAFE import LLMChatDialog as _LLM
    LLMChatDialog = _LLM
except Exception:
    pass

try:
    from meta_store import MetaStore as _MS
    MetaStore = _MS
except Exception:
    MetaStore = None

# -------- Password & Config Management --------
def _new_salt(n=16) -> str:
    return binascii.hexlify(secrets.token_bytes(n)).decode("ascii")

def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return binascii.hexlify(dk).decode("ascii")

def _load_cfg() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_cfg(data: dict):
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def ensure_admin_password(root) -> bool:
    """Wizard for first run: set admin password if not configured. Returns True if configured."""
    cfg = _load_cfg()
    if cfg.get("admin_hash") and cfg.get("admin_salt"):
        return True
    messagebox.showinfo(APP_NAME, "Vamos a crear la contraseña de administración.")
    while True:
        pwd1 = simpledialog.askstring(APP_NAME, "Introduce una contraseña admin:", show="*", parent=root)
        if pwd1 is None:
            messagebox.showwarning(APP_NAME, "Configuración cancelada. Puedes crearla más tarde desde el icono de candado.")
            return False
        if len(pwd1) < 6:
            messagebox.showwarning(APP_NAME, "La contraseña debe tener al menos 6 caracteres.")
            continue
        pwd2 = simpledialog.askstring(APP_NAME, "Repite la contraseña:", show="*", parent=root)
        if pwd2 != pwd1:
            messagebox.showwarning(APP_NAME, "No coinciden. Inténtalo de nuevo.")
            continue
        salt = _new_salt()
        h = _hash_password(pwd1, salt)
        cfg["admin_salt"] = salt
        cfg["admin_hash"] = h
        cfg["admin_last_login"] = None
        _save_cfg(cfg)
        messagebox.showinfo(APP_NAME, "Contraseña admin creada correctamente.")
        return True

def admin_login(parent) -> bool:
    """Login dialog. Returns True if authenticated; supports cooldown on failures."""
    cfg = _load_cfg()
    if not cfg.get("admin_hash"):
        messagebox.showwarning(APP_NAME, "Aún no hay contraseña de admin. Vamos a crearla.")
        return ensure_admin_password(parent)
    attempts = 0
    while True:
        pwd = simpledialog.askstring(APP_NAME, "Contraseña de administrador:", show="*", parent=parent)
        if pwd is None:
            return False
        salt = cfg.get("admin_salt", "")
        if not salt:
            messagebox.showwarning(APP_NAME, "Configuración incompleta. Crea de nuevo la contraseña.")
            return ensure_admin_password(parent)
        if _hash_password(pwd, salt) == cfg.get("admin_hash"):
            cfg["admin_last_login"] = int(time.time())
            _save_cfg(cfg)
            return True
        attempts += 1
        wait = min(60, 5 * attempts)
        messagebox.showerror(APP_NAME, f"Contraseña incorrecta. Espera {wait} s para reintentar.")
        parent.after(wait * 1000, lambda: None)
        parent.update()
        time.sleep(wait)

# -------- Data access (keywords & notes) --------
class DataAccess:
    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path or DEFAULT_DB)
        self._lock = threading.RLock()
        self._ms = MetaStore(db_path=str(self.db_path)) if MetaStore else None

    def _connect(self):
        return sqlite3.connect(str(self.db_path))

    def stats(self):
        """Returns (tables, keywords_count, notes_count)"""
        try:
            with self._lock, self._connect() as con:
                cur = con.cursor()
                cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
                tables = cur.fetchone()[0]
                try:
                    cur.execute("SELECT COUNT(*) FROM doc_keywords")
                    kw = cur.fetchone()[0]
                except Exception:
                    kw = 0
                try:
                    cur.execute("SELECT COUNT(*) FROM doc_notes")
                    nt = cur.fetchone()[0]
                except Exception:
                    nt = 0
                return tables, kw, nt
        except Exception:
            return 0, 0, 0

    def keywords_top(self, limit=50):
        """Return most frequent keywords with counts."""
        try:
            with self._lock, self._connect() as con:
                cur = con.cursor()
                cur.execute("""
                    SELECT keyword, COUNT(*) as cnt
                    FROM doc_keywords
                    GROUP BY lower(keyword)
                    ORDER BY cnt DESC, keyword COLLATE NOCASE ASC
                    LIMIT ?
                """, (limit,))
                return cur.fetchall()
        except Exception:
            return []

    def search_sources_by_text(self, text: str, limit=50):
        """Return list of {path, keyword, note} hits by keyword LIKE text."""
        if not text or len(text.strip()) < 2:
            return []
        pattern = f"%{text.strip()}%"
        rows = []
        with self._lock, self._connect() as con:
            cur = con.cursor()
            try:
                cur.execute("""
                    SELECT k.fullpath, k.keyword,
                           (SELECT n.note FROM doc_notes n WHERE lower(n.fullpath)=lower(k.fullpath) LIMIT 1) as note
                    FROM doc_keywords k
                    WHERE k.keyword LIKE ?
                    GROUP BY lower(k.fullpath), lower(k.keyword)
                    ORDER BY k.keyword COLLATE NOCASE ASC
                    LIMIT ?
                """, (pattern, limit))
                for fp, kw, note in cur.fetchall():
                    rows.append({"path": fp, "keyword": kw, "note": note or ""})
            except Exception:
                pass
        return rows

# -------- Helpers --------
class ScrollableFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.canvas = canvas

# -------- UI Components --------
class ChatFrame(ttk.Frame):
    """Public chat-first view: left chips, center chat, right sources/observations"""
    def __init__(self, master, data: DataAccess):
        super().__init__(master)
        self.data = data
        self._hits = []
        self._build_styles()
        self._build_ui()
        self._load_chips()

    def _build_styles(self):
        style = ttk.Style()
        # Use 'clam' for a more modern look if available
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Chip.TButton", padding=(6,2))

    def _build_ui(self):
        # Paned layout: left (chips) | center (chat) | right (sources)
        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # Left pane: keywords
        left = ttk.Frame(paned, padding=(10,10))
        paned.add(left, weight=1)
        ttk.Label(left, text="Palabras clave", style="Header.TLabel").pack(anchor="w", pady=(0,6))
        self.chips = ScrollableFrame(left)
        self.chips.pack(fill="both", expand=True, pady=(0,8))
        row2 = ttk.Frame(left)
        row2.pack(fill="x")
        ttk.Button(row2, text="Buscar palabra clave…", command=self._ask_keyword).pack(side="left")

        # Center pane: chat
        center = ttk.Frame(paned, padding=(10,10))
        paned.add(center, weight=3)
        ttk.Label(center, text="PACqui — Asistente (modo público)", style="Header.TLabel").pack(anchor="w", pady=(0,6))
        self.txt_chat = tk.Text(center, height=18, wrap="word")
        self.txt_chat.pack(fill="both", expand=True)
        self.txt_chat.insert("end", "Bienvenido/a. Pregúntame algo y te sugeriré rutas relevantes.\n")
        input_row = ttk.Frame(center)
        input_row.pack(fill="x", pady=(8,0))
        self.ent_input = ttk.Entry(input_row)
        self.ent_input.pack(side="left", fill="x", expand=True)
        self.ent_input.bind("<Return>", lambda e: self._on_send())
        ttk.Button(input_row, text="Enviar", style="Big.TButton", command=self._on_send).pack(side="left", padx=(8,0))

        # Right pane: sources + note + actions
        right = ttk.Frame(paned, padding=(10,10))
        paned.add(right, weight=2)
        ttk.Label(right, text="Fuentes sugeridas", style="Header.TLabel").pack(anchor="w", pady=(0,6))

        # Treeview with columns
        cols = ("keyword","name","path")
        self.tv = ttk.Treeview(right, columns=cols, show="headings", height=18)
        self.tv.heading("keyword", text="Keyword")
        self.tv.heading("name", text="Documento")
        self.tv.heading("path", text="Ruta")
        self.tv.column("keyword", width=120, anchor="w")
        self.tv.column("name", width=220, anchor="w")
        self.tv.column("path", width=400, anchor="w")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", self._on_tv_select)
        self.tv.bind("<Double-1>", self._open_path)

        # Note box
        ttk.Label(right, text="Observaciones").pack(anchor="w", pady=(8,2))
        self.txt_note = tk.Text(right, height=6, wrap="word")
        self.txt_note.pack(fill="both", expand=False)

        # Actions
        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(8,0))
        self.btn_open = ttk.Button(actions, text="Abrir archivo", command=self._open_path, state="disabled")
        self.btn_open.pack(side="left")
        self.btn_open_folder = ttk.Button(actions, text="Abrir carpeta", command=self._open_folder, state="disabled")
        self.btn_open_folder.pack(side="left", padx=(8,0))

        # Footer status
        self.status = ttk.Label(self, anchor="w", padding=(10,4))
        self.status.pack(fill="x")

    def set_status(self, text: str):
        self.status.config(text=text)

    def _load_chips(self):
        # Clear chips
        for w in list(self.chips.inner.children.values()):
            w.destroy()
        kws = self.data.keywords_top(limit=60)
        for kw, cnt in kws:
            btn = ttk.Button(self.chips.inner, text=f"{kw} ({cnt})", style="Chip.TButton")
            btn.configure(command=lambda k=kw: self._chip_click(k))
            btn.pack(anchor="w", pady=2, fill="x")

    def _chip_click(self, kw: str):
        self.ent_input.delete(0, "end")
        self.ent_input.insert(0, kw)
        self._on_send()

    def _ask_keyword(self):
        kw = simpledialog.askstring(APP_NAME, "Palabra clave:", parent=self.winfo_toplevel())
        if kw:
            self.ent_input.delete(0, "end")
            self.ent_input.insert(0, kw)
            self._on_send()

    def _on_send(self):
        text = self.ent_input.get().strip()
        if not text:
            return
        self._append_chat("Tú", text)
        self.ent_input.delete(0, "end")
        # Suggest sources
        self._populate_sources(text)

    def _append_chat(self, who: str, content: str):
        import re, uuid, os
        from pathlib import Path
        try:
            from path_utils import open_in_explorer
        except Exception:
            # fallback mínimo para no romper la UI si falla el import
            def open_in_explorer(p):
                from pathlib import Path
                import os, sys, subprocess
                p = Path(p)
                try:
                    if os.name == "nt":
                        if p.is_file():
                            subprocess.run(["explorer", "/select,", str(p)], check=False)
                        else:
                            os.startfile(str(p))  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.run(["open", str(p if p.is_dir() else p.parent)], check=False)
                    else:
                        subprocess.run(["xdg-open", str(p if p.is_dir() else p.parent)], check=False)
                except Exception:
                    pass

        # Punto de inserción antes de volcar el texto
        start = self.txt_chat.index("end-1c")
        self.txt_chat.insert("end", f"\n{who}: {content}\n")
        end = self.txt_chat.index("end-1c")

        # Textual del bloque recién insertado
        segment = self.txt_chat.get(start, end)

        # 1) Linkifica líneas tipo "Ruta: <path>"
        for m in re.finditer(r"Ruta:\s+(.+)", segment):
            raw_path = m.group(1).strip()
            # Normaliza y protege
            try:
                p = str(Path(raw_path))
            except Exception:
                p = raw_path

            s_idx = f"{start}+{m.start(1)}c"
            e_idx = f"{start}+{m.end(1)}c"
            tag = f"pathlink_{uuid.uuid4().hex[:8]}"

            # pinta aspecto de enlace
            self.txt_chat.tag_add(tag, s_idx, e_idx)
            self.txt_chat.tag_config(tag, foreground="#0b5bd3", underline=True)

            # cursor mano
            self.txt_chat.tag_bind(tag, "<Enter>", lambda e: self.txt_chat.config(cursor="hand2"))
            self.txt_chat.tag_bind(tag, "<Leave>", lambda e: self.txt_chat.config(cursor=""))

            # on-click: abrir en explorador/sistema
            def _open(_e=None, _p=p):
                try:
                    open_in_explorer(Path(_p))
                except Exception:
                    # fallback: intenta abrir archivo directamente
                    try:
                        os.startfile(_p)  # Windows
                    except Exception:
                        pass

            self.txt_chat.tag_bind(tag, "<Button-1>", _open)

        self.txt_chat.see("end")

    def _populate_sources(self, text: str):
        self.tv.delete(*self.tv.get_children())
        self.txt_note.delete("1.0", "end")
        hits = self.data.search_sources_by_text(text, limit=100)
        self._hits = hits
        if not hits:
            self._append_chat("PACqui", "No encontré coincidencias por palabras clave en el índice.")
            self.btn_open.config(state="disabled")
            self.btn_open_folder.config(state="disabled")
            return
        self._append_chat("PACqui", f"Sugerencias por palabras clave ({len(hits)}). Doble clic para abrir el archivo.")
        for h in hits:
            name = Path(h["path"]).name
            self.tv.insert("", "end", values=(h["keyword"], name, h["path"]))
        self.btn_open.config(state="disabled")
        self.btn_open_folder.config(state="disabled")

    def _selected_hit(self):
        sel = self.tv.selection()
        if not sel:
            return None
        vals = self.tv.item(sel[0], "values")
        # Map back to hit
        path = vals[2]
        for h in self._hits:
            if h["path"] == path:
                return h
        return None

    def _on_tv_select(self, *_):
        h = self._selected_hit()
        if not h:
            return
        self.txt_note.delete("1.0", "end")
        self.txt_note.insert("1.0", h.get("note") or "")
        self.btn_open.config(state="normal")
        self.btn_open_folder.config(state="normal")

    def _open_path(self, *_):
        h = self._selected_hit()
        if not h:
            return
        path = h["path"]
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo abrir el archivo:\n{e}")

    def _open_folder(self):
        h = self._selected_hit()
        if not h:
            return
        folder = str(Path(h["path"]).parent)
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo abrir la carpeta:\n{e}")

# -------- Admin wrapper --------
class AdminFrame(ttk.Frame):
    """Private admin area. Opens legacy OrganizadorFrame in a Toplevel window when requested."""
    def __init__(self, master):
        super().__init__(master)
        self._legacy_win = None
        self._build_ui()

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        # Índice / Herramientas clásicas
        frm_idx = ttk.Frame(nb, padding=12)
        nb.add(frm_idx, text="Índice y herramientas")

        ttk.Label(frm_idx, text="Panel de administración", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frm_idx, text="Aquí puedes abrir las herramientas clásicas (Carpeta base, import/export, reindex).").pack(anchor="w", pady=(4,10))
        ttk.Button(frm_idx, text="Abrir herramientas clásicas (Carpeta base…)", command=self._open_legacy, style="Big.TButton").pack(anchor="w")

        # Logs / Estado
        frm_logs = ttk.Frame(nb, padding=12)
        nb.add(frm_logs, text="Logs y estado")
        ttk.Label(frm_logs, text="(Próximo) Estado del índice, RAG y registros de proceso.").pack(anchor="w")

        # Zona peligrosa (placeholders)
        frm_danger = ttk.Frame(nb, padding=12)
        nb.add(frm_danger, text="Zona peligrosa")
        ttk.Label(frm_danger, text="(Próximo) Reset de configuración, vaciado de cachés, etc.").pack(anchor="w")

    def _open_legacy(self):
        if OrganizadorFrame is None:
            messagebox.showerror(APP_NAME, "No se encontró la UI clásica (OrganizadorFrame) en PACqui_RAG_bomba_SAFE.py")
            return
        if self._legacy_win and tk.Toplevel.winfo_exists(self._legacy_win):
            self._legacy_win.lift()
            return
        try:
            self._legacy_win = tk.Toplevel(self)
            self._legacy_win.title("PACqui — Herramientas clásicas (Admin)")
            # Crear la UI clásica dentro del Toplevel (tal y como fue diseñada)
            legacy = OrganizadorFrame(self._legacy_win)
            legacy.pack(fill="both", expand=True)
        except Exception as e:
            if self._legacy_win:
                try:
                    self._legacy_win.destroy()
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"No se pudo abrir la UI clásica:\n{e}")

# -------- Root Controller --------
class AppRoot(tk.Tk):
    def __init__(self, db_path: str | None = None):
        super().__init__()
        self.title(f"{APP_NAME} — Chat-first")
        self.geometry("1180x760")
        self.minsize(980, 640)

        # Theme scaling
        try:
            self.call("tk", "scaling", 1.25)
        except Exception:
            pass

        # Session
        self._is_admin = False
        self._ttl_minutes = 20
        self._last_auth = None

        # Data
        self.data = DataAccess(db_path)

        # UI
        self._build_ui()

        # First-run check
        ensure_admin_password(self)

        # Status
        self._refresh_status()

    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self, padding=(10,10,10,6))
        top.pack(fill="x")
        ttk.Label(top, text="PACqui", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Button(top, text="Ayuda", command=self._on_help).pack(side="right", padx=(8,0))
        self.btn_lock = ttk.Button(top, text="🔒 Admin", command=self._on_admin_toggle)
        self.btn_lock.pack(side="right")

        # Stack
        self.stack = ttk.Frame(self)
        self.stack.pack(fill="both", expand=True)

        self.chat = ChatFrame(self.stack, self.data)
        self.chat.pack(fill="both", expand=True)

        self.admin = None  # lazy

        # Footer
        self.footer = ttk.Label(self, anchor="w", padding=(10,6))
        self.footer.pack(fill="x")

    def _on_help(self):
        messagebox.showinfo(APP_NAME, "Modo público: conversa y revisa rutas sugeridas.\n"
                                      "Modo admin: herramientas de índice y mantenimiento (protegido).\n\n"
                                      "Consejo: usa los chips de la izquierda para explorar el índice rápidamente.")

    def _on_admin_toggle(self):
        if not self._is_admin:
            if admin_login(self):
                self._is_admin = True
                self._last_auth = time.time()
                self.btn_lock.configure(text="🔓 Admin (activo)")
                if self.admin is None:
                    self.admin = AdminFrame(self.stack)
                self.chat.pack_forget()
                self.admin.pack(fill="both", expand=True)
                self._refresh_status()
        else:
            self._is_admin = False
            self.btn_lock.configure(text="🔒 Admin")
            if self.admin:
                self.admin.pack_forget()
            self.chat.pack(fill="both", expand=True)
            self._refresh_status()

    def _refresh_status(self):
        # Build status
        tables, kw, nt = self.data.stats()
        if Path(DEFAULT_DB).exists():
            idx_text = f"Índice: {Path(DEFAULT_DB).name} (tablas: {tables}, keywords: {kw}, notas: {nt})"
        else:
            idx_text = "Índice: (no encontrado)"
        model_text = "Modelo: (pendiente)"
        admin_text = "Admin: activo" if self._is_admin else "Admin: bloqueado"

        self.chat.set_status(f"{idx_text} | {model_text}")
        self.footer.config(text=f"{idx_text} | {admin_text}")

        # TTL auto-logout
        if self._is_admin and self._last_auth is not None:
            elapsed = (time.time() - self._last_auth) / 60.0
            if elapsed > self._ttl_minutes:
                messagebox.showinfo(APP_NAME, "Sesión de administrador caducada.")
                self._on_admin_toggle()

def main():
    app = AppRoot(db_path=DEFAULT_DB)
    app.mainloop()

if __name__ == "__main__":
    main()
