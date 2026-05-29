---
name: ios-test-gen-pure
description: Generate XCTest or Swift Testing unit tests for an iOS Swift / Objective-C project, end-to-end, using only Claude Code's built-in tools (Read, Glob, Grep, Edit, Write, Bash). This is the pure-agent equivalent of the `lego` Python pipeline — invoke it when the user wants tests generated for an iOS project WITHOUT relying on the lego CLI. Trigger phrases include "generate iOS tests via the skill", "run the pure-claude test generator", "ios-test-gen-pure", "use the skill to make XCTest files".
---

# ios-test-gen-pure

You are the orchestrator. Your job is to walk an iOS codebase, decide what to test, write the test files, and validate them with xcodebuild — all without invoking the `lego` Python CLI or its modules. Use only Read, Glob, Grep, Edit, Write, and Bash.

## Required inputs (ask the user if missing)

- `path` — root of the Swift/Obj-C source to scan (e.g. `~/Repos/MyApp/MyApp`)
- `output_dir` — directory to write generated `*Tests.swift` files (often the test target dir)
- `module_name` — Swift module for `@testable import` (e.g. `MyApp`)
- `xcworkspace` OR `xcodeproj` — needed for validation (Workspace REQUIRED if project uses CocoaPods)
- `scheme` — Xcode scheme name (e.g. `MyApp`)
- `test_target_name` — Xcode target to add generated files to (e.g. `MyAppTests`)
- `method_limit` — soft cap (default 5 for first run)
- `destination` — xcodebuild simulator destination (auto-pick if omitted)

If any are missing, stop and ask before any expensive operation.

---

## Step 1 — Scan

1. List Swift/Obj-C source files with `Glob`:
   ```
   <path>/**/*.swift
   <path>/**/*.m   (if --include-objc)
   <path>/**/*.h   (if --include-objc)
   ```
   Exclude paths matching `*/Pods/*`, `*/Carthage/*`, `*/.build/*`, `*/DerivedData/*`, `*/build/*`, and any `*Tests.swift` / `*UITests.swift` / `*Tests/*` directories.

2. For each candidate file, read its imports and top-level type declarations. You do NOT need a tree-sitter parse — pattern-match against `class X : Y`, `struct X`, `final class X`, etc. Capture per class:
   - name, kind (class/struct/enum/protocol/extension)
   - superclass (if any)
   - protocol conformances
   - methods with their access level (`private`, `fileprivate`, `internal`, `public`, `open`), body text, and approximate line count
   - imports (Swift `import X` → "X"; ObjC `#import <X/Foo.h>` → "X")

Skip protocols outright (no implementation to test).

---

## Step 2 — Pre-filter (no Claude API needed)

Drop classes for any of these reasons and record them as "skipped (reason)":

### CocoaPods dependencies (hard skip)

1. Find `Podfile.lock` by walking up from `<path>` to the workspace root.
2. Parse top-level pod names from lines like `  - Alamofire (5.10.0):` — capture the identifier before the version paren.
3. For each scanned class, derive imported module names:
   - Swift: bare module name from `import X`
   - Obj-C: capture `X` from `#import <X/Foo.h>` and `@import X;`
   - Drop quoted-string `#import "Local.h"` (file-local, not a module).
4. If a class's imports intersect the pod module set, skip it. We can't reasonably mock pod-provided frameworks.

### Views and view controllers (hard skip)

A class is UI if ANY of:
- superclass starts with one of: `UIViewController`, `UIView`, `UITableView`, `UICollectionView`, `UITableViewCell`, `UICollectionViewCell`, `UITableViewHeaderFooterView`, `UINavigationController`, `UITabBarController`, `UISplitViewController`, `UIPageViewController`, `UIWindow`, `UIControl`, `UIButton`, `UILabel`, `UIImageView`, `UITextView`, `UITextField`, `UIScrollView`, `UIStackView`, `NSView`, `NSViewController`, `NSWindow`, `NSWindowController`
- conforms to SwiftUI `View`, `App`, or `Scene`
- name ends with `View`, `ViewController`, `Cell`, or `Coordinator` **AND** does NOT end with any of: `ViewModel`, `ViewState`, `ViewData`, `ViewProvider`, `ViewBuilder`, `ViewFactory`, `ViewRouter`, `ViewStore`, `ViewEvent`, `ViewAction` (these have logic and should stay)

