
# ui_observaciones.py — Toplevel para editar Observaciones (doc_notes) de un fichero
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable
from pathlib import Path
#PAcqui_
try:
    from meta_store import MetaStore
except Exception:
    # Permite import relativo si se usa como módulo suelto
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))
    from meta_store import MetaStore

class ObservacionesDialog(tk.Toplevel):
    def __init__(self, master, db_path: Optional[str], fullpath: str, on_saved: Optional[Callable[[str], None]] = None):
        super().__init__(master)
        self.title("Observaciones del documento")
        self.transient(master)
        self.resizable(True, True)
        self.grab_set()
        self.geometry("720x420")
        self.on_saved = on_saved
        self.fullpath = fullpath
        self.store = MetaStore(db_path)

        # ---- UI ----
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Fichero:", font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.lbl_path = ttk.Label(frm, text=fullpath, wraplength=680)
        self.lbl_path.grid(row=0, column=1, sticky="w", padx=(6,0))

        ttk.Label(frm, text="Observaciones (se volcarán en la Excel):").grid(row=1, column=0, columnspan=2, pady=(10,4), sticky="w")

        self.txt = tk.Text(frm, height=12, wrap="word")
        self.txt.grid(row=2, column=0, columnspan=2, sticky="nsew")
        vsb = ttk.Scrollbar(frm, orient="vertical", command=self.txt.yview)
        vsb.grid(row=2, column=2, sticky="ns")
        self.txt.configure(yscrollcommand=vsb.set)

        # Botonera
        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12,0))
        self.btn_save = ttk.Button(btns, text="Guardar", command=self._save)
        self.btn_save.pack(side="right", padx=(6,0))
        ttk.Button(btns, text="Cancelar", command=self._close).pack(side="right")

        # Resize weights
        frm.rowconfigure(2, weight=1)
        frm.columnconfigure(1, weight=1)

        # Cargar nota inicial
        try:
            note = self.store.get_note(fullpath)
        except Exception:
            note = ""


        self.txt.insert("1.0", note or "")
        self.protocol("WM_DELETE_WINDOW", self._close)


    def _save(self):
        note = self.txt.get("1.0", "end-1c")
        try:
            self.store.set_note(self.fullpath, note)
            if self.on_saved:
                self.on_saved(note)
            messagebox.showinfo("Observaciones", "Observaciones guardadas correctamente.", parent=self)
            self._close()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar la nota:\n{e}", parent=self)

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

# Helper rápido
def edit_observaciones(master, fullpath: str, db_path: Optional[str] = None, on_saved: Optional[Callable[[str], None]] = None):
    return ObservacionesDialog(master, db_path, fullpath, on_saved)

if __name__ == "__main__":
    # Demo rápida si se ejecuta este archivo
    root = tk.Tk()
    root.withdraw()
    demo_file = str(Path.home() / "demo.txt")
    edit_observaciones(root, demo_file)
    root.mainloop()
