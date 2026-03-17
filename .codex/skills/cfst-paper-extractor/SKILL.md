---
name: cfst-paper-extractor
description: Extract specimen-level data from processed CFST paper PDFs into schema-v2 JSON, validate ordinary-CFST inclusion, provenance, and physical plausibility, orchestrate isolated one-paper workers, and publish canonical JSON outputs. Use when Codex needs to work from `processed/` PDF files, repair or review CFST JSON outputs.
---

# CFST Paper Extractor

Use only bundled files in this skill. Do not depend on external metadata manifests.
The `processed/` directory contains PDF files matching `[Ax-yy]*.pdf`. Workers read these PDFs via the `pdf_text` (text-layer search), `pdf_pages` (image rendering), and `view_image` (page inspection) tools.

## Use This Workflow

1. Prepare a batch workspace from processed PDF files.

```bash
python .codex/skills/cfst-paper-extractor/scripts/prepare_batch.py \
  --processed-root processed
```

This creates `manifests/`, `tmp/`, `output/`, and `logs/`. It discovers PDF files in `processed/` and writes manifests for worker orchestration.

2. Follow the **Parent Playbook** below for worker orchestration (worktree creation, worker briefs, validation, retries), publication, and optional checkpoints.

## Parent Playbook

Use this sequence as the canonical parent-agent orchestration path. If you follow these steps, the parent agent does not need to inspect bundled script internals.

### Parent-Owned Artifacts

- `output/manifests/batch_manifest.json`: batch summary, paper titles, and PDF inspection results.
- `output/manifests/worker_jobs.json`: source of truth for `paper_id`, `paper_pdf_relpath`, worker temp JSON path, and final output path.
- `output/manifests/batch_state.json`: parent-owned per-paper state tracker; parent updates it during worker launch, worker completion, retry/failure handling, and publication.
- `output/tmp/<paper_id>/<paper_id>.json`: sandbox-visible temp JSON path.
- `worker_jobs.json[*].worker_output_json_path`: on-disk host-backed temp JSON path; workers must write here so the sandbox bind mount can see the same file at `output/tmp/<paper_id>/<paper_id>.json`.
- `output/output/<paper_id>.json`: canonical published artifact; parent writes here only through `publish_validated_output.py`.

### Batch State Status Values

- `prepared`: initial state written by `prepare_batch.py` for a paper that is ready to spawn.
- `running`: the parent has launched the one-paper worker and is waiting for a terminal result.
- `ready_for_publication`: the worker reported success and the parent confirmed the temp JSON exists.
- `failed`: the latest worker attempt failed before publication. `last_error` should contain the exact failure string. Increment `retry_count` when recording this state.
- `published`: the canonical JSON was published successfully to `output/output/<paper_id>.json`.
- `publish_failed`: publication failed even though the parent reached the publish step. `last_error` should contain the publication failure string.

`validated` means the current temp or published JSON passed validator checks. `published` means the canonical output file exists in the final output directory.

### Direct-Use Sequence

1. Ensure the repository has a git `HEAD`. If it does not, initialize it once:

```bash
python .codex/skills/cfst-paper-extractor/scripts/bootstrap_git_repo.py \
  --repo-root . \
  --initial-empty-commit
```

2. Prepare the batch workspace:

```bash
python .codex/skills/cfst-paper-extractor/scripts/prepare_batch.py \
  --processed-root processed
```

3. Read `output/manifests/worker_jobs.json`. Process only items whose `status` is `prepared`. Do not reconstruct paper paths from `paper_id`; use `paper_pdf_relpath` from this file exactly as written.
Use `worker_output_json_path` from this file as the on-disk host write target for the worker's temp JSON.

4. For each prepared job, create one isolated worker worktree:

```bash
python .codex/skills/cfst-paper-extractor/scripts/git_worktree_isolation.py create \
  --paper-dir '<paper_pdf_relpath>' \
  --output-dir output/tmp/<paper_id>
```

This returns a JSON object. The created worker worktree is pruned to the owned paper payload, the owned skill payload, and git metadata so unrelated tracked repository content does not appear in the worker directory. Record at least:

- `worktree_path`
- `branch`
- `paper_rel`
- `output_dir`
- `output_host_path`

5. Spawn exactly one worker sub-agent for that paper. Pass only one paper per worker and include this ownership tuple:

When launching the worker sub-agent, set the sub-agent model and reasoning explicitly and do not vary them:

- `model=gpt-5.4`
- `reasoning_effort=xhigh`

- `paper_id=<paper_id>`
- `worktree_path=<worktree_path>`
- `paper_pdf_relpath=<paper_pdf_relpath>`
- `paper_pdf_path=<absolute_host_path_to_pdf>`
- `output_dir=output/tmp/<paper_id>`
- `output_host_path=<output_host_path>`
- `temp_json_workspace_path=output/tmp/<paper_id>/<paper_id>.json`
- `temp_json_host_path=<worker_output_json_path_from_worker_jobs.json>`

