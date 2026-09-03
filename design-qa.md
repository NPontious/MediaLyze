# Design QA: Organic Storage Map folder fields

## Visual truth and evidence

- Selected ImageGen source: `/Users/frederikemmer/.codex/generated_images/019fa7af-1da8-7832-a28e-5c7502c6ca4b/call_IhsivhxZTStdef16VQhS55B7.png`
- Source size: 1672 × 941; normalized comparison size: 1280 × 720.
- Desktop implementation: `/Users/frederikemmer/.codex/visualizations/2026/07/28/019fa7af-1da8-7832-a28e-5c7502c6ca4b/storage-map-organic-folder-gradient-implementation.jpg`
- Focused source/implementation comparison: `/Users/frederikemmer/.codex/visualizations/2026/07/28/019fa7af-1da8-7832-a28e-5c7502c6ca4b/storage-map-organic-folder-gradient-focus-comparison.png`
- Full source/live-data comparison: `/Users/frederikemmer/.codex/visualizations/2026/07/28/019fa7af-1da8-7832-a28e-5c7502c6ca4b/storage-map-organic-folder-gradient-full-comparison.png`
- Responsive evidence: `storage-map-tablet-implementation.jpg` at 768 × 900 and `storage-map-mobile-implementation.jpg` at 390 × 844 in the same visualization directory.

The connected test library currently contains files but no folder nodes. The real route therefore verifies the production layout, metric colors, and controls; the canonical `/ui-elements` fixture verifies the mixed-folder visual state. Backend and frontend tests verify that the fixture behavior is driven by the real byte-weighted distribution contract.

## Fidelity surfaces

- Layout and spacing: the field stays clipped to the exact folder tile and introduces no gaps, borders, or extra layout space.
- Color: folders with multiple represented colors render seven soft, overlapping fields across all four corners and interior positions. Field allocation is storage-weighted. Single-color folders and all files retain the existing solid metric color.
- Shape and image quality: a blurred, saturated color-field layer creates smooth organic transitions without raster assets, hard bands, or a horizontal directional bias.
- Typography and icons: tile labels, file/folder icons, adaptive text behavior, and the contextual up action remain unchanged and sit above the color layer.
- Responsiveness: desktop, tablet, and mobile captures show no overlap, clipping, or unusable controls. The field scales with the tile rather than the viewport.
- Accessibility and behavior: the decorative field is `aria-hidden` and pointer-events are disabled. The semantic button, keyboard focus, rich tooltip, folder drill-down, and file-detail navigation remain intact.

## Interaction and data checks

- 17 grouped color metrics remain available on the live page.
- Live audio-codec mode showed AAC and Dolby Digital Plus with distinct file colors and no alert state.
- Backend storage-map test verifies byte-weighted codec and dynamic-range distributions.
- Frontend tests verify mixed-folder radial fields, tooltip content, folder drill-down, file-detail navigation, and Jellyfin-name fallback behavior.
- Production build completed successfully.

## Comparison history

1. P2: the first implementation read as a broad horizontal ellipse and did not match the selected organic field direction.
2. P2: a nine-layer low-opacity pass removed the band but washed the categories into a muted surface.
3. Fix: moved the gradient to a dedicated blurred color-field layer, reduced field radii, placed distinct anchors at four corners and three interior positions, and retained deterministic storage weighting.
4. Post-fix evidence: the focused comparison shows separate but smoothly blended interior and corner regions with no directional stripe. No P0, P1, or P2 findings remain.

final result: passed
