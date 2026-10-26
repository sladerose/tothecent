---
name: finance-contribute
description: Offer to contribute a finished parser or tool improvement back to the shared project. Use after a new bank parser passes its contract test, or when the user has built a feature or fix that other households would want.
---

# Contribute a change back

A contribution is a pull request: a proposal to add the change to the shared project, which the maintainer reviews and accepts or declines. Nothing is sent without an explicit yes from the user, each time. If the user says no, record that in `CLAUDE.local.md`, do not ask again for the same change, and carry on.

## 1. Decide whether it is worth offering

Offer only when the change is general, and when its proof passes: a new parser must pass `Tools\check_parser.py` on every statement it was built from. A feature or fix needs its own test, and the existing proof must be unchanged: run `Tools\report.py` and `Tools\check_sources.py` before and after and compare. A change that exists only for this household is not offered.

## 2. Check what would be sent

List the exact files the contribution would add or change. Run:

`<venv python> Tools\check_contribution.py <each file>`

Any refusal stops the process. Remove the household value or the file, then run it again. The check searches for this household's own names, account numbers, meter numbers and rule text. It cannot recognise every private detail, so the next step matters.

## 3. Show everything

Fixtures must be synthetic: invented names, invented account numbers, invented amounts that still reconcile. `Tools\tests\synthetic_statements.py` writes text PDFs with no extra package and is the model to copy. Never include a real statement, a page of one, or text copied from one, including a layout sample with real merchants. Show the user the full diff and the full pull request text, and say plainly what leaves the machine. Ask for an explicit yes. The pull request text states what the change does, how it was proven, and which banks or accounts it affects.

## 4. Send (switched off until the project has a public repository)

Sending needs the GitHub CLI signed in as the user and a project repository address recorded in `Tools\project.json`. While that file does not exist, do not send. Tell the user the change is saved locally and can be sent later. When it exists and the user has said yes: fork the repository, create a branch named for the change, commit only the checked files, push, and open the pull request with `gh pr create` using the text the user approved. Then tell the user the pull request address.
