from __future__ import annotations

from .models import PipelineReport


def generate_report(report: PipelineReport) -> str:
    lines: list[str] = []
    lines.append("# Test Generation Report\n")
    lines.append("## Summary")
    pct = lambda part, whole: f"{(part / whole * 100):.1f}%" if whole else "n/a"
    lines.append(f"- Files scanned: {report.files_scanned}")
    lines.append(f"- Classes analyzed: {report.classes_analyzed}")
    lines.append(
        f"- Testable classes: {report.testable_classes} "
        f"({pct(report.testable_classes, report.classes_analyzed)})"
    )
    lines.append(f"- Classes needing refactoring: {report.classes_needing_refactor}")
    lines.append(f"- Tests generated: {report.tests_generated}")
    lines.append(f"- First-pass compilation rate: {report.first_pass_compile_rate * 100:.1f}%")
    lines.append(f"- Final pass rate (after retries): {report.final_pass_rate * 100:.1f}%")
    lines.append(f"- Average retries needed: {report.average_retries:.2f}")
    lines.append(f"- Total API cost: ${report.estimated_cost:.4f}")
    lines.append("")

    lines.append("## Per-Class Results\n")
    lines.append("| Class | Methods Tested | Status | Retries |")
    lines.append("| --- | --- | --- | --- |")
    for r in report.class_results:
        methods = ", ".join(r.methods) if r.methods else "—"
        lines.append(f"| {r.class_name} | {methods} | {r.status} | {r.retries} |")
    lines.append("")

    failed = [
        r for r in report.class_results
        if r.status in {"compile_failed", "test_failed", "generation_failed"}
    ]
    if failed:
        lines.append("## Failed Tests\n")
        for r in failed:
            summary = r.error_summary or "(no error captured)"
            lines.append(f"- **{r.class_name}**: {summary}")
        lines.append("")

    if report.refactoring_needed:
        lines.append("## Refactoring Needed\n")
        lines.append("Classes that couldn't be tested without changes:\n")
        for item in report.refactoring_needed:
            issues = "; ".join(item.get("blocking_issues") or []) or "(unspecified)"
            lines.append(f"- **{item['class_name']}**: {issues}")
            for s in item.get("refactoring_suggestions") or []:
                lines.append(f"  - suggestion: {s}")
        lines.append("")

    if report.top_recommendations:
        lines.append("## Recommendations\n")
        lines.append("Top prioritized methods to focus on next:\n")
        for i, m in enumerate(report.top_recommendations[:10], start=1):
            reason = f" — {m.reason}" if m.reason else ""
            lines.append(f"{i}. `{m.class_name}.{m.method_name}` (score {m.priority_score}){reason}")
        lines.append("")

    lines.append("## Token Usage\n")
    usage = report.token_usage or {}
    by_type = usage.get("by_type", {})
    for call_type in ("analysis", "generation", "fix", "other"):
        bucket = by_type.get(call_type)
        if not bucket:
            continue
        lines.append(
            f"- {call_type} calls: {bucket['calls']} "
            f"({bucket['input_tokens'] + bucket['output_tokens']} tokens)"
        )
    lines.append(
        f"- Total: {usage.get('total_input_tokens', 0) + usage.get('total_output_tokens', 0)} "
        f"tokens, ${report.estimated_cost:.4f}"
    )
    lines.append("")

    return "\n".join(lines)
