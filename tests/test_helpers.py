# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pytest>=8.0.0",
#     "pytest-asyncio>=0.24.0",
#     "pydantic>=2.0.0",
#     "mcp[cli]>=1.0.0,<2.0.0",
# ]
# ///
"""
Tests for Claudex MCP server helpers.

Run with:
    uv run --script tests/test_helpers.py

Or directly:
    uv run pytest tests/test_helpers.py -v
"""

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio

# --- Import server helpers ---
# Add project root to sys.path so we can import from server/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "server"))

from server import (
    _safe_claudex_path,
    _normalize_file_list,
    _init_session,
    _append_to_session,
    _read_session_rounds,
    _get_truncated_session,
    _build_collaborate_system,
    _build_review_system,
    _auto_session_id,
    _check_codex_version,
    _version_cache,
    _metrics,
    _record_metric,
    _get_metrics_summary,
    _chain_session_id,
    _format_finding,
    _format_review_files_json,
    _format_review_diff_json,
    RequestType,
    COLLAB_PERSONAS,
    MAX_SESSION_ROUNDS,
    SESSION_MAX_BYTES,
    DEFAULT_MODEL,
    DEFAULT_REASONING_SUMMARY,
    EXEC_TIMEOUT_SECONDS,
    ARTIFACT_INSTRUCTIONS,
    STRUCTURED_OUTPUT_INSTRUCTIONS,
    REVIEW_FILES_SYSTEM_BASE,
    REVIEW_DIFF_SYSTEM_BASE,
    REVIEW_FILES_SCHEMA,
    REVIEW_DIFF_SCHEMA,
    _FINDING_SCHEMA,
)
from pydantic import ValidationError
from server import (
    SecondOpinionInput,
    ParallelPlanInput,
    BrainstormInput,
    CollaborateInput,
    QuickReviewInput,
    EvaluateInput,
    RecapInput,
    ReviewDiffInput,
    StatusInput,
    codex_review,
    codex_review_diff,
    _run_codex_once,
    _run_codex,
    ERROR_PREFIX,
)


# =========================================================================
# _safe_claudex_path
# =========================================================================


class TestSafeClaudexPath:
    """Security-critical path validation."""

    def test_valid_filename(self, tmp_path):
        result = _safe_claudex_path(str(tmp_path), "sessions", "my-session.md")
        assert result is not None
        assert result.name == "my-session.md"
        assert ".claudex" in str(result)

    def test_path_traversal_rejected(self, tmp_path):
        result = _safe_claudex_path(str(tmp_path), "sessions", "../../etc/passwd")
        assert result is None

    def test_null_bytes_rejected(self, tmp_path):
        result = _safe_claudex_path(str(tmp_path), "sessions", "file\x00.md")
        assert result is None

    def test_dotfile_rejected(self, tmp_path):
        result = _safe_claudex_path(str(tmp_path), "sessions", ".hidden")
        assert result is None

    def test_symlink_at_target_rejected(self, tmp_path):
        # Create the directory structure
        sessions_dir = tmp_path / ".claudex" / "sessions"
        sessions_dir.mkdir(parents=True)
        # Create a symlink at the target location
        target = sessions_dir / "evil.md"
        target.symlink_to("/etc/passwd")
        result = _safe_claudex_path(str(tmp_path), "sessions", "evil.md")
        assert result is None

    def test_symlink_at_claudex_dir_rejected(self, tmp_path):
        # Create a symlink at .claudex/ itself
        claudex_link = tmp_path / ".claudex"
        claudex_link.symlink_to("/tmp")
        result = _safe_claudex_path(str(tmp_path), "sessions", "test.md")
        assert result is None

    def test_special_chars_sanitized(self, tmp_path):
        result = _safe_claudex_path(str(tmp_path), "sessions", "foo bar!.md")
        assert result is not None
        assert result.name == "foo_bar_.md"


# =========================================================================
# _normalize_file_list
# =========================================================================


class TestNormalizeFileList:
    """File path normalization and validation."""

    def test_valid_comma_separated(self, tmp_path):
        # Create some files
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        result = _normalize_file_list("a.py, b.py", str(tmp_path))
        assert result == ["a.py", "b.py"]

    def test_nonexistent_paths_dropped(self, tmp_path):
        (tmp_path / "real.py").write_text("x")
        result = _normalize_file_list("real.py, ghost.py", str(tmp_path))
        assert result == ["real.py"]

    def test_out_of_root_rejected(self, tmp_path):
        result = _normalize_file_list("../../etc/passwd", str(tmp_path))
        assert result == []

    def test_empty_string_returns_empty(self, tmp_path):
        assert _normalize_file_list("", str(tmp_path)) == []
        assert _normalize_file_list("   ", str(tmp_path)) == []

    def test_whitespace_padded_paths(self, tmp_path):
        (tmp_path / "file.py").write_text("x")
        result = _normalize_file_list("  file.py  ", str(tmp_path))
        assert result == ["file.py"]


# =========================================================================
# Session management
# =========================================================================


class TestSessionManagement:
    """Session document lifecycle tests."""

    def test_init_session_creates_file(self, tmp_path):
        session_path = tmp_path / "test-session.md"
        _init_session(session_path, "test-session")
        assert session_path.exists()
        content = session_path.read_text()
        assert "# Session: test-session" in content
        assert "<!-- claudex:rounds=0 -->" in content

    def test_append_increments_and_writes(self, tmp_path):
        session_path = tmp_path / "test.md"
        _init_session(session_path, "test")
        _append_to_session(session_path, 1, "CC found a bug", "Codex agrees, suggests fix")
        content = session_path.read_text()
        assert "<!-- claudex:rounds=1 -->" in content
        assert "## Round 1" in content
        assert "CC found a bug" in content
        assert "Codex agrees, suggests fix" in content

    def test_read_session_rounds_parses(self, tmp_path):
        session_path = tmp_path / "test.md"
        _init_session(session_path, "test")
        assert _read_session_rounds(session_path) == 0
        _append_to_session(session_path, 1, "analysis", "response")
        assert _read_session_rounds(session_path) == 1
        _append_to_session(session_path, 2, "analysis 2", "response 2")
        assert _read_session_rounds(session_path) == 2

    def test_read_session_rounds_missing_file(self, tmp_path):
        assert _read_session_rounds(tmp_path / "nonexistent.md") == 0

    def test_get_truncated_session_under_limit(self, tmp_path):
        session_path = tmp_path / "test.md"
        _init_session(session_path, "test")
        _append_to_session(session_path, 1, "short", "also short")
        result = _get_truncated_session(session_path)
        assert "## Round 1" in result
        assert "short" in result

    def test_get_truncated_session_drops_oldest(self, tmp_path):
        session_path = tmp_path / "test.md"
        _init_session(session_path, "test")
        # Write enough rounds with large content to exceed limit
        # Each round ≈ 2,200 bytes (1k CC + 1k Codex + metadata)
        big_text = "x" * 1_000
        for i in range(1, 5):
            _append_to_session(session_path, i, big_text, big_text)
        # 4 rounds ≈ 8,800 bytes. Limit to 5,000 — keeps newest, drops oldest.
        result = _get_truncated_session(session_path, max_bytes=5_000)
        # Oldest rounds should be truncated, newest should remain
        assert "[Earlier rounds truncated]" in result
        # Round 4 (newest) should be present
        assert "## Round 4" in result


# =========================================================================
# _build_collaborate_system
# =========================================================================


