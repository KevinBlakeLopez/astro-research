# AstroChart Bridge fixtures

Made by `make.mjs` with AstroChart Bridge's own format module, `extension/core/portable.js`, as last changed in commit `8fd1ddd` (2 November 2026) of [astrochart-bridge](https://github.com/KevinBlakeLopez/astrochart-bridge). The platform's reader is checked against that reference implementation, never against itself. Every chart is synthetic.

- **`chart-<date>.astrochart.json`:** five charts written as the extension writes a file, `serialize(chart, 2)` and a newline. They cover British Summer Time, a half-hour offset, a local-mean-time offset to the second (no name), a southern chart past midnight read from a screenshot, and a pasted chart with no site.
- **`conformance.json`:** inputs a reader must get right, each with what AstroChart Bridge's `parse()` made of it: the reason it refused it, or the chart it read.

To make them again, from this folder:

```bash
node make.mjs <astrochart-bridge checkout>/extension/core/portable.js
```
