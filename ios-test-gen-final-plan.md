# iOS Legacy Test Generator — Final Development Plan
## Hybrid Architecture: tree-sitter + Claude, All Python

---

## Architecture Overview

Three-layer pipeline:

```
Layer 1: tree-sitter (Fast, free, deterministic)
  Scan entire codebase → extract AST metadata → structured JSON
  Runs in milliseconds on hundreds of files

Layer 2: Claude + AST metadata (Cheap, intelligent triage)
  Read structural summaries → classify testability → rank priority
  Small JSON payloads, minimal token cost

Layer 3: Claude + full file content (Deep understanding, test generation)
  Only for filtered high-priority testable classes
  Full file + related protocols/models as context
  Generates complete XCTest files
  xcodebuild validates → Claude fixes failures → retry loop
```

---

## Project Structure

```
swift-test-gen/
├── CLAUDE.md
├── README.md
├── LICENSE
├── pyproject.toml
├── src/
│   └── swift_test_gen/
│       ├── __init__.py
│       ├── cli.py                    # Click CLI entry point
│       ├── models.py                 # All Pydantic data models
│       │
│       ├── scanner/                  # LAYER 1: tree-sitter
│       │   ├── __init__.py
│       │   ├── swift_scanner.py      # Parse .swift files → AST metadata
│       │   ├── objc_scanner.py       # Parse .m/.h files → AST metadata
│       │   └── file_discovery.py     # Find and filter source files
│       │
│       ├── analyzer/                 # LAYER 2: Claude + AST metadata
│       │   ├── __init__.py
│       │   ├── testability.py        # Classify classes as testable/partial/needs-refactor
│       │   ├── prioritizer.py        # Rank methods by test value
│       │   └── templates/
│       │       ├── testability.txt   # Prompt: assess testability from AST
│       │       └── prioritize.txt    # Prompt: rank methods by importance
│       │
│       ├── generator/                # LAYER 3: Claude + full file
│       │   ├── __init__.py
│       │   ├── context_builder.py    # Assembles full file + related files
│       │   ├── test_generator.py     # Generates XCTest files
│       │   └── templates/
│       │       ├── generate.txt      # Prompt: generate tests
│       │       └── fix.txt           # Prompt: fix failing tests
│       │
│       ├── llm/                      # Shared Claude API layer
│       │   ├── __init__.py
│       │   ├── client.py             # Anthropic SDK wrapper
│       │   └── token_tracker.py      # Track usage and estimate costs
│       │
│       ├── validator/                # xcodebuild integration
│       │   ├── __init__.py
│       │   ├── xcodebuild.py         # Run builds and tests
│       │   ├── error_parser.py       # Parse compiler/runtime errors
│       │   └── feedback_loop.py      # Error → Claude → fix → retry
│       │
│       ├── orchestrator.py           # Runs the full 3-layer pipeline
│       └── reporter.py               # Generates markdown reports
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── simple_model.swift
│   │   ├── view_controller.swift
│   │   ├── network_service.swift
│   │   ├── protocol_example.swift
│   │   ├── singleton_heavy.swift
│   │   └── expected_ast/             # Expected scanner output for each fixture
│   │       ├── simple_model.json
│   │       ├── view_controller.json
│   │       └── network_service.json
│   ├── test_scanner.py
│   ├── test_analyzer.py
│   ├── test_generator.py
│   ├── test_validator.py
│   └── test_orchestrator.py
│
├── demo/
│   ├── sample_project/               # Small legacy iOS project for demos
│   └── run_demo.sh
│
└── docs/
    ├── architecture.md
    └── prompt_changelog.md           # Track prompt iterations and results
```

---

## CLAUDE.md

