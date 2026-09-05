- [ ] ## Page Feedback: /meet-agentdeck/quickstart/

### 1. <LayoutBody></layoutbody> <DocsShell></docsshell> <SiteHeader></siteheader> <FullSearchTrigger></fullsearchtrigger> kbd

**Location:** .ad-bar > .inline-flex > .ms-auto > .rounded-md
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <LayoutBody></layoutbody> <DocsShell></docsshell> <SiteHeader></siteheader> <FullSearchTrigger></fullsearchtrigger>
**Feedback:** when refreshing this card cahnges look fix that

## From the impeccable critique (dual-agent sweep, 2026-09-05)

Fixed already: mobile grid 257px -> 390px; Ask Jack sheet anchoring; grey ink 2.48:1 -> 4.60:1.

### P1 accessibility, every page
- [x] Ten touch targets under 44x44, clustered top-right at 390: Ask Jack 28x36, GitHub 30x30, sidebar 32x32, search 34x34, banner close 30x30, heading anchors 24x24, code copy 24x24, banner link 358x19, logo lockup 193x40, inline nav cards ~25-32 high

### P2
- [x] Quickstart skips a heading level, h1 -> h3 at "Install"
- [x] Ask Jack is an unlabelled red spark below 768; reads as a notification badge, not an entry point

### P3 and polish
- [x] Sidebar shows 10 top-level sections at once, and "Resources" holds one child also called "Resources"
- [ ] "Build Your Deck" exposes 6 sibling pages flat, past the 4-item chunking limit
- [x] `run.mdx:16` renders a plain "and" bold and oversized inside a link
- [x] Long tables have no zebra striping or row rhythm; easy to lose your row returning from the last column
- [ ] Sidebar is `collapsible: false`, so there is no full-width reading column at any viewport
- [ ] Backdrop click-to-dismiss on the mobile drawer was never verified

### Carried over, not from the sweep
- [ ] Copy page / Open in ChatGPT affordance, lost in the Fumadocs migration
- [ ] Content sweep of the 43 docs pages: six reviewers were stopped part-way, only fragments survived
- [ ] ## Page Feedback: /meet-agentdeck/quickstart/#build-your-deck
**Viewport:** 1920×911

### 1. <DocsPage> <Container> <DocsBody> font medium
**Location:** #nd-page > .prose > .group/alert > .font-medium
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <DocsPage> <Container> <DocsBody>
**Feedback:** remove this font type
- [ ] fix this : ## Page Feedback: /meet-agentdeck/quickstart/#build-your-deck
**Viewport:** 1920×911

### 1. <DocsPage> <Container> <DocsBody> font medium
**Location:** #nd-page > .prose > .group/alert > .font-medium
**Source:** _next/static/chunks/node_modules_agentation_dist_index_mjs_1bt__qk._.js:14067:23
**React:** <DocsPage> <Container> <DocsBody>
**Feedback:** remove this font type
- [ ] in mobile view the askjack need to animae in form the bootm up not rihgt left like deskto
- [ ] in mbile view the topbar dont show the naviaiog option at all. and also teh askjack is to much need onl the spark
