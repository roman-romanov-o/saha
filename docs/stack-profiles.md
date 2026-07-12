# Stack Profiles & Per-AC Verification

Sahaidachny's execution loop is **language-agnostic**. It does not assume
Python or `pytest`. Instead it resolves a *stack profile* — how to build, test,
lint, and run the project under test — and lets each acceptance criterion (AC)
declare *how* it should be verified. This guide covers both, with a worked
Swift/AppKit example for GUI apps whose behaviour a machine cannot fully check.

## 1. How a stack profile is resolved

The profile is resolved in priority order (later steps overlay earlier ones):

1. **Auto-detection** from a marker file at the repo root.
2. **`.sahaidachny/stack.yaml`** — a full or partial override, deep-merged on
   top of the detected base (per-field: what you set wins, what you omit keeps
   the detected value).

Marker-file → detected defaults:

| Marker                         | language | build           | test (command / globs)                          | quality                       | run            |
| ------------------------------ | -------- | --------------- | ----------------------------------------------- | ----------------------------- | -------------- |
| `pyproject.toml`/`setup.py`    | python   | —               | `pytest -v` · `**/test_*.py`, `**/*_test.py`, `**/tests/**/*.py` | `ruff check`, `ty check`, `complexipy` | —              |
| `Package.swift`                | swift    | `swift build`   | `swift test` · `**/*Tests.swift`, `**/Tests/**/*.swift` | `swiftlint`                   | `swift run`    |
| `package.json`                 | node     | `npm run build` | `npm test` · `**/*.test.*`, `**/*.spec.*`       | `eslint .`, `tsc --noEmit`    | `npm start`    |
| `Cargo.toml`                   | rust     | `cargo build`   | `cargo test` · `**/tests/**/*.rs`               | `cargo clippy`, `cargo check` | `cargo run`    |
| `go.mod`                       | go       | `go build ./...`| `go test ./...` · `**/*_test.go`                | `go vet ./...`, `golangci-lint run` | `go run .`|

If no marker is present (and no `stack.yaml`), every command is empty — the loop
runs the agents but skips all tool gates.

## 2. The `stack.yaml` schema

```yaml
language: swift            # advisory metadata handed to the agents
build:
  command: "swift build"   # empty "" => skip the build gate
test:
  command: "swift test"    # empty "" => skip the test gate
  file_globs:              # which paths count as test files
    - "**/*Tests.swift"
quality:
  commands: ["swiftlint"]  # empty [] => skip the quality gate
  changed_files_only: true # lint only files the iteration touched
run:
  command: "swift run"     # used by verify:build ACs to launch the artifact
```

**An empty command means "skip that gate."** This is the key to supporting
UI-only targets: set `test.command: ""` and the loop will never try to invent a
headless test or churn iterations chasing one. See
[`examples/stack-swift.yaml`](../examples/stack-swift.yaml) for a ready-to-copy
profile tuned for an AppKit desktop app.

## 3. Per-AC verification methods

A single project mixes criteria a machine *can* assert with criteria only a
human can confirm (visual rendering, animation smoothness, "feels responsive").
Each AC therefore declares its verification method with an HTML-comment tag in
the user story:

```markdown
- [ ] **AC-1:** Parser rejects malformed config       <!-- verify: automated -->
- [ ] **AC-2:** App builds and launches without errors <!-- verify: build -->
- [ ] **AC-3:** Terminal pane renders with correct font <!-- verify: manual: launch the app, open a pane, confirm the font matches the design -->
```

| Method                | What the loop does                                                                 | When to use                                                  |
| --------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `automated` (default) | A headless test must assert it; failure produces `fix_info` and re-implementation. | Logic, parsing, state, anything a test can check.           |
| `build`               | It's enough that the project compiles / launches cleanly (no behavioural assert).  | "App builds", "links resolve", "binary starts".             |
| `manual: <how>`       | QA records it as a deferred human check; it never produces `fix_info`.             | Visual/UX criteria no headless test can confirm.            |

Pick the **weakest method that still gives real confidence** — prefer
`automated`; reserve `manual` for things a machine genuinely cannot check.

## 4. The `COMPLETED_PENDING_MANUAL` outcome

When all `automated` and `build` criteria pass but one or more `manual`
criteria remain, the loop does **not** keep iterating (which would burn
iterations chasing un-checkable ACs). Instead it terminates in a *success*
state, `completed_pending_manual`, and prints a checklist of what a human must
sign off:

```
── MANUAL VERIFICATION REQUIRED ──
Code is complete. The following acceptance criteria need human sign-off:
  [ ] Terminal pane renders with correct font
      ↳ launch the app, open a pane, confirm the font matches the design
```

Manual checks accumulate across iterations (deduplicated by criterion) in
`state.context["pending_manual_checks"]`, so nothing declared along the way is
lost when the loop finalises.

## 5. Unblocking a Swift/AppKit app — checklist

1. Copy [`examples/stack-swift.yaml`](../examples/stack-swift.yaml) to your
   Swift project's `.sahaidachny/stack.yaml`; adjust `build`/`run` for SPM vs
   Xcode, and set `test.command: ""` if the target has no headless tests.
2. Annotate every AC in the user stories with a `verify:` tag — `automated`
   for logic, `build` for "compiles/launches", `manual:` for visual/UX.
3. Run the loop. Logic ACs are tested and fixed automatically; build ACs gate on
   a clean compile/launch; visual ACs land in the `COMPLETED_PENDING_MANUAL`
   checklist for you to verify by hand.