```markdown
# CLAUDE.md — swift-test-gen

## What this project is
A Python CLI that generates XCTest unit tests for legacy iOS/Swift codebases.
Uses a hybrid approach: tree-sitter for fast AST extraction, Claude API for
intelligent analysis and test generation.

## Architecture: Three Layers
1. **Scanner (tree-sitter)**: Fast, deterministic extraction of class/method/
   property/protocol metadata from Swift files. Outputs structured JSON.
   No LLM calls. Processes hundreds of files in milliseconds.
2. **Analyzer (Claude + AST JSON)**: Takes the structural metadata and
   classifies testability, identifies dependency injection patterns, ranks
   methods by priority. Cheap — sends small JSON, not full files.
3. **Generator (Claude + full files)**: Only for filtered, testable classes.
   Sends full source + related files. Generates complete XCTest files.
   Validated by xcodebuild with a fix-retry loop.

## Tech stack
- Python 3.11+
- tree-sitter + tree-sitter-swift for AST parsing
- Anthropic Python SDK for Claude API
- Click for CLI
- Pydantic for data models
- pytest for testing

## Commands
- `pip install -e ".[dev]"` — install with dev deps
- `pytest` — run all tests
- `swift-test-gen scan --path ./MyApp` — Layer 1 only, outputs AST JSON
- `swift-test-gen analyze --path ./MyApp` — Layers 1+2, outputs testability report
- `swift-test-gen generate --path ./MyApp --output ./Tests` — full pipeline
- `swift-test-gen estimate --path ./MyApp` — cost/time estimate without generating

## Key data models (models.py)
- SwiftFile: path, content, language (swift|objc)
- ClassMetadata: name, superclass, protocols, properties, methods, imports
  (extracted by tree-sitter — deterministic, structured)
- MethodMetadata: name, parameters, return_type, access_level, is_static,
  line_count, body_text
- PropertyMetadata: name, type, is_optional, is_let, access_level,
  injection_style (init|property|none)
- DependencyInfo: type_name, injection_style, is_protocol, is_singleton,
  is_system_framework
- TestabilityResult: class_name, testable (bool), mockable_deps, unmockable_deps,
  refactoring_suggestions, priority_score
- GeneratedTest: file_content, target_class, target_methods, mock_code
- ValidationResult: compiled (bool), passed (bool), errors, retry_count
- PipelineReport: files_scanned, classes_analyzed, tests_generated, pass_rate,
  token_usage, estimated_cost

## Code conventions
- Type hints on all function signatures
- Pydantic models for all data between modules
- Docstrings on all public functions
- No wildcard imports
- pytest fixtures, not setUp/tearDown
- Prompt templates in .txt files, prompt assembly in Python

## When editing prompts
- Log every prompt change in docs/prompt_changelog.md with date, what changed,
  and the effect on pass rates
- Test prompt changes against all fixture files before committing
- Use the --dry-run flag to see assembled prompts without API calls
```

---

## Development Phases

---

### Phase 0: Project Scaffolding
**Time: 1 day**

**Claude Code prompt:**
```
Read CLAUDE.md. Initialize the swift-test-gen project:

1. Create the full directory structure as specified in CLAUDE.md
2. Set up pyproject.toml:
   - Dependencies: click, anthropic, pydantic, tree-sitter, tree-sitter-swift
   - Dev dependencies: pytest, pytest-cov, ruff, mypy
   - Console script entry point: swift-test-gen = swift_test_gen.cli:main
3. Create models.py with ALL Pydantic models listed in CLAUDE.md
4. Create cli.py with Click command group and these subcommands (stubs):
   - scan: --path (required), --output (optional JSON file path)
   - analyze: --path, --output, --api-key / ANTHROPIC_API_KEY env
   - generate: --path, --output (required), --api-key, --model (default
     claude-sonnet-4-20250514), --xcodeproj, --scheme, --max-retries (3),
     --dry-run, --single-file, --batch-size (10), --include-objc
   - estimate: --path, --api-key
5. Create the fixture Swift files in tests/fixtures/:
   - simple_model.swift: a basic User struct with name, email, init,
     a validation method, and a formatting method
   - view_controller.swift: a UIViewController subclass with IBOutlets,
     a tableView dataSource, a network service dependency injected via init,
     an analytics singleton accessed via .shared, UserDefaults usage, and
     3-4 methods with real logic
   - network_service.swift: a protocol NetworkServiceProtocol with a
     fetch method, and a concrete NetworkService class conforming to it
   - protocol_example.swift: a protocol with 3 methods and a class that
     conforms to it with all dependencies injected via init (fully testable)
   - singleton_heavy.swift: a class that accesses 3 different singletons
     directly (hard to test, needs refactoring)
6. Create empty conftest.py and placeholder test files

Verify: pip install -e ".[dev]" works, swift-test-gen --help shows all commands.
```