Immediately after launch, update the parent-owned state tracker:

```bash
python .codex/skills/cfst-paper-extractor/scripts/update_batch_state.py \
  --batch-state output/manifests/batch_state.json \
  --paper-id <paper_id> \
  --status running \
  --validated false \
  --published false \
  --clear-last-error
```

Parent agents should not need to inspect `update_batch_state.py` source. Use only these arguments in the parent flow:

- `--batch-state`: path to `output/manifests/batch_state.json`
- `--paper-id`: owned paper id
- `--status`: one of `running`, `ready_for_publication`, `failed`, `published`, or `publish_failed`
- `--validated true|false`: whether validator-backed output currently exists for that paper
- `--published true|false`: whether canonical output currently exists in `output/output/`
- `--last-error '<exact failure>'`: record the exact failure string when the paper is in a failed state
- `--clear-last-error`: clear `last_error` on a clean transition such as launch, ready-for-publication, or published
- `--increment-retry-count`: use when recording a failed worker attempt

6. Use this worker brief template verbatim except for placeholder substitution:

```text
Own exactly one CFST paper.

Inputs:
- paper_id: <paper_id>
- worktree_path: <worktree_path>
- paper_pdf_relpath: <paper_pdf_relpath>        (relative path — used in sandbox commands: --paper-dir-relpath)
- paper_pdf_path: <absolute_host_path_to_pdf>   (absolute path — used in pdf_info / pdf_text / pdf_pages MCP tool calls)
- output_dir: output/tmp/<paper_id>
- output_host_path: <output_host_path>
- temp_json_workspace_path: output/tmp/<paper_id>/<paper_id>.json
- temp_json_host_path: <worker_output_json_path_from_worker_jobs.json>

Required reading inside the worktree:
- .codex/skills/cfst-paper-extractor/references/extraction-rules.md
- .codex/skills/cfst-paper-extractor/references/single-flow.md

Authoritative sources for this task, in order:
- the owned paper PDF at <paper_pdf_path>
- .codex/skills/cfst-paper-extractor/references/extraction-rules.md
- .codex/skills/cfst-paper-extractor/references/single-flow.md
- the exact commands and paths in this brief

Do not use other files to infer schema, validation, or path behavior:
- do not read .codex/skills/cfst-paper-extractor/SKILL.md
- do not read .codex/skills/cfst-paper-extractor/scripts/*.py to infer rules or parameters
- do not inspect runs/, tmp/, output/, or other papers for schema examples
- only inspect a named helper script if the parent-provided command itself fails with a concrete runtime blocker that the sources above do not explain

Execution rules:
- Work only on this one paper.
- Do not revert unrelated changes.
- Write exactly one JSON file on disk, at `temp_json_host_path`.
- Do not create or modify a worktree-local relative `runs/...` JSON path.
- `temp_json_workspace_path` is the sandbox-visible path of that same file after `output_host_path` is bound into `output_dir`.
- Read the paper using this sequence: `pdf_info` → `pdf_text` (text-layer index for page search) → `pdf_pages(paths_only=true)` (render target pages) → `view_image` (inspect page images one at a time).
- Use the text layer from `pdf_text` only for locating target pages by keyword search. Do not extract specimen values from the text layer.
- Read values directly from the rendered PDF page images via `view_image`. The page image is the single source of truth.
- Run the validation command exactly as written below; do not rewrite paths or create a second output location.
- After writing JSON, validate inside the sandbox with:
  python .codex/skills/cfst-paper-extractor/scripts/worker_sandbox.py \
    --worktree-path <worktree_path> \
    --paper-dir-relpath <paper_pdf_relpath> \
    --output-dir output/tmp/<paper_id> \
    --host-output-dir <output_host_path> \
    --cwd-mode workspace \
    -- \
    python3 .codex/skills/cfst-paper-extractor/scripts/validate_single_output.py \
      --json-path output/tmp/<paper_id>/<paper_id>.json \
      --strict-rounding
- If validation fails, repair once before returning.
- If the validator or sandbox reports a path, mount, or sandbox startup failure, stop and return that failure to the parent; do not move the JSON to a different path and do not write a second copy elsewhere.

Return exactly:
- paper_id
- temp_json path (`temp_json_workspace_path`)
- validation pass/fail
- failure reason if any
```

7. When a worker finishes:

- If the worker reports success and the temp JSON exists, mark the paper ready for publication.
- If the worker fails with a schema, evidence, or extraction decision problem, create a fresh worktree and retry once with a focused correction prompt that includes the exact failure.
- If the worker fails with a path, mount, or sandbox startup problem, treat that as a parent-side orchestration issue. Fix the command or worktree/output binding first, then rerun on a fresh worktree; do not ask the worker to relocate outputs on its own.
- If the retry also fails, mark the paper failed and continue the batch. Do not block unrelated papers.

