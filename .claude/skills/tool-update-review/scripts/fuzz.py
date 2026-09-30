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
  5. every key of an item                    (items.ITEM_FIELDS, top level)
  6. every sub-key of `anchor`/`local`/`security`/`change`
                                             (items.ITEM_FIELDS, dotted)
  7. `watch_hit.*`, hostile AND grounded     (§2.5, I-20 — the stored topic
                                              itself is among the shapes, so
                                              the grounded branch runs)
  8. every key of an edit suggestion         (schemas.md §1.2)
  9. every key of a `structural` block       (item-schema.md §4)
 10. every memory-proposal payload field     (items.MEMORY_PAYLOAD_FIELDS)
 11. every key of `config_status`
 12. every checker flag and forbidden flag   (items.CHECKER_FLAGS +
                                              items.VALIDATOR_ONLY_FLAGS)
 13. the watch-items.json session snapshot, at all three depths
 14. a malformed member inside a brew_health/skill_drift findings block
 15. every key of an object-form evidence entry (G-SEC: `role`, `quote`)
 16. hostile DERIVED inputs, called directly — the pipeline never produces
     them: `accepts_baseline`, `security_tier`, `pre_accept_bars` and
     `converge.initial_pre_accept` over hostile `security_tier`,
     `pre_accept_bars`, `usage_evidence` and `bucket_inputs`, and
     `converge.check_corpus_versions` over hostile version keys

**Survival is not the gate; semantics are (G-SEC).** 1613/0
was the crash baseline. Every case's assembled report is now also read and
held to the tier's invariants (`semantic_violations`): the four bucket/tier
coherence rules, no held or P0 tool pre-accepted, no tool with an unreadable
tier-input enum or item container pre-accepted or in the auto strip, and
every `security_tier` null or valid. The fuzz corpus makes `brew:openssh` a
positively identified fix whose baseline starts accepted, so each hostile
shape is thrown at the ACCEPTING path — the one a hole would open.