---

### Phase 1: Scanner (tree-sitter AST Extraction)
**Time: 3-4 days**

**Claude Code prompt:**
```
Read CLAUDE.md. Build the scanner module (Layer 1).

1. scanner/file_discovery.py:
   - Function discover_files(path, include_objc=False) that:
     - Recursively finds .swift files (and .m/.h if include_objc)
     - Skips: Pods/, Carthage/, .build/, DerivedData/, *Tests.swift,
       *Tests/, Package.swift, *.generated.swift
     - Respects .gitignore if present
     - Returns list of SwiftFile models (path + content + language)

2. scanner/swift_scanner.py:
   - Function scan_file(swift_file: SwiftFile) -> list[ClassMetadata]
   - Uses tree-sitter with tree-sitter-swift grammar
   - Extracts from each class/struct/enum:
     - Name, superclass, protocol conformances
     - All properties with: name, type, optional?, let/var, access level
     - All methods with: name, full parameter list (name + type for each),
       return type, access level, is_static, line count, raw body text
     - Import statements
   - For properties, detect injection style:
     - "init" if the property appears as an init parameter
     - "property" if it's a var with a setter (could be set externally)
     - "none" if it's hardcoded, uses .shared, or is a let with default
   - Handle nested types, extensions, and protocol definitions
   - Return structured ClassMetadata models

3. scanner/objc_scanner.py:
   - Same interface but for Objective-C files
   - Extract: @interface declarations, method signatures (-/+), properties
     (@property), protocol conformances (<NSCoding, UITableViewDelegate>)
   - Simpler than Swift — focus on getting class/method/property names and types

4. Create tests/fixtures/expected_ast/ JSON files with the exact expected
   scanner output for simple_model.swift, view_controller.swift, and
   network_service.swift.

5. Write thorough tests in test_scanner.py:
   - Parse each fixture file and compare output to expected JSON
   - Test edge cases: extensions, nested types, computed properties,
     closures in property initializers, generic types
   - Test file_discovery filtering logic

Wire the scan CLI command: swift-test-gen scan --path ./tests/fixtures/
should print the AST JSON for all fixture files.

Verify: pytest tests/test_scanner.py passes, CLI scan command outputs valid JSON.
```

**Validation step before moving on:**
```
Run the scanner against a real open-source iOS project to check coverage:
git clone https://github.com/nicklockwood/iCarousel.git /tmp/icarousel
swift-test-gen scan --path /tmp/icarousel

Verify it doesn't crash and extracts reasonable metadata. Fix any tree-sitter
edge cases that come up.
```

---

### Phase 2: LLM Client & Token Tracking
**Time: 1 day**

**Claude Code prompt:**
```
Read CLAUDE.md. Build the shared LLM layer.

1. llm/client.py:
   - Class ClaudeClient initialized with api_key and model name
   - Method call(messages, max_tokens=4096) that:
     - Calls Anthropic SDK messages.create()
     - Retries on rate limit errors with exponential backoff (3 attempts)
     - Retries on overloaded errors with 30-second wait
     - Returns the response text content
   - Method call_json(messages, max_tokens=4096) that:
     - Calls call() and parses response as JSON
     - If JSON parsing fails (markdown fences, preamble text), strips
       common wrappers and retries parsing
     - If still fails, makes one more API call asking Claude to fix the JSON
     - Returns parsed dict/list
   - Method call_dry_run(messages) that just logs the prompt and returns None

2. llm/token_tracker.py:
   - Class TokenTracker that accumulates:
     - Total input tokens, output tokens per call
     - Number of API calls by type (analysis, generation, fix)
     - Estimated cost (using Claude pricing)
   - Method report() that returns a summary dict
   - The ClaudeClient should automatically feed usage data to the tracker
     after each call

Write tests with mocked Anthropic client. Test retry logic, JSON parsing
with various malformed responses (fenced, with preamble, truncated).
```

