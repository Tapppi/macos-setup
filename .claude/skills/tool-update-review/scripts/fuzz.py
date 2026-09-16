#!/usr/bin/env python3
"""
fuzz.py — hammer assemble.main() with hostile shapes at every input surface,
and report any that still abort the whole run.
Usage: python3 fuzz.py            (from anywhere; exit 1 if any case aborts)

This is the SPOF fuzzer behind the "0 aborting cases" acceptance baseline.
It was born outside the repo (scratch/spof/repro, Sep 7) and is vendored
here because a baseline the project cites must run from the tree it
describes — anything living outside it will evaporate.

One report is assembled from ~22 research files covering ~78 tools, so the
contract under test is the degradation doctrine: any one drifted key, member
or file costs one noted tool (or one section), NEVER the run. A case aborts
when assemble.main() raises out of `assemble_session` — for whole-collect
abuse a clean, message-bearing SystemExit is the right answer and does not
count.

Surfaces, from the outside in (key lists derive from `items.py` wherever a
constant exists, so this file cannot silently under-cover a widened set):

  1. every top-level research key            (items.RESEARCH_KEYS)
  2. retired/unknown research keys           (the E-RESEARCH-UNKNOWNKEY path)
  3. every top-level collect.json key
  4. the two whole documents
  5. every key of an item                    (item-schema.md §2)
  6. every key of `local`                    (§2.3)
  7. every key of `security`                 (§2.4)
  8. every key of `change`                   (§2.2)
  9. `watch_hit.topic`, grounded and not     (§2.5, I-20)
 10. every key of an edit suggestion         (schemas.md §1.2)
 11. every key of a `structural` block       (item-schema.md §4)
 12. every memory-proposal payload field     (items.MEMORY_PAYLOAD_FIELDS)
 13. every key of `config_status`
 14. every checker flag and forbidden flag   (items.CHECKER_FLAGS +
                                              items.VALIDATOR_ONLY_FLAGS)
 15. the watch-items.json session snapshot, at all three depths
 16. a malformed member inside a brew_health/skill_drift findings block

Never narrow HOSTILE or a key list to make a case pass: an abort is a
finding to fix in assemble.py/validate_items.py, not in this file.
"""
import copy
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items as model  # noqa: E402
import test_assemble as T  # noqa: E402

HOSTILE = [None, "a string", 42, True, [], {}, ["x"], [None], [42], [[1]],
	{"k": "v"}, [{"k": ["v"]}], [{"id": []}], "", [""], {"a": {"b": {"c": 1}}}]

# 3 — collect.json's top level (references/schemas.md §1.1).
COLLECT_KEYS = ["brew", "mise", "standalone", "macos", "brew_health", "skill_drift",
	"machine", "generated_at"]
# 2 — the retired schema plus a key from nobody's schema: the quarantine path.
UNKNOWN_KEYS = ["headliners", "relevancy", "context", "notable", "a_future_key"]
# 5–8, 13 — the item model's containers (references/item-schema.md §2, §2.2–2.4;
# no constant exports these, so they are spelled here with their sections).
ITEM_KEYS = ["anchor", "title", "body", "tags", "severity", "change", "local",
	"security", "watch_hit"]
LOCAL_KEYS = ["direction", "effect", "statement", "evidence", "citations"]
SECURITY_KEYS = ["cve_id", "advisory_id", "rating", "rating_basis", "exploited_in_wild"]
CHANGE_KEYS = ["version", "citation", "link_index"]
CONFIG_STATUS_KEYS = ["state", "detail", "evidence", "citations"]
# 10 — an edit/upgrade suggestion's read surface (references/schemas.md §1.2).
SUGGESTION_KEYS = ["id", "kind", "title", "target_files", "rationale",
	"motivating_link", "diff_preview", "command", "auto_runnable", "needs_sudo",
	"manual_reason", "target_version"]
# 11 — the structural outlet (references/item-schema.md §4).
STRUCTURAL_KEYS = ["op", "subjects", "manifest", "from", "to", "anchor"]

STRUCTURAL_SUG = {
	"id": "brew:podman:struct", "kind": "structural", "title": "Move it",
	"target_files": [], "rationale": "r", "motivating_link": None,
	"diff_preview": None,
	"structural": {"op": "manifest_remove", "manifest": "Brewfile",
		"subjects": [{"type": "formula", "name": "sops"}],
		"from": {"type": "formula", "name": "sops"}, "to": None, "anchor": None},
}
WATCH_SUG = {"id": "brew:podman:watch", "kind": "watch-item",
	"watch_topic": "krun support", "watch_note": "seen once", "rationale": "why"}

# 9, 15 — a snapshot that grounds one topic for the item-level watch_hit runs.
SNAPSHOT = {"brew:openssh": [{"topic": "agent forwarding"}]}

fails = []
cases = 0


def attempt(label, collect, research, session_files=None):
	global cases
	cases += 1
	try:
		T.assemble_session(collect, research, session_files=session_files)
	except SystemExit as exc:
		# A clean, message-bearing exit is the right answer for a collect.json
		# nothing can be salvaged from; only a traceback is a failure here.
		if not label.startswith("collect="):
			fails.append((label, "SystemExit({})".format(exc.code)))
	except Exception:
		fails.append((label, traceback.format_exc().strip().splitlines()[-1]))


def research_with(entry_id, mutate):
	"""A deep copy of the fixture corpus with `mutate(entry)` applied to the
	named entry — deep, because most surfaces below sit inside containers the
	shallow original never had to reach."""
	entries = copy.deepcopy(T.RESEARCH)
	for e in entries:
		if e["id"] == entry_id:
			mutate(e)
	return entries


