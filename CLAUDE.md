# Household finance tool

This folder holds bank statements, the data extracted from them, a dashboard and the Python tools that build both. Household facts (people, accounts, properties, how money moves) are not in this file. They live in `CLAUDE.local.md` and in `Knowledge`. Read both at the start of every session.

## Design principle

Code holds every exact rule: parsing, reconciliation, duplicate and overlap checks, categorisation, transfer matching, backups and locks. Claude holds the conversation and the judgement: interviewing the user, filling in config, proposing category rules, explaining gaps. If a rule is hard, it is enforced in code first. Never work around a refusal from the importer. Report it, explain it, and let the user decide.

## First session

If `Config\household.json` does not exist, the folder is not set up yet. Follow the skill `finance-onboard` (`.claude\skills\finance-onboard`). It runs `Tools\setup.py`, interviews the user, writes `Config\household.json` so that it names only the banks in use, fills `CLAUDE.local.md` and `Knowledge`, runs the first import and then walks the user through every gap and uncategorised transaction in the output of `Tools\report.py`.

Supported statement sources are FNB (current account, Money Maximiser, savings pockets, credit card, home loan), Nedbank (current account, home loan), Discovery Bank and TymeBank. Another bank needs a new parser in `Tools\finance_tools`. Tell the user this early and follow the skill `finance-new-bank`. When a parser or an improvement passes its proof, follow the skill `finance-contribute`: offering a contribution to the shared project is automatic, sending one never is, and `Tools\check_contribution.py` must pass first.

## Standing instruction: keep the knowledge current

When the user tells you something about their finances, accounts, properties or how money moves, record it in `Knowledge` in the same session. Mark each fact as confirmed (stated by the user) or observed (inferred from the data). Put unresolved items in `Knowledge\open-questions.md` and decisions in `Knowledge\decisions-log.md`.

## Layout

- `Inbox`: drop box for new statement PDFs, one folder per bank. Accepted PDFs are filed in `_Archive` and removed from the Inbox. Failed files stay with the reason in the report.
- `_Archive`: the only copy of imported statements, read only. Never edit or delete.
- `Data\raw`: one transactions CSV and one statements CSV per bank, as parsed. `Data\master\transactions.csv`: the single source of truth, rebuilt from raw. `Data\last_run.md` and `Data\import_log.csv` record each run. `Data\_backup` keeps the last six pre-import copies.
- `Config`: `household.json` (owners, account numbers, loan properties, dashboard sentences), `category_rules.csv`, `pocket_map.csv`, `row_overrides.csv`, `targets.csv`, `assets.csv`, `loan_balances.csv`, `bills.csv`, `obligations.csv`, `statement_sources.csv` and others. Each file starts with a header row that names its columns.
- `Dashboard\household-dashboard.html`: self contained, no network, opens by double click.
- `Tools`: the code. `process_inbox.py` is the import command. `setup.py` prepares the folder. `report.py` lists what needs the user's attention and changes nothing.

## Routine

Drop statements in `Inbox\<Bank>`, then run `<venv python> Tools\process_inbox.py`. `--dry-run` checks the Inbox and changes nothing. `--rebuild-only` rebuilds the master and dashboard after a rules change. The importer identifies the bank from the PDF, rejects a file in the wrong folder, skips periods already imported, refuses any statement that does not reconcile, overlaps an imported one or belongs to an unknown account, then rebuilds everything.

## Rules

- Never edit `Data\raw`, `Data\master` or `Dashboard` by hand. They are rebuilt. Correct data through `Config\row_overrides.csv` and `Config\category_rules.csv`.
- Never guess. A statement that fails reconciliation is reported and not imported. Unknown merchants stay uncategorised and go on the review list.
- Transfers between the user's own accounts are not income or spending. Categories starting with `Transfer:` are excluded from totals.
- Do not copy header personal data (name, address, phone numbers, branch, VAT or ID numbers) into CSVs, dashboards or notes. Reduce a full card number to its last four digits.
- Passwords for locked statements come from environment variables (`TYMEBANK_PDF_PASSWORDS`, comma separated). Never store one in a file or repeat it in chat.
- Data stays local. Never publish the data, the dashboard or the knowledge files to a hosted service without asking.
- Only one import runs at a time. The importer takes `Data\.import.lock` and refuses if it exists. Delete a lock only if it is older than six hours and nothing is running. If the folder is synced between computers, let the sync finish before and after an import, and resolve any file named "conflict" before importing.
- The dashboard is desktop only.

## Writing style

Direct and plain. Give a recommendation, not a survey of options.