Never narrow HOSTILE or a key list to make a case pass: an abort is a
finding to fix in assemble.py/validate_items.py, not in this file.
"""
import copy
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble  # noqa: E402
import converge  # noqa: E402
import items as model  # noqa: E402
import test_assemble as T  # noqa: E402

HOSTILE = [None, "a string", 42, True, [], {}, ["x"], [None], [42], [[1]],
	{"k": "v"}, [{"k": ["v"]}], [{"id": []}], "", [""], {"a": {"b": {"c": 1}}},
	# Unpaired UTF-16 surrogates: valid JSON escapes that no UTF-8 write
	# accepts (items.scrub_unencodable). One in a value, one in a key.
	"a lone \ud800 surrogate", {"k\udfff": ["\ud800"]}]

# 3 — collect.json's top level (references/schemas.md §1.1).
COLLECT_KEYS = ["brew", "mise", "standalone", "macos", "brew_health", "skill_drift",
	"machine", "generated_at"]
# 2 — the retired schema plus a key from nobody's schema: the quarantine path.
UNKNOWN_KEYS = ["headliners", "relevancy", "context", "notable", "a_future_key"]
# 5–8, 13 — the item model's containers, DERIVED from items.ITEM_FIELDS (the
# published field table) so a widened item schema cannot be silently
# under-fuzzed. Includes the validator-assigned fields (id, id_stability):
# a checker is not supposed to write them, which is exactly why a hostile
# value there must cost nothing.
def _item_fields(prefix=None):
	# `local.evidence[].role`-style rows name a key of an ARRAY MEMBER, not of
	# the container; surface 15 fuzzes those on an evidence entry.
	names = [n for n, _type, _req, _note in model.ITEM_FIELDS if "[]" not in n]
	if prefix is None:
		return [n for n in names if "." not in n]
	return [n.split(".", 1)[1] for n in names if n.startswith(prefix + ".")]


ITEM_KEYS = _item_fields()
ANCHOR_KEYS = _item_fields("anchor")
LOCAL_KEYS = _item_fields("local")
SECURITY_KEYS = _item_fields("security")
CHANGE_KEYS = _item_fields("change")
WATCH_HIT_KEYS = _item_fields("watch_hit")
for _lst in (ITEM_KEYS, ANCHOR_KEYS, LOCAL_KEYS, SECURITY_KEYS, CHANGE_KEYS,
		WATCH_HIT_KEYS):
	assert _lst, "ITEM_FIELDS stopped exporting a fuzzed container"
# config_status has no exporting constant; spelled with its section.
CONFIG_STATUS_KEYS = ["state", "detail", "evidence", "citations"]
# 10 — an edit/upgrade suggestion's read surface (references/schemas.md §1.2),
# plus G-SEC's `requirement`/`serves` (items.SUGGESTION_FIELDS, less the
# validator-assigned `serves_item_ids`, which a checker writing must cost
# nothing either).
SUGGESTION_KEYS = ["id", "kind", "title", "target_files", "rationale",
	"motivating_link", "diff_preview", "command", "auto_runnable", "needs_sudo",
	"manual_reason", "target_version"] + [n for n, _t, _r, _note in model.SUGGESTION_FIELDS]
# 15 — an object-form evidence entry (items.ITEM_FIELDS' `local.evidence[].*`
# rows, plus the shape keys normalize_evidence reads).
EVIDENCE_KEYS = ["path", "lines", "note"] + [n.split("[].", 1)[1]
	for n, _t, _r, _note in model.ITEM_FIELDS if n.startswith("local.evidence[].")]
USAGE_ENTRY = {"path": "Brewfile", "role": "usage", "quote": "brew"}
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

# The fuzz corpus: test_assemble's, with `brew:openssh`'s one item made a
# positively identified fix that reaches with benefit and quotes a usage
# line — a G-SEC tool whose baseline STARTS ACCEPTED (P3), so every hostile
# shape below is thrown at the accepting path. `brew:ssh-copy-id` is a second,
# unmutated fix.
FUZZ_RESEARCH = copy.deepcopy(T.RESEARCH)
for _entry in FUZZ_RESEARCH:
	if _entry["id"] in ("brew:openssh", "brew:ssh-copy-id"):
		for _item in _entry["items"]:
			_item["security"]["nature"] = "fix"
	if _entry["id"] == "brew:openssh":
		_entry["items"][0]["local"]["effect"] = "benefit"
		_entry["items"][0]["local"]["evidence"] = [copy.deepcopy(USAGE_ENTRY)]

fails = []
cases = 0

# G-SEC's tier-input enums (their unreadable forms hold/bar as `enum-invalid`)
# and item/suggestion containers (`container-unreadable`), by finding field.
TIER_ENUM_FIELDS = frozenset({"severity", "local.direction", "local.effect",
	"security.nature", "local.evidence.role", "kind", "requirement",
	"config_status.state"})
CONTAINER_FIELDS = frozenset({"local", "security", "change", "watch_hit", "tags",
	"serves"})


def unreadable_tools(validation):
	"""Tool ids carrying E-ENUM-INVALID on a tier-input field, or E-FIELD-TYPE
	on a tier-input enum or on a CONTAINER itself (its message names the
	container — a wrong-typed tag MEMBER is not a wrong-typed container)."""
	out = set()
	for finding in (validation or {}).get("findings") or []:
		code, field = finding.get("code"), finding.get("field")
		if code == "E-ENUM-INVALID" and field in TIER_ENUM_FIELDS:
			out.add(finding.get("tool_id"))
		elif code == "E-FIELD-TYPE" and field in TIER_ENUM_FIELDS:
			out.add(finding.get("tool_id"))
		elif (code == "E-FIELD-TYPE" and field in CONTAINER_FIELDS
				and str(finding.get("message", "")).startswith(field + " is ")):
			out.add(finding.get("tool_id"))
	return out


def semantic_violations(report):
	"""The tier's invariants over one assembled report → [message]."""
	out = []
	unreadable = unreadable_tools(report.get("_validation"))
	for tool in report.get("tools") or []:
		tid = tool.get("id")
		tier = tool.get("security_tier")
		valid = model.valid_security_tier(tier)
		bucket = tool.get("review_bucket")
		baseline = assemble.baseline_upgrade(tool)
		accepted = bool(baseline and baseline.get("pre_accept"))
		if tier is not None and not valid:
			out.append("{}: security_tier is neither null nor valid: {!r}".format(tid, tier))
		if valid:
			if tier["tier"] in model.ACCEPTED_TIERS and bucket != "security_auto":
				out.append("{}: accepted tier {} but bucket {}".format(tid, tier["tier"], bucket))
			if tier["tier"] not in model.ACCEPTED_TIERS and bucket not in (
					"security_mixed", "attention"):
				out.append("{}: tier {} but bucket {}".format(tid, tier["tier"], bucket))
			if bucket == "attention" and "content-losing" not in tier["holds"]:
				out.append("{}: attention with a tier but no content-losing hold".format(tid))
			if (tier["holds"] or tier["priority"] == "P0") and accepted:
				out.append("{}: held/P0 tier {} but the baseline is pre-accepted".format(
					tid, tier["tier"]))
		elif bucket == "security_auto":
			inputs = tool.get("bucket_inputs") or {}
			if not (inputs.get("has_security") and inputs.get("security_only")
					and inputs.get("impact") == "none" and inputs.get("runnable")
					and inputs.get("version_delta") not in ("major", "unknown")
					and not tool.get("pre_accept_bars")):
				out.append("{}: security_auto with no tier but clause 2b's conjuncts "
					"do not all hold: {!r}".format(tid, inputs))
		if tid in unreadable:
			in_panel = valid and tier["priority"] in model.HIGHLIGHT_PRIORITIES
			if accepted:
				out.append("{}: an unreadable enum/container, yet pre-accepted".format(tid))
			if bucket == "security_auto" and not in_panel:
				out.append("{}: an unreadable enum/container, yet in the auto strip".format(tid))
	return out


