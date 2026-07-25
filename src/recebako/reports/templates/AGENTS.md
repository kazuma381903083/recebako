# Report-specific rules

- Generated reports must be fully self-contained.
- Do not use CDNs, remote fonts, remote JavaScript, or external images.
- Generate charts as inline SVG.
- Add a test that rejects `http://` and `https://` in generated HTML.
- Preserve the visual direction of the report sample without copying hard-coded sample data.