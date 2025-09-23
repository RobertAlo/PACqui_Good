
from __future__ import annotations
import argparse
from massive_indexer import export_massive_index

def main():
    ap = argparse.ArgumentParser(description="Exportación masiva a Excel/CSV (hipermasivo)")
    ap.add_argument("base", help="Carpeta base a indexar")
    ap.add_argument("--out", help="Ruta de salida (.xlsx o .csv). Si no se indica, se crea en el escritorio.", default=None)
    ap.add_argument("--csv", help="Fuerza CSV aunque exista xlsxwriter", action="store_true")
    args = ap.parse_args()

    path = export_massive_index(args.base, out_path=args.out, prefer_xlsx=not args.csv)
    print(f"\\nOK → {path}")

if __name__ == "__main__":
    main()
