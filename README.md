# tothecent

A local tool that reads your bank statement PDFs, refuses any statement that does not reconcile to the cent, sorts every transaction into a category, tracks each property separately and builds one dashboard you open by double click. Nothing leaves your computer. There is no account, no server and no upload.

It is built to be run by Claude Code. You do not need to know Python. Claude sets the folder up, asks you about your household, loads your statements and then walks you through whatever is missing or not yet categorised.

## What you need

- A computer running Windows. It has been tested on Windows only. macOS has not been tested.
- Python 3.10 or later.
- Claude Code.
- Tesseract OCR, only if a bank sends scanned statements.
- Statements as PDF files. The tool reads PDFs only.

## Banks supported today

FNB (current account, Money Maximiser, savings pockets, credit card, home loan), Nedbank (current account, home loan), Discovery Bank and TymeBank. Another bank needs a new parser written for it. Ask Claude to add your bank and it follows the skill `finance-new-bank`. A parser is built from your own statements, it takes effort and success cannot be promised. A bank is added only when its statements reconcile.

## Getting started

1. Clone this repository to where you want your finance records to live: `git clone https://github.com/sladerose/tothecent`.
2. Open the folder in Claude Code and say: set me up.
3. Answer the questions. Claude follows the skill `finance-onboard`.
4. Each month, drop new statement PDFs into the matching folder under `Inbox` and ask Claude to run the import.

The dashboard is written to `Dashboard\household-dashboard.html`.

## How it keeps the numbers right

Exact rules live in code, not in conversation. A statement is imported only if its opening balance, transactions and closing balance reconcile to the cent. Duplicates are skipped, gaps between statements are reported, and nothing in the raw data or the archive is ever edited by hand. Claude proposes categories. You confirm them.

## Your data

Your statements, configuration and dashboard stay in this folder and are excluded from version control by `.gitignore`. Keep the folder private. Your PDF passwords, where a bank locks its statements, are read from an environment variable and are never written to a file.

## Contributing

New bank parsers and fixes are welcome. Read `CONTRIBUTING.md`. A contribution must use synthetic test data only. Never send a real statement. When a parser is proven, Claude offers to prepare a pull request, shows you everything that would leave your computer and sends nothing without your explicit yes.

To run the tests: `python -m unittest discover -s Tools/tests -v`.

## Licence

MIT. See `LICENSE.txt`. The tool is provided as is, without warranty. It is a record keeping aid. It is not tax, legal or financial advice.
