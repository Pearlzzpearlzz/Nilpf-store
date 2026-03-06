import re
import sys
import pathlib

p = pathlib.Path("app.py")
s = p.read_text(encoding="utf-8")

original = s

def replace_products_block(text: str) -> str:
    m = re.search(r"(?m)^PRODUCTS\s*=\s*\{", text)
    if not m:
        raise RuntimeError("Could not find PRODUCTS = { block in app.py")

    start = m.start()
    brace_start = text.find("{", m.end()-1)
    if brace_start == -1:
        raise RuntimeError("Found PRODUCTS line but could not find opening '{'")

    depth = 0
    i = brace_start

    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    else:
        raise RuntimeError("Unbalanced braces while scanning PRODUCTS dict")
    new_block = """PRODUCTS = {
    "complete_bundle": {
        "name": "Complete NILPF Package",
        "price": 497,
        "zip": "Complete_NILPF_Package.zip"
    },
    "essential_forms": {
        "name": "Essential Forms",
        "price": 57,
        "zip": "Essential_Forms_Independent_Living_Universal.zip"
    },
    "core_docs": {
        "name": "Core Documents",
        "price": 149,
        "zip": "Core_Docs-NILPF_Store.zip"
    }
}"""

    return text[:start] + new_block + text[end:]

updated = replace_products_block(s)

backup = p.with_name("app.py.bak_products_fix")
backup.write_text(original, encoding="utf-8")

p.write_text(updated, encoding="utf-8")

print("PRODUCTS block replaced successfully.")
print("Backup created:", backup.name)
