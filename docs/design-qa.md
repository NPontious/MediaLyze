# Storage Map adaptive labels and rich tooltip design QA

- Source visual truth: `prototypes/storage-map-streamlined-chrome-desktop.png` for the existing Storage Map composition and `prototypes/storage-map-overlay-ui-catalog.png` for the previous small-tile label behavior.
- Browser-rendered implementation: `prototypes/storage-map-adaptive-labels-desktop.png`.
- Adaptive-label implementation fixture: `prototypes/storage-map-adaptive-faded-labels-ui-catalog.png`.
- Rich-tooltip implementation fixture: `prototypes/storage-map-rich-tooltip-ui-catalog.png`.
- Dark-theme color-regression implementation: `prototypes/storage-map-color-regression-fixed-desktop.png`.
- Full comparison: `prototypes/storage-map-adaptive-labels-qa-comparison.png`.
- Focused label comparison: `prototypes/storage-map-adaptive-labels-qa-focus-comparison.png`.
- Focused tooltip comparison: `prototypes/storage-map-rich-tooltip-qa-comparison.png`.
- Source and implementation: 1306 × 1204 pixels at a 1306 × 1204 CSS viewport, device pixel ratio 1.
- Density normalization: none required.
- State: dark theme; Storage Map root for the full view; representative compact tile and open hover card in the canonical `/ui-elements` fixture for focused interaction evidence.

## Full-view comparison evidence

The combined full-view comparison confirms that the adaptive labels and custom hover card do not change the established MediaLyze composition, toolbar density, treemap proportions, tile colors, or viewport-filling behavior. The file tiles remain directly clickable and preserve the existing size-proportional layout.

## Focused comparison evidence

The focused label comparison shows the former all-or-nothing behavior beside the revised compact tile: the long asset name is now retained, wraps when space allows, and fades at the right edge instead of leaving the tile blank. Metadata remains visible while a line fits; the least-important size label is removed first.

The focused tooltip comparison shows the same tile at rest and with its custom hover card open. The card appears next to the tile, uses the MediaLyze panel surface, border, radius, shadow, typography, and semantic metric badge, and exposes the full name plus storage, file count, codec, resolution, HDR, and quality information without navigating away.

## Required fidelity surfaces

- Fonts and typography: existing tile weights, sizes, and line heights are preserved. Names can wrap, compact tiles progressively reduce secondary lines, and the edge mask avoids a hard ellipsis while retaining readable leading text.
- Spacing and layout rhythm: tile geometry and zero-gap treemap packing are unchanged. The hover card uses compact internal spacing and definition-list alignment consistent with existing MediaLyze panels.
- Colors and visual tokens: tile colors continue to come from the active metric. The hover card uses the existing panel, border, muted-text, foreground, and accent tokens and remains legible on every tested tile color.
- Image and asset fidelity: existing Lucide file/folder icons are reused; no raster or approximate replacement assets were introduced.
- Copy and content: the card reuses existing localized labels and displays the full asset/folder name, active metric, storage, count where applicable, codec, resolution, HDR, and quality data. No new untranslated copy was added.

## Interaction and runtime evidence

- A tile has no native `title`, so the delayed system tooltip is not used.
- The custom hover card opens after 80 ms, closes on pointer leave, does not pin on click, and automatically flips above when there is insufficient space below.
- File clicks still navigate to file details; folder clicks still descend into the selected folder.
- Labels are hidden only below the final 42 × 28 px threshold, where a usable text line no longer fits.
- At intermediate sizes, the tile changes from full name/metadata/size to name/metadata and finally name-only before hiding all copy.
- Browser console after the final root-route reload: no errors or warnings.
- Dark-theme color verification: the computed tile background now matches `--storage-map-tile-color`; the size mode produced four distinct computed colors for the four differently sized test files. The codec mode correctly produced one shared green because all four test files are H.265/HEVC.
- Focused frontend tests passed: 5 of 5 across Storage Map and TooltipTrigger; the broader targeted pass passed 23 of 23 across Storage Map, TooltipTrigger, and App Shell.
- Production frontend build passed.
- `git diff --check` passed.

## Comparison history

1. Earlier P2: labels were hidden as a complete block whenever all text did not fit, leaving usable medium-size tiles blank.
   - Fix: replace JavaScript all-or-nothing measurement with CSS container-query tiers that preserve at least the name while one line fits.
   - Post-fix evidence: `prototypes/storage-map-adaptive-labels-qa-focus-comparison.png`.
2. Earlier P2: long names ended abruptly or forced the layout to suppress all copy.
   - Fix: permit wrapping at usable sizes and apply a right-edge mask fade, while progressively hiding size and metadata before the name.
   - Post-fix evidence: `prototypes/storage-map-adaptive-faded-labels-ui-catalog.png`.
3. Earlier P2: the browser-native tooltip appeared slowly and exposed only a plain text string.
   - Fix: use the shared tooltip primitive with an 80 ms hover delay and a structured MediaLyze metadata card.
   - Post-fix evidence: `prototypes/storage-map-rich-tooltip-qa-comparison.png`.
4. Earlier P2: a fixed below-tile card could be clipped near the viewport edge.
   - Fix: add automatic above/below placement based on available viewport space.
   - Post-fix evidence: interaction inspection and the focused tooltip comparison.
5. Earlier P1 regression: converting each tile into the shared tooltip trigger allowed the later dark-theme `.tooltip-trigger` background rule to override every per-node color.
   - Fix: scope the Storage Map background rule through the treemap and combined tile/tooltip classes so its specificity remains above the global dark-theme tooltip rule.
   - Post-fix evidence: `prototypes/storage-map-color-regression-fixed-desktop.png` and computed-style checks across codec and size modes.

## Findings

No actionable P0, P1, or P2 findings remain.

## Follow-up polish

No P3 follow-up is required for this refinement.

final result: passed
