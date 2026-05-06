#!/usr/bin/env python3
"""Robust AST-based fixer to indent docstring Expr nodes correctly.

This scans Python files under src/silisocs and for any FunctionDef/ClassDef
where the first body element is a string Expr (a docstring) that is
insufficiently indented, it indents the entire docstring block to the
expected body indentation (node.col_offset + 4).
"""

from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "silisocs"


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except Exception:
        return False

    lines = text.splitlines(True)
    changed = False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        # detect module/class/function docstring expressed as ast.Expr(Constant(str))
        if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) and isinstance(first.value.value, str):
            # determine current indentation and desired indentation
            try:
                doc_start = first.lineno - 1
                doc_end = first.end_lineno - 1
            except AttributeError:
                # If end_lineno is not available, fallback to simple heuristic
                # find closing triple quote by scanning forward
                doc_start = first.lineno - 1
                doc_end = doc_start
                while doc_end < len(lines) and '"""' not in lines[doc_end] and "'''" not in lines[doc_end]:
                    doc_end += 1
            current_col = first.col_offset
            desired_col = node.col_offset + 4
            if current_col < desired_col:
                add = desired_col - current_col
                spacer = ' ' * add
                for i in range(doc_start, min(doc_end + 1, len(lines))):
                    lines[i] = spacer + lines[i]
                changed = True

    if changed:
        path.write_text(''.join(lines), encoding='utf-8')
    return changed


def main():
    if not SRC.exists():
        print('no src/silisocs found')
        return
    touched = 0
    for p in sorted(SRC.rglob('*.py')):
        if p.name.startswith('test_'):
            continue
        try:
            if fix_file(p):
                print('Fixed:', p)
                touched += 1
        except Exception as e:
            print('Error', p, e)
    print('Touched', touched)


if __name__ == '__main__':
    main()