---

### Phase 3: Analyzer (Testability & Prioritization)
**Time: 2-3 days**

**Claude Code prompt:**
```
Read CLAUDE.md. Build the analyzer module (Layer 2).

1. analyzer/templates/testability.txt — the testability assessment prompt:
   """
   You are an expert iOS developer assessing code testability.

   Given the following AST metadata for Swift classes (as JSON), assess
   each class's testability. For each class, determine:

   1. testable: true/false — can meaningful unit tests be written as-is?
   2. testability_score: 0-100 — how testable is it overall?
   3. For each dependency (property with external type):
      - mockable: true/false
      - mock_strategy: "protocol" | "subclass" | "wrapper" | "injection_needed"
      - reason: brief explanation
   4. blocking_issues: list of things preventing testability
      (e.g., "AnalyticsManager accessed via singleton .shared — no injection point")
   5. refactoring_suggestions: specific, actionable steps to make it testable
      (e.g., "Add init parameter for AnalyticsManager, extract protocol")
   6. testable_methods: list of method names that CAN be tested right now
   7. untestable_methods: list of methods that need refactoring first, with reason

   Respond ONLY with valid JSON. No markdown fences. No preamble.
   Schema: { "assessments": [ { "class_name": "", "testable": bool,
   "testability_score": int, "dependencies": [...], "blocking_issues": [...],
   "refactoring_suggestions": [...], "testable_methods": [...],
   "untestable_methods": [...] } ] }
   """

2. analyzer/templates/prioritize.txt — the prioritization prompt:
   """
   You are an expert iOS developer deciding which methods to test first.

   Given the following testable classes and their methods (as JSON),
   rank the methods by test priority. Consider:
   - Business logic density (data transformations, validations, calculations
     rank highest)
   - Error handling paths (methods with multiple failure modes)
   - Side effects (methods that change state, call external services)
   - Complexity (methods with many branches/conditions)
   - Skip trivial code: simple getters, setters, one-line passthroughs,
     deinit, basic init with no logic

   Return a JSON array of methods in priority order:
   [{ "class_name": "", "method_name": "", "priority_score": 1-100,
      "reason": "brief explanation", "suggested_test_cases": ["happy path",
      "nil input", ...] }]
   Top 50 methods max. Respond ONLY with valid JSON.
   """

3. analyzer/testability.py:
   - Function assess_testability(class_metadata_list, claude_client) that:
     - Batches classes into groups that fit comfortably in context
       (~50 classes per batch, since we're sending compact AST JSON)
     - Builds the testability prompt with the AST JSON
     - Calls claude_client.call_json()
     - Returns list of TestabilityResult models
   - Function filter_testable(results) that returns only classes with
     testable=True or that have at least some testable_methods

4. analyzer/prioritizer.py:
   - Function prioritize_methods(testable_classes, claude_client) that:
     - Takes the filtered testable classes
     - Builds the prioritization prompt
     - Returns ranked list of (class_name, method_name, priority_score)
   - Function apply_limit(ranked_methods, limit) that takes the top N

5. Wire the analyze CLI command:
   swift-test-gen analyze --path ./tests/fixtures/
   Should output a testability report showing which fixtures are testable,
   which need refactoring, and a prioritized method list.

Write tests using the fixture expected_ast JSON files as input with a
mocked Claude client that returns realistic assessment JSON. Verify the
filtering and ranking logic works correctly.
```

---

### Phase 4: Generator (Full Test Generation)
**Time: 3-4 days**

This is the core of the product. Spend extra time here.

