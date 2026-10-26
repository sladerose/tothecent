"""Build the self-contained household dashboard (one HTML file with the data embedded).

usage: build_dashboard.py MASTER_CSV RAW_DIR TEMPLATE_HTML OUT_HTML
"""

import csv
import json
import re
import sys
from datetime import datetime

master_csv, raw_dir, template_path, out_path = sys.argv[1:5]

with open(master_csv, newline="", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

LOAN_BANKS = {"FNB Home Loan", "Nedbank Home Loan"}   # liabilities: kept out of cash, income and spending, shown in the loans block
rows = [r for r in rows if r["bank"] not in LOAN_BANKS]

import os
from finance_tools import household
HH = household.load(os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "Config"))
STEM_BANK = {"fnb": "FNB", "discovery": "Discovery", "nedbank": "Nedbank", "fnbmm": "FNB Money Maximiser", "fnbpk": "FNB Savings Pockets", "fnbcc": "FNB Credit Card",
             "tymecur": "TymeBank Current", "tymesav": "TymeBank Savings", "tymeslcur": "TymeBank Current ({second})", "tymeslsav": "TymeBank Savings ({second})"}
SECOND = HH["second_tyme_holder"]
OWN = {stem: HH["owners"].get(household.resolve(bank, HH), "") for stem, bank in STEM_BANK.items()}   # owner of each raw file, from Config\household.json
with open(os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "Config", "pocket_map.csv"), newline="", encoding="utf-8-sig") as f:
    pocket_rows = list(csv.DictReader(f))
    pocket_labels = {r["identifier_last4"].strip(): r["app_label"].strip() for r in pocket_rows}
    pocket_held = {r["identifier_last4"].strip(): (r.get("held_for") or "").strip() for r in pocket_rows}  # money held for someone else, not household cash
    pocket_type = {r["identifier_last4"].strip(): (r.get("pocket_type") or "").strip() for r in pocket_rows}  # spend, tenant or blank (keep)
def raw_statements(stem):
    """Statement rows of one raw file. A bank that is not in use has no file."""
    path = f"{raw_dir}/{stem}_statements.csv"
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


pocket_accounts = sorted({s["account_number"] for s in raw_statements("fnbpk")}, key=lambda a: pocket_labels.get(a[-4:], a))

# (name, owner, raw file stem, type, account number filter). Each savings pocket is its own account, told apart by account number.
bank_defs = [("FNB", OWN["fnb"], "fnb", "current", None), ("Discovery", OWN["discovery"], "discovery", "current", None),
             ("Nedbank", OWN["nedbank"], "nedbank", "current", None), ("FNB Money Maximiser", OWN["fnbmm"], "fnbmm", "savings", None)]
bank_defs += [(pocket_labels.get(a[-4:], f"Savings pocket {a[-4:]}"), OWN["fnbpk"], "fnbpk", "savings", a) for a in pocket_accounts]
# The FNB credit card: an everyday account that is in credit most months and shows what is owed when it is not.
if os.path.exists(f"{raw_dir}/fnbcc_statements.csv"):
    with open(f"{raw_dir}/fnbcc_statements.csv", newline="", encoding="utf-8-sig") as f:
        if any(True for _ in csv.DictReader(f)):
            bank_defs += [("FNB Credit Card", OWN["fnbcc"], "fnbcc", "current", None)]
# TymeBank: the current account is one account. Each funded GoalSave pocket is its own account (named in Config\tyme_pockets.csv).
# Closed pockets (zero balance, no name) are pooled into one account so the history stays without clutter. A tuple means a pool of accounts.
with open(os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "Config", "tyme_pockets.csv"), newline="", encoding="utf-8-sig") as f:
    tyme_names = {r["account_number"].strip(): r["app_label"].strip() for r in csv.DictReader(f)}
_tsts = raw_statements("tymesav")
_tclose = {}
for _s in sorted(_tsts, key=lambda s: s["period_end"]):
    _tclose[_s["account_number"]] = float(_s["closing"])
