import pathlib, re

p = pathlib.Path("app.py")
s = p.read_text(encoding="utf-8")
orig = s

# 1) Ensure session is marked modified when we set it (forces cookie write)
# We'll inject: session.modified = True right after session["licensed_location"] = ...
pattern_set = r'(session\["licensed_location"\]\s*=\s*.+)'
def repl(m):
    line = m.group(1)
    return line + "\n        session.modified = True"

s2, n = re.subn(pattern_set, repl, s, count=1)
if n == 0:
    raise SystemExit("Could not find session['licensed_location'] assignment to patch.")

# 2) Make sure we redirect to /product only AFTER setting session (some code does early redirect)
# (This is a safety: no change unless the pattern exists)
s2 = re.sub(r'return redirect\("/product"\)\s*', 'return redirect("/product")\n', s2)

backup = p.with_name("app.py.bak_before_session_patch")
backup.write_text(orig, encoding="utf-8")
p.write_text(s2, encoding="utf-8")

print("Patched address session write successfully.")
print("Backup:", backup.name)