### Pure data holders (hard skip)

A `struct` or `enum` is a data holder if EITHER:
- it has zero methods, OR
- every method is trivial: `line_count ≤ 2`, OR no control-flow tokens (`if`, `for`, `while`, `guard`, `switch`, `do`, `try`, `throw`, `return`)

### Per-method filters

For each surviving class, drop methods that are:
- Empty: body is `{}` or whitespace
- Private: `access_level` ∈ {`private`, `fileprivate`}

If a class has zero remaining methods, skip the whole class with "no public/internal non-empty methods".

---

## Step 3 — Existing tests + framework detection

Walk `output_dir` (or the test target dir if different) with Glob `**/*.swift`:

### Framework detection
Count files containing `^\s*import Testing` vs `^\s*import XCTest`. Whichever wins is the **default framework for newly generated files**. Default to `xctest` on ties or empty dirs. Augment mode always matches the existing file's framework, not the project default.

### Existing test files
For each `<ClassName>Tests.swift` or `<ClassName>Tests.m` file found:
1. Read it.
2. Extract test method names:
   - XCTest Swift: `func\s+test_?([A-Za-z][A-Za-z0-9_]*)`
   - XCTest Obj-C: `-\s*\(void\)\s*test_?([A-Za-z][A-Za-z0-9_]*)`
   - Swift Testing: `@Test\b(?:\([^)]*\))?\s*func\s+([A-Za-z][A-Za-z0-9_]*)` (multi-line, attribute may have a description)
3. For each method we plan to test, **prefix-match** the test tokens against the method name (longest method name wins; require either end-of-token or a non-alphanumeric or uppercase boundary). Test method `testIsShortReturnAfterFiveMinutes` covers method `isShortReturn`.

For each class:
- If all planned methods are already covered → mark the class skipped ("all methods already covered").
- If a test file exists and some methods are uncovered → use **augment mode**: extend the existing file with new test methods.
- If no test file exists → use **new mode**: write a fresh `<ClassName>Tests.swift`.

---

## Step 4 — Per-class testability + prioritization

**Exhaustiveness requirement (no early-bail).** You MUST read EVERY class that survived Step 2's pre-filter — not a sample, not "enough to make a plan." Bailing out early once you have a handful of "good enough" candidates is the single biggest failure mode of this skill and produces materially worse plans than the lego Python pipeline (see `lego-vs-skill-comparison.md` Tables 4–6). Concretely:

- Before Step 5, you must have called `Read` on every surviving source file. Track them — a TaskCreate per class or a checklist of remaining files is fine, but the count of files read must equal the count of surviving classes.
- "I've already found N solid candidates so I'll stop" is NOT an acceptable reason to skip remaining files. Read them all, then rank.
- If the candidate count is large (say >40 files), batch-read with multiple parallel Read calls rather than skipping. Token cost is the skill's known trade-off — accept it.
- If a class genuinely has no testable surface after reading, record it as skipped with a specific reason ("touches Bundle.main", "only public method requires UIView in window", etc.) — do NOT silently drop it.

For each surviving class:
1. Read the source file in full.
2. Reason about testability yourself:
   - For each external dependency (property of a non-Foundation/UIKit type), decide: mockable via protocol? Subclassable? Singleton (`.shared`) — needs injection?
   - Note blocking issues that prevent any test (e.g., global state accessed inside method, hard-coded `URLSession.shared`, file system calls without abstraction).
   - Identify which methods are testable as-is vs need refactoring first.
3. Rank methods by priority: business logic density > error handling > side effects > complexity. Skip trivial getters/setters, plain `init`, `deinit`.

