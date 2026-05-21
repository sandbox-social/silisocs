"""Generate API reference pages for ProperDocs using mkdocs-gen-files.

This script is invoked by the `gen-files` MkDocs plugin at build time.
It walks the `src/silisocs` package, creates one Markdown file per module
under `docs/api/` containing a `mkdocstrings` directive (e.g. "::: silisocs.module").
It also emits `docs/api/index.md` with a literate nav of all generated pages.
"""

import os
import sys
from pathlib import Path

import mkdocs_gen_files

PACKAGE = "silisocs"
SRC_ROOT = Path("src") / PACKAGE


def iter_python_modules(root: Path):
    """Yield (path, rel) for .py files that live inside true Python packages.

    A file is considered part of the package tree only if `root/__init__.py`
    exists and every directory between `root` and the file's parent contains
    an `__init__.py` file as well. This filters out loose scripts or
    non-package directories.
    """
    # root must be a package
    root_init = root / "__init__.py"
    if not root_init.exists():
        return

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)

        # skip private modules and packages
        if any(part.startswith("_") for part in rel.parts):
            continue

        # ensure every ancestor directory is a package (has __init__.py)
        parent = rel.parent
        in_package = True
        # check each directory in the parent path
        if parent != Path("."):
            for i in range(len(parent.parts)):
                pkg_dir = root / Path(*parent.parts[: i + 1])
                if not (pkg_dir / "__init__.py").exists():
                    in_package = False
                    break

        if not in_package:
            continue

        yield path, rel


def main():
    if not SRC_ROOT.exists():
        return

    # ensure the package root itself is a package
    if not (SRC_ROOT / "__init__.py").exists():
        return

    nav = mkdocs_gen_files.Nav()

    # ensure src is on the import path once
    src_abspath = os.path.abspath("src")
    if src_abspath not in sys.path:
        sys.path.insert(0, src_abspath)

    for path, rel in iter_python_modules(SRC_ROOT):
        # skip test modules that aren't part of the public API
        if rel.name.startswith("test_"):
            continue

        # Determine the module parts and where to place the generated .md
        if rel.name == "__init__.py":
            # package-level module: map to the package path (e.g. a/b -> a/b.md)
            module_parts = list(rel.parent.parts)
        else:
            module_parts = list(rel.with_suffix("").parts)

        # module identifier for import (e.g. silisocs.foo.bar)
        if module_parts:
            module_identifier = PACKAGE + "." + ".".join(module_parts)
            doc_rel = Path(*module_parts).with_suffix(".md")
        else:
            # top-level package (src/silisocs/__init__.py)
            module_identifier = PACKAGE
            doc_rel = Path(PACKAGE + ".md")

        doc_path = Path("api") / doc_rel

        with mkdocs_gen_files.open(doc_path, "w") as fh:
            fh.write(f"::: {module_identifier}\n")

        # link the generated doc back to the source for "Edit on GitHub"
        mkdocs_gen_files.set_edit_path(doc_path, path)

        # add to navigation using module path parts as nested titles
        nav_key = tuple(module_parts) if module_parts else (PACKAGE,)
        nav[nav_key] = doc_rel.as_posix()

    # write index
    index_file = Path("api") / "index.md"
    with mkdocs_gen_files.open(index_file, "w") as fh:
        # The title that will appear in the breadcrumbs/tab
        fh.write("# API Reference\n\n")

        # This writes the actual navigation tree
        # MkDocs Literate Nav will consume this list for the sidebar
        fh.writelines(nav.build_literate_nav())


if __name__ == "__main__":
    main()
