"""The synthetic statement generator writes readable PDFs whose figures chain, and one that does not reconcile."""
import os
import re
import sys
import tempfile
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import synthetic_statements as ss

MONEY = r"(-?\d+\.\d\d)"


def read_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


class SyntheticStatements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.names = ss.write_all(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def figures(self, name):
        text = read_text(os.path.join(self.tmp.name, name))
        opening = D(re.search(r"Opening balance: " + MONEY, text).group(1))
        closing = D(re.search(r"Closing balance: " + MONEY, text).group(1))
        amounts = [D(a) for a, _ in re.findall(r"^\d{4}-\d\d-\d\d \| .+ \| " + MONEY + r" \| " + MONEY + "$", text, re.M)]
        return opening, closing, amounts

    def test_three_files_are_written(self):
        self.assertEqual(self.names, ["testbank_2026-01.pdf", "testbank_2026-02.pdf", "testbank_2026-03_BAD.pdf"])

    def test_good_statements_reconcile_and_chain(self):
        o1, c1, a1 = self.figures("testbank_2026-01.pdf")
        o2, c2, a2 = self.figures("testbank_2026-02.pdf")
        self.assertEqual(o1 + sum(a1), c1)
        self.assertEqual(o2 + sum(a2), c2)
        self.assertEqual(c1, o2)

    def test_bad_statement_does_not_reconcile(self):
        o, c, a = self.figures("testbank_2026-03_BAD.pdf")
        self.assertNotEqual(o + sum(a), c)


if __name__ == "__main__":
    unittest.main()
