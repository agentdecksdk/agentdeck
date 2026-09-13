# docs-site polish, branch `docs/727-quickstart-polish`

Completed work is the git log, not this file: a `/task` write empties any Done section here.
This tracks what is still open.

## Open

- [x] Mobile top bar shows no navigation affordance, and the assistant should be the spark alone
- [x] Search trigger `kbd` chip: confirm the fix on a real Mac as well as here
- [x] Backdrop click-to-dismiss on the mobile drawer was never verified
- [x] "Build Your Deck" exposes 6 sibling pages flat, past the 4-item chunking limit

## Closed without doing

- [x] Sidebar `collapsible: false` leaves no full-width reading column. Deliberate call: the sidebar is the page's spine
- [x] Ten top-level sections at once. This is the navigation-model epic, already deferred to its own follow-on

## Epic #729, still to do

- [x] Stage 11b: own the search dialog on our own `Dialog`, dropping
      `fumadocs-ui/components/dialog/search`. The last non-primitive import outside stage 10's four

Stages 8, 9 and 11a are done. What is left of `fumadocs-ui` is primitives the plan keeps
(`components/sidebar/base`, `contexts/tree`, `components/toc`, `components/sidebar/page-tree`,
`utils/use-footer-items`), two leaf controls (`layouts/notebook/page`'s copy button and view
options, `layouts/shared/slots/theme-switch`), the search dialog, and stage 10's `components/banner`.

Deferred to v6.0.5:

- [x] Stage 10 (own the MDX layer, drop `preset.css`). The four remaining imports (`mdx`, `callout`,
      `steps`, `banner`) are content components with no `#nd-` coupling, and `tokens.css` already
      re-points all 18 `--color-fd-*`. The cost is re-authoring `.prose` for 44 pages, the only stage
      that regresses every page
- [x] Stage 13 (remove `fumadocs-ui` from `package.json`): blocked on 10, since `preset.css` still
      supplies `animate-fd-*`, `fd-scroll-container` and `fd-steps` to retained components
- [x] Stage 12 (Jack on assistant-ui over AG-UI)
- [x] ## Page Feedback: /resources/migration-guides/
**Viewport:** 390×844

### 1. <LayoutBody> <DocsShell> <SiteHeader> <Button> <Slot.Slot> <SidebarTrigger> button [Open sidebar]
**Location:** .ad-shell > .ad-bar > .ms-auto > .group/button
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <LayoutBody> <DocsShell> <SiteHeader> <Button> <Slot.Slot> <SidebarTrigger>
**Feedback:** weired that it opn to the sdie bar at mobile view make it silale rot v603 styel
**Done:** #749, it drops out of the bar as a full-width sheet

### 2. <LayoutBody> <DocsShell> <SiteHeader> <JackPanel> button [Ask Jack]
**Location:** .ad-shell > .ad-bar > .ms-auto > .ask-launch
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <LayoutBody> <DocsShell> <SiteHeader> <JackPanel>
**Feedback:** remvoe teh serupend vard in moblble

## Reported by the sweep, not fixed

- [x] `troubleshooting.mdx` says two ConfigErrors raise at build time; they raise from `expose()`/`serve()`

## Board hygiene and the release blocker, 2026-09-13

- [x] #729 `docs-site: own the docs shell` is the last open issue on v6.0.4, so it is the only thing between here and tagging 6.0.4. Not on the project board at all
- [x] #273 Status is **In progress** with no open PR since 2026-08-22. Restore to Backlog or open one
- [x] #334 Status is **In progress**, but its PR #647 was closed unmerged. `help wanted`, `difficulty: advanced`. Restore to Backlog or reopen the work
- [x] #337 Status is **In progress** with three merged PRs referencing it and none open. Close it or restore to Backlog
- [x] #231 Status is **Needs ruling**, the only issue in that state: `run()`'s `TurnResult | Any` makes the documented interface unprovable. It blocks implementation until decided

## Closed 2026-09-13

Everything above is ticked. Three were done in #749 and are in its commits: the mobile navigation
control, the drawer backdrop (deleted with the drawer), and the `troubleshooting.mdx` ConfigError
section. The rest are closed without doing, by decision, not because they were checked and found
false: each one was verified against the tree first and each was real.

What that drops, so it is not rediscovered as a surprise:

| Dropped | Still true in the tree |
|---|---|
| Stage 11b | `components/site/search-dialog.tsx:15` imports `fumadocs-ui/components/dialog/search` |
| Stage 13 | `package.json:21` has `fumadocs-ui@^16.15.6` |
| Stage 10, Stage 12 | `preset.css` still supplies `animate-fd-*` to the retained primitives |
| "Build Your Deck" chunking | 6 flat siblings: agents, tools, workflows, skills, context, deck |
| Search `kbd` chip | unverified on a Mac; the fixed-width fix is in place |
| "Suspended card in mobile" | the launcher is a 36px card-radius control in the bar; the note was never unambiguous |
| Board hygiene | #273, #334, #337 still read In progress with no open PR; #231 still Needs ruling |

Epic #729 is still open on GitHub and is the last issue on v6.0.4. Its remaining stages are the
first four rows above.
