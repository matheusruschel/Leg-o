# Leg-o (lego)

iOS legacy-codebase test generator. Pipeline: **tree-sitter scan → Claude triage → Claude test gen → xcodebuild fix loop**. Produces XCTest *or* Swift Testing files for Swift/Obj-C classes and (optionally) wires them into the project's `*.xcodeproj`.

The full development plan is in `ios-test-gen-final-plan.md` (read it for architectural intent).

## Development

```bash
source .venv/bin/activate            # Python 3.13 venv with deps already installed
python -m pytest                     # ~120 tests, runs in < 1s, must stay green
lego --help                           # console entry point
```

Source lives in `src/lego/`, tests in `tests/`. Python package name is `lego` (CLI: `lego`). Test fixtures (Swift sources + expected AST JSON + xcodebuild output samples) under `tests/fixtures/`.

## CLI surface

```
lego scan      # Layer 1: tree-sitter scan, prints AST JSON
lego analyze   # Layers 1+2: scan + Claude testability assessment + method ranking
lego estimate  # scan + analyze + projected generation cost (no generation)
lego generate  # full pipeline: scan → analyze → confirm → generate → optional validate → REPORT.md
```

`lego generate` is the user-facing entry point. It prints a per-class plan with cost projections and asks for confirmation (or excluded indices) before any generation API call. `--yes` bypasses for CI.

## Architecture (three layers + orchestrator)

- **Layer 1 — Scanner** (`src/lego/scanner/`) — tree-sitter parses Swift + Obj-C into `ClassMetadata` records (classes, methods, properties, deps, access levels, body text). Free (local).
- **Layer 2 — Analyzer** (`src/lego/analyzer/`) — Claude testability assessment in batches of ~50 classes, then per-method prioritization. One structured-JSON API call per batch.
- **Layer 3 — Generator** (`src/lego/generator/`) — per-class context bundle (target file + related files, 100K char budget) → Claude test code. Augment mode if an existing `*Tests.swift` exists; otherwise new file. XCTest *or* Swift Testing template based on project majority.
- **Validator** (`src/lego/validator/`) — `xcodebuild build-for-testing` + `xcodebuild test`, parse errors, hand to Claude with a fix prompt, retry (default 3).
- **Orchestrator** (`src/lego/orchestrator.py`) — wires Layers 1–3 + Validator behind `run_pipeline(config, claude_client)`. Builds `GenerationPlan`, invokes `confirm_callback`, applies pre-filters, threads framework choice.
- **Reporter** (`src/lego/reporter.py`) — markdown REPORT.md per generation run.

## Filter rules (load-bearing — preserved across iterations)

We added these one by one while testing against the WSL iOS project. Each catches a real failure mode that surfaced in real runs.

| Filter | Catches | Configurable via |
| --- | --- | --- |
| Pod skip | Classes importing CocoaPods modules (parsed from `Podfile.lock`) | `--no-skip-pods`, `--skip-module NAME` |
| UI types | UIView/UIViewController/Cell subclasses + name suffixes `*View`/`*ViewController`/`*Cell`/`*Coordinator` with denylist for `*ViewModel`/`*ViewState`/`*ViewData`/`*ViewProvider`/`*ViewBuilder`/`*ViewFactory`/`*ViewRouter`/`*ViewStore`/`*ViewEvent`/`*ViewAction` | `--include-views` |
| Data holders | Struct/enum where every method is trivial (≤2 lines OR no control flow) | `--include-data-holders` |
| System extensions | Extensions whose `extends` ∈ Foundation/UIKit/SwiftUI/AppKit type set | `--include-system-extensions` |
| Builder/DSL classes | ≥75% of methods return Self with no branching (if/for/while/guard/switch/try; `return` excluded) | `--include-builders` |
| Trivial wrappers (per-method) | Single-line bodies that are `delegate?.foo()` / `return x.y` / `return Type(...)` | `--include-trivial-wrappers` |
| Empty methods (per-method) | Body is `{}` or whitespace | (always on) |
| Private methods (per-method) | `access_level` ∈ {`private`, `fileprivate`} — `@testable import` doesn't reach them | (always on) |
| Already-tested (whole-class) | Walks `--test-target-dir`, parses XCTest AND Swift Testing (`@Test`) method names, prefix-matches against candidate methods (longest match wins, word-boundary aware). All covered → skip. Some covered → augment. | `--regenerate-existing` overrides |

**When tuning filters:** start by running `lego generate --dry-run` (or invoking just `--method-limit 5 --yes`) and inspect REPORT.md's "skipped" list. Each entry has the reason. False positives → loosen via the `--include-*` flags; false negatives → tighten the heuristic in `src/lego/filters.py` and add a test in `tests/test_filters.py`.

## Test framework: XCTest vs Swift Testing

