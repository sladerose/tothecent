"""Write synthetic Test Bank statement PDFs (text, Helvetica). No real data.

usage: python synthetic_statements.py OUTDIR
Writes two statements that chain and reconcile, and one with a wrong closing balance on purpose.
"""
import os
import sys
from decimal import Decimal as D


def esc(s):
    b = chr(92)
    return s.replace(b, b + b).replace("(", b + "(").replace(")", b + ")")


def write_pdf(path, lines):
    body = ["BT", "/F1 10 Tf", "50 790 Td", "14 TL"]
    for ln in lines:
        body.append("(" + esc(ln) + ") Tj T*")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for o in offs:
        out += ("%010d 00000 n \n" % o).encode()
    out += b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(x).encode() + b"\n%%EOF\n"
    with open(path, "wb") as f:
        f.write(out)


def statement(path, no, start, end, opening, rows, closing_override=None):
    bal = D(opening)
    lines = ["TEST BANK (synthetic, for trials only)", "Cheque Account Statement",
             "Account number: TB-000111", f"Statement number: {no}", f"Statement period: {start} to {end}",
             f"Opening balance: {bal:.2f}", "", "Date | Description | Amount | Balance"]
    for d, desc, amt in rows:
        bal += D(amt)
        lines.append(f"{d} | {desc} | {D(amt):.2f} | {bal:.2f}")
    closing = D(closing_override) if closing_override is not None else bal
    lines += ["", f"Closing balance: {closing:.2f}"]
    write_pdf(path, lines)
    return bal


def write_all(out):
    os.makedirs(out, exist_ok=True)
    b = statement(os.path.join(out, "testbank_2026-01.pdf"), 1, "2026-01-01", "2026-01-31", "1000.00",
                  [("2026-01-05", "Salary Example Employer", "5000.00"), ("2026-01-09", "Grocer One", "-850.40"),
                   ("2026-01-15", "Fuel Station", "-620.00"), ("2026-01-28", "Electricity Prepaid", "-300.00")])
    b = statement(os.path.join(out, "testbank_2026-02.pdf"), 2, "2026-02-01", "2026-02-28", b,
                  [("2026-02-05", "Salary Example Employer", "5000.00"), ("2026-02-11", "Grocer One", "-910.25"),
                   ("2026-02-19", "Pharmacy", "-145.60")])
    statement(os.path.join(out, "testbank_2026-03_BAD.pdf"), 3, "2026-03-01", "2026-03-31", b,
              [("2026-03-05", "Salary Example Employer", "5000.00"), ("2026-03-12", "Grocer One", "-700.00")],
              closing_override="9999.99")
    return sorted(os.listdir(out))


if __name__ == "__main__":
    print("written", write_all(sys.argv[1]))