**Claude Code prompt:**
```
Read CLAUDE.md. Build the generator module (Layer 3).

1. generator/context_builder.py:
   - Function build_context(target_file, all_files, analysis) that:
     - Starts with the full content of the target Swift file
     - From the analysis, identifies which dependencies need mock protocols
     - Searches all_files for protocol definitions, base classes, and model
       types referenced by the target class
     - Assembles a context bundle: target file + related files
     - If total content exceeds 100K characters, prioritize: protocols first,
       then models referenced in method signatures, then other imports
     - Returns a ContextBundle with: target_content, related_contents dict,
       analysis_summary (from Layer 2)

2. generator/templates/generate.txt — the test generation prompt:
   """
   You are an expert iOS test engineer writing XCTest unit tests.

   TARGET FILE:
   {target_content}

   RELATED FILES (protocols, models, dependencies):
   {related_contents}

   TESTABILITY ANALYSIS:
   {analysis_summary}

   Generate a COMPLETE, COMPILABLE XCTest file for the class {class_name},
   specifically testing these methods: {method_list}

   REQUIREMENTS:
   - Start with: import XCTest @testable import {module_name}
   - Create mock classes for ALL dependencies using protocols
     - Each mock should track method calls (e.g., var fetchUserCalled = false)
     - Each mock should allow configuring return values (e.g., var fetchUserResult: Result<User, Error>)
   - setUp(): create the system under test with all mocked dependencies
   - tearDown(): set sut and all mocks to nil
   - Test naming: test_methodName_scenario_expectedBehavior
   - For each method, include tests for:
     - Happy path with valid inputs
     - Nil or empty inputs where applicable
     - Error conditions and failure paths
     - Boundary values (empty strings, zero, negative numbers, max values)
   - Assertions: use XCTAssertEqual, XCTAssertNil, XCTAssertNotNil,
     XCTAssertTrue, XCTAssertFalse, XCTAssertThrowsError as appropriate
   - For async methods using completion handlers:
     - Use XCTestExpectation
     - Always set timeout to 5.0 seconds
     - Call expectation.fulfill() in the completion
   - For async/await methods: use async test methods
   - NEVER force unwrap (!) — use XCTUnwrap or guard let + XCTFail
   - NEVER use real network, file system, or UserDefaults — everything mocked
   - NEVER test private methods directly

   Output ONLY the complete .swift test file. No markdown fences.
   No explanation. No preamble. Just the Swift code.
   """

3. generator/templates/fix.txt — the fix prompt:
   """
   The following XCTest file failed to compile or run. Fix the errors.

   ORIGINAL TEST FILE:
   {test_content}

   BUILD/TEST ERRORS:
   {error_output}

   ORIGINAL SOURCE FILE (for reference):
   {source_content}

   Common issues to check:
   - Missing imports
   - Mock method signatures don't match the protocol
   - Wrong assertion types (XCTAssertEqual needs Equatable conformance)
   - Accessing private members
   - Missing @objc for Objective-C protocol methods
   - XCTestExpectation not fulfilled (async timing issues)
   - Main thread assertions for UI code (use DispatchQueue.main in test)
   - Force unwraps that crash on nil

   Return ONLY the complete corrected .swift file. No markdown fences.
   No explanation. Just the Swift code.
   """

4. generator/test_generator.py:
   - Function generate_tests(context_bundle, claude_client, methods) that:
     - Builds the generation prompt from context + template
     - Calls claude_client.call() (not call_json — response is Swift code)
     - Strips any markdown fences if present
     - Validates basic structure: has "import XCTest", has "class.*Tests",
       has at least one "func test"
     - Returns GeneratedTest model with the file content
   - Function write_test_file(generated_test, output_dir) that:
     - Names the file {ClassName}Tests.swift
     - Writes to output_dir
     - Returns the file path

Write tests with mocked Claude client. The mock should return realistic
Swift test code (create a hand-written example test in fixtures/).
Test that the context builder correctly assembles related files and
that the generator handles markdown fences, extra preamble text, etc.
```

---

### Phase 5: Validator & Feedback Loop
**Time: 3 days**

