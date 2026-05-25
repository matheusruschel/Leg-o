# Leg-o

iOS Legacy Test Generator — a hybrid pipeline that combines tree-sitter and Claude to analyze Swift/Objective-C codebases and generate XCTest unit tests for legacy code.

## Overview

Leg-o uses a three-layer architecture:

1. **tree-sitter scan** — fast, deterministic AST extraction across the codebase.
2. **Claude triage** — classifies classes as testable / partial / needs-refactor using structural summaries.
3. **Claude test generation** — generates full XCTest files for prioritized targets, validated via `xcodebuild` with an automated fix-and-retry loop.

See [ios-test-gen-final-plan.md](./ios-test-gen-final-plan.md) for the full development plan.

## Status

Early planning. Implementation has not started yet.
