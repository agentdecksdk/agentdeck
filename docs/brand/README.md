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
| `contributor-welcome.svg` | the card the bot posts on a first pull request |
| `contributor-merged.svg` | the card it posts after a first merged one |

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

## components/ — the parts every composition is copied from

| Part | What it is |
|---|---|
| `components/card.svg` | the ace-cut card carrying the A, which is a hole in the path |
| `components/spark.svg` | the spark alone, box `x 880..1077, y 146..356` |
| `components/wordmark.svg` | `agentdeck` as live text, Poppins 700, tracking `-0.0354em` |

All three are `currentColor` and share one coordinate space
(`transform="translate(0,1254) scale(0.1,-0.1)"`), so a `<g>` pasted from any of them lands
correctly in any composition using that space. Colour belongs to the composition: the dark-mode
lockup is a blue card, a white A and a red spark, and the part files do not know that.

**Copy from here, never from a finished card.** Every other SVG in this directory is a composition
of these parts, and `tests/test_brand_assets.py` fails if one carries geometry that is in no
component, or sets the wordmark in anything but Poppins 700 at that tracking.

Copy, rather than reference, because reference does not survive the renderer:

| Mechanism | Result |
|---|---|
| `<use href="other.svg#id">`, default Chrome flags | renders blank, no error |
| `<use href="#id">` where the enclosing `<svg>` viewBox is tighter than the part's own space | disappears, no error |

Both failures are silent and land in a PNG that nobody looks at twice. A single source of truth
that survives every renderer would have to be generation rather than reference, and the test above
buys most of what that would, for none of the build step.

## Vectors only

The rule and its reasoning are in `docs/coding-standards.md` §11. What it means here: the
wordmark PNG, the mark PNG and the fourteen-panel brand sheet **live outside the repository**,
with the designer's source files, and `.gitignore` keeps `docs/brand/*.{png,webp,jpg}` out of the
tree. Only vectors are tracked, because a vector is text: reviewable in a pull request, and a few
kilobytes. The mark loses nothing by this, since `logo.svg` supersedes its PNG entirely.

Rasters that a GitHub comment or the social-preview upload genuinely needs are the one exception,
and they belong under `.github/assets/`, not here. Everything in this directory regenerates from
a recipe, so treat a PNG next to these files as a build artifact that escaped.

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

## The social card

`social-card.svg` is the 1280×640 image GitHub shows under every link to this repository in X,
LinkedIn, Slack, Discord and Hacker News. Left unset — as it was until 2026-08-12 — GitHub renders
its grey default with the repository name in it.

It is **not** committed as a PNG, for the same reason nothing else here is: `.gitignore:19` keeps
`docs/brand/*.png` out of the tree. Regenerate the upload artifact instead:

```bash
# Poppins (wordmark and tagline) and Inter (body) are live `<text>`, not paths, so they must be
# resolvable to the renderer. Skip if already installed system-wide.
mkdir -p ~/.local/share/fonts/agentdeck-render && cd "$_"
for f in ofl/poppins/Poppins-Bold.ttf ofl/poppins/Poppins-SemiBold.ttf ofl/inter/'Inter[opsz,wght].ttf'; do
  curl -sSLO "https://github.com/google/fonts/raw/main/$f"
done && fc-cache -f

# Chrome shrinks a 640-tall SVG to make room for a scrollbar it then hides, so render into a
# taller window at 2x and crop. Rendering straight to 1280x640 silently clips the last line.
cd "$(git rev-parse --show-toplevel)/docs/brand"
{ printf '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#0b1220}svg{display:block;width:1280px;height:640px}</style>'; cat social-card.svg; } > /tmp/card.html
google-chrome --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1280,800 --screenshot=/tmp/card-2x.png file:///tmp/card.html
magick /tmp/card-2x.png -crop 2560x1280+0+0 +repage -resize 1280x640 -strip social-card.png
```

Then upload `social-card.png` at **Settings → General → Social preview**. It is a manual upload;
no API sets it.

Two things in the SVG that look wrong and are not. The mark is the **dark-mode lockup** from §05 —
Agent Blue card, white A, Ace Red spark — so it is three fills, not the single `currentColor` the
other files here use. And the A is a **hole** in the card path, not a shape: both `nonzero` and
`evenodd` knock it out, because its subpath winds against the outer contour. The white rectangle
behind the card is what the A shows through. Keep it inside the card silhouette and below the cut
corner, or it appears as a white edge.

## The contributor cards

`contributor-welcome.svg` and `contributor-merged.svg` are what `first-contribution.yml` posts on
a contributor's first PR and after their first merged one. They are 1280×380 rather than the
social card's 1280×640: a comment renders them near 640px wide and near 320px on a phone, so the
type is sized for that render instead of scaled down from the social card.

Both carry their own copy of the lockup, same nested viewBox and same transform, taken from
`components/` and held to it by `tests/test_brand_assets.py`. The merged card adds the spark alone and
enlarged on the right. Its tight box is `x 880..1077, y 146..356` in the placed coordinate space,
measured with `getBBox()` rather than derived, since the nested `viewBox` is not square and
letterboxes under the default `preserveAspectRatio`.

Render them with the recipe above, changing only the height:

```bash
for card in contributor-welcome contributor-merged; do
  { printf '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#0b1220}svg{display:block;width:1280px;height:380px}</style>'; cat "$card.svg"; } > /tmp/$card.html
  google-chrome --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1280,560 --screenshot=/tmp/$card-2x.png file:///tmp/$card.html
  magick /tmp/$card-2x.png -crop 2560x760+0+0 +repage -resize 1280x380 -strip /tmp/$card.png
done
```

The PNGs belong under `.github/assets/`, which is the one place a tracked raster is allowed
(`docs/coding-standards.md` §11). They live there rather than here because a GitHub comment cannot
render an SVG.

## Naming

The mark reads `agentdeck`; prose says **AgentDeck SDK** on first mention and **AgentDeck**
thereafter. The qualifier lives in titles, descriptions and the domain — not in the glyph, where it
would read as a sub-brand of a parent product that does not exist. See
`docs/delivery/plan-adoption.md` §1.

## The typeface: Poppins, and the sheet's own label is wrong

This was open — the sheet's §07 names **Space Grotesk** for headings while the wordmark looked
like something else. Setting the social card decided it, because a wordmark either matches or it
does not.

**The wordmark is Poppins Bold.** The `a` is **single-storey**, and Space Grotesk's is
double-storey — that alone rules it out, and the geometric `e`, the straight-tailed `g`, the
angled cut on the `t` and the straight `k` diagonals all agree. Rendered side by side against
`wordmark.png`, Poppins 700 matches and Space Grotesk 700 is not close.

So two of three sources always did agree, and the third is a label in the sheet contradicting the
sheet's own artwork. Poppins is what the wordmark is, what the site loads, and what the card sets.
Space Grotesk should be treated as a typo unless someone produces a wordmark drawn in it.

The palette is not affected — the sheet's hexes and the site's `brand.css` already match exactly.
The wider presentation question stays with the docs-site design pass (#140).