class TestBuildCollaborateSystem:
    """Dynamic persona system prompt generation."""

    @pytest.mark.parametrize("rt", list(RequestType))
    def test_all_request_types_return_persona(self, rt):
        result = _build_collaborate_system(rt)
        assert isinstance(result, str)
        assert len(result) > 100
        # Should contain the persona text for this request type
        persona_text = COLLAB_PERSONAS.get(rt, COLLAB_PERSONAS[RequestType.GENERAL])
        assert persona_text in result

    def test_unknown_falls_back_to_general(self):
        # Simulate a fallback by calling with GENERAL directly
        result = _build_collaborate_system(RequestType.GENERAL)
        assert "Collaborative Engineer" in result


# =========================================================================
# Pydantic models
# =========================================================================


class TestPydanticModels:
    """Input model validation."""

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            SecondOpinionInput(plan="x" * 20, unknown_field="bad")

    def test_required_fields_missing(self):
        with pytest.raises(ValidationError):
            ParallelPlanInput()  # missing required 'task'

    def test_min_length_enforcement(self):
        with pytest.raises(ValidationError):
            CollaborateInput(problem="short", cc_analysis="also short")
        # "short" is only 5 chars, min_length=10


# =========================================================================
# _auto_session_id
# =========================================================================


class TestAutoSessionId:
    """Auto-generated session ID slugification."""

    def test_slugifies_problem(self):
        result = _auto_session_id("Fix the database connection retry logic")
        # Should be lowercase slug with uuid suffix
        assert re.match(r'^fix-the-database-connection-retry-logic-[a-f0-9]{6}$', result)

    def test_empty_input_fallback(self):
        result = _auto_session_id("!!!")
        assert re.match(r'^session-[a-f0-9]{6}$', result)

    def test_long_input_truncated(self):
        long_problem = "a" * 200
        result = _auto_session_id(long_problem)
        # Slug part should be from first 50 chars only
        parts = result.rsplit("-", 1)
        slug = parts[0]
        assert len(slug) <= 50


# =========================================================================
# _check_codex_version (consume / display-once semantics)
# =========================================================================


def _reset_version_cache(warning: str = "", resolved: bool = True):
    """Reset _version_cache to a known state for testing."""
    _version_cache["warning"] = warning
    _version_cache["resolved"] = resolved
    _version_cache["consumed"] = False
    _version_cache["lock"] = None


FAKE_WARNING = (
    "\u26a0 Codex CLI v0.99.0 is outdated (latest: v1.0.0).\n"
    "  Run: npm i -g @openai/codex\n"
)


class TestCheckCodexVersion:
    """Version check consume semantics — warning shown once in tool output."""

    @pytest.mark.asyncio
    async def test_consume_returns_warning_once(self):
        """consume=True returns warning on first call, empty on second."""
        _reset_version_cache(warning=FAKE_WARNING)
        first = await _check_codex_version(consume=True)
        assert first == FAKE_WARNING
        second = await _check_codex_version(consume=True)
        assert second == ""

    @pytest.mark.asyncio
    async def test_no_consume_always_returns_warning(self):
        """consume=False (default) always returns the cached warning."""
        _reset_version_cache(warning=FAKE_WARNING)
        first = await _check_codex_version(consume=False)
        assert first == FAKE_WARNING
        second = await _check_codex_version(consume=False)
        assert second == FAKE_WARNING

    @pytest.mark.asyncio
    async def test_status_sees_warning_after_consume(self):
        """codex_status (consume=False) still sees warning after _run_codex consumed it."""
        _reset_version_cache(warning=FAKE_WARNING)
        # _run_codex path consumes
        consumed = await _check_codex_version(consume=True)
        assert consumed == FAKE_WARNING
        # codex_status path — should still see it
        status = await _check_codex_version(consume=False)
        assert status == FAKE_WARNING

    @pytest.mark.asyncio
    async def test_no_warning_returns_empty(self):
        """When CLI is up to date, both paths return empty."""
        _reset_version_cache(warning="")
        assert await _check_codex_version(consume=True) == ""
        assert await _check_codex_version(consume=False) == ""

    @pytest.mark.asyncio
    async def test_unresolved_retries_on_failure(self, monkeypatch):
        """When the check fails (e.g. timeout), resolved stays False so next call retries."""
        import server as server_mod
        _reset_version_cache(warning="", resolved=False)

        def _fake_find_codex_bin():
            raise FileNotFoundError("no codex")

        monkeypatch.setattr(server_mod, "_find_codex_bin", _fake_find_codex_bin)
        result = await _check_codex_version(consume=False)
        assert result == ""
        # Failed check should NOT set resolved — allows retry
        assert _version_cache["resolved"] is False


# =========================================================================
# Per-tool timeout
# =========================================================================


class TestPerToolTimeout:
    """Validate timeout_seconds field on input models."""

    def test_none_is_valid(self):
        """Default None means use per-tool default."""
        inp = SecondOpinionInput(plan="x" * 20, timeout_seconds=None)
        assert inp.timeout_seconds is None

    def test_valid_timeout(self):
        inp = SecondOpinionInput(plan="x" * 20, timeout_seconds=1200)
        assert inp.timeout_seconds == 1200

    def test_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            SecondOpinionInput(plan="x" * 20, timeout_seconds=300)

    def test_above_maximum_rejected(self):
        with pytest.raises(ValidationError):
            SecondOpinionInput(plan="x" * 20, timeout_seconds=2000)

    def test_exec_timeout_constant(self):
        """EXEC_TIMEOUT_SECONDS should be set to a reasonable value."""
        assert EXEC_TIMEOUT_SECONDS == 1200


# =========================================================================
# Model override
# =========================================================================


class TestModelOverride:
    """Validate model field on input models."""

    def test_none_is_default(self):
        inp = SecondOpinionInput(plan="x" * 20, model=None)
        assert inp.model is None

    def test_custom_model(self):
        inp = SecondOpinionInput(plan="x" * 20, model="gpt-5-codex-mini")
        assert inp.model == "gpt-5-codex-mini"

    def test_all_models_have_field(self):
        """Every input model that calls Codex should accept model override."""
        for cls in [SecondOpinionInput, ParallelPlanInput, BrainstormInput,
                     CollaborateInput, QuickReviewInput, EvaluateInput,
                     RecapInput, ReviewDiffInput]:
            assert "model" in cls.model_fields, f"{cls.__name__} missing model field"


# =========================================================================
# Reasoning summary
# =========================================================================


class TestReasoningSummary:
    """Validate reasoning_summary field."""

    def test_none_is_default(self):
        inp = SecondOpinionInput(plan="x" * 20, reasoning_summary=None)
        assert inp.reasoning_summary is None

    def test_custom_summary(self):
        inp = SecondOpinionInput(plan="x" * 20, reasoning_summary="concise")
        assert inp.reasoning_summary == "concise"

    def test_default_constant(self):
        assert DEFAULT_REASONING_SUMMARY == "detailed"


# =========================================================================
# Effort downgrade — REMOVED in v1.8.0
# =========================================================================

class TestNoDowngradeRetry:
    """v1.8.0: the automatic effort-downgrade retry was removed — a timeout is
    a single honest error, never a hidden second quota message."""

    def test_downgrade_constant_removed(self):
        import server as srv
        assert not hasattr(srv, "EFFORT_DOWNGRADE")

    @pytest.mark.asyncio
    async def test_timeout_returns_error_without_retry(self, tmp_path):
        import server as srv
        calls = []

        async def _once(prompt, **kw):
            calls.append(kw.get("reasoning_effort"))
            return "Error: Codex timed out after 1200s. Try: (1) focus_files"

        with patch.object(srv, "_run_codex_once", new=_once):
            result = await srv._run_codex("p", project_dir=str(tmp_path),
                                          reasoning_effort="xhigh", tool_name="t")
        assert result.startswith("Error:")
        assert calls == ["xhigh"]  # exactly one attempt, at the requested effort


# =========================================================================



# =========================================================================
# Metrics
# =========================================================================


