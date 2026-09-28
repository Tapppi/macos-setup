#!/usr/bin/env python3
"""
regenerate.py — rewrite the generated fixtures from the code that defines them.

	python3 contract/regenerate.py [--allow-dirty]

Two of the five fixtures are generated (`contract.json`, `expected_validation.json`)
and two are hand-written (`ordering.json`, `comparator.json`). `test_items.py`
asserts every one of them still agrees with the code, so a fixture cannot go
stale — which is the only reason a published fixture is worth more than a
paragraph.

Run this after an intentional contract change, then READ the diff. A diff in
`expected_validation.json` that you did not intend is the fixture doing its job.

**It refuses to run over uncommitted changes to the files it writes**
(`GENERATED`), and when it cannot ask git. It reads its inputs from the working
tree and rewrites all six outputs, so an agent running it sweeps any other
agent's in-progress edits to those files into its own diff — which happened
once, between two passes sharing one worktree. `--allow-dirty` says "those
uncommitted changes are mine": pass it only when you made them, e.g. when
re-running after your own previous regeneration.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import apply_converge  # noqa: E402
import converge  # noqa: E402
import items as model  # noqa: E402
import validate_items  # noqa: E402


# Every file this script writes. The concurrency guard checks exactly these.
GENERATED = ("contract.json", "expected_validation.json", "convergence.json",
	"expected_converge_view.json", "expected_converge_tables.json",
	"expected_converge_effect.json")


def dirty_outputs(here=HERE):
	"""→ the GENERATED files with uncommitted changes (staged, unstaged or
	untracked), or None when git cannot answer — which the caller treats as
	a refusal, never as clean."""
	try:
		proc = subprocess.run(
			["git", "-C", here, "status", "--porcelain", "--untracked-files=all",
				"--"] + list(GENERATED),
			capture_output=True, text=True, timeout=30)
	except (OSError, subprocess.SubprocessError):
		return None
	if proc.returncode != 0:
		return None
	dirty = set()
	for line in proc.stdout.splitlines():
		path = line[3:].strip().strip('"')
		if " -> " in path:
			path = path.split(" -> ", 1)[1]
		dirty.add(os.path.basename(path))
	return sorted(name for name in GENERATED if name in dirty)


def write(name, document, here=HERE):
	path = os.path.join(here, name)
	with open(path, "w", encoding="utf-8") as fh:
		json.dump(document, fh, ensure_ascii=False, indent="\t", sort_keys=False)
		fh.write("\n")
	print(path)


def main(argv=None, here=HERE):
	parser = argparse.ArgumentParser(description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--allow-dirty", action="store_true",
		help="regenerate over uncommitted changes to the generated files — only "
		"when those changes are your own")
	args = parser.parse_args(argv)
	if not args.allow_dirty:
		dirty = dirty_outputs(here)
		if dirty is None:
			print("Error: cannot ask git whether the generated fixtures have "
				"uncommitted changes — refusing to overwrite them. Pass "
				"--allow-dirty if you know they are yours.", file=sys.stderr)
			return 2
		if dirty:
			print("Error: uncommitted changes to {} — regenerating would sweep "
				"them into this run's output. Commit them, or pass --allow-dirty "
				"if they are your own.".format(", ".join(dirty)), file=sys.stderr)
			return 2

	outputs = {"contract.json": model.contract()}
	session, roots, unconfigured = validate_items.fixture_session()
	validation = validate_items.validate_session(
		session, roots, manifest_root=roots[0], unconfigured_roots=unconfigured)
	outputs["expected_validation.json"] = validation

	# WP3 — the convergence output contract, pinned the same way. The
	# submission `converge.json` is HAND-WRITTEN input, like `session/`; the
	# four below are generated from it and the fixture corpus.
	outputs["convergence.json"] = converge.contract()
	with open(os.path.join(session, "collect.json"), encoding="utf-8") as fh:
		collect = json.load(fh)
	with open(os.path.join(session, "watch-items.json"), encoding="utf-8") as fh:
		stores = {"watch_items": json.load(fh)}
	corpus_pre = converge.build_corpus_pre(validation, collect, stores)
	outputs["expected_converge_view.json"] = converge.build_view(corpus_pre)
	outputs["expected_converge_tables.json"] = converge.build_tables(corpus_pre)
	with open(os.path.join(HERE, "converge.json"), encoding="utf-8") as fh:
		submission = json.load(fh)
	result = apply_converge.apply_converge(corpus_pre, submission, attempt=1,
		terminal=False)
	if result["state"] != "converged" or result["critical"]:
		print("contract/converge.json no longer converges against the fixture "
			"corpus — findings:", file=sys.stderr)
		for finding in result["critical"]:
			print("  {} {} {}".format(finding["code"], finding.get("edit_id"),
				finding["detail"]), file=sys.stderr)
		return 1
	effect = apply_converge.finalize_clean(corpus_pre, submission, result, 1, [])
	outputs["expected_converge_effect.json"] = {
		"corpus_pre_digest": converge.canonical_digest(corpus_pre),
		"corpus_post_digest": converge.canonical_digest(result["corpus_post"]),
		"effect": effect,
	}
	# Computed first, written last: a submission that stops converging used to
	# leave the first three fixtures rewritten and the rest stale.
	for name in GENERATED:
		write(name, outputs[name], here)
	return 0


if __name__ == "__main__":
	sys.exit(main())
