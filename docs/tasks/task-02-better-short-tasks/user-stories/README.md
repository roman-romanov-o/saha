# User Stories

User stories define features from the user's perspective.

## Contents

| ID | Title | Priority | Status |
|----|-------|----------|--------|
| US-001 | One-command lightweight planning for a small task | Must Have | Draft |
| US-002 | Codebase-grounded quick plan | Must Have | Draft |
| US-003 | Quick-planned task runs through the full verify loop and finishes | Must Have | Draft |
| US-004 | One clear small-task path (remove the broken minimal mode) | Must Have | Draft |

## Story Map

### Developer using saha — the lightweight on-ramp
- US-001: Describe a small task in one command → minimum artifacts in one pass
- US-002: That pass is grounded by a light, targeted codebase scan
- US-003: Run `/saha:execute` on the result → full verify loop, clean termination

### Developer using saha — a coherent tool
- US-004: Remove `--mode=minimal` and its contradictions; one clear small-task path

## Dependency Order

```
US-002 (grounding) ─┐
                    ├─→ US-001 (quick command) ─→ US-003 (execute + verify)
US-001 ─────────────┴─→ US-004 (remove minimal mode, once quick exists)
```
