#!/usr/bin/env python3
"""Auto-insert minimal Sphinx-style docstrings into Python files.

This script walks `src/silisocs`, finds modules, classes and functions
that are missing a docstring, and inserts a generated Sphinx-style
reStructuredText docstring describing parameters and return values
based on available type annotations.

This is intentionally conservative: it only inserts docstrings where
none exist, and keeps wording generic so it is safe to run automatically.

Usage:
    python tools/add_sphinx_docstrings.py

The script modifies files in-place and prints a summary of changes.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "silisocs"


def sphinx_doc_for_function(node: ast.FunctionDef) -> str:
    parts: List[str] = []
    name = node.name
    parts.append(f"{name}.")
    args = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        arg_name = arg.arg
        ann = None
        if arg.annotation is not None:
            try:
                ann = ast.unparse(arg.annotation)
            except Exception:
                ann = None
        if ann:
            args.append((arg_name, ann))
        else:
            args.append((arg_name, None))

    if args:
        parts.append("")
        for arg_name, ann in args:
            if ann:
                parts.append(f":param {ann} {arg_name}:")
                parts.append(f":type {arg_name}: {ann}")
            else:
                parts.append(f":param {arg_name}:")

    # returns
    returns = None
    if getattr(node, "returns", None) is not None:
        try:
            returns = ast.unparse(node.returns)
        except Exception:
            returns = None

    if returns:
        parts.append("")
        parts.append(f":returns: {returns}")
        parts.append(f":rtype: {returns}")

    body = "\n".join(parts)
    return body


def sphinx_doc_for_class(node: ast.ClassDef) -> str:
    parts: List[str] = []
    parts.append(f"{node.name}.")
    # find init signature if present
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            init_doc = sphinx_doc_for_function(item)
            if init_doc:
                parts.append("")
                parts.append("Constructor parameters:\n")
                parts.append(init_doc)
            break
    return "\n".join(parts)


def format_docstring(content: str, indent: str = "") -> str:
    lines = content.splitlines() if content else []
    if not lines:
        return '"""No description available."""\n\n'
    # Build triple-quoted block with proper indentation
    out = [f'"""{lines[0]}']
    for line in lines[1:]:
        out.append(line)
    out.append('"""')
    # indent each line by indent
    return "\n".join((indent + l) if l else "" for l in out) + "\n\n"


def insert_docstrings_in_file(path: Path) -> Tuple[int, int, int]:
    """Insert docstrings into a single file.

    Returns a tuple (modules_added, classes_added, funcs_added).
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except Exception:
        return 0, 0, 0

    edits: List[Tuple[int, str]] = []  # (insert_after_lineno, text)

    # Module docstring
    if ast.get_docstring(tree) is None:
        mod_doc = f"{path.stem} module. Auto-generated module docstring."
        doc_block = format_docstring(mod_doc)
        # Insert at top before any code (after possible shebang and encoding)
        insert_at = 0
        lines = src.splitlines(True)
        # skip shebang and encoding comments
        for idx, line in enumerate(lines):
            if idx == 0 and line.startswith("#!"):
                continue
            if line.lstrip().startswith("#") and "encoding" in line:
                continue
            insert_at = idx
            break
        edits.append((insert_at, doc_block))

    class_count = 0
    func_count = 0

    # Walk classes and functions
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if ast.get_docstring(node) is None:
                # determine insertion line: before first body element
                if node.body:
                    insert_line = node.body[0].lineno - 1
                else:
                    insert_line = node.lineno
                indent = " " * node.col_offset
                content = sphinx_doc_for_class(node)
                doc_block = format_docstring(content, indent=indent)
                edits.append((insert_line, doc_block))
                class_count += 1
        elif isinstance(node, ast.FunctionDef):
            # skip methods (they are handled via ClassDef) to avoid double-insert
            if isinstance(getattr(node, "parent", None), ast.ClassDef):
                continue
            if ast.get_docstring(node) is None:
                if node.body:
                    insert_line = node.body[0].lineno - 1
                else:
                    insert_line = node.lineno
                indent = " " * node.col_offset
                content = sphinx_doc_for_function(node)
                doc_block = format_docstring(content, indent=indent)
                edits.append((insert_line, doc_block))
                func_count += 1

    if not edits:
        return 0, 0, 0

    # Apply edits in reverse order of insert positions
    lines = src.splitlines(True)
    for insert_pos, text in sorted(edits, key=lambda x: x[0], reverse=True):
        if insert_pos < 0:
            insert_pos = 0
        if insert_pos > len(lines):
            insert_pos = len(lines)
        lines.insert(insert_pos, text)

    new_src = "".join(lines)
    path.write_text(new_src, encoding="utf-8")
    return (1 if ast.get_docstring(tree) is None else 0), class_count, func_count


def add_parent_links(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            setattr(child, "parent", node)


def main() -> None:
    if not SRC_ROOT.exists():
        print("src/silisocs not found, aborting")
        return

    py_files = sorted(SRC_ROOT.rglob("*.py"))
    total_mods = total_classes = total_funcs = 0
    touched_files = 0
    for path in py_files:
        if path.name.startswith("test_"):
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except Exception:
            print(f"Skipping (parse error): {path}")
            continue
        add_parent_links(tree)
        mods, classes, funcs = insert_docstrings_in_file(path)
        if mods or classes or funcs:
            touched_files += 1
            total_mods += mods
            total_classes += classes
            total_funcs += funcs
            print(f"Updated {path}: modules={mods} classes={classes} funcs={funcs}")

    print("\nSummary:")
    print(f"  Files modified: {touched_files}")
    print(f"  Module docstrings added: {total_mods}")
    print(f"  Class docstrings added: {total_classes}")
    print(f"  Function docstrings added: {total_funcs}")


if __name__ == "__main__":
    main()
