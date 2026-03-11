"""Stuffs all the human-readable files in a single markdown file. Maybe use it with an LLM 🤭.

Example:
    uv run python repo_to_md.py --tree -v --include .ipynb .py

    Will create a markdown file named mastodon_sim.md with all the .ipynb and .py files
    in the repo, and print the tree of processed files.
"""

import argparse
import mimetypes
import os
import subprocess
import sys
from typing import TextIO

import tiktoken
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

# Ensure .md files are recognized as text/markdown
mimetypes.add_type("text/markdown", ".md")


def is_human_readable(file_path: str) -> bool:
    """
    Check if a file is human-readable based on its MIME type or extension.

    Args:
        file_path (str): The path to the file to check.

    Returns
    -------
        bool: True if the file is human-readable, False otherwise.
    """
    _, ext = os.path.splitext(file_path)
    if ext.lower() in {".txt", ".py", ".ipynb", ".md"}:
        return True
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type is not None and mime_type.startswith("text")


def get_git_root() -> str:
    """
    Get the root directory of the git repository.

    Returns
    -------
        str: The path to the root of the git repository.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], universal_newlines=True
        ).strip()
    except subprocess.CalledProcessError:
        print("Error: Not a git repository. Please run this script from within a git repository.")
        sys.exit(1)


def get_gitignore_spec(repo_root: str) -> PathSpec:
    """
    Create a PathSpec object from the .gitignore file.

    Args:
        repo_root (str): The root directory of the git repository.

    Returns
    -------
        PathSpec: A PathSpec object representing the .gitignore rules.
    """
    gitignore_path = os.path.join(repo_root, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path) as gitignore_file:
            gitignore_content = gitignore_file.read()
        return PathSpec.from_lines(GitWildMatchPattern, gitignore_content.splitlines())
    return PathSpec([])


def get_human_readable_extensions(directory: str, gitignore_spec: PathSpec) -> set[str]:
    """
    Get all human-readable file extensions in the repository, respecting .gitignore.

    Args:
        directory (str): The directory to process.
        gitignore_spec (PathSpec): A PathSpec object representing the .gitignore rules.

    Returns
    -------
        Set[str]: Set of human-readable file extensions.
    """
    extensions = set()
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, directory)
            if not gitignore_spec.match_file(relative_path) and is_human_readable(file_path):
                _, ext = os.path.splitext(file)
                if ext:
                    extensions.add(ext.lower())
    return extensions


def build_file_tree(processed_files: list[str]) -> dict:
    """
    Build a tree structure of the processed files.

    Args:
        processed_files (List[str]): List of processed file paths.

    Returns
    -------
        Dict: A tree structure representing the processed files.
    """
    tree: dict = {}
    for file_path in processed_files:
        parts = file_path.split(os.sep)
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = None
    return tree


def print_file_tree(tree: dict, prefix: str = "") -> None:
    """
    Print the file tree structure.

    Args:
        tree (Dict): The tree structure to print.
        prefix (str): The prefix to use for indentation.
    """
    items = list(tree.items())
    for i, (name, subtree) in enumerate(items):
        if i == len(items) - 1:
            print(f"{prefix}└── {name}")
            if subtree is not None:
                print_file_tree(subtree, prefix + "    ")
        else:
            print(f"{prefix}├── {name}")
            if subtree is not None:
                print_file_tree(subtree, prefix + "│   ")


def process_directory(
    directory: str,
    output_file: TextIO,
    tokenizer: tiktoken.Encoding,
    allowed_extensions: set[str],
    gitignore_spec: PathSpec,
) -> tuple[int, set[str], list[str]]:
    """
    Recursively process a directory and write the content of human-readable files to the output file.

    Args:
        directory (str): The directory to process.
        output_file (TextIO): The file object to write the output to.
        tokenizer (tiktoken.Encoding): The tokenizer to use for counting tokens.
        allowed_extensions (Set[str]): Set of allowed file extensions.
        gitignore_spec (PathSpec): A PathSpec object representing the .gitignore rules.

    Returns
    -------
        Tuple[int, Set[str], List[str]]: Total number of tokens written, set of processed extensions, and list of processed files.
    """
    total_tokens = 0
    processed_extensions = set()
    processed_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, directory)
            _, ext = os.path.splitext(file)
            ext = ext.lower()

            # Skip files that are not in allowed_extensions or are ignored by .gitignore
            if ext not in allowed_extensions or gitignore_spec.match_file(relative_path):
                continue

            # Skip the output file itself and hidden files/directories
            if (
                is_human_readable(file_path)
                and os.path.basename(output_file.name) != file
                and not file.startswith(".")
                and not any(part.startswith(".") for part in relative_path.split(os.sep))
            ):
                with open(file_path, encoding="utf-8") as f:
                    content = f.read()

                # Write the file name and content to the output file
                header = f"## {relative_path}\n\n"
                footer = "\n```\n\n"
                full_content = header + "```\n" + content + footer
                output_file.write(full_content)

                # Count tokens
                tokens = tokenizer.encode(full_content)
                total_tokens += len(tokens)
                processed_extensions.add(ext)
                processed_files.append(relative_path)

    return total_tokens, processed_extensions, processed_files


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns
    -------
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Aggregate human-readable files from a git repository into a single markdown file."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="mastodon_sim.md",
        help="Output file name (default: mastodon_sim.md)",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        default=[
            ".py",
            ".ipynb",
        ],
        help="File extensions to include (default: .py)",
    )
    parser.add_argument(
        "--list-extensions",
        action="store_true",
        help="List all human-readable file extensions in the repository and exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--tree", action="store_true", help="Print the tree of processed files")
    return parser.parse_args()


def repo_to_md() -> None:
    """Aggregate all human-readable files in the repo into a single markdown file."""
    args = parse_arguments()

    # Get the root directory of the git repository
    repo_root = get_git_root()

    # Get the .gitignore rules
    gitignore_spec = get_gitignore_spec(repo_root)

    if args.list_extensions:
        extensions = get_human_readable_extensions(repo_root, gitignore_spec)
        print("Human-readable file extensions in the repository (respecting .gitignore):")
        for ext in sorted(extensions):
            print(ext)
        return

    output_path = os.path.join(repo_root, args.output)

    # Load the tokenizer
    tokenizer = tiktoken.get_encoding("cl100k_base")

    # Determine allowed extensions
    all_extensions = get_human_readable_extensions(repo_root, gitignore_spec)
    print(f"args.include: {args.include}")
    allowed_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in args.include
    }
    allowed_extensions &= all_extensions  # Only include extensions that actually exist in the repo

    if args.verbose:
        print(
            f"All detected extensions (respecting .gitignore): {', '.join(sorted(all_extensions))}"
        )
        print(f"Allowed extensions: {', '.join(sorted(allowed_extensions))}")

    # Open the output file and process the directory
    with open(output_path, "w", encoding="utf-8") as output_file:
        total_tokens, processed_extensions, processed_files = process_directory(
            repo_root, output_file, tokenizer, allowed_extensions, gitignore_spec
        )

    print(
        f"All human-readable files from the git repository have been aggregated into '{output_path}'."
    )
    print(f"Total tokens (tiktoken cl100k_base): {total_tokens}")
    print(f"Processed file types: {', '.join(sorted(processed_extensions))}")

    if args.verbose:
        print(
            f"Allowed but not processed: {', '.join(sorted(allowed_extensions - processed_extensions))}"
        )

    if args.tree:
        print("\nProcessed files tree:")
        file_tree = build_file_tree(processed_files)
        print_file_tree(file_tree)


if __name__ == "__main__":
    repo_to_md()
