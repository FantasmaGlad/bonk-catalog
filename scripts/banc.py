#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from catalogue.banc import bench_errors, matrix, merge


def read_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def run_matrix(args):
    bench = read_json(args.bench, [])
    images = {image["id"] for image in read_json(args.images, [])}
    errors = list(bench_errors(bench, images))
    for error in errors:
        print(f"INVALID {error}", file=sys.stderr)
    if errors:
        return 1
    print(json.dumps(matrix(bench), separators=(",", ":")))
    return 0


def run_result(args):
    info = read_json(args.info, None) if args.info else None
    write_json(args.out, {"image": args.image, "firmware": args.firmware, "ok": args.ok == "true", "info": info})
    return 0


def run_proofs(args):
    results = [read_json(path, {}) for path in sorted(Path(args.results).glob("**/*.json"))]
    proofs, failures = merge(read_json(args.bench, []), results, read_json(args.previous, {}), args.run, args.date)
    write_json(args.out, proofs)
    write_json(args.failures, failures)
    return 0


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(required=True)
    command = commands.add_parser("matrice")
    command.add_argument("bench")
    command.add_argument("images")
    command.set_defaults(handler=run_matrix)
    command = commands.add_parser("resultat")
    command.add_argument("--image", required=True)
    command.add_argument("--firmware", required=True)
    command.add_argument("--ok", choices=["true", "false"], required=True)
    command.add_argument("--info")
    command.add_argument("--out", required=True)
    command.set_defaults(handler=run_result)
    command = commands.add_parser("preuves")
    command.add_argument("--bench", required=True)
    command.add_argument("--results", required=True)
    command.add_argument("--previous", required=True)
    command.add_argument("--run", required=True)
    command.add_argument("--date", required=True)
    command.add_argument("--out", required=True)
    command.add_argument("--failures", required=True)
    command.set_defaults(handler=run_proofs)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
