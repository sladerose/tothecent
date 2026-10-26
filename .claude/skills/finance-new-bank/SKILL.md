---
name: finance-new-bank
description: Build a parser for a bank or account type the tool cannot read yet. Use whenever the user mentions a bank, card, loan or account that is not supported, says a statement will not import or reconcile, asks whether their bank can be added, or says anything like a missing or broken parser. When the parser is proven, this hands over to finance-contribute.
---

# Add a new bank

Say this first, in plain words: a parser is written from the user's own statements, it takes effort, and it cannot be promised. Some statements are scans, some change layout, some never reconcile. The user's own statements stay on this computer.

## 1. Check the options

Ask whether the bank offers a CSV or Excel export, or a PDF with selectable text. A text PDF is the best case. A scanned image needs Tesseract and is the worst case. Ask for two or three statements of the same account, ideally from different months, and put them in a scratch folder outside the project (for example `~/finance-scratch/<bank>`). Keep them there until `check_parser.py` has passed on every one. Only then copy a single statement into `Inbox` for the dry run in section 3. Never commit them.

## 2. Read the contract

A parser is one module in `Tools\finance_tools\<name>.py`. Read `discovery.py` (no running balance) and `fnbcc.py` or `nedbank.py` (running balance) as worked examples. The module must provide:

- `parse_statement(path)` returning one statement, or `parse_file(path)` returning a list when one file holds several accounts.
- `reconcile(statement)` returning a list of problems, empty when the statement agrees to the cent.

A statement is a dict with `statement_no`, `account_number`, `product`, `period_start` and `period_end` (dates), `opening` and `closing` (Decimal) and `transactions`. Each transaction has `date`, `description`, `desc_source` (use `"text"`), `amount` (Decimal, money in positive, money out negative), `balance` (Decimal or None), `accrued_charge` (Decimal, usually 0) and `page`. Use Decimal everywhere, never float. Never copy a name, address, phone number or ID number from the statement header into the output. Keep account numbers and counterparty names, as the existing parsers do.

Then add the module to the `from . import ...` line in `Tools\finance_tools\importer.py` and register the bank in `BANKS` in the same file: a key, `mod`, `inbox` and `archive` folder names, a unique `stem`, and a `signature`, which is text found only on this bank's statements, lowercase with all whitespace removed. Add the bank to the setup folder list in `Tools\setup.py` (`INBOX_BANKS`) and add the key to `owners` in `Config\household.json`. The importer also refuses any statement whose account number is not listed under that bank in `accounts` in the same file, so ask the user before writing the account number there. This is the one exception to the privacy rule. The master builder and the dashboard read the registration in `BANKS`, so a current or savings account needs nothing more. A loan needs its own dashboard panel and is outside this skill: tell the user so. Then re-run `Tools\setup.py`, which creates the new `Inbox` folder from `INBOX_BANKS`. It never overwrites a file.

## 3. Build and prove

Write the parser against the first statement, then run the contract test on every scratch statement:

`<venv python> Tools\check_parser.py <name> <statement.pdf> --bank "<Bank Key>"`

Every line must pass on every good statement. A statement that does not reconcile must be refused, never forced. The decisive checks are that opening plus transactions equals closing and, where the statement prints a running balance, that it agrees on every row. If a statement does not reconcile, find the cause in the parser. Never loosen `reconcile`, and never adjust an amount to force a match. Then run `Tools\process_inbox.py --dry-run` with one statement in the Inbox, then a real import, then `Tools\report.py`. Count the rows per bank in `Data\master\transactions.csv` (the `bank` column) before the first real import and again after it. Every existing bank must show the same count. In a household with no other bank this proves nothing, so say so. After the first statement imports cleanly, tell the user to place the remaining proven months in the Inbox and import them the same way.

If after honest effort a statement cannot reconcile, stop and tell the user what is unreadable. A bank that cannot be proven is not added.

## 4. Offer the contribution

When every scratch statement passes, write a synthetic fixture for the parser under `Tools\tests`: copy `synthetic_statements.py` as the model, make it write statements in the bank's layout with invented names, account number and amounts, and add a unit test that runs the parser on its output and checks that a good statement reconciles and a bad one does not. Never copy a real statement or any text from one. Then follow the skill `finance-contribute`. Offering is automatic. Sending is never automatic.