_funded = sorted([a for a in _tclose if a in tyme_names or _tclose[a] != 0], key=lambda a: (-_tclose[a], a))
_closed = tuple(sorted(a for a in _tclose if a not in _funded))
bank_defs += [("TymeBank Current", OWN["tymecur"], "tymecur", "current", None)]
bank_defs += [(tyme_names.get(a, f"TymeBank pocket {a[-4:]}"), OWN["tymesav"], "tymesav", "savings", a) for a in _funded]
if _closed:
    bank_defs += [("TymeBank closed pockets", OWN["tymesav"], "tymesav", "savings", _closed)]
# The second holder's TymeBank account (monthly emailed statements): the same rules, with the second holder in brackets in the label as in the master table.
if os.path.exists(f"{raw_dir}/tymeslsav_statements.csv"):
    with open(f"{raw_dir}/tymeslsav_statements.csv", newline="", encoding="utf-8-sig") as f:
        _ssts = list(csv.DictReader(f))
    _sclose = {}
    for _s in sorted(_ssts, key=lambda s: s["period_end"]):
        _sclose[_s["account_number"]] = float(_s["closing"])
    _sfunded = sorted([a for a in _sclose if a in tyme_names or _sclose[a] != 0], key=lambda a: (-_sclose[a], a))
    _sclosed = tuple(sorted(a for a in _sclose if a not in _sfunded))
    with open(f"{raw_dir}/tymeslcur_statements.csv", newline="", encoding="utf-8-sig") as f:
        _scur = list(csv.DictReader(f))
    if _scur:
        bank_defs += [(f"TymeBank Current ({SECOND})", OWN["tymeslcur"], "tymeslcur", "current", None)]
    bank_defs += [(tyme_names.get(a, f"TymeBank pocket {a[-4:]} ({SECOND})"), OWN["tymeslsav"], "tymeslsav", "savings", a) for a in _sfunded]
    if _sclosed:
        bank_defs += [(f"TymeBank closed pockets ({SECOND})", OWN["tymeslsav"], "tymeslsav", "savings", _sclosed)]
# Any other bank registered in importer.BANKS (a parser added later) is one account of the type its entry names. Loans need their own panel.
from finance_tools.importer import BANKS
_known_stems = set(STEM_BANK) | {"fnbhl", "nedbankhl"}
bank_defs += [(name, HH["owners"].get(name, ""), d["stem"], d.get("type", "current"), None)
              for name, d in BANKS.items() if d["stem"] not in _known_stems and d.get("type") != "loan"]


def inset(spec, account):
    """Does an account belong to a bank definition? None means every account in the file, a string one account, a tuple a pool."""
    return spec is None or (account in spec if isinstance(spec, tuple) else account == spec)
bank_defs = [d for d in bank_defs if any(inset(d[4], s["account_number"]) for s in raw_statements(d[2]))]  # an account with no statements yet has nothing to show
banks = []
for name, owner, stem, kind, acct in bank_defs:
    sts = [s for s in raw_statements(stem) if inset(acct, s["account_number"])]
    banks.append({
        "name": name, "owner": owner, "type": kind, "group": ("Everyday" if kind == "current" else "Money Maximiser" if "Money Maximiser" in name else "Savings pockets"), "held": (pocket_held.get(acct[-4:], "") if isinstance(acct, str) else ""),
        "ptype": ("" if kind != "savings" else "spend" if (isinstance(acct, str) and pocket_type.get(acct[-4:]) == "spend") else "" if (isinstance(acct, str) and pocket_type.get(acct[-4:]) == "tenant") else "keep"),
        "from": min(s["period_start"] for s in sts), "to": max(s["period_end"] for s in sts),
        "statements": len(sts), "reconciled": sum(1 for s in sts if s["reconciled"] == "True"),
        "rows": sum(1 for r in rows if r["bank"] == name),
    })


