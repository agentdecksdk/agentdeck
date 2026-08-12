# Brand assets

The AgentDeck mark: an ace-cut card carrying the A. The notched corner reads as both a playing
card and a file, so "deck" lands twice without drawing a literal card — it is the best idea in the
system and the reason the mark survives at one colour.

## What is here

| File | Use it for |
|---|---|
| `logo.svg` | the mark with its spark — headers, READMEs, anywhere above ~48px |
| `logo-mark.svg` | the card alone — small sizes, dense UI, anywhere the spark would be noise |
| `favicon.svg` | square and centred on the card — favicons, app icons, avatars |
| `logo-blue.svg` | the mark in Agent Blue, for anywhere it is a *file* rather than markup |
| `logo-traced-original.svg` | provenance only — see below |

The first three are `fill="currentColor"`, so they take the colour of whatever they sit in and
need no light/dark variants — provided they are **inlined as markup**.

`logo-blue.svg` exists because that proviso is real: an SVG loaded through `<img src>` has no
parent to inherit from, so `currentColor` falls back to black, and on GitHub's dark theme the mark
is then black on near-black. Verified side by side rather than assumed. Agent Blue reads on both
themes, and the A's counter is transparent so each background shows through it — which is why one
coloured file covers both instead of needing a pair.

```html
<span style="color: #2563FF">  <!-- Agent Blue -->
  <svg …>…</svg>
</span>
```

## Vectors only — no rasters in this repository

**This repository tracks no binary images, and did not before these assets arrived.** The
`check-added-large-files` pre-commit hook is the enforcement, and it is the right rule: a binary
cannot be diffed, cannot be reviewed, and cannot be removed from git history later without
rewriting it for everyone.

So the wordmark PNG, the mark PNG and the fourteen-panel brand sheet **live outside the
repository**, with the designer's source files. Only vectors are tracked, because a vector is
text: reviewable in a pull request, and a few kilobytes.

The mark loses nothing by this — `logo.svg` supersedes its PNG entirely.

## Still owed from the designer

- **`wordmark.svg`.** The wordmark exists only as a raster, so it is the one asset this directory
  cannot offer. It should not be traced: type belongs in outlines from the real font, not in an
  approximation of a screenshot of it.
- **The original vector for the mark.** `logo-traced-original.svg` is what was handed over: a
  **potrace trace of a PNG**, not an export. The tells are the SVG 1.0 DTD, the
  `translate(0,1254) scale(0.1,-0.1)` flip, and a viewBox of exactly the PNG's pixel width. The
  files here are cleaned from that trace — unitless, `currentColor`, titled, correctly cropped,
  each one rendered and measured in a browser rather than eyeballed. Two things a cleanup cannot
  fix: traced outlines are polygon approximations (invisible at UI sizes, visible on a hero or in
  print), and a crop fixes framing rather than drawing.
- **A 1280×640 social card.** GitHub renders its grey default on every share to X, LinkedIn, Slack
  and Hacker News until one exists, and the brand sheet is not it — fourteen panels are unreadable
  at thumbnail size.

## Why the favicon drops the spark

At 16px the spark is about three grey pixels — noise rather than signature. Keeping it would also
force a non-square box, because it sits outside the card's top-right corner, and squaring that box
shrinks the card to roughly two-thirds the width it could otherwise use. The brand sheet's own
favicon panel shows the same shrinkage.

So `favicon.svg` is the card, centred in a square of its own height, filling the box edge to edge.
The card is already a rounded square, so it reads correctly under a platform's own icon mask.

## Naming

The mark reads `agentdeck`; prose says **AgentDeck SDK** on first mention and **AgentDeck**
thereafter. The qualifier lives in titles, descriptions and the domain — not in the glyph, where it
would read as a sub-brand of a parent product that does not exist. See
`docs/delivery/plan-adoption.md` §1.

## Unresolved: the typeface

Three sources disagree. The brand sheet specifies **Space Grotesk** for headings; the wordmark
appears to be set in a geometric sans closer to **Poppins**; and the live docs site
(`docs-site/app/layout.tsx`) uses **Poppins**. Two of the three agree, and the sheet is the odd one
out — but it should be decided rather than left to whichever file someone opens first. Tracked
against the docs-site design pass (#140), along with the palette, which the sheet also changes
wholesale.
