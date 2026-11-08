#!/usr/bin/env python3
"""Keep one open issue describing the sources that failed, and close it when none do."""

import json
import subprocess
import sys

TITLE = "Sources du catalogue en échec"


def gh(*args, stdin=None):
    return subprocess.run(
        ["gh", *args], input=stdin, text=True, capture_output=True, check=True
    ).stdout


def body_of(items):
    return "\n".join(
        f"- `{item['id']}` : {item['error']}" if "error" in item
        else f"- `{item['family']} {item['version']}` : {item['where']}"
        for item in items
    )


def main():
    failures = json.load(open(sys.argv[1], encoding="utf-8"))
    title = sys.argv[2] if len(sys.argv) > 2 else TITLE
    found = json.loads(
        gh("issue", "list", "--state", "open", "--search", f'in:title "{title}"', "--json", "number,title")
    )
    issue = next((item["number"] for item in found if item["title"] == title), None)
    if failures and issue:
        gh("issue", "edit", str(issue), "--body-file", "-", stdin=body_of(failures))
    elif failures:
        gh("issue", "create", "--title", title, "--body-file", "-", stdin=body_of(failures))
    elif issue:
        gh("issue", "close", str(issue))


if __name__ == "__main__":
    main()
