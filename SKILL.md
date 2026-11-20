---
name: wish-architectural-cad
description: Generate architectural floor plans, elevations, sections and, on Windows with the desktop apps, a SketchUp model and editable Photoshop board from one building specification. Use for spec-driven architectural revisions and coordinated SU, CAD and PS output.
---

# Wish Architectural CAD

- Verify the command is available with `wish-cad --help`. See the [README](README.md) for installation and CJK font setup.
- Start from [the example specification](examples/reading-room.json). Draw geometry at 1:1 in millimetres. Exterior walls are `EXT.0`, `EXT.1`, … in outline order. An opening's `at` is measured along its host wall from its starting vertex.
- Record meaningful dimensional assumptions and check the spec with `wish-cad check <spec.json>`. Keep revisions in the spec so the sheets stay in sync.
- Run `wish-cad plan <spec.json> --output <new-directory>`. Read `report.json`, then inspect whole-sheet PNGs and crowded annotation areas. The report surfaces missing formats and DXF structure issues. Review actual design dimensions, font appearance, and drawing completeness separately.
- Provide the DXF for editing and the PDF for printing. DXF geometry is in model space. The printed frame is rendered into PDF and PNG.

## Windows desktop suite

- For a linked SketchUp model, CAD drawings and presentation board, use the [Windows setup](docs/windows-suite.md). Verify that the correct bridge responds to the status command.
- Run the build with a new output directory. Include the Photoshop flag when a layered PSD is requested and the AutoCAD flag when native DXF readback is requested.
- Stop if the active SketchUp model has unsaved changes. Read the root report and check the model, drawings and board before reporting delivery.