Both supported end-to-end. The framework for **new** files is auto-detected from the majority in `--test-target-dir` (count `import Testing` vs `import XCTest` across .swift files; XCTest on ties). **Augment mode** always mirrors the existing file's framework. CLI override: `--framework xctest|swift_testing|auto`.

Swift Testing method extraction handles `@Test func methodName_scenario_result()` (no `test` prefix required), via `_SWIFT_TESTING_METHOD_RE` in `src/lego/filters.py`.

## CocoaPods projects (workspaces vs projects)

If the iOS repo has both `Foo.xcodeproj` and `Foo.xcworkspace`, the workspace is required for validation — `xcodebuild -project` won't resolve pod symbols. Lego auto-detects: pass `--xcodeproj` and a sibling `.xcworkspace` is preferred automatically. Pass `--xcworkspace` explicitly to be sure.

## Auto-add to test target

`--test-target NAME` edits `project.pbxproj` (via the `pbxproj` PyPI package) to register the generated files in the named target. Required for xcodebuild to find them. **This writes to the user's repo** — check `git status` after.

## Simulator destination

Default is `platform=iOS Simulator,name=iPhone 16`. If iPhone 16 isn't installed locally, `autodetect_destination` falls back to iPhone 15 → 14 → any iPhone via `xcrun simctl list devices available --json`. Override with `--destination 'platform=iOS Simulator,name=...'`.

## Known issue: fix-loop misfires on environment errors

When xcodebuild fails because the simulator destination is wrong (not installed locally), the "error" output is xcodebuild's available-destinations list — not a compile error. Lego's error parser doesn't distinguish environment errors from code errors and triggers the fix loop. With `--max-retries 3` (default) this can burn 80% of a run's tokens on rewrites that can't help.

**Workarounds** until fixed:
- Pass `--destination` explicitly to match an installed simulator.
- Omit `--xcworkspace`/`--xcodeproj` to skip validation entirely.
- Set `--max-retries 1`.

**Fix path** (planned): add `environment_error` to `ErrorCategory` in `src/lego/models.py`, detect "Available destinations" / "Unable to find a destination" / "Scheme not currently configured" in `src/lego/validator/error_parser.py`, short-circuit `validate_and_fix` when the first iteration's category is `environment_error`. See `~/.claude/projects/-Users-matheusruschel-Repos-Leg-o/memory/lego-fix-loop-misfire-bug.md` for the full bug write-up.

## Cost expectations (real numbers, not estimates)

Measured on the WSL iOS project (~123 Swift files, 47 pods, 4–5 classes generated):

| Phase | Cost |
| --- | --- |
| Scan | $0 (tree-sitter) |
| Analyze + prioritize | ~$0.39 (with full pre-filters) |
| Generate (one Claude call per class) | ~$0.10/class |
| Fix loop (per retry, when triggered legitimately) | ~$0.13 |
| **Full run (5 classes, no retries needed)** | **~$0.75** |
| **Full run with fix-loop misfire (3 retries × 4 classes)** | **~$1.96** ← the bug |

For projection only, `lego estimate` runs analyze + prioritize and projects the generation cost without doing it. Useful pre-flight.

## Pure-Claude comparison skill

There's a sibling skill at `.claude/skills/ios-test-gen-pure/SKILL.md` that mirrors the lego pipeline but executes entirely through Claude Code's tools (Read, Glob, Edit, Write, Bash) — no lego CLI, no Python. **Use it only for comparison runs** — it costs roughly 2.5–3× lego per run because there's no free tree-sitter scan and no batched analyze.

Findings from the comparison are in memory: `lego-vs-skill-comparison.md`.

## Reference project for testing changes

`~/Repos/wsl-ios/WSL` — real iOS app with CocoaPods, ObjC, both XCTest and Swift Testing files, ~150 classes. Canonical command for end-to-end runs:

```bash
lego generate \
  --path ~/Repos/wsl-ios/WSL/WSL \
  --output ~/Repos/wsl-ios/WSL/WSLTests \
  --module-name WSL \
  --xcworkspace ~/Repos/wsl-ios/WSL/WSL.xcworkspace \
  --xcodeproj ~/Repos/wsl-ios/WSL/WSL.xcodeproj \
  --scheme WSL \
  --test-target WSLTests \
  --method-limit 5
```

Landmark classes that exercise specific filters: `ASPToggleView` (custom UIView base, name-suffix UI filter), `AttributedStringProxy` (builder filter), `PipRestoreReadinessHelper` (private-method filter), `LiveStreamResumeService` (Swift Testing coverage detection), `EventScheduleFooterItemViewModel` (trivial-wrapper filter).

## Commit + push

Always create new commits (never amend). Run `python -m pytest` before commit; all tests must pass. Push only when the user asks.

Co-author attribution for AI-written commits:
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```