**Claude Code prompt:**
```
Read CLAUDE.md. Build the validation module.

1. validator/xcodebuild.py:
   - Function compile_test(test_file_path, xcodeproj, scheme) that:
     - Runs: xcodebuild build-for-testing -project {xcodeproj}
       -scheme {scheme} -destination 'platform=iOS Simulator,name=iPhone 16'
     - Timeout: 120 seconds
     - Captures stdout + stderr
     - Returns (success: bool, output: str)
   - Function run_test(test_file_path, xcodeproj, scheme, test_class) that:
     - Runs: xcodebuild test -project {xcodeproj} -scheme {scheme}
       -destination 'platform=iOS Simulator,name=iPhone 16'
       -only-testing:{scheme}Tests/{test_class}
     - Timeout: 180 seconds
     - Returns (success: bool, output: str)
   - Both functions should handle: xcodebuild not found (not on macOS),
     simulator not available, project file not found — with clear error messages

2. validator/error_parser.py:
   - Function parse_errors(xcodebuild_output) that extracts:
     - Compilation errors: {file, line, column, message, severity}
     - Linker errors: {symbol, message}
     - Test failures: {test_name, assertion, expected, actual, file, line}
     - Runtime crashes: {signal, message, backtrace_summary}
   - Function categorize_errors(errors) that groups them:
     - "missing_import" — missing module or type
     - "type_mismatch" — wrong types in assertions or assignments
     - "access_control" — accessing private/internal members
     - "mock_mismatch" — mock doesn't conform to protocol correctly
     - "async_issue" — expectation timeout, wrong async pattern
     - "runtime_crash" — EXC_BAD_ACCESS, nil unwrap, etc.
     - "other"
   - This categorization helps the fix prompt give Claude better context

3. validator/feedback_loop.py:
   - Function validate_and_fix(generated_test, source_file, config, claude_client):
     - Step 1: Write test file to project's test target directory
     - Step 2: Compile. If compilation fails → parse errors → build fix prompt
       → call Claude → write corrected file → go to Step 2
     - Step 3: If compilation succeeds, run tests. If tests fail →
       parse failures → build fix prompt → call Claude → write corrected
       file → go to Step 2
     - Max retries from config (default 3)
     - Log each iteration: what errors occurred, what fix was attempted
     - Return ValidationResult with: final pass/fail, error history,
       retry count, token usage for fix calls
   - If no --xcodeproj provided, skip validation entirely and just output
     the generated test files with a warning that they haven't been validated

Write tests for error_parser using hardcoded xcodebuild output strings
as fixtures (include examples of each error category). Test feedback_loop
with mocked xcodebuild and mocked Claude client — simulate a scenario
where the first generation has a compilation error, the fix resolves it,
but a test fails, and the second fix resolves that too.
```

---

### Phase 6: Orchestrator & Reporter
**Time: 2 days**

**Claude Code prompt:**
```
Read CLAUDE.md. Build the orchestrator and reporter.

1. orchestrator.py — the main pipeline:
   - Function run_pipeline(config) that executes:

     LAYER 1 — SCAN:
     a. Discover all Swift files in --path using file_discovery
     b. Scan each file with tree-sitter → list of ClassMetadata
     c. Log: "Scanned {N} files, found {M} classes, {K} methods"

     LAYER 2 — ANALYZE:
     d. Send AST metadata to Claude for testability assessment
     e. Filter to testable classes
     f. Prioritize methods
     g. Apply --batch-size limit
     h. Log: "{X} classes testable, {Y} methods prioritized"

     LAYER 3 — GENERATE:
     i. For each prioritized class:
        - Build context bundle (full file + related files)
        - Generate tests via Claude
        - Write test file to --output
        - If --xcodeproj provided: validate and run feedback loop
        - Track results
     j. Log progress: "Generated tests for {class} ({N}/{total})"

     REPORT:
     k. Generate report and write to --output/REPORT.md
     l. Print summary to stdout

   - Handle interruption gracefully (Ctrl+C saves progress so far)
   - Support --single-file mode: skip layers 1-2, go straight to layer 3
     for one specific file
   - Support --dry-run: run layers 1-2, then for layer 3 print the prompts
     that would be sent without calling the API

2. reporter.py:
   - Function generate_report(results, token_tracker) that produces markdown:

     # Test Generation Report

     ## Summary
     - Files scanned: {N}
     - Classes analyzed: {M}
     - Testable classes: {X} ({pct}%)
     - Classes needing refactoring: {Y}
     - Tests generated: {Z}
     - First-pass compilation rate: {pct}%
     - Final pass rate (after retries): {pct}%
     - Average retries needed: {avg}
     - Total API cost: ${amount}

     ## Per-Class Results
     | Class | Methods Tested | Status | Retries |
     ...

     ## Failed Tests
     - {ClassName}.{method}: {error summary}

     ## Refactoring Needed
     Classes that couldn't be tested without changes:
     - {ClassName}: {blocking issues}

     ## Recommendations
     Top untested methods to prioritize next:
     1. {class.method} — {reason}

     ## Token Usage
     - Analysis calls: {N} ({tokens} tokens, ${cost})
     - Generation calls: {N} ({tokens} tokens, ${cost})
     - Fix calls: {N} ({tokens} tokens, ${cost})
     - Total: {tokens} tokens, ${cost}

3. Wire all CLI commands:
   - `scan` → file_discovery + scanner, output JSON
   - `analyze` → scan + analyzer, output testability report
   - `generate` → full pipeline via orchestrator
   - `estimate` → scan + analyze + calculate estimated generation
     costs without actually generating (count methods × avg tokens per
     generation call × price per token)

Write integration tests for the orchestrator using mocked Claude client
and mocked xcodebuild. Test the full pipeline end-to-end: scan fixtures
→ analyze → generate → validate → report. Verify the report contains
accurate numbers.
```