class TestMetrics:
    """In-memory metrics tracking."""

    def setup_method(self):
        """Clear metrics before each test."""
        _metrics.clear()

    def test_record_success(self):
        _record_metric("codex_plan", success=True, elapsed=10.5)
        assert _metrics["codex_plan"]["calls"] == 1
        assert _metrics["codex_plan"]["successes"] == 1
        assert _metrics["codex_plan"]["total_elapsed"] == 10.5

    def test_record_timeout(self):
        _record_metric("codex_plan", success=False, elapsed=600.0, timed_out=True)
        assert _metrics["codex_plan"]["timeouts"] == 1
        assert _metrics["codex_plan"]["successes"] == 0

    def test_record_error(self):
        _record_metric("codex_plan", success=False, elapsed=5.0)
        assert _metrics["codex_plan"]["errors"] == 1

    def test_accumulation(self):
        _record_metric("codex_plan", success=True, elapsed=10.0)
        _record_metric("codex_plan", success=True, elapsed=20.0)
        _record_metric("codex_plan", success=False, elapsed=5.0, timed_out=True)
        assert _metrics["codex_plan"]["calls"] == 3
        assert _metrics["codex_plan"]["successes"] == 2
        assert _metrics["codex_plan"]["timeouts"] == 1
        assert _metrics["codex_plan"]["total_elapsed"] == 35.0

    def test_empty_tool_name_skipped(self):
        _record_metric("", success=True, elapsed=1.0)
        assert "" not in _metrics

    def test_summary_formatting(self):
        _record_metric("codex_plan", success=True, elapsed=10.0)
        summary = _get_metrics_summary()
        assert "codex_plan" in summary
        assert "Calls" in summary

    def test_summary_empty(self):
        summary = _get_metrics_summary()
        assert "No tool invocations" in summary


# =========================================================================
# Chain session ID
# =========================================================================


class TestChainSessionId:
    """Session ID chaining for auto-rollover."""

    def test_first_chain(self):
        assert _chain_session_id("my-session") == "my-session-p2"

    def test_second_chain(self):
        assert _chain_session_id("my-session-p2") == "my-session-p3"

    def test_third_chain(self):
        assert _chain_session_id("my-session-p3") == "my-session-p4"

    def test_numeric_suffix_not_confused(self):
        """Session ID ending in a number shouldn't be confused with -pN."""
        assert _chain_session_id("debug-issue-42") == "debug-issue-42-p2"

    def test_hyphenated_name(self):
        assert _chain_session_id("fix-race-condition") == "fix-race-condition-p2"


# =========================================================================
# ReviewDiffInput
# =========================================================================


class TestReviewDiffInput:
    """Pydantic validation for the new codex_review_diff tool."""

    def test_minimal_valid(self):
        inp = ReviewDiffInput()
        assert inp.staged is False
        assert inp.focus is None

    def test_staged_flag(self):
        inp = ReviewDiffInput(staged=True)
        assert inp.staged is True

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ReviewDiffInput(unknown_field="bad")

    def test_all_v14_fields_present(self):
        """ReviewDiffInput should have all v1.4 shared fields."""
        fields = ReviewDiffInput.model_fields
        assert "model" in fields
        assert "timeout_seconds" in fields
        assert "reasoning_summary" in fields

    def test_full_construction(self):
        inp = ReviewDiffInput(
            focus="security",
            staged=True,
            context="Pre-commit review",
            user_prompt="Review my changes",
            model="gpt-5-codex-mini",
            timeout_seconds=1200,
            reasoning_summary="concise",
        )
        assert inp.focus == "security"
        assert inp.model == "gpt-5-codex-mini"


# =========================================================================
# Backward compatibility
# =========================================================================


class TestBackwardCompatibility:
    """Existing calls without new v1.4 optional fields still work."""

    def test_second_opinion_no_new_fields(self):
        inp = SecondOpinionInput(plan="x" * 20)
        assert inp.model is None
        assert inp.timeout_seconds is None
        assert inp.reasoning_summary is None

    def test_parallel_plan_no_new_fields(self):
        inp = ParallelPlanInput(task="x" * 20)
        assert inp.model is None
        assert inp.timeout_seconds is None

    def test_collaborate_no_new_fields(self):
        inp = CollaborateInput(problem="x" * 20, cc_analysis="x" * 20)
        assert inp.model is None

    def test_quick_review_no_new_fields(self):
        inp = QuickReviewInput(files="test.py")
        assert inp.model is None

    def test_evaluate_no_new_fields(self):
        inp = EvaluateInput(options="x" * 20)
        assert inp.model is None

    def test_recap_no_new_fields(self):
        inp = RecapInput(session_id="test")
        assert inp.model is None

    def test_review_diff_no_new_fields(self):
        inp = ReviewDiffInput()
        assert inp.model is None


# =========================================================================
# Review schemas
# =========================================================================


class TestReviewSchemas:
    """Validate structured output schema definitions."""

    def test_review_files_schema_required_fields(self):
        required = REVIEW_FILES_SCHEMA["required"]
        assert "findings" in required
        assert "file_summaries" in required
        assert "overall_assessment" in required
        assert "overall_confidence_score" in required

    def test_review_diff_schema_required_fields(self):
        required = REVIEW_DIFF_SCHEMA["required"]
        assert "findings" in required
        assert "overview" in required
        assert "verdict" in required
        assert "overall_explanation" in required
        assert "overall_confidence_score" in required

    def test_additional_properties_false_recursive(self):
        """All object nodes must have additionalProperties: false."""
        def check_no_additional(schema, path="root"):
            if schema.get("type") == "object":
                assert schema.get("additionalProperties") is False, (
                    f"Missing additionalProperties:false at {path}"
                )
                for key, prop in schema.get("properties", {}).items():
                    check_no_additional(prop, f"{path}.{key}")
            elif schema.get("type") == "array":
                items = schema.get("items", {})
                check_no_additional(items, f"{path}[]")
            # Traverse anyOf branches (used for nullable object types)
            for variant in schema.get("anyOf", []):
                check_no_additional(variant, f"{path}|anyOf")

        check_no_additional(REVIEW_FILES_SCHEMA, "REVIEW_FILES_SCHEMA")
        check_no_additional(REVIEW_DIFF_SCHEMA, "REVIEW_DIFF_SCHEMA")

    def test_schemas_serialize_to_valid_json(self):
        """Schemas must be JSON-serializable (for temp file writing)."""
        files_json = json.dumps(REVIEW_FILES_SCHEMA)
        diff_json = json.dumps(REVIEW_DIFF_SCHEMA)
        assert json.loads(files_json) == REVIEW_FILES_SCHEMA
        assert json.loads(diff_json) == REVIEW_DIFF_SCHEMA


# =========================================================================
# Review formatters
# =========================================================================


