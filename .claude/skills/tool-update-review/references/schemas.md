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
  - [1.7 `kind: "watch-item"` field semantics](#17-kind-watch-item-field-semantics)
  - [1.8 `version_delta` semantics](#18-version_delta-semantics)
  - [1.9 `security` semantics](#19-security-semantics)
  - [1.10 `review_bucket` semantics](#110-review_bucket-semantics)
  - [1.11 `highlights` semantics](#111-highlights-semantics)
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
		"total_outdated":      14,       // version-outdated tools only (excludes brew-health)
		"incompatible_count":  2,
		"warning_count":       3,
		"suggestions_count":   7,
		"health_count":        3,        // source "brew-health" tools (references/assembly.md §Brew-Health Assembly)
		"skill_drift_count":   2,        // source "skill-drift" tools (references/assembly.md §Skill-Drift Assembly)

		// ── Triage rollups (assembly-computed; additive, so a page must
		// tolerate all three being absent when a user reopens an older
		// session dir) ──
		"by_delta":  {                   // §1.8 — version updates ONLY
			"major": 2, "minor": 6, "patch": 5, "revision": 1, "unknown": 0
		},                               // sum (14) == total_outdated, always
		"by_bucket": {                   // §1.10 — EVERY tool, brew-health and skill-drift included
			"security_auto": 2, "security_mixed": 4, "attention": 6, "routine": 7
		},                               // sum (19) == len(tools)
		                                 //          == total_outdated + health_count + skill_drift_count
		"security": {                    // §1.9
			"cve_count":                9,   // size of the UNION of cve_ids across tools, never the sum
			"severity_counts": {             // over that same union — never the sum of the per-tool counts
				"critical": 1, "high": 2, "medium": 0, "low": 0, "unknown": 6
			},                               // sum (9) == cve_count above
			"tools_with_security":      6,
			"auto_count":               2,   // == by_bucket.security_auto
			"mixed_count":              4,   // == by_bucket.security_mixed
			"tools_with_unlisted_cves": 1    // vendor claims more advisories than we could extract ids for
		}
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
	// ── Highlights (assembly-computed, §1.11 below) ──
	// "The biggest decision drivers / inputs needed / major patches",
	// ranked by a fixed deterministic score — never a per-run LLM judgment.
	// At most 8 entries; may be absent on an older report.
	"highlights": [
		{
			"tool_id":  "cask:google-chrome",
			"title":    "google-chrome 150.0.7871.129 → 151.0.7922.174",
			"why":      "Chrome 151 shipped 370 security fixes, 7 of them Critical…",   // ≤ 220 chars
			"why_source": "relevancy_other", // which branch produced `why` (§1.11)
			"why_ref":    "rel:0",           // string|null — the content item it came from
			"severity": "warning",           // relevancy's vocabulary, so the page reuses one palette
			"suggestion_ids": [               // every suggestion on the tool, in array order
				"cask:google-chrome:upgrade",
				"cask:google-chrome:add-intel-note"
			],
			"reasons":  ["config_stale", "warning_finding", "major_bump", "cves"],  // stable codes → chips
			"score":    250                   // exposed for debuggability; the UI need not show it
		}
	],
	"tools": [ /* Tool[] — see §1.2 below */ ]
}
```

**`by_bucket` and `by_delta` have different denominators, deliberately.**
`by_delta` counts the version updates only (14 above); `by_bucket` counts
**every Tool object exactly once** — brew-health findings and skill-drift
findings included (19 above) — because `review_bucket` is defined for every
Tool while a non-version finding has no version delta to classify. That is
the durable statement of the rule: `by_bucket` sums to `len(tools)`, and the
arithmetic form `total_outdated + health_count + skill_drift_count` is the
same number spelled out by source. Every non-version source added in future
lengthens that sum by its own count; `sum(by_delta) == total_outdated` is
untouched by any of them. Never mix the two denominators in one percentage —
`by_bucket.routine / total_outdated` is a number that means nothing. See
§1.10 and `references/assembly.md` §Summary Counts and Output for the
invariants that hold instead.

**And `by_bucket.attention` is not "updates needing attention".** Every
non-version finding that is not `expected` lands there too — on the live run
12 drifted skills, taking `attention` from 12 to 24 with no update involved.
The page never renders the raw number (it filters non-version sources out of
the Overview chips and gives drift its own band), so this is a trap for any
*other* consumer of `report.json`. `summary` carries no version-only bucket
count; a consumer that needs one counts `review_bucket` over the tools whose
`source` is not a non-version source itself.

### 1.2 Tool object

```jsonc
{
	// ── Identity ──────────────────────────────────────────────────────
	"id":      "brew:podman",           // stable: "{source}:{name}"
	"name":    "podman",
	"source":  "brew",                  // "brew" | "cask" | "mise" | "standalone" | "macos"
	                                    //   | "brew-health" | "skill-drift"  (the two
	                                    //   non-version sources — §1.3)
	"pinned":  true,                    // brew pin active

	// ── Versions ──────────────────────────────────────────────────────
	"current_version": "4.9.3",
	"latest_version":  "5.5.1",
	// Assembly-computed classification of the pair above (§1.8 below).
	// One shared classifier, so the report never carries two different
	// answers to "how big is this bump".
	"version_delta":      "major",      // "major"|"minor"|"patch"|"revision"|"unknown"
	"version_scheme":     "semver",     // "semver"|"calver"|"date"|"opaque"|"none"
	"version_delta_note": "index 0",    // one short phrase; tooltip text and debugging handle

	// ── Risk assessment (assembly-computed — see §1.4 below) ────────────
	"risk_level": "elevated",           // "low" | "elevated"

	// ── Security content of this update (§1.9) ──────────────────────────
	// Mixed provenance, and this is the first object in the report where that
	// is true: cve_ids/cve_count/cve_claimed_count/has_security/security_only/
	// impact/severity_counts are assembly-COMPUTED, while cve_severities and
	// notable are research-SUPPLIED and assembly-validated. A consumer reads
	// them all the same way; a writer must not.
	"security": {
		"cve_ids":           ["CVE-2026-9595", "CVE-2026-12143"],  // deduped, sorted by (year, sequence)
		"cve_count":         2,          // ALWAYS len(cve_ids) — id-backed, never a claim
		"cve_claimed_count": 33,         // int|null — the vendor's own largest stated count
		"has_security":      true,
		"security_only":     false,
		"impact":            "possible", // "none" | "possible" | "unknown"
		"severity_counts": {             // rolled up over cve_ids; sums to cve_count, always
			"critical": 0, "high": 1, "medium": 0, "low": 0, "unknown": 1
		},
		"cve_severities": [              // the graded subset only; ids not here are `unknown`
			{"cve_id": "CVE-2026-9595", "severity": "high", "basis": "vendor"}
		],
		"notable": [                     // ≤ 3; affects_me first, then worst. EMPTY IS THE COMMON CASE.
			{
				"cve_id":      "CVE-2026-9595",  // string|null when no CVE was assigned
				"advisory_id": null,             // string|null — "wnpa-sec-2026-87", "TS-2026-011"
				"severity":    "high",           // critical|high|medium|low|unknown — vocabulary UNCHANGED
				"summary":     "Lands on the gh commands this machine pre-approves for agents.",
				"affects_me":  true,             // a concrete touchpoint here — never auto-derived
				"source_ref":  "rel:0"           // string|null — the item it restates (§1.11)
			}
		]
	},

	// ── Review effort (assembly-computed, §1.10) ────────────────────────
	// "security_auto" | "security_mixed" | "attention" | "routine".
	// A review-effort axis, orthogonal to `source` — the page groups
	// brew-health cards by source, never by bucket.
	"review_bucket": "security_mixed",

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
	// research-authored "edit" suggestions, plus zero or more
	// research-proposed "watch-item" suggestions (§1.7 below;
	// references/research.md §Watch Items (Proposing)) — a standing,
	// forward-looking concern the user can accept/reject in the review UI,
	// distinct from a one-off "edit" fix.
	"suggestions": [
		{
			"id":      "cask:wireshark-app:upgrade",       // always "{source}:{name}:upgrade" for the baseline
			"kind":    "upgrade",                           // "edit" (default, omittable) | "upgrade"
			"title":   "Upgrade wireshark-app 3.4.6 → 4.6.6",
			"target_files": [],                             // always empty for kind "upgrade"
			"command": "brew upgrade --cask wireshark-app", // what actually runs when accepted (see references/apply.md §Executing Upgrade Suggestions)
			"target_version": "4.6.6",                      // the reviewed version apply must land on (§1.6, WP5/I2)
			"version_pinned": false,                        // brew/cask can't pin a version in `command` — apply
			                                                 // preflight/verify-checks against target_version instead
			"auto_runnable": true,                          // may the session execute `command` itself? (subject to the
			                                                 // report-level auto_run_upgrades toggle — see §2 below /
			                                                 // references/apply.md)
			"needs_sudo": true,                              // hints the askpass path is needed — this cask's pkg
			                                                 // installers (ChmodBPF, PATH helper) require admin
			"pre_accept": true,                              // assembly-computed; the page reads this instead of
			                                                 // re-deriving pre-accept from risk_level (§1.6)
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
			"target_version": "5.5.1",
			"version_pinned": false,
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
		},
		{
			// A proposed watch item (§1.7 below) — a standing, forward-looking
			// concern, not a one-off fix. No target_files/command/diff_preview:
			// there is nothing to apply here except the watch-item entry
			// itself, written to watch-items.json on accept
			// (references/apply.md §Watch Items (Writing)).
			"id":    "cask:cursor-cli:watch-shell-integration",  // "{source}:{name}:watch-{slug}"
			"kind":  "watch-item",
			"title": "Watch: shell-integration / session recording",
			"target_files": [],
			"command": null,
			"auto_runnable": false,           // never auto-run — accepting this writes a
			                                  // watch-items.json entry, nothing else
			"needs_sudo": false,
			"rationale": "install-shell-integration execs `agent record` on every new shell via ~/.zshrc, undocumented data handling; the user implemented an on-demand cursor-record() function instead. Worth flagging if a future release touches this again.",
			"motivating_link": null,
			"diff_preview": null,
			"watch_topic": "shell-integration / session recording",   // → watch-items.json's `topic` on accept
			"watch_note":  "User wants any change to cursor-agent's shell hook or `agent record` behavior called out — see 2026-07-06's investigation: install-shell-integration execs `agent record` on every new shell via ~/.zshrc, zsh-only, undocumented data handling. User implemented an on-demand cursor-record() function instead of the vendor's always-on hook."  // → watch-items.json's `note` on accept
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
current→latest delta for that specific update. For the same reason
`version_delta` is forced to `"unknown"` (scheme `"none"`) for every `macos`
tool: a delta computed from a version that isn't this update's would be
fiction, so assembly short-circuits rather than classifying it (§1.8). `brew-health` findings have
**no** `current_version`/`latest_version` at all (both `null`) and get **no**
synthesized `upgrade` baseline — their action is the finding's own
remediation (`references/assembly.md` §Brew-Health Assembly).

`skill-drift` = the second non-version source: a vendored agent skill under
`dotfiles/config/agent-skills/` whose content no longer matches its upstream
(see `references/collection.md` §Skill-Drift Collection for the three-way
tree-hash detection, and `references/assembly.md` §Skill-Drift Assembly for
the Tool object). It behaves exactly as `brew-health` does on every
version-shaped field — `current_version`/`latest_version` both `null`, no
synthesized `upgrade` baseline, `version_delta: "unknown"` with scheme
`"none"`, excluded from `summary.by_delta` and from `summary.total_outdated`,
counted instead in `summary.skill_drift_count` — and carries four extra Tool
fields of its own:

- `drift_state` (string) — `"in_sync"` | `"upstream_ahead"` | `"local_only"`
  | `"diverged"` | `"probe_error"`, copied from the collect finding. This is
  the field a card's version slot renders in place of a version pair, so it
  must never be reworded into something that reads as "upstream moved" for
  `local_only`, which means the opposite (`references/collection.md`
  §Skill-Drift Collection).
- `drift_expected` (bool) — the finding's `expected` flag, i.e. "no decision
  is required here". Exactly the role `health_expected` plays for a health
  finding: it is what drives `risk_level`, `review_bucket` and
  `security.impact` to their quiet values. True for `local_only` and for
  every `probe_error` **except** the one that means upstream removed or
  renamed an adopted skill — that one is `notable` and demands a decision
  (`references/collection.md` §Skill-Drift Collection). Read the flag, not
  the state.
- `drift_vendor` / `drift_skill` (string) — the vendor directory and the
  skill within it (`"anthropics"` / `"pptx"`). The vendor is the unit the
  remediation acts on, so it is carried explicitly rather than re-split out
  of the tool id.

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
`"elevated"` if any of:

- `pinned` is true;
- any `relevancy[]` item has severity `warning`/`incompatible`;
- any `edit`-kind suggestion exists for this tool (`kind` defaults to
  `"edit"` when omitted);
- `version_delta` is `"major"` or `"unknown"` (§1.8) — the *one shared
  classifier*, so the report never carries two answers to how big the bump
  is, and an unknown delta size is still never treated as low-risk;
- `research_error` is set;
- the tool has no `headliners[]` **and** an empty `vendor_silent_categories`
  — research returned an object but wrote nothing and didn't say the vendor
  was silent.

Otherwise `"low"`. The last two conditions extend the "an unknown delta size
is never low-risk" doctrine to unknown *content*: a tool whose research
subagent failed has no headliners, no relevancy and no edit suggestions, so
without them it scores `"low"` and gets pre-accepted — the skill would
silently auto-approve exactly the updates it understands least. Documented
silence is different and stays `"low"`: a vendor that publishes nothing, ever
(`vendor_silent_categories` non-empty, no `research_error`), is noise the user
can't act on.

`risk_level` feeds the assembly-computed `pre_accept` flag (§1.6), which is
the single pre-accept mechanism: a suggestion renders pre-accepted iff
`pre_accept` is true, and assembly sets that from `risk_level == "low"` **or**
`review_bucket == "security_auto"` (§1.10), on the baseline `upgrade`
suggestion only. Research-authored `edit` and `watch-item` suggestions always
start undecided regardless of the tool's `risk_level`.

See `references/assembly.md` §Risk Level for the computation's place in
`assemble.py`'s flow and §Review Buckets and Pre-Accept for the union, and
`references/rendering-report.md` §Suggestion Card for how pre-accept state
renders.

### 1.5 `config_status.state` semantics

(See `references/research.md` §Config Status for how this is computed.)

- `up_to_date` — either (a) a prior commit or `changelog.md` entry already
  addressed this tool at a version ≥ today's `latest_version`, or (b) a
  prior entry addressed an *older* version V, and re-verifying the
  V→`latest_version` delta (`references/research.md` §Config Status) found
  nothing that invalidates that handling — `detail` says so explicitly in
  that second case (e.g. "Reviewed at 5.0.0; nothing in 5.0.0→5.5.1 affects
  the pin rationale."), it isn't left blank just because nothing changed.
  Purely confirmatory either way; render it quietly (small green check),
  don't make it loud — the point is reassurance that nothing was missed,
  not a new thing to review.
- `needs_attention` — a prior commit/`changelog.md` entry addressed this
  tool at an older version V, and re-verifying the V→`latest_version` delta
  found a change that could invalidate that fix's own reasoning (e.g. a pin
  comment reasoned about v5.0's requirement, and the v5.0→v5.5 delta changed
  the requirement again). Render as a warning banner — this is exactly the
  kind of thing a periodic review should catch that a one-off glance
  wouldn't. **Always pairs with at least one suggestion that addresses
  it** — a banner telling the user something might be stale, with nothing
  offered to resolve it, just relocates the "someone should check this"
  burden onto them instead of doing the check. If the re-verification
  concludes there's nothing to change after all, that's `up_to_date`, not
  `needs_attention`.
- `unknown` (default) — no prior evidence at all (no matching commit, no
  changelog entry) — the common case for a first-time review of a tool — or
  a delta that genuinely can't be assessed either way (no citable basis for
  a verdict in either direction). **Not** the default just because a prior
  entry predates `latest_version` — that case is always re-verified against
  the V→latest delta and lands on `up_to_date` or `needs_attention`, per
  `references/research.md` §Config Status. Render neutrally, same as today
  (no badge).

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
- `target_version` (string | null, assembly-computed): the version this
  suggestion upgrades to — the version that was reviewed, copied from the
  tool's `latest_version` at assembly time (§1.2). **Apply must install
  this version, or refuse — never whatever a package manager resolves as
  latest when the command actually runs** (`references/apply.md` §Pinning
  the reviewed version, WP5/I2). **Written onto every tool's *baseline*
  `{source}:{name}:upgrade` suggestion only** (the one `baseline_upgrade()`
  identifies — §Baseline Suggestion Synthesis below), including
  manual-only ones (`macos`/`standalone`) — the manual polling step
  compares against it too. **Not on a `brew-health` `:remediate` or
  `skill-drift` `:sync` suggestion, even though both are also
  `kind: "upgrade"`**: their `id` never ends `:upgrade`, so they are not
  the baseline `baseline_upgrade()` identifies, and assembly's baseline-only
  synthesis code path (`references/assembly.md` §Baseline Suggestion
  Synthesis) is the only place that sets this key — key off baseline
  identity when consuming this field, never off `kind` alone, or a
  remediation/sync gets treated as a pinnable upgrade it structurally is
  not. `null` when collection could not determine a `latest_version` at all
  (§Baseline Suggestion Synthesis) — assembly refuses to synthesize a
  runnable or pinned command in that case rather than leave apply comparing
  against nothing.
- `version_pinned` (bool, assembly-computed): whether `command` itself is
  guaranteed to install exactly `target_version` if run as-is. Same
  baseline-only scope as `target_version` above — absent on a
  `:remediate`/`:sync` suggestion. **`true` only for mise, and only when
  `name` does not already contain `@`** — `mise upgrade {name}@{target_version}`
  pins the version as a CLI argument, confirmed against upstream mise docs
  (`mise upgrade tiny@3.0.1` rewrites the version-specific request, not
  "upgrade within range"; `mise use -g npm:prettier@3` confirms mise's own
  qualifier syntax uses `:` for a backend, never `@` in the identifier, so
  a backend-qualified name like `npm:prettier` still pins normally). If
  `name` already carries an `@` — a shape mise's own docs never produce
  today, guarded anyway rather than assumed absent — pinning is refused
  rather than risking a malformed `name@version@version`-shaped command
  that would still (wrongly) claim `version_pinned: true`. **`false` for
  brew and cask** — Homebrew has no general `brew install name@version` for
  an arbitrary formula/cask, so `brew upgrade`/`brew upgrade --cask` always
  resolves to whatever the tap currently calls latest;
  `references/apply.md`'s `scripts/check_pin.py` preflight/verify is how
  apply catches drift for these instead of pinning the command. `false`
  (not `null`) when there is no command to run at all
  (`macos`/`standalone`, or a missing `latest_version`) — moot, but written
  for the same "never absent" reason as `pre_accept` below.
- `pre_accept` (bool, assembly-computed): whether this suggestion renders
  already-accepted before the user touches anything. Written onto **every**
  suggestion on every tool (so a consumer never has to distinguish "false"
  from "absent"), but only ever `true` on the tool's baseline
  `{source}:{name}:upgrade` suggestion, and only when `auto_runnable` is
  true and either `risk_level == "low"` (§1.4) or
  `review_bucket == "security_auto"` (§1.10). The page reads this field and
  never re-derives the decision — a second derivation in the page is exactly
  how the rendered state and the submitted payload drift apart. Absent field
  ⇒ treat as `false`. Three consequences worth stating explicitly:
  **`auto_runnable: false` is a hard exclusion** — a `macos` or `standalone`
  baseline, or a brew-health remediation the user must run themselves, never
  pre-accepts: "accepted" would claim a decision about something the skill
  cannot execute. **`needs_sudo: true` does *not* block it**, and that
  combination looks wrong until you know why: blocking it would un-pre-accept
  nearly every cask (the heuristic defaults casks to `true` unless research
  sets `cask_sudo_hint: false`), and it isn't silent — the card renders
  visibly as ACCEPTED before Submit, and at apply time `needs_sudo` routes
  through the askpass prompt (`references/apply.md` §Executing Upgrade
  Suggestions) the user answers interactively. The page must render an
  explicit "needs admin password" chip on such a suggestion
  (`references/rendering-report.md` §Suggestion Card). A brew-health
  suggestion can never pre-accept either, including an `auto_runnable: true`
  one like `brew install dtc`: its id ends `:remediate`, not `:upgrade`, so
  it is not a baseline. A `skill-drift` suggestion is barred by the same
  mechanism — its id ends `:sync` — and that is deliberate rather than
  incidental: re-syncing rewrites vendored files inside the dotfiles
  submodule and can conflict with a local customisation, so it is never a
  "just do it" (`references/apply.md` §Skill-Drift Remediation). Still an
  ordinary toggle afterward — the user can un-accept it like any other
  decision.

### 1.7 `kind: "watch-item"` field semantics

A standing, forward-looking concern about a tool — "call this out
automatically on a future run" — proposed by a research subagent
(`references/research.md` §Watch Items (Proposing)) or by the session itself
mid-apply (as a `pending_followups` entry, `origin: "agent_initiated"`,
§3.2 below). Renders on the tool's card (or the Results view's followups
section) exactly like any other suggestion — same Accept/Reject/Discuss
buttons, same `decisions`/turn plumbing — **it is not a parallel system**,
just a different `kind` with a different body and a different apply-time
action. See `references/apply.md` §Watch Items (Writing) for what Accept
actually does.

- `target_files`, `command`: always `[]` / `null` — there is nothing to edit
  or run for this kind, only a watch-items.json entry to write.
- `auto_runnable`: always `false` — a watch-item proposal always needs an
  explicit human accept; there's no "safe to auto-run" reading of it.
- `watch_topic` (string, required): the short phrase a future research
  subagent matches its changelog content against — copied verbatim into
  watch-items.json's `topic` field on accept (`references/research.md`
  §Watch Items (Reading) for how a later run consumes it).
- `watch_note` (string, required): the fuller context, so a future hit can
  explain itself without re-deriving everything — copied verbatim into
  watch-items.json's `note` field on accept.
- **Accept** writes `{topic: watch_topic, note: watch_note, added_at:
  <today>}` under this tool's id in `watch-items.json`
  (`references/apply.md` §Watch Items (Writing)) — nothing else. **Reject**
  does nothing (no file write). **Discuss** behaves like any other
  discussed suggestion — surfaced in conversation/as a followup, never
  auto-applied.
- Never elevates a tool's `risk_level` (`references/assembly.md` §Risk
  Level's edit-kind check only matches `kind: "edit"`) — proposing a watch
  item is not itself a risky change.

### 1.8 `version_delta` semantics

Assembly's answer to "how big is this bump", computed once from
`current_version`/`latest_version` and reused everywhere — the tiles, the
header delta pill, the "Major delta first" sort, `risk_level` (§1.4) and
`review_bucket` (§1.10) all read this field rather than re-parsing versions.

| Value | Means |
|---|---|
| `major` | first differing component is the semver major position (or a 0.x position shifted up — see below) |
| `minor` | a real upstream release with no compatibility promise either way |
| `patch` | a bugfix-position bump, a same-number suffix change (`3.7b → 3.7c`), or a pre-release → final |
| `revision` | **packaging only** — a Homebrew `_N` rebuild or a cask build-half bump with an identical upstream version |
| `unknown` | not interpretable: an opaque/build-number scheme, a missing version, or a non-version source |

`version_scheme` says how the pair was parsed, so the page can caption an
`unknown` honestly ("build-number scheme") rather than implying we failed:

| Scheme | Detected when | Positional mapping |
|---|---|---|
| `date` | both cores are 8 digits parsing as `YYYYMMDD` (1990‥2099) | any difference → `minor` |
| `calver` | both cores' first component is a 4-digit int in 2000‥2099 **and** each has ≥2 components | index 0 or 1 → `minor`, ≥2 → `patch` |
| `opaque` | either core's first component is non-numeric, or ≥ 1000 and not a calendar year | any difference → `unknown` |
| `semver` | anything else that parsed | 0 → `major`, 1 → `minor`, 2 → `patch`, ≥3 → `patch` |
| `none` | unparseable, missing, or a `brew-health`/`skill-drift`/`macos` source | `unknown` |

`version_delta_note` is one short human phrase — `"index 1"`,
`"index 2 (0.x rule)"`, `"calver index 2"`, `"packaging revision only"`,
`"build-number scheme, not interpretable"`, `"date-versioned release"`,
`"suffix change at index 1"`, `"missing version"`,
`"no numeric component"`, `"versions compare equal"`, or
`"no version delta for this source"`. It is tooltip text on the delta pill,
and the debugging handle when a classification looks wrong.

Four rules worth knowing before touching the classifier:

- **A false `patch` is the dangerous direction**, because `patch` reads as
  "nothing to think about" and feeds the pre-accept path. So a scheme that
  can't be interpreted returns `unknown`, and every ambiguous positional call
  rounds **up** in significance, never down (nmap's `7.99 → 7.991` is
  `minor`, not `patch`).
- **Calendar versioning never produces `major`.** A calver year rolls over on
  the calendar, not on a compatibility promise; mapping it to `major` would
  put `yt-dlp`, `mise` and `bitwarden` in the major box every January and
  destroy the box's meaning. `minor` is the honest middle, and it never
  reaches the pre-accept path on delta grounds.
- **The ≥1000 guard keeps build numbers out of `major`.** Microsoft Teams'
  `26163.407.4839.8659 → 26213.1006.5011.1671` would otherwise read as a
  major bump of "version 26163"; instead the pair is `opaque` and the tool
  lands in `unknown` — visible, never pre-accepted, never mislabeled `patch`.
- **0.x shifts every position up one** (semver §4, "anything MAY change"):
  index 0 → `major`, 1 → `major`, 2 → `minor`, ≥3 → `patch`. `uv`'s
  `0.11.29 → 0.12.5` and `codex`'s `0.144.6 → 0.149.0` (which removed a
  documented flag) are majors.

Rolling-major schemes stay honest — Chrome `150 → 151` and gcloud
`576 → 581` classify as `major` because upstream calls them major versions.
There is no per-tool override table; the resulting noise is handled in
highlight *ranking* instead (§1.11: a bare major scores 25, below the
threshold of 40). `brew-health` and `skill-drift` findings always carry
`version_delta: "unknown"`, `version_scheme: "none"` and are excluded from
`summary.by_delta`. Full algorithm and the worked-example matrix:
`references/assembly.md` §Version Delta.

### 1.9 `security` semantics

Every Tool object carries a `security` object with eight keys always present,
plus `notable` — the ninth — whose *presence* is itself information and which
assembly emits only when it has an answer (see **`notable`: `[]` versus
absent** below).
Six are computed by assembly from the tool's own text; `cve_severities` and
`notable` are supplied by research and validated by assembly;
`severity_counts` is assembly's rollup of the first of those two. A consumer
reads them uniformly — but a writer must not, and §1.2's jsonc block marks the
split.

| Key | Type | Meaning |
|---|---|---|
| `cve_ids` | string[] | Distinct CVE ids found in this tool's research text, deduped and sorted by `(year, sequence)` as integers — so `CVE-2026-9595` precedes `CVE-2026-12143`, which a lexical sort gets wrong and the page renders verbatim. |
| `cve_count` | int | **Always** `len(cve_ids)`. An id-backed count, never a claim, so the page can attach every counted CVE to something concrete. |
| `cve_claimed_count` | int\|null | The vendor's own **largest** stated count when it says "fixes 33 CVEs" without listing them. Max wins, never sum. Emitted whether or not ids were found — "7 of 33 listed" is more honest than either number alone. Render it as secondary text, never as the headline count. |
| `has_security` | bool | This release has security content. |
| `security_only` | bool | Its substantive content is security/patch material and nothing else. |
| `impact` | string | `"none"` \| `"possible"` \| `"unknown"` — does anything here touch *this* setup. |
| `severity_counts` | object | `{critical, high, medium, low, unknown}`, all five keys always present as ints, over `cve_ids`. **Sums to `cve_count`, always** — never to `cve_claimed_count`. |
| `cve_severities` | object[] | `{cve_id, severity, basis}` for the ids research actually graded — the *graded subset*, so an id absent here is `unknown`. Every `cve_id` resolves in `cve_ids`; sorted by `(year, sequence)`. |
| `notable` | object[] | ≤ 3 security items worth showing inline, ordered `affects_me` first then worst severity (Ordering, below). **Empty is the common, correct case — and an absent key is not the same answer as an empty one.** |

**Extraction scope.** CVE ids are scanned from `headliners[].text`,
`relevancy[].summary`/`.detail`/`.motivating_change`, `context[].title`/
`.detail`, and `security.notable[].cve_id`/`.summary` — the last pair because
research selected those items from *this* range by construction, which is
exactly the property the excluded fields lack. The **claim** scan is
unchanged and does **not** include them: a notable summary reading "one of 28
advisories" must not become a vendor claim of 28, which is the same trap the
`context[]` exclusion exists for. Deliberately excluded: `links[].embedded_content` and
`links[].url` (an unbounded changelog excerpt can cover releases outside the
current→latest range, inflating the count with CVEs the user isn't being
asked about), `suggestions[]` (derived text restating headliners), and
`config_status.detail` (backward-looking audit prose, where an id is usually
a *prior* run's finding). The claim scan (`cve_claimed_count`) is narrower
still — `context[]` is out of it. The exclusions are safe because
`has_security` never depends on ids: a missed id understates `cve_count`, it
can't flip a security release into a non-security one. Rationale per
boundary: `references/assembly.md` §Security Extraction.

**`has_security`** is true when any `headliners[]`/`relevancy[]` item has
`category: "security"`, **or** `vendor_silent_categories` contains
`"security"`, **or** any CVE id was extracted, **or** `notable` is non-empty
(a notable entry *is* security content, and a card carrying one whose security
strip never rendered would be a lie) — and false unconditionally for both
non-version sources, `brew-health` and `skill-drift` (§1.10): the security
section is about *patches the user can take*, and neither a trust decision
about a tap nor a vendored skill lagging its upstream is one.

**`security_only`** requires `has_security` *and* that research actually
produced content, then allows only these `(category, severity)` pairs across
`headliners + relevancy`: `security` at **any** severity, `fixes` at
`info`/`notable`, `notes` at `info`. Everything else disqualifies —
`features` at any severity (a new feature is not a security patch), `notes`
above `info` (codex's breaking "`codex exec --full-auto` was removed" is
filed as `notes/notable`; category alone would have waved it through),
`fixes` at `warning`/`incompatible`, and a non-`security` entry in
`vendor_silent_categories`. `context[]` never disqualifies — those are
present-tense repo-scope notes carrying no severity by design. A malformed
item disqualifies, since only an explicit allowed pair passes.

**`impact`** grounds "does this touch me" in `relevancy[]`, which is exactly
that claim (§1.2). `"possible"` when the tool is `pinned`, or
`config_status.state == "needs_attention"`, or it carries any `edit`/
`watch-item` suggestion, or any `incompatible` relevancy, or any
**non-security** relevancy at `notable`+, or any **non-security** headliner
at `warning`+. Otherwise `"none"`.

Two "never" rules hold the whole thing up:

- **No research ⇒ never `security_only`, and `impact` is `"unknown"`.** A
  subagent that failed, timed out, or returned an empty shell told us
  nothing; "we know nothing" must never be reported as "nothing but security
  fixes".
- **A `security`-category relevancy is not by itself impact.** "CVE-2026-18408
  turns any dump this machine restores into a shell-execution vector" is a
  reason to *take* the update, not a risk of taking it. Counting it as impact
  made the `security_auto` bucket permanently empty across a whole live run.

**`severity_counts`** uses the CVSS qualitative bands
(`critical|high|medium|low|unknown`), which are **not** relevancy's vocabulary
— relevancy severity is "how much does this matter to this machine", CVE
severity is "what did the issuer rate the flaw", and teamviewer's Linux-only
CVSS 8.8 is the case that makes conflating them expensive. The sum invariant
holds **by construction**, not by assertion: the rollup iterates `cve_ids` and
every id lands in exactly one bucket, so an id research forgot to rate becomes
`unknown` rather than a broken sum, and an id research rated but assembly
never extracted is dropped with a warning rather than inflating the total.

**`unknown` is a measured absence and routinely the majority.** Research
grades only what the page it already read states, plus the ≤3 items it puts in
`notable[]`; everything else is honestly `unknown`. On this run's text, 22 of
99 ids carry a rating, and 24 of the 41 security-bearing tools have no
extracted ids at all. A consumer must therefore render the *honest compact*
form by default — ids count, graded count, and only the graded classes that
are non-zero — and never fabricate a class to fill a meter. An all-`unknown`
`severity_counts` is a correct report, not a broken one.

**`cve_severities`** carries `basis ∈ vendor | nvd | cvss | unrated`, and a
grade with no basis is read as `unknown`: a rating with no source is not a
rating (`references/research.md` §CVE Severity Capture). It is keyed by CVE
id, so a vendor-only advisory — wireshark's `wnpa-sec-*`, tailscale's `TS-`,
teamviewer's `TV-` — never appears in it and never enters `severity_counts`;
its severity rides on the `notable` entry instead. When two sources rate one
id differently, assembly keeps the worse and warns; understating a severity is
the failure mode with a cost.

**`notable`** is the only security content a card shows inline. An item
qualifies when its severity is `critical` (unconditionally); or `high` with a
precondition this setup actually satisfies; or it is the subject of a
`security`-category `relevancy[]` item at `notable`+, **with or without a
CVE id** — which is what makes the 24 id-less tools representable; or it is
being exploited in the wild.

**Ordering: `affects_me: true` first, then worst severity, then the id's
`(year, sequence)`, then id-less last.** Cap 3. Assembly re-sorts and re-caps
by that key *after* validating every entry, so an over-long array loses its
weakest entries rather than its last ones; a consumer should cap again rather
than trust it.

Two things about that key are deliberate, because the key both orders **and**
evicts:

- **`affects_me` outranks severity.** An entry with a concrete touchpoint here
  is never evicted by a higher-rated one that misses this machine. R5's third
  clause exists to surface id-less, ungraded, machine-touching flaws
  (`brew:iproute2mac`'s never-assigned command injection is one of the two
  most important security items in the recorded run) and severity-first
  ordering evicted exactly those — three `low` CVEs nobody here can reach beat
  it. Every entry cleared R5 on its own before it got here, so promoting one
  cannot smuggle in a weak item.
- **`unknown` sorts above `low`, not below it — and the `severity` vocabulary
  is unchanged.** `critical|high|medium|low|unknown` is still exactly what
  research may write and what a consumer must accept; only the *rank* moved.
  Assembly keeps two rank maps for the one vocabulary and they disagree on
  `unknown` on purpose: in `severity_counts`/`cve_severities` resolution
  `unknown` means "no rating recorded" and must never beat a recorded one, so
  it ranks lowest; on a `notable[]` entry it means "research selected this and
  nobody published a grade", which is not evidence of a small flaw. A consumer
  that re-sorts `notable[]` must use the second ordering, not the first.

`affects_me` means a concrete touchpoint on this setup — a file, a service, a
call site — and is **never derived** from the presence of a relevancy item: a
third of one live run's security relevancy items exist precisely to say a fix
does *not* reach this machine, and auto-deriving would invert every one of
them. Assembly warns when `affects_me: true` has no security-category
relevancy backing it, and warns only. It is a display flag on one item and has
no relationship to `impact`, which is a bucket input (§1.9's second "never"
rule).

`source_ref` is the `"rel:{i}"` / `"hl:{i}"` identity of the
`relevancy[]`/`headliners[]` item this entry restates, or `null`. It exists so
the highlights de-duplication (§1.11) matches on a slot rather than on prose
that `why`'s 220-char truncation may already have cut.

`notable` is forced empty for `source: "brew-health"` and for a tool whose
research failed or produced no headliners — the same doctrine as "No research
⇒ never `security_only`", applied twice.

**`notable`: `[]` versus absent.** These are two different answers and a
consumer must keep them apart:

- **`notable: []`** — the selection ran and nothing qualified. The card draws
  its **single-column** variant: there is no security column, because an
  empty one is a panel that says nothing.
- **`notable` absent** — the question was never put to this tool, so there is
  no answer to render. The consumer falls back to deriving the column from
  the tool's own security content, exactly as it did before the field
  existed.

Assembly emits the key when it actually has an answer: research supplied a
readable `security` block, **or** research supplied nothing at all
(`research_error` / no headliners) and assembly forced `[]` under the
doctrine above, **or** the tool is `brew-health`, where `[]` is likewise
assembly's own decision. It omits the key for a research file that carried
real content and no `security` block — such a file predates the field, and
assembly must not answer "nothing here is notable" on its behalf. A block too
drifted to read at its root (a bare string, a list, `null`) is not an answer
either: the key is omitted and the consumer falls back, rather than reporting
a silence research never uttered. **Emitting `[]` unconditionally is not a
harmless default**: on the recorded run, whose 22 research files carry no
`security` block at all, it deleted the security column from 77 of the 78
cards.

### 1.10 `review_bucket` semantics

The review-effort axis: how much of a human does this tool need. Strict
precedence, first match wins.

| Bucket | Means | Renders as |
|---|---|---|
| `security_auto` | Security content only, no impact here, delta not `major`/`unknown`, and a runnable baseline | The auto-approved list in the Overview's security section; its baseline is pre-accepted (§1.6) |
| `security_mixed` | Has security content **plus** something else — other changes, a possible impact, an unknown, an elevated risk | The side-by-side card: security items and other items shown together so the user decides fast |
| `attention` | No security content, but something needs a human: elevated `risk_level`, stale `config_status`, a proposed `edit`/`watch-item`, or nothing runnable | The "needs you" list |
| `routine` | No security content, low risk, only the baseline upgrade to decide | The long tail, collapsed by default |

Order of evaluation: the two non-version sources first — `brew-health`
(`routine` when `health_expected`, else `attention`) and `skill-drift`
(`routine` when `drift_expected`, else `attention`) — then `security_auto`,
then `security_mixed`, then `attention`, then `routine`. Because
`risk_level` is computed before the bucket, the `attention` test doesn't
re-check the major/unknown delta or `research_error` conditions —
`risk_level` already returned `"elevated"` for both.

**`review_bucket` is orthogonal to `source`.** The page groups brew-health
cards by `source == "brew-health"` and counts them with
`summary.health_count`, and skill-drift cards by `source == "skill-drift"`
with `summary.skill_drift_count` — **never** by bucket. `routine` on the one
expected GNU-utils PATH note, or on a `local_only` skill we deliberately
patched, means "nothing to decide here", not "hide it"; the findings that do
need a human land in `attention` and, when their severity warrants, in
`highlights` too.

A `macos` or `standalone` tool has `auto_runnable: false` on its baseline, so
the runnable guard bars it from `security_auto` unconditionally: a
security-only macOS update lands in `security_mixed` and one with no security
content lands in `attention`. That is deliberate — there is nothing to
auto-approve when the skill cannot run the command; render such a card with
the baseline's `manual_reason` instead of an approve control.

Computation and the bucket-by-bucket rationale: `references/assembly.md`
§Review Buckets and Pre-Accept.

### 1.11 `highlights` semantics

A ranked, capped list of "the biggest decision drivers / inputs needed /
major patches", computed by a fixed score — `assemble.py` is a plain script,
so the ranking is reproducible from the data alone, with no per-run LLM
judgment anywhere in it.

Object shape (all keys always present):

- `tool_id` — resolves in `tools[]`.
- `title` — `"{name} {current_version} → {latest_version}"`, or just the
  finding's `name` for a `brew-health` or `skill-drift` tool, which has no
  versions (rendering `null → null` as "→ UNKNOWN" is the failure this
  avoids — `references/rendering-report.md` §Skill-Drift Rendering).
- `why` — one line, ≤ 220 chars, truncated on a word boundary with `…`.
- `why_source` — which branch of the fixed order produced `why`:
  `relevancy_security` | `relevancy_other` | `config_status` |
  `research_error` | `headliner_security` | `headliner_other` | `major_bump` |
  `none`. Provenance, not styling: a page may ignore it.
- `why_ref` — the `"rel:{i}"` / `"hl:{i}"` identity of the content item `why`
  came from, or `null` for the branches that synthesize their own text. It is
  the other half of `security.notable[].source_ref` (§1.9).
- `severity` — max severity across `headliners + relevancy`, in relevancy's
  vocabulary so the page reuses one palette. With no items at all:
  `needs_attention` → `"warning"`; `research_error` or a `major`/`unknown`
  delta → `"notable"`; else `"info"`.
- `suggestion_ids` — **every** suggestion id on the tool, in array order
  (baseline first when present), read after assembly's id-uniqueness pass so
  the ids are the final ones. The page looks each up in `tools[]`.
- `reasons` — stable machine-readable codes in a fixed emission order, meant
  to be rendered as chips (`why` is the prose).
- `score` — the ranking score, exposed for debuggability and stable
  client-side re-sorts; the UI need not show it.

Scoring, threshold 40, cap 8, and the sort key are in
`references/assembly.md` §Highlights. Two properties consumers depend on: a
bare `major` bump scores 25 and a bare CVE count scores 10, so neither
qualifies alone (this is what keeps Chrome/Firefox/gcloud's rolling majors
out of highlights while leaving them counted in `summary.by_delta.major`);
and `security_auto` contributes nothing, being by definition the bucket that
needs no decision.

**No highlight restates its own tool's security card.** When a candidate's
`why_ref` matches a `source_ref` in that tool's `security.notable[]`, the
**highlight** yields — it is dropped and the slot is backfilled from the next
ranked candidate, so the section still carries up to 8 *distinct* decision
drivers. The security card keeps the sentence, because on the tools where this
fires it is usually the single most important line on the card
(`cask:windows-app`'s RDP-client RCE, `brew:gh`'s pre-approved-command reach).
The match is structural on both sides for a reason: `why` is already
truncated, so a string comparison against it fails silently on any summary
longer than 220 chars.

`highlights` is agent-independent but not authoritative about what *blocks
Submit* — the page recomputes the blocking set live from undecided
suggestions on `incompatible`-severity tools rather than reading it from here
(`references/rendering-report.md` §Overview Tab → Highlights).

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
  For `kind: "watch-item"` (§1.7): the session appends the proposal's
  `watch_topic`/`watch_note` to `watch-items.json`
  (`references/apply.md` §Watch Items (Writing)) — no repo edit, no
  command, nothing else.
- `reject` — session skips; records in summary. For `kind: "watch-item"`,
  this means no watch-items.json write — the proposal is simply dropped.
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
      "thread": [],

      // WP5/I2 (references/apply.md §Pinning the reviewed version): each
      // present key is one scripts/check_pin.py JSON result, recorded via
      // `write_status.py record-pin-check {session} {id} {phase}
      // <result-file>` — not stored verbatim unvalidated: record-pin-check
      // requires "source"/"name" (non-empty strings), "match" (bool), and
      // "target_version"/"observed_version"/"reason" (each string or null)
      // to be present and well-typed, and refuses (nothing written) rather
      // than store a malformed object a later reader would have to guard
      // against. It also refuses when the result's own "phase" (stamped by
      // check_pin.py's emit() at the moment the check ran, never asserted
      // by the caller) disagrees with the {phase} being recorded under —
      // closes the one way a preflight result could be filed as a verify,
      // since the two can otherwise look byte-identical for the same tool
      // at the same version. `{}` for every action that isn't a
      // pin-checkable upgrade (the common case). "verify" — not
      // "preflight" — is what `set-action ... done` reads: it refuses that
      // transition for any suggestion carrying a target_version on a
      // check_pin.py-checkable source (assemble.PIN_CHECKABLE_SOURCES —
      // brew/cask/mise) unless "verify" is present here, its "match" is
      // `true`, and its "source"/"name"/"target_version" all match the
      // suggestion's own — so a stale or copy-pasted result from a
      // different tool, source, or an earlier target_version can never
      // satisfy it either. This is a guardrail against a forgotten or
      // misfiled step, not a boundary against a caller that deliberately
      // fabricates a result — see references/apply.md §Pinning the
      // reviewed version for that distinction stated plainly.
      "pin_checks": {
        "preflight": { "phase": "preflight", "checked_at": "2026-07-04T14:52:05Z",
          "source": "brew", "name": "podman", "target_version": "5.5.1",
          "observed_version": "5.5.1", "match": true, "reason": null },
        "verify": { "phase": "verify", "checked_at": "2026-07-04T14:52:40Z",
          "source": "brew", "name": "podman", "target_version": "5.5.1",
          "observed_version": "5.5.1", "match": true, "reason": null }
      }
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
      // different kind of thing. A `kind: "watch-item"` followup (§1.7) is
      // the apply-time counterpart to a research-proposed one: the session
      // itself, not a research subagent, noticed a standing concern worth
      // watching while applying something (`origin: "agent_initiated"`),
      // and carries `watch_topic`/`watch_note` instead of
      // target_files/command/diff_preview — same accept-writes-to-
      // watch-items.json behavior as the report-time version
      // (`references/apply.md` §Watch Items (Writing)).
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
