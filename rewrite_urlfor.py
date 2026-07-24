import re, glob, os

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'backend', 'templates')
SKIP_PREFIXES = ('static', 'wordmaster')

# regex: url_for('NAME' or "NAME") with optional args
pattern = re.compile(r"url_for\(\s*([\"'])([a-zA-Z_][\w.]*)\1")

def repl(m):
    quote = m.group(1)
    name = m.group(2)
    if name.startswith(SKIP_PREFIXES):
        return m.group(0)
    return f"url_for({quote}wordmaster.{name}{quote}"

changed_files = []
for path in glob.glob(os.path.join(TEMPLATES_DIR, '*.html')):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content, n = pattern.subn(repl, content)
    if n:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        changed_files.append((os.path.basename(path), n))

for name, n in changed_files:
    print(f"rewrote {n:3d}  {name}")

print("DONE, files changed:", len(changed_files))
