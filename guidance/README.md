# guidance/: the single source of the agent instructions

Edit text here, then run `python scripts/gen_guidance.py`. Never edit the generated files by hand
(`guidance_gen.py`, the `kurgu:generated` regions of `SKILL.md`, `AGENTS.md`, `README*.md`, `SPEC.md`). `tests/test_guidance_sync.py` fails when they drift.

- `rules/<rule>.md`: one file per rule, one section per target variant. A section starts with a line `@@ <variant>`.
  Variants: `block` (short, English, in `AGENT_BLOCK`), `wake` (Turkish, in `WAKE_PROMPT`), `full` (SKILL.md and AGENTS.md), `tool` (MCP descriptions).
- `tools/<mcp_tool>.md`: the MCP tool description (section `description`). Schemas come from the argparse parsers, not from here.
- `commands.md`, `tools.md`: command and tool lists reused by AGENTS.md, the READMEs and SPEC.md.
- `targets/*.md`: templates. `{{file#variant}}` inserts a section (`{{rules/align#block}}`). `agent_block` and `wake` become Python strings;
  `region.<name>` templates are written between `<!-- kurgu:generated:<name> -->` and `<!-- kurgu:generated:end -->` markers in the files listed in `scripts/gen_guidance.py`.
