# DESIGN.md — wall.cloud Seasonal

Use this file with Codex / GPT before any visual work on
https://jwallio.github.io/seasonal/

Stack to preserve:
- Static GitHub Pages viewer
- `public/seasonal/index.html`
- `public/seasonal/dashboard.css`
- `public/seasonal/dashboard.js`
- Catalog-driven data from `seasonal/catalog.json`
- Existing URL state: view, model, product, run, target, compare, period, reference, role, available

Do not migrate to React, Next, or a component framework unless explicitly asked.
Restyle and restructure the existing HTML/CSS/JS.

---

## Product

Seasonal is an operational forecast desk for meteorologists and serious
weather users. The job of the UI is to answer, in order:

1. Which models and fields are usable right now?
2. How fresh is each surface?
3. Open the map.
4. Compare the same field across models.
5. Inspect analogs when looking at 500-mb height.

The maps and the availability matrix are the product.
Chrome is secondary.

---

## What to keep

- Dark weather-ops palette
- wall.cloud brand lockup
- Overview / Explore / Compare information architecture
- Status vocabulary: Fresh, Aging, Stale, Partial, Failed, No map, N/A
- Freshness rules already encoded in `dashboard.js`
- Super Ensemble as the default blend
- Share / download / copy-link behavior
- Analog panel on Compare for 500-mb
- Catalog and manifest loading
- Existing model families, blends, and parameters

## What to change

- The four identical KPI cards. They currently look like generic SaaS stats.
  Make "Needs attention" the only loud stat. The others can be compact metadata.
- The split header. Brand, tabs, overflow menu, and "Open Super Ensemble"
  should read as one command bar, not four separate objects.
- The Availability matrix should be the first-screen hero, not something
  under a banner of system prose.
- Status pills should be filterable. Clicking "Aging" or "Needs attention"
  should isolate those cells / rows.
- Opening a map from Overview should feel like the primary action, not a
  side effect of a pill.
- Notices should be exceptions only. Healthy state should be quiet.
- Grammar and density: "1 surface have partial ensemble coverage" and the
  long coverage sentence should become a short exception list.

## Forbidden

- Purple/teal marketing gradients that compete with the maps
- Glassmorphism
- Hero headlines, feature-card grids, testimonials
- Animated orbs, particles, 3D, or "AI slop" decoration
- A second design system or new font family
- Inventing new status words
- Hiding cadence, init time, or next-update timing
- Motion that is not explaining a state change

---

## Visual tokens (current — keep unless a change is explicit)

```css
:root {
  color-scheme: dark;
  --ink: #f3f7f9;
  --muted: #a9bac3;
  --panel: #10232d;
  --line: #2b4855;
  --teal: #59d4c0;
  --coral: #ff927c;
  --blue: #75b8e8;
  --map: #e8eff2;
}
```

Use teal for Fresh / on-schedule / primary action.
Use gold/amber only for Aging / processing.
Use coral for overdue, failed, or needs-attention.
Use purple only for Partial, and only as a badge, never as a theme.
Keep Inter / system-ui.

Radius can come down slightly on operational surfaces (12–16px instead of 22px
on every card) so the matrix feels like an instrument panel, not a marketing
page.

---

## External resources — use like Lego

Inspect the named resource first. Copy structure and interaction, then restyle
to the tokens above. Do not keep demo branding.

### Use on this site

1. https://ui.shadcn.com
   Tabs, Badge, Dialog, Button, Dropdown menu, Tooltip.
   Map Overview tabs and the map dialog onto these interaction patterns.

2. https://reui.io/components
   Data Grid, Records Table, Filter patterns, KPI alternatives.
   The Availability matrix should behave like a dense operational grid:
   sticky first column, row groups, status cells as the click target.

3. https://coss.com/ui
   Accessible Table, Tabs, Dialog, Toast.
   Use for keyboard behavior and quieter component anatomy.

4. https://www.beautifului.dev
   Only these primitives: Records Table, Filter Table, Insight Cards,
   Status / task rows.
   Do not import chat, thinking traces, prompt bars, or agent chrome.

5. https://transitions.dev
   Dialog open/close, badge pop, table row reveal, error shake.
   One transition per interaction. No page-wide motion.

6. https://emilkowal.ski/ui/you-dont-need-animations
   Default posture. If a transition does not explain freshness or map
   loading, delete it.

7. https://www.designsystemchecklist.com
   QA pass after visual work: contrast, focus, touch targets, loading,
   empty, error, reduced motion.

### Do not use here unless a later pass is explicitly decorative

- https://www.rareui.com
- https://beui.dev
- Marketing landing kits, hero sections, tilt cards, orbs

---

## Page recipes

### Overview

Primary scan path:

1. Brand + view tabs + primary action
2. Exception strip (only if something is aging, partial, failed, or late)
3. Availability matrix
4. Footer notes / methodology

KPI row: collapse to a single compact status line, or keep four metrics
but make only "Needs attention" a large number. Clicking it filters the
matrix to those surfaces.

Matrix:
- Row groups stay: Frequent refresh vs monthly/release-window models
- First column sticky
- Status cell is a button. It opens the map and sets Explore state
- Disabled cells (No map / N/A) are not buttons
- Hover shows init time, age, and coverage without leaving the grid
- A filter chip row above the grid: All, Fresh, Aging, Partial, Attention

### Explore

Keep model / field / run / valid-period selectors, but treat them as a
single filter bar above a large map. Facts (model, valid period, lead,
ensemble, field, status) belong in a compact legend, not a competing card
column.

### Compare

Keep the compare grid and analog panel. The analog panel is domain-specific
and should stay visually secondary to the model grid. Do not turn analogs
into a marketing "insights" module.

### Map dialog

Keep Share, Download, Close. Title must include model + field + valid
period. Focus trap and Esc-to-close must continue to work.

---

## Acceptance criteria for a redesign pass

- Catalog loading, URL state, and map fallbacks still work
- Fresh / Aging / Partial / Failed logic is unchanged
- A user can open CFSv2 500-mb height from Overview in two clicks
- Needs-attention surfaces are filterable
- No new dependencies unless they are copy-pasted CSS/JS into this repo
- Contrast on pills and muted text still holds on the dark panel
- Mobile: matrix stacks without losing status meaning
- Reduced-motion users get instant state changes

---

## How to work with the agent

Every UI task must follow this loop:

1. Read this file and the current `index.html` / `dashboard.css` / `dashboard.js`
2. Inspect only the resource named in the prompt
3. List the 3–6 pieces being borrowed
4. Implement against the existing files
5. Report what was borrowed and what was left alone