def attempt(label, collect, research, session_files=None):
	global cases
	cases += 1
	try:
		report, _ = T.assemble_session(collect, research, session_files=session_files,
			with_validation=True)
	except SystemExit as exc:
		# A clean, message-bearing exit is the right answer for a collect.json
		# nothing can be salvaged from; only a traceback is a failure here.
		if not label.startswith("collect="):
			fails.append((label, "SystemExit({})".format(exc.code)))
		return
	except Exception:
		fails.append((label, traceback.format_exc().strip().splitlines()[-1]))
		return
	for problem in semantic_violations(report):
		fails.append((label, "SEMANTIC " + problem))


def research_with(entry_id, mutate):
	"""A deep copy of the fixture corpus with `mutate(entry)` applied to the
	named entry — deep, because most surfaces below sit inside containers the
	shallow original never had to reach."""
	entries = copy.deepcopy(FUZZ_RESEARCH)
	for e in entries:
		if e["id"] == entry_id:
			mutate(e)
	return entries


def run_key(label_fmt, entry_id, apply_shape, session_files=None, shapes=None):
	for shape in (HOSTILE if shapes is None else shapes):
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

	# 6 — the four sub-objects. The base item is well-formed, so the
	# container is a dict when the mutation reaches inside it.
	def set_sub_key(e, container, key, shape):
		for item in e["items"]:
			item.setdefault(container, {})[key] = shape
	for container, keys in (("anchor", ANCHOR_KEYS), ("local", LOCAL_KEYS),
			("security", SECURITY_KEYS), ("change", CHANGE_KEYS)):
		for key in keys:
			run_key("items[].{}.{}={{!r}}".format(container, key), "brew:openssh",
				lambda e, s, container=container, key=key:
					set_sub_key(e, container, key, s))

	# 7 — watch_hit sub-keys, with a snapshot present so grounding actually
	# runs — and not only hostile shapes: the stored topic itself (verbatim
	# and whitespace-padded, both of which .strip() grounds) drives
	# grounded_watch_hit()'s True branch, the positive path the
	# watch_hit_item_ids export and the 70-point highlight read. HOSTILE
	# alone never equals the stored topic, so without these the path is
	# fuzzed only in its negative branch.
	for key in WATCH_HIT_KEYS:
		run_key("items[].watch_hit." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: set_sub_key(e, "watch_hit", key, s),
			session_files={"watch-items.json": SNAPSHOT},
			shapes=HOSTILE + ["agent forwarding", " agent forwarding ",
				"agent forwarding\n"])

	# 8 — every key of the (edit-kind) suggestions the fixture carries.
	def set_sug_key(e, key, shape):
		for sug in e["suggestions"]:
			sug[key] = shape
	for key in SUGGESTION_KEYS:
		run_key("suggestions[]." + key + "={!r}", "brew:podman",
			lambda e, s, key=key: set_sug_key(e, key, s))

	# 9 — the structural block, on a synthesized structural suggestion.
	def set_struct_key(e, key, shape):
		sug = copy.deepcopy(STRUCTURAL_SUG)
		sug["structural"][key] = shape
		e["suggestions"] = [sug]
	for key in STRUCTURAL_KEYS:
		run_key("structural." + key + "={!r}", "brew:podman",
			lambda e, s, key=key: set_struct_key(e, key, s))

	# 10 — every memory payload field, derived from the model so a widened
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

	# 11 — config_status, added well-formed and then broken one key at a time.
	def set_config_key(e, key, shape):
		# A VALID base state since G-SEC validates it: every other key's cases
		# then run on a tool the state alone would not hold.
		e["config_status"] = {"state": "up_to_date", "detail": "d", "evidence": [],
			"citations": [], key: shape}
	for key in CONFIG_STATUS_KEYS:
		run_key("config_status." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: set_config_key(e, key, s))

	# 12 — the four flags a checker may emit and the five it may not.
	for key in model.CHECKER_FLAGS + model.VALIDATOR_ONLY_FLAGS:
		run_key("flags." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: e.__setitem__("flags", {key: s}))

	# 13 — the watch-items snapshot: the whole file, one tool's entry list,
	# and one entry's topic.
	for shape in HOSTILE:
		attempt("watch-items.json={!r}".format(shape), T.COLLECT, T.RESEARCH,
			session_files={"watch-items.json": shape})
		attempt("watch-items.json[tool]={!r}".format(shape), T.COLLECT, T.RESEARCH,
			session_files={"watch-items.json": {"brew:openssh": shape}})
		attempt("watch-items.json[tool][0].topic={!r}".format(shape), T.COLLECT,
			T.RESEARCH,
			session_files={"watch-items.json": {"brew:openssh": [{"topic": shape}]}})

	# 15 — every key of an object-form evidence entry, on the G-SEC item.
	def set_evidence_key(e, key, shape):
		for item in e["items"]:
			entry = dict(USAGE_ENTRY)
			entry[key] = shape
			item.setdefault("local", {})["evidence"] = [entry]
	for key in EVIDENCE_KEYS:
		run_key("items[].local.evidence[0]." + key + "={!r}", "brew:openssh",
			lambda e, s, key=key: set_evidence_key(e, key, s))

	# 14 — one malformed member INSIDE a findings block. A hostile value at
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

	hostile_derived_inputs()
	self_check()

	aborting = [f for f in fails if not f[1].startswith("SEMANTIC ")]
	semantic = [f for f in fails if f[1].startswith("SEMANTIC ")]
	print("cases run:", cases)
	print("aborting cases:", len(aborting))
	print("semantic violations:", len(semantic))
	for label, why in fails:
		print("  ABORT" if not why.startswith("SEMANTIC ") else "  VIOLATION", label, "->", why)
	return 1 if fails else 0