---

### Phase 7: Polish & Open Source
**Time: 2-3 days**

**Claude Code prompt:**
```
Read CLAUDE.md. Prepare for open source release.

1. README.md:
   - Project name, one-line description, badges (CI, Python version, license)
   - Problem statement: "Legacy iOS codebases have near-zero test coverage..."
   - Solution: "swift-test-gen uses tree-sitter for code analysis and Claude
     for intelligent test generation..."
   - Quick start (5 lines: install, set API key, run)
   - Full CLI reference for all 4 commands with examples
   - Architecture diagram (the 3-layer ASCII diagram)
   - Example: show a real before/after (legacy ViewController → generated tests)
   - Configuration options
   - Limitations: requires macOS + Xcode for validation, API costs, non-deterministic
   - Contributing
   - License: MIT

2. Code quality:
   - ruff check --fix . && ruff format .
   - mypy src/ — fix all type errors
   - Verify all tests pass with pytest -v --cov
   - Add missing docstrings

3. CI: .github/workflows/ci.yml
   - Trigger on push to main and PRs
   - Matrix: Python 3.11, 3.12
   - Steps: install deps, ruff check, mypy, pytest with coverage
   - Upload coverage to codecov (optional)

4. demo/ directory:
   - sample_project/ with 3-4 Swift files exhibiting common legacy patterns:
     a ViewController with mixed dependencies, a model with validation logic,
     a network service with protocol, and a utility class with pure functions
   - run_demo.sh that runs the full pipeline against sample_project
   - sample_output/ with example generated tests and report for reference

5. docs/architecture.md:
   - Detailed architecture writeup (good for EB-2)
   - The 3-layer design rationale
   - Why hybrid approach (tree-sitter + LLM)
   - Prompt engineering methodology
   - Validation feedback loop design
   - Benchmarking approach

6. BENCHMARKS.md (template to fill with real numbers):
   - First-pass compilation rate: ____%
   - Pass rate after feedback loop: ____%
   - Average retries needed: ____
   - Average tokens per class: ____
   - Average cost per class: $____
   - Tested against: (list of open-source iOS projects)

7. docs/prompt_changelog.md:
   - Template for tracking prompt iterations
   - Date | Change | Effect on pass rate | Notes

8. CONTRIBUTING.md:
   - How to set up development environment
   - How to run tests
   - How to add new fixture files
   - How to modify prompts (always update changelog)
   - PR process
```

---

## Timeline

| Phase | What | Days | Cumulative |
|-------|------|------|------------|
| 0 | Scaffolding | 1 | 1 |
| 1 | Scanner (tree-sitter) | 3-4 | 4-5 |
| 2 | LLM Client | 1 | 5-6 |
| 3 | Analyzer (testability + priority) | 2-3 | 7-9 |
| 4 | Generator (test generation) | 3-4 | 10-13 |
| 5 | Validator (xcodebuild + feedback loop) | 3 | 13-16 |
| 6 | Orchestrator + Reporter | 2 | 15-18 |
| 7 | Polish + Open Source | 2-3 | 17-21 |