class TestReviewFormatters:
    """Structured JSON → markdown formatting."""

    SAMPLE_FINDING = {
        "title": "Unchecked null return",
        "body": "get_user() can return None but line 42 dereferences without check.",
        "severity": "critical",
        "priority": 0,
        "confidence_score": 0.95,
        "category": "bug",
        "code_location": {
            "file_path": "src/auth.py",
            "line_range": {"start": 42, "end": 42},
        },
        "suggestion": "if user := get_user(): ...",
    }

    SAMPLE_FINDING_NO_SUGGESTION = {
        "title": "Good error handling",
        "body": "Error paths are well covered.",
        "severity": "positive",
        "priority": 3,
        "confidence_score": 0.9,
        "category": "other",
        "code_location": {"file_path": "src/utils.py"},
        "suggestion": None,
    }

    SAMPLE_FINDING_RANGE = {
        "title": "Performance issue",
        "body": "N+1 query in loop.",
        "severity": "warning",
        "priority": 1,
        "confidence_score": 0.8,
        "category": "performance",
        "code_location": {
            "file_path": "src/db.py",
            "line_range": {"start": 10, "end": 25},
        },
        "suggestion": "Use bulk query instead.",
    }

    def test_format_finding_badge_and_location(self):
        result = _format_finding(self.SAMPLE_FINDING, 1)
        assert "[CRITICAL]" in result
        assert "Unchecked null return" in result
        assert "`src/auth.py:42`" in result
        assert "confidence: 95%" in result
        assert "**bug**" in result

    def test_format_finding_with_suggestion(self):
        result = _format_finding(self.SAMPLE_FINDING, 1)
        assert "**Suggested fix:**" in result
        assert "if user := get_user():" in result

    def test_format_finding_without_suggestion(self):
        result = _format_finding(self.SAMPLE_FINDING_NO_SUGGESTION, 1)
        assert "**Suggested fix:**" not in result
        assert "[POSITIVE]" in result

    def test_format_finding_line_range(self):
        result = _format_finding(self.SAMPLE_FINDING_RANGE, 1)
        assert "`src/db.py:10-25`" in result

    def test_format_finding_single_line(self):
        result = _format_finding(self.SAMPLE_FINDING, 1)
        assert "`src/auth.py:42`" in result

    def test_format_finding_no_line_range(self):
        result = _format_finding(self.SAMPLE_FINDING_NO_SUGGESTION, 1)
        assert "`src/utils.py`" in result

    def test_format_review_files_json(self):
        data = {
            "findings": [self.SAMPLE_FINDING, self.SAMPLE_FINDING_NO_SUGGESTION],
            "file_summaries": [
                {"file_path": "src/auth.py", "summary": "Auth module", "quality_assessment": "Needs work"},
            ],
            "overall_assessment": "Generally okay with one critical bug.",
            "overall_confidence_score": 0.85,
        }
        result = _format_review_files_json(data)
        assert "## File Summaries" in result
        assert "## Findings" in result
        assert "## Overall Assessment" in result
        assert "confidence: 85%" in result

    def test_format_review_diff_json_verdict_labels(self):
        for verdict, label in [("ship", "Ship It"), ("fix_first", "Fix First"), ("needs_discussion", "Needs Discussion")]:
            data = {
                "findings": [],
                "overview": "Minor changes.",
                "verdict": verdict,
                "overall_explanation": "Looks good.",
                "overall_confidence_score": 0.9,
            }
            result = _format_review_diff_json(data)
            assert f"## Verdict: {label}" in result

    def test_format_empty_findings(self):
        data = {
            "findings": [],
            "file_summaries": [],
            "overall_assessment": "Clean.",
            "overall_confidence_score": 1.0,
        }
        result = _format_review_files_json(data)
        assert "No issues found" in result

    def test_severity_sorting(self):
        """Findings should be sorted critical > warning > suggestion > positive."""
        findings = [
            {**self.SAMPLE_FINDING_NO_SUGGESTION, "severity": "positive", "title": "Positive"},
            {**self.SAMPLE_FINDING, "severity": "critical", "title": "Critical"},
            {**self.SAMPLE_FINDING_RANGE, "severity": "suggestion", "title": "Suggestion"},
            {**self.SAMPLE_FINDING_RANGE, "severity": "warning", "title": "Warning"},
        ]
        data = {
            "findings": findings,
            "file_summaries": [],
            "overall_assessment": "Mixed.",
            "overall_confidence_score": 0.7,
        }
        result = _format_review_files_json(data)
        crit_pos = result.index("[CRITICAL]")
        warn_pos = result.index("[WARNING]")
        sugg_pos = result.index("[SUGGESTION]")
        pos_pos = result.index("[POSITIVE]")
        assert crit_pos < warn_pos < sugg_pos < pos_pos


# =========================================================================
# Structured output field on input models
# =========================================================================


class TestStructuredOutputField:
    """structured_output field on review input models."""

    def test_default_true_quick_review(self):
        inp = QuickReviewInput(files="test.py")
        assert inp.structured_output is True

    def test_default_true_review_diff(self):
        inp = ReviewDiffInput()
        assert inp.structured_output is True

    def test_explicit_false_quick_review(self):
        inp = QuickReviewInput(files="test.py", structured_output=False)
        assert inp.structured_output is False

    def test_explicit_false_review_diff(self):
        inp = ReviewDiffInput(structured_output=False)
        assert inp.structured_output is False

    def test_backward_compat_no_field(self):
        """Old calls without structured_output should default True."""
        inp_files = QuickReviewInput(files="test.py")
        inp_diff = ReviewDiffInput()
        assert inp_files.structured_output is True
        assert inp_diff.structured_output is True


# =========================================================================
# _build_review_system
# =========================================================================


class TestBuildReviewSystem:
    """Toggle-based review system prompt builder."""

    def test_unstructured_includes_artifact_instructions(self):
        result = _build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=False)
        assert "claudex-artifact" in result
        assert "---FINAL-ANSWER---" in result

    def test_structured_includes_json_instructions(self):
        result = _build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=True)
        assert "Structured Output Instructions" in result
        assert "code_location" in result

    def test_structured_excludes_artifact_instructions(self):
        result = _build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=True)
        assert "claudex-artifact" not in result

    def test_base_prompt_preserved(self):
        for structured in [True, False]:
            result = _build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=structured)
            assert "Senior Code Reviewer" in result
            result_diff = _build_review_system(REVIEW_DIFF_SYSTEM_BASE, structured=structured)
            assert "Diff Reviewer" in result_diff


# =========================================================================
# Structured output integration tests (mock-based)
# =========================================================================


# Sample valid JSON that matches REVIEW_FILES_SCHEMA
SAMPLE_REVIEW_FILES_JSON = json.dumps({
    "findings": [
        {
            "title": "Missing null check",
            "body": "get_user() can return None.",
            "severity": "critical",
            "priority": 0,
            "confidence_score": 0.95,
            "category": "bug",
            "code_location": {"file_path": "src/auth.py", "line_range": {"start": 42, "end": 42}},
            "suggestion": "if user := get_user(): ...",
        }
    ],
    "file_summaries": [
        {"file_path": "src/auth.py", "summary": "Auth module", "quality_assessment": "Needs work"}
    ],
    "overall_assessment": "One critical bug found.",
    "overall_confidence_score": 0.9,
})

SAMPLE_REVIEW_DIFF_JSON = json.dumps({
    "findings": [
        {
            "title": "Race condition in lock",
            "body": "Lock acquire without timeout.",
            "severity": "warning",
            "priority": 1,
            "confidence_score": 0.8,
            "category": "bug",
            "code_location": {"file_path": "src/lock.py", "line_range": {"start": 10, "end": 15}},
            "suggestion": None,
        }
    ],
    "overview": "Adds locking mechanism.",
    "verdict": "fix_first",
    "overall_explanation": "Fix the race condition before shipping.",
    "overall_confidence_score": 0.85,
})


