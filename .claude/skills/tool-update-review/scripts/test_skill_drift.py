#!/usr/bin/env python3
"""
test_skill_drift.py — the test matrix for collect_skill_drift.py.
Usage: python3 test_skill_drift.py [-v]

Stdlib `unittest` only (no pytest, no fixtures directory, NO NETWORK) so it
runs on the same bare python3 the detector itself targets, and so it can run
on a machine that is offline or behind a proxy. Groups, in the order the
detector uses them:

1. classify() — the three-way tree-hash truth table. This is the whole point
   of the source: a local patch must never be reported as "upstream moved",
   so every one of the five states gets a row.
2. Provenance parsers — sync-upstream.sh's vendor tables, marketplace.json's
   adopted set, and the two baseline-SHA lookups. All of them read files the
   detector does not own, so every one must degrade to empty rather than
   raise.
3. End-to-end — a synthetic dotfiles repo driven through main() with
   --no-network, asserting the contract shape (references/collection.md
   §Skill-Drift Collection) and that an unreachable upstream costs one quiet
   card per vendor rather than one per skill.

Where a test builds a real git repo it passes `-c commit.gpgsign=false`
alongside the identity: this machine SSH-signs commits through 1Password, and
an unconfigured `git commit` here would block on an interactive approval no
test runner can answer.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_skill_drift as drift  # noqa: E402


# ── git helpers — real repos, never the network, never a signing prompt ──────
GIT_FLAGS = [
	"-c", "user.email=test@example.com",
	"-c", "user.name=Test",
	"-c", "commit.gpgsign=false",
	"-c", "init.defaultBranch=master",
]


def git(repo, *args):
	"""Run git in `repo` with the identity/signing overrides; assert success."""
	p = subprocess.run(["git", "-C", repo] + GIT_FLAGS + list(args),
		capture_output=True, text=True, timeout=60)
	assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
	return p.stdout.strip()


def write(root, rel, text):
	path = os.path.join(root, rel)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8") as fh:
		fh.write(text)
	return path


# ── 1. classify() — the three-way truth table ────────────────────────────────
# (label, local, baseline, upstream, state). The state is keyed on the two
# comparisons (local-vs-baseline, baseline-vs-upstream), not on local-vs-
# upstream, so a local edit can never be mistaken for an upstream move.
CLASSIFY_MATRIX = [
	("pristine and current", "a", "a", "a", "in_sync"),
	("pristine, upstream moved", "a", "a", "b", "upstream_ahead"),
	("patched, upstream still at baseline", "b", "a", "a", "local_only"),
	("patched and upstream moved", "b", "a", "c", "diverged"),
	("patched to whatever upstream now is", "b", "a", "b", "diverged"),
	("local tree missing", None, "a", "a", "probe_error"),
	("baseline unresolvable", "a", None, "a", "probe_error"),
	("upstream unreachable", "a", "a", None, "probe_error"),
	("nothing resolved at all", None, None, None, "probe_error"),
]


class ClassifyTests(unittest.TestCase):
	def test_matrix(self):
		for label, local, baseline, upstream, state in CLASSIFY_MATRIX:
			with self.subTest(label, local=local, baseline=baseline, upstream=upstream):
				self.assertEqual(drift.classify(local, baseline, upstream), state, label)

	def test_every_state_is_covered(self):
		# A state the matrix never produces is a state nothing tests.
		self.assertEqual(
			{row[4] for row in CLASSIFY_MATRIX},
			{"in_sync", "upstream_ahead", "local_only", "diverged", "probe_error"})


# ── 2. Provenance parsers ────────────────────────────────────────────────────
SYNC_SCRIPT = '''#!/usr/bin/env bash
# sync-upstream.sh — Pull upstream subtrees and surface per-skill changes.

# Vendor table:
#   <prefix>|<upstream-url>|<branch>|<adopted-skills-relative-to-prefix>
vendors=(
\t"config/agent-skills/anthropics|https://github.com/anthropics/skills|main|skills/pdf skills/pptx"
\t"config/agent-skills/google|https://github.com/google/skills|main|skills/cloud/gke-basics"
)

#   <dest-prefix>|<upstream-url>|<branch>|<upstream-subpath>
sparse_vendors=(
\t"config/agent-skills/softaworks/jira|https://github.com/softaworks/agent-toolkit|main|skills/jira"
)

for entry in "${vendors[@]}"; do
\techo "not a table row"
done
'''


class VendorTableTests(unittest.TestCase):
	def test_both_tables_parse(self):
		vendors = drift.parse_vendor_tables(SYNC_SCRIPT)
		self.assertEqual([v["kind"] for v in vendors], ["subtree", "subtree", "sparse"])
		self.assertEqual(vendors[0], {
			"kind": "subtree",
			"prefix": "config/agent-skills/anthropics",
			"url": "https://github.com/anthropics/skills",
			"branch": "main",
			"skills": ["skills/pdf", "skills/pptx"],
		})
		self.assertEqual(vendors[2], {
			"kind": "sparse",
			"dest": "config/agent-skills/softaworks/jira",
			"url": "https://github.com/softaworks/agent-toolkit",
			"branch": "main",
			"subpath": "skills/jira",
		})

	def test_vendors_pattern_does_not_swallow_sparse_vendors(self):
		# `vendors=(` is a suffix of `sparse_vendors=(`; an unanchored match
		# would read the sparse table as subtree rows.
		only_sparse = 'sparse_vendors=(\n\t"a/b|https://u|main|skills/x"\n)\n'
		self.assertEqual([v["kind"] for v in drift.parse_vendor_tables(only_sparse)], ["sparse"])

	def test_malformed_rows_are_skipped_not_fatal(self):
		text = (
			'vendors=(\n'
			'\t"too|few|fields"\n'
			'\t# a comment row\n'
			'\t"config/agent-skills/ok|https://u|main|skills/a"\n'
			'\t\n'
			')\n')
		vendors = drift.parse_vendor_tables(text)
		self.assertEqual(len(vendors), 1)
		self.assertEqual(vendors[0]["prefix"], "config/agent-skills/ok")

	def test_absent_or_garbage_tables_yield_empty(self):
		for label, text in (
			("no tables at all", "#!/usr/bin/env bash\necho hi\n"),
			("empty string", ""),
			("truncated table", 'vendors=(\n\t"a|b|c|d"\n'),
			("not a shell script", "\x00\x01binary"),
		):
			with self.subTest(label):
				self.assertEqual(drift.parse_vendor_tables(text), [])

	def test_never_raises_on_non_string(self):
		self.assertEqual(drift.parse_vendor_tables(None), [])


MARKETPLACE = json.dumps({
	"name": "tapppi-skills",
	"plugins": [
		{"name": "find-skills", "source": {
			"source": "git-subdir",
			"url": "https://github.com/vercel-labs/skills.git",
			"path": "skills/find-skills", "ref": "main"}},
		{"name": "browser", "source": "./tapppi/browser"},
		{"name": "pptx", "source": "./anthropics/skills/pptx"},
		{"name": "jira", "source": "./softaworks/jira"},
		{"name": "no-source-at-all"},
	],
})


class MarketplaceTests(unittest.TestCase):
	def test_string_sources_are_adopted(self):
		adopted = drift.parse_marketplace_adopted(MARKETPLACE)
		self.assertEqual(
			[(a["name"], a["rel_path"]) for a in adopted],
			[("browser", "tapppi/browser"),
			 ("pptx", "anthropics/skills/pptx"),
			 ("jira", "softaworks/jira")])

	def test_object_source_is_excluded_as_tool_owned(self):
		# find-skills resolves itself from a git URL at plugin-install time —
		# nothing is vendored here, so drift against "our copy" is meaningless
		# (CLAUDE.md: tool-owned config is re-asserted, not vendored). This is
		# a correctness rule, not tidiness: including it would emit a finding
		# about a path that does not exist in the repo.
		names = [a["name"] for a in drift.parse_marketplace_adopted(MARKETPLACE)]
		self.assertNotIn("find-skills", names)

	def test_malformed_input_yields_empty(self):
		for label, text in (
			("not json", "{ not json"),
			("empty", ""),
			("json but not an object", "[1, 2, 3]"),
			("plugins is not a list", '{"plugins": {"a": 1}}'),
			("none", None),
		):
			with self.subTest(label):
				self.assertEqual(drift.parse_marketplace_adopted(text), [])


class SparseBaselineTests(unittest.TestCase):
	def test_present(self):
		text = ("# softaworks\n\n## Local patches\n\n"
			"- Last synced commit: `3027f20f3181758385a1bb8c022d4041dfb4de84`\n")
		self.assertEqual(drift.sparse_baseline_sha(text),
			"3027f20f3181758385a1bb8c022d4041dfb4de84")

	def test_absent_or_malformed(self):
		for label, text in (
			("no such line", "# softaworks\n\nNothing recorded.\n"),
			("too short to be a sha", "- Last synced commit: `3027f20`\n"),
			("not hex", "- Last synced commit: `zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz`\n"),
			("empty", ""),
			("none", None),
		):
			with self.subTest(label):
				self.assertIsNone(drift.sparse_baseline_sha(text))


# ── 3. Baseline lookup against a real repository ─────────────────────────────
def _init_repo(path, branch="main"):
	os.makedirs(path, exist_ok=True)
	subprocess.run(["git", "init", "-q", "-b", branch, path],
		capture_output=True, text=True, timeout=60, check=True)
	return path


def _commit_all(repo, message):
	git(repo, "add", "-A")
	git(repo, "commit", "-q", "-m", message)
	return git(repo, "rev-parse", "HEAD")


def _squash_commit(dotfiles, upstream_repo, upstream_branch, prefix, split_sha):
	"""Reproduce what `git subtree add/pull --squash` leaves behind: a
	parentless commit whose tree is the pristine upstream content, carrying
	the `git-subtree-dir`/`git-subtree-split` trailers, merged in with `-s
	ours` so it is reachable without disturbing the working tree."""
	git(dotfiles, "fetch", "-q", upstream_repo, upstream_branch)
	tree = git(dotfiles, "rev-parse", "FETCH_HEAD^{tree}")
	message = (f"Squashed '{prefix}/' content from commit {split_sha[:7]}\n\n"
		f"git-subtree-dir: {prefix}\n"
		f"git-subtree-split: {split_sha}\n")
	squash = git(dotfiles, "commit-tree", tree, "-m", message)
	git(dotfiles, "merge", "-q", "-s", "ours", "--allow-unrelated-histories",
		"-m", f"Merge squash of {prefix}", squash)
	return squash


class SubtreeBaselineTests(unittest.TestCase):
	def test_reads_the_trailers_off_a_real_squash_commit(self):
		with tempfile.TemporaryDirectory() as tmp:
			up = _init_repo(os.path.join(tmp, "up"))
			write(up, "skills/pptx/SKILL.md", "pptx v1\n")
			split = _commit_all(up, "upstream v1")
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			squash = _squash_commit(dot, up, "main", "config/agent-skills/anthropics", split)

			self.assertEqual(drift.subtree_baseline_sha(dot, "config/agent-skills/anthropics"),
				(squash, split))
			# The squash commit's own tree is the pristine upstream content —
			# this is what makes BASELINE resolvable with no network.
			self.assertEqual(
				git(dot, "rev-parse", squash + ":skills/pptx"),
				git(up, "rev-parse", split + ":skills/pptx"))

	def test_picks_the_most_recent_of_two_syncs(self):
		with tempfile.TemporaryDirectory() as tmp:
			up = _init_repo(os.path.join(tmp, "up"))
			write(up, "skills/pptx/SKILL.md", "pptx v1\n")
			first = _commit_all(up, "upstream v1")
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			_squash_commit(dot, up, "main", "config/agent-skills/anthropics", first)
			write(up, "skills/pptx/SKILL.md", "pptx v2\n")
			second = _commit_all(up, "upstream v2")
			newer = _squash_commit(dot, up, "main", "config/agent-skills/anthropics", second)

			# A stale baseline would report every later upstream release as
			# drift that a sync already took.
			self.assertEqual(drift.subtree_baseline_sha(dot, "config/agent-skills/anthropics"),
				(newer, second))

	def test_prefix_is_matched_whole(self):
		with tempfile.TemporaryDirectory() as tmp:
			up = _init_repo(os.path.join(tmp, "up"))
			write(up, "skills/pptx/SKILL.md", "pptx v1\n")
			split = _commit_all(up, "upstream v1")
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			_squash_commit(dot, up, "main", "config/agent-skills/anthropics-extra", split)

			# `anthropics` must not borrow `anthropics-extra`'s sync point.
			self.assertEqual(drift.subtree_baseline_sha(dot, "config/agent-skills/anthropics"),
				(None, None))

	def test_absent_prefix_and_non_repo_are_survivable(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			self.assertEqual(drift.subtree_baseline_sha(dot, "config/agent-skills/nope"), (None, None))
			self.assertEqual(drift.subtree_baseline_sha(os.path.join(tmp, "not-a-repo"), "x"), (None, None))
			self.assertEqual(drift.subtree_baseline_sha(dot, ""), (None, None))


# ── 4. End to end ────────────────────────────────────────────────────────────
# Every key the `skill_drift` contract promises on a finding
# (references/collection.md §Skill-Drift Collection). assemble.py reads these
# by name, so a rename here is a silent field of nulls over there.
CONTRACT_KEYS = {
	"id", "name", "source", "drift_state", "severity", "detail", "vendor",
	"skill", "vendor_kind", "upstream_url", "upstream_branch",
	"upstream_subpath", "local_path", "baseline_sha", "upstream_sha",
	"expected", "remediation", "pinned", "current_version", "latest_version",
}

SYNC_TEMPLATE = '''#!/usr/bin/env bash
vendors=(
\t"config/agent-skills/anthropics|{anthropics}|main|skills/pdf skills/pptx skills/docx skills/skill-creator"
)

sparse_vendors=(
\t"config/agent-skills/softaworks/jira|{softaworks}|main|skills/jira"
)
'''

WORLD_MARKETPLACE = json.dumps({"plugins": [
	{"name": "find-skills", "source": {"source": "git-subdir", "url": "https://example.invalid/x", "path": "p", "ref": "main"}},
	{"name": "browser", "source": "./tapppi/browser"},
	{"name": "pdf", "source": "./anthropics/skills/pdf"},
	{"name": "pptx", "source": "./anthropics/skills/pptx"},
	{"name": "docx", "source": "./anthropics/skills/docx"},
	{"name": "skill-creator", "source": "./anthropics/skills/skill-creator"},
	{"name": "jira", "source": "./softaworks/jira"},
]}, indent=1)


def _build_world(tmp):
	"""A synthetic dotfiles repo plus two local 'upstream' repos, arranged to
	produce one of each interesting state. The upstreams are plain filesystem
	paths — a valid git URL, so the fetch path is exercised for real without
	a single packet leaving the machine.

	    anthropics (subtree)  pdf            in_sync
	                          pptx           upstream_ahead
	                          docx           diverged   (patched here AND moved upstream)
	                          skill-creator  local_only (patched here only)
	    softaworks (sparse)   jira           upstream_ahead
	    tapppi                browser        ours — under no vendor prefix

	Two deliberate shapes beyond "one of each state". Anthropics drifts on
	*two* skills, because one `sync-upstream.sh` run fixes a whole vendor and
	the remediation label and detail both have to count them. And the sparse
	upstream moves off the commit `CUSTOMISATION.md` recorded, so its baseline
	is no longer the branch tip: that is the only arrangement in which the
	detector's second, sha-targeted fetch runs at all."""
	up_a = _init_repo(os.path.join(tmp, "upstream-anthropics"))
	write(up_a, "skills/pdf/SKILL.md", "pdf v1\n")
	write(up_a, "skills/pptx/SKILL.md", "pptx v1\n")
	write(up_a, "skills/docx/SKILL.md", "docx v1\n")
	write(up_a, "skills/skill-creator/SKILL.md", "skill-creator v1\n")
	base_a = _commit_all(up_a, "anthropics v1")

	up_s = _init_repo(os.path.join(tmp, "upstream-softaworks"))
	write(up_s, "skills/jira/SKILL.md", "jira v1\n")
	base_s = _commit_all(up_s, "softaworks v1")

	dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
	root = os.path.join(dot, "config", "agent-skills")
	for rel, body in (("anthropics/skills/pdf/SKILL.md", "pdf v1\n"),
			("anthropics/skills/pptx/SKILL.md", "pptx v1\n"),
			("anthropics/skills/docx/SKILL.md", "docx v1\n"),
			("anthropics/skills/skill-creator/SKILL.md", "skill-creator v1\n"),
			("softaworks/jira/SKILL.md", "jira v1\n"),
			("tapppi/browser/SKILL.md", "our own skill\n")):
		write(root, rel, body)
	write(root, "softaworks/CUSTOMISATION.md", f"- Last synced commit: `{base_s}`\n")
	write(root, "sync-upstream.sh", SYNC_TEMPLATE.format(anthropics=up_a, softaworks=up_s))
	write(root, ".claude-plugin/marketplace.json", WORLD_MARKETPLACE)
	_commit_all(dot, "vendor upstream skills")
	_squash_commit(dot, up_a, "main", "config/agent-skills/anthropics", base_a)

	# Upstream releases a new pptx and a new docx; pdf is untouched.
	write(up_a, "skills/pptx/SKILL.md", "pptx v2 — upstream moved\n")
	write(up_a, "skills/docx/SKILL.md", "docx v2 — upstream moved\n")
	_commit_all(up_a, "anthropics v2")
	# The sparse upstream moves too, off the sync commit CUSTOMISATION.md
	# names — so BASELINE stops being reachable as the branch tip.
	write(up_s, "skills/jira/SKILL.md", "jira v2 — upstream moved\n")
	_commit_all(up_s, "softaworks v2")
	# We patch skill-creator locally and upstream never moved on it
	# (local_only); we patch docx locally and upstream did (diverged).
	write(root, "anthropics/skills/skill-creator/SKILL.md", "skill-creator v1 + local patch\n")
	write(root, "anthropics/skills/docx/SKILL.md", "docx v1 + local patch\n")
	_commit_all(dot, "patch skill-creator and docx locally")
	return dot


