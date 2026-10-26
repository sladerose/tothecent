"""Household settings from Config\\household.json.

Holds what differs from one household to the next: who owns each account, the account numbers of the credit card
and the loans, the property each loan belongs to, the holder names to look for in TymeBank statements, and the household sentences shown on the dashboard.
A missing file or key stops the run with a message that names it. There are no silent defaults.
"""

from __future__ import annotations

import json
import os

FILE = "household.json"
SECOND = "{second}"  # placeholder in code for the second TymeBank holder, as in "TymeBank Current ({second})"


def load(config_dir: str) -> dict:
    path = os.path.join(config_dir, FILE)
    if not os.path.exists(path):
        raise SystemExit(f"{path} is missing. It names the owner of each account. Copy Config\\household.example.json and fill it in.")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("owners", "loan_properties", "holder_keywords", "dashboard_notes"):
        if not isinstance(cfg.get(key), dict):
            raise SystemExit(f"{path}: '{key}' is missing or is not an object.")
    if not isinstance(cfg.get("second_tyme_holder", ""), str):
        raise SystemExit(f"{path}: 'second_tyme_holder' is not a name.")
    cfg.setdefault("second_tyme_holder", "")  # optional: only a household with a second TymeBank holder names one
    return cfg


def in_use(label: str, cfg: dict) -> bool:
    """A bank is in use when it has an owner. A label that needs the second holder is in use only when one is named."""
    return (SECOND not in label or bool(cfg["second_tyme_holder"])) and resolve(label, cfg) in cfg["owners"]


def resolve(text: str, cfg: dict) -> str:
    """Replace the second holder placeholder in a bank label or folder name."""
    return text.replace(SECOND, cfg["second_tyme_holder"])


def apply(config_dir: str, banks: dict) -> dict:
    """Name the second holder in the bank labels, fill the owner and the known account numbers of each bank definition, and the TymeBank holder keywords."""
    from . import tyme
    cfg = load(config_dir)
    resolved = {resolve(b, cfg): d for b, d in banks.items() if in_use(b, cfg)}
    for d in resolved.values():
        d["archive"] = resolve(d["archive"], cfg)
        if "route" in d:
            d["route"] = {k: resolve(v, cfg) for k, v in d["route"].items()}
    banks.clear()
    banks.update(resolved)
    if not banks:
        raise SystemExit(f"{os.path.join(config_dir, FILE)}: 'owners' names no bank in use.")
    unknown = [b for b in cfg["owners"] if b not in banks]
    if unknown:
        raise SystemExit(f"{os.path.join(config_dir, FILE)}: 'owners' names a bank the tool does not know: {', '.join(unknown)}.")
    for bank, d in banks.items():
        d["owner"] = cfg["owners"][bank]
        if bank in cfg.get("accounts", {}):
            d["accounts"] = tuple(cfg["accounts"][bank])
    tyme.OWNER_KEYWORDS.clear()
    tyme.OWNER_KEYWORDS.update(cfg["holder_keywords"])
    return cfg
