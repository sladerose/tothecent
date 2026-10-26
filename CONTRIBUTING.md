# Contributing

## What is welcome

A parser for a bank or account type the tool does not read, a fix to an existing parser, a rule or check that other households would want, or a new feature. Each must be general. A change that exists only for one household stays local.

## What a pull request must contain

1. The change, kept narrow. Every changed line traces to what the pull request says it does.
2. A parser: a registration in `BANKS`, an entry in `INBOX_BANKS` in `Tools/setup.py`, and a synthetic fixture under `Tools/tests/` that passes `Tools/check_parser.py`. A feature or fix: its own test.
3. A statement in the pull request text that the existing proof is unchanged: master rows and totals for the existing banks are identical before and after.
4. No private data. Fixtures use invented names, account numbers and amounts. No real statement, page or text copied from one. Run `Tools/check_contribution.py` on the files; it must report clean.

## How it is reviewed

The maintainer reads the diff, runs the contract test and the automatic checks, and accepts, asks for changes or declines. A change that alters existing results needs a reason stated and agreed before it merges. The automatic checks catch mistakes. They do not replace review.

## How the tool helps

When a parser or improvement passes its proof, the tool offers to contribute it, shows the user the full diff and the pull request text, and sends only after an explicit yes. The user may decline, and the tool does not ask again for the same change.
