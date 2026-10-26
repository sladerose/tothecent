---
name: finance-onboard
description: Set up this finance folder for a new household and walk the user through every gap and uncategorised transaction. Use on the first session, when Config\household.json does not exist, or when the user asks to onboard, set up, add a bank or start again.
---

# Onboard a household

You prepare the folder, learn the household, load the first statements and then walk the user through what is missing. Code holds every exact rule. You hold the conversation. If the importer refuses something, report the refusal and its reason. Never work around it.

Ask one question at a time, say in a few words why you ask it, and write each answer into `CLAUDE.local.md` or `Knowledge` before you move on. Mark each fact confirmed (the user said it) or observed (you inferred it from the data). Never write ID numbers or passwords into any file, and never write account numbers beyond the last four digits except the one case in section 3, after the user agrees.

## 1. Check the machine

Run `python Tools/setup.py` (on Windows `py Tools\setup.py`). It needs Python 3.10 or later. It creates the folders, a virtual environment outside this folder (`~/.finance-tools/venv`, or the folder named in `FINANCE_TOOLS_VENV`), installs the packages and copies the config and knowledge templates in. It never overwrites a file. Read its output. If it reports that Tesseract is missing, say so: scanned statements cannot be read until it is installed. If the home folder is not writable, pass `--venv <folder>` to name another location. Use the virtual environment Python for every later command.

## 2. Interview

Find out, in this order:
1. Who is in the household, and who owns each account.
2. Which banks and account types are in use. Supported: FNB (current account, Money Maximiser, savings pockets, credit card, home loan), Nedbank (current account, home loan), Discovery Bank, TymeBank. For any other bank, tell the user plainly that it needs a new parser and cannot be loaded today.
3. Whether the user can download PDF statements from each bank. The tool reads PDFs only.
4. Each property, whether it is rented out, and which bond belongs to it.
5. What to track for each rental property: expenses only, or income and profit as well.
6. Any money held for someone else, such as a tenant deposit.
7. What the user wants from the tool, for example a spending view, a rental profit view or a check that no statement is missing. Record the answer in `CLAUDE.local.md` and let it set the order of the walk in section 5.

## 3. Write the configuration

Copy `Config\household.example.json` to `Config\household.json` and edit it so that it holds only what the household uses:
- `owners` names each account in use with its owner. A bank with no entry is not in use and is ignored everywhere. Delete the entries for banks that are not used.
- `second_tyme_holder` is needed only when a second person holds a TymeBank account. Remove the key otherwise, together with the two `(Name)` owner entries.
- `loan_properties` maps each home loan in use to its property name. `holder_keywords` is needed only for TymeBank. `accounts` is optional: last known account numbers for the credit card and loans, which the importer needs for accounts it cannot read from the statement. Account numbers are the one exception to the privacy rule, because the importer needs them. Ask the user before writing any.
- `dashboard_notes` holds the three sentences shown on the dashboard. Leave them empty until the data supports them. `loan_notes` is optional, one sentence per loan.

Then fill `CLAUDE.local.md` from the interview, and `Knowledge\household.md` and `Knowledge\properties-and-loans.md`. Set the dashboard targets in `Config\targets.csv` with the user (savings rate, monthly spending ceiling, uncategorised share, runway months). Never invent a target. A blank value is allowed.

## 4. First import

Ask the user to place statement PDFs in `Inbox\<Bank>`, as many months as they have. Run `Tools\process_inbox.py --dry-run` and tell the user, for each file, the bank, the period and whether it is ready, a duplicate or failed, with the reason. Do not force a failed file in. Then run `Tools\process_inbox.py` for real. A statement that does not reconcile is refused. That is the design.

## 5. Walk the gaps

Run `Tools\report.py`. It changes nothing and lists, in order: run state, files left in the Inbox, statements that did not reconcile, gaps and balance breaks between statements, stale banks and the largest uncategorised merchants. Take each section with the user, one item at a time:
- A gap or stale bank: name the missing period and ask the user to fetch that statement.
- A failed file: explain the reason in one or two sentences and ask how to proceed.
- Uncategorised merchants: propose a category and, for a rental, a property, for each of the largest first. Add the confirmed ones to `Config\category_rules.csv` (columns `priority,pattern,category,scope,direction,bank,property,note`) and one off corrections to `Config\row_overrides.csv`. Then run `Tools\process_inbox.py --rebuild-only` and run the report again so the user sees the count fall.

Stop when the user is satisfied or the report shows nothing needing attention. Tell the user the dashboard is at `Dashboard\household-dashboard.html` and opens by double click. State what was not done and what is still open, and record the open items in `Knowledge\open-questions.md`.

## 6. A bank the tool does not read

If the user banks somewhere that is not supported, say so early, in the interview, and again here. Follow the skill `finance-new-bank`. State plainly that a parser is built from the user's own statements on this computer, that it takes effort and that success cannot be promised. Until a parser exists, that bank stays out of `owners` and out of the dashboard, and the open item goes into `Knowledge\open-questions.md`. If that bank is the only one the household uses, `owners` is empty and import and report will refuse to run ("owners names no bank in use"). That is expected. Build the parser first, register it, and add it to `owners` as part of `finance-new-bank`.
