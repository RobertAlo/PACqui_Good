
import os
import tkinter as tk
from tkinter import ttk, messagebox
#PACqui_1.3.0
class SourcesPanel(tk.Toplevel):
    """
    Panel lateral (desacoplado) para listar "Fuentes" sugeridas por el índice (keywords/observaciones)
    y, opcionalmente, por el RAG. Está pensado para ser creado desde LLMChatDialog.

    Uso:
        panel = SourcesPanel(master,
                             on_open_file=lambda path: ...,
                             on_open_folder=lambda path: ...,
                             on_show_observaciones=lambda path: ...)
        panel.update_sources([
            {"path": "C:/doc/a.pdf", "name": "a.pdf", "score": 4, "keywords": "mic; pago; g7", "note": "Resumen..."}
        ])

    - Doble clic abre el fichero.
    - Menú contextual: Abrir, Abrir carpeta, Ver Observaciones, Copiar ruta.
    - Zona de vista previa muestra Palabras clave y Observaciones del ítem seleccionado.
    """
    def __init__(self, master, on_open_file=None, on_open_folder=None, on_show_observaciones=None):
        super().__init__(master)
        self.title("Fuentes — PACqui")
        self.resizable(True, True)
        self.minsize(520, 360)
        self.transient(master)
        self.on_open_file = on_open_file or (lambda p: self._open_default(p))
        self.on_open_folder = on_open_folder or (lambda p: self._open_default(os.path.dirname(p)))
        self.on_show_observaciones = on_show_observaciones or (lambda p: messagebox.showinfo("Observaciones", "(sin observaciones)", parent=self))

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # posiciona al lado derecho de la ventana principal (best effort)
        try:
            self.update_idletasks()
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
            sx = mx + mw + 8
            sy = my
            self.geometry(f"+{sx}+{sy}")
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        root = ttk.Frame(self, padding=(8, 8))
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text="Fuentes sugeridas (índice + RAG)", font=("Segoe UI", 10, "bold"))
        title.pack(anchor="w", pady=(0, 6))

        paned = ttk.Panedwindow(root, orient="vertical")
        paned.pack(fill="both", expand=True)

        # Tabla superior con los resultados
        frm_top = ttk.Frame(paned)
        self.tree = ttk.Treeview(frm_top, columns=("score", "name", "ruta"), show="headings", selectmode="browse", height=8)
        self.tree.heading("score", text="Coinc.")
        self.tree.heading("name", text="Nombre")
        self.tree.heading("ruta", text="Ruta")
        self.tree.column("score", width=60, anchor="center")
        self.tree.column("name", width=150, anchor="w")
        self.tree.column("ruta", width=380, anchor="w")
        ysb = ttk.Scrollbar(frm_top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        paned.add(frm_top, weight=2)

        # Vista previa inferior
        frm_bottom = ttk.LabelFrame(paned, text="Vista previa")
        paned.add(frm_bottom, weight=1)
        self.txt_preview = tk.Text(frm_bottom, wrap="word", height=6)
        self.txt_preview.pack(fill="both", expand=True, padx=6, pady=6)

        # Botonera
        frm_btn = ttk.Frame(root)
        frm_btn.pack(fill="x", pady=(6,0))
        ttk.Button(frm_btn, text="Abrir", command=self._cmd_abrir).pack(side="left")
        ttk.Button(frm_btn, text="Abrir carpeta", command=self._cmd_carpeta).pack(side="left", padx=(6,0))
        ttk.Button(frm_btn, text="Ver Observaciones", command=self._cmd_obs).pack(side="left", padx=(6,0))
        ttk.Button(frm_btn, text="Copiar ruta", command=self._cmd_copiar).pack(side="left", padx=(6,0))
        ttk.Button(frm_btn, text="Cerrar", command=self._on_close).pack(side="right")

        # Menú contextual
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Abrir", command=self._cmd_abrir)
        self.menu.add_command(label="Abrir carpeta", command=self._cmd_carpeta)
        self.menu.add_separator()
        self.menu.add_command(label="Ver Observaciones", command=self._cmd_obs)
        self.menu.add_command(label="Copiar ruta", command=self._cmd_copiar)

        self.tree.bind("<Double-Button-1>", lambda e: self._cmd_abrir())
        self.tree.bind("<Button-3>", self._on_context)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self._sources = []  # lista de dicts

    # ---------- API ----------
    def update_sources(self, sources):
        """sources: list[dict] con claves path, name, score, keywords, note"""
        self._sources = list(sources or [])
        self.tree.delete(*self.tree.get_children())
        for item in self._sources:
            self.tree.insert("", "end", values=(item.get("score", 0), item.get("name", ""), item.get("path", "")))
        if self._sources:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
        else:
            self.txt_preview.delete("1.0", "end")
            self.txt_preview.insert("1.0", "No hay fuentes para la consulta actual.")

    # ---------- Helpers ----------
    def _selected_item(self):
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        ruta = vals[2] if len(vals) >= 3 else None
        for it in self._sources:
            if it.get("path") == ruta:
                return it
        return None

    def _on_context(self, event):
        try:
            iid = self.tree.identify_row(event.y)
            if iid:
                self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _on_select(self, event=None):
        item = self._selected_item()
        self.txt_preview.delete("1.0", "end")
        if not item:
            return
        name = item.get("name","")
        ruta = item.get("path","")
        kws = item.get("keywords","")
        note = item.get("note","") or "(sin observaciones)"
        if len(note) > 2000:
            note = note[:2000] + "…"
        text = f"{name}\n{ruta}\n\nPalabras clave:\n{(kws or '-')}\n\nObservaciones:\n{note}"
        self.txt_preview.insert("1.0", text)

    def _cmd_abrir(self):
        item = self._selected_item()
        if not item:
            return
        self.on_open_file(item.get("path"))

    def _cmd_carpeta(self):
        item = self._selected_item()
        if not item:
            return
        self.on_open_folder(item.get("path"))

    def _cmd_obs(self):
        item = self._selected_item()
        if not item:
            return
        self.on_show_observaciones(item.get("path"))

    def _cmd_copiar(self):
        item = self._selected_item()
        if not item:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(item.get("path",""))
            self.update()
        except Exception:
            pass

    def _on_close(self):
        try:
            self.withdraw()
        except Exception:
            pass

    # fallback muy básico por si no pasan los callbacks
    def _open_default(self, p):
        try:
            if os.name == "nt":
                os.startfile(p)  # type: ignore
            else:
                import subprocess
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("Abrir", f"No se pudo abrir:\n{p}\n\n{e}", parent=self)
