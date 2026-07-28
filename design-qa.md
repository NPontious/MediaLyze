# Storage Map viewport-fill design QA

- Source visual truth: `prototypes/storage-map-select-chevron-desktop.png` (the previous fixed-height treemap state corresponding to the annotation).
- Browser-rendered implementation: `prototypes/storage-map-viewport-fill-desktop.png`.
- Mobile implementation: `prototypes/storage-map-viewport-fill-mobile.png`.
- Full comparison: `prototypes/storage-map-viewport-fill-qa-comparison.png`.
- Focused lower-page comparison: `prototypes/storage-map-viewport-fill-qa-focus-comparison.png`.
- Desktop source and implementation: 1306 × 1204 pixels at a 1306 × 1204 CSS viewport, device pixel ratio 1.
- Mobile implementation: 390 × 844 pixels at a 390 × 844 CSS viewport, device pixel ratio 1.
- Density normalization: none required.
- State: Storage Map root, dark theme, library “Filme”, color “Video codec”, order “Size”.

## Full-view comparison evidence

The side-by-side comparison shows that the treemap now consumes the previously unused lower panel area while preserving the existing header, toolbar, footer, tile proportions, and MediaLyze visual language. The app shell finishes at the normal 48 px bottom page gutter instead of leaving a large empty region inside the Storage Map panel.

## Focused comparison evidence

The lower-page crop was needed to judge the annotated vertical-space issue. It confirms that the treemap footer remains directly beneath the tiles and moves with the viewport-filling stage, rather than stretching itself or leaving an empty panel region. The root view uses three grid rows; nested folder views retain four rows so the breadcrumb does not reduce or misplace the footer.

## Required fidelity surfaces

- Fonts and typography: all labels, filenames, metadata, and footer text retain their existing family, weight, line height, wrapping, and optical hierarchy.
- Spacing and layout rhythm: the Storage Map page now takes the app shell's remaining grid row. At 1204 px viewport height the panel ends at 1156 px, respecting the existing 48 px page-bottom gutter; the treemap grows from 460 px to 862 px.
- Colors and visual tokens: surfaces, borders, shadows, tile colors, and theme tokens are unchanged.
- Image and asset fidelity: no imagery or icon assets changed.
- Copy and content: no user-visible copy changed.

## Interaction and runtime evidence

- Root view: stage height 862 px, footer height 35 px, no page overflow.
- Nested-folder/error view: breadcrumb height 24 px, stage height 861.5 px, footer height 35 px, and the Up overlay remains available.
- Mobile view: the existing 430 px functional minimum is preserved, horizontal overflow remains absent (`scrollWidth === 390`), and the page scrolls naturally when the stacked controls exceed the viewport.
- Primary interactions checked: root rendering, nested-path rendering, and parent-folder navigation availability.
- Browser console after the final implementation: no errors or warnings.
- Focused Storage Map tests passed: 3 of 3.
- Production frontend build passed.
- `git diff --check` passed.

## Comparison history

1. Earlier P2: the explorer had a capped 840 px minimum height while its content remained only 543 px tall, leaving 297 px unused inside the panel.
   - Fix: make the Storage Map app shell a two-row viewport-height grid and stretch the page, panel, explorer, and content through the remaining row.
   - Post-fix evidence: `prototypes/storage-map-viewport-fill-qa-comparison.png`.
2. Earlier P2 found during the first implementation pass: hiding the root breadcrumb shifted the footer into the flexible grid row, stretching it to 437 px while the treemap remained 460 px tall.
   - Fix: use a root-specific three-row grid (`toolbar / treemap / footer`) and retain the four-row layout when breadcrumbs are visible.
   - Post-fix evidence: final browser measurements report rows `48px 862px 35px`; the footer remains 35 px.

## Findings

No actionable P0, P1, or P2 findings remain.

## Follow-up polish

No P3 follow-up is needed for this refinement.

final result: passed