class TestStructuredOutputIntegration:
    """Integration tests for structured JSON output path in review tools."""

    @pytest.mark.asyncio
    async def test_review_files_structured_happy_path(self, tmp_path):
        """Valid JSON from Codex → formatted markdown with details block."""
        (tmp_path / "test.py").write_text("x = 1")

        with patch("server._run_codex", new_callable=AsyncMock) as mock_run, \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):
            mock_run.return_value = SAMPLE_REVIEW_FILES_JSON

            params = QuickReviewInput(files="test.py", project_dir=str(tmp_path))
            result = await codex_review(params)

        assert "## File Summaries" in result
        assert "## Findings" in result
        assert "[CRITICAL]" in result
        assert "Missing null check" in result
        assert "`src/auth.py:42`" in result
        assert "<details>" in result
        assert "Raw JSON" in result
        assert "_Codex:" in result

    @pytest.mark.asyncio
    async def test_review_files_structured_malformed_json_fallback(self, tmp_path):
        """Malformed JSON triggers text-mode fallback with user notification."""
        (tmp_path / "test.py").write_text("x = 1")

        call_count = 0
        async def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("output_schema") is not None:
                return "NOT VALID JSON {{{broken"
            return "## Text mode review\nLooks good."

        with patch("server._run_codex", side_effect=mock_run_side_effect) as mock_run, \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):

            params = QuickReviewInput(files="test.py", project_dir=str(tmp_path))
            result = await codex_review(params)

        assert call_count == 2  # First structured, then text fallback
        assert "Structured output failed" in result
        assert "2 Codex messages" in result
        assert "Text mode review" in result

    @pytest.mark.asyncio
    async def test_review_files_cli_error_fallback(self, tmp_path):
        """CLI error mentioning output-schema triggers text-mode fallback."""
        (tmp_path / "test.py").write_text("x = 1")

        call_count = 0
        async def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("output_schema") is not None:
                return f"{ERROR_PREFIX}Codex exited with code 1.\nStderr: error: unknown option '--output-schema'"
            return "## Text fallback\nAll good."

        with patch("server._run_codex", side_effect=mock_run_side_effect) as mock_run, \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):

            params = QuickReviewInput(files="test.py", project_dir=str(tmp_path))
            result = await codex_review(params)

        assert call_count == 2
        assert "Text fallback" in result
        # Should NOT show the error to user — auto-recovered
        assert "unknown option" not in result

    @pytest.mark.asyncio
    async def test_review_files_unstructured_mode(self, tmp_path):
        """structured_output=False uses legacy path — no schema, no JSON parsing."""
        (tmp_path / "test.py").write_text("x = 1")

        with patch("server._run_codex", new_callable=AsyncMock) as mock_run, \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):
            mock_run.return_value = "## Legacy Review\nAll fine."

            params = QuickReviewInput(
                files="test.py", project_dir=str(tmp_path), structured_output=False
            )
            result = await codex_review(params)

        # Should pass output_schema=None
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("output_schema") is None
        assert "Legacy Review" in result

    @pytest.mark.asyncio
    async def test_review_diff_structured_happy_path(self, tmp_path):
        """Valid JSON from Codex → formatted diff review with verdict."""

        with patch("server._run_codex", new_callable=AsyncMock) as mock_run, \
             patch("server._get_git_diff", new_callable=AsyncMock, return_value="diff --git a/x.py\n+new line"), \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):
            mock_run.return_value = SAMPLE_REVIEW_DIFF_JSON

            params = ReviewDiffInput(project_dir=str(tmp_path))
            result = await codex_review_diff(params)

        assert "## Verdict: Fix First" in result
        assert "## Findings" in result
        assert "[WARNING]" in result
        assert "Race condition in lock" in result
        assert "<details>" in result

    @pytest.mark.asyncio
    async def test_review_diff_structured_malformed_fallback(self, tmp_path):
        """Malformed JSON in diff review triggers text-mode fallback."""
        call_count = 0
        async def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("output_schema") is not None:
                return "[1, 2, 3]"  # Valid JSON but wrong type (array, not object)
            return "## Text diff review\nShip it."

        with patch("server._run_codex", side_effect=mock_run_side_effect) as mock_run, \
             patch("server._get_git_diff", new_callable=AsyncMock, return_value="diff --git a/x.py"), \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):

            params = ReviewDiffInput(project_dir=str(tmp_path))
            result = await codex_review_diff(params)

        assert call_count == 2
        assert "Structured output failed" in result
        assert "Text diff review" in result

    @pytest.mark.asyncio
    async def test_review_diff_cli_error_fallback(self, tmp_path):
        """CLI error mentioning output-schema in diff review triggers fallback."""
        call_count = 0
        async def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("output_schema") is not None:
                return f"{ERROR_PREFIX}Codex exited with code 2.\nStderr: Unknown flag: --output-schema"
            return "## Recovered review\nLGTM."

        with patch("server._run_codex", side_effect=mock_run_side_effect) as mock_run, \
             patch("server._get_git_diff", new_callable=AsyncMock, return_value="diff --git a/x.py"), \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):

            params = ReviewDiffInput(project_dir=str(tmp_path))
            result = await codex_review_diff(params)

        assert call_count == 2
        assert "Recovered review" in result

    @pytest.mark.asyncio
    async def test_review_files_api_schema_validation_error_fallback(self, tmp_path):
        """API schema validation error (invalid_json_schema) triggers text-mode fallback."""
        (tmp_path / "test.py").write_text("x = 1")

        call_count = 0
        async def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("output_schema") is not None:
                return (
                    f"{ERROR_PREFIX}Codex exited with code 1.\nStderr: ERROR: "
                    '{"error": {"message": "Invalid schema for response_format '
                    "'codex_output_schema'\", \"type\": \"invalid_request_error\", "
                    '"code": "invalid_json_schema"}}'
                )
            return "## Recovered from schema error\nAll good."

        with patch("server._run_codex", side_effect=mock_run_side_effect) as mock_run, \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):

            params = QuickReviewInput(files="test.py", project_dir=str(tmp_path))
            result = await codex_review(params)

        assert call_count == 2
        assert "Recovered from schema error" in result

    @pytest.mark.asyncio
    async def test_review_diff_api_schema_validation_error_fallback(self, tmp_path):
        """API response_format error in diff review triggers text-mode fallback."""
        call_count = 0
        async def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("output_schema") is not None:
                return (
                    f"{ERROR_PREFIX}Codex exited with code 1.\nStderr: ERROR: "
                    '{"error": {"message": "Invalid schema for response_format", '
                    '"type": "invalid_request_error"}}'
                )
            return "## Recovered diff review\nShip it."

        with patch("server._run_codex", side_effect=mock_run_side_effect) as mock_run, \
             patch("server._get_git_diff", new_callable=AsyncMock, return_value="diff --git a/x.py"), \
             patch("server._get_git_context", new_callable=AsyncMock, return_value=None):

            params = ReviewDiffInput(project_dir=str(tmp_path))
            result = await codex_review_diff(params)

        assert call_count == 2
        assert "Recovered diff review" in result


class TestTempFileLifecycle:
    """Verify schema temp file creation and cleanup in _run_codex_once."""

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_on_success(self, tmp_path):
        """Schema temp file should not persist after successful run."""
        import server as server_mod

        # Track temp files created
        created_temps = []
        original_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_temps.append(path)
            return fd, path

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b'{"test": true}', b''))
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("tempfile.mkstemp", side_effect=tracking_mkstemp), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b'{"test": true}', b'')):
            mock_proc.communicate = AsyncMock(return_value=(b'{"test": true}', b''))

            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
                output_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )

        # Temp file should have been created and then cleaned up
        assert len(created_temps) == 1
        assert not os.path.exists(created_temps[0]), "Schema temp file was not cleaned up"

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_on_timeout(self, tmp_path):
        """Schema temp file should be cleaned up even after timeout."""
        import server as server_mod

        created_temps = []
        original_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_temps.append(path)
            return fd, path

        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b'', b''))

        async def timeout_wait_for(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("tempfile.mkstemp", side_effect=tracking_mkstemp), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch("asyncio.wait_for", side_effect=timeout_wait_for):

            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
                output_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )

        assert "timed out" in result
        assert len(created_temps) == 1
        assert not os.path.exists(created_temps[0]), "Schema temp file was not cleaned up after timeout"

    @pytest.mark.asyncio
    async def test_no_temp_file_without_schema(self, tmp_path):
        """No temp file should be created when output_schema is None."""
        import server as server_mod

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b'plain text output', b''))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b'plain text output', b'')), \
             patch.object(server_mod, "_prepare_run_dir", return_value=tmp_path / "run-test"), \
             patch.object(server_mod, "_extract_and_save_artifacts", return_value=("plain text output", [])), \
             patch("tempfile.mkstemp") as mock_mkstemp:

            # Create the run dir to avoid OSError
            (tmp_path / "run-test").mkdir()

            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
                output_schema=None,
            )

        mock_mkstemp.assert_not_called()