**On UserDefaults specifically:** the global "never touch real UserDefaults" rule in Step 6 is about *production* UserDefaults state leaking into tests. A class whose only "untestability" is reading/writing a `UserDefaults.standard` key is still testable — set the key in `setUp`, clear it in `tearDown`, assert on the read. Do NOT skip such classes; queue them with a note that the test must clean up after itself.

You do NOT need an API call per class — you can do this reasoning inline as you read. Combine results into one ranked list across all classes, then take the top `method_limit` methods.

---

## Step 5 — Show plan, ask user

Print a per-class plan to the user:
```
--- Generation plan ---
Framework: xctest (auto-detected)

  [ 1] [new]     <Class> (N method(s)): m1, m2
  [ 2] [augment] <Class> (N method(s)): m3   (adding to <Class>Tests.swift)
  ...

Skipped (with reasons):
  - <Class>: depends on pod modules (Alamofire)
  - <Class>: UI type (inherits UIViewController)
  - <Class>: all methods already covered by existing tests
  ...

~XX classes to generate
```

Ask: *"Numbers to EXCLUDE (comma-separated), empty to keep all, or 'cancel' to abort."* Wait for the user's answer before generating anything.

---

## Step 6 — Generate

For each kept target, produce a complete test file.

### Match the framework

- **XCTest** (Swift): start with `import XCTest` and `@testable import {module}`. Class `<Class>Tests: XCTestCase`. Methods `func test_methodName_scenario_expectedBehavior()`. Use `XCTAssertEqual`, `XCTAssertNil`, `XCTUnwrap`, `XCTestExpectation` with 5s timeout, async methods async.
- **Swift Testing** (Xcode 16+): `import Testing`, `@Suite("<Class>")` struct, `@Test("...")` methods named `methodName_scenario_result()` (no `test` prefix). Use `#expect(...)`, `try #require(...)`, `#expect(throws:)`.

### Hard rules

- Create mock classes for every dependency, with call tracking (`var fooCalled = false`) and configurable return values.
- For augment mode: preserve every line of the existing file as-is; only ADD new test methods (and new helper mocks AT THE BOTTOM of the file). Match the existing file's style precisely.
- NEVER force unwrap (`!`).
- NEVER touch real network, FS, UserDefaults.
- NEVER test private methods directly.

### Output validation

