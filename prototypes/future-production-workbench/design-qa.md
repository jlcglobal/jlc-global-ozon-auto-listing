# Design QA — AI Factory 商品制作工作台预览

## Comparison target

- source visual truth path: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/source-frame-01.png`
- secondary visual truth path: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/source-frame-09.png`
- implementation screenshot path: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/desktop-default.png`
- viewport: `1440 × 1080`, device scale factor `1`, light theme
- state: `P000001`, all six production stages completed, first generated SKU image selected, `资料` inspector active, manual review mode
- local URL: `http://127.0.0.1:4175/`

## Evidence opened and compared

- full-view comparison: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/comparison-full.png`
- focused comparison: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/comparison-focus.png`
- responsive screenshots:
  - `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/desktop-1024.png`
  - `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/mobile-390.png`

The source and implementation are not the same product. The comparison therefore checks the selected visual language—one dominant object, pale rounded shell, violet/blue AI accent, strong dark command surface, contextual tools, and controlled spatial depth—while preserving AI Factory's real information architecture and real product assets.

## Findings

- No actionable P0, P1, or P2 findings remain.
- Typography: system UI font stack renders consistently; the hierarchy from workbench title to stage labels and metadata is clear. Small operational metadata was raised from 8–9 px to 9–10 px during iteration. Long Chinese and Russian titles wrap without overlap or clipping.
- Spacing and layout: the full comparison preserves the reference's dominant central subject and large rounded outer shell. The implementation intentionally adds a left production flow and right inspector because both are required by the real Factory workflow. Region gaps, panel radii, command dock, and image canvas remain consistent at 1440 px.
- Colors and tokens: off-white canvas, ice gray outer field, restrained violet AI rail, pale violet context surface, green success state, and charcoal command dock match the reference direction. No CSS gradients are used.
- Image quality and asset fidelity: every visible product image comes from `P000001` source or generated image files. Crops use `object-fit: contain/cover` according to the slot, remain sharp, and do not replace the product with fake 3D or CSS artwork. Phosphor icons provide one consistent icon family.
- Copy and content: visible product ID, SKU IDs, Chinese and Russian titles, prices, category, store, task ID, product ID, and uploaded status come from the current local P000001 snapshot. Prototype boundaries explicitly say that it does not connect to Factory or Ozon.
- Interactions and states: navigation feedback, production-stage selection, image switching, lightbox, global search, capability search, manual/automatic preview toggle, inspector tabs, capability drawer, publication-result modal, export/download menus, Escape dismissal, and guarded delete state were tested.
- Responsiveness: 1440 × 1080, 1024 × 768, and 390 × 844 have no page-level horizontal overflow. Primary product canvas and `查看上架结果` remain visible. The mobile state intentionally hides the dense left flow and right inspector to protect the core image-review task.
- Accessibility: semantic buttons/tabs/dialog labels, keyboard Escape and Command/Ctrl+K, focus-visible outlines, alt text for meaningful product imagery, and reduced-motion handling are present. Contrast remains legible across white, violet, green, and dark command surfaces.

## Comparison history

### Iteration 1

- [P2] Metadata text in inspector notes, SKU chips, and the bottom status dock rendered too small relative to the reference and could reduce scanability on a 1440 px display.
- Fix: raised the key metadata sizes from 8–9 px to 9–10 px without changing panel proportions; allowed the inspector to scroll naturally for long content.

### Iteration 2

- Post-fix evidence: `comparison-full.png`, `comparison-focus.png`, and the refreshed responsive screenshots.
- Result: text remains within its containers, long titles wrap correctly, the product stays dominant, and no new P0/P1/P2 mismatch is visible.

## Browser verification

- browser-rendered implementation screenshot: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/desktop-default.png`
- test record: `/Users/apple/Documents/crossborder-ai-factory/prototypes/future-production-workbench/qa-artifacts/validation-results.json`
- primary interactions tested: 24 checks, all passed
- console errors checked: `0`
- page errors checked: `0`
- production build: passed

## Follow-up polish

- [P3] If this visual direction is selected for implementation, define a dedicated Chinese/Latin type scale and compact density token before adapting additional Factory pages.
- [P3] The mobile inspector can become a bottom sheet in a later implementation pass; this preview keeps the first screen focused on image review.

final result: passed
