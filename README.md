# Leg-o

Generate XCTest (or Swift Testing) unit tests for legacy iOS Swift / Objective-C codebases using a hybrid tree-sitter + Claude pipeline. Detects what's testable, picks high-value methods, writes the tests, registers them in your `.xcodeproj`, and validates with `xcodebuild` — including an automated compile-and-fix loop.

## Why?

iOS apps that grew over years tend to mix Swift and Objective-C, use CocoaPods, drag UIKit deep into business logic, and lack tests for the parts that matter most. Hand-writing the first batch of tests against that kind of code is the part most teams put off forever. Leg-o does the boring 80% — scans the codebase, decides what's worth testing, writes the file, adds it to Xcode, and runs the build — so you can focus on the gnarly cases that actually need human judgment.

## How it works

```
Swift / Obj-C source
        ↓  (tree-sitter, local, free)
   AST metadata
        ↓  (Claude: which classes are testable + which methods to prioritize)
   Ranked plan
        ↓  → confirm step (per-class plan + cost projection, exclude any you don't want)
        ↓  (Claude: per-class test generation)
   *Tests.swift files
        ↓  (pbxproj registration)
   Xcode target updated
        ↓  (xcodebuild build-for-testing + xcodebuild test)
   Pass / fix loop / report
```

If you already have a test file for a class, Leg-o reads the existing test method names, figures out which target methods are uncovered, and **augments** the existing file instead of overwriting. Augment mode respects the file's existing framework (XCTest or Swift Testing).

## Install

Requires Python 3.11+, macOS, and Xcode (for the validation step).

```bash
git clone git@github.com:matheusruschel/Leg-o.git
cd Leg-o
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

You'll need an Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Quickstart

Lowest-risk first run on any project:

```bash
lego estimate --path ~/path/to/swift/code --method-limit 5
```

That scans, analyzes, ranks, and prints a projected cost without generating anything. Once the projection looks reasonable:

```bash
lego generate \
  --path ~/path/to/swift/code \
  --output /tmp/lego-out \
  --module-name YourAppModule \
  --method-limit 5
```

You'll be shown the per-class plan and asked to confirm (or exclude classes by index) before any generation API call. The tests land in `/tmp/lego-out/` along with a `REPORT.md`. Validation is skipped if you don't provide Xcode details.

To run end-to-end against an actual project including auto-target-add and `xcodebuild` validation:

```bash
lego generate \
  --path ~/MyApp/MyApp \
  --output ~/MyApp/MyAppTests \
  --module-name MyApp \
  --xcworkspace ~/MyApp/MyApp.xcworkspace \
  --xcodeproj ~/MyApp/MyApp.xcodeproj \
  --scheme MyApp \
  --test-target MyAppTests \
  --method-limit 5
```

For CocoaPods projects, always pass `--xcworkspace` — `xcodebuild -project` alone can't resolve pod symbols.

## Commands

| Command | What it does |
| --- | --- |
| `lego scan` | Tree-sitter scan only. Prints AST metadata as JSON. Free, no API calls. |
| `lego analyze` | Scan + Claude testability assessment + method prioritization. Prints a structured report. |
| `lego estimate` | Like `analyze` but also projects the cost of the generation step. No tests written. |
| `lego generate` | Full pipeline: scan → analyze → confirm → generate → (optional validate) → REPORT.md. |

`lego <command> --help` for the full flag list on any command.

## What it skips, and why

Before paying Claude tokens, Leg-o pre-filters classes that are unlikely to produce useful tests:

- **CocoaPods dependencies** — classes importing pod-provided frameworks (Alamofire, RxSwift, Firebase, etc.). Can't reasonably mock them.
- **Views & view controllers** — UIView/UIViewController subclasses, SwiftUI Views, anything ending in `View` / `ViewController` / `Cell` / `Coordinator`. ViewModels and ViewStates *are* tested.
- **Pure data holders** — Codable structs and enums whose methods are all trivial.
- **Foundation/UIKit/SwiftUI extensions** — single-method `extension UIFont { ... }`, `extension NSString { ... }`, etc.
- **Builder / DSL classes** — chainable setter classes like `Foo.font(...).kern(...).color(...)` that just return `self`.
- **Private methods** — `private` / `fileprivate` are sealed under `@testable import`.
- **Empty methods** — `func foo() {}`.
- **Trivial wrappers** — one-line `delegate?.foo()` / `return x.y` passthrough methods.
- **Already-covered methods** — if `FooTests.swift` exists in your test target dir and the planned method has a matching `test_methodName_…` or `@Test methodName_…` in there, Leg-o knows.

Each opt-out is a CLI flag: `--include-views`, `--include-data-holders`, `--include-system-extensions`, `--include-builders`, `--include-trivial-wrappers`, `--no-skip-pods`, `--regenerate-existing`.

## Cost

Costs depend on project size and the model you choose. On a 120-file iOS app generating tests for 5 classes:

| Phase | Cost |
| --- | --- |
| Scan | $0 (tree-sitter, local) |
| Analyze + prioritize | ~$0.40 |
| Generate (per class) | ~$0.10 |
| Compile-fix loop (per retry, if triggered) | ~$0.13 |

So a typical 5-class run with no retries lands around **$1**. Use `lego estimate` to project the cost on your project before committing tokens. The `--method-limit N` flag bounds total spend by capping how many methods get tests.

## Frameworks

Both **XCTest** and **Swift Testing** (Xcode 16's `@Suite` / `@Test`) are fully supported — generation, augment-mode coverage detection, and validation. The framework for new test files is auto-detected from the majority in your test target dir, or you can force it with `--framework xctest|swift_testing`. Augment mode always matches the existing file's framework so you don't get mixed-style test files.

## Repository layout

```
src/lego/
  scanner/      tree-sitter Swift + Obj-C parsers → ClassMetadata
  analyzer/     Claude testability + prioritization
  generator/    context bundling + test code generation (XCTest + Swift Testing templates)
  validator/    xcodebuild wrappers + error parsing + compile-fix loop
  llm/          Anthropic SDK client with retry + JSON repair + token tracking
  filters.py    pre-filter heuristics (UI, data, builders, etc.)
  pod_detector.py CocoaPods import handling
  xcode_project.py pbxproj target registration
  orchestrator.py glue: scan → analyze → plan → confirm → generate → validate → report
  reporter.py   markdown REPORT.md
  cli.py        click-based CLI
tests/          ~120 unit + integration tests
.claude/skills/ios-test-gen-pure/   pure-Claude comparison skill (no Python)
```

## Project status

Feature-complete CLI. Battle-tested against a real CocoaPods-heavy iOS app. One known bug worth being aware of: when the iOS Simulator destination isn't installed locally, `xcodebuild` errors get misclassified as compile failures and trigger pointless fix-loop retries. Workaround: pass `--destination 'platform=iOS Simulator,name=<your-installed-device>'`, or skip validation by omitting the Xcode flags.

The original development plan lives in [ios-test-gen-final-plan.md](./ios-test-gen-final-plan.md) for architectural context.

## License

MIT. See `pyproject.toml`.
