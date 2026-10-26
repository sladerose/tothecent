"""Prepare a finance folder: create the folder skeleton, the virtual environment and the packages.

usage: python setup.py [--root PATH] [--venv PATH]

Uses only the standard library, so it runs with any Python 3.10 or later on Windows and Mac.
The virtual environment lives outside the synced folder: FINANCE_TOOLS_VENV, else ~/.finance-tools/venv.
Copies the config, knowledge and CLAUDE.local.md templates from Tools/templates where missing.
Safe to run again: existing folders, files and an existing virtual environment are kept.
"""

import argparse
import os
import shutil
import subprocess
import sys
import venv as venvlib

SKELETON = ("Config", "Data/raw", "Data/master", "Dashboard", "Inbox", "_Archive")
INBOX_BANKS = ("FNB", "FNB Money Maximiser", "Savings and pockets", "Nedbank", "Discovery",
               "TymeBank", "FNB Credit Card", "FNB Home Loan")
WIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def default_venv() -> str:
    return os.environ.get("FINANCE_TOOLS_VENV") or os.path.join(os.path.expanduser("~"), ".finance-tools", "venv")


def venv_python(venv: str) -> str:
    return os.path.join(venv, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv, "bin", "python")


def copy_templates(src: str, root: str) -> list[str]:
    """Copy each template into the same relative place under root. An existing file is never overwritten."""
    added = []
    for folder, _, names in os.walk(src):
        rel_dir = os.path.relpath(folder, src)
        for name in sorted(names):
            dest = os.path.normpath(os.path.join(root, rel_dir, name))
            if os.path.exists(dest):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(folder, name), dest)
            added.append(os.path.relpath(dest, root))
    return added


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(here))
    ap.add_argument("--venv", default=default_venv())
    args = ap.parse_args()
    args.root = os.path.abspath(args.root)

    if sys.version_info < (3, 10):
        print(f"Python 3.10 or later is required. This is {sys.version.split()[0]}.", file=sys.stderr)
        return 2

    for sub in SKELETON:
        os.makedirs(os.path.join(args.root, *sub.split("/")), exist_ok=True)
    for bank in INBOX_BANKS:
        os.makedirs(os.path.join(args.root, "Inbox", bank), exist_ok=True)
    print(f"Folders ready under {args.root}")

    added = copy_templates(os.path.join(here, "templates"), args.root)
    print(f"Templates added: {len(added)}" + "".join(f"\n  {rel}" for rel in added))

    py = venv_python(args.venv)
    if not os.path.exists(py):
        venvlib.EnvBuilder(with_pip=True).create(args.venv)
    req = os.path.join(args.root, "requirements.txt")
    if not os.path.exists(req):
        print(f"requirements.txt not found at {req}", file=sys.stderr)
        return 2
    subprocess.run([py, "-m", "pip", "install", "--quiet", "-r", req], check=True)
    print(f"Packages installed in {args.venv}")

    if shutil.which("tesseract") or os.path.exists(WIN_TESSERACT):
        print("Tesseract OCR found.")
    else:
        print("WARNING: Tesseract OCR not found. Scanned statements cannot be read until it is installed "
              "(Windows: the UB Mannheim installer; Mac: brew install tesseract).")
    print(f"Next: put statement PDFs in {os.path.join(args.root, 'Inbox')} and run Tools{os.sep}process_inbox.py with {py}")
    return 0


sys.exit(main())
