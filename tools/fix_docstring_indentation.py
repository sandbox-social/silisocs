#!/usr/bin/env python3
"""Fix docstring indentation issues where a docstring is not indented
inside a function/class body.

This repairs cases where a function or class definition is immediately
followed by a triple-quoted string that is not indented to the body
level. The script indents the entire docstring block so it becomes a
properly nested statement.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "silisocs"


def fix_file(path: Path) -> bool:
    changed = False
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(True)
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            def_indent = len(line) - len(stripped)
            # next non-empty line
            j = i + 1
            # skip possible decorator lines or continued signature lines
            # find immediate next line after signature end (we assume signature end at next line)
            if j < len(lines):
                next_line = lines[j]
                nxt_stripped = next_line.lstrip()
                if nxt_stripped.startswith(('"""', "'''")):
                    doc_indent = len(next_line) - len(nxt_stripped)
                    if doc_indent <= def_indent:
                        # indent entire docstring block
                        quote = nxt_stripped[:3]
                        k = j
                        while k < len(lines):
                            if quote in lines[k]:
                                # find closing triple quote (may be same line)
                                # if closing on same line, indent only that line
                                pass
                            if k > j and quote in lines[k]:
                                break
                            k += 1
                        # now k is at line containing closing quote or end
                        add = (def_indent + 4) - doc_indent
                        if add > 0:
                            spacer = " " * add
                            for t in range(j, min(k + 1, len(lines))):
                                lines[t] = spacer + lines[t]
                            changed = True
                        i = k
        i += 1
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed


def main() -> None:
    if not SRC.exists():
        print("No src/silisocs found")
        return
    py_files = sorted(SRC.rglob("*.py"))
    touched = 0
    for p in py_files:
        if p.name.startswith("test_"):
            continue
        try:
            if fix_file(p):
                print(f"Fixed indentation in: {p}")
                touched += 1
        except Exception as e:
            print(f"Error processing {p}: {e}")
    print(f"Fixed files: {touched}")


if __name__ == "__main__":
    main()
