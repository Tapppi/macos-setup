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
	write("expected_validation.json", validate_items.validate_session(
		session, roots, manifest_root=roots[0], unconfigured_roots=unconfigured))
	return 0


if __name__ == "__main__":
	sys.exit(main())
