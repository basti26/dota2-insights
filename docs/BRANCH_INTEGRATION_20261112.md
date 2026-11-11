# Public branch integration — 2026-11-12

The published and local lineages had different root commits. Ordinary merge commits retain both ancestries; no existing commits were rewritten and no force push is required.

- `main` adopts the validated `codex/map-ward-updates` file tree, then retains the former published main ancestry.
- `codex/insights-portable` retains the existing local cleanup and license changes, then retains its former published ancestry.
- The map branch is unchanged; published main was not merged back into it.
- `data/validation-locks.json` remains an empty required public policy template. Real hold IDs belong only in ignored `config/validation-locks.local.json`; local and public policies remain additive and malformed policy files fail closed.
- Existing historical commits can still contain formerly public identifiers. This integration does not claim to remove historical exposure or change remote caches or other clones.

There is no verified list identifying “17 extra files.” The portable tracked tree contains no paths absent from the map branch. The comparison against the corresponding older portable snapshot shows policy, documentation and synthetic fixture substitutions, not evidence that those files are disposable. No source, tests, local evidence or historical recordings were deleted on that basis.

A fresh-worktree check exposed legacy Windows stdout encoding in `--self-check` when an optional parser dependency is missing. Its machine-readable JSON now uses ASCII escapes, preserving the decoded values. Regression checks enforce empty public/example hold templates and the MIT license.

Validation: main has 33 and portable has 32 relevant offline tests passing, including local/public lock union, fail-closed policies, replay entry guards, packaging boundaries, isolated CLI entry and the encoding regression. The main integration initially retained exactly the already-tested map tree (1068 complete offline tests previously passed); only the documented self-check encoding fix and boundary tests were then added. This is not a new gameplay, replay parsing, or portable EXE acceptance claim.

The original working directory's private lock bytes were unchanged and its configured holds were confirmed refused without opening sealed replay payloads. A verified pre-integration Git bundle and machine-readable checks are retained in the ignored local diagnostics directory. Normal Git history and clones remain usable.
