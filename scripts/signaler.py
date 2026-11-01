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


def body_of(failures):
    return "\n".join(f"- `{item['id']}` : {item['error']}" for item in failures)


def main():
    failures = json.load(open(sys.argv[1], encoding="utf-8"))
    found = json.loads(
        gh("issue", "list", "--state", "open", "--search", f'in:title "{TITLE}"', "--json", "number,title")
    )
    issue = next((item["number"] for item in found if item["title"] == TITLE), None)
    if failures and issue:
        gh("issue", "edit", str(issue), "--body-file", "-", stdin=body_of(failures))
    elif failures:
        gh("issue", "create", "--title", TITLE, "--body-file", "-", stdin=body_of(failures))
    elif issue:
        gh("issue", "close", str(issue))


if __name__ == "__main__":
    main()
