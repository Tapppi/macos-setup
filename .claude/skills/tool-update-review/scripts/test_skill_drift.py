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
\t"anthropics|https://github.com/anthropics/skills|main|skills/pdf skills/pptx"
\t"google|https://github.com/google/skills|main|skills/cloud/gke-basics"
)

#   <dest-prefix>|<upstream-url>|<branch>|<upstream-subpath>
sparse_vendors=(
\t"softaworks/jira|https://github.com/softaworks/agent-toolkit|main|skills/jira"
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
			"prefix": "anthropics",
			"url": "https://github.com/anthropics/skills",
			"branch": "main",
			"skills": ["skills/pdf", "skills/pptx"],
		})
		self.assertEqual(vendors[2], {
			"kind": "sparse",
			"dest": "softaworks/jira",
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
			'\t"ok|https://u|main|skills/a"\n'
			'\t\n'
			')\n')
		vendors = drift.parse_vendor_tables(text)
		self.assertEqual(len(vendors), 1)
		self.assertEqual(vendors[0]["prefix"], "ok")

	def test_a_subtree_row_may_list_excluded_paths(self):
		# The Tapppi/skills table carries a fifth field: paths never vendored.
		text = ('vendors=(\n'
			'\t"anthropics|https://u|main|skills/skill-creator|skills/docx .claude-plugin"\n'
			')\n')
		self.assertEqual(drift.parse_vendor_tables(text), [{"kind": "subtree",
			"prefix": "anthropics", "url": "https://u", "branch": "main",
			"skills": ["skills/skill-creator"]}])

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

	def test_a_plugin_naming_its_skills_adopts_each_of_them(self):
		text = json.dumps({"plugins": [{"name": "skill-creator", "source": "./",
			"strict": False, "skills": ["./anthropics/skills/skill-creator", 7,
				"../escape"]}]})
		self.assertEqual(drift.parse_marketplace_adopted(text),
			[{"name": "skill-creator", "rel_path": "anthropics/skills/skill-creator"}])

	def test_a_plugin_directory_holding_a_vendor_stands_for_its_skills(self):
		vendors = drift.parse_vendor_tables(
			'vendors=(\n\t"anthropics|https://a|main|skills/pdf skills/pptx"\n)\n'
			'sparse_vendors=(\n\t"softaworks/skills/jira|https://s|main|skills/jira"\n)\n')
		adopted = drift.parse_marketplace_adopted(json.dumps({"plugins": [
			{"name": "jira", "source": "./softaworks"},
			{"name": "all-anthropics", "source": "./anthropics"},
			{"name": "browser", "source": "./tapppi/browser"},
			{"name": "pdf-again", "source": "./anthropics/skills/pdf"},
		]}))
		self.assertEqual(drift.resolve_adopted(adopted, vendors), [
			{"name": "jira", "rel_path": "softaworks/skills/jira"},
			{"name": "pdf", "rel_path": "anthropics/skills/pdf"},
			{"name": "pptx", "rel_path": "anthropics/skills/pptx"},
			{"name": "browser", "rel_path": "tapppi/browser"},
		])

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
			squash = _squash_commit(dot, up, "main", "anthropics", split)

			self.assertEqual(drift.subtree_baseline_sha(dot, "anthropics"),
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
			_squash_commit(dot, up, "main", "anthropics", first)
			write(up, "skills/pptx/SKILL.md", "pptx v2\n")
			second = _commit_all(up, "upstream v2")
			newer = _squash_commit(dot, up, "main", "anthropics", second)

			# A stale baseline would report every later upstream release as
			# drift that a sync already took.
			self.assertEqual(drift.subtree_baseline_sha(dot, "anthropics"),
				(newer, second))

	def test_a_mainline_merge_reads_its_squash_parent(self):
		"""The Tapppi/skills shape: after the move out of dotfiles the latest
		commit naming the prefix is a merge carrying `git-subtree-mainline:`,
		whose own tree is the whole repo. Its squash is its second parent."""
		with tempfile.TemporaryDirectory() as tmp:
			up = _init_repo(os.path.join(tmp, "up"))
			write(up, "skills/pptx/SKILL.md", "pptx v1\n")
			split = _commit_all(up, "upstream v1")
			repo = _init_repo(os.path.join(tmp, "skills"))
			write(repo, "anthropics/skills/pptx/SKILL.md", "pptx v1\n")
			mainline = _commit_all(repo, "init")
			git(repo, "fetch", "-q", up, "main")
			squash = git(repo, "commit-tree", git(repo, "rev-parse", "FETCH_HEAD^{tree}"),
				"-m", "Squashed 'anthropics/' content\n\ngit-subtree-dir: anthropics\n"
				"git-subtree-split: " + split + "\n")
			git(repo, "merge", "-q", "-s", "ours", "--allow-unrelated-histories", "-m",
				"Re-establish the base\n\ngit-subtree-dir: anthropics\n"
				"git-subtree-mainline: " + mainline + "\ngit-subtree-split: " + split + "\n",
				squash)
			self.assertEqual(drift.subtree_baseline_sha(repo, "anthropics"), (squash, split))

	def test_prefix_is_matched_whole(self):
		with tempfile.TemporaryDirectory() as tmp:
			up = _init_repo(os.path.join(tmp, "up"))
			write(up, "skills/pptx/SKILL.md", "pptx v1\n")
			split = _commit_all(up, "upstream v1")
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			_squash_commit(dot, up, "main", "anthropics-extra", split)

			# `anthropics` must not borrow `anthropics-extra`'s sync point.
			self.assertEqual(drift.subtree_baseline_sha(dot, "anthropics"),
				(None, None))

	def test_absent_prefix_and_non_repo_are_survivable(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			self.assertEqual(drift.subtree_baseline_sha(dot, "nope"), (None, None))
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
# <prefix>|<upstream-url>|<branch>|<adopted-skills>|<excluded-paths>
vendors=(
\t"anthropics|{anthropics}|main|skills/pdf skills/pptx skills/docx skills/skill-creator|skills/xlsx"
)

sparse_vendors=(
\t"softaworks/skills/jira|{softaworks}|main|skills/jira"
)
'''

# The Tapppi/skills marketplace's shapes: a bundle of our own, a plugin
# rooted at "./" that names its vendored skills, and a plugin whose source is
# a directory holding a sparse vendor.
WORLD_MARKETPLACE = json.dumps({"plugins": [
	{"name": "find-skills", "source": {"source": "git-subdir", "url": "https://example.invalid/x", "path": "p", "ref": "main"}},
	{"name": "browser", "source": "./tapppi/browser"},
	{"name": "anthropic-skills", "source": "./", "strict": False, "skills": [
		"./anthropics/skills/pdf", "./anthropics/skills/pptx",
		"./anthropics/skills/docx", "./anthropics/skills/skill-creator"]},
	{"name": "jira", "source": "./softaworks"},
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

	dot = _init_repo(os.path.join(tmp, "skills"))
	root = dot
	for rel, body in (("anthropics/skills/pdf/SKILL.md", "pdf v1\n"),
			("anthropics/skills/pptx/SKILL.md", "pptx v1\n"),
			("anthropics/skills/docx/SKILL.md", "docx v1\n"),
			("anthropics/skills/skill-creator/SKILL.md", "skill-creator v1\n"),
			("softaworks/skills/jira/SKILL.md", "jira v1\n"),
			("tapppi/browser/SKILL.md", "our own skill\n")):
		write(root, rel, body)
	write(root, "softaworks/CUSTOMISATION.md", f"- Last synced commit: `{base_s}`\n")
	write(root, "sync-upstream.sh", SYNC_TEMPLATE.format(anthropics=up_a, softaworks=up_s))
	write(root, ".claude-plugin/marketplace.json", WORLD_MARKETPLACE)
	_commit_all(dot, "vendor upstream skills")
	_squash_commit(dot, up_a, "main", "anthropics", base_a)

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
		code = drift.main(["--skills-root", dotfiles_root] + list(extra))
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
				"bash sync-upstream.sh")

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

	def test_the_one_sync_is_labelled_all_vendor_and_lists_what_it_touches(self):
		# `sync-upstream.sh` takes no vendor argument: one run merges every
		# subtree vendor and overwrites every sparse one. So every drifted card
		# — anthropics and softaworks alike — carries the SAME remediation,
		# labelled as a sync of every vendor, and its detail names each vendor,
		# every drifted skill it resolves and every local patch it touches.
		# A "Sync anthropics" label here once invited a user to refresh jira
		# without knowing it.
		with tempfile.TemporaryDirectory() as tmp:
			_, out = _detect(_build_world(tmp))
			by_skill = {f["skill"]: f for f in out["findings"]}
			drifted = [by_skill[s] for s in ("pptx", "docx", "jira")]
			label = ("Sync every vendor from upstream (anthropics, softaworks) — review "
				"local patches first")
			for f in drifted:
				self.assertEqual(f["remediation"], {"command": "bash sync-upstream.sh",
					"auto_runnable": False, "needs_sudo": False, "label": label})
				detail = f["detail"]
				self.assertIn("There is no per-vendor or per-skill sync", detail)
				self.assertIn("anthropics (a git subtree merge), softaworks (a sparse copy, "
					"overwritten with `rsync --delete`)", detail)
				self.assertIn("all 3 drifted skills: anthropics/docx, anthropics/pptx, "
					"softaworks/jira", detail)
				self.assertIn("anthropics/docx (diverged: the merge can conflict", detail)
				self.assertIn("anthropics/skill-creator (local patch: the merge keeps it",
					detail)
			self.assertIsNone(by_skill["skill-creator"]["remediation"])
			self.assertNotIn("per-vendor", by_skill["skill-creator"]["detail"])

	def test_another_vendors_sync_names_the_sparse_patch_it_overwrites(self):
		# The review's scenario: accept an anthropics card while a softaworks
		# (sparse) copy carries a local patch. The one command overwrites that
		# copy with rsync --delete, so the anthropics card has to say so.
		with tempfile.TemporaryDirectory() as tmp:
			dot = _build_world(tmp)
			write(dot, "softaworks/skills/jira/SKILL.md", "jira v1 + our jira patch\n")
			_commit_all(dot, "patch jira locally")
			_, out = _detect(dot)
			by_skill = {f["skill"]: f for f in out["findings"]}
			self.assertEqual(by_skill["jira"]["drift_state"], "diverged")
			self.assertIn("softaworks/jira (diverged: the copy is overwritten, so the "
				"patch must be re-applied)", by_skill["pptx"]["detail"])
			self.assertIn("Syncing overwrites the patch rather than merging it",
				by_skill["jira"]["detail"])
			self.assertNotIn("conflict review", by_skill["jira"]["detail"])

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
			self.assertEqual(pptx["local_path"], "anthropics/skills/pptx")
			self.assertRegex(pptx["baseline_sha"], r"^[0-9a-f]{40}$")
			self.assertRegex(pptx["upstream_sha"], r"^[0-9a-f]{40}$")
			self.assertNotEqual(pptx["baseline_sha"], pptx["upstream_sha"])
			self.assertEqual(pptx["remediation"]["command"], "bash sync-upstream.sh")
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
			script = os.path.join(dot, "sync-upstream.sh")
			with open(script, encoding="utf-8") as fh:
				text = fh.read()
			text = text.replace(os.path.join(tmp, "upstream-anthropics"),
				os.path.join(tmp, "gone"))
			write(dot, "sync-upstream.sh", text)
			_commit_all(dot, "break the anthropics upstream")
			code, out = _detect(dot, "--timeout", "20")
			self.assertEqual(code, 0)
			ids = [f["id"] for f in out["findings"]]
			self.assertIn("skill-drift:anthropics:probe-failed", ids)
			self.assertNotIn("skill-drift:anthropics/pptx", ids)
			# The one sync still runs the vendor nobody could check.
			jira = next(f for f in out["findings"] if f["skill"] == "jira")
			self.assertIn("Not checked this run, but synced by that same run anyway: "
				"anthropics.", jira["detail"])

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
			git(dot, "rm", "-r", "-q", "anthropics/skills/pdf")
			_commit_all(dot, "drop our copy of pdf")
			_, out = _detect(dot)
			self.assertNotIn("skill-drift:anthropics/pdf", [f["id"] for f in out["findings"]])
			line = next(s for s in out["suppressed"] if s.startswith("anthropics/pdf"))
			self.assertIn("did not resolve in local", line)


class RemediationTextTests(unittest.TestCase):
	"""One `sync-upstream.sh` run syncs EVERY vendor, so the remediation and
	the detail it ends each drifted card with are built from one plan of what
	that run touches — and have to agree with themselves at one and more."""

	VENDORS = [{"kind": "subtree", "prefix": "anthropics"},
		{"kind": "sparse", "dest": "softaworks/skills/jira"}]

	@staticmethod
	def _f(vendor, skill, state, kind):
		return {"vendor": vendor, "skill": skill, "drift_state": state, "vendor_kind": kind}

	def test_the_label_names_every_vendor_and_asks_for_review_only_with_patches(self):
		clean = drift._sync_plan(self.VENDORS,
			[self._f("anthropics", "pptx", "upstream_ahead", "subtree")], [])
		self.assertEqual(drift._remediation(clean)["label"],
			"Sync every vendor from upstream (anthropics, softaworks)")
		patched = drift._sync_plan(self.VENDORS,
			[self._f("anthropics", "pptx", "upstream_ahead", "subtree"),
			self._f("softaworks", "jira", "local_only", "sparse")], [])
		self.assertEqual(drift._remediation(patched)["label"],
			"Sync every vendor from upstream (anthropics, softaworks) — review local "
			"patches first")
		self.assertFalse(drift._remediation(patched)["auto_runnable"])

	def test_the_scope_counts_agree_and_name_every_risk(self):
		one = drift._sync_scope(drift._sync_plan(self.VENDORS,
			[self._f("anthropics", "pptx", "upstream_ahead", "subtree")], []))
		self.assertIn("One run resolves the one drifted skill: anthropics/pptx.", one)
		self.assertNotIn("review these local patches", one)
		many = drift._sync_scope(drift._sync_plan(self.VENDORS, [
			self._f("anthropics", "pptx", "upstream_ahead", "subtree"),
			self._f("anthropics", "docx", "diverged", "subtree"),
			self._f("softaworks", "jira", "local_only", "sparse"),
			self._f("anthropics", "pdf", "probe_error", "subtree"),
		], ["google"]))
		self.assertIn("all 2 drifted skills: anthropics/docx, anthropics/pptx.", many)
		self.assertIn("softaworks/jira (local patch: the copy is overwritten, so the "
			"patch must be re-applied)", many)
		self.assertIn("It also stops shipping anthropics/pdf", many)
		self.assertIn("synced by that same run anyway: google.", many)

	def test_a_sparse_local_patch_is_told_any_sync_discards_it(self):
		def detail(kind):
			return drift._detail("local_only", "jira", "softaworks", "softaworks",
				"https://u", "main", "skills/jira", "a" * 40, "a" * 40, kind)
		self.assertIn("overwrites this sparse copy with `rsync --delete`", detail("sparse"))
		self.assertIn("a sync merges it three-way and keeps it", detail("subtree"))
		self.assertNotIn("would discard it", detail("subtree"))


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

	def _assert_source_unavailable(self, root, *extra, missing=False):
		"""`missing` — the source is not where it was looked for, which is a
		setup problem with a fix (it is how the move out of dotfiles went
		unnoticed), so that card is loud: notable, not expected, naming the
		override. Everything else is the quiet honest "unknown"."""
		code, out = _detect(root, *extra)
		self.assertEqual(code, 0)
		self.assertEqual(set(out), {"findings", "suppressed"})
		self.assertEqual(len(out["findings"]), 1, out["findings"])
		card = out["findings"][0]
		self.assertEqual(set(card), CONTRACT_KEYS)
		self.assertEqual(card["id"], "skill-drift:source-unavailable")
		self.assertEqual(card["drift_state"], "probe_error")
		if missing:
			self.assertEqual(card["severity"], "notable")
			self.assertFalse(card["expected"], "a missing source is a setup problem to fix")
			self.assertIn("--skills-root", card["detail"])
			self.assertIn(drift.SKILLS_ROOT_ENV, card["detail"])
		else:
			self.assertEqual(card["severity"], "info")
			self.assertTrue(card["expected"],
				"nothing was checked, so there is nothing here to decide")
		self.assertIsNone(card["remediation"])
		self.assertIsNone(card["skill"])
		self.assertTrue(card["detail"].strip(),
			"a card that says nothing is no better than the silence it replaces")
		self.assertTrue(out["suppressed"], "the stderr log must still say why")
		return card

	def test_missing_skills_root(self):
		with tempfile.TemporaryDirectory() as tmp:
			card = self._assert_source_unavailable(os.path.join(tmp, "nope"), missing=True)
			self.assertIn("nope", card["detail"], "the card must name what it looked for")

	def test_root_is_not_a_git_repo(self):
		with tempfile.TemporaryDirectory() as tmp:
			os.makedirs(os.path.join(tmp, "plain", "anthropics"))
			card = self._assert_source_unavailable(os.path.join(tmp, "plain"), missing=True)
			self.assertIn("git", card["detail"])

	def test_repo_without_a_sync_script(self):
		# e.g. pointed at the dotfiles checkout the skills moved out of
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "README.md", "dotfiles\n")
			_commit_all(dot, "init")
			card = self._assert_source_unavailable(dot, missing=True)
			self.assertIn("sync-upstream.sh", card["detail"])

	def test_unparseable_sync_script(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "sync-upstream.sh", "#!/usr/bin/env bash\nexit 0\n")
			write(dot, ".claude-plugin/marketplace.json", WORLD_MARKETPLACE)
			_commit_all(dot, "init")
			card = self._assert_source_unavailable(dot)
			self.assertIn("vendor tables", card["detail"])

	def test_no_adopted_plugins(self):
		with tempfile.TemporaryDirectory() as tmp:
			dot = _init_repo(os.path.join(tmp, "dotfiles"), branch="master")
			write(dot, "sync-upstream.sh",
				SYNC_TEMPLATE.format(anthropics="https://example.invalid/a",
					softaworks="https://example.invalid/s"))
			write(dot, ".claude-plugin/marketplace.json", "{ not json")
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


class SourceLocationTests(unittest.TestCase):
	def test_the_environment_names_the_skills_root(self):
		with tempfile.TemporaryDirectory() as tmp:
			target = os.path.join(tmp, "elsewhere")
			old = os.environ.get(drift.SKILLS_ROOT_ENV)
			os.environ[drift.SKILLS_ROOT_ENV] = target
			try:
				buf = io.StringIO()
				with contextlib.redirect_stdout(buf):
					drift.main(["--no-network"])
			finally:
				if old is None:
					os.environ.pop(drift.SKILLS_ROOT_ENV, None)
				else:
					os.environ[drift.SKILLS_ROOT_ENV] = old
			card = json.loads(buf.getvalue())["findings"][0]
			self.assertIn(target, card["detail"])

	def test_failed_emits_the_quiet_source_unavailable_card(self):
		buf = io.StringIO()
		with contextlib.redirect_stdout(buf):
			self.assertEqual(drift.main(["--failed", "It timed out."]), 0)
		out = json.loads(buf.getvalue())
		self.assertEqual([f["id"] for f in out["findings"]], ["skill-drift:source-unavailable"])
		self.assertEqual(set(out["findings"][0]), CONTRACT_KEYS)
		self.assertIn("It timed out.", out["findings"][0]["detail"])


class CliTests(unittest.TestCase):
	def test_runs_as_a_script_and_prints_one_json_object(self):
		# collect.sh shells out to this file and pipes the result into jq —
		# so stdout must be exactly one parseable object, with the exit code
		# and the stream unaffected by whatever went wrong inside.
		script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collect_skill_drift.py")
		with tempfile.TemporaryDirectory() as tmp:
			p = subprocess.run(
				[sys.executable, script, "--skills-root", _build_world(tmp), "--no-network"],
				capture_output=True, text=True, timeout=180)
			self.assertEqual(p.returncode, 0, p.stderr)
			parsed = json.loads(p.stdout)
			self.assertEqual(set(parsed), {"findings", "suppressed"})
			self.assertIsInstance(parsed["findings"], list)
			self.assertIsInstance(parsed["suppressed"], list)


if __name__ == "__main__":
	unittest.main(verbosity=2)