MISSING = object()


def hostile_derived_inputs():
	"""16 — the derived inputs the pipeline never produces, called directly.
	None may raise (except `accepts_baseline`'s documented KeyError for a
	missing key), and none may accept a malformed tier."""
	global cases
	base = {"id": "brew:x", "source": "brew", "pinned": False, "research_error": None,
		"validator_error": None, "quarantine": [], "spec_violations": [],
		"config_status": {"state": "up_to_date"}, "suggestions": [],
		"items": [{"id": "brew:x#cve:CVE-2026-1", "tags": ["security"],
			"severity": "notable", "change": {"citation": "a fix"},
			"security": {"nature": "fix"}}],
		"risk_level": "low", "initial_review_bucket": "security_auto",
		"bucket_inputs": {"runnable": True}, "usage_evidence": []}
	base["security_tier"] = model.security_tier(base)
	base["pre_accept_bars"] = model.pre_accept_bars(base)
	assert model.accepts_baseline(base), "the direct-input base must start accepted"
	for field in ("security_tier", "pre_accept_bars", "usage_evidence", "bucket_inputs"):
		for shape in HOSTILE + [MISSING]:
			cases += 1
			label = "direct {}={!r}".format(field, "<missing>" if shape is MISSING else shape)
			view = copy.deepcopy(base)
			if shape is MISSING:
				del view[field]
			else:
				view[field] = copy.deepcopy(shape)
			try:
				model.security_tier(view)
				model.pre_accept_bars(view)
				eligible = converge.initial_pre_accept(view)
				try:
					accepted = model.accepts_baseline(view)
				except KeyError:
					if not (shape is MISSING and field in ("security_tier", "pre_accept_bars")):
						raise
					accepted = False
			except Exception:
				fails.append((label, traceback.format_exc().strip().splitlines()[-1]))
				continue
			tier = view.get("security_tier")
			if tier is not None and not model.valid_security_tier(tier) and (accepted or eligible):
				fails.append((label, "SEMANTIC a malformed tier was accepted"))
			bars = view.get("pre_accept_bars", MISSING)
			if (bars is MISSING or not isinstance(bars, list) or bars) and (accepted or eligible):
				fails.append((label, "SEMANTIC hostile bars were accepted"))
	for key in ("contract_version", "converge_version"):
		for shape in HOSTILE + [MISSING]:
			cases += 1
			label = "direct corpus.{}={!r}".format(key, "<missing>" if shape is MISSING else shape)
			corpus = {"contract_version": model.CONTRACT_VERSION,
				"converge_version": converge.CONVERGE_VERSION}
			if shape is MISSING:
				del corpus[key]
			else:
				corpus[key] = shape
			try:
				converge.check_corpus_versions(corpus)
			except converge.CorpusVersionError:
				continue
			except Exception:
				fails.append((label, traceback.format_exc().strip().splitlines()[-1]))
				continue
			fails.append((label, "SEMANTIC a hostile corpus version was accepted"))


def self_check():
	"""The semantic checker must catch a planted violation — a pre-accepted
	tool with a held tier, built directly. Without this, deleting an
	assertion from `semantic_violations` would leave the fuzz green."""
	global cases
	cases += 1
	held = dict(model.TIER_UNCOMPUTED)
	planted = {"tools": [{"id": "brew:planted", "security_tier": held,
		"review_bucket": "security_mixed", "bucket_inputs": {},
		"suggestions": [{"id": "brew:planted:upgrade", "kind": "upgrade",
			"pre_accept": True}]}]}
	if not semantic_violations(planted):
		fails.append(("self-check", "SEMANTIC the checker missed a planted pre-accepted "
			"held tool"))


if __name__ == "__main__":
	sys.exit(main())
