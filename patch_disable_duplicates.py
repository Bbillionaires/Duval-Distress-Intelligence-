from pathlib import Path
import re

p = Path("app.py")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines(True)

def is_top_level(s): 
    return len(s) - len(s.lstrip(" ")) == 0 and not s.startswith("\t")

def is_app_decorator(s):
    s2 = s.strip()
    return s2.startswith("@app.") or s2.startswith("@bp.")  # safety

def is_def_line(s):
    return is_top_level(s) and s.startswith("def ")

def def_name(s):
    m = re.match(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", s.strip())
    return m.group(1) if m else None

seen = set()
out = []
i = 0
disabled = 0

while i < len(lines):
    line = lines[i]

    # Capture decorator stack (top-level)
    if is_top_level(line) and is_app_decorator(line):
        dec_start = i
        dec_block = [line]
        i += 1
        while i < len(lines) and is_top_level(lines[i]) and is_app_decorator(lines[i]):
            dec_block.append(lines[i])
            i += 1

        # Next must be def
        if i < len(lines) and is_def_line(lines[i]):
            name = def_name(lines[i]) or "UNKNOWN"
            # If we already saw this function name, disable the whole block (decorators + function body)
            if name in seen:
                disabled += 1
                out.extend(["# DISABLED_DUPLICATE_ENDPOINT " + x for x in dec_block])
                out.append("# DISABLED_DUPLICATE_ENDPOINT " + lines[i])
                i += 1
                # Disable indented body lines until next top-level def/decorator/if __name__
                while i < len(lines):
                    if is_top_level(lines[i]) and (is_app_decorator(lines[i]) or is_def_line(lines[i]) or lines[i].startswith("if __name__")):
                        break
                    out.append("# DISABLED_DUPLICATE_ENDPOINT " + lines[i])
                    i += 1
                continue
            else:
                seen.add(name)
                out.extend(dec_block)
                out.append(lines[i])
                i += 1
                continue
        else:
            # Decorators without def (rare) just pass through
            out.extend(dec_block)
            continue

    # Plain def without decorators: keep first, disable later duplicates (extra safety)
    if is_def_line(line):
        name = def_name(line)
        if name and name in seen:
            disabled += 1
            out.append("# DISABLED_DUPLICATE_DEF " + line)
            i += 1
            while i < len(lines):
                if is_top_level(lines[i]) and (is_app_decorator(lines[i]) or is_def_line(lines[i]) or lines[i].startswith("if __name__")):
                    break
                out.append("# DISABLED_DUPLICATE_DEF " + lines[i])
                i += 1
            continue
        if name:
            seen.add(name)

    out.append(line)
    i += 1

p.write_text("".join(out), encoding="utf-8")
print(f"OK: disabled {disabled} duplicate endpoint blocks")