def pooled_balances(fname, sfile, pool):
    """Month end balance of a set of accounts held in one file (the GoalSave pockets), added together.
    Each pocket keeps its last balance until its next transaction. Before a pocket's first statement it counts as its opening balance."""
    with open(f"{raw_dir}/{sfile}", newline="", encoding="utf-8-sig") as f:
        sts = [s for s in csv.DictReader(f) if s["account_number"] in pool]
    with open(f"{raw_dir}/{fname}", newline="", encoding="utf-8-sig") as f:
        txs = [t for t in csv.DictReader(f) if t["account_number"] in pool]
    accts = sorted({s["account_number"] for s in sts})
    first = min(s["period_start"] for s in sts)[:7]
    final_end = max(s["period_end"] for s in sts)
    last = {a: {} for a in accts}
    for t in txs:
        last[t["account_number"]][t["date"][:7]] = float(t["balance"])
    opening = {}
    for a in accts:
        mine = sorted((s for s in sts if s["account_number"] == a), key=lambda s: s["period_start"])
        opening[a] = (mine[0]["period_start"][:7], float(mine[0]["opening"]))
    months, y, mo = [], int(first[:4]), int(first[5:])
    while True:
        m = f"{y}-{mo:02d}"
        months.append(m)
        if m >= final_end[:7]:
            break
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
    cur = {a: (opening[a][1] if opening[a][0] <= first else 0.0) for a in accts}
    series = []
    for m in months:
        for a in accts:
            if m in last[a]:
                cur[a] = last[a][m]
            elif m == opening[a][0]:
                cur[a] = opening[a][1]
        series.append([m, round(sum(cur.values()), 2)])
    closing = 0.0
    for a in accts:
        mine = sorted((s for s in sts if s["account_number"] == a), key=lambda s: s["period_end"])
        closing += float(mine[-1]["closing"])
    return {"asAt": final_end, "closing": round(closing, 2), "series": series}


def month_end_balances(fname, sfile, acct=None):
    """Running balance per transaction (bank figure, or opening plus cumulative where the bank prints none),
    then the last balance in each month, carried forward across quiet months."""
    with open(f"{raw_dir}/{sfile}", newline="", encoding="utf-8-sig") as f:
        sts = {s["statement_no"]: s for s in csv.DictReader(f) if acct is None or s["account_number"] == acct}
    with open(f"{raw_dir}/{fname}", newline="", encoding="utf-8-sig") as f:
        txs = [t for t in csv.DictReader(f) if acct is None or t["account_number"] == acct]
    last, run, cur = {}, None, None
    for t in txs:
        if t["statement_no"] != cur:
            cur = t["statement_no"]; run = float(sts[cur]["opening"])
        run = float(t["balance"]) if t["balance"] else round(run + float(t["amount"]), 2)
        last[t["date"][:7]] = (t["date"], run)
    first = min(s["period_start"] for s in sts.values())[:7]
    final = max(sts.values(), key=lambda s: s["period_end"])
    assert abs(last[max(last)][1] - float(final["closing"])) < 0.005, f"{fname}: running balance does not meet closing balance"
    series, bal, y, mo = [], None, int(first[:4]), int(first[5:])
    end = max(last)
    while True:
        m = f"{y}-{mo:02d}"
        if m in last: bal = last[m][1]
        series.append([m, bal])
        if m >= end: break
        mo += 1
        if mo > 12: mo, y = 1, y + 1
    return {"asAt": final["period_end"], "closing": float(final["closing"]), "series": series}

balances = [pooled_balances(f"{stem}_transactions.csv", f"{stem}_statements.csv", acct) if isinstance(acct, tuple)
            else month_end_balances(f"{stem}_transactions.csv", f"{stem}_statements.csv", acct) for _, _, stem, _, acct in bank_defs]

bank_idx = {b["name"]: i for i, b in enumerate(banks)}
categories = sorted({r["category"] for r in rows if r["category"]})
properties = sorted({r["property"] for r in rows if r["property"]})
cat_idx = {c: i for i, c in enumerate(categories)}
prop_idx = {p: i for i, p in enumerate(properties)}

PAYEE_PREFIX = re.compile(r"^(pos purchase|magtape debit|magtape credit|fuel purchase|fnb app payment to|fnb app payment from|debicheck|payment to|payshap credit|payshap debit|internal debit order|b ?2? ?b collection|netbank payment|mvno payment|electricity prepaid)(\s+|$)")
MONTHS = r"(january|february|march|april|may|june|july|august|september|october|november|december)"


