---
name: wish-architectural-cad
description: Generate editable DXF architectural floor plans, elevations, and sections with matching PDF and PNG sheets from a dimensioned building specification. Use for early architectural drawing sets and iterative revisions driven by a shared JSON specification.
---

# Wish Architectural CAD

- Verify the command is available with `wish-cad --help`. See the [README](README.md) for installation and CJK font setup.
- Start from [the example specification](examples/reading-room.json). Draw geometry at 1:1 in millimetres. Exterior walls are `EXT.0`, `EXT.1`, … in outline order. An opening's `at` is measured along its host wall from its starting vertex.
- Record meaningful dimensional assumptions and check the spec with `wish-cad check <spec.json>`. Keep revisions in the spec so the sheets stay in sync.
- Run `wish-cad plan <spec.json> --output <new-directory>`. Read `report.json`, then inspect whole-sheet PNGs and crowded annotation areas. The report surfaces missing formats and DXF structure issues. Review actual design dimensions, font appearance, and drawing completeness separately.
- Provide the DXF for editing and the PDF for printing. DXF geometry is in model space. The printed frame is rendered into PDF and PNG.
