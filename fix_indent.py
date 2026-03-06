import pathlib
import re

p = pathlib.Path("app.py")
text = p.read_text(encoding="utf-8")
original = text

# Remove orphan MASTER_LEASE block that sits outside PRODUCTS
pattern = r"""\},\s*
\s*"MASTER_LEASE"\s*:\s*\{.*?\}\s*
\}"""

text = re.sub(pattern, "}", text, flags=re.DOTALL)

backup = p.with_name("app.py.bak_before_indent_fix")
backup.write_text(original, encoding="utf-8")

p.write_text(text, encoding="utf-8")

print("Indent cleanup complete.")
print("Backup created:", backup.name)

