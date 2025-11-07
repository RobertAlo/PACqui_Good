from pathlib import Path
import threading, time, queue, os, sys, traceback

import tkinter as tk
from tkinter import ttk, messagebox

try:
    from massive_indexer import export_massive_index
    from meta_store import MetaStore
except Exception:
    sys.path.append(str(Path(__file__).resolve().parent))
    from massive_indexer import export_massive_index  # type: ignore
    from meta_store import MetaStore  # type: ignore

#PACqui_1.3.0
class ExportProgress(tk.Toplevel):
    """
    Ventana de progreso en 2 fases:
      - Fase 1: Conteo de ficheros (barra indeterminada + texto "Contando... N")
      - Fase 2: Exportación (barra determinada con máximo=total y valor incremental)
    """
    def __init__(self, master, base_path: str, db_path: str | None = None, prefer_xlsx: bool = True):
        super().__init__(master)
        self.title("Generando Excel...")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.geometry("600x120")

        self.base_path = str(Path(base_path))
        self.db_path = db_path
        self.prefer_xlsx = bool(prefer_xlsx)

        pad = 10
        frm = ttk.Frame(self, padding=pad)
        frm.pack(fill="both", expand=True)

        self.lbl_title = ttk.Label(frm, text="Generando Excel...", font=("", 10, "bold"))
        self.lbl_title.pack(anchor="w")

        self.pbar = ttk.Progressbar(frm, mode="indeterminate", length=540, maximum=100, value=0)
        self.pbar.pack(fill="x", pady=(6, 4))

        info = ttk.Frame(frm)
        info.pack(fill="x")
        self.lbl_left = ttk.Label(info, text="Contando ficheros...", anchor="w")
        self.lbl_left.pack(side="left")
        self.lbl_right = ttk.Label(info, text="", anchor="e")
        self.lbl_right.pack(side="right")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(6, 0))
        self.btn_cancel = ttk.Button(btns, text="Cancelar", command=self._cancel)
        self.btn_cancel.pack(side="right")

        self._cancel_evt = threading.Event()
        self._done_evt = threading.Event()
        self._q = queue.Queue()
        self._total = 0
        self._value = 0
        self._result_path: str | None = None
        self._error: str | None = None

        self.pbar.start(18)
        t = threading.Thread(target=self._worker, name="ExportMassiveWorker", daemon=True)
        t.start()

        self.after(50, self._on_pump)

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _worker(self):
        try:
            base = Path(self.base_path)
            if not base.exists():
                raise FileNotFoundError(f"No existe la carpeta base: {base}")

            total = 0
            last_ui = time.time()
            for root, dirs, files in os.walk(base):
                total += len(files)
                if time.time() - last_ui > 0.1:
                    self._q.put(("count", total))
                    last_ui = time.time()
                if self._cancel_evt.is_set():
                    self._q.put(("cancelled", None))
                    return
            self._q.put(("count", total))
            self._q.put(("switch", total))

            store = MetaStore(self.db_path)

            def meta_provider(fullpath: str):
                kws = "; ".join(store.get_keywords(fullpath)) or ""
                note = store.get_note(fullpath) or ""
                return (kws, note)

            def progress_cb(kind: str, payload: str):
                if kind == "progress":
                    try:
                        n = int(payload)
                    except Exception:
                        n = None
                    if n is not None:
                        self._q.put(("value", n))
                elif kind == "status":
                    self._q.put(("status", payload))

            out_path = export_massive_index(
                base_path=str(base),
                out_path=None,
                prefer_xlsx=self.prefer_xlsx,
                meta_provider=meta_provider,
                progress_cb=progress_cb,
                tick_every=50,
            )
            self._q.put(("done", out_path))

        except Exception as e:
            tb = traceback.format_exc(limit=3)
            self._q.put(("error", f"{e}\n\n{tb}"))

    def _on_pump(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "count":
                    self._total = int(payload or 0)
                    self.lbl_left.config(text=f"Contando ficheros... {self._total:,}")
                elif kind == "switch":
                    self._switch_to_determinate(int(payload or 0))
                elif kind == "value":
                    self._value = int(payload or 0)
                    self.pbar["value"] = min(self._value, self._total or self.pbar["maximum"])
                    self.lbl_right.config(text=f"{self._value:,} / {self._total:,}")
                elif kind == "status":
                    self.lbl_right.config(text=str(payload or ""))
                elif kind == "done":
                    self._result_path = str(payload or "")
                    self._done_evt.set()
                    self.destroy()
                    return
                elif kind == "error":
                    self._error = str(payload or "Error desconocido")
                    self._done_evt.set()
                    self.destroy()
                    return
                elif kind == "cancelled":
                    self._error = "Cancelado por el usuario."
                    self._done_evt.set()
                    self.destroy()
                    return
        except queue.Empty:
            pass
        if not self._done_evt.is_set():
            self.after(50, self._on_pump)

    def _switch_to_determinate(self, total: int):
        self.pbar.stop()
        if total <= 0:
            self.lbl_left.config(text="Exportando...")
            return
        self.lbl_left.config(text=f"Exportando {total:,} ficheros...")
        self._total = total
        self.pbar.config(mode="determinate", maximum=total, value=0)

    def _cancel(self):
        self._cancel_evt.set()
        self.btn_cancel.state(["disabled"])
        self.lbl_left.config(text="Cancelando...")

    def wait(self) -> tuple[bool, str]:
        self.wait_window(self)
        if self._error:
            return (False, self._error)
        return (True, self._result_path or "")


def run_export_ui(master, base_path: str, db_path: str | None = None, prefer_xlsx: bool = True) -> tuple[bool, str]:
    dlg = ExportProgress(master, base_path, db_path=db_path, prefer_xlsx=prefer_xlsx)
    return dlg.wait()