class TestFormatterEdgeCases:
    """Edge cases that could crash formatters with malformed model output."""

    def test_finding_with_nan_like_confidence(self):
        """Confidence score that can't be converted should not crash."""
        finding = {
            "title": "Test",
            "body": "Test body",
            "severity": "warning",
            "priority": 1,
            "confidence_score": "not_a_number",
            "category": "bug",
            "code_location": {"file_path": "test.py"},
            "suggestion": None,
        }
        # Should not raise
        result = _format_finding(finding, 1)
        assert "confidence: 0%" in result

    def test_finding_with_missing_code_location(self):
        """Finding with empty code_location dict should not crash."""
        finding = {
            "title": "Test",
            "body": "Body",
            "severity": "suggestion",
            "priority": 2,
            "confidence_score": 0.5,
            "category": "other",
            "code_location": {},
            "suggestion": None,
        }
        result = _format_finding(finding, 1)
        assert "`unknown`" in result

    def test_finding_with_unknown_severity(self):
        """Unknown severity should fall through to uppercase."""
        finding = {
            "title": "Test",
            "body": "Body",
            "severity": "alien_level",
            "priority": 2,
            "confidence_score": 0.5,
            "category": "other",
            "code_location": {"file_path": "test.py"},
            "suggestion": None,
        }
        result = _format_finding(finding, 1)
        assert "[ALIEN_LEVEL]" in result

    def test_review_files_with_non_list_findings(self):
        """If findings is not a list, formatter should not crash."""
        data = {
            "findings": "not a list",
            "file_summaries": [],
            "overall_assessment": "Test",
            "overall_confidence_score": 0.5,
        }
        # This would be caught by the isinstance check + broad exception in the tool
        # but the formatter itself should handle it — it will iterate a string
        # which gives individual characters. This tests that nothing crashes fatally.
        try:
            _format_review_files_json(data)
        except (TypeError, AttributeError):
            pass  # Expected — this is caught by the tool function's exception handler

    def test_review_diff_with_unknown_verdict(self):
        """Unknown verdict value should display raw value."""
        data = {
            "findings": [],
            "overview": "Test",
            "verdict": "unknown_verdict",
            "overall_explanation": "Test",
            "overall_confidence_score": 0.5,
        }
        result = _format_review_diff_json(data)
        assert "## Verdict: unknown_verdict" in result

    def test_overall_confidence_non_numeric(self):
        """Non-numeric overall_confidence_score should not crash."""
        data = {
            "findings": [],
            "file_summaries": [],
            "overall_assessment": "Test",
            "overall_confidence_score": "high",  # String instead of float
        }
        result = _format_review_files_json(data)
        assert "## Overall Assessment" in result
        # Should show 0% (fallback)
        assert "confidence: 0%" in result


# =========================================================================
# Error handling fixes (issues #1-#5)
# =========================================================================


class TestErrorHandlingFixes:
    """Tests for the 5 error handling fixes in _run_codex_once and _run_codex."""

    @pytest.mark.asyncio
    async def test_stderr_fallback_has_error_prefix(self, tmp_path):
        """Fix #1: When returncode=0, stdout empty, stderr has content,
        result must start with ERROR_PREFIX."""
        import server as server_mod

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b'', b'some warning on stderr'))
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b'', b'some warning on stderr')):

            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
            )

        assert result.startswith(ERROR_PREFIX), f"Expected ERROR_PREFIX, got: {result[:50]}"
        assert "some warning on stderr" in result

    @pytest.mark.asyncio
    async def test_timeout_cleanup_kill_raises(self, tmp_path):
        """Fix #2: If proc.kill() raises ProcessLookupError, the function
        should still return a timeout error (not raise)."""
        import server as server_mod

        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock(side_effect=ProcessLookupError("No such process"))
        mock_proc.communicate = AsyncMock(return_value=(b'', b''))

        call_count = 0

        async def mock_wait_for(coro, *, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: the main communicate — raise timeout
                raise asyncio.TimeoutError()
            # Second call: the cleanup communicate — succeed
            return await coro

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch("asyncio.wait_for", side_effect=mock_wait_for):

            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
            )

        assert result.startswith(ERROR_PREFIX)
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_oserror_from_subprocess_returns_error(self, tmp_path):
        """Fix #3: OSError (e.g. PermissionError) from create_subprocess_exec
        should return an ERROR_PREFIX string, not raise."""
        import server as server_mod

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
                    side_effect=PermissionError("Permission denied")), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"):

            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
            )

        assert result.startswith(ERROR_PREFIX)
        assert "Permission denied" in result

    @pytest.mark.asyncio
    async def test_version_warning_not_prepended_on_error(self, tmp_path):
        """Fix #4: Version warning must not be prepended to error results,
        which would break the ERROR_PREFIX contract."""
        import server as server_mod

        error_result = f"{ERROR_PREFIX}Codex timed out after 60s."

        with patch.object(server_mod, "_run_codex_once", new_callable=AsyncMock, return_value=error_result), \
             patch.object(server_mod, "_check_codex_version", new_callable=AsyncMock,
                          return_value="⚠ Codex CLI v0.1 is outdated"):

            result = await _run_codex(
                "test prompt",
                project_dir=str(tmp_path),
                tool_name="test_tool",
            )

        assert result.startswith(ERROR_PREFIX), \
            f"Version warning masked ERROR_PREFIX: {result[:80]}"

    @pytest.mark.asyncio
    async def test_schema_write_type_error_handled(self, tmp_path):
        """Fix #5: TypeError from json.dump (e.g. non-serializable schema)
        should be caught, not raise."""
        import server as server_mod

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b'text mode output', b''))
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b'text mode output', b'')), \
             patch.object(server_mod, "_prepare_run_dir", return_value=tmp_path / "run-test"), \
             patch.object(server_mod, "_extract_and_save_artifacts", return_value=("text mode output", [])), \
             patch("json.dump", side_effect=TypeError("Object not serializable")):

            (tmp_path / "run-test").mkdir()

            # Should NOT raise — should fall back to text mode
            result = await _run_codex_once(
                "test prompt",
                project_dir=str(tmp_path),
                output_schema={"type": "object"},  # Schema itself is fine, json.dump is mocked to fail
            )

        assert not result.startswith(ERROR_PREFIX), \
            "Schema write failure should degrade to text mode, not return error"
        assert "text mode output" in result


# =========================================================================
# Async job layer (v1.7): codex_submit / codex_result / _run_job
# =========================================================================

import server as _server
from server import (
    codex_submit,
    codex_result,
    SubmitInput,
    JobResultInput,
    _jobs,
    _job_tasks,
    _write_job_file,
    MAX_ACTIVE_JOBS,
    JOB_ID_RE,
    ERROR_PREFIX,
)


@pytest_asyncio.fixture
async def clean_jobs():
    """Isolate job registry state per test (cancels leftover tasks, resets semaphore)."""
    _jobs.clear()
    _job_tasks.clear()
    _server._job_semaphore = None
    yield
    for task in list(_job_tasks.values()):
        task.cancel()
    await asyncio.gather(*_job_tasks.values(), return_exceptions=True)
    _jobs.clear()
    _job_tasks.clear()
    _server._job_semaphore = None


