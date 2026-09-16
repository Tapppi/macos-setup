#!/usr/bin/env python3
"""
regenerate.py — rewrite the generated fixtures from the code that defines them.

	python3 contract/regenerate.py

Two of the five fixtures are generated (`contract.json`, `expected_validation.json`)
and two are hand-written (`ordering.json`, `comparator.json`). `test_items.py`
asserts every one of them still agrees with the code, so a fixture cannot go
stale — which is the only reason a published fixture is worth more than a
paragraph.

Run this after an intentional contract change, then READ the diff. A diff in
`expected_validation.json` that you did not intend is the fixture doing its job.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import apply_converge  # noqa: E402
import converge  # noqa: E402
import items as model  # noqa: E402
import validate_items  # noqa: E402


def write(name, document):
	path = os.path.join(HERE, name)
	with open(path, "w", encoding="utf-8") as fh:
		json.dump(document, fh, ensure_ascii=False, indent="\t", sort_keys=False)
		fh.write("\n")
	print(path)


def main():
	write("contract.json", model.contract())
	session, roots, unconfigured = validate_items.fixture_session()
	validation = validate_items.validate_session(
		session, roots, manifest_root=roots[0], unconfigured_roots=unconfigured)
	write("expected_validation.json", validation)

	# WP3 — the convergence output contract, pinned the same way. The
	# submission `converge.json` is HAND-WRITTEN input, like `session/`; the
	# four below are generated from it and the fixture corpus.
	write("convergence.json", converge.contract())
	with open(os.path.join(session, "collect.json"), encoding="utf-8") as fh:
		collect = json.load(fh)
	with open(os.path.join(session, "watch-items.json"), encoding="utf-8") as fh:
		stores = {"watch_items": json.load(fh)}
	corpus_pre = converge.build_corpus_pre(validation, collect, stores)
	write("expected_converge_view.json", converge.build_view(corpus_pre))
	write("expected_converge_tables.json", converge.build_tables(corpus_pre))
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
	write("expected_converge_effect.json", {
		"corpus_pre_digest": converge.canonical_digest(corpus_pre),
		"corpus_post_digest": converge.canonical_digest(result["corpus_post"]),
		"effect": effect,
	})
	return 0


if __name__ == "__main__":
	sys.exit(main())
