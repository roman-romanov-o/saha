# Research

Technical research and codebase analysis.

## Contents

- [spec-kit-vs-sahaidachny.md](spec-kit-vs-sahaidachny.md) — Critical analysis: is Sahaidachny needed given GitHub spec-kit? Planning layer is commoditized; the autonomous execution-and-verification loop is the real moat; short tasks are whitespace for both tools.
- [current-lightweight-mode-analysis.md](current-lightweight-mode-analysis.md) — Why `--mode=minimal` doesn't solve "small tasks": it targets greenfield (not small scope), only drops 2 folders, has 4 contradictory definitions, and references a phantom DoD step. The execution loop hard-requires ≥1 story+ACs+phase. Recommends a single on-ramp command that auto-generates the minimum and hands off to the verify loop.
