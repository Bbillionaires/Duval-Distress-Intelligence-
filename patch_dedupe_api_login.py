from pathlib import Path

p = Path("app.py")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines(True)

targets = [
    '@app.post("/api/login")',
    "@app.post('/api/login')",
]

# Find all occurrences
hits = [i for i,l in enumerate(lines) if any(t in l for t in targets)]
print("API_LOGIN_DECORATOR_COUNT=", len(hits))

if len(hits) <= 1:
    print("NO_DEDUPE_NEEDED")
    raise SystemExit(0)

# Keep first, disable the rest by commenting out their whole handler blocks
keep = hits[0]
disable_starts = set(hits[1:])

out = []
i = 0
disable_mode = False
while i < len(lines):
    line = lines[i]

    if i in disable_starts:
        disable_mode = True

    if disable_mode:
        # stop disabling BEFORE the next route decorator or main guard
        if line.startswith("@app.") and not any(t in line for t in targets):
            disable_mode = False
            continue  # re-process this line normally
        if line.startswith("if __name__"):
            disable_mode = False
            continue  # re-process normally

        out.append("# DISABLED_DUPLICATE_API_LOGIN " + line)
        i += 1
        continue

    out.append(line)
    i += 1

p.write_text("".join(out), encoding="utf-8")
print("DEDUPED_API_LOGIN_OK")