def payee_name(key):
    """A readable payee from the master merchant key: bank prefixes and reference noise removed,
    month names dropped so a monthly rent payment groups as one payee. Observed grouping, not a bank field."""
    k = key.replace("*", "")
    k = PAYEE_PREFIX.sub("", k)
    k = re.sub(MONTHS + r"\b", "", k)
    k = re.sub(r"[/\-]+", " ", k)
    k = re.sub(r"\s+", " ", k).strip()
    k = re.sub(r"x{3,}", "", k).strip()
    if key.startswith("fnb app payment") and not re.search(r"[a-z]{4,}", k):
        return "App payment, reference only"   # the reference is typed by the payer, not a payee name
    return (k.title()[:40] or "Unnamed")


payees = sorted({payee_name(r["merchant_key"]) for r in rows})
payee_idx = {n: i for i, n in enumerate(payees)}
tx = [[r["date"], bank_idx[r["bank"]], float(r["amount"]),
       cat_idx.get(r["category"], -1), prop_idx.get(r["property"], -1), r["description"],
       payee_idx[payee_name(r["merchant_key"])]] for r in rows]


def build_recurring():
    """Monthly outgoing payments that repeat: grouped by bank and merchant key, kept when they appear in most months
    between first and last seen, at most about one a month. Fixed means the amount holds steady, so a change is a price change."""
    from collections import defaultdict
    from statistics import median
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        amt = float(r["amount"])
        if amt >= 0 or r["category"].startswith("Transfer:") or r["is_transfer"]:
            continue
        if r["category"] == "Bank fees" and "monthly account fee" not in r["merchant_key"]:
            continue
        groups[(r["bank"], r["merchant_key"])].append((i, r["date"], -amt, r))
    data_end = max(r["date"] for r in rows)
    out = []
    for (bank, key), items in groups.items():
        items.sort(key=lambda x: x[1])
        months = sorted({x[1][:7] for x in items})
        debit_order = re.match(r"(magtape debit|debicheck|b ?2? ?b collection|internal debit order|nedbhl|fee monthly|#monthly)", key)
        if len(months) < (4 if debit_order else 6):
            continue
        span = (int(months[-1][:4]) - int(months[0][:4])) * 12 + int(months[-1][5:]) - int(months[0][5:]) + 1
        if len(months) / span < 0.7 or len(items) / len(months) > 1.6:
            continue
        amts = [x[2] for x in items]
        med = median(amts)
        stable = sum(1 for a in amts if abs(a - med) <= 0.05 * med) / len(amts) >= 0.6
        changes = []
        if stable:
            lvl = [(x[1], x[2]) for x in items]
            prev = lvl[0][1]
            for j in range(1, len(lvl)):
                d, a = lvl[j]
                nxt_ok = j == len(lvl) - 1 or abs(lvl[j + 1][1] - a) <= 0.03 * a
                if abs(a - prev) > 0.03 * prev and nxt_ok:
                    changes.append({"date": d, "from": round(prev, 2), "to": round(a, 2)})
                    prev = a
        if len([c for c in changes if c["date"] >= str(int(data_end[:4]) - 1) + data_end[4:]]) >= 3:
            stable, changes = False, []   # the amount keeps moving: treat as variable, not as price changes
        if not stable and not debit_order and items[-1][3]["category"] not in ("Insurance", "Subscriptions and software", "Gym"):
            continue   # variable card spend such as fuel or restaurants is not a recurring commitment
        last_d = items[-1][1]
        gap = (datetime.fromisoformat(data_end) - datetime.fromisoformat(last_d)).days
        recent = [x[2] for x in items[-6:]]
        out.append({"name": items[-1][3]["description"][:48], "bank": bank_idx[bank], "cat": cat_idx.get(items[-1][3]["category"], -1),
                    "months": len(months), "first": items[0][1], "last": last_d, "active": gap <= 50, "fixed": stable,
                    "now": round(amts[-1] if stable else median(recent), 2), "lo": round(min(amts), 2), "hi": round(max(amts), 2),
                    "changes": changes[-3:], "rows": [x[0] for x in items]})
    out.sort(key=lambda x: (not x["active"], -x["now"]))
    return out