**MVP (Phases 0-4):** ~13 days. Scans codebase, analyzes testability,
generates test files. No xcodebuild validation — you run the tests manually.

**Full tool (Phases 0-7):** ~3 weeks.

---

## Working with Claude Code: Session-by-Session

```bash
# Day 1 — Scaffolding
claude "Read CLAUDE.md, then do Phase 0"
pip install -e ".[dev]"
swift-test-gen --help

# Days 2-4 — Scanner
claude "Read CLAUDE.md, then do Phase 1: build the scanner module"
pytest tests/test_scanner.py -v
swift-test-gen scan --path ./tests/fixtures/

# Day 5 — LLM Client
claude "Read CLAUDE.md, then do Phase 2: build the LLM client"
pytest tests/ -v

# Days 6-8 — Analyzer
claude "Read CLAUDE.md, then do Phase 3: build the analyzer"
# First real API test:
export ANTHROPIC_API_KEY=sk-...
swift-test-gen analyze --path ./tests/fixtures/

# Days 9-12 — Generator
claude "Read CLAUDE.md, then do Phase 4: build the generator"
swift-test-gen generate --path ./tests/fixtures/simple_model.swift \
  --output ./generated_tests/ --single-file

# Days 13-15 — Validator
claude "Read CLAUDE.md, then do Phase 5: build the validator"
# Test against a real Xcode project:
swift-test-gen generate --path ./demo/sample_project/ \
  --output ./demo/sample_project/Tests/ \
  --xcodeproj ./demo/sample_project/SampleProject.xcodeproj \
  --scheme SampleProject

# Days 16-17 — Orchestrator
claude "Read CLAUDE.md, then do Phase 6: build the orchestrator and reporter"
# Full pipeline test:
swift-test-gen generate --path ./demo/sample_project/ \
  --output ./demo/generated/ --xcodeproj ./demo/sample_project/SampleProject.xcodeproj \
  --scheme SampleProject

# Days 18-20 — Polish
claude "Read CLAUDE.md, then do Phase 7: polish for open source"
```

---

## Debugging Prompts for Common Issues

```bash
# Tests won't compile — mock signatures wrong
claude "The generated tests are failing to compile because the mock classes
don't match the protocol signatures. Here's the error: {paste error}.
Update the generate.txt prompt to be more explicit about matching exact
protocol method signatures including parameter labels."

# Tests compile but fail — wrong assertions
claude "Generated tests compile but fail at runtime. The assertions are
using XCTAssertEqual on types that don't conform to Equatable. Update
the generate prompt to check for Equatable conformance and use
XCTAssertNotNil or property-level assertions as fallback."

# Claude returns markdown-fenced code
claude "The LLM client is receiving responses wrapped in ```swift fences
even though the prompt says not to. Make the response parsing more robust —
strip triple backtick fences and any language identifier before extracting
the Swift code."

# Scanner misses methods in extensions
claude "tree-sitter isn't picking up methods defined in Swift extensions.
The fixture view_controller.swift has a UITableViewDataSource extension
and those methods don't appear in the scan output. Fix swift_scanner.py
to handle extension blocks and associate their methods with the extended type."

# Token usage too high
claude "The generator is using 50K+ tokens per class because context_builder
includes too many related files. Add smarter filtering: only include files
that define types directly referenced in the target class's method signatures
and property types. Skip transitive dependencies."
```

---

## EB-2 Deliverables from This Project

As you build, you'll naturally produce:

1. **Open source project with adoption metrics** — GitHub stars, forks, issues
2. **Published benchmarks** — BENCHMARKS.md with real numbers on compilation rates,
   pass rates, cost efficiency
3. **Architecture writeup** — docs/architecture.md (expand into a blog post or paper)
4. **Prompt engineering methodology** — docs/prompt_changelog.md showing systematic
   iteration with measurable improvements
5. **Client impact evidence** — run it against a client project, measure hours saved,
   get a letter from your company attesting to the impact
6. **Technical novelty** — the 3-layer hybrid approach (AST scanning → AI triage →
   AI generation with compiler validation) is a genuinely novel contribution to
   AI-assisted testing
