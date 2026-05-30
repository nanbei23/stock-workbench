import ast
from pathlib import Path

SKIP_DIRS = {".git", "venv", "env", "__pycache__", "build", "dist"}
files = sorted(
    str(path)
    for path in Path(".").rglob("*.py")
    if not any(part in SKIP_DIRS or part.startswith(".venv") for part in path.parts)
)
failed = False
checked = 0
for f in files:
    try:
        with open(f, "r", encoding="utf-8") as src:
            ast.parse(src.read(), filename=f)
        checked += 1
    except SyntaxError as e:
        failed = True
        print(f'FAIL {f}: {e}')

if failed:
    raise SystemExit(1)

print(f"OK syntax: {checked} Python files")
