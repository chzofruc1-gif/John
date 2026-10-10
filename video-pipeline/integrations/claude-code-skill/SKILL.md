---
name: avp-video
description: Produce faceless bilingual explainer videos with the local avp pipeline — research, bilingual script with sourced claims, review gate, illustrations, image-to-video, voices, 16:9 + 9:16 renders, publish copy and QC. Use when the user wants a new episode, wants to revise/fact-check a script, re-render, or check progress of an avp series.
---

# avp video pipeline

The pipeline lives in the `video-pipeline/` folder (CLI `avp`, Python API `avp.api`, MCP server `avp mcp`).
Read `video-pipeline/AGENTS.md` first — it is the contract. Essentials:

1. `avp --json status <series>` to see where things are.
2. New episode: `avp --json new <series> "<brief>" --slug <slug>`, then `avp --json run <ep> --to script`.
3. Script review: read `<ep>/script.md`; edit via `avp script get/put` or `avp revise <ep> @notes.md`; validate with `avp check <ep>`.
   Never run `avp approve` without the user's explicit OK.
4. Before any production run: `avp --json plan <ep>` and show the user `paid_calls` per stage. Then
   `avp --json run <ep> --max-paid <N>` with the number they agreed to.
5. Results: `avp --json outputs <ep>` (videos, subtitles, publish copy, QC verdict).

Assets made outside the pipeline go in `<ep>/assets/<scene>/` and are kept after `avp adopt <ep>`.
`--provider mock` runs everything offline for free (placeholder art and voices) to test structure and timing.
