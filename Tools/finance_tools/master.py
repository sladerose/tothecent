"""Combine per-bank transactions into the master table: clean text, key, month, categories, transfers."""

from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from . import household

CARD_REF = re.compile(r"\b\d{6}\*\d{4}\b(?:\s+\d{1,2}\s+[A-Z][a-z]{2})?")
LONG_DIGITS = re.compile(r"\b\d{5,}\b")


def clean_description(desc: str, source: str) -> str:
    d = desc.strip().lstrip("'‘’")
    if source == "ocr":
        d = re.sub(r"(?<=[A-Za-z])[\]|]$", "", d)
    return re.sub(r"\s+", " ", d).strip()


def merchant_key(desc: str) -> str:
    """Stable, lower case form of a description with card references and long numbers removed."""
    d = CARD_REF.sub(" ", desc)
    d = LONG_DIGITS.sub(" ", d)
    d = re.sub(r"\d+(?:[.,]\d+)?", " ", d)
    return re.sub(r"\s+", " ", d).strip().lower()


def tx_id(account: str, statement_no: str, row_no: int) -> str:
    return hashlib.sha1(f"{account}|{statement_no}|{row_no}".encode()).hexdigest()[:12]


def load_rules(path: str) -> list[dict]:
    """Rules file columns: priority, pattern (regex, case insensitive), category, scope, note."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: int(r["priority"]))
    for r in rows:
        r["_re"] = re.compile(r["pattern"], re.I)
    return rows


def categorise(desc: str, amount: Decimal, bank: str, rules: list[dict]) -> tuple[str, str, str, str]:
    """Return (category, scope, property, matching pattern) for the first rule that fits."""
    for r in rules:
        rb = (r.get("bank") or "").lower()
        if rb and rb != bank.lower() and not bank.lower().startswith(rb + " ("):  # a rule for a bank also covers "<bank> (owner)"
            continue
        if r["_re"].search(desc):
            need = r.get("direction", "")
            if need == "in" and amount <= 0 or need == "out" and amount >= 0:
                continue
            return r["category"], r.get("scope", ""), r.get("property", ""), r["pattern"]
    return "", "", "", ""


POCKET_BANK = "FNB Savings Pockets"


def load_pockets(path: str) -> dict[str, str]:
    """Pocket map: last four digits of the account number to the label used inside the FNB app."""
    import os
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["identifier_last4"].strip(): r["app_label"].strip() for r in csv.DictReader(f)}


def load_pocket_props(path: str) -> dict[str, str]:
    """Property each savings pocket belongs to (column `property` of the pocket map), by last four digits."""
    import os
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["identifier_last4"].strip(): (r.get("property") or "").strip() for r in csv.DictReader(f) if (r.get("property") or "").strip()}


LOAN_BANKS = {"FNB Home Loan", "Nedbank Home Loan"}  # liability accounts: their rows never count as cash, income or spending
def load_pocket_held(path: str) -> dict[str, str]:
    """Pockets that hold money for someone else (column `held_for` of the pocket map), by last four digits."""
    import os
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["identifier_last4"].strip(): (r.get("held_for") or "").strip() for r in csv.DictReader(f) if (r.get("held_for") or "").strip()}


TYME_SAVINGS = "TymeBank Savings"
_TYME_SAVINGS_BANKS = (TYME_SAVINGS, "TymeBank Savings ({second})")  # templates: configure() names the second holder
TYME_SAVINGS_BANKS = _TYME_SAVINGS_BANKS
TYME_CLOSED = "TymeBank closed pockets"


def load_tyme_pockets(path: str) -> dict[str, str]:
    """Names of the funded TymeBank GoalSave pockets, by full account number (Config\\tyme_pockets.csv)."""
    import os
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["account_number"].strip(): r["app_label"].strip() for r in csv.DictReader(f)}


def tyme_label(account: str, names: dict[str, str], last_balance: dict[str, Decimal]) -> str:
    """A named pocket keeps its name. An unnamed pocket that still holds money is flagged. The rest (closed, zero balance) are pooled."""
    if account in names:
        return names[account]
    if last_balance.get(account, Decimal(0)) != 0:
        return f"TymeBank pocket {account[-4:]}"
    return TYME_CLOSED


def build(rows_by_bank: dict[str, list[dict]], rules: list[dict], pockets: dict[str, str] | None = None,
          pocket_props: dict[str, str] | None = None, tyme_names: dict[str, str] | None = None,
          pocket_held: dict[str, str] | None = None) -> list[dict]:
    """Each savings pocket is shown as its own account: the master bank column carries the app label."""
    pockets = pockets or {}
    pocket_props = pocket_props or {}
    tyme_names = tyme_names or {}
    pocket_held = pocket_held or {}
    master = []
    for bank, rows in rows_by_bank.items():
        last_bal: dict[str, Decimal] = {}
        if bank in TYME_SAVINGS_BANKS:
            seen_date: dict[str, str] = {}
            for r in rows:
                if r["date"] >= seen_date.get(r["account_number"], ""):
                    seen_date[r["account_number"]] = r["date"]
                    last_bal[r["account_number"]] = Decimal(r["balance"])
        counters: dict[tuple, int] = defaultdict(int)
        for r in rows:
            key = (r["account_number"], r["statement_no"])
            counters[key] += 1
            d = date.fromisoformat(r["date"])
            desc = clean_description(r["description"], r.get("desc_source", "text"))
            amount = Decimal(r["amount"])
            cat, scope, prop, hit = categorise(desc, amount, bank, rules)
            if bank == POCKET_BANK and not prop:
                prop = pocket_props.get(r["account_number"][-4:], "")
            if bank == POCKET_BANK and cat == "Interest earned" and r["account_number"][-4:] in pocket_held:
                cat = "Transfer: Tenant deposit interest" if pocket_held[r["account_number"][-4:]] == "tenant" else "Transfer: Interest held for others"  # interest on money held for someone else is theirs, not household income
            label = pockets.get(r["account_number"][-4:], f"Savings pocket {r['account_number'][-4:]}") if bank == POCKET_BANK else bank
            if bank in TYME_SAVINGS_BANKS:
                label = tyme_label(r["account_number"], tyme_names, last_bal)
                if bank != TYME_SAVINGS and (label == TYME_CLOSED or label.startswith("TymeBank pocket ")):
                    label += bank[len(TYME_SAVINGS):]  # another owner's pooled or unnamed pockets keep their own account
            master.append({
                "tx_id": tx_id(r["account_number"], r["statement_no"], counters[key]),
                "date": d.isoformat(), "month": d.strftime("%Y-%m"),
                "bank": label, "account_number": r["account_number"], "owner": r["owner"],
                "description": desc, "merchant_key": merchant_key(desc),
                "amount": f"{amount:.2f}", "direction": "in" if amount > 0 else "out",
                "category": cat, "scope": scope, "property": prop, "rule_hit": hit,
                "is_transfer": "", "transfer_pair": "",
                "balance": r.get("balance", ""), "desc_source": r.get("desc_source", "text"),
                "statement_no": r["statement_no"], "source_file": r["source_file"],
            })
    master.sort(key=lambda m: (m["date"], m["bank"], m["tx_id"]))
    return master


def apply_overrides(master: list[dict], path: str) -> list[str]:
    """Row level corrections from Config\row_overrides.csv, applied after transfer matching. A row is found by date, bank
    label, amount and a text its description contains. Columns: date, bank, amount, contains, category, property,
    split_amount, split_category, note. category and property replace the row's values when filled. split_amount cuts that
    part off the row into a second row (tx_id with an s suffix) with split_category, so one bank credit can be part rent
    and part deposit. The bank figure and the raw files are never changed. Returns a message for each override that
    matched no row or more than one row."""
    import os
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        overrides = [r for r in csv.DictReader(f) if (r.get("date") or "").strip()]
    problems = []
    extra = []
    for o in overrides:
        hits = [m for m in master if m["date"] == o["date"].strip() and m["bank"] == o["bank"].strip()
                and Decimal(m["amount"]) == Decimal(o["amount"].strip()) and o["contains"].strip().lower() in m["description"].lower()
                and not m["tx_id"].endswith("s")]
        if len(hits) != 1:
            problems.append(f"override {o['date']} {o['bank']} {o['amount']} {o['contains']!r} matched {len(hits)} rows, expected 1")
            continue
        m = hits[0]
        if (o.get("category") or "").strip():
            m["category"], m["rule_hit"] = o["category"].strip(), "override"
        if (o.get("property") or "").strip():
            m["property"] = o["property"].strip()
        part = (o.get("split_amount") or "").strip()
        if part:
            cut = Decimal(part)
            if abs(cut) >= abs(Decimal(m["amount"])) or (cut > 0) != (Decimal(m["amount"]) > 0):
                problems.append(f"override {o['date']} {o['amount']}: split_amount {part} must be smaller than the row and have the same sign")
                continue
            m["amount"] = f"{Decimal(m['amount']) - cut:.2f}"
            second = dict(m, tx_id=m["tx_id"] + "s", amount=f"{cut:.2f}", category=(o.get("split_category") or "").strip(),
                          rule_hit="override", is_transfer="", transfer_pair="")
            extra.append(second)
    master.extend(extra)
    master.sort(key=lambda m: (m["date"], m["bank"], m["tx_id"]))
    return problems


_RAW_FILES = (("FNB", "fnb_transactions.csv"), ("Nedbank", "nedbank_transactions.csv"),
             ("Discovery", "discovery_transactions.csv"), ("FNB Money Maximiser", "fnbmm_transactions.csv"),
             (POCKET_BANK, "fnbpk_transactions.csv"),
             ("TymeBank Current", "tymecur_transactions.csv"), ("TymeBank Savings", "tymesav_transactions.csv"),
             ("TymeBank Current ({second})", "tymeslcur_transactions.csv"), ("TymeBank Savings ({second})", "tymeslsav_transactions.csv"),
             ("FNB Home Loan", "fnbhl_transactions.csv"), ("Nedbank Home Loan", "nedbankhl_transactions.csv"),
             ("FNB Credit Card", "fnbcc_transactions.csv"))
RAW_FILES = _RAW_FILES


MASTER_COLUMNS = ("tx_id", "date", "month", "bank", "account_number", "owner", "description", "merchant_key", "amount",
                  "direction", "category", "scope", "property", "rule_hit", "is_transfer", "transfer_pair", "balance",
                  "desc_source", "statement_no", "source_file")   # header used when there are no rows to take it from


def configure(config_dir: str) -> None:
    """Name the second TymeBank holder in the bank labels, from Config\\household.json."""
    global TYME_SAVINGS_BANKS, TYME_CURRENT_BANKS, RAW_FILES
    cfg = household.load(config_dir)
    TYME_SAVINGS_BANKS = tuple(household.resolve(b, cfg) for b in _TYME_SAVINGS_BANKS)
    TYME_CURRENT_BANKS = tuple(household.resolve(b, cfg) for b in _TYME_CURRENT_BANKS)
    RAW_FILES = tuple((household.resolve(b, cfg), f) for b, f in _RAW_FILES) + _registered_banks()


def _registered_banks() -> tuple:
    """Raw files of banks registered in importer.BANKS that the list above does not name, so a new bank needs one registration."""
    from .importer import BANKS
    known = {f.split("_transactions")[0] for _, f in _RAW_FILES}
    return tuple((b, f"{d['stem']}_transactions.csv") for b, d in BANKS.items() if d["stem"] not in known)


def rebuild_master(raw_dir: str, master_path: str, rules_path: str) -> tuple[list[dict], int]:
    """Read the per-bank raw CSVs, categorise, match transfers and write the master CSV."""
    import os
    configure(os.path.dirname(rules_path))
    rules = load_rules(rules_path) if os.path.exists(rules_path) else []
    rows_by_bank = {}
    for bank, fname in RAW_FILES:
        raw_path = os.path.join(raw_dir, fname)
        if not os.path.exists(raw_path):   # a bank with no statements imported yet
            rows_by_bank[bank] = []
            continue
        with open(raw_path, newline="", encoding="utf-8-sig") as f:
            rows_by_bank[bank] = list(csv.DictReader(f))
    pockets = load_pockets(os.path.join(os.path.dirname(rules_path), "pocket_map.csv"))
    props = load_pocket_props(os.path.join(os.path.dirname(rules_path), "pocket_map.csv"))
    tyme_names = load_tyme_pockets(os.path.join(os.path.dirname(rules_path), "tyme_pockets.csv"))
    held = load_pocket_held(os.path.join(os.path.dirname(rules_path), "pocket_map.csv"))
    master = build(rows_by_bank, rules, pockets, props, tyme_names, held)
    pairs = match_transfers(master)
    override_problems = apply_overrides(master, os.path.join(os.path.dirname(rules_path), "row_overrides.csv"))
    for msg in override_problems:
        print("WARNING:", msg)
    os.makedirs(os.path.dirname(master_path), exist_ok=True)
    tmp = master_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(master[0]) if master else list(MASTER_COLUMNS))
        w.writeheader()
        w.writerows(master)
    os.replace(tmp, master_path)
    return master, pairs


_TYME_CURRENT_BANKS = ("TymeBank Current", "TymeBank Current ({second})")
TYME_CURRENT_BANKS = _TYME_CURRENT_BANKS
GOALSAVE_REF = re.compile(r"GoalSave (\d{9,})")


def _is_tyme_pocket(m: dict) -> bool:
    return m["source_file"].startswith("TymeBank Sav") or m["bank"].startswith(("TymeBank closed pockets", "TymeBank pocket "))


def _goalsave_ok(m: dict, c: dict) -> bool:
    """A GoalSave movement on a TymeBank current account pairs only with a pocket of the same owner. When the current
    account row names the pocket, the pocket account number must start with it (the statement prints it one digit short)."""
    if m["owner"] != c["owner"] or _is_tyme_pocket(m) == _is_tyme_pocket(c):
        return False
    cur = c if _is_tyme_pocket(m) else m
    pocket = m if _is_tyme_pocket(m) else c
    ref = GOALSAVE_REF.search(cur["description"])
    return not ref or pocket["account_number"].startswith(ref.group(1))


def _pair_unique(pool: list[dict], window_days: int, ok, pairs: int, balanced: bool = False) -> int:
    """Pair rows of the pool (same amount, opposite direction, different account, within the window, ok(m, c)) when the
    pair is the only candidate for both rows. Repeats until nothing more pairs. With balanced, rows of one day with equal
    numbers of debits and credits of one amount are also paired in order, since either pairing leaves both legs found."""
    while True:
        free = [m for m in pool if not m["is_transfer"]]
        ins = defaultdict(list)
        for c in free:
            if c["direction"] == "in":
                ins[c["amount"]].append(c)
        cand, back = [], defaultdict(list)
        for m in free:
            if m["direction"] != "out":
                continue
            d = date.fromisoformat(m["date"])
            hits = [c for c in ins.get(f"{-Decimal(m['amount']):.2f}", [])
                    if c["account_number"] != m["account_number"]
                    and abs(date.fromisoformat(c["date"]) - d) <= timedelta(days=window_days) and ok(m, c)]
            cand.append((m, hits))
            for c in hits:
                back[c["tx_id"]].append(m)
        new = 0
        for m, hits in cand:
            if len(hits) == 1 and len(back[hits[0]["tx_id"]]) == 1 and not m["is_transfer"] and not hits[0]["is_transfer"]:
                pairs += 1
                m["is_transfer"] = hits[0]["is_transfer"] = "matched"
                m["transfer_pair"] = hits[0]["transfer_pair"] = f"T{pairs:04d}"
                new += 1
        if not new and balanced:
            groups = defaultdict(lambda: ([], []))
            for m in free:
                groups[(m["date"], abs(Decimal(m["amount"])), m["owner"])][0 if m["direction"] == "out" else 1].append(m)
            for outs, ins_ in groups.values():
                if len(outs) != len(ins_):
                    continue
                for m in sorted(outs, key=lambda x: x["tx_id"]):
                    for c in sorted(ins_, key=lambda x: x["tx_id"]):
                        if not c["is_transfer"] and c["account_number"] != m["account_number"] and ok(m, c):
                            pairs += 1
                            m["is_transfer"] = c["is_transfer"] = "matched"
                            m["transfer_pair"] = c["transfer_pair"] = f"T{pairs:04d}"
                            new += 1
                            break
        if not new:
            return pairs


def match_transfers(master: list[dict], window_days: int = 2) -> int:
    """Pair a debit in one account with an equal credit in a different account within a few days.
    Pass 1 pairs TymeBank GoalSave movements on a current account with the pockets of the same owner. Pass 2 pairs rows
    whose category starts with Transfer:, only when the pair is the sole candidate for both rows. Together they stop a
    card purchase or a fixed deposit leg of the same amount from taking a transfer's other leg. Pass 3 is the original
    greedy pairing for what remains."""
    pairs = 0
    live = [m for m in master if m["bank"] not in LOAN_BANKS and (m["category"] or "").startswith("Transfer:")]
    gs = [m for m in live if _is_tyme_pocket(m) or (m["bank"] in TYME_CURRENT_BANKS and "goalsave" in m["description"].lower())]
    pairs = _pair_unique(gs, window_days, _goalsave_ok, pairs, balanced=True)
    pairs = _pair_unique(live, window_days, lambda m, c: True, pairs)
    credits = defaultdict(list)
    for m in master:
        if m["bank"] in LOAN_BANKS:
            continue  # the loan side of a debit order is not paired: the FNB debit order stays a Housing: Bond cost
        if m["direction"] == "in":
            credits[m["amount"]].append(m)
    for m in master:
        if m["direction"] != "out" or m["is_transfer"] or m["bank"] in LOAN_BANKS:
            continue
        want = f"{-Decimal(m['amount']):.2f}"
        d = date.fromisoformat(m["date"])
        for c in credits.get(want, []):
            if (c["is_transfer"] or c["account_number"] == m["account_number"]
                    or abs(date.fromisoformat(c["date"]) - d) > timedelta(days=window_days)):
                continue
            pairs += 1
            m["is_transfer"] = c["is_transfer"] = "matched"
            m["transfer_pair"] = c["transfer_pair"] = f"T{pairs:04d}"
            break
    return pairs
