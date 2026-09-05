# docs-site polish, branch `docs/727-quickstart-polish`

Completed work is the git log, not this file: a `/task` write empties any Done section here.
This tracks what is still open.

## Open

- [ ] Mobile top bar shows no navigation affordance, and the assistant should be the spark alone
- [ ] Search trigger `kbd` chip: confirm the fix on a real Mac as well as here
- [ ] Backdrop click-to-dismiss on the mobile drawer was never verified
- [ ] "Build Your Deck" exposes 6 sibling pages flat, past the 4-item chunking limit

## Closed without doing

- [ ] Sidebar `collapsible: false` leaves no full-width reading column. Deliberate call: the sidebar is the page's spine
- [ ] Ten top-level sections at once. This is the navigation-model epic, already deferred to its own follow-on

## Epic #729, still to do

- [ ] Stage 3 second half: split `brand.css` (started, agent hit the session limit before writing; orphan base.css/docs.css removed) into `styles/{base,docs,utilities}.css` plus colocated component sheets
- [ ] Stages 8-9: own the page and TOC, then drop `DocsLayout`
- [ ] Stages 10-11: own the MDX layer and typography, own the providers, drop `preset.css` and `RootProvider`
- [ ] Stage 13: remove `fumadocs-ui` from `package.json`
- [ ] Stage 12 (Jack on assistant-ui over AG-UI) deferred to v6.0.5
- [ ] ## Page Feedback: /resources/migration-guides/
**Viewport:** 390×844

### 1. <LayoutBody> <DocsShell> <SiteHeader> <Button> <Slot.Slot> <SidebarTrigger> button [Open sidebar]
**Location:** .ad-shell > .ad-bar > .ms-auto > .group/button
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <LayoutBody> <DocsShell> <SiteHeader> <Button> <Slot.Slot> <SidebarTrigger>
**Feedback:** weired that it opn to the sdie bar at mobile view make it silale rot v603 styel

### 2. <LayoutBody> <DocsShell> <SiteHeader> <JackPanel> button [Ask Jack]
**Location:** .ad-shell > .ad-bar > .ms-auto > .ask-launch
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <LayoutBody> <DocsShell> <SiteHeader> <JackPanel>
**Feedback:** remvoe teh serupend vard in moblble

## Reported by the sweep, not fixed

- [ ] `troubleshooting.mdx` says two ConfigErrors raise at build time; they raise from `expose()`/`serve()`
