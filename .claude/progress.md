# docs-site polish, branch `docs/727-quickstart-polish`

## Done

- [x] Mobile grid: article 257px -> 390px, the footer's grid area was taking a third of the viewport
- [x] Ask Jack sheet anchored to the top of the phone instead of the bottom
- [x] Grey ink at 2.48:1 on the light canvas, 13 rules, now 4.60:1
- [x] refine-brand is the canonical geometry; two different sparks no longer ship on one page
- [x] `components/card-a.svg`: the A is a hole again, so the mono mark is not a blank card
- [x] Zero `nav` landmarks: sidebar and table of contents now carry the role
- [x] Mobile drawer ignores Escape
- [x] Mobile drawer drops focus to `body` instead of the trigger
- [x] Reference tables illegible at 390: one card per row below 768
- [x] `whats-new-6` first table had a 746px minimum
- [x] Duplicate hidden sidebar trigger (closed: it is fumadocs-ui's own drawer-close button, not ours)
- [x] Ten tap targets under 44x44 at 390
- [x] Search shortcut chip resized on every non-Mac load, 52.4px -> 72.0px
- [x] Quickstart skipped a heading level, h1 -> h3
- [x] Ask Jack read as a notification badge below 768
- [x] `run.mdx:16` rendered a plain "and" bold inside a link
- [x] `---Resources---` separator duplicated the only label under it
- [x] Long tables had no row rhythm above 768

## Open

- [ ] Callout title carries `font-medium`: remove that type. `/meet-agentdeck/quickstart/#build-your-deck`, `.group/alert > .font-medium`
- [ ] Mobile Ask Jack should animate up from the bottom, not in from the right as on desktop
- [ ] Mobile top bar shows no navigation affordance, and the assistant should be the spark alone
- [ ] Search trigger `kbd` chip: confirm the fix on a real Mac as well as here
- [ ] Copy page / Open in ChatGPT affordance, lost in the Fumadocs migration
- [ ] Content sweep of the 43 docs pages: six reviewers were stopped part-way, only fragments survived
- [ ] Backdrop click-to-dismiss on the mobile drawer was never verified
- [ ] "Build Your Deck" exposes 6 sibling pages flat, past the 4-item chunking limit

## Closed without doing

- [ ] Sidebar `collapsible: false` leaves no full-width reading column. Deliberate call: the sidebar is the page's spine
- [ ] Ten top-level sections at once. This is the navigation-model epic, already deferred to its own follow-on

## Epic #729, still to do

- [ ] Stage 3 second half: split `brand.css` into `styles/{base,docs,utilities}.css` plus colocated component sheets
- [ ] Stages 8-9: own the page and TOC, then drop `DocsLayout`
- [ ] Stages 10-11: own the MDX layer and typography, own the providers, drop `preset.css` and `RootProvider`
- [ ] Stage 13: remove `fumadocs-ui` from `package.json`
- [ ] Stage 12 (Jack on assistant-ui over AG-UI) deferred to v6.0.5
