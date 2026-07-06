# Tool Update Review — Design Document

Table of contents:
- A. Data Model — report JSON schema (A.1) and feedback.json schema (A.2)
- B. Page Design — palette, wireframes, interaction spec, keyboard nav
- C. Server + Session Workflow — server spec (C.1), session steps (C.2),
  failure modes (C.3), executing upgrades (C.4), repo-freshness/config-status
  cross-referencing (C.5), testing bespoke `tasks/*.sh` fixes (C.6)
- D. Template Variables — placeholder strategy and suggestion-id mapping

## A. Data Model

### A.1 Report Input Schema

The report is a single JSON object injected into the page template. The top-level shape:

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
	// ── Repo freshness (see C.5) ────────────────────────────────────────
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
	"tools": [ /* Tool[] — see below */ ]
}
```

**Tool object:**

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

	// ── Risk assessment (assembly-computed — see below) ────────────────
	"risk_level": "elevated",           // "low" | "elevated"

	// ── Research ──────────────────────────────────────────────────────
	"research_error": null,             // null | string — set if subagent failed
	// Agent-written; ≤6 concise items covering the whole current→latest
	// range. Each is one atomic fact — split a compound changelog bullet
	// ("added X, plus fixed CVE Y") into separate items before writing them
	// here, never one item bundling both (SKILL.md step 3's quality bar).
	// "category" and "severity" are independent axes, both assigned by
	// research (not derived client-side from keywords — a heuristic can't
	// tell topic apart from urgency, which is exactly how a low-profile
	// security item used to get bucketed into Notes just because it read as
	// minor). "category" is topic-only: which of the four content groups
	// (B.2) this fact belongs in. "severity" reuses relevancy's vocabulary
	// (`"info" | "notable" | "warning" | "incompatible"`) and drives this
	// item's own color/icon within its category — it never changes which
	// category the item lands in.
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
	// Updating is recommended.", nothing technical ever published) — SKILL.md
	// forbids authoring fake bullets to fill this gap. Listing the category
	// here renders one small compact tag in its place (B.2) instead of an
	// empty or manufactured group. Rare: most tools never set this.
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
			// as normal links (B.2).
		}
	],

	// ── Config status ─────────────────────────────────────────────────
	// Cross-referenced against recent commit history (repo_context, top
	// level) and the changelog.md audit trail — see C.5. Answers "has this
	// tool's config already been dealt with, and does that still hold?"
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
	// that are (see B.2 — rendered collapsed-by-default, title-only until
	// expanded, since they tend to run long: a one-line claim followed by a
	// full paragraph of evidence).
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
			"command": "brew upgrade --cask wireshark-app", // what actually runs when accepted (see C.4)
			"auto_runnable": true,                          // may the session execute `command` itself? (subject to the
			                                                 // report-level auto_run_upgrades toggle — see A.2/C.4)
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
			"id":    "brew:podman:fix-machine-init-flags",  // bespoke-setup fix — see C.6
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

Source vocabulary: `brew` = Brewfile `brew` line, `cask` = Brewfile `cask`
line, `mise` = `mise outdated` runtime, `standalone` = a CLI genuinely
unmanaged by brew, version-checked by running `--version` and comparing to
the latest release, `macos` = a `softwareupdate -l` entry (system OS/app
updates — Safari, Xcode CLT, the OS itself). `current_version` for `macos`
entries is the running `sw_vers -productVersion`, not a per-update version —
research should treat it as "what's currently installed system-wide" context
rather than a strict current→latest delta for that specific update.

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

`risk_level` semantics: computed entirely by `scripts/assemble.py` from
signals already present in the assembled Tool object — not a subjective
per-tool judgment call left to the research subagent, so every run applies
the same rule the same way. `"elevated"` if any of: `pinned` is true, any
`relevancy[]` item has severity `warning`/`incompatible`, any `edit`-kind
suggestion exists for this tool, or the version delta is a major bump
(semver-aware — an unparseable version pair defaults to `"elevated"`, since
an unknown delta size is never treated as low-risk). Otherwise `"low"`.
Used by assembly to pre-set the baseline `upgrade` suggestion's initial
decision (A.2) — a `"low"` tool starts `accept`ed instead of undecided; an
`"elevated"` one starts undecided as before. This only ever affects the
*baseline* `upgrade` suggestion; research-authored `edit` suggestions
always start undecided regardless of the tool's risk_level.

`config_status.state` semantics (see C.5 for how this is computed):
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

`kind: "upgrade"` field semantics (see C.4 for the execution mechanism):
- `auto_runnable` (bool, default `true`): whether the apply step may execute
  `command` itself at all, subject to the report-level `auto_run_upgrades`
  toggle. This stays `true` even for tools with bespoke `tasks/*.sh` setup
  (podman-style) — the baseline suggestion only ever runs the plain
  package-manager command (`brew upgrade podman`), which is never itself
  the risky part. Set `false` only when *no command at all* is safe to
  auto-run for this tool (rare — see C.6 for the ordinary case, which is a
  separate tested `kind: "edit"` suggestion, not this flag). When `false`,
  a `manual_reason` string is required, and the session always just tells
  the user what to run, never executes anything for that suggestion.
- `needs_sudo` (bool, default `false`): hints that `command` may invoke a
  privileged installer (e.g. a cask shipping a `pkg` artifact). When `true`
  and the suggestion is accepted with auto-run enabled, the session routes
  through the askpass mechanism (C.4) instead of a bare subprocess call.
  Default to `true` when genuinely unsure — assuming `false` and hitting an
  un-satisfiable password prompt is worse than an unnecessary askpass popup.

### A.2 Feedback Schema (`feedback.json`)

Written atomically by the server to `{session_dir}/feedback.json`.

```jsonc
{
	"report_id":    "tool-update-review-20260704T143012",
	"submitted_at": "2026-07-04T14:52:07Z",

	// Global toggle (checkbox on the page, near Submit) — default true.
	// Governs every accepted kind:"upgrade" suggestion with auto_runnable
	// true; see C.4. Suggestions with auto_runnable:false are never
	// affected by this toggle — they're always manual, always.
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

	// A baseline "upgrade" suggestion on a "low" risk_level (A.1) tool
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

`decision` semantics:
- `accept` — for `kind: "edit"`, session applies the edit immediately,
  following dotfiles-submodule conventions. For `kind: "upgrade"`: if
  `auto_runnable` is `false`, always manual regardless of any toggle — the
  session tells the user the `command` and polls for completion, never runs
  it. If `auto_runnable` is `true`, behavior depends on the report-level
  `auto_run_upgrades` toggle (default `true`, see C.4): when on, the session
  executes `command` itself (via the askpass mechanism if `needs_sudo`);
  when off, same manual/poll behavior as `auto_runnable: false`.
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

## B. Page Design

### B.1 Aesthetic Constraints

Solarized Dark palette as CSS custom properties, no external dependencies:

| Token | Hex | Role |
|---|---|---|
| `--base03` | `#002b36` | page background |
| `--base02` | `#073642` | panel / card background |
| `--base01` | `#586e75` | border, de-emphasized |
| `--base00` | `#657b83` | secondary text, `macos` source badge |
| `--base0`  | `#839496` | body text |
| `--base1`  | `#93a1a1` | emphasis text |
| `--base2`  | `#eee8d5` | heading text |
| `--yellow` | `#b58900` | `warning` severity, pin badge, discuss state |
| `--orange` | `#cb4b16` | `notable` severity badge, `cask` source badge |
| `--red`    | `#dc322f` | `incompatible` severity, prominent callout |
| `--blue`   | `#268bd2` | links, `info` severity, `brew` source badge |
| `--cyan`   | `#2aa198` | Accept confirmed state |
| `--green`  | `#859900` | version delta new-version text, diff additions |
| `--violet` | `#6c71c4` | mise source badge |
| `--magenta`| `#d33682` | standalone source badge |

All five `source` values (A.1) get a badge: `brew`/`cask` reuse the severity/link
colors above (blue/orange), `mise`/`standalone` have dedicated colors, and
`macos` reuses `--base00` as a neutral "system-level, not a package manager"
badge.

Single file: all CSS and JS inline. Zero CDN calls; system font stacks only.
Must render correctly offline.

### B.2 Page Layout

Header: title, date, machine context (arch highlighted when Intel — it gates
compatibility). Counts row: tools with updates, incompatible, suggestions.

Filter bar: source select (All|brew|cask|mise|standalone), severity select,
"Only relevant to me" toggle (hides tools with empty `relevancy[]`), sort
select (**Needs decision first** [new default] | Incompatible first | Name |
Source | Major-delta first). "Needs decision first" sorts any tool with at
least one undecided suggestion (including the baseline `upgrade` suggestion
— nearly every tool has one) above tools whose suggestions are all
decided/absent; incompatible severity breaks ties within that. Client-side
only: toggle `display:none` and reorder DOM nodes. **The filter bar must stay
reachable in the frozen Results view** (see "Results view", below) so a
filter applied before Submit can still be cleared/inspected afterward —
never leave the user stuck looking at a filtered-empty report with no way
to reset it.

Sticky progress bar (`position: sticky; top: 0`): thin progress bar (cyan
decided / base01 remaining), text "N of M decided · K incompatible undecided"
(red flash when K > 0), Submit button — disabled until every suggestion on an
`incompatible`-severity tool has a decision; tooltip explains why.
Non-incompatible suggestions may be left undecided.

Per-tool `<section>`: collapsible; header row with name, `current → latest`
(latest in green), source badge, PINNED badge (yellow) when pinned, and a
**`config_status` badge** when `state` isn't `"unknown"`: a quiet small
green check + "config current" for `up_to_date` (hover/click for the
`detail`/evidence), a visible orange/red banner for `needs_attention`
("⚠ config may be stale — {detail}") placed right under the header before
any content group, since it's a review-the-review flag the user shouldn't
have to dig for.

**Header badges, at a glance without expanding:**
- **Decision-count badge**: while the tool has ≥1 undecided suggestion,
  show an attention-styled badge with the undecided count (colored
  icon — e.g. `--yellow` "?" or "!" — not neutral chrome, since this is the
  "does this need me" signal). Once every suggestion on the tool has a
  decision, downgrade to a quiet "N decided" badge (`--base01`, same
  low-key treatment as the `config_status` "ok" badge) — the badge never
  disappears, it just stops demanding attention.
- **Severity-tier counts**: one small count per severity level actually
  present among the tool's headliners+relevancy items (info/notable/
  warning/incompatible), using the same icon/color mapping as per-item
  severity above — e.g. "⛔1 ⚠2". Omit a tier with zero items rather than
  showing "0". This is the "how significant is this tool's changelog"
  signal, independent of the decision-count badge's "does it need action"
  signal — both render, neither substitutes for the other.

**Collapse controls**: the header row's existing collapse toggle gets a
twin at the very bottom of the expanded body (after suggestion cards and
the per-tool note area) — same handler, same collapsed/expanded state; a
long tool card shouldn't force a scroll back to the top just to close it.
This is distinct from the note textarea's own Close/collapse control
(below), which only dismisses the note, not the whole section.

**Default collapse state and auto-advance**: on page load, every tool
section starts collapsed except the first (in current sort order) — with
70+ tools, an all-expanded initial view is unusable. Collapsing a section
(via either collapse control) auto-expands the next section in view order;
this is gated by a toggle (default **on**) near the filter bar, so the
behavior can be turned off for anyone who'd rather step through manually.
**Auto-advance triggers on the collapse action itself** — not on "all of
this tool's suggestions are decided," which would be ambiguous for a tool
with zero suggestions or one the user collapses without deciding anything;
collapsing is the one unambiguous, user-initiated signal that means "I'm
done looking at this one for now." Add "Collapse all" / "Expand all"
buttons next to the auto-advance toggle — both bulk actions ignore
auto-advance entirely (they set every section to one state, not a
one-at-a-time walk).
**There is no separate "headliners" bullet list and no separate "links row"
wall of buttons — both `headliners[]` and `relevancy[]` render entirely
inside four content groups: Security, Fixes, Features, Notes.** Each item
carries its own `category` (which group) and `severity` (A.1, both assigned
by research) — the groups themselves are neutral, purely-organizational
containers with no color of their own; **only individual items are colored**,
by their own `severity`, independent of which group they're in. This is a
deliberate change from an earlier version of this design that colored the
whole group box by a single severity/notability accent and derived
category client-side from keyword matching — that heuristic is exactly
what caused topic and urgency to get conflated (a low-profile security
item reading as "minor" would get bucketed into Notes by the same signal
that was supposed to be its severity, not its topic). Research assigns
both explicitly now; the page just renders what it's given, no
classification logic of its own. Nothing is shown twice: a headliner that's
really a security fix renders once, under Security, not also in a generic
list. Evidence paths for relevancy items stay attached to their item
wherever it lands. Lead with title + one-line description for each item;
push its changelog/release link into a compact footer-style reference per
item (a direct deep link where the source supports line-level anchors, e.g.
a CHANGELOG.md section) rather than a shared links block.

**Per-item severity → color/icon** (reuses the palette's existing severity
roles, B.1 — no new colors introduced): `incompatible` → `--red`, `⛔`;
`warning` → `--yellow`, `⚠`; `notable` → `--orange`, `●`; `info` → `--blue`,
`·`. Applied to the item's left border accent and a small leading icon —
the group's own border/heading stays neutral (`--base01`) regardless of
what severities its items carry.

**Link click behavior**: a link with only `url` (A.1) behaves as a normal
anchor — opens in a new tab. A link with `embedded_content` set opens a
modal instead, rendering that markdown text inline (any links inside the
markdown itself render as normal anchors within the modal) rather than
navigating away — this is the fallback for source content with no stable
browsable destination at all, not a general-purpose reader view; most links
just have `url` and never trigger the modal.

**After the four content groups, two more optional sections render** (only
when the tool's research populated them — most tools have neither):
- **Context** (from `context[]`, A.1): a callout, visually distinct from the
  four groups above — no severity coloring, since these aren't change-risk
  items. Each item starts **collapsed to its `title` only**; clicking
  expands to show `detail`/`evidence`/`link`. This is a stronger collapse
  default than suggestion cards or content-group items get elsewhere —
  context items tend to be a one-line claim followed by a full paragraph
  (e.g. "the existing warning and control flow remain accurate as
  written" followed by the reasoning why), and showing that expanded by
  default for every tool would bury the actual changelog content above it.
- **Release inventory** (from `release_inventory[]`, A.1): a short list,
  one line per `{version, link}` pair, no collapse (these are already
  short) — pure bookkeeping about what releases exist in the current→latest
  range, not a claim about what any of them changed.

Both sit below Notes and above the suggestion cards. Suggestion cards
follow the grouped content; a per-tool note textarea sits last, **with a
Close/collapse control** so it can be dismissed after reading without
leaving it visually "open" forever.

**Compact "no detail published" tag**: driven by `vendor_silent_categories`
(A.1) — a category name listed there renders one small pill-style tag in
that group's slot ("No detailed changelog published") instead of its normal
item list, even if the group would otherwise be empty. No bullet, no
border-left accent, no per-item link (the link lives once, on the tool's
canonical changelog/release reference). SKILL.md's research quality bar
forbids authoring a fake bullet to fill this gap — this field is the
correct alternative, not a fallback the page invents on its own from empty
groups.

**Per-item detail collapse**: inside the four content groups, an item with
real secondary detail (`detail` beyond the one-line `summary`/title) shows
only the summary in the forefront view; a small expand control (e.g. "▸
more") reveals the rest, evidence, and per-item link. Items with no extra
detail beyond the summary render with no expand control at all — don't add
one that opens onto nothing. This is a lighter-weight collapse than the
Context section's whole-item collapse (above) — the summary line stays
visible here, since it's real changelog content, unlike Context's
scope/locality notes.

Suggestion card: for `kind: "upgrade"`, render the `command` in a copyable
code chip instead of a diff (there is nothing to diff) with a short "run
this yourself" hint; Accept only marks the decision; it does not imply a
diff preview exists. For `kind: "edit"`, unchanged: title, target file(s),
rationale, motivating link, diff
preview (`+` green / `-` red in a `<pre>`), Accept/Reject/Discuss buttons,
comment textarea (1 row collapsed, 3 rows focused). Clicking an active
decision button toggles back to undecided.

| State | Visual |
|---|---|
| Undecided | default card |
| Accepted | left border + Accept button filled cyan, "ACCEPTED" |
| Rejected | left border base01, card dimmed, "REJECTED" |
| Discuss | left border + Discuss button filled yellow, "DISCUSS" |

Submit: POST JSON to `/feedback`; on 200 show full-page overlay "Feedback
submitted — return to your terminal"; on error keep data, show retry.

### B.3 Keyboard Navigation (nice-to-have)

`j`/`k` next/previous tool section (blue outline, scroll into view); `a`/`r`/`c`
act on first undecided suggestion in focused tool (repeats cycle); `s` submit
if ready; `f` cycle filter presets All → Incompatible → Relevant; `?` help
overlay. Suppressed while typing in a textarea.

### B.4 Results / Apply-Progress View

After Submit (or on page load if `feedback.json` was already submitted —
`GET /status` returns 200), the page transitions into a Results view: the
filter bar stays visible and interactive (see B.2 — only the decision
controls in the Report tab freeze), the progress bar becomes a tab strip
(Results | Report), and a panel replaces the report with a live action list
polled from `GET /status` (2s initial backoff, growing — see C.1). Each
action row shows an icon
(`○` pending, spinner frame `running`, `✓` done, `✗` failed, `—` skipped),
label, and one-line note; clicking a row with `detail[]` expands a `<pre>`.
A Recap section and Changelog-entries section appear once populated. Finish
button posts `/shutdown` and shows a closing splash; disabled/confirms if
any action is still `running`.

**The transition must show a loading/spinner state immediately**, before the
first `/status` fetch resolves — there is real network + apply latency
between Submit and the first status write, and a blank/static panel during
that gap reads as broken. Don't wait for data to render *something*.

## C. Server + Session Workflow

### C.1 Python Stdlib Server

`server.py {session_dir} {port}` — stdlib only (`http.server`, `socketserver`,
`json`, `os`, `threading`). Bind `127.0.0.1` only. Try port 8742, on EADDRINUSE
increment up to 8751, then fail with `lsof -i :8742` hint.
`allow_reuse_address = True`.

Endpoints:
- `GET /` → 200, serves `{session_dir}/index.html` read at request time, or
  an embedded loading page (results-view-design.md E) when `index.html`
  doesn't exist yet — the server starts before research/render finish (C.2
  step 2), so this is the normal state for the first stretch of a session.
- `GET /research-status` → `research-status.json` or 404 (results-view-design.md E.1).
- `GET /health` → `{"status":"ok"}` — session polls before opening browser.
- `POST /feedback` → validate JSON + `report_id` + known suggestion ids
  (400 on any mismatch); 409 if `feedback.json` already exists (one
  submission per session); write `feedback.json.tmp`; `os.replace()` to
  `feedback.json`; respond `{"status":"written","path":...}`. **Does not
  shut down the server** — it stays up so the page can keep polling
  `/status` through the whole apply pass. Only `/shutdown` (below) actually
  stops it.
- `POST /followup` → **appends a turn** (results-view-design.md C.10) to a
  thread — either a `pending_followups` entry or a `"failed"` action's own
  `thread` (same mechanism, shared by #27/#28): `{"thread_id", "decision",
  "comment"}`, `decision` one of `"accept"|"reject"|"discuss"|null`. Unlike
  the original one-shot design, **there is no duplicate-decision 409** —
  threads are multi-turn by design (results-view-design.md C.10), so a
  second POST to the same `thread_id` is expected and valid, not an error.
  Writes/merges into `followup_turns.json` (`.tmp` + `os.replace()`, dict
  keyed by `thread_id` → array of turns, each turn timestamped `at` server-side
  and `author: "user"`) — the session polls that file directly (no new GET
  endpoint needed, it already has local filesystem access) and appends its
  own `author: "agent"` turns directly into `status.json` (not through this
  endpoint, which is browser→session only).
- `POST /shutdown` → schedules `server.shutdown()` on a background thread
  (must run off the `serve_forever()` thread) after a ~1s delay so the
  response flushes; idempotent (still 200 if already scheduled).

Session detects submission by blocking on the server subprocess exit
(`wait(timeout=86400)` — 24 h). Fallback: poll for `feedback.json` existence
every 5 s.

### C.2 Session Steps

1. **Collect** — run `scripts/collect.sh` (brew outdated intersected with
   Brewfile + pin state, mise outdated, standalone CLI versions) → candidates
   JSON, and `scripts/repo_context.sh` → `repo_context.json` (C.5).
2. **Serve** — launch `server.py`, poll `/health`, `open http://localhost:{port}/`
   — started here, before research, since `server.py` tolerates a missing
   `report.json` and falls back to a loading page (E) until one exists.
3. **Research** — tiered subagents (SKILL.md step 3): one per tool with a
   real repo touchpoint, one per ~4-9-tool batch otherwise; each writes its
   own `research/{slug}.json` file rather than returning text to transcribe,
   and the session updates `research-status.json` (E.1) as groups progress.
   Returns headliners, typed links, relevancy (against setup repos + machine
   arch), suggestions, config_status. Timeout/failure → `research_error`
   set, tool still listed with versions only.
4. **Assemble + Render** — read back `research/*.json`, merge with collect
   output, compute summary counts, synthesize the baseline `kind: "upgrade"`
   suggestion per tool, validate suggestion-id uniqueness and evidence paths
   (warn, don't abort) — `scripts/assemble.py` does this mechanically — then
   `scripts/render.py report.json` writes `index.html` into the session dir
   (`/tmp/tool-update-review-{report_id}/`), followed by a `research-status.json`
   update to `phase: "ready"` so the already-open loading page reloads into it.
5. **Wait** — block on server exit (24 h timeout; offer re-open or abandon).
6. **Apply** — read `feedback.json`; for `kind: "edit"` accepts, apply per
   repo conventions (dotfiles submodule flow for `dotfiles/` paths; direct
   edit for `Brewfile` etc., with the `./setup.sh projects` exception per
   CLAUDE.md when a target file is projects.sh-managed); for
   `kind: "upgrade"` accepts, execute per C.4 when auto-runnable, else
   prompt the user and poll; investigate `tool_comments`/`discuss` items and
   turn any concluded action into a new pending followup thread
   (`origin: "user_comment"`, results-view-design.md C.10) rather than
   applying it directly — and do the same, unprompted, whenever the apply
   pass itself hits something needing an explicit decision beyond plain
   success/fail (`origin: "agent_initiated"`; results-view-design.md D);
   summarize accepted/rejected/discuss/undecided + comments.

### C.3 Failure Modes

| Failure | Handling |
|---|---|
| All ports busy | Fail with `lsof` hint |
| Tab closed, no submit | Server stays up; 24 h timeout, offer re-open (state lives in the page until tab closes; re-open re-renders fresh) |
| Server crash | Check for valid `feedback.json` (partial submit), else offer re-serve |
| Stale `feedback.json` | `report_id` mismatch → warn, confirm before use |
| One tool's research fails | `research_error` shown, no suggestions for it |
| POST with unknown suggestion ids | 400, page shows error toast |
| `kind: "upgrade"` accepted but user never runs the command | Poll caps at ~20 min, action stays `"running"` with a reminder note; Finish remains available, session doesn't block on it |

### C.4 Executing `kind: "upgrade"` Suggestions

The **session** (the orchestrating Claude Code agent, in-conversation —
not `server.py`, which never executes suggestions) is what runs an accepted
upgrade command, when it runs one at all. Two independent gates must both
pass:
1. `auto_runnable` on the suggestion is `true` (false = always manual, see
   A.2 decision semantics — no toggle overrides this).
2. `feedback.json`'s report-level `auto_run_upgrades` toggle is `true`
   (page checkbox near Submit, defaults to `true`).

When both hold, the session runs `command` directly via its normal shell
tool — same as any other command it runs, subject to the harness's own
permission prompts (a separate, expected approval layer independent of
anything below; don't try to route around it).

**Privileged commands (`needs_sudo: true`)**: brew formulae, mise, and uv
never need elevation — only a handful of casks that ship a `pkg` installer
(e.g. Wireshark's ChmodBPF helper) do, and Homebrew invokes that `sudo`
internally (never wrap the outer `brew`/`mise` command itself in `sudo` —
Homebrew refuses to run as root). The mechanism:
- A small helper script (`scripts/askpass.sh` in this skill) that pops a
  native macOS password dialog via `osascript ... with hidden answer` and
  prints the entered text to stdout — this is what `SUDO_ASKPASS` points at.
- Run the command with `SUDO_ASKPASS=<path to askpass.sh>` and
  `ASKPASS_PROMPT="<the exact command>"` set in its environment. If the
  command's own internal `sudo` call can't find a controlling terminal, it
  falls back to the askpass helper automatically — this is standard macOS
  `sudo` behavior for GUI-launched installers, not something this skill
  invents. The password is typed by the user into that native dialog and
  never touches the session's own code path, the HTTP server, or any file.
- **This can't be verified as bulletproof for every possible cask/tool** —
  behavior depends on the invoked tool's own internal elevation call
  correctly detecting no-tty. Bound the command with a timeout (~90s of no
  new output is a reasonable heuristic, not just total wall-clock, since
  some installers are legitimately slow); if it hangs, kill it, mark the
  action `"failed"` with a note to run `command` manually in a real
  terminal instead, and move on rather than blocking the rest of the apply
  pass.
- Never persist, cache, log, or extend-timestamp any credential. No
  `NOPASSWD` sudoers edits, no `sudo -v` timestamp tricks. Each privileged
  command gets its own fresh native prompt.

### C.5 Repo Freshness and Config-Status Cross-Referencing

**Before researching (part of step 1, Collect):** check whether
macos-setup and the `dotfiles/` submodule are up to date with their
respective origins:
- `git fetch origin --quiet` in each repo (network read-only, never
  auto-pulls/merges — that's a separate decision for the user), then
  compare `git rev-parse HEAD` against `git rev-parse @{u}` (or
  `origin/master` if no upstream is configured) to get ahead/behind counts.
- If behind, surface it prominently at the top of the report (and mention
  it in conversation before generating) — recommendations grounded in a
  stale checkout can be wrong (e.g. a Brewfile pin someone already removed
  upstream). Don't block the review on it, just flag it loudly.
- Pull `git log --oneline -20` from each repo into `repo_context` (A.1) —
  this is research *context*, not something rendered verbatim as a big log
  dump in the UI (the recent-commits list backs `config_status`
  computation and gives research subagents visibility into very recent
  changes that might already address what they're about to suggest).

**Per tool, computing `config_status` (part of step 2, Research):** each
research subagent additionally:
1. Greps `git log --oneline -- <the tool's relevant files>` (its Brewfile
   line, its `tasks/*.sh` section, its dotfiles config) beyond just the
   last 20 — targeted history for files it's already inspecting for
   relevancy — for a commit whose message references this tool or its
   version.
2. Checks `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md`
   (the audit trail every prior session's machine-local upgrades get
   appended to — see step 8) for a prior entry naming this tool.
3. If either shows the config was already touched for a version ≥ today's
   `latest_version`: `state: "up_to_date"`. If touched for an *older*
   version and this run's headliners describe something that could
   invalidate that fix's reasoning (grep the fix's own commit message/diff
   for keywords that also appear in the new headliners — e.g. "libkrun",
   "Apple Silicon", the specific flag/requirement named): `state:
   "needs_attention"`, with `detail` explaining specifically what changed
   since. Otherwise `state: "unknown"` (say nothing rather than guess).
4. Cite the evidence (commit hash + subject, changelog.md entry date) in
   `config_status.evidence[]` same as any other finding.

This deliberately reuses the same "cite it or don't claim it" discipline
as `relevancy[]` — a `needs_attention` verdict without a concrete diff
between what changed is worse than no verdict at all.

### C.6 Bespoke `tasks/*.sh` Setup — Test the Fix, Don't Just Defer

Some tools have setup logic in `tasks/install.sh`/`tasks/config.sh` beyond a
plain package-manager command (podman's `install_podman_intel`, custom
`config_duti` mappings, etc.). The default posture for these is **not**
"mark the baseline upgrade `auto_runnable: false` and tell the user to
re-run `install.sh` themselves" — that punts on exactly the kind of fix
this skill exists to produce, and leaves the task script's own code
un-updated and untested. Instead:

1. **Research reads the current function implementation** and compares it
   against this run's headliners/changelog to judge whether the function's
   own logic (not just the package version) needs to change — e.g. a
   removed/renamed flag, a changed default, a new required step.
2. **If a change is needed, identify the specific underlying command(s)
   affected** — not the whole function, and never the whole
   `tasks/install.sh`/`./setup.sh install` entrypoint (that has broad,
   unrelated side effects and is exactly what CLAUDE.md's setup-script rule
   exists to prevent running unattended).
3. **Test those specific commands directly, non-destructively, before
   proposing anything.** This is the load-bearing safety rule: never run a
   test that could affect the user's real state (e.g. reinitializing their
   actual `podman-machine-default`, overwriting a real duti association).
   Prefer, in order: read-only inspection (`--help` output, `--version`,
   dry-run/`--dry-run` flags where the tool has them); a throwaway-named
   resource created and torn down within the same test (a scratch podman
   machine, a temp directory) — see the worked example in A.1's
   `brew:podman:fix-machine-init-flags` suggestion; if neither is possible
   for a given tool, that specific fix is not verifiable this way — fall
   back to the `auto_runnable: false` / `manual_reason` path for it
   (documented in A.1) instead of guessing.
4. **Propose a normal `kind: "edit"` suggestion** (design.md A.1) targeting
   the `tasks/*.sh` file, with a `diff_preview` reflecting the *tested* fix
   and a `rationale` that says plainly what was tested and how — this is
   the one category of suggestion held to a higher evidence bar than a
   plain changelog-motivated edit, precisely because machine-setup code is
   more consequential to get wrong than a comment or a Brewfile line.
5. **The baseline `kind: "upgrade"` suggestion for the plain package command
   stays separate and normal** (`auto_runnable: true` by default, per A.1)
   — a bespoke-setup problem in the *surrounding* task code is not a
   reason to block the *package* upgrade itself from auto-running.
6. When the accepted `edit` suggestion from step 4 is applied (SKILL.md
   step 7), it's a normal repo edit + commit — nothing about this section
   changes how accepted `edit` suggestions get applied.

### C.7 Watch Items — Persisting a Forward-Looking Concern

`config_status` (C.5) is backward-looking: "was this tool's config already
handled." Watch items are forward-looking: "the user flagged an ongoing
concern about this tool — does *this run's* changelog touch it." Example:
after investigating cursor-cli's shell-integration hook, the user wants any
future cursor-cli changelog mentioning shell-integration/recording to be
called out automatically, not re-investigated from scratch or missed.

**File**: `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json`
— a sibling to `changelog.md`, same directory, created on first use. Shape:

```jsonc
{
	"cask:cursor-cli": [
		{
			"topic": "shell-integration / session recording",
			"note": "User wants any change to cursor-agent's shell hook or `agent record` behavior called out — see 2026-07-06's investigation: install-shell-integration execs `agent record` on every new shell via ~/.zshrc, zsh-only, undocumented data handling. User implemented an on-demand cursor-record() function instead of the vendor's always-on hook.",
			"added_at": "2026-07-06"
		}
	]
}
```

One entry per concern (a tool can have several); `topic` is a short phrase
the next run's research subagent matches against its changelog content,
`note` is the fuller context so a hit can explain itself without re-deriving
everything.

**Adding a watch item** (part of step 7's `tool_comments`/`discuss`
investigation, SKILL.md): when investigating a comment concludes the user
has an *ongoing* concern — they care about this topic on future runs, not
just this one — append an entry here in addition to (never instead of)
surfacing any concrete suggestion normally. A one-off question ("does this
release fix the bug I hit last week?") doesn't need a watch item; a
standing preference ("I don't want this tool doing X, ever, without me
knowing") does. Use judgment; don't create a watch item for every comment
reflexively.

**Reading watch items** (part of step 2, Research): before researching, a
subagent checks whether its assigned tool(s) have any watch-items.json
entries and includes their `topic`/`note` in its own context. If this run's
headliners/changelog touch a watched topic, that's not a normal `info`
relevancy finding — bump it to at least `notable` severity (design.md A.1)
and prefix the summary so it reads as a highlight (e.g. "⚠ Watch item hit:
..."), citing the watch item's `note` as part of the evidence. This is the
one case where relevancy severity is elevated by something *other* than
the changelog content's own weight — a topic the user asked to be told
about earns extra prominence regardless of how minor the change looks on
its own.

## D. Template Variables

Exactly three tokens, replaced by plain string substitution (no template engine):

```html
<meta name="report-id" content="__REPORT_ID__">
<meta name="generated-at" content="__GENERATED_AT__">
<script>const REPORT = __REPORT_DATA__;</script>
```

```python
html = html.replace('"__REPORT_ID__"', json.dumps(report_id))
html = html.replace('"__GENERATED_AT__"', json.dumps(generated_at))
report_json = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
html = html.replace('__REPORT_DATA__', report_json)
```

`__REPORT_DATA__` is unquoted in the template so the JSON object lands as a JS
expression. The other two sit inside attribute quotes, so the replacement
target includes the quotes. The `"</"` → `"<\\/"` escape on `__REPORT_DATA__`
guards against a literal `</script>` inside any agent-written free-text field
(headliners, rationale, `config_status.detail`, `tool_comments`, ...) —
release notes and security advisories routinely quote HTML/JS snippets —
prematurely closing the `<script>` tag and corrupting the rest of the page.

Rendering is done in JS from `REPORT.tools[]`: sections carry
`data-tool-id` and `data-max-severity` attributes; suggestion cards carry
`data-suggestion-id` and `data-decision` (CSS attribute selectors drive visual
state). Submit walks the DOM to build the feedback payload.

Suggestion ids: `{source}:{name}:{slug}` — deterministic kebab-case slug of the
action, never index-based, unique within the report (`-2`, `-3` suffix on
collision). The session looks up accepted ids in its in-memory report to get
`target_files`, `diff_preview`, `rationale` for the edit.
