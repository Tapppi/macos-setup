# Tool Update Review — Data Model Reference

Every JSON shape in the system, in one place: the report object (the `Tool`
schema and its parent, injected into the rendered page), `feedback.json`
(the user's decisions coming back from the page), `status.json` (the live
apply-progress file the page polls, including the `Turn` object shared by
followups and failed-action threads), and `research-status.json` (the
pre-report progress file the loading page polls).

This is a pure data-model reference — no workflow prose. For *how* a field
gets computed or *when* a step writes it, follow the pointers inline to
`references/research.md`, `references/assembly.md`, `references/apply.md`,
`references/collection.md`, or `references/server-and-session.md`. For *how*
a field renders, follow the pointers to `references/rendering-report.md` or
`references/rendering-results.md`.

Table of contents:
- [1. Report Object (report.json)](#1-report-object-reportjson)
  - [1.1 Top-level shape](#11-top-level-shape)
  - [1.2 Tool object](#12-tool-object)
  - [1.3 Source vocabulary](#13-source-vocabulary)
  - [1.4 `risk_level` semantics](#14-risk_level-semantics)
  - [1.5 `config_status.state` semantics](#15-config_statusstate-semantics)
  - [1.6 `kind: "upgrade"` field semantics](#16-kind-upgrade-field-semantics)
- [2. Feedback Object (feedback.json)](#2-feedback-object-feedbackjson)
  - [2.1 Schema](#21-schema)
  - [2.2 `decision` semantics](#22-decision-semantics)
- [3. Status Object (status.json)](#3-status-object-statusjson)
  - [3.1 File location and write pattern](#31-file-location-and-write-pattern)
  - [3.2 Schema (schema_version 2)](#32-schema-schema_version-2)
  - [3.3 The Turn object](#33-the-turn-object)
  - [3.4 Action ordering](#34-action-ordering)
  - [3.5 State transitions](#35-state-transitions)
- [4. Research-Status Object (research-status.json)](#4-research-status-object-research-statusjson)

---

## 1. Report Object (report.json)

The report is a single JSON object injected into the page template (see
`references/rendering-report.md` §Template Variables for the injection
mechanism).

### 1.1 Top-level shape

```jsonc
{
	"schema_version": 1,
	"report_id": "tool-update-review-20260704T143012",   // stable within a session run
	"generated_at": "2026-07-04T14:30:12Z",              // ISO-8601 UTC
	"machine": {
		"arch":    "x86_64",         // "x86_64" | "arm64"
		"os":      "macOS 15.3",
		"hostname": "your-mac"
	},
	"summary": {
		"total_outdated":      14,
		"incompatible_count":  2,
		"warning_count":       3,
		"suggestions_count":   7
	},
	// ── Repo freshness (see references/collection.md §Repo Freshness) ──
	"repo_context": {
		"macos_setup": {
			"up_to_date": true,             // HEAD == origin/master
			"ahead": 0, "behind": 0,
			"recent_commits": [              // last 20, oneline — research context only
				"37e0774 Run install_podman_intel after mise runtimes; guard missing uv",
				"1639adb Update dotfiles"
			]
		},
		"dotfiles": {
			"up_to_date": true,
			"ahead": 0, "behind": 0,
			"recent_commits": [ "81f5be1 tool-update-review: add Results view and live status tracking" ]
		}
	},
	"tools": [ /* Tool[] — see §1.2 below */ ]
}
```

### 1.2 Tool object

```jsonc
{
	// ── Identity ──────────────────────────────────────────────────────
	"id":      "brew:podman",           // stable: "{source}:{name}"
	"name":    "podman",
	"source":  "brew",                  // "brew" | "cask" | "mise" | "standalone" | "macos"
	"pinned":  true,                    // brew pin active

	// ── Versions ──────────────────────────────────────────────────────
	"current_version": "4.9.3",
	"latest_version":  "5.5.1",

	// ── Risk assessment (assembly-computed — see §1.4 below) ────────────
	"risk_level": "elevated",           // "low" | "elevated"

	// ── Research ──────────────────────────────────────────────────────
	"research_error": null,             // null | string — set if subagent failed
	// Agent-written; ≤6 concise items covering the whole current→latest
	// range. Each is one atomic fact — split a compound changelog bullet
	// ("added X, plus fixed CVE Y") into separate items before writing them
	// here, never one item bundling both (references/research.md's quality
	// bar). "category" and "severity" are independent axes, both assigned by
	// research (not derived client-side from keywords — a heuristic can't
	// tell topic apart from urgency, which is exactly how a low-profile
	// security item used to get bucketed into Notes just because it read as
	// minor). "category" is topic-only: which of the four content groups
	// (references/rendering-report.md §Page Layout) this fact belongs in.
	// "severity" reuses relevancy's vocabulary (`"info" | "notable" |
	// "warning" | "incompatible"`) and drives this item's own color/icon
	// within its category — it never changes which category the item lands
	// in.
	"headliners": [
		{
			"text":     "Migrated networking to netavark/aardvark stack",
			"category": "features",           // "security" | "fixes" | "features" | "notes"
			"severity": "notable"
		},
		{
			"text":     "libkrun dependency now required (Apple Silicon only)",
			"category": "fixes",
			"severity": "warning"
		}
	],

	// Categories where the vendor's own release notes for this range are
	// pure non-detail ("This release includes security improvements.
	// Updating is recommended.", nothing technical ever published) —
	// references/research.md's quality bar forbids authoring fake bullets to
	// fill this gap. Listing the category here renders one small compact tag
	// in its place (references/rendering-report.md §Page Layout) instead of
	// an empty or manufactured group. Rare: most tools never set this.
	"vendor_silent_categories": [],   // e.g. ["security"]

	"links": [
		{
			"type":  "changelog",           // "changelog" | "release" | "blog"
			"label": "CHANGELOG.md",
			"url":   "https://github.com/containers/podman/blob/main/CHANGELOG.md"
			// "embedded_content" (below) is absent here — this link is a
			// normal, human-navigable web page, so it just opens in a new tab.
		},
		{
			// Fallback shape: used only when no stable, browsable destination
			// exists for the source content at all (e.g. the actual changelog
			// text was only available inside a downloaded release tarball, or
			// as a raw non-browsable file) — never as a substitute for a real
			// URL that does exist. "url" is omitted or null in this case; a
			// link that would otherwise dead-end or trigger a file download
			// is worse than no link.
			"type":  "changelog",
			"label": "stunnel 5.79 NEWS (from source tarball — no stable webpage)",
			"url":   null,
			"embedded_content": "### 5.79 (2026-06-20)\n\n- Fixed X\n- Changed Y\n\n[upstream advisory](https://...)"
			// Markdown text, the actually-relevant excerpt only (not the
			// whole file) — the page renders it in a modal instead of
			// navigating externally. Any links inside the markdown render
			// as normal links (references/rendering-report.md §Page Layout).
		}
	],

	// ── Config status ─────────────────────────────────────────────────
	// Cross-referenced against recent commit history (repo_context, top
	// level) and the changelog.md audit trail — see references/research.md
	// §Config Status for how this is computed. Answers "has this tool's
	// config already been dealt with, and does that still hold?"
	"config_status": {
		"state":  "needs_attention",     // "up_to_date" | "needs_attention" | "unknown"
		"detail": "Brewfile:84's Intel-incompatibility comment (commit a1b2c3d, 2026-05-02) was written against v5.0.0's libkrun requirement. Headliners above show v5.5 additionally requires X — re-verify the pin rationale still covers it.",
		"evidence": [
			"commit a1b2c3d — \"Pin podman to 4.x on Intel (libkrun ARM-only)\"",
			"~/.local/state/tool-update-review/changelog.md — 2026-05-02 entry"
		]
	},

	// ── Relevancy ─────────────────────────────────────────────────────
	// A relevancy item connects a genuine changelog fact to a concrete
	// effect on this setup — `motivating_change` is required and must name
	// an actual changelog item, never null/"none found"/"not a
	// changelog-driven finding". A finding with no real motivating change
	// belongs in `context` below, not here with a hollow motivating_change.
	"relevancy": [
		{
			"category": "fixes",            // "security" | "fixes" | "features" | "notes" —
			                                 // topic only, independent of severity (see headliners above)
			"severity": "incompatible",     // "info" | "notable" | "warning" | "incompatible"
			"summary":  "Requires Apple Silicon (libkrun); Intel Mac not supported in v5+",
			"detail":   "Longer explanation with the concrete failure mode.",
			"evidence": [                   // file paths with optional :line suffix
				"Brewfile:84",
				"intel.Brewfile"
			],
			"motivating_change": "v5.0.0 release notes — 'libkrun is now a required dependency'"
		}
	],

	// ── Context ───────────────────────────────────────────────────────
	// Present-tense repo-scope/usage/locality/config-verification notes —
	// "is this tool even used here", "does the claimed touchpoint actually
	// hold", "does existing script logic still cover this release" —
	// distinct from relevancy because there's no changelog fact driving
	// them (motivating_change would be null there). Distinct from
	// config_status, which is about audit-trail history ("was this handled
	// by a prior commit"), not present-tense scope. No severity — these
	// aren't change-risk items and shouldn't compete visually with ones
	// that are (see references/rendering-report.md §Page Layout — rendered
	// collapsed-by-default, title-only until expanded, since they tend to
	// run long: a one-line claim followed by a full paragraph of evidence).
	"context": [
		{
			"title":    "No bespoke touchpoint anywhere in this repo",
			"detail":   "Grepped tasks/*.sh, Brewfile, and dotfiles/ for azure-cli references; found none — this tool has no setup logic or config tracking it beyond the plain Brewfile line.",
			"evidence": ["Brewfile:112"],
			"link":     null                // optional; same shape as a links[] entry when relevant
		}
	],

	// ── Release inventory ─────────────────────────────────────────────
	// Bookkeeping about the release cadence itself within current→latest —
	// "which releases exist in this range", not a claim about what any of
	// them changed. Keep changelog content in headliners/relevancy/context;
	// this is purely the version list, rendered as a short one-line-per-
	// release list (not collapsed — these are already short).
	"release_inventory": [
		{ "version": "2026.06.09", "link": "https://github.com/yt-dlp/yt-dlp/releases/tag/2026.06.09" },
		{ "version": "2026.07.04", "link": "https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04" }
	],

	// ── Suggestions ───────────────────────────────────────────────────
	// Every tool gets exactly one synthesized "upgrade" suggestion (added
	// during assembly, not by the research subagent) plus zero or more
	// research-authored "edit" suggestions.
	"suggestions": [
		{
			"id":      "cask:wireshark-app:upgrade",       // always "{source}:{name}:upgrade" for the baseline
			"kind":    "upgrade",                           // "edit" (default, omittable) | "upgrade"
			"title":   "Upgrade wireshark-app 3.4.6 → 4.6.6",
			"target_files": [],                             // always empty for kind "upgrade"
			"command": "brew upgrade --cask wireshark-app", // what actually runs when accepted (see references/apply.md §Executing Upgrade Suggestions)
			"auto_runnable": true,                          // may the session execute `command` itself? (subject to the
			                                                 // report-level auto_run_upgrades toggle — see §2 below /
			                                                 // references/apply.md)
			"needs_sudo": true,                              // hints the askpass path is needed — this cask's pkg
			                                                 // installers (ChmodBPF, PATH helper) require admin
			"rationale": "Picks up the changes described in headliners[] above.",
			"motivating_link": {
				"type":  "release",
				"label": "Wireshark 4.6.0 Release Notes",
				"url":   "https://www.wireshark.org/docs/relnotes/wireshark-4.6.0.html"
			},
			"diff_preview": null                            // not applicable to kind "upgrade"
		},
		{
			"id":      "brew:podman:upgrade",
			"kind":    "upgrade",
			"title":   "Upgrade podman 4.9.3 → 5.5.1",
			"target_files": [],
			"command": "brew upgrade podman",
			"auto_runnable": true,                           // plain package upgrade — normal rules apply
			"needs_sudo": false,
			"rationale": "Picks up the changes described in headliners[] above.",
			"motivating_link": {
				"type":  "release",
				"label": "podman v5.0.0",
				"url":   "https://github.com/containers/podman/releases/tag/v5.0.0"
			},
			"diff_preview": null
		},
		{
			"id":    "brew:podman:fix-machine-init-flags",  // bespoke-setup fix — see references/research.md §Bespoke Setup Testing
			"kind":  "edit",
			"title": "Update install_podman_intel for v5's changed `podman machine init` flags",
			"target_files": [
				{
					"path":        "tasks/install.sh",
					"description": "install_podman_intel: replace the removed --image-path flag with --rootful"
				}
			],
			"rationale": "v5.0.0 removed the --image-path flag install_podman_intel currently passes to `podman machine init`. Verified empirically (not just inferred from the changelog): ran `podman machine init --rootful pm-verify-tmp` in a scratch-named machine, confirmed it succeeds where the old flag combination now errors, then removed the scratch machine (`podman machine rm -f pm-verify-tmp`) — the user's real `podman-machine-default` was never touched.",
			"motivating_link": {
				"type":  "release",
				"label": "podman v5.0.0",
				"url":   "https://github.com/containers/podman/releases/tag/v5.0.0"
			},
			"diff_preview": "-\tpodman machine init --image-path \"${image_path}\"\n+\tpodman machine init --rootful"
		},
		{
			"id":    "brew:podman:keep-pin-add-comment",   // unique within report
			"kind":  "edit",                                 // may be omitted — "edit" is the default
			"title": "Retain pin; annotate Brewfile with Intel-incompatibility note",
			"target_files": [
				{
					"path":        "Brewfile",
					"description": "Add inline comment above podman line explaining pin rationale"
				}
			],
			"rationale": "Documents why the package is held so a future cleanup doesn't unpin it blindly.",
			"motivating_link": {
				"type":  "release",
				"label": "podman v5.0.0",
				"url":   "https://github.com/containers/podman/releases/tag/v5.0.0"
			},
			"diff_preview": "-brew \"podman\"\n+# Intel only — v5+ requires libkrun (ARM). Keep at 4.x.\n+brew \"podman\""
		}
	]
}
```

### 1.3 Source vocabulary

`brew` = Brewfile `brew` line, `cask` = Brewfile `cask` line, `mise` =
`mise outdated` runtime, `standalone` = a CLI genuinely unmanaged by brew,
version-checked by running `--version` and comparing to the latest release,
`macos` = a `softwareupdate -l` entry (system OS/app updates — Safari, Xcode
CLT, the OS itself), `brew-health` = a `brew doctor` environment-health
finding (deprecated cask, orphaned/unlinked keg, untrusted tap, missing
dependency — **not** a version delta; see `references/collection.md`
§Brew-Health Collection and `references/assembly.md` §Brew-Health Assembly).
`current_version` for `macos` entries is the running `sw_vers
-productVersion`, not a per-update version — research should treat it as
"what's currently installed system-wide" context rather than a strict
current→latest delta for that specific update. `brew-health` findings have
**no** `current_version`/`latest_version` at all (both `null`) and get **no**
synthesized `upgrade` baseline — their action is the finding's own
remediation (`references/assembly.md` §Brew-Health Assembly).

Note: `claude CLI` and `codex CLI` are **not** current examples of
`standalone` — both are plain Homebrew casks (`claude-code@latest`, `codex`)
with `auto_updates` unset and a real resolved version (not the `:latest`
sentinel), so `brew outdated --greedy` (used by `collect.sh` specifically to
also catch `auto_updates: true`/`version :latest` casks — e.g. the separate
`claude` desktop-app cask) tracks them correctly on its own. `standalone`
currently has no active tool using it; the source type stays in the schema
for a future CLI that's genuinely installed outside brew. If you're re-adding
a standalone check for either, first re-verify with `brew info --cask --json=v2
<token>` that the cask hasn't reverted to being untracked — don't assume the
old rationale still holds.

Node gets a richer `headliners[]` list (security advisories, notable API
changes); other mise runtimes get a coarser treatment (two or three bullets
max, focus on breaking changes only).

### 1.4 `risk_level` semantics

Computed entirely by `scripts/assemble.py` from signals already present in
the assembled Tool object — not a subjective per-tool judgment call left to
the research subagent, so every run applies the same rule the same way.
`"elevated"` if any of: `pinned` is true, any `relevancy[]` item has
severity `warning`/`incompatible`, any `edit`-kind suggestion exists for
this tool, or the version delta is a major bump (semver-aware — an
unparseable version pair defaults to `"elevated"`, since an unknown delta
size is never treated as low-risk). Otherwise `"low"`. Used by assembly to
pre-set the baseline `upgrade` suggestion's initial decision (§2 below) — a
`"low"` tool starts `accept`ed instead of undecided; an `"elevated"` one
starts undecided as before. This only ever affects the *baseline* `upgrade`
suggestion; research-authored `edit` suggestions always start undecided
regardless of the tool's risk_level.

See `references/assembly.md` §Risk Level for the computation's place in
`assemble.py`'s flow, and `references/rendering-report.md` §Page Layout for
how `risk_level`-driven pre-accept state renders.

### 1.5 `config_status.state` semantics

(See `references/research.md` §Config Status for how this is computed.)

- `up_to_date` — a prior commit or `changelog.md` entry already addressed
  this tool at a version ≥ today's `latest_version`. Purely confirmatory;
  render it quietly (small green check), don't make it loud — the point is
  reassurance that nothing was missed, not a new thing to review.
- `needs_attention` — this tool's config *was* touched for an older
  version, but something in this run's `headliners`/`relevancy` suggests
  that fix may no longer be complete or correct (e.g. a pin comment
  reasoned about v5.0's requirement, and v5.5 changed the requirement
  again). Render as a warning banner — this is exactly the kind of thing a
  periodic review should catch that a one-off glance wouldn't. **Always
  pairs with at least one suggestion that addresses it** — a banner telling
  the user something might be stale, with nothing offered to resolve it,
  just relocates the "someone should check this" burden onto them instead
  of doing the check. If research concludes there's nothing to change
  after all, that's `up_to_date`, not `needs_attention`.
- `unknown` (default) — no evidence either way (no matching commit, no
  changelog entry). The common case for a first-time review of a tool.
  Render neutrally, same as today (no badge).

### 1.6 `kind: "upgrade"` field semantics

(See `references/apply.md` §Executing Upgrade Suggestions for the execution
mechanism.)

- `auto_runnable` (bool, default `true`): whether the apply step may execute
  `command` itself at all, subject to the report-level `auto_run_upgrades`
  toggle. This stays `true` even for tools with bespoke `tasks/*.sh` setup
  (podman-style) — the baseline suggestion only ever runs the plain
  package-manager command (`brew upgrade podman`), which is never itself
  the risky part. Set `false` only when *no command at all* is safe to
  auto-run for this tool (rare — see `references/research.md` §Bespoke
  Setup Testing for the ordinary case, which is a separate tested
  `kind: "edit"` suggestion, not this flag). When `false`, a `manual_reason`
  string is required, and the session always just tells the user what to
  run, never executes anything for that suggestion.
- `needs_sudo` (bool, default `false`): hints that `command` may invoke a
  privileged installer (e.g. a cask shipping a `pkg` artifact). When `true`
  and the suggestion is accepted with auto-run enabled, the session routes
  through the askpass mechanism (`references/apply.md` §Executing Upgrade
  Suggestions) instead of a bare subprocess call. Default to `true` when
  genuinely unsure — assuming `false` and hitting an un-satisfiable
  password prompt is worse than an unnecessary askpass popup.

---

## 2. Feedback Object (feedback.json)

Written atomically by the server to `{session_dir}/feedback.json`.

### 2.1 Schema

```jsonc
{
	"report_id":    "tool-update-review-20260704T143012",
	"submitted_at": "2026-07-04T14:52:07Z",

	// Global toggle (checkbox on the page, near Submit) — default true.
	// Governs every accepted kind:"upgrade" suggestion with auto_runnable
	// true; see references/apply.md §Executing Upgrade Suggestions.
	// Suggestions with auto_runnable:false are never affected by this
	// toggle — they're always manual, always.
	"auto_run_upgrades": true,

	// One entry per suggestion the user has a decision recorded for.
	// Absent key = undecided — but "undecided" is no longer always the
	// page's *starting* state (see below), only the state feedback.json
	// records when the user never touched a control either way.
	"decisions": {
		"brew:podman:keep-pin-add-comment": {
			"decision": "accept",           // "accept" | "reject" | "discuss"
			"comment":  ""                  // may be non-empty for any decision
		}
	},

	// A baseline "upgrade" suggestion on a "low" risk_level (§1.4) tool
	// renders pre-accepted (Accept button already shown active) instead of
	// undecided — the page's default state, computed at render time from
	// each tool's risk_level, not something feedback.json itself encodes
	// specially. The user can still flip it to reject/discuss like any
	// other decision; if they never touch it, it submits as a normal
	// "accept" entry here, indistinguishable from one they clicked
	// themselves. Only the baseline upgrade suggestion ever starts this
	// way — research-authored "edit" suggestions always start undecided.

	// Optional free-text comment anchored to a tool (not a suggestion).
	"tool_comments": {
		"mise:node": "Hold off until the project upgrades its .nvmrc"
	},

	"overall_comment": ""
}
```

### 2.2 `decision` semantics

- `accept` — for `kind: "edit"`, session applies the edit immediately,
  following dotfiles-submodule conventions. For `kind: "upgrade"`: if
  `auto_runnable` is `false`, always manual regardless of any toggle — the
  session tells the user the `command` and polls for completion, never runs
  it. If `auto_runnable` is `true`, behavior depends on the report-level
  `auto_run_upgrades` toggle (default `true`, see
  `references/apply.md` §Executing Upgrade Suggestions): when on, the
  session executes `command` itself (via the askpass mechanism if
  `needs_sudo`); when off, same manual/poll behavior as
  `auto_runnable: false`.
- `reject` — session skips; records in summary.
- `discuss` — session does not apply; surfaces the suggestion + comment as a
  follow-up dialogue item after the apply pass.
- **Tool comments can themselves generate new decisions.** If investigating
  a `tool_comments` entry (or a `discuss` comment) concludes a concrete
  change is warranted, the session does **not** apply it directly — it adds
  a new suggestion to the live action list (same shape as any other
  suggestion, `id` like `{source}:{name}:from-comment-{slug}`) in `pending`
  state and surfaces it in the Results view for an explicit accept/reject,
  exactly like an original suggestion. The action that triggered the
  investigation (e.g. "Investigate: {tool} — {comment}") stays `running`
  until that follow-on decision is made, then completes reflecting the
  outcome. Nothing gets applied to the repo or the machine on the strength
  of a free-text comment alone.

---

## 3. Status Object (status.json)

### 3.1 File location and write pattern

```
{session_dir}/status.json          <- live file; page polls this
{session_dir}/status.json.tmp      <- transient; os.replace()d into place
```

The server reads the file on every `GET /status` request. The session writes
it atomically: open `.tmp`, `json.dump` + trailing newline, `os.replace()`
to final path. The page tolerates 404 (server not yet written first status)
and handles partial/older-shaped responses gracefully by defaulting every
field it reads (`data.summary || {}`, `?? 0`, etc.) rather than branching on
`schema_version` — there's no code path that actually inspects that field
today; it exists so a future incompatible schema bump has somewhere to
signal itself.

### 3.2 Schema (schema_version 2)

```jsonc
{
  "schema_version": 2,
  "report_id": "tool-update-review-20260704T143012",

  // "applying" | "discussing" | "done"
  // "applying"   — session executing accepted suggestion actions
  // "discussing" — apply pass complete, session surfacing discuss items in
  //                conversation (user may still be interacting with session)
  // "done"       — all work complete; done flag true; Finish button enabled
  "phase": "applying",

  "started_at": "2026-07-04T14:52:10Z",  // when session began apply pass
  "written_at": "2026-07-04T14:53:42Z",  // timestamp of this write (staleness check)

  "actions": [
    {
      // Suggestion-backed: the suggestion id from feedback.json decisions
      // Synthetic (non-suggestion work): "{verb}:{context}" format
      //   commit:dotfiles       — git commit in dotfiles submodule
      //   commit:macos-setup    — git commit in parent repo
      // Add more synthetic ids as needed; the page treats them identically.
      "id": "brew:podman:keep-pin-add-comment",

      // Human-readable one-liner shown in the action list
      "label": "Add pin comment to Brewfile",

      // Mirrors the user's decision from feedback.json, null for synthetic actions
      "decision": "accept",   // "accept" | "reject" | "discuss" | null

      // "pending" — not yet started
      // "running" — actively executing (spinner shown)
      // "done"    — completed successfully
      // "failed"  — execution error (detail lines explain)
      // "skipped" — not executed (decision was reject/discuss/undecided, or
      //             a dependency failed)
      "state": "running",

      "started_at": "2026-07-04T14:52:11Z",  // ISO UTC; null if not started
      "finished_at": null,                    // ISO UTC; null if not finished

      // Written on transition to done/failed/skipped; null while pending/running
      "note": null,    // e.g. "Committed as abc1234 in dotfiles"

      // Last ≤10 lines of relevant output; empty array if none
      // e.g. diff hunk applied, commit hash, error message
      "detail": [],

      // Present only on a "failed" action (or one the user has commented
      // on) — same Turn shape as pending_followups' turns[] below (§3.3).
      // This is what backs the failed-action inline retry/debug UI
      // (references/rendering-results.md §Turn-Based Threads): a failed
      // action isn't just a dead end, it's a thread the user can add a
      // debug comment to or retry from, using the exact same mechanism a
      // followup uses. Absent/empty for actions nothing has been added to
      // yet.
      "thread": []
    }
    // ... more actions in execution order
  ],

  // Threads raised live, mid-apply — by investigating a tool_comments entry
  // or a discuss comment (user-initiated), or by the session itself hitting
  // something during apply that needs an explicit decision, not just a
  // plain success/fail outcome (agent-initiated —
  // references/apply.md §Turn-Based Threads).
  // Each is a conversation, not a one-shot decision: turns accumulate, the
  // newest is what's "active", and a thread is never permanently closed to
  // new input — even one already resolved can get another out-of-turn turn
  // later (e.g. an unrelated Gatekeeper popup noticed after an upgrade the
  // followup was originally about had already been applied).
  "pending_followups": [
    {
      "id": "mise:node:from-comment-hold-for-nvmrc",
      "kind": "edit",
      "tool_id": "mise:node",              // originating tool
      "tool_name": "node",                 // display name for the origin line
      "origin": "user_comment",            // "user_comment" | "agent_initiated"
      // Purely descriptive of the latest concluded decision — never a hard
      // "closed" gate. "pending": awaiting a decision on the latest turn.
      // "applied"/"rejected": last decision was acted on, but the thread
      // still accepts new turns (see turns[] below).
      "resolution": "pending",            // "pending" | "applied" | "rejected"
      "turns": [
        {
          "turn": 1,
          "author": "user",                 // "user" | "agent"
          "at": "2026-07-04T14:53:00Z",
          "decision": "discuss",            // "accept" | "reject" | "discuss" | null (agent turns: null)
          "comment": "Hold off until the project upgrades its .nvmrc",
          "action_taken": null               // agent turns: what it did, e.g. "Applied edit, committed abc1234"
        }
        // ... more turns, newest last
      ]
      // ...rest of the fields are identical to a normal Suggestion object
      // (title, rationale, target_files/diff_preview or command,
      // auto_runnable/needs_sudo/manual_reason, motivating_link) — a
      // followup is a suggestion with a conversation attached, not a
      // different kind of thing.
    }
  ],

  // Free-form markdown text written by the session at done time — pure
  // narrative summary now (applied edits with commit hashes, failed
  // actions with remediation hints for the ones that stay unresolved,
  // rejected/undecided items). It does NOT need to mention followup counts
  // or status — those live entirely in pending_followups/the merged status
  // header (references/rendering-results.md §Element Detail) now, computed
  // live on every poll, so recap can never go stale relative to them the
  // way a frozen "N pending" sentence used to.
  // Empty string until the session writes it (phase "done"). Rendered as
  // parsed markdown (references/rendering-results.md §Markdown Rendering),
  // not preformatted text.
  "recap": "",

  // Each element is the markdown text of one changelog entry appended to
  // ${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md
  // during this session. Empty list until entries are written.
  // Example element: "## 2026-07-04\n### brew: gh 2.48.0 → 2.52.0\n..."
  "changelog_entries": [],

  // Written at done time. Counts are over user decisions (from feedback),
  // not over action execution states. "failed" counts execution failures
  // among accepted suggestions (not user rejections).
  "summary": {
    "applied":   3,   // accepted + execution succeeded
    "rejected":  2,   // user rejected
    "discussed": 1,   // user selected discuss (not applied, raised in session)
    "undecided": 0,   // no decision made (skipped)
    "failed":    0    // accepted but execution failed
  },

  // Terminal signal. The page enables the Finish button when true.
  // Set to true only in the same write that sets phase "done".
  "done": false
}
```

### 3.3 The Turn object

A single shared mechanism backs two surfaces: a `pending_followups` entry
(§3.2 above) and a `"failed"` action's `thread` field (§3.2 above). Both are
an array of **Turn** objects:

```jsonc
{
  "turn": 1,
  "author": "user",             // "user" | "agent"
  "at": "2026-07-04T14:53:00Z",
  "decision": "discuss",        // "accept" | "reject" | "discuss" | null — agent turns: null
  "comment": "Hold off until the project upgrades its .nvmrc",
  "action_taken": null          // agent turns only: what it did, e.g. "Applied edit, committed abc1234"
}
```

This shape is cited by both halves of the turn-based-thread feature:
`references/rendering-results.md` §Turn-Based Threads (the rendering/UI
half — click-to-expand, decoupled decision-select-from-submit, multi-turn
history rendering) and `references/apply.md` §Turn-Based Threads (the
session-side half — polling `followup_turns.json` for new user turns and
appending agent turns directly into `status.json`).

### 3.4 Action ordering

The `actions` array is in execution order: accepted suggestions first (in
the order they appear in the feedback), then synthetic commit actions, then
rejected/undecided items (state "skipped"). The page renders them in array
order.

### 3.5 State transitions

```
pending → running → done
                 → failed
         skipped  (set directly from pending, no running state)
```

A single session write covers one transition at a time (e.g.
pending→running, then running→done in the next write). Never write both in
one atomic op; the page needs to observe "running" to show the spinner.

---

## 4. Research-Status Object (research-status.json)

Written before/during research (see `references/collection.md` and
`references/research.md` for when each phase writes it), polled by the
pre-report loading page (`references/rendering-results.md` §Loading Page,
`references/server-and-session.md` §Pre-Report Status).

```jsonc
{
  "phase": "collecting",   // "collecting" | "researching" | "assembling" | "ready"
  "started_at": "2026-07-06T08:58:07Z",
  "written_at": "2026-07-06T08:59:41Z",

  // Populated once tiering has grouped the candidates; empty during
  // "collecting". One entry per research subagent (both individual-focus
  // and batched-by-category groups).
  "groups": [
    {
      "id": "01-podman",             // matches research/{id}.json's filename
      "label": "podman",             // display label — tool name, or a short
                                      // category label for a batch group
      "state": "done",               // "pending" | "running" | "done" | "failed"
      "tool_ids": ["brew:podman"]     // every group has ≥1; batches have several
    }
  ]
}
```

Same write pattern as `status.json` (§3.1): `.tmp` + `os.replace()`. `phase`
transitions `collecting → researching → assembling → ready`; `ready` is
written only after `render.py` has actually produced `index.html` — it's
the exact signal the loading page's poll is waiting for.
