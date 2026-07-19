# LikeC4 Architecture Model — Spec

Status: **implemented** (see `/saha:decide`, `/saha:verify`, `/saha:research` and the
"Architecture Model" section of `docs/user-guide.md`).
Companion spec: `agentic-coding-kanban/docs/proposals/likec4-architecture-pane.md`.

Division of labor (fixed): **saha owns the model (data), the kanban app owns
rendering (pixels).** Saha ships zero visualization code — no HTML generation,
no webview, no image export in the loop.

## Design rules

1. One model per project, many views — never a model per task. Per-task models
   fork the single source of truth, which is the exact failure C4 exists to
   prevent.
2. Everything is opt-in and degrades: no `docs/architecture/`, no `likec4`
   CLI, no kanban app — planning still works identically.
3. The model is plain text (LikeC4 DSL). Agents read and edit it like any
   other artifact; there is no extra pipeline between agents and the model.

## 1. Location

```
docs/architecture/
  model.c4        # elements: systems, containers, components
  views.c4        # views over the model
```

One or more `*.c4` files; the split above is a convention, not a requirement
(LikeC4 merges all files in the directory into one model).

## 2. Who touches it

| Command | Interaction |
|---------|-------------|
| `/saha:research` | Reads the model (if present) as context; flags drift between model and code as a research finding. |
| `/saha:decide` | The only writer. A design decision that changes architecture updates `model.c4`/`views.c4` in the same pass and records the affected view ids in the DD doc. |
| `/saha:verify` | Validates the DSL (see §4). |
| Execution agents | Never write the model. If implementation deviates from it, the verification report says so; a follow-up `/saha:decide` reconciles. |

If `docs/architecture/` does not exist, `/saha:decide` offers to create it
only when a decision is genuinely architectural (new component, new boundary,
new external system) — never for local design choices.

## 3. View naming

- Evergreen views: stable ids — `index` (landscape), `context`, one per
  container.
- Task-scoped views: `task-NN-<slug>` (e.g. `task-03-quota-flow`). These show
  the slice of architecture a task changes and are referenced from that task's
  design decisions. They are kept after the task ships (they document *why*
  the architecture looks like this), pruned only when they stop rendering
  against the current model.

The `task-NN-` prefix is the contract the kanban app uses to deep-link a task
card to its architecture views.

## 4. Validation (`/saha:verify`)

- `likec4` CLI on PATH → run `likec4 validate` against `docs/architecture/`;
  a non-zero exit (DSL error, dangling reference) is a verify failure like any
  other inconsistent artifact. (Implementation note: originally specced as
  `likec4 export json --skip-layout`, but that exits 0 even on dangling
  references — verified empirically; `likec4 validate` is the command whose
  exit code carries the signal. `export json` is still used to obtain the view
  list.)
- CLI absent → skip with a one-line note. Never a failure; never block
  planning on node tooling.
- Additionally verify that every view id referenced from a DD doc exists in
  the exported view list.

## 5. Fallback rendering (no kanban app)

Saha never renders, but the model stays useful without the app:

- `likec4 export png -o docs/architecture/img` for static images in docs/PRs.
- `likec4 export mermaid` degrades views into the mermaid pipeline any
  markdown viewer already renders.

Both are manual/CI conveniences, not part of the loop.

## Out of scope

- Any rendering inside saha (`saha status` does not draw the model).
- Per-task models or copies of the model into task folders.
- Requiring `likec4` for any planning or execution step.
- User-story dependency graphs — those are structured plan data rendered
  natively by the kanban app (Grape), not architecture, and never go through
  LikeC4.