After a successful worker result with a confirmed temp JSON, update the state tracker:

```bash
python .codex/skills/cfst-paper-extractor/scripts/update_batch_state.py \
  --batch-state output/manifests/batch_state.json \
  --paper-id <paper_id> \
  --status ready_for_publication \
  --validated true \
  --published false \
  --clear-last-error
```

After a failed worker result, update the state tracker before any retry or final failure handling:

```bash
python .codex/skills/cfst-paper-extractor/scripts/update_batch_state.py \
  --batch-state output/manifests/batch_state.json \
  --paper-id <paper_id> \
  --status failed \
  --validated false \
  --published false \
  --increment-retry-count \
  --last-error '<exact failure>'
```

While a worker remains in normal `running` state and has not reported a concrete failure, interrupt, or terminal result, do not interrupt, replace, or redirect it. One-paper extraction can legitimately exceed a short wait timeout. Use generous waits and only intervene on terminal status or concrete blockers.

8. After the worker exits, confirm the temp JSON exists at `worker_output_json_path` in the parent workspace. This same file must also be visible inside the sandbox at `output/tmp/<paper_id>/<paper_id>.json` because `--host-output-dir` binds the parent directory into `output_dir`.

9. Always clean up each finished worker worktree, whether the paper succeeded or failed:

```bash
python .codex/skills/cfst-paper-extractor/scripts/git_worktree_isolation.py remove \
  --worktree-path '<worktree_path>' \
  --delete-branch
```

10. After all prepared papers finish, publish all validated temp outputs:

```bash
python .codex/skills/cfst-paper-extractor/scripts/publish_validated_output.py \
  --batch-manifest output/manifests/batch_manifest.json \
  --batch-state output/manifests/batch_state.json \
  --tmp-root output/tmp \
  --output-dir output/output \
  --publish-log output/logs/publish_log.jsonl \
  --strict-rounding
```

For a focused republish after a successful single-paper rerun, you may additionally pass `--paper-ids <paper_id>`.

11. If repository policy requires checkpoints, run them only after publication:

```bash
python .codex/skills/cfst-paper-extractor/scripts/checkpoint_output_commits.py \
  --processed-count <published_plus_failed_count> \
  --output-dir output/output
```

12. The parent agent's final report should distinguish:

- papers skipped before spawn because `worker_jobs.json.status != prepared`
- papers that failed after retry
- papers successfully published to `output/`

## Respect These Contracts

### Batch orchestration

- Use a parent-child model for every multi-paper extraction.
- Regardless of paper count, extraction work must always be executed by a spawned worker sub-agent; even a single-paper extraction must not be performed directly by the parent agent.
- Spawn one worker sub-agent per prepared paper PDF from `processed/`.
- Spawn every worker sub-agent with `model=gpt-5.4` and `reasoning_effort=xhigh`.
- Cap concurrency at 5 active paper workers.
- Declare worker ownership at launch: one paper PDF, one worker-local temp JSON path, and one worker worktree path.
- Read `worker_jobs.json` after batch preparation and treat it as the only source of truth for `paper_pdf_relpath`, temp JSON path, and per-paper readiness.
- Treat the repository as concurrently modified; workers must ignore unrelated changes and must not revert anything outside their ownership.
- Keep the parent focused on orchestration, validation review, retries, and publication after workers launch.
- If a worker is still running normally, do not interrupt it just because a local wait call timed out. Extraction may take longer than the nominal wait window.
- Retry a failed paper once with a focused correction prompt. If it still fails, return the failure reason and temp JSON path.

### Worker execution

- Process exactly one prepared paper PDF.
- Worker-authoritative sources are the owned paper PDF, `references/extraction-rules.md`, `references/single-flow.md`, and the parent-supplied worker brief.
- Do not inspect `SKILL.md`, `scripts/`, `runs/`, prior outputs, or other papers to infer schema, validation, or path behavior. Only inspect a named helper script when a concrete runtime blocker remains unresolved after following the documented command.
- When both `temp_json_workspace_path` and `temp_json_host_path` are provided, write the JSON on disk to `temp_json_host_path` and validate that same file through `temp_json_workspace_path` inside the sandbox bind mount.
- Read the paper using `pdf_info` → `pdf_text` → `pdf_pages(paths_only=true)` → `view_image`. Use text layer for page navigation only.
- Read values directly from the rendered PDF page images via `view_image`. The PDF page image is the single source of truth for all specimen values.
- Resolve `fc_basis` by following `references/extraction-rules.md` `## 8. Concrete-Strength Basis Rules`. Before interpreting symbols such as `fck`, `fc`, `f'c`, or `Fc`, first search nearby material/property text, table headers, and footnotes for code-defined grade notation such as Chinese `C30`, `C40`, `C50`, `C60` or Eurocode `C60/75`. In Chinese GB/T context, those `Cxx` grades sit above nearby bare `fck` / `fc` symbols in the priority order; when the reported measured value clearly matches the cube-strength system, keep `fc_type` consistent with that stored value instead of mirroring sloppy symbol usage. Do not assign `fc_basis` without consulting those rules.
- Keep `fc_type` in validator-compatible form only: `cube`, `cylinder`, `prism`, `unknown`, or sized forms such as `Cube 150` or `Cylinder 100x200`. Never store symbolic notation like `fck/fcu/f'c/fc` or explanatory phrases in `fc_type`.
- Inside the worker sandbox, use `scripts/safe_calc.py` for conversions, rounding, and derived values; do not do ad hoc arithmetic.
- Preserve eccentricity signs exactly as source evidence shows them.
- Do not exclude ordinary CFST specimens from the dataset based on the sign pattern of `e1` and `e2` alone.
- Preserve recycled aggregate replacement ratio `R%` in `r_ratio`.