class TestAsyncJobLayer:
    @pytest.mark.asyncio
    async def test_submit_unknown_tool_fast_fail(self, clean_jobs):
        result = await codex_submit(SubmitInput(tool="nonsense", arguments={}))
        assert result.startswith(ERROR_PREFIX)
        assert "Unknown tool" in result
        assert not _jobs

    @pytest.mark.asyncio
    async def test_submit_invalid_arguments_fast_fail(self, clean_jobs):
        result = await codex_submit(SubmitInput(tool="review", arguments={"bogus": 1}))
        assert result.startswith(ERROR_PREFIX)
        assert "Invalid arguments" in result
        assert not _jobs

    @pytest.mark.asyncio
    async def test_submit_accepts_codex_prefix(self, clean_jobs, tmp_path):
        with patch.object(_server, "codex_critique", new=AsyncMock(return_value="ok")):
            result = await codex_submit(SubmitInput(
                tool="codex_critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
        assert "Job submitted: job-" in result

    @pytest.mark.asyncio
    async def test_full_lifecycle_completed(self, clean_jobs, tmp_path):
        with patch.object(_server, "codex_critique", new=AsyncMock(return_value="analysis text")):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            result = await codex_result(JobResultInput(job_id=job_id, wait_seconds=5))
        assert result == "analysis text"
        assert _jobs[job_id]["status"] == "completed"
        # Persisted to disk with terminal status
        job_file = tmp_path / ".claudex" / "jobs" / f"{job_id}.md"
        assert job_file.is_file()
        assert "Status: completed" in job_file.read_text()

    @pytest.mark.asyncio
    async def test_error_result_marks_failed(self, clean_jobs, tmp_path):
        with patch.object(_server, "codex_critique", new=AsyncMock(return_value=f"{ERROR_PREFIX}boom")):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            result = await codex_result(JobResultInput(job_id=job_id, wait_seconds=5))
        assert result.startswith(ERROR_PREFIX)
        assert _jobs[job_id]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_admission_cap(self, clean_jobs, tmp_path):
        never = asyncio.Event()

        async def _hang(params):
            await never.wait()
            return "unreachable"

        with patch.object(_server, "codex_critique", new=_hang):
            for _ in range(MAX_ACTIVE_JOBS):
                r = await codex_submit(SubmitInput(
                    tool="critique",
                    arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
                ))
                assert "Job submitted" in r
            overflow = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
        assert overflow.startswith(ERROR_PREFIX)
        assert "Too many active jobs" in overflow
        never.set()
        for task in list(_job_tasks.values()):
            task.cancel()
        await asyncio.gather(*_job_tasks.values(), return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cancellation_persists_interrupted(self, clean_jobs, tmp_path):
        started = asyncio.Event()

        async def _hang(params):
            started.set()
            await asyncio.sleep(3600)
            return "unreachable"

        with patch.object(_server, "codex_critique", new=_hang):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            await started.wait()
            task = _job_tasks[job_id]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert _jobs[job_id]["status"] == "interrupted"
        job_file = tmp_path / ".claudex" / "jobs" / f"{job_id}.md"
        assert "Status: interrupted" in job_file.read_text()

    @pytest.mark.asyncio
    async def test_result_rejects_bad_job_id_format(self, clean_jobs):
        result = await codex_result(JobResultInput(job_id="../../etc/passwd"))
        assert result.startswith(ERROR_PREFIX)
        assert "Invalid job_id format" in result

    @pytest.mark.asyncio
    async def test_disk_fallback_stale_running(self, clean_jobs, tmp_path):
        jobs_dir = tmp_path / ".claudex" / "jobs"
        jobs_dir.mkdir(parents=True)
        stale_id = "job-abcdefabcdef"
        (jobs_dir / f"{stale_id}.md").write_text(
            f"# Codex job {stale_id}\nTool: codex_review\nStatus: running\n"
        )
        result = await codex_result(JobResultInput(job_id=stale_id, project_dir=str(tmp_path)))
        assert result.startswith(ERROR_PREFIX)
        assert "did not finish" in result

    @pytest.mark.asyncio
    async def test_disk_fallback_completed_returns_body(self, clean_jobs, tmp_path):
        jobs_dir = tmp_path / ".claudex" / "jobs"
        jobs_dir.mkdir(parents=True)
        done_id = "job-123456abcdef"
        (jobs_dir / f"{done_id}.md").write_text(
            f"# Codex job {done_id}\nTool: codex_review\nStatus: completed\n"
            f"Finished: 2026-01-01T00:00:00+00:00\n\n---\n\nthe analysis body"
        )
        result = await codex_result(JobResultInput(job_id=done_id, project_dir=str(tmp_path)))
        assert result == "the analysis body"

    @pytest.mark.asyncio
    async def test_disk_fallback_failed_preserves_error_prefix(self, clean_jobs, tmp_path):
        jobs_dir = tmp_path / ".claudex" / "jobs"
        jobs_dir.mkdir(parents=True)
        fail_id = "job-fffff0000001"
        (jobs_dir / f"{fail_id}.md").write_text(
            f"# Codex job {fail_id}\nTool: codex_review\nStatus: failed\n"
            f"Finished: 2026-01-01T00:00:00+00:00\n\n---\n\n{ERROR_PREFIX}it broke"
        )
        result = await codex_result(JobResultInput(job_id=fail_id, project_dir=str(tmp_path)))
        assert result.startswith(ERROR_PREFIX)

    def test_job_id_regex(self):
        assert JOB_ID_RE.match("job-0123456789ab")
        assert not JOB_ID_RE.match("job-XYZ")
        assert not JOB_ID_RE.match("job-0123456789ab/../x")
        assert not JOB_ID_RE.match("notajob")

    @pytest.mark.asyncio
    async def test_disk_fallback_rejects_malformed_status(self, clean_jobs, tmp_path):
        jobs_dir = tmp_path / ".claudex" / "jobs"
        jobs_dir.mkdir(parents=True)
        bad_id = "job-badbadbadbad"
        (jobs_dir / f"{bad_id}.md").write_text(
            f"# Codex job {bad_id}\nTool: codex_review\nStatus: nonsense\n\n---\n\nnot a result"
        )
        result = await codex_result(JobResultInput(job_id=bad_id, project_dir=str(tmp_path)))
        assert result.startswith(ERROR_PREFIX)
        assert "unrecognized" in result

    @pytest.mark.asyncio
    async def test_job_file_permissions(self, clean_jobs, tmp_path):
        import stat as _stat
        with patch.object(_server, "codex_critique", new=AsyncMock(return_value="ok")):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            await codex_result(JobResultInput(job_id=job_id, wait_seconds=5))
        jobs_dir = tmp_path / ".claudex" / "jobs"
        assert (jobs_dir.stat().st_mode & 0o777) == 0o700
        assert ((jobs_dir / f"{job_id}.md").stat().st_mode & 0o777) == 0o600

    @pytest.mark.asyncio
    async def test_terminal_record_eviction(self, clean_jobs, tmp_path):
        from server import MAX_JOB_RECORDS, _evict_old_job_records
        import time as _time
        for n in range(MAX_JOB_RECORDS + 5):
            _jobs[f"job-{n:012x}"] = {
                "tool": "critique", "status": "completed", "submitted": _time.time() + n,
                "started_running": None, "finished": _time.time(), "project_dir": str(tmp_path),
                "result": "x",
            }
        _evict_old_job_records()
        assert len(_jobs) == MAX_JOB_RECORDS
        # Oldest evicted, newest retained
        assert f"job-{0:012x}" not in _jobs
        assert f"job-{MAX_JOB_RECORDS + 4:012x}" in _jobs

    @pytest.mark.asyncio
    async def test_cancellation_removes_task_from_registry(self, clean_jobs, tmp_path):
        started = asyncio.Event()

        async def _hang(params):
            started.set()
            await asyncio.sleep(3600)
            return "unreachable"

        with patch.object(_server, "codex_critique", new=_hang):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            await started.wait()
            task = _job_tasks[job_id]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert job_id not in _job_tasks  # finally-block cleanup ran
        assert _jobs[job_id]["status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_list_jobs(self, clean_jobs, tmp_path):
        with patch.object(_server, "codex_critique", new=AsyncMock(return_value="ok")):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"project_dir": str(tmp_path), "plan": "a plan long enough"},
            ))
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            await codex_result(JobResultInput(job_id=job_id, wait_seconds=5))
            listing = await codex_result(JobResultInput(job_id="list"))
        assert job_id in listing
        assert "completed" in listing

# =========================================================================
# v1.8.0 hardening
# =========================================================================

from server import (
    _validate_project_dir,
    _sanitized_codex_env,
    _get_session_lock,
    _check_daily_budget,
    _daily_job_count,
    ALLOWED_ROOTS_ENV,
    MAX_JOBS_PER_DAY_ENV,
)


class TestHardening:
    def test_allowlist_blocks_outside_root(self, tmp_path, monkeypatch):
        allowed = tmp_path / "work"; allowed.mkdir()
        outside = tmp_path / "elsewhere"; outside.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(allowed))
        assert _validate_project_dir(str(allowed / ".")) == str(allowed.resolve())
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            _validate_project_dir(str(outside))

    def test_allowlist_allows_subdirectory(self, tmp_path, monkeypatch):
        allowed = tmp_path / "work"; sub = allowed / "repo"; sub.mkdir(parents=True)
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(allowed))
        assert _validate_project_dir(str(sub)) == str(sub.resolve())

    def test_home_directory_rejected(self, monkeypatch):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        with pytest.raises(ValueError, match="entire home directory"):
            _validate_project_dir(str(Path.home()))

    def test_protected_location_rejected(self, monkeypatch):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        ssh = Path.home() / ".ssh"
        if ssh.is_dir():
            with pytest.raises(ValueError, match="protected location"):
                _validate_project_dir(str(ssh))

    def test_sanitized_env_drops_secrets(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
        monkeypatch.setenv("GH_TOKEN", "gh-secret")
        env = _sanitized_codex_env()
        assert "OPENAI_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "GH_TOKEN" not in env
        assert "PATH" in env and "HOME" in env

    def test_session_lock_alias_collision_shares_lock(self):
        assert _get_session_lock("foo bar") is _get_session_lock("foo@bar")
        assert _get_session_lock("foo_bar") is _get_session_lock("foo bar")
        assert _get_session_lock("other") is not _get_session_lock("foo bar")

    def test_daily_budget_enforced(self, monkeypatch):
        monkeypatch.setenv(MAX_JOBS_PER_DAY_ENV, "2")
        _daily_job_count["date"] = ""; _daily_job_count["count"] = 0
        assert _check_daily_budget() is None
        assert _check_daily_budget() is None
        third = _check_daily_budget()
        assert third is not None and third.startswith("Error:")
        _daily_job_count["date"] = ""; _daily_job_count["count"] = 0

    def test_daily_budget_unlimited_when_zero(self, monkeypatch):
        monkeypatch.setenv(MAX_JOBS_PER_DAY_ENV, "0")
        _daily_job_count["date"] = ""; _daily_job_count["count"] = 0
        for _ in range(10):
            assert _check_daily_budget() is None

    @pytest.mark.asyncio
    async def test_isolation_flags_in_command(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        import server as srv
        captured = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env")
            captured["start_new_session"] = kwargs.get("start_new_session")
            raise FileNotFoundError()  # short-circuit after capture

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert result.startswith("Error:")
        cmd = captured["cmd"]
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral"):
            assert flag in cmd, f"missing {flag}"
        assert "project_doc_max_bytes=0" in cmd
        assert captured["start_new_session"] is True
        assert captured["env"] is not None and "PATH" in captured["env"]

    @pytest.mark.asyncio
    async def test_diff_includes_deletions_and_attestation(self, tmp_path):
        import subprocess as sp
        from server import _get_git_diff
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                "--allow-empty", "-m", "init"], cwd=repo, check=True)
        keep = repo / "keep.py"; keep.write_text("x = 1\n")
        gone = repo / "gone.py"; gone.write_text("secret_check = True\n")
        sp.run(["git", "add", "-A"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add"],
               cwd=repo, check=True)
        gone.unlink()                      # deletion must appear
        (repo / "sneaky.py").write_text("backdoor = 1\n")  # untracked must be named
        diff = await _get_git_diff(str(repo), staged=False)
        assert diff is not None
        assert "REVIEWED-STATE: HEAD" in diff and "sha256:" in diff
        assert "gone.py" in diff           # deletion included
        assert "sneaky.py" in diff         # untracked named in header

# =========================================================================
# v1.8.0 gate-2 fixes
# =========================================================================

class TestGate2Fixes:
    @pytest.mark.asyncio
    async def test_oversized_diff_fails_closed(self, tmp_path):
        import subprocess as sp
        from server import _get_git_diff, DIFF_MAX_BYTES
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        big = repo / "big.txt"; big.write_text("line zero\n")
        sp.run(["git", "add", "-A"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
               cwd=repo, check=True)
        big.write_text("x" * (DIFF_MAX_BYTES + 10_000))
        result = await _get_git_diff(str(repo), staged=False)
        assert result is not None and result.startswith("Error:")
        assert "refusing a partial review" in result

    @pytest.mark.asyncio
    async def test_untracked_only_repo_not_silent(self, tmp_path):
        import subprocess as sp
        import server as srv
        from server import codex_review_diff, ReviewDiffInput
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                "--allow-empty", "-m", "i"], cwd=repo, check=True)
        (repo / "backdoor.py").write_text("evil = True\n")
        result = await codex_review_diff(ReviewDiffInput(project_dir=str(repo)))
        assert result.startswith("Error:")
        assert "UNTRACKED" in result and "backdoor.py" in result

    @pytest.mark.asyncio
    async def test_budget_counts_synchronous_calls(self, tmp_path, monkeypatch):
        import server as srv
        from server import _daily_job_count, MAX_JOBS_PER_DAY_ENV
        monkeypatch.setenv(MAX_JOBS_PER_DAY_ENV, "1")
        _daily_job_count["date"] = ""; _daily_job_count["count"] = 0
        with patch.object(srv, "_run_codex_once", new=AsyncMock(return_value="ok")):
            first = await srv._run_codex("p", project_dir=str(tmp_path), tool_name="t")
            second = await srv._run_codex("p", project_dir=str(tmp_path), tool_name="t")
        assert not first.startswith("Error:")
        assert second.startswith("Error:") and "budget" in second.lower()
        _daily_job_count["date"] = ""; _daily_job_count["count"] = 0

    def test_proxy_env_preserved(self, monkeypatch):
        from server import _sanitized_codex_env
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        monkeypatch.setenv("NO_PROXY", "localhost")
        env = _sanitized_codex_env()
        assert env.get("HTTPS_PROXY") == "http://proxy:8080"
        assert env.get("NO_PROXY") == "localhost"

    @pytest.mark.asyncio
    async def test_skill_isolation_flags_present(self, tmp_path, monkeypatch):
        import server as srv
        captured = {}
        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            raise FileNotFoundError()
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert "skills.include_instructions=false" in captured["cmd"]
        assert "skills.bundled.enabled=false" in captured["cmd"]

# =========================================================================
# Entry point for uv run --script
# =========================================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