def _detect(dotfiles_root, *extra):
	"""Run the detector in-process and return (exit_code, parsed_json)."""
	buf = io.StringIO()
	with contextlib.redirect_stdout(buf):
		code = drift.main(["--dotfiles-root", dotfiles_root] + list(extra))
	return code, json.loads(buf.getvalue())


class EndToEndTests(unittest.TestCase):
	def test_every_state_against_local_upstreams(self):
		with tempfile.TemporaryDirectory() as tmp:
			code, out = _detect(_build_world(tmp))
			self.assertEqual(code, 0)
			states = {f["id"]: f["drift_state"] for f in out["findings"]}
			self.assertEqual(states, {
				"skill-drift:anthropics/pptx": "upstream_ahead",
				"skill-drift:anthropics/docx": "diverged",
				"skill-drift:anthropics/skill-creator": "local_only",
				"skill-drift:softaworks/jira": "upstream_ahead",
			})
			# in_sync is not a finding, and neither is a skill of ours.
			joined = "\n".join(out["suppressed"])
			self.assertIn("anthropics/pdf", joined)
			self.assertIn("tapppi/browser", joined)

	def test_diverged_is_the_one_that_warns_and_names_the_patch_record(self):
		# Both sides moved, so the sync lands on top of a local patch. This is
		# the only state whose severity is `warning`, and the only drift card
		# whose text sends the reader to CUSTOMISATION.md *before* syncing.
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			docx = next(f for f in out["findings"] if f["skill"] == "docx")
			self.assertEqual(docx["drift_state"], "diverged")
			self.assertEqual(docx["severity"], "warning")
			self.assertFalse(docx["expected"])
			self.assertIn("CUSTOMISATION.md", docx["detail"])
			self.assertIn("conflict review", docx["detail"])
			self.assertEqual(docx["remediation"]["command"],
				"bash config/agent-skills/sync-upstream.sh")

	def test_sparse_baseline_is_fetched_by_sha_once_upstream_moves(self):
		# The sparse vendor has no offline baseline: when upstream has moved
		# off the recorded sync commit, that commit is no longer the branch tip
		# and has to be fetched by sha. If that second fetch did not happen,
		# BASELINE would not resolve and jira would degrade to a suppressed
		# line instead of classifying.
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			jira = next(f for f in out["findings"] if f["skill"] == "jira")
			self.assertEqual(jira["drift_state"], "upstream_ahead")
			self.assertEqual(jira["vendor_kind"], "sparse")
			self.assertEqual(jira["upstream_subpath"], "skills/jira")
			self.assertNotEqual(jira["baseline_sha"], jira["upstream_sha"])

	def test_vendor_scoped_remediation_counts_the_skills_one_run_fixes(self):
		# `git subtree pull` is per-vendor, so two anthropics cards carry the
		# same command; the label and the detail have to say so rather than
		# reading as two independent fixes. One drifted skill (softaworks) gets
		# neither the count nor the "also" clause.
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			by_skill = {f["skill"]: f for f in out["findings"]}
			self.assertEqual(by_skill["pptx"]["remediation"]["label"],
				"Sync anthropics from upstream (updates all 2 drifted anthropics skills)")
			self.assertIn("also refreshes the other 1 drifted `anthropics` skill.",
				by_skill["pptx"]["detail"])
			self.assertEqual(by_skill["jira"]["remediation"]["label"],
				"Sync softaworks from upstream")
			self.assertNotIn("also refreshes", by_skill["jira"]["detail"])

	def test_upstream_ahead_finding_is_fully_populated(self):
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			pptx = next(f for f in out["findings"] if f["skill"] == "pptx")
			self.assertEqual(set(pptx), CONTRACT_KEYS)
			self.assertEqual(pptx["source"], "skill-drift")
			self.assertEqual(pptx["name"], "pptx (anthropics)")
			self.assertEqual(pptx["severity"], "notable")
			self.assertFalse(pptx["expected"], "an upstream release is a decision to make")
			self.assertEqual(pptx["vendor_kind"], "subtree")
			self.assertEqual(pptx["upstream_subpath"], "skills/pptx")
			self.assertEqual(pptx["local_path"], "config/agent-skills/anthropics/skills/pptx")
			self.assertRegex(pptx["baseline_sha"], r"^[0-9a-f]{40}$")
			self.assertRegex(pptx["upstream_sha"], r"^[0-9a-f]{40}$")
			self.assertNotEqual(pptx["baseline_sha"], pptx["upstream_sha"])
			self.assertEqual(pptx["remediation"]["command"], "bash config/agent-skills/sync-upstream.sh")
			self.assertFalse(pptx["remediation"]["auto_runnable"],
				"a subtree pull rewrites vendored files and can conflict — never automatic")
			self.assertFalse(pptx["remediation"]["needs_sudo"])
			self.assertIsNone(pptx["current_version"])
			self.assertIsNone(pptx["latest_version"])
			self.assertFalse(pptx["pinned"])

	def test_local_only_is_expected_and_carries_no_remediation(self):
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			sc = next(f for f in out["findings"] if f["skill"] == "skill-creator")
			self.assertEqual(sc["drift_state"], "local_only")
			self.assertEqual(sc["severity"], "info")
			self.assertTrue(sc["expected"], "our own patch is not a decision to make")
			self.assertIsNone(sc["remediation"],
				"there is nothing to sync — offering the vendor sync here would "
				"propose discarding the local patch")

	def test_no_network_costs_one_card_per_vendor_not_per_skill(self):
		with tempfile.TemporaryDirectory() as tmp:
			code, out = _detect(_build_world(tmp), "--no-network")
			self.assertEqual(code, 0)
			self.assertEqual(sorted(f["id"] for f in out["findings"]),
				["skill-drift:anthropics:probe-failed", "skill-drift:softaworks:probe-failed"])
			for f in out["findings"]:
				self.assertEqual(set(f), CONTRACT_KEYS)
				self.assertEqual(f["drift_state"], "probe_error")
				self.assertEqual(f["severity"], "info")
				self.assertTrue(f["expected"])
				self.assertIsNone(f["remediation"])
				self.assertIsNone(f["skill"])
			# Every adopted skill goes to suppressed rather than becoming a
			# card nobody can act on.
			joined = "\n".join(out["suppressed"])
			for skill in ("pdf", "pptx", "docx", "skill-creator", "jira"):
				self.assertIn(skill, joined)
			# The detail is read aloud on a card, so its count has to agree
			# with itself in both the singular and the plural branch.
			by_vendor = {f["vendor"]: f["detail"] for f in out["findings"]}
			self.assertIn("The 4 adopted `anthropics` skills were not checked", by_vendor["anthropics"])
			self.assertIn("The 1 adopted `softaworks` skill was not checked", by_vendor["softaworks"])

	def test_unmatched_skill_is_suppressed_not_a_finding(self):
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			self.assertNotIn("tapppi/browser", [f["id"] for f in out["findings"]])
			self.assertTrue(any("tapppi/browser" in s for s in out["suppressed"]),
				"a skill under no vendor prefix is ours, and must say so")

	def test_unreachable_upstream_degrades_to_probe_error(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _build_world(tmp)
			# Point one vendor at a path that is not a repository at all.
			script = os.path.join(dot, "config", "agent-skills", "sync-upstream.sh")
			with open(script, encoding="utf-8") as fh:
				text = fh.read()
			text = text.replace(os.path.join(tmp, "upstream-anthropics"),
				os.path.join(tmp, "gone"))
			write(dot, "config/agent-skills/sync-upstream.sh", text)
			_commit_all(dot, "break the anthropics upstream")
			code, out = _detect(dot, "--timeout", "20")
			self.assertEqual(code, 0)
			ids = [f["id"] for f in out["findings"]]
			self.assertIn("skill-drift:anthropics:probe-failed", ids)
			self.assertNotIn("skill-drift:anthropics/pptx", ids)

	def test_upstream_deleting_a_skill_is_a_finding_not_a_shrug(self):
		# LOCAL and BASELINE resolve, UPSTREAM does not: the skill was there at
		# the sync point and is gone from upstream now. Suppressing that would
		# hide the one consequence that matters — the next vendor sync silently
		# stops shipping the skill — behind a line nobody reads.
		with tempfile.TemporaryDirectory() as tmp:
			dot = _build_world(tmp)
			up_a = os.path.join(tmp, "upstream-anthropics")
			git(up_a, "rm", "-r", "-q", "skills/pdf")
			_commit_all(up_a, "upstream drops pdf")
			code, out = _detect(dot)
			self.assertEqual(code, 0)
			pdf = next(f for f in out["findings"] if f["skill"] == "pdf")
			self.assertEqual(set(pdf), CONTRACT_KEYS)
			self.assertEqual(pdf["id"], "skill-drift:anthropics/pdf")
			self.assertEqual(pdf["drift_state"], "probe_error")
			self.assertEqual(pdf["severity"], "notable")
			self.assertFalse(pdf["expected"],
				"a skill upstream deleted is a decision, not a quiet note")
			self.assertIsNone(pdf["remediation"],
				"the vendor sync is what would lose it — it is not the fix")
			self.assertIn("skills/pdf", pdf["detail"])
			self.assertIn("removed or renamed", pdf["detail"])

	def test_other_unresolved_sides_are_suppressed_and_name_the_side(self):
		# Local copy gone, baseline and upstream fine. Nothing is knowable
		# about it, so it stays suppressed — but the line has to say which of
		# the three sides failed, or it is a shrug with a path in it.
		with tempfile.TemporaryDirectory() as tmp:
			dot = _build_world(tmp)
			git(dot, "rm", "-r", "-q", "config/agent-skills/anthropics/skills/pdf")
			_commit_all(dot, "drop our copy of pdf")
			_, out = _detect(dot)
			self.assertNotIn("skill-drift:anthropics/pdf", [f["id"] for f in out["findings"]])
			line = next(s for s in out["suppressed"] if s.startswith("anthropics/pdf"))
			self.assertIn("did not resolve in local", line)


class RemediationTextTests(unittest.TestCase):
	"""One `sync-upstream.sh` run fixes a whole vendor, so both the label and
	the detail state how many skills that covers — and both have to agree with
	themselves at one, two and more than two."""

	def test_label_counts_only_when_one_run_fixes_more_than_one(self):
		self.assertEqual(drift._remediation("google", 1)["label"],
			"Sync google from upstream")
		self.assertEqual(drift._remediation("google", 3)["label"],
			"Sync google from upstream (updates all 3 drifted google skills)")

	def test_detail_also_clause_agrees_with_its_count(self):
		def detail(drifted):
			return drift._detail("upstream_ahead", "pptx", "anthropics",
				"config/agent-skills/anthropics", "https://u", "main",
				"skills/pptx", "a" * 40, "b" * 40, drifted)
		self.assertNotIn("also refreshes", detail(1))
		self.assertIn("the other 1 drifted `anthropics` skill.", detail(2))
		self.assertIn("the other 2 drifted `anthropics` skills.", detail(3))


class DegradationTests(unittest.TestCase):
	"""The collector must not abort — every one of these exits 0 with a
	parseable object rather than raising.

	And it must not go quiet either. `suppressed` is logged to stderr and never
	reaches report.json or the page, so an empty `findings` list is
	indistinguishable on screen from a clean run: a missing dotfiles checkout,
	or a `sync-upstream.sh` restructured past the parser, would render as "all
	vendored skills in sync". Each of these therefore asserts exactly one
	quiet, expected `probe_error` card saying the check did not run — the same
	silence-is-not-success rule the per-vendor probe failure follows."""

	def _assert_source_unavailable(self, root, *extra):
		code, out = _detect(root, *extra)
		self.assertEqual(code, 0)
		self.assertEqual(set(out), {"findings", "suppressed"})
		self.assertEqual(len(out["findings"]), 1, out["findings"])
		card = out["findings"][0]
		self.assertEqual(set(card), CONTRACT_KEYS)
		self.assertEqual(card["id"], "skill-drift:source-unavailable")
		self.assertEqual(card["drift_state"], "probe_error")
		self.assertEqual(card["severity"], "info")
		self.assertTrue(card["expected"],
			"nothing was checked, so there is nothing here to decide")
		self.assertIsNone(card["remediation"])
		self.assertIsNone(card["skill"])
		self.assertTrue(card["detail"].strip(),
			"a card that says nothing is no better than the silence it replaces")
		self.assertTrue(out["suppressed"], "the stderr log must still say why")
		return card

	def test_missing_dotfiles_root(self):
		with tempfile.TemporaryDirectory() as tmp:
			card = self._assert_source_unavailable(os.path.join(tmp, "nope"))
			self.assertIn("nope", card["detail"], "the card must name what it looked for")

	def test_root_is_not_a_git_repo(self):
		with tempfile.TemporaryDirectory() as tmp:
			os.makedirs(os.path.join(tmp, "plain", "config", "agent-skills"))
			card = self._assert_source_unavailable(os.path.join(tmp, "plain"))
			self.assertIn("git", card["detail"])

	def test_repo_without_agent_skills(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			card = self._assert_source_unavailable(dot)
			self.assertIn("sync-upstream.sh", card["detail"])

	def test_unparseable_sync_script(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "config/agent-skills/sync-upstream.sh", "#!/usr/bin/env bash\nexit 0\n")
			write(dot, "config/agent-skills/.claude-plugin/marketplace.json", WORLD_MARKETPLACE)
			_commit_all(dot, "init")
			card = self._assert_source_unavailable(dot)
			self.assertIn("vendor tables", card["detail"])

	def test_no_adopted_plugins(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "config/agent-skills/sync-upstream.sh",
				SYNC_TEMPLATE.format(anthropics="https://example.invalid/a",
					softaworks="https://example.invalid/s"))
			write(dot, "config/agent-skills/.claude-plugin/marketplace.json", "{ not json")
			_commit_all(dot, "init")
			card = self._assert_source_unavailable(dot)
			self.assertIn("marketplace.json", card["detail"])

	def test_unexpected_exception_reports_itself_rather_than_vanishing(self):
		# main()'s catch-all is the last line of defence, and it used to be the
		# quietest: a crash printed an empty findings list that read as health.
		original = drift.detect

		def boom(*_args, **_kwargs):
			raise RuntimeError("kaboom")

		drift.detect = boom
		try:
			card = self._assert_source_unavailable("/nonexistent")
		finally:
			drift.detect = original
		self.assertIn("kaboom", card["detail"])
		self.assertIn("RuntimeError", card["detail"])


class CliTests(unittest.TestCase):
	def test_runs_as_a_script_and_prints_one_json_object(self):
		# collect.sh shells out to this file and pipes the result into jq —
		# so stdout must be exactly one parseable object, with the exit code
		# and the stream unaffected by whatever went wrong inside.
		script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collect_skill_drift.py")
		with tempfile.TemporaryDirectory() as tmp:
			p = subprocess.run(
				[sys.executable, script, "--dotfiles-root", _build_world(tmp), "--no-network"],
				capture_output=True, text=True, timeout=180)
			self.assertEqual(p.returncode, 0, p.stderr)
			parsed = json.loads(p.stdout)
			self.assertEqual(set(parsed), {"findings", "suppressed"})
			self.assertIsInstance(parsed["findings"], list)
			self.assertIsInstance(parsed["suppressed"], list)


if __name__ == "__main__":
	unittest.main(verbosity=2)