### Output shape

- Produce the schema-v2.1 top-level keys `schema_version`, `paper_id`, `is_valid`, `is_ordinary_cfst`, `reason`, `ordinary_filter`, `ref_info`, `paper_level`, `Group_A`, `Group_B`, and `Group_C`.
- Treat `is_valid=false` as an unusable paper with empty specimen groups.
- Treat `is_valid=true` as usable; extract all specimens regardless of ordinary status.
- Tag each specimen with `is_ordinary` and `ordinary_exclusion_reasons` using the two-tier evaluation in `references/extraction-rules.md` section 2.
- Derive `is_ordinary_cfst` from specimen flags: `true` when at least one specimen has `is_ordinary=true`.
- Keep worker output in `tmp/<paper_id>/<paper_id>.json` only.
- Let the parent publish the final JSON into `output/<paper_id>.json`; workers must never write final outputs directly.
- Treat published JSON as canonical. Any project-specific tabular conversion should happen outside this skill.

### Git and sandbox isolation

- Require a git repository with `HEAD` before creating worktrees.
- Initialize one when needed:

```bash
python .codex/skills/cfst-paper-extractor/scripts/bootstrap_git_repo.py \
  --repo-root . \
  --initial-empty-commit
```

- Create every worker environment with `scripts/git_worktree_isolation.py create`.
- Launch every worker only through `scripts/worker_sandbox.py`.
- The paper PDF is mounted read-only inside the sandbox; only the declared worker-local temp output directory is writable. For parent-managed batches, bind that writable directory from the parent workspace with `--host-output-dir` so temp JSON survives worktree deletion.
- `scripts/safe_calc.py` and `scripts/validate_single_output.py` are sandbox-only helpers in this variant; they fail fast if `CFST_SANDBOX=1` is missing.
- Require `bubblewrap` or `bwrap`.
- Treat sandbox startup failure as fatal; do not fall back to unsandboxed execution.
- Remove finished worktrees with `scripts/git_worktree_isolation.py remove`.

## Use These Bundled Scripts

- `scripts/prepare_batch.py`: preferred entry point; discover processed PDF files, verify readability, and write manifests/state for worker orchestration.
- `scripts/validate_single_output.py`: sandbox-only validator for one worker-local schema-v2 JSON; checks shape, provenance, plausibility, ordinary-filter consistency, and rounding.
- `scripts/publish_validated_output.py`: revalidate worker outputs, publish final JSON, append a publish log, optionally publish only selected `--paper-ids`, and update `batch_state.json` when `--batch-state` is provided.
- `scripts/update_batch_state.py`: update one paper entry in `batch_state.json` from the parent orchestration flow.
- `scripts/git_worktree_isolation.py`: create and remove per-paper git worktrees. In the parent flow, `create` also returns `output_host_path`, the persistent host directory that should be bound into `worker_sandbox.py`.
- `scripts/worker_sandbox.py`: mandatory worker launcher; it mounts paper inputs read-only and the worker-local output directory read-write. Use `--host-output-dir` in parent-managed batches so worker temp outputs persist outside the worktree; never bypass it.
- `scripts/bootstrap_git_repo.py`: initialize a repo and optional empty commit so worktree execution can start.
- `scripts/checkpoint_output_commits.py`: commit or push published outputs at fixed intervals when the repository policy calls for output-only checkpoints.
- `scripts/safe_calc.py`: sandbox-only arithmetic helper for deterministic conversions and derived geometry values instead of handwritten calculations.

## Read These References

- `references/extraction-rules.md`: use for schema details, group mapping, required fields, evidence format, loading-mode decisions, numeric rules, and invalid-output handling.
- `references/single-flow.md`: use for one-paper worker sequencing, required input layout, setup-figure rules, and validation expectations.