def run_key(label_fmt, entry_id, apply_shape, session_files=None):
	for shape in HOSTILE:
		attempt(label_fmt.format(shape),
			T.COLLECT, research_with(entry_id, lambda e: apply_shape(e, shape)),
			session_files=session_files)


def main():
	# 1 — every recognized top-level research key, read or echoed.
	for key in model.RESEARCH_KEYS:
		run_key("research[brew:podman]." + key + "={!r}", "brew:podman",
			lambda e, s, key=key: e.__setitem__(key, s))
	# 2 — the unknown-key quarantine path.
	for key in UNKNOWN_KEYS:
		run_key("research[brew:podman]." + key + "={!r} (unknown key)", "brew:podman",
			lambda e, s, key=key: e.__setitem__(key, s))

	# 3 — collect.json's top level.
	for key in COLLECT_KEYS:
		for shape in HOSTILE:
			collect = dict(T.COLLECT)
			collect[key] = shape
			attempt("collect.{}={!r}".format(key, shape), collect, T.RESEARCH)

	# 4 — whole-shape abuse of the two top-level documents.
	for shape in [None, "x", 42, [], ["a"]]:
		attempt("collect={!r}".format(shape), shape, T.RESEARCH)
		attempt("research={!r}".format(shape), T.COLLECT, shape)

	# 5 — every key of an item, on the entry whose item carries every block.
	def set_item_key(e, key, shape):
		for item in e["items"]:
			item[key] = shape
	for key in ITEM_KEYS:
		run_key("items[]." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: set_item_key(e, key, s))

	# 6–8 — the three sub-objects. The base item is well-formed, so the
	# container is a dict when the mutation reaches inside it.
	def set_sub_key(e, container, key, shape):
		for item in e["items"]:
			item.setdefault(container, {})[key] = shape
	for container, keys in (("local", LOCAL_KEYS), ("security", SECURITY_KEYS),
			("change", CHANGE_KEYS)):
		for key in keys:
			run_key("items[].{}.{}={{!r}}".format(container, key), "brew:openssh",
				lambda e, s, container=container, key=key:
					set_sub_key(e, container, key, s))

	# 9 — watch_hit.topic, with a snapshot present so grounding actually runs.
	run_key("items[].watch_hit.topic={!r}", "brew:openssh",
		lambda e, s: set_sub_key(e, "watch_hit", "topic", s),
		session_files={"watch-items.json": SNAPSHOT})

	# 10 — every key of the (edit-kind) suggestions the fixture carries.
	def set_sug_key(e, key, shape):
		for sug in e["suggestions"]:
			sug[key] = shape
	for key in SUGGESTION_KEYS:
		run_key("suggestions[]." + key + "={!r}", "brew:podman",
			lambda e, s, key=key: set_sug_key(e, key, s))

	# 11 — the structural block, on a synthesized structural suggestion.
	def set_struct_key(e, key, shape):
		sug = copy.deepcopy(STRUCTURAL_SUG)
		sug["structural"][key] = shape
		e["suggestions"] = [sug]
	for key in STRUCTURAL_KEYS:
		run_key("structural." + key + "={!r}", "brew:podman",
			lambda e, s, key=key: set_struct_key(e, key, s))

	# 12 — every memory payload field, derived from the model so a widened
	# payload cannot be silently under-fuzzed, plus the self-test tag.
	for kind in model.MEMORY_SUGGESTION_KINDS:
		payload_keys = sorted(model.MEMORY_PAYLOAD_FIELDS[kind]) + ["self_test_failed"]
		for key in payload_keys:
			def set_memory_key(e, shape, kind=kind, key=key):
				sug = dict(WATCH_SUG, id="brew:podman:" + kind, kind=kind)
				sug[key] = shape
				e["suggestions"] = [sug]
			run_key("memory[{}].{}={{!r}}".format(kind, key), "brew:podman",
				set_memory_key)

	# 13 — config_status, added well-formed and then broken one key at a time.
	def set_config_key(e, key, shape):
		e["config_status"] = {"state": "ok", "detail": "d", "evidence": [],
			"citations": [], key: shape}
	for key in CONFIG_STATUS_KEYS:
		run_key("config_status." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: set_config_key(e, key, s))

	# 14 — the four flags a checker may emit and the five it may not.
	for key in model.CHECKER_FLAGS + model.VALIDATOR_ONLY_FLAGS:
		run_key("flags." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: e.__setitem__("flags", {key: s}))

	# 15 — the watch-items snapshot: the whole file, one tool's entry list,
	# and one entry's topic.
	for shape in HOSTILE:
		attempt("watch-items.json={!r}".format(shape), T.COLLECT, T.RESEARCH,
			session_files={"watch-items.json": shape})
		attempt("watch-items.json[tool]={!r}".format(shape), T.COLLECT, T.RESEARCH,
			session_files={"watch-items.json": {"brew:openssh": shape}})
		attempt("watch-items.json[tool][0].topic={!r}".format(shape), T.COLLECT,
			T.RESEARCH,
			session_files={"watch-items.json": {"brew:openssh": [{"topic": shape}]}})

	# 16 — one malformed member INSIDE a findings block. A hostile value at
	# the block level never reaches read_findings_block's entry guards (a
	# non-object block is ignored whole), so the member level is its own
	# surface — this is where a bare string used to become an AttributeError
	# out of main().
	for source_key in ("brew_health", "skill_drift"):
		for shape in HOSTILE:
			collect = dict(T.COLLECT)
			collect[source_key] = {"findings": [shape], "suppressed": [shape]}
			attempt("collect.{}.findings[0]={!r}".format(source_key, shape),
				collect, T.RESEARCH)

	print("cases run:", cases)
	print("aborting cases:", len(fails))
	for label, why in fails:
		print("  ABORT", label, "->", why)
	return 1 if fails else 0


if __name__ == "__main__":
	sys.exit(main())