After Claude produces the file content, verify:
- Has `import XCTest` OR `import Testing`
- If XCTest: contains a `XCTestCase` subclass and at least one `func test...`
- If Swift Testing: contains `@Suite` or `@Test`
- No code fences (` ``` `) — strip them if present

If invalid, retry the generation once with a corrective prompt to yourself.

Write the file to `output_dir/<Class>Tests.swift` (new mode) or overwrite the existing path (augment mode).

---

## Step 7 — Auto-add to test target

For each newly written file (skip in augment mode — file's already in the target):

1. Locate `project.pbxproj` inside the `xcodeproj` directory.
2. Read it. It's an OpenStep plist; the structure to understand is:
   - `PBXFileReference` section — lists every file the project knows about
   - `PBXBuildFile` section — pairs a file ref with a target build phase
   - `PBXSourcesBuildPhase` section — has a `files = (...)` array for each target
   - `PBXNativeTarget` section — names each target and its build phases
3. To add a file:
   - Generate two new 24-char hex UUIDs (uppercase) for `<FileRefUUID>` and `<BuildFileUUID>`. Use `openssl rand -hex 12 | tr 'a-f' 'A-F'` via Bash.
   - Insert a new `PBXFileReference` entry like:
     ```
     <FileRefUUID> /* FooTests.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = FooTests.swift; sourceTree = "<group>"; };
     ```
   - Insert a `PBXBuildFile`:
     ```
     <BuildFileUUID> /* FooTests.swift in Sources */ = {isa = PBXBuildFile; fileRef = <FileRefUUID> /* FooTests.swift */; };
     ```
   - Find the test target's `PBXSourcesBuildPhase` (search for the target's name, follow to its `buildPhases`, find the `Sources` phase) and add `<BuildFileUUID> /* FooTests.swift in Sources */,` to its `files` list.
   - Add the file ref to the test target's PBXGroup `children` list.
4. Use `Edit` (exact-string replacement) — do not regex over the whole file unless absolutely necessary. pbxproj corruption breaks the project.

If pbxproj editing seems too risky, you may instead `Write` the test file to `output_dir` and tell the user at the end: *"Drag these files into the {test_target} target in Xcode before running tests."*

---

## Step 8 — Validate (xcodebuild)

If neither `xcworkspace` nor `xcodeproj` was provided, SKIP this step and report that tests were generated but not validated.

### Pick a simulator destination

If `destination` wasn't supplied, run:
```bash
xcrun simctl list devices available --json
```
Parse the JSON. Prefer iPhone 16, then iPhone 15, then any iPhone, then anything iOS. Build `platform=iOS Simulator,name=<picked>`. If nothing usable, report and skip validation.

### Compile

```bash
xcodebuild build-for-testing \
  {-workspace <ws> | -project <proj>} \
  -scheme <scheme> \
  -destination '<dest>'
```

Use `-workspace` whenever a workspace was provided (CocoaPods/SPM-workspace projects don't build correctly with `-project`).

### On failure, parse errors and retry

Look in the captured output for:
- Compiler diagnostics: `<file>:<line>:<col>: error: <message>`
- Linker undefined symbols: `Undefined symbol: "..."` (often missing import / mock not conforming)
- Test failures (after running): `-[ModuleTests.FooTests test_x] : XCTAssertEqual failed: ...`
- Runtime crashes: `EXC_BAD_INSTRUCTION`, `Fatal error: Unexpectedly found nil`

Categorize each as `missing_import`, `mock_mismatch`, `type_mismatch`, `access_control`, `async_issue`, `runtime_crash`, or `other`.

Compose a fix prompt to yourself: read the broken test file, read the source file, identify the failing assertions, and rewrite the test file to fix them. Re-write the file. Re-compile. Stop after at most 3 retries total per class.

### Run tests

If compile succeeds:
```bash
xcodebuild test \
  {-workspace <ws> | -project <proj>} \
  -scheme <scheme> \
  -destination '<dest>' \
  -only-testing:<scheme>Tests/<ClassName>Tests
```

If a test fails, use the same fix-loop logic. Max 3 retries total per class.

---

## Step 9 — Report

At the end, write `output_dir/REPORT.md` with these sections:

```
# Test Generation Report

## Summary
- Classes scanned / kept / generated
- First-pass compile rate
- Final pass rate after retries
- Average retries per class
- (You can't easily estimate token cost; note that)

## Per-Class Results
| Class | Methods | Status | Retries |
| ----- | ------- | ------ | ------- |

## Failed
- Class: short error summary

## Skipped Pre-filter
- Class: reason

## Refactoring Suggestions (for classes that couldn't be tested as-is)
- Class: blocking issue → suggestion
```

Print the report to stdout AND save it.

---

## What this skill is good for

This skill produces the same artifacts as the `lego` Python CLI but does everything inside a Claude conversation. Use it to compare:

- Cost: pure-Claude will spend significantly more tokens because there's no free local tree-sitter parse, no batched analyzer call, no structured-output JSON — every reading/reasoning step is conversational.
- Determinism: pure-Claude may make different choices on different runs (Claude wanders); the Python pipeline always produces the same plan from the same inputs.
- Maintainability: the skill is one markdown file; the pipeline is ~3000 lines of Python with 122 tests.
- Speed: pure-Claude is roughly linear in classes (one reasoning pass each); Python batches and parallelizes better.

When evaluating, run BOTH on the same project with the same `method_limit` and `path`, then compare:
- Total Anthropic spend (input + output tokens × pricing)
- Wall-clock time
- Number of generated test files
- First-pass compile rate
- Final pass rate after retries
- Quality / coverage of the generated tests (manual review)