recurring = build_recurring()

import os


def read_targets():
    """Optional Config\\targets.csv: key,value. A blank value means no target is set for that key."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(master_csv)))), "Config", "targets.csv")
    out = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                v = (r.get("value") or "").strip()
                if v:
                    try:
                        out[r["key"].strip()] = float(v)
                    except ValueError:
                        pass
    return out


targets = read_targets()


def build_electricity():
    """Prepaid electricity for the home meter. Rand comes from the bank rows whose description carries the meter number
    (Config\\electricity.csv). Readings are captured by hand in Config\\electricity_log.csv (type reading, meter_kwh is the
    units left on the meter display). A purchase row with kWh is optional and attaches to the bank purchase on the same date."""
    cfg_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(master_csv)))), "Config")
    meter = None
    path = os.path.join(cfg_dir, "electricity.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["key"].strip() == "home_meter":
                    meter = r["value"].strip() or None
    if not meter:
        return None
    buys = [{"date": r["date"], "rand": round(-float(r["amount"]), 2), "kwh": None, "row": i}
            for i, r in enumerate(rows)
            if r["category"] == "Housing: Electricity" and float(r["amount"]) < 0 and meter in r["description"].replace(" ", "")]
    reads = []
    lpath = os.path.join(cfg_dir, "electricity_log.csv")
    if os.path.exists(lpath):
        with open(lpath, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d, typ = (r.get("date") or "").strip(), (r.get("type") or "").strip().lower()
                try:
                    datetime.fromisoformat(d)
                except ValueError:
                    continue
                num = lambda k: float(r[k]) if (r.get(k) or "").strip() else None
                if typ == "reading" and num("meter_kwh") is not None:
                    reads.append({"date": d, "kwh": num("meter_kwh")})
                elif typ == "purchase" and num("kwh") is not None:
                    rand = num("rand")
                    hit = next((b for b in buys if b["date"] == d and b["kwh"] is None and (rand is None or abs(b["rand"] - rand) < 0.01)), None)
                    if hit:
                        hit["kwh"] = num("kwh")
    buys.sort(key=lambda b: b["date"])
    reads.sort(key=lambda b: b["date"])
    return {"meter": meter[-4:], "buys": buys, "reads": reads}


electricity = build_electricity()


LOAN_DEFS = [
    {"name": "FNB Home Loan", "stem": "fnbhl", "owner": HH["owners"].get("FNB Home Loan", ""), "property": HH["loan_properties"].get("FNB Home Loan", ""),
     "repay": lambda d: d == "debit order", "drawn": lambda d: d.startswith("payout"),
     "note": "The instalment is already counted once as Housing: Bond from the FNB current account."},
    {"name": "Nedbank Home Loan", "stem": "nedbankhl", "owner": HH["owners"].get("Nedbank Home Loan", ""), "property": HH["loan_properties"].get("Nedbank Home Loan", ""),
     "repay": lambda d: d.startswith("payment"), "drawn": lambda d: d.startswith("disbursement"),
     "note": "The balance is as at the last statement date. Interest for a month is posted on the first of the next month, so the amount owed the day after the statement is higher."},
]


def build_loans():
    """Home loans from the raw loan files. Balances are positive amounts owed. Per month: interest, fees, repayments
    and capital repaid (repayments less interest and fees). Interest counts as a cost of the property, the capital is equity."""
    import os
    out = []
    for df in LOAN_DEFS:
        if df["name"] not in HH["owners"]:  # a loan that the household does not have
            continue
        df = dict(df, note=" ".join(x for x in (df["note"], HH.get("loan_notes", {}).get(df["name"], "")) if x))
        spath = os.path.join(raw_dir, df["stem"] + "_statements.csv")
        tpath = os.path.join(raw_dir, df["stem"] + "_transactions.csv")
        if not (os.path.exists(spath) and os.path.exists(tpath)):
            continue
        with open(spath, newline="", encoding="utf-8-sig") as f:
            sts = list(csv.DictReader(f))
        with open(tpath, newline="", encoding="utf-8-sig") as f:
            txs = list(csv.DictReader(f))
        for acct in sorted({s["account_number"] for s in sts}):
            mine = sorted((s for s in sts if s["account_number"] == acct), key=lambda s: s["period_start"])
            t = [x for x in txs if x["account_number"] == acct]
            if not t:
                continue
            months, opened = {}, ""
            for x in t:
                m = months.setdefault(x["date"][:7], {"balance": 0.0, "interest": 0.0, "fees": 0.0, "repaid": 0.0, "drawn": 0.0, "init": 0.0, "last": ""})
                a = float(x["amount"])
                d = x["description"].lower()
                if d == "interest":
                    m["interest"] += -a
                elif "initiation" in d:
                    m["init"] += -a   # added to the loan, not paid from the instalment
                elif "fee" in d:
                    m["fees"] += -a
                elif df["repay"](d):
                    m["repaid"] += a
                elif df["drawn"](d):
                    m["drawn"] += -a
                    opened = min(opened, x["date"]) if opened else x["date"]
                if x["date"] >= m["last"]:
                    m["last"], m["balance"] = x["date"], -float(x["balance"])
            first, last_end = min(months), mine[-1]["period_end"][:7]
            series, bal, y, mo = [], 0.0, int(first[:4]), int(first[5:])
            while True:
                k = f"{y}-{mo:02d}"
                if k in months:
                    bal = months[k]["balance"]
                series.append([k, round(bal, 2), round(months.get(k, {}).get("interest", 0), 2), round(months.get(k, {}).get("fees", 0), 2),
                               round(months.get(k, {}).get("repaid", 0), 2)])
                if k >= last_end:
                    break
                mo += 1
                if mo > 12:
                    mo, y = 1, y + 1
            drawn = round(sum(v["drawn"] for v in months.values()), 2)
            interest = round(sum(v["interest"] for v in months.values()), 2)
            fees = round(sum(v["fees"] for v in months.values()), 2)
            repaid = round(sum(v["repaid"] for v in months.values()), 2)
            init = round(sum(v["init"] for v in months.values()), 2)
            out.append({"name": df["name"], "account": acct[-4:], "owner": df["owner"], "property": df["property"],
                        "asAt": mine[-1]["period_end"], "balance": round(-float(mine[-1]["closing"]), 2), "lent": drawn, "opened": opened,
                        "initiation": init, "interest": interest, "fees": fees, "repaid": repaid, "capital": round(repaid - interest - fees, 2),
                        "series": series, "statements": len(mine), "reconciled": sum(1 for s in mine if s["reconciled"] == "True"),
                        "note": df["note"]})
    return out


loans = build_loans()
def build_networth():
    """Net worth inputs. Each property is carried at its current value if one is recorded in Config/assets.csv, otherwise
    at its purchase price, with the bond owed against it. A bond is owed at its last statement balance unless
    Config/loan_balances.csv holds a later posted balance. Cash is summed in the page from the account balances, so that
    it matches the Cash on hand tile."""
    import os
    cfg = os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "Config")
    assets, posted = {}, {}
    p = os.path.join(cfg, "assets.csv")
    if os.path.exists(p):
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                assets.setdefault(r["property"], {})[r["item"]] = r
    p = os.path.join(cfg, "loan_balances.csv")
    if os.path.exists(p):
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                posted.setdefault(r["account_last4"], []).append(r)
    props = []
    for l in loans:
        a = assets.get(l["property"], {})
        cur, price = a.get("current_value"), a.get("purchase_price")
        if cur and cur.get("amount"):
            value, basis, vdate = float(cur["amount"]), ("Municipal value" if "municipal" in (cur.get("source") or "").lower() else "Estimate"), cur.get("date") or ""
        elif price and price.get("amount"):
            value, basis, vdate = float(price["amount"]), "Purchase price", price.get("date") or ""
        else:
            continue
        owed, as_at, how = l["balance"], l["asAt"], "Last statement balance"
        later = [r for r in posted.get(l["account"], []) if r["as_at"] > l["asAt"]]
        if later:
            r = max(later, key=lambda r: r["as_at"])
            owed, as_at, how = float(r["balance_owed"]), r["as_at"], r["source"]
        props.append({"name": l["property"], "value": value, "basis": basis, "valueDate": vdate,
                      "loan": l["name"], "owed": round(owed, 2), "asAt": as_at, "how": how})
    return {"properties": props}


networth = build_networth()
def build_bills():
    """Payments due and late from Config\\bills.csv. The last payment is found in the master by exact bank label and
    description text. A bill with a due day is judged against its most recent due date: paid when a matching payment
    falls within ten days before it (a bill with issue_day and due_offset_days has a floating due date and is paid once a payment falls on or after the issue date), awaiting statement when the bank data does not yet reach three days past it,
    otherwise late. A bill with a statement_due date is judged on that date alone, because it is settled in lumps."""
    import calendar
    from datetime import date, timedelta
    path = os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "Config", "bills.csv")
    if not os.path.exists(path):
        return []
    today = datetime.now().date()
    root_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "..", ".."))
    bank_end = {}
    for r in rows:
        if r["date"] > bank_end.get(r["bank"], ""):
            bank_end[r["bank"]] = r["date"]

    def clamp(y, m, d):
        return date(y, m, min(d, calendar.monthrange(y, m)[1]))

    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for b in csv.DictReader(f):
            due_day = int(b["due_day"]) if b["due_day"].strip().isdigit() else None
            issue_day = int(b["issue_day"]) if (b.get("issue_day") or "").strip().isdigit() else None
            offset = int(b["due_offset_days"]) if (b.get("due_offset_days") or "").strip().isdigit() else None
            mb, mt = b["match_bank"].strip().lower(), b["match_text"].strip().lower()
            last, lastamt = None, None
            if mt:
                for r in rows:
                    if float(r["amount"]) >= 0 or r["bank"].lower() != mb or mt not in r["description"].lower():
                        continue
                    if last is None or r["date"] >= last:
                        last, lastamt = r["date"], -float(r["amount"])
            ends = [e for k, e in bank_end.items() if mb and k.lower() == mb]
            end = max(ends) if ends else ""
            due, status = None, "unknown"
            amount = float(b["amount"]) if b["amount"].strip() else None
            st = None
            if (b.get("statement_dir") or "").strip():
                try:
                    from finance_tools.kwadukuza import latest
                    st = latest(os.path.join(root_dir, b["statement_dir"].strip()))
                except Exception:
                    st = None
                if st and st["due_date"]:
                    b = dict(b, statement_due=st["due_date"], statement_amount=str(st["total_due"] if st["total_due"] is not None else ""))
            sdue = (b.get("statement_due") or "").strip()
            if sdue:
                due = date.fromisoformat(sdue)
                status = "late" if due < today else ("due soon" if (due - today).days <= 7 else "later")
                if (b.get("statement_amount") or "").strip():
                    amount = float(b["statement_amount"])
            elif (due_day or (issue_day and offset)) and last:
                if issue_day and offset:
                    # Floating due date: issued on issue_day, due due_offset_days later. Paid once a payment falls on or after the issue date.
                    cands = [clamp(today.year + (today.month + k - 1) // 12, (today.month + k - 1) % 12 + 1, issue_day) + timedelta(days=offset) for k in (-2, -1, 0, 1)]
                    window = offset
                else:
                    cands = [clamp(today.year + (today.month + k - 1) // 12, (today.month + k - 1) % 12 + 1, due_day) for k in (-1, 0, 1)]
                    window = 10
                cd = max(c for c in cands if c <= today)
                nd = min(c for c in cands if c > today)
                paid = date.fromisoformat(last) >= cd - timedelta(days=window)
                if paid:
                    status, due = ("due soon", nd) if (nd - today).days <= 7 else ("paid", cd)
                elif end and end < (cd + timedelta(days=3)).isoformat():
                    status, due = "awaiting statement", cd
                else:
                    status, due = "late", cd
            out.append({"id": b["bill_id"], "bill": b["bill"], "who": b["who_pays"], "amount": amount, "amountType": b["amount_type"],
                        "due": due.isoformat() if due else "", "dueDay": due_day, "status": status, "lastPaid": last or "",
                        "lastAmount": round(lastamt, 2) if lastamt is not None else None, "how": b["how_paid"],
                        "confirmed": b["confirmed"], "note": b["note"], "statementDate": st["account_date"] if st else ""})
    return out


bills = build_bills()


def build_yearahead(bills):
    """The year ahead: every dated payment from today to the same day next year. Monthly bills come from Config\\bills.csv
    (a fixed due day, or a floating due date of issue_day plus due_offset_days, or the single statement due date held by
    build_bills). Annual and irregular items come from Config\\obligations.csv and repeat each year from next_due."""
    import calendar
    from datetime import date, timedelta
    cfg = os.path.join(os.path.dirname(os.path.abspath(master_csv)), "..", "..", "Config")
    today = datetime.now().date()
    end = today.replace(year=today.year + 1, day=28) if (today.month, today.day) == (2, 29) else today.replace(year=today.year + 1)
    byid = {b["id"]: b for b in bills}

    def clamp(y, m, d):
        return date(y, m, min(d, calendar.monthrange(y, m)[1]))

    def monthly(day):
        out = []
        for k in range(14):
            y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
            out.append(clamp(y, m, day))
        return out

    items = []
    path = os.path.join(cfg, "bills.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for b in csv.DictReader(f):
                bd = byid.get(b["bill_id"], {})
                due_day = int(b["due_day"]) if b["due_day"].strip().isdigit() else None
                issue_day = int(b["issue_day"]) if (b.get("issue_day") or "").strip().isdigit() else None
                offset = int(b["due_offset_days"]) if (b.get("due_offset_days") or "").strip().isdigit() else None
                if due_day:
                    cand = monthly(due_day)
                elif issue_day and offset:
                    cand = [d + timedelta(days=offset) for d in monthly(issue_day)]
                else:
                    cand = [date.fromisoformat(bd["due"])] if bd.get("due") else []
                amount = float(b["amount"]) if b["amount"].strip() else bd.get("amount") or bd.get("lastAmount")
                items.append({"id": b["bill_id"], "name": b["bill"], "group": "Monthly bills and debit orders", "amount": amount,
                              "amountType": b["amount_type"], "confirmed": b["confirmed"], "note": b["note"],
                              "dates": [d.isoformat() for d in cand if today <= d <= end]})
    path = os.path.join(cfg, "obligations.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for o in csv.DictReader(f):
                d = date.fromisoformat(o["next_due"])
                dates = []
                while d <= end:
                    if d >= today:
                        dates.append(d.isoformat())
                    if o["frequency"].strip() != "yearly":
                        break
                    d = clamp(d.year + 1, d.month, d.day)
                items.append({"id": o["obligation_id"], "name": o["obligation"], "group": o["category"],
                              "amount": float(o["amount"]) if o["amount"].strip() else None, "amountType": o["amount_type"],
                              "confirmed": o["confirmed"], "note": o["note"], "dates": dates})
    return {"start": today.isoformat(), "end": end.isoformat(), "items": items}


yearahead = build_yearahead(bills)
data = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "banks": banks, "categories": categories,
        "properties": properties, "balances": balances, "recurring": recurring, "payees": payees, "targets": targets, "notes": HH["dashboard_notes"],
        "electricity": electricity, "loans": loans, "networth": networth, "bills": bills, "yearAhead": yearahead, "tx": tx}
blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

with open(template_path, encoding="utf-8") as f:
    html = f.read()
assert "/*DATA*/null" in html, "template is missing the data placeholder"
html = html.replace("/*DATA*/null", blob)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"dashboard written: {out_path} ({len(html) / 1024:.0f} KB, {len(tx)} transactions)")
