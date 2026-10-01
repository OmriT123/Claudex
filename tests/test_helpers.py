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
import errno
import json
import os
import shutil
import subprocess
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
    _prepare_run_dir,
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
    ReasoningEffort,
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


@pytest.fixture(autouse=True)
def _default_allowed_roots(monkeypatch, tmp_path):
    """Deny-by-default (v2.0): every test runs with tmp_path as its allowed root.

    Tests exercising the unconfigured/denied states override this inside the
    test body (later monkeypatching wins over the autouse default). The quota
    state dir is likewise isolated per-test so no test touches real app-data.
    """
    monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("CLAUDEX_STATE_DIR", str(tmp_path / ".claudex-state"))
    # v2.4: never read the developer's real roots config.
    import server as _srv
    monkeypatch.setattr(_srv, "_config_path", lambda: tmp_path / ".claudex-config" / "config.json")
    monkeypatch.delenv("CLAUDEX_PLUGIN_FOLDER", raising=False)
    monkeypatch.delenv("CLAUDEX_DISTRIBUTION", raising=False)
    # v2.2: the suite may itself run inside a Claude Code cloud session; the
    # cloud-default root must never leak into tests that expect deny-all.
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
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


class TestPrepareRunDir:
    def test_symlink_at_claudex_dir_rejected(self, tmp_path):
        # Same guard as _safe_claudex_path: a symlinked .claudex is refused
        claudex_link = tmp_path / ".claudex"
        claudex_link.symlink_to("/tmp")
        with pytest.raises(OSError, match="symlink"):
            _prepare_run_dir(str(tmp_path))

    def test_normal_run_dir_created(self, tmp_path):
        run_dir = _prepare_run_dir(str(tmp_path))
        assert run_dir.is_dir()
        assert run_dir.name.startswith("run-")
        assert run_dir.parent == tmp_path / ".claudex"


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
    async def test_submit_without_project_dir_stores_resolved_cwd(self, clean_jobs, monkeypatch):
        # Codex #1: omitting project_dir must store the RESOLVED cwd, not None
        # (None → _write_job_file does Path(None) → crash). Allow cwd as a root
        # so validation of the defaulted dir passes.
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, os.getcwd())
        with patch.object(_server, "codex_critique", new=AsyncMock(return_value="ok text")):
            sub = await codex_submit(SubmitInput(
                tool="critique",
                arguments={"plan": "a plan long enough"},  # no project_dir
            ))
            assert "Project: None" not in sub  # bug symptom: display showed None
            job_id = sub.split("Job submitted: ")[1].split("\n")[0]
            assert _jobs[job_id]["project_dir"] == os.path.realpath(os.getcwd())
            result = await codex_result(JobResultInput(job_id=job_id, wait_seconds=5))
        assert result == "ok text"  # lifecycle completed without a Path(None) crash

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
    _reserve_daily_run,
    _read_daily_run_count,
    _quota_db_path,
    _state_dir,
    ALLOWED_ROOTS_ENV,
    MAX_JOBS_PER_DAY_ENV,
    MAX_RUNS_PER_DAY_ENV,
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
        # State dir is tmp_path-scoped by the autouse fixture — hermetic per test.
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "2")
        assert _reserve_daily_run() is None
        assert _reserve_daily_run() is None
        third = _reserve_daily_run()
        assert third is not None and third.startswith("Error:")

    def test_daily_budget_unlimited_when_zero(self, monkeypatch):
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "0")
        for _ in range(10):
            assert _reserve_daily_run() is None

    @pytest.mark.asyncio
    async def test_isolation_flags_in_command(self, tmp_path, monkeypatch):
        # autouse fixture supplies tmp_path as the allowed root (deny-by-default
        # would otherwise reject before the spawn this test wants to capture)
        import server as srv
        captured = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env")
            captured["start_new_session"] = kwargs.get("start_new_session")
            raise FileNotFoundError()  # short-circuit after capture

        # without this the runner returns "Codex CLI not found" before spawning
        # on machines with no codex on PATH (CI, fresh cloud VMs)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert result.startswith("Error:")
        cmd = captured["cmd"]
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config"):
            assert flag in cmd, f"missing {flag}"
        assert "project_doc_max_bytes=0" in cmd
        # v2.1: native multi-agent delegation is disabled at the CLI boundary —
        # config key (model metadata cannot override it) AND both feature flags,
        # all in `-c` form so older CLIs never see an unknown argv flag.
        overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
        for key in ("agents.enabled=false", "features.multi_agent=false", "features.multi_agent_v2=false"):
            assert key in overrides, f"missing {key}"
        assert "--disable" not in cmd
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
    async def test_budget_reserves_at_subprocess_boundary(self, tmp_path, monkeypatch):
        # Reservation lives in _run_codex_once (the single real subprocess
        # boundary) — AFTER validation, so direct callers count and a rejected
        # dir never spends a reservation. Mock the spawn to fail cleanly after
        # the reservation runs.
        import server as srv
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "1")

        async def boom(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", boom)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        first = await srv._run_codex_once("p", project_dir=str(tmp_path))
        second = await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert "not found" in first.lower()          # reservation spent, spawn failed
        assert "cap reached" in second.lower()        # cap already exhausted

    @pytest.mark.asyncio
    async def test_rejected_dir_does_not_spend_reservation(self, tmp_path, monkeypatch):
        # A dir outside the allowed root is rejected BEFORE the reservation.
        import server as srv
        root = tmp_path / "allowed"; root.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(root))
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        out = await srv._run_codex_once("p", project_dir=str(tmp_path / "outside"))
        assert out.startswith("Error:")
        assert _read_daily_run_count() == 0  # no reservation burned on a rejected dir

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
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert "skills.include_instructions=false" in captured["cmd"]
        assert "skills.bundled.enabled=false" in captured["cmd"]

# =========================================================================
# v1.8.2: unexpanded ${user_config...} placeholder must not become a root
# =========================================================================

from server import _allowed_roots


class TestAllowedRootsPlaceholder:
    def test_literal_placeholder_env_yields_no_roots(self, monkeypatch):
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, "${user_config.allowed_roots}")
        assert _allowed_roots() == []

    def test_mixed_real_and_placeholder_keeps_real_root_only(self, tmp_path, monkeypatch):
        real = tmp_path / "work"; real.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, f"{real}{os.pathsep}${{user_config.allowed_roots}}")
        assert _allowed_roots() == [real.resolve()]

    def test_placeholder_env_denies_all(self, tmp_path, monkeypatch):
        # INVERTED (v2.0, M0-A1): the literal placeholder used to mean
        # "unrestricted"; deny-by-default now rejects every project directory
        # until real roots are configured.
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, "${user_config.allowed_roots}")
        project = tmp_path / "repo"; project.mkdir()
        # v2.4: the message names the source that yielded nothing.
        with pytest.raises(ValueError, match="yields no usable folder"):
            _validate_project_dir(str(project))

    def test_non_template_dollar_part_still_fails_closed(self, tmp_path, monkeypatch):
        # Narrow guard: a hand-written unexpanded var like ${HOME}/dev is a
        # misconfiguration, not the launcher bug — it must stay a (bogus) root
        # so confinement still denies loudly instead of silently opening up.
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, "${HOME}/dev")
        assert len(_allowed_roots()) == 1
        project = tmp_path / "repo"; project.mkdir()
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            _validate_project_dir(str(project))


class TestLauncherPlaceholder:
    def _launcher_script(self):
        import json as _json
        manifest = PROJECT_ROOT / "desktop-extension" / "manifest.json"
        return _json.loads(manifest.read_text())["server"]["mcp_config"]["args"][1]

    def _run_launcher(self, *args):
        import subprocess as sp
        probe = self._launcher_script().split("; export CLAUDEX_ALLOWED_ROOTS")[0] \
            + '; printf "%s" "$CLAUDEX_ALLOWED_ROOTS"'
        out = sp.run(["/bin/sh", "-c", probe, "claudex-launcher", *args],
                     capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout

    def test_literal_placeholder_neutralized(self):
        assert self._run_launcher("${user_config.allowed_roots}") == ""

    def test_real_roots_preserved(self):
        assert self._run_launcher("/tmp/a", "/tmp/b") == "/tmp/a:/tmp/b"

    def test_mixed_input_neutralized_whole(self):
        # Launcher layer is deliberately blunt: any placeholder empties the
        # whole value; the server layer keeps real parts if one leaks through.
        assert self._run_launcher("/tmp/a", "${user_config.allowed_roots}") == ""

    def test_launcher_script_parses(self):
        import subprocess as sp
        check = sp.run(["/bin/sh", "-n", "-c", self._launcher_script()],
                       capture_output=True, text=True)
        assert check.returncode == 0, check.stderr


# =========================================================================
# v2.0 (M0-A2): durable SQLite quota store
# =========================================================================

import sqlite3 as _sq
import threading as _threading


class TestQuotaStore:
    def test_reservations_persist_in_the_db_file(self, monkeypatch):
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        assert _reserve_daily_run() is None
        assert _reserve_daily_run() is None
        # The count lives in the file, not the process — a restart reads it back.
        conn = _sq.connect(_quota_db_path())
        row = conn.execute("SELECT attempts FROM quota_usage").fetchone()
        conn.close()
        assert row[0] == 2
        assert _read_daily_run_count() == 2

    def test_concurrent_reservations_never_exceed_cap(self, monkeypatch):
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        results = []

        def worker():
            results.append(_reserve_daily_run())

        threads = [_threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        granted = sum(1 for r in results if r is None)
        denied = sum(1 for r in results if r is not None)
        assert granted == 5, f"expected exactly cap grants, got {granted}"
        assert denied == 5

    def test_utc_rollover_resets(self, monkeypatch):
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "1")
        assert _reserve_daily_run() is None
        assert _reserve_daily_run() is not None
        # Simulate the day rolling over: age the stored row.
        conn = _sq.connect(_quota_db_path())
        conn.execute("UPDATE quota_usage SET day_utc = '2000-01-01'")
        conn.commit()
        conn.close()
        assert _reserve_daily_run() is None

    def test_corrupt_db_denies_with_repairable_error(self, monkeypatch):
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        p = _quota_db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"this is not a sqlite database")
        err = _reserve_daily_run()
        assert err is not None and err.startswith("Error:")
        assert str(p) in err  # names the file to repair
        assert MAX_RUNS_PER_DAY_ENV in err  # names the disable escape hatch

    def test_legacy_env_alias_still_works(self, monkeypatch):
        monkeypatch.delenv(MAX_RUNS_PER_DAY_ENV, raising=False)
        monkeypatch.setenv(MAX_JOBS_PER_DAY_ENV, "1")
        assert _reserve_daily_run() is None
        assert _reserve_daily_run() is not None

    def test_wording_is_executions_never_messages(self, monkeypatch):
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "1")
        _reserve_daily_run()
        err = _reserve_daily_run()
        assert "message" not in err.lower() and "credit" not in err.lower()
        assert "execution" in err.lower()

    def test_state_dir_is_outside_project_dirs_and_pure(self, tmp_path, monkeypatch):
        # CLAUDEX_STATE_DIR (autouse fixture) points under tmp_path/.claudex-state
        # — the resolver must honor it and never place state in a repo .claudex/.
        # It is now PURE (no mkdir side-effect) so it's safe in error handlers;
        # the directory materializes lazily on first _quota_conn use.
        d = _state_dir()
        assert d == (tmp_path / ".claudex-state")
        assert not d.exists()  # pure resolve — no mkdir
        _reserve_daily_run()   # first real use creates it
        assert d.is_dir()

    def test_deleting_corrupt_db_repairs_without_restart(self, monkeypatch):
        # Codex #7: after a DB is initialized, deleting a corrupted file and
        # letting it recreate must restore a usable schema (DDL every connect).
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        assert _reserve_daily_run() is None          # initializes DB + cache
        p = _quota_db_path()
        p.write_bytes(b"corrupt")                     # simulate corruption
        assert _reserve_daily_run() is not None       # denied (fail-closed)
        p.unlink()                                    # user follows the advice: delete
        # Cache still holds the path, but DDL runs on every connect → repaired.
        assert _reserve_daily_run() is None           # works again, no restart

    def test_state_dir_mkdir_failure_returns_repairable_error(self, tmp_path, monkeypatch):
        # Codex #3: if the state dir can't be created, the error handler must
        # NOT re-invoke the creating resolver and raise — it returns the
        # repairable error string.
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        # Point the state dir at a path whose parent is a FILE → mkdir fails.
        blocker = tmp_path / "blocker"; blocker.write_text("x")
        monkeypatch.setenv("CLAUDEX_STATE_DIR", str(blocker / "state"))
        srv_mod = sys.modules["server"]
        srv_mod._quota_db_initialized.clear()
        err = _reserve_daily_run()
        assert err is not None and err.startswith("Error:")  # no exception raised


# =========================================================================
# v2.0 (M0-A3): incremental streaming caps
# =========================================================================

from server import (
    _pump_capped,
    _read_stream_capped,
    _StreamCapExceeded,
    MAX_OUTPUT_BYTES,
    MAX_LINE_BYTES,
    STDERR_TAIL_BYTES,
)


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        return self._chunks.pop(0) if self._chunks else b""


class _FakeStdin:
    def write(self, b):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeProc:
    def __init__(self, out_chunks=(), err_chunks=(), rc=0):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream(out_chunks)
        self.stderr = _FakeStream(err_chunks)
        self.returncode = rc
        self.pid = 999_999_999  # getpgid fails -> _kill_tree falls back to kill()
        self.killed = False

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True

    async def communicate(self, input=None):
        return (b"", b"")


class TestStreamCaps:
    @pytest.mark.asyncio
    async def test_combined_byte_cap_breach_raises(self):
        # newline-terminated chunks keep the line cap out of the way,
        # isolating the combined-bytes cap (5MB > 4MB)
        proc = _FakeProc(out_chunks=[b"x" * 999_999 + b"\n"] * 5)
        with pytest.raises(_StreamCapExceeded, match="output exceeded"):
            await _pump_capped(proc, b"prompt")

    @pytest.mark.asyncio
    async def test_no_newline_flood_trips_line_cap(self):
        # 1.2MB with no newline: under the 4MB total cap, over the 1MB line cap
        proc = _FakeProc(out_chunks=[b"y" * 600_000, b"y" * 600_001])
        with pytest.raises(_StreamCapExceeded, match="single output line"):
            await _pump_capped(proc, b"prompt")

    @pytest.mark.asyncio
    async def test_oversize_line_completing_mid_chunk_trips_cap(self):
        # Codex #6: a >1MB line that ENDS inside a chunk (prior run + prefix
        # before the newline) must trip — not slip through because the chunk
        # also contains a trailing newline.
        proc = _FakeProc(out_chunks=[b"z" * 983_040, b"z" * 16_961 + b"\ntail"])
        with pytest.raises(_StreamCapExceeded, match="single output line"):
            await _pump_capped(proc, b"prompt")

    @pytest.mark.asyncio
    async def test_newlines_reset_the_line_run(self):
        chunks = [b"z" * 600_000 + b"\n", b"z" * 600_000 + b"\n"]
        out, _err = await _pump_capped(_FakeProc(out_chunks=chunks), b"p")
        assert len(out) == 2 * 600_001

    @pytest.mark.asyncio
    async def test_stderr_flood_does_not_trip_the_output_cap(self):
        # Codex/CC #4: trimmed stderr bulk must NOT count against the memory cap
        # — a small good stdout + huge stderr must still return the stdout.
        proc = _FakeProc(
            out_chunks=[b"GOOD RESULT\n"],
            err_chunks=[b"noise\n" * 100_000] * 8,  # ~4.8MB of stderr, all trimmed
        )
        out, err = await _pump_capped(proc, b"prompt")
        assert out == b"GOOD RESULT\n"
        assert len(err) <= 2 * STDERR_TAIL_BYTES

    @pytest.mark.asyncio
    async def test_stderr_keeps_only_the_tail(self):
        # Amortized trim: bounded at 2×STDERR_TAIL_BYTES, and always the TAIL.
        # Feed well past the trim threshold so at least one trim has fired.
        err_chunks = [b"A" * 20_000, b"B" * 20_000, b"C" * 20_000, b"D" * 20_000]
        _out, err = await _pump_capped(_FakeProc(err_chunks=err_chunks), b"p")
        assert len(err) <= 2 * STDERR_TAIL_BYTES
        assert err.endswith(b"D" * STDERR_TAIL_BYTES)  # tail, not head
        assert b"A" not in err  # oldest bytes trimmed away

    @pytest.mark.asyncio
    async def test_invalid_utf8_passes_through_bytes(self):
        out, _err = await _pump_capped(
            _FakeProc(out_chunks=[b"ok \xff\xfe bytes\n"]), b"p")
        assert out == b"ok \xff\xfe bytes\n"
        assert "ok" in out.decode(errors="replace")

    @pytest.mark.asyncio
    async def test_sink_memory_never_exceeds_cap(self):
        sink = bytearray()
        stream = _FakeStream([b"x" * 1_000_000] * 6)
        with pytest.raises(_StreamCapExceeded):
            await _read_stream_capped(
                stream, sink, {"total": 0}, tail_only=False)
        assert len(sink) <= MAX_OUTPUT_BYTES

    @pytest.mark.asyncio
    async def test_runner_returns_error_and_kills_on_breach(self, tmp_path, monkeypatch):
        import server as srv
        proc = _FakeProc(out_chunks=[b"x" * 1_000_000] * 5)

        async def fake_exec(*cmd, **kwargs):
            return proc

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        result = await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert result.startswith("Error:")
        assert "discarded" in result  # never a truncated success
        assert proc.killed is True


# =========================================================================
# v2.0 (M0-A4): complete subprocess env hygiene + ping split + npm removal
# =========================================================================


class TestEnvHygiene:
    def test_no_npm_spawn_in_server_source(self):
        # The npm-registry version lookup is gone for good — an undeclared
        # runtime outbound call has no place on enterprise machines (R4).
        src = (PROJECT_ROOT / "server" / "server.py").read_text()
        assert '"npm",' not in src

    @pytest.mark.asyncio
    async def test_version_check_sanitized_env_and_pinned_minimum(self, monkeypatch):
        import server as srv
        _reset_version_cache(warning="", resolved=False)
        srv._version_cache["last_failure"] = 0.0  # clear backoff from earlier tests
        spawns = []

        class _P:
            returncode = 0

            async def communicate(self, input=None):
                return (b"codex-cli 0.100.0", b"")

        async def fake_exec(*cmd, **kw):
            spawns.append((cmd, kw))
            return _P()

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        warn = await srv._check_codex_version()
        assert "older than the supported minimum" in warn
        assert len(spawns) == 1, "exactly one spawn — the npm lookup must be gone"
        cmd, kw = spawns[0]
        assert cmd[1] == "--version"
        assert kw.get("env") is not None and "PATH" in kw["env"]
        _reset_version_cache()

    @pytest.mark.asyncio
    async def test_version_check_quiet_at_minimum(self, monkeypatch):
        import server as srv
        _reset_version_cache(warning="", resolved=False)
        srv._version_cache["last_failure"] = 0.0  # clear backoff from earlier tests

        class _P:
            returncode = 0

            async def communicate(self, input=None):
                return (f"codex-cli {srv.MIN_CODEX_VERSION}".encode(), b"")

        async def fake_exec(*cmd, **kw):
            return _P()

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        assert await srv._check_codex_version() == ""
        _reset_version_cache()


class TestGitConfigExecutionBlocked:
    @pytest.mark.asyncio
    async def test_hostile_repo_config_does_not_execute(self, tmp_path, monkeypatch):
        # Security CRITICAL: repo-local diff.external / core.fsmonitor are
        # command-execution vectors that live in .git/config (not env), so
        # sanitizing env alone doesn't stop them. The safe -c flags +
        # --no-ext-diff/--no-textconv must neutralize them. Probe reaches the
        # real vulnerable branch (_get_git_diff + _get_git_context).
        import server as srv
        import subprocess
        marker_ext = tmp_path / "MARKER_EXT"
        marker_fsm = tmp_path / "MARKER_FSM"
        repo = tmp_path / "repo"; repo.mkdir()

        def g(*a):
            subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
        g("init", "-q"); g("config", "user.email", "x@x"); g("config", "user.name", "x")
        (repo / "a.txt").write_text("v1\n"); g("add", "."); g("commit", "-qm", "one")
        (repo / "a.txt").write_text("v2\n")
        g("config", "diff.external", f"touch {marker_ext};")
        g("config", "core.fsmonitor", f"touch {marker_fsm}; true")

        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(tmp_path))
        await srv._get_git_diff(str(repo), staged=False)
        await srv._get_git_context(str(repo))
        # These two ARE closed by --no-ext-diff/--no-textconv + -c core.fsmonitor=false.
        assert not marker_ext.exists(), "diff.external executed — sandbox escape"
        assert not marker_fsm.exists(), "core.fsmonitor executed — sandbox escape"
        # KNOWN RESIDUAL (documented, NOT fixed — descoped per README "Not a sandbox
        # against a repository you open" + v2.1 git-op-sandbox backlog): git has no
        # flag to disable attribute-driven clean/smudge filters, so a hostile
        # .gitattributes + filter.<d>.clean still executes on a worktree diff. This
        # is inherent git behavior; not asserted closed here on purpose.

    @pytest.mark.asyncio
    async def test_diff_still_works_on_a_benign_repo(self, tmp_path, monkeypatch):
        # The safe flags must not break legitimate diff gathering.
        import server as srv
        import subprocess
        repo = tmp_path / "repo2"; repo.mkdir()

        def g(*a):
            subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
        g("init", "-q"); g("config", "user.email", "x@x"); g("config", "user.name", "x")
        (repo / "f.txt").write_text("one\n"); g("add", "."); g("commit", "-qm", "c1")
        (repo / "f.txt").write_text("two\n")
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(tmp_path))
        diff = await srv._get_git_diff(str(repo), staged=False)
        assert diff and "f.txt" in diff and "-one" in diff and "+two" in diff


class TestGitEnvSanitized:
    @pytest.mark.asyncio
    async def test_git_spawns_use_sanitized_env(self, tmp_path, monkeypatch):
        # Codex/security #2: the git spawns must drop GIT_DIR/GIT_EXTERNAL_DIFF
        # etc. so an env var can't redirect the read outside the allowed cwd or
        # inject a command.
        import server as srv
        captured = {}

        async def fake_exec(*cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            raise FileNotFoundError()  # short-circuit after capture

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        await srv._git_cmd(str(tmp_path), "rev-parse", "HEAD")
        assert captured["env"] is not None, "git spawn must pass a sanitized env"
        assert "GIT_DIR" not in captured["env"]
        assert "GIT_EXTERNAL_DIFF" not in captured["env"]
        assert "GIT_SSH_COMMAND" not in captured["env"]
        assert "PATH" in captured["env"] and "HOME" in captured["env"]

    def test_env_keep_has_windows_essentials(self):
        import server as srv
        for var in ("SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "COMSPEC"):
            assert var in srv._CODEX_ENV_KEEP, f"{var} missing — Codex can't run on Windows"


class TestReservationNotBurnedOnMissingBinary:
    @pytest.mark.asyncio
    async def test_missing_binary_does_not_spend_reservation(self, tmp_path, monkeypatch):
        # Security/correctness #9: a missing CLI is not an execution and must
        # not burn a durable slot (else a broken install spends the whole cap).
        import server as srv
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "codex")
        monkeypatch.setattr(srv.shutil, "which", lambda _x: None)  # not on PATH
        out = await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert "not found" in out.lower()
        assert _read_daily_run_count() == 0  # no reservation burned


class TestPingSplit:
    @pytest.mark.asyncio
    async def test_health_check_no_model_call_no_quota(self, monkeypatch, tmp_path):
        import server as srv
        monkeypatch.setenv(MAX_RUNS_PER_DAY_ENV, "5")
        _reset_version_cache(warning="", resolved=True)
        spawned_cmds = []

        class _P:
            returncode = 0

            async def communicate(self, input=None):
                return (b"codex-cli 0.146.0", b"")

        async def fake_exec(*cmd, **kw):
            spawned_cmds.append(cmd)
            assert kw.get("env") is not None, "every spawn must use the sanitized env"
            return _P()

        async def no_model(*a, **k):
            raise AssertionError("model call during health check")

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(srv, "_run_codex", no_model)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        out = await srv.codex_ping(srv.PingInput())
        assert "health check" in out
        for cmd in spawned_cmds:
            assert "exec" not in cmd, "health check must never spawn codex exec"
        assert _read_daily_run_count() == 0, "health check must not consume quota"

    @pytest.mark.asyncio
    async def test_model_test_routes_through_the_runner(self, monkeypatch, tmp_path):
        import server as srv
        called = {}

        async def fake_run(prompt, **kw):
            called["prompt"] = prompt
            called.update(kw)
            return "pong"

        monkeypatch.setattr(srv, "_run_codex", fake_run)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        out = await srv.codex_ping(srv.PingInput(model_test=True))
        assert "round-trip OK" in out
        assert Path(called["project_dir"]) == Path(str(tmp_path)).resolve()
        assert called["tool_name"] == "ping"
        assert called["reasoning_effort"] == srv.DEFAULT_REASONING_EFFORT  # v2.1: policy effort, not "low"

    @pytest.mark.asyncio
    async def test_model_test_denied_without_roots(self, monkeypatch):
        import server as srv
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        out = await srv.codex_ping(srv.PingInput(model_test=True))
        assert out.startswith("Error:")
        assert ALLOWED_ROOTS_ENV in out


# =========================================================================
# v2.0 (M0-A1): deny-by-default confinement + authorization boundary
# =========================================================================

import server as _srv
from server import (
    codex_critique,
    codex_plan,
    codex_brainstorm,
    codex_collab,
    codex_evaluate,
    codex_recap,
    codex_status,
    BrainstormInput,
    CollaborateInput,
    EvaluateInput,
    RecapInput,
    StatusInput,
)


class TestDenyByDefault:
    def test_unset_env_denies_all(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        project = tmp_path / "repo"; project.mkdir()
        with pytest.raises(ValueError, match="No workspace roots configured"):
            _validate_project_dir(str(project))

    def test_whitespace_env_denies_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, "   ")
        project = tmp_path / "repo"; project.mkdir()
        with pytest.raises(ValueError, match="No workspace roots configured"):
            _validate_project_dir(str(project))

    def test_deny_message_names_the_fix(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        project = tmp_path / "repo"; project.mkdir()
        with pytest.raises(ValueError) as exc:
            _validate_project_dir(str(project))
        assert ALLOWED_ROOTS_ENV in str(exc.value)
        assert "README" in str(exc.value)

    def test_configured_root_allows_inside_denies_outside(self, tmp_path, monkeypatch):
        root = tmp_path / "allowed"; root.mkdir()
        inside = root / "proj"; inside.mkdir()
        outside = tmp_path / "elsewhere"; outside.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(root))
        assert _validate_project_dir(str(inside)) == str(inside.resolve())
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            _validate_project_dir(str(outside))

    def test_argv_roots_take_precedence_over_env(self, tmp_path, monkeypatch):
        argv_root = tmp_path / "argv_root"; argv_root.mkdir()
        env_root = tmp_path / "env_root"; env_root.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(env_root))
        monkeypatch.setattr(_srv, "_ARGV_ROOTS", [str(argv_root)])
        assert _allowed_roots() == [argv_root.resolve()]
        proj = env_root / "p"; proj.mkdir()
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            _validate_project_dir(str(proj))

    def test_env_parsing_uses_os_pathsep(self, tmp_path, monkeypatch):
        r1 = tmp_path / "r1"; r1.mkdir()
        r2 = tmp_path / "r2"; r2.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, f"{r1}{os.pathsep}{r2}")
        assert _allowed_roots() == [r1.resolve(), r2.resolve()]


class TestAuthorizationBoundary:
    """M0-A1 contract: every project-bound tool authorizes BEFORE any project op.

    Instrumented, not AST-searched: the project-op primitives are tripwired to
    record and fail; each tool called with a project_dir OUTSIDE the allowed
    root must return the confinement error having fired zero tripwires.
    """

    @pytest.fixture
    def outside_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "allowed"; root.mkdir()
        outside = tmp_path / "outside"; outside.mkdir()
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(root))
        return outside

    @pytest.fixture
    def tripwires(self, monkeypatch):
        fired = []

        def _trip(name):
            def _fn(*a, **k):
                fired.append(name)
                raise AssertionError(f"project op '{name}' before authorization")
            return _fn

        def _atrip(name):
            async def _fn(*a, **k):
                fired.append(name)
                raise AssertionError(f"project op '{name}' before authorization")
            return _fn

        monkeypatch.setattr(_srv, "_get_git_context", _atrip("_get_git_context"))
        monkeypatch.setattr(_srv, "_get_git_diff", _atrip("_get_git_diff"))
        monkeypatch.setattr(_srv, "_run_codex", _atrip("_run_codex"))
        monkeypatch.setattr(_srv, "_prepare_run_dir", _trip("_prepare_run_dir"))
        monkeypatch.setattr(_srv, "_safe_claudex_path", _trip("_safe_claudex_path"))
        monkeypatch.setattr(
            _srv.asyncio, "create_subprocess_exec", _atrip("create_subprocess_exec")
        )
        return fired

    @pytest.mark.asyncio
    async def test_every_project_bound_tool_denies_before_any_project_op(
        self, outside_dir, tripwires
    ):
        out_dir = str(outside_dir)
        cases = {
            "critique": codex_critique(SecondOpinionInput(
                plan="a plan long enough", project_dir=out_dir)),
            "plan": codex_plan(ParallelPlanInput(
                task="a task long enough", project_dir=out_dir)),
            "brainstorm": codex_brainstorm(BrainstormInput(
                topic="a topic long enough", project_dir=out_dir)),
            "collab": codex_collab(CollaborateInput(
                problem="a problem long enough",
                cc_analysis="analysis long enough", project_dir=out_dir)),
            "review": codex_review(QuickReviewInput(
                files="a.py", project_dir=out_dir)),
            "evaluate": codex_evaluate(EvaluateInput(
                options="Option A: aaaa. Option B: bbbb.", project_dir=out_dir)),
            "recap": codex_recap(RecapInput(
                session_id="s1", project_dir=out_dir)),
            "review_diff": codex_review_diff(ReviewDiffInput(project_dir=out_dir)),
            "result_disk_fallback": codex_result(JobResultInput(
                job_id="job-abcdef123456", project_dir=out_dir)),
            "submit": codex_submit(SubmitInput(
                tool="critique",
                arguments={"plan": "a plan long enough", "project_dir": out_dir})),
        }
        for name, coro in cases.items():
            out = await coro
            assert out.startswith("Error: "), f"{name}: expected confinement error, got: {out[:120]}"
            assert "outside the allowed workspace" in out, f"{name}: wrong error: {out[:160]}"
        assert tripwires == [], f"project ops before authorization: {tripwires}"

    @pytest.mark.asyncio
    async def test_status_stays_usable_but_skips_project_state(
        self, outside_dir, monkeypatch
    ):
        async def _no_spawn(*a, **k):
            raise OSError("no spawn in test")
        monkeypatch.setattr(_srv.asyncio, "create_subprocess_exec", _no_spawn)
        monkeypatch.setattr(_srv, "_check_codex_version", AsyncMock(return_value=""))
        out = await codex_status(StatusInput(project_dir=str(outside_dir)))
        assert not out.startswith("Error: ")
        assert "Project state: skipped" in out
        assert "Sessions" not in out

    @pytest.mark.asyncio
    async def test_status_shows_deny_all_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)

        async def _no_spawn(*a, **k):
            raise OSError("no spawn in test")
        monkeypatch.setattr(_srv.asyncio, "create_subprocess_exec", _no_spawn)
        monkeypatch.setattr(_srv, "_check_codex_version", AsyncMock(return_value=""))
        out = await codex_status(StatusInput())
        assert "DENY-ALL" in out
        assert "unrestricted" not in out


class TestPluginManifest:
    """Guards the MCP declaration — `claude plugin validate` cannot catch this.

    Validation exits 0 when `mcpServers` is absent or misspelled, so a lost
    declaration ships a plugin that installs clean and silently has no tools.
    """

    def _manifest(self):
        import json as _json
        return _json.loads((PROJECT_ROOT / ".claude-plugin" / "plugin.json").read_text())

    def _mcp_json(self):
        import json as _json
        return _json.loads((PROJECT_ROOT / ".mcp.json").read_text())

    def test_declares_codex_server(self):
        # Bare plugin shape: server name at top level, no "mcpServers" wrapper.
        # Wrapping it makes the project loader register a second, broken codex
        # server (CLAUDE_PLUGIN_ROOT is undefined outside plugin scope).
        mcp = self._mcp_json()
        assert "mcpServers" not in mcp
        server = mcp["codex"]
        assert server["command"] == "uv"
        # v2.4: the full argv is the contract (locked, script mode).
        assert server["args"] == ["run", "--locked", "--script", "${CLAUDE_PLUGIN_ROOT}/server/server.py"]
        assert "env" not in server

    def test_no_user_config_anywhere(self):
        # The desktop app can leave a plugin MCP server that needs plugin
        # settings unstarted (user_config_unsupported); one server config must
        # start on every surface. Roots come from --configure-roots, the env
        # var or the cloud default instead.
        assert "userConfig" not in self._manifest()
        assert "user_config" not in (PROJECT_ROOT / ".mcp.json").read_text()

    def test_declaration_not_moved_into_manifest(self):
        # Upstream anthropics/claude-code#16143 (open) drops plugin.json's
        # mcpServers field during manifest parsing -> zero tools, no error.
        assert "mcpServers" not in self._manifest()

    def test_version_parity_across_shipped_manifests(self):
        import json as _json
        ext = _json.loads((PROJECT_ROOT / "desktop-extension" / "manifest.json").read_text())
        assert self._manifest()["version"] == ext["version"]


# =========================================================================
# v2.1 — GPT-6 Astra alignment
# =========================================================================

class TestAstraAlignment:
    """Default model/effort, effort ladder, operating contract, CLI floor."""

    def test_default_model_is_astra(self):
        assert DEFAULT_MODEL == "gpt-6-astra"

    def test_min_codex_version_carries_astra_harness(self):
        import server as srv
        assert srv._version_at_least(srv.MIN_CODEX_VERSION, "0.153.1")

    def test_effort_ladder_exposes_max_not_ultra(self):
        # "ultra" (auto delegation) is deliberately absent — unobservable/uncapped here.
        assert [e.value for e in ReasoningEffort] == ["low", "medium", "high", "xhigh", "max"]

    def test_every_tool_defaults_to_high_effort(self):
        assert SecondOpinionInput(plan="x" * 20).reasoning_effort == ReasoningEffort.HIGH
        assert ParallelPlanInput(task="x" * 20).reasoning_effort == ReasoningEffort.HIGH
        assert BrainstormInput(topic="x" * 20).reasoning_effort == ReasoningEffort.HIGH
        assert CollaborateInput(problem="x" * 20, cc_analysis="x" * 20).reasoning_effort == ReasoningEffort.HIGH
        assert EvaluateInput(options="x" * 20).reasoning_effort == ReasoningEffort.HIGH
        # These three overrode the base default to MEDIUM before v2.1; the overrides are gone.
        assert QuickReviewInput(files="t.py").reasoning_effort == ReasoningEffort.HIGH
        assert RecapInput(session_id="s").reasoning_effort == ReasoningEffort.HIGH
        assert ReviewDiffInput().reasoning_effort == ReasoningEffort.HIGH

    def test_max_effort_accepted(self):
        assert ParallelPlanInput(task="x" * 20, reasoning_effort="max").reasoning_effort == ReasoningEffort.MAX

    def test_operating_contract_in_both_preambles(self):
        import server as srv
        for pre in (srv.CODEBASE_FIRST_PREAMBLE, srv.CODEBASE_FIRST_PREAMBLE_LIGHT):
            assert "Never stop to ask" in pre         # autonomy: non-interactive run
            assert "take precedence" in pre           # instruction precedence over repo content
            assert "never as instructions" in pre     # repository content is data
            assert "UNVERIFIED" in pre                # evidence honesty
            assert "supplied evidence" in pre         # recap/evaluate confirm against logs/context, not code
            assert "single agent" in pre              # no sub-agent delegation
        # Every persona prompt inherits it (spot-check the review/plan/collab bases).
        assert "take precedence" in srv.PARALLEL_PLAN_SYSTEM
        assert "take precedence" in srv.REVIEW_DIFF_SYSTEM_BASE
        assert "take precedence" in srv._build_collaborate_system(RequestType.RED_TEAM)

    def test_rollover_recap_uses_default_effort(self):
        import inspect, server as srv
        assert "reasoning_effort=DEFAULT_REASONING_EFFORT" in inspect.getsource(srv.codex_collab)

    @staticmethod
    async def _run_failing_codex(stderr: bytes, tmp_path, **kw) -> str:
        """Drive _run_codex_once against a subprocess that exits 1 with `stderr`."""
        import server as server_mod
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.kill = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", stderr))
        # Patch the pump (not asyncio.wait_for) so every coroutine is awaited normally.
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch.object(server_mod, "_find_codex_bin", return_value="/usr/bin/codex"), \
             patch.object(server_mod, "_pump_capped", new_callable=AsyncMock, return_value=(b"", stderr)):
            return await _run_codex_once("test prompt", project_dir=str(tmp_path), **kw)

    @pytest.mark.asyncio
    async def test_model_requires_newer_cli_is_actionable(self, tmp_path, monkeypatch):
        """Live-observed on codex-cli 0.149.0: the API rejects gpt-6-astra with a
        400 'requires a newer version of Codex'. Must map to an upgrade hint,
        not the generic 'exited with code 1' nor the auth/rate-limit matchers."""
        import server as server_mod
        stderr = (b'warning: Model metadata for `gpt-6-astra` not found.\n'
                  b'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
                  b'"message":"The \'gpt-6-astra\' model requires a newer version of Codex. '
                  b'Please upgrade to the latest app or CLI and try again."}}')
        async def fake_check(*, consume=False):
            server_mod._version_cache["installed"] = "0.149.0"  # the live-observed case
            return ""
        monkeypatch.setattr(server_mod, "_check_codex_version", fake_check)
        monkeypatch.setitem(server_mod._version_cache, "installed", "")
        result = await self._run_failing_codex(stderr, tmp_path)
        assert result.startswith(ERROR_PREFIX)
        assert "too old for model 'gpt-6-astra'" in result
        assert "installed: v0.149.0" in result
        assert f">= v{server_mod.MIN_CODEX_VERSION}" in result
        assert "not authenticated" not in result.lower()
        assert "rate limit" not in result.lower()

    @pytest.mark.asyncio
    async def test_newer_cli_error_for_override_omits_astra_floor(self, tmp_path, monkeypatch):
        """No floor is established for a per-call override model: report the rejection
        without asserting the CLI is old and without quoting the Astra floor."""
        import server as server_mod
        async def fake_check(*, consume=False):
            server_mod._version_cache["installed"] = "0.153.4"
            return ""
        monkeypatch.setattr(server_mod, "_check_codex_version", fake_check)
        monkeypatch.setitem(server_mod._version_cache, "installed", "")
        stderr = b'ERROR: {"message":"The \'gpt-7-future\' model requires a newer version of Codex."}'
        result = await self._run_failing_codex(stderr, tmp_path, model="gpt-7-future")
        assert "rejected model 'gpt-7-future'" in result and "installed CLI: v0.153.4" in result
        assert "too old" not in result
        assert f">= v{server_mod.MIN_CODEX_VERSION}" not in result  # floor hint is Astra-only
        assert "Update to the latest CLI" in result and server_mod.CODEX_INSTALL_CMD in result

    @pytest.mark.asyncio
    async def test_newer_cli_error_is_access_diagnosis_when_floor_met(self, tmp_path, monkeypatch):
        """Installed >= floor yet the API still rejects the default model: the floor
        is wrong or the account lacks Astra access. The two diagnoses are exclusive —
        no 'too old' / 'Update to >= vX' text may appear here."""
        import server as server_mod
        stderr = b'ERROR: {"message":"The \'gpt-6-astra\' model requires a newer version of Codex."}'
        async def fake_check(*, consume=False):
            server_mod._version_cache["installed"] = "0.153.4"
            return ""
        monkeypatch.setattr(server_mod, "_check_codex_version", fake_check)
        monkeypatch.setitem(server_mod._version_cache, "installed", "")
        result = await self._run_failing_codex(stderr, tmp_path)
        assert result.startswith(ERROR_PREFIX)
        assert "(v0.153.4) already meets the pinned floor" in result
        assert "GPT-6 Astra access" in result
        assert "too old" not in result
        assert f">= v{server_mod.MIN_CODEX_VERSION}" not in result

    @pytest.mark.asyncio
    async def test_newer_cli_error_resolves_version_on_cold_cache(self, tmp_path, monkeypatch):
        """First model call of the process: the version cache is cold because
        _run_codex checks it only AFTER the runner returns. The matcher must resolve
        it itself, otherwise the access diagnosis can never fire on the first call."""
        import server as server_mod
        monkeypatch.setitem(server_mod._version_cache, "installed", "")
        calls = []
        async def fake_check(*, consume=False):
            calls.append(consume)
            server_mod._version_cache["installed"] = "0.153.4"
            return ""
        monkeypatch.setattr(server_mod, "_check_codex_version", fake_check)
        stderr = b'ERROR: {"message":"The \'gpt-6-astra\' model requires a newer version of Codex."}'
        result = await self._run_failing_codex(stderr, tmp_path)
        assert calls == [False]  # resolved in-path, without consuming the one-shot warning
        assert "already meets the pinned floor" in result
        assert "too old" not in result

    @pytest.mark.asyncio
    async def test_newer_cli_error_never_asserts_too_old_without_evidence(self, tmp_path, monkeypatch):
        """Version probe failed/in backoff (installed == "") or unparseable (pre-release):
        the message must not claim 'too old' — it has no evidence either way."""
        import server as server_mod
        stderr = b'ERROR: {"message":"The \'gpt-6-astra\' model requires a newer version of Codex."}'
        for seeded, shown in (("", "unknown"), ("0.154.0-beta.1", "'0.154.0-beta.1'")):
            async def fake_check(*, consume=False, _v=seeded):
                server_mod._version_cache["installed"] = _v
                return ""
            monkeypatch.setattr(server_mod, "_check_codex_version", fake_check)
            monkeypatch.setitem(server_mod._version_cache, "installed", "")
            result = await self._run_failing_codex(stderr, tmp_path)
            assert result.startswith(ERROR_PREFIX)
            assert "could not be determined" in result and shown in result
            assert "too old" not in result and "already meets" not in result
            assert server_mod.CODEX_INSTALL_CMD in result

    def test_version_at_least(self):
        import server as srv
        assert not srv._version_at_least("0.153.4", "garbage-floor")  # unparseable floor: False, not TypeError
        assert srv._version_at_least("0.153.4", "0.153.1")
        assert srv._version_at_least("v0.153.1", "0.153.1")
        assert not srv._version_at_least("0.149.0", "0.153.1")
        assert not srv._version_at_least("", "0.153.1")
        assert not srv._version_at_least("garbage", "0.153.1")
        assert not srv._version_at_least("0.154.0-beta.1", "0.153.1")  # pre-release: unparseable, fails closed
        assert srv._parse_version("v0.153.4") == (0, 153, 4) and srv._parse_version("x") is None

    @pytest.mark.asyncio
    async def test_ping_timeout_gets_probe_specific_message(self, monkeypatch):
        import server as srv
        async def fake_run(prompt, **kw):
            return f"{ERROR_PREFIX}Codex timed out after 180s. Try: (1) focus_files ..."
        monkeypatch.setattr(srv, "_run_codex", fake_run)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        out = await srv.codex_ping(srv.PingInput(model_test=True))
        assert out.startswith(ERROR_PREFIX)
        assert "focus_files" not in out and "timeout_seconds" not in out
        assert "without model_test" in out

    @pytest.mark.asyncio
    async def test_ping_passes_through_non_timeout_errors_verbatim(self, monkeypatch):
        """A stderr echo that merely CONTAINS 'timed out' is not the runner's timeout —
        it must not be rewritten into a false '180s' claim."""
        import server as srv
        passthrough = f"{ERROR_PREFIX}Codex exited with code 1.\nStderr: error sending request: operation timed out (os error 60)"
        async def fake_run(prompt, **kw):
            return passthrough
        monkeypatch.setattr(srv, "_run_codex", fake_run)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        assert await srv.codex_ping(srv.PingInput(model_test=True)) == passthrough

    @pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
    def test_single_agent_boundary_renders_without_agent_role(self, tmp_path):
        """Integration (no model call, zero quota): render the model-visible prompt with the
        plugin's exact multi-agent switches and assert the <multi_agent_role> block is gone,
        with the un-switched render as the positive control. Verified live on codex-cli 0.153.4."""
        import server as srv
        home = tmp_path / "codex_home"; home.mkdir()
        cache = Path.home() / ".codex" / "models_cache.json"
        if cache.exists():
            shutil.copy(cache, home / "models_cache.json")
        env = {**os.environ, "CODEX_HOME": str(home)}
        def render(*extra):
            r = subprocess.run(["codex", "debug", "prompt-input", "-c", f"model={srv.DEFAULT_MODEL}", *extra, "hi"],
                               capture_output=True, text=True, timeout=120, env=env)
            return r
        baseline = render()
        if baseline.returncode != 0 or "multi_agent_role" not in baseline.stdout:
            pytest.skip("prompt-input render unavailable in this environment (no positive control)")
        switches = [a for pair in (("-c", k) for k in ("agents.enabled=false", "features.multi_agent=false", "features.multi_agent_v2=false")) for a in pair]
        switched = render(*switches)
        assert switched.returncode == 0, f"switched render failed: {switched.stderr[-500:]}"
        assert switched.stdout.strip(), "switched render produced no prompt"
        assert "multi_agent_role" not in switched.stdout
        assert "multi_agent_mode" not in switched.stdout

# =========================================================================
# v2.2 — Claude Code cloud sessions (claude.ai/code)
# =========================================================================

class TestCloudSessions:
    """Cloud sessions cap MCP calls at 60s, have no shell profile for roots,
    route egress through a policy proxy, and flag untracked files."""

    def _cloud(self, monkeypatch, project):
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

    # --- MCP declaration ---

    def test_mcp_json_declares_tool_timeout(self):
        # Cloud sessions set MCP_TOOL_TIMEOUT=60000; a per-server timeout wins
        # over it. Must cover the longest sync call (timeout_seconds <= 1800).
        server = json.loads((PROJECT_ROOT / ".mcp.json").read_text())["codex"]
        assert server["timeout"] >= 1_800_000

    # --- workspace roots ---

    def test_cloud_default_root_is_the_project_dir(self, tmp_path, monkeypatch):
        import server as srv
        project = tmp_path / "repo"; (project / "sub").mkdir(parents=True)
        outside = tmp_path / "other"; outside.mkdir()
        self._cloud(monkeypatch, project)
        assert srv._allowed_roots() == [project.resolve()]
        assert srv._roots_are_cloud_default()
        assert _validate_project_dir(str(project / "sub")) == str((project / "sub").resolve())
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            _validate_project_dir(str(outside))

    @pytest.mark.parametrize("remote", [None, "", "1", "True", "false"])
    def test_no_cloud_default_outside_cloud_sessions(self, tmp_path, monkeypatch, remote):
        import server as srv
        monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        if remote is None:
            monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CODE_REMOTE", remote)
        assert srv._allowed_roots() == []
        with pytest.raises(ValueError, match="No workspace roots configured"):
            _validate_project_dir(str(tmp_path))

    def test_cloud_default_never_home_root_relative_or_missing(self, tmp_path, monkeypatch):
        import server as srv
        for bad in (str(Path.home()), "/", "relative/dir", str(tmp_path / "missing"), ""):
            self._cloud(monkeypatch, bad)
            assert srv._allowed_roots() == [], bad

    def test_explicit_roots_win_over_cloud_default(self, tmp_path, monkeypatch):
        import server as srv
        project = tmp_path / "repo"; project.mkdir()
        other = tmp_path / "other"; other.mkdir()
        self._cloud(monkeypatch, project)
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(other))
        assert srv._allowed_roots() == [other.resolve()]
        assert not srv._roots_are_cloud_default()
        # an explicitly set value that yields nothing stays deny-all
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, "${user_config.allowed_roots}")
        assert srv._allowed_roots() == []
        monkeypatch.delenv(ALLOWED_ROOTS_ENV)
        monkeypatch.setattr(srv, "_ARGV_ROOTS", [str(other)])
        assert srv._allowed_roots() == [other.resolve()]

    @pytest.mark.asyncio
    async def test_status_and_ping_label_the_cloud_default(self, tmp_path, monkeypatch):
        import server as srv
        project = tmp_path / "repo"; project.mkdir()
        self._cloud(monkeypatch, project)
        _reset_version_cache(warning="", resolved=True)

        class _P:
            returncode = 0

            async def communicate(self, input=None):
                return (b"codex-cli 0.157.0", b"")

        async def fake_exec(*cmd, **kw):
            return _P()

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        status = await srv.codex_status(srv.StatusInput(project_dir=str(project)))
        assert "cloud session default" in status
        ping = await srv.codex_ping(srv.PingInput())
        assert "cloud session default" in ping and "confinement active" in ping
        _reset_version_cache()

    # --- Codex credentials ---

    def test_codex_auth_env_reaches_codex_spawns_only(self, monkeypatch):
        import server as srv
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        monkeypatch.setenv("CODEX_ACCESS_TOKEN", "at-codex")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        plain = srv._sanitized_codex_env()
        assert "CODEX_API_KEY" not in plain and "CODEX_ACCESS_TOKEN" not in plain
        auth = srv._sanitized_codex_env(codex_auth=True)
        assert auth["CODEX_API_KEY"] == "sk-codex"
        assert auth["CODEX_ACCESS_TOKEN"] == "at-codex"
        assert "OPENAI_API_KEY" not in auth  # codex exec ignores it; keep stripping

    @pytest.mark.asyncio
    async def test_git_spawns_never_get_codex_auth(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        envs = []

        class _P:
            returncode = 0

            async def communicate(self, input=None):
                return (b"", b"")

        async def fake_exec(*cmd, **kw):
            envs.append((cmd, kw.get("env")))
            return _P()

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        await srv._get_git_context(str(tmp_path))
        assert envs, "git context must spawn git"
        for cmd, env in envs:
            assert cmd[0] == "git"
            assert env is not None and "CODEX_API_KEY" not in env

    @pytest.mark.asyncio
    async def test_exec_gets_codex_auth_and_network_hygiene_flags(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        captured = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env")
            raise FileNotFoundError()

        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await srv._run_codex_once("p", project_dir=str(tmp_path))
        cmd = captured["cmd"]
        overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
        for key in (
            "features.unbounded_connection_retries=false",
            "analytics.enabled=false",
            'otel.metrics_exporter="none"',
            "features.plugins=false",
            "features.apps=false",
            "shell_environment_policy.ignore_default_excludes=false",
        ):
            assert key in overrides, f"missing {key}"
        assert "--strict-config" in cmd
        assert captured["env"]["CODEX_API_KEY"] == "sk-codex"

    @pytest.mark.asyncio
    async def test_ping_reports_env_api_key(self, monkeypatch):
        import server as srv
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        _reset_version_cache(warning="", resolved=True)

        class _P:
            returncode = 1  # `codex login status` never reads CODEX_API_KEY

            async def communicate(self, input=None):
                return (b"", b"Not logged in")

        async def fake_exec(*cmd, **kw):
            return _P()

        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        out = await srv.codex_ping(srv.PingInput())
        assert "API key from CODEX_API_KEY" in out
        assert "sk-codex" not in out
        _reset_version_cache()

    # --- network errors ---

    PROXY_403 = (
        "ERROR codex_api::endpoint::responses_websocket: failed to connect to websocket: "
        "URL error: Proxy connection failed: HTTP CONNECT failed with status 403, "
        "url: wss://api.openai.com/v1/responses\n"
        "ERROR: Reconnecting... 2/5\n"
        "ERROR: Connection failed: error sending request\n"
    )

    def test_network_error_messages(self):
        import server as srv
        blocked = srv._network_error_message(self.PROXY_403)
        assert blocked.startswith("Error:") and "api.openai.com" in blocked
        assert "Network access" in blocked
        offline = srv._network_error_message("ERROR: Connection failed: error sending request")
        assert offline.startswith("Error:") and "could not reach OpenAI" in offline
        assert srv._network_error_message("unexpected status 401 Unauthorized") is None

    @pytest.mark.asyncio
    async def test_proxy_block_is_reported_as_network_not_auth(self, tmp_path, monkeypatch):
        # the stderr also mentions "login"; the network cause must win
        import server as srv
        stderr = (self.PROXY_403 + "hint: codex login\n").encode()

        async def fake_exec(*cmd, **kwargs):
            return _FakeProc(err_chunks=[stderr], rc=1)

        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", fake_exec)
        out = await srv._run_codex_once("p", project_dir=str(tmp_path))
        assert "network policy blocked" in out and "api.openai.com" in out

    # --- .claudex/ stays out of git ---

    def test_claudex_dir_ignores_itself(self, tmp_path):
        import subprocess as sp
        from server import _prepare_run_dir
        sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        run_dir = _prepare_run_dir(str(tmp_path))
        (run_dir / "artifact.py").write_text("x = 1\n")
        assert (tmp_path / ".claudex" / ".gitignore").read_text().splitlines()[-1] == "*"
        untracked = sp.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        ).stdout
        assert untracked == ""

    def test_claudex_ignore_never_overwrites_or_follows_symlinks(self, tmp_path):
        import server as srv
        claudex = tmp_path / ".claudex"; claudex.mkdir()
        (claudex / ".gitignore").write_text("mine\n")
        srv._ensure_claudex_ignored(claudex)
        assert (claudex / ".gitignore").read_text() == "mine\n"
        other = tmp_path / "other"; other.mkdir()
        claudex2 = other / ".claudex"; claudex2.mkdir()
        target = tmp_path / "target.txt"
        (claudex2 / ".gitignore").symlink_to(target)
        srv._ensure_claudex_ignored(claudex2)
        assert not target.exists()

    def test_job_files_land_in_an_ignored_dir(self, tmp_path):
        import server as srv
        job_id = "job-test123"
        srv._jobs[job_id] = {
            "project_dir": str(tmp_path), "tool": "review", "status": "done", "result": "ok",
        }
        try:
            srv._write_job_file(job_id)
        finally:
            srv._jobs.pop(job_id, None)
        assert (tmp_path / ".claudex" / "jobs" / f"{job_id}.md").is_file()
        assert (tmp_path / ".claudex" / ".gitignore").is_file()


# =========================================================================
# v2.3 — /codex:login (device-code sign-in without a browser)
# =========================================================================

class TestCodexLogin:
    PROMPT = (
        "\nWelcome to Codex [v\x1b[90m0.157.0\x1b[0m]\n"
        "\x1b[90mOpenAI's command-line coding agent\x1b[0m\n\n"
        "Follow these steps to sign in with ChatGPT using device code authorization:\n\n"
        "1. Open this link in your browser and sign in to your account\n"
        "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n\n"
        "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n"
        "   \x1b[94mXPVP-N3KGO\x1b[0m\n\n"
        "\x1b[90mContinue only if you started this login in Codex.\x1b[0m\n"
    )

    def _fake_codex(self, tmp_path, device_body=None):
        """A `codex` that answers `login status` from a marker file (the user's
        approval) and prints the real device prompt for `login --device-auth`."""
        approved = tmp_path / "approved"
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(self.PROMPT)
        body = device_body or f'cat "{prompt}"; sleep 30'
        script = tmp_path / "codex"
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = login ] && [ "$2" = status ]; then\n'
            f'  if [ -f "{approved}" ]; then echo "Logged in using ChatGPT"; exit 0; fi\n'
            '  echo "Not logged in" >&2; exit 1\n'
            "fi\n"
            'if [ "$1" = login ] && [ "$2" = --device-auth ]; then\n'
            f"  {body}\n"
            "fi\n"
            "exit 2\n"
        )
        script.chmod(0o755)
        return str(script), approved

    def test_parse_device_prompt(self):
        import server as srv
        url, code = srv._parse_device_prompt(self.PROMPT)
        assert url == "https://auth.openai.com/codex/device"
        assert code == "XPVP-N3KGO"

    def test_parse_refuses_foreign_urls(self):
        import server as srv
        for bad in ("https://evil.example/codex/device",
                    "https://auth.openai.com.evil.example/codex/device",
                    "http://auth.openai.com/codex/device"):
            url, _ = srv._parse_device_prompt(self.PROMPT.replace("https://auth.openai.com/codex/device", bad))
            assert url is None, bad

    @pytest.mark.asyncio
    async def test_full_flow_code_then_pending_then_signed_in(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        codex, approved = self._fake_codex(tmp_path)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: codex)
        try:
            first = await srv.codex_login(srv.LoginInput())
            assert "https://auth.openai.com/codex/device" in first
            assert "XPVP-N3KGO" in first
            assert not first.startswith("Error:")
            proc = srv._device_login["proc"]
            assert proc is not None and proc.returncode is None

            again = await srv.codex_login(srv.LoginInput())
            assert "Still waiting" in again and "XPVP-N3KGO" in again
            assert srv._device_login["proc"] is proc, "a pending login is reused, not restarted"

            approved.write_text("yes")  # the user approved on their phone
            done = await srv.codex_login(srv.LoginInput())
            assert "signed in" in done and "Logged in using ChatGPT" in done
            assert srv._device_login["proc"] is None
            assert proc.returncode is not None, "the device login is stopped once signed in"
        finally:
            await srv._stop_device_login()

    @pytest.mark.asyncio
    async def test_restart_gives_a_fresh_login(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        codex, _ = self._fake_codex(tmp_path)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: codex)
        try:
            await srv.codex_login(srv.LoginInput())
            old = srv._device_login["proc"]
            out = await srv.codex_login(srv.LoginInput(restart=True))
            assert "Sign Codex in" in out
            new = srv._device_login["proc"]
            assert new is not old and old.returncode is not None
        finally:
            await srv._stop_device_login()

    @pytest.mark.asyncio
    async def test_already_signed_in_starts_nothing(self, tmp_path, monkeypatch):
        import server as srv
        codex, approved = self._fake_codex(tmp_path)
        approved.write_text("yes")
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: codex)
        out = await srv.codex_login(srv.LoginInput())
        assert "signed in" in out and srv._device_login["proc"] is None

    @pytest.mark.asyncio
    async def test_env_api_key_needs_no_sign_in(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        codex, _ = self._fake_codex(tmp_path)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: codex)
        out = await srv.codex_login(srv.LoginInput())
        assert "CODEX_API_KEY" in out and "sk-codex" not in out
        assert srv._device_login["proc"] is None

    @pytest.mark.asyncio
    async def test_blocked_auth_host_names_the_fix(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        codex, _ = self._fake_codex(tmp_path, device_body=(
            'echo "Error logging in with device code: error sending request for url '
            '(https://auth.openai.com/api/accounts/deviceauth/usercode)"; exit 1'
        ))
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: codex)
        out = await srv.codex_login(srv.LoginInput())
        assert out.startswith("Error:") and "auth.openai.com" in out and "Network access" in out
        assert srv._device_login["proc"] is None

    @pytest.mark.asyncio
    async def test_missing_cli(self, monkeypatch):
        import server as srv
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: "codex")
        monkeypatch.setattr(srv.shutil, "which", lambda name: None)
        out = await srv.codex_login(srv.LoginInput())
        assert out.startswith("Error:") and "/codex:login" in out

    @pytest.mark.asyncio
    async def test_ping_points_to_codex_login(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        codex, _ = self._fake_codex(tmp_path)
        monkeypatch.setattr(srv, "_find_codex_bin", lambda: codex)
        _reset_version_cache(warning="", resolved=True)
        out = await srv.codex_ping(srv.PingInput())
        assert "Not logged in — run /codex:login" in out
        _reset_version_cache()

    def test_command_is_wired_to_the_tool(self):
        text = (PROJECT_ROOT / "commands" / "login.md").read_text()
        front = text.split("---")[1]
        assert "name: login" in front
        assert "mcp__plugin_codex_codex__codex_login" in front


# =========================================================================
# v2.3.1 security: no symlink following inside .claudex/ (real filesystem)
# =========================================================================

import time as _time


def _make_old(path: Path, days: float = 3) -> None:
    old = _time.time() - days * 86_400
    os.utime(path, (old, old))


class TestV231CleanupContainment:
    """A repo could commit .claudex/<subdir> as a symlink; cleanup followed it
    and deleted day-old files in the target (reported 2026-10-01)."""

    @pytest.fixture(params=[True, False], ids=["dir_fd", "pathwise"])
    def mode(self, request, monkeypatch):
        import server as srv
        if request.param and not srv._HAVE_DIR_FD:
            pytest.skip("platform lacks dir_fd support")
        monkeypatch.setattr(srv, "_HAVE_DIR_FD", request.param)
        # "pathwise" stands for Windows, the only place the fallback runs.
        monkeypatch.setattr(srv, "_PATHWISE_FALLBACK", not request.param)
        return request.param

    @pytest.mark.parametrize("subdir", ["sessions", "recaps", "jobs"])
    def test_symlinked_subdir_to_outside_is_not_followed(self, tmp_path, subdir, mode):
        import server as srv
        victim_dir = tmp_path / "victim"; victim_dir.mkdir()
        victim = victim_dir / "precious.txt"; victim.write_text("keep me")
        _make_old(victim)
        claudex = tmp_path / "proj" / ".claudex"; claudex.mkdir(parents=True)
        (claudex / subdir).symlink_to(victim_dir)
        srv._cleanup_old_sessions(claudex)
        assert victim.exists() and victim.read_text() == "keep me"

    @pytest.mark.parametrize("subdir", ["sessions", "recaps", "jobs"])
    def test_symlinked_subdir_to_inside_is_not_followed(self, tmp_path, subdir, mode):
        import server as srv
        claudex = tmp_path / "proj" / ".claudex"
        other = claudex / "run-keep"; other.mkdir(parents=True)
        f = other / "artifact.md"; f.write_text("x"); _make_old(f)
        (claudex / subdir).symlink_to(other)
        srv._cleanup_old_sessions(claudex)
        assert f.exists()

    def test_symlinked_claudex_dir_is_not_followed(self, tmp_path, mode):
        import server as srv
        victim_dir = tmp_path / "victim" / "jobs"; victim_dir.mkdir(parents=True)
        victim = victim_dir / "precious.txt"; victim.write_text("keep"); _make_old(victim)
        proj = tmp_path / "proj"; proj.mkdir()
        (proj / ".claudex").symlink_to(tmp_path / "victim")
        srv._cleanup_old_sessions(proj / ".claudex")
        assert victim.exists()

    def test_normal_cleanup_still_works(self, tmp_path, mode):
        import server as srv
        claudex = tmp_path / ".claudex"
        for sub in ("sessions", "recaps", "jobs"):
            (claudex / sub).mkdir(parents=True)
            stale = claudex / sub / "stale.md"; stale.write_text("old"); _make_old(stale)
            fresh = claudex / sub / "fresh.md"; fresh.write_text("new")
        outside = tmp_path / "outside.md"; outside.write_text("o"); _make_old(outside)
        (claudex / "sessions" / "leaf-link.md").symlink_to(outside)
        srv._cleanup_old_sessions(claudex)
        for sub in ("sessions", "recaps", "jobs"):
            assert not (claudex / sub / "stale.md").exists()
            assert (claudex / sub / "fresh.md").exists()
        assert outside.exists()  # leaf symlinks are skipped, never followed

    def test_prepare_run_dir_path_cannot_reach_outside(self, tmp_path, monkeypatch):
        # End-to-end through the real trigger (_prepare_run_dir runs cleanup).
        import server as srv
        victim_dir = tmp_path / "victim"; victim_dir.mkdir()
        victim = victim_dir / "precious.txt"; victim.write_text("keep"); _make_old(victim)
        proj = tmp_path / "proj"; (proj / ".claudex").mkdir(parents=True)
        (proj / ".claudex" / "jobs").symlink_to(victim_dir)
        srv._prepare_run_dir(str(proj))
        assert victim.exists()


class TestV231NoFollowIO:
    def test_symlink_to_sibling_is_rejected(self, tmp_path):
        sessions = tmp_path / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        (sessions / "real.md").write_text("real")
        (sessions / "alias.md").symlink_to("real.md")
        assert _safe_claudex_path(str(tmp_path), "sessions", "alias.md") is None

    def test_write_refuses_symlinked_subdir(self, tmp_path):
        import server as srv
        if not srv._HAVE_DIR_FD:
            pytest.skip("platform lacks dir_fd support")
        outside = tmp_path / "outside"; outside.mkdir()
        claudex = tmp_path / "proj" / ".claudex"; claudex.mkdir(parents=True)
        (claudex / "sessions").symlink_to(outside)
        with pytest.raises(OSError):
            srv._write_text_nofollow(claudex / "sessions" / "s.md", "x")
        assert not (outside / "s.md").exists()

    def test_write_refuses_leaf_symlink(self, tmp_path):
        import server as srv
        outside = tmp_path / "outside.md"; outside.write_text("original")
        sessions = tmp_path / "proj" / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        (sessions / "s.md").symlink_to(outside)
        with pytest.raises(OSError):
            srv._write_text_nofollow(sessions / "s.md", "overwritten")
        assert outside.read_text() == "original"

    def test_job_file_refuses_symlinked_jobs_dir(self, tmp_path):
        import server as srv
        outside = tmp_path / "outside"; outside.mkdir()
        proj = tmp_path / "proj"; (proj / ".claudex").mkdir(parents=True)
        (proj / ".claudex" / "jobs").symlink_to(outside)
        job_id = "job-abcdef123456"
        srv._jobs[job_id] = {"project_dir": str(proj), "tool": "plan",
                             "status": "completed", "result": "r"}
        try:
            srv._write_job_file(job_id)
        finally:
            srv._jobs.pop(job_id, None)
        assert list(outside.iterdir()) == []

    @pytest.mark.asyncio
    async def test_collab_session_swapped_during_model_call(self, tmp_path):
        # The session path is validated before the minutes-long model call;
        # swapping .claudex/sessions for a symlink meanwhile must not redirect
        # the write (the original report's TOCTOU follow-up).
        import server as srv
        proj = tmp_path / "proj"; sessions = proj / ".claudex" / "sessions"
        sessions.mkdir(parents=True)
        outside = tmp_path / "outside"; outside.mkdir()

        async def swap_then_answer(*a, **k):
            shutil.rmtree(sessions)
            sessions.symlink_to(outside)
            return "Codex says hi"

        with patch.object(srv, "_run_codex", side_effect=swap_then_answer):
            out = await srv.codex_collab(srv.CollaborateInput(
                problem="a problem long enough", cc_analysis="analysis long enough",
                project_dir=str(proj), session_id="swap-test"))
        assert list(outside.iterdir()) == []
        assert "Session document NOT updated" in out

    @pytest.mark.asyncio
    async def test_collab_refuses_symlinked_session_file(self, tmp_path):
        import server as srv
        proj = tmp_path / "proj"; sessions = proj / ".claudex" / "sessions"
        sessions.mkdir(parents=True)
        secret = tmp_path / "secret.md"; secret.write_text("<!-- claudex:rounds=1 -->")
        (sessions / "s1.md").symlink_to(secret)
        with patch.object(srv, "_run_codex", new_callable=AsyncMock, return_value="x"):
            out = await srv.codex_collab(srv.CollaborateInput(
                problem="a problem long enough", cc_analysis="analysis long enough",
                project_dir=str(proj), session_id="s1"))
        assert out.startswith("Error:")


class TestV231AnchoredIO:
    """Codex R1 review (2026-10-01): operations must stay anchored even when
    .claudex or a subdir is swapped for a symlink AFTER path validation."""

    def _swap_claudex(self, proj, outside):
        shutil.rmtree(proj / ".claudex")
        (proj / ".claudex").symlink_to(outside)

    def test_swap_after_validation_write_refused(self, tmp_path):
        import server as srv
        proj = tmp_path / "proj"; (proj / ".claudex" / "sessions").mkdir(parents=True)
        outside = tmp_path / "outside"; (outside / "sessions").mkdir(parents=True)
        path = srv._safe_claudex_path(str(proj), "sessions", "s.md")
        assert path == proj / ".claudex" / "sessions" / "s.md"  # lexical, not resolved
        self._swap_claudex(proj, outside)
        with pytest.raises(OSError):
            srv._write_text_nofollow(path, "x")
        assert list((outside / "sessions").iterdir()) == []

    def test_swap_after_validation_read_refused(self, tmp_path):
        import server as srv
        proj = tmp_path / "proj"; (proj / ".claudex" / "sessions").mkdir(parents=True)
        outside = tmp_path / "outside"; (outside / "sessions").mkdir(parents=True)
        (outside / "sessions" / "s.md").write_text("<!-- claudex:rounds=1 --> secret")
        path = srv._safe_claudex_path(str(proj), "sessions", "s.md")
        self._swap_claudex(proj, outside)
        with pytest.raises(OSError):
            srv._read_text_nofollow(path)

    @pytest.mark.parametrize("atomic", [True, False], ids=["renameat", "in_place"])
    def test_job_writer_both_modes(self, tmp_path, monkeypatch, atomic):
        import server as srv
        if not srv._HAVE_DIR_FD:
            pytest.skip("platform lacks dir_fd support")
        monkeypatch.setattr(srv, "_HAVE_RENAME_DIR_FD", atomic and srv._HAVE_RENAME_DIR_FD)
        proj = tmp_path / "proj"; proj.mkdir()
        job_id = "job-abcdef123456"
        srv._jobs[job_id] = {"project_dir": str(proj), "tool": "plan",
                             "status": "completed", "result": "the result"}
        try:
            srv._write_job_file(job_id)
            srv._write_job_file(job_id)  # overwrite works in both modes
        finally:
            srv._jobs.pop(job_id, None)
        f = proj / ".claudex" / "jobs" / f"{job_id}.md"
        assert "Status: completed" in f.read_text() and "the result" in f.read_text()
        assert oct(f.stat().st_mode & 0o777) == "0o600"
        assert [p.name for p in f.parent.iterdir() if p.name.endswith(".tmp")] == []

    def test_job_writer_selection_is_fd_based_on_posix(self):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX only")
        assert srv._HAVE_DIR_FD and srv._HAVE_RENAME_DIR_FD

    @pytest.mark.asyncio
    async def test_fifo_job_file_is_refused_without_hanging(self, tmp_path):
        import server as srv
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs")
        proj = tmp_path / "proj"; jobs = proj / ".claudex" / "jobs"; jobs.mkdir(parents=True)
        os.mkfifo(jobs / "job-abcdef123456.md")
        out = await asyncio.wait_for(srv.codex_result(srv.JobResultInput(
            job_id="job-abcdef123456", project_dir=str(proj))), timeout=5)
        assert out.startswith("Error:")

    def test_fifo_session_is_refused(self, tmp_path):
        import server as srv
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs")
        sessions = tmp_path / "proj" / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        os.mkfifo(sessions / "s.md")
        with pytest.raises(OSError):
            srv._read_session_text(sessions / "s.md")

    def test_oversized_session_is_refused(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.setattr(srv, "SESSION_FILE_MAX_CHARS", 100)
        sessions = tmp_path / "proj" / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        (sessions / "big.md").write_text("x" * 500)
        with pytest.raises(OSError):
            srv._read_session_text(sessions / "big.md")

    def test_run_dir_creation_and_artifacts_refuse_swapped_claudex(self, tmp_path):
        import server as srv
        proj = tmp_path / "proj"; proj.mkdir()
        run_dir = srv._prepare_run_dir(str(proj))
        outside = tmp_path / "outside"; (outside / run_dir.name).mkdir(parents=True)
        self._swap_claudex(proj, outside)
        out = (
            "---FINAL-ANSWER---\n"
            '<claudex-artifact filename="proof.txt" language="text">pwned</claudex-artifact>'
        )
        _, artifacts = srv._extract_and_save_artifacts(out, run_dir)
        assert artifacts == []
        assert not (outside / run_dir.name / "proof.txt").exists()
        with pytest.raises(OSError):
            srv._prepare_run_dir(str(proj))

    def test_remove_run_dir_refuses_swapped_claudex(self, tmp_path):
        import server as srv
        proj = tmp_path / "proj"; proj.mkdir()
        run_dir = srv._prepare_run_dir(str(proj))
        outside = tmp_path / "outside"; (outside / run_dir.name).mkdir(parents=True)
        keep = outside / run_dir.name / "keep.txt"; keep.write_text("k")
        self._swap_claudex(proj, outside)
        srv._remove_run_dir(run_dir)
        assert keep.exists()

    def test_stale_run_dir_cleanup_skips_symlinks(self, tmp_path):
        import server as srv
        claudex = tmp_path / "proj" / ".claudex"; claudex.mkdir(parents=True)
        stale = claudex / "run-old"; stale.mkdir(); (stale / "a.txt").write_text("a")
        _make_old(stale)
        outside = tmp_path / "outside"; outside.mkdir()
        keep = outside / "keep.txt"; keep.write_text("k"); _make_old(outside)
        (claudex / "run-link").symlink_to(outside)
        srv._cleanup_old_run_dirs(claudex)
        assert not stale.exists() and keep.exists() and (claudex / "run-link").is_symlink()

    def test_gitignore_not_created_through_symlink(self, tmp_path):
        import server as srv
        outside = tmp_path / "outside"; outside.mkdir()
        proj = tmp_path / "proj"; proj.mkdir()
        (proj / ".claudex").symlink_to(outside)
        srv._ensure_claudex_ignored(proj / ".claudex")
        assert not (outside / ".gitignore").exists()

    @pytest.mark.asyncio
    async def test_status_ignores_symlinked_subdirs(self, tmp_path):
        import server as srv
        outside = tmp_path / "outside"; outside.mkdir()
        (outside / "secret_recap.md").write_text("x" * 5000)
        (tmp_path / ".claudex").mkdir()
        (tmp_path / ".claudex" / "recaps").symlink_to(outside)
        out = await srv.codex_status(srv.StatusInput(project_dir=str(tmp_path)))
        assert "Recaps: none" in out


class TestV231CollabContinuity:
    @pytest.mark.asyncio
    async def test_existing_session_without_rounds_still_gives_context(self, tmp_path):
        import server as srv
        sessions = tmp_path / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        (sessions / "s1.md").write_text("# Session: s1\nEARLIER-NOTES-MARKER\n")
        seen = {}

        async def capture(prompt, **k):
            seen["prompt"] = prompt
            return "ok"
        with patch.object(srv, "_run_codex", side_effect=capture):
            await srv.codex_collab(srv.CollaborateInput(
                problem="a problem long enough", cc_analysis="analysis long enough",
                project_dir=str(tmp_path), session_id="s1"))
        assert "EARLIER-NOTES-MARKER" in seen["prompt"]

    @pytest.mark.asyncio
    async def test_rollover_recap_failure_is_reported_and_context_kept(self, tmp_path):
        import server as srv
        sessions = tmp_path / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        (sessions / "s1.md").write_text(
            f"# Session: s1\n<!-- claudex:rounds={srv.MAX_SESSION_ROUNDS} -->\n"
            "\n---\n\n## Round 1\n\nDECISION-MARKER\n")
        seen = {}

        async def capture(prompt, **k):
            seen["prompt"] = prompt
            return "fine"
        with patch.object(srv, "_run_codex_once", new_callable=AsyncMock,
                          return_value="Error: recap timed out"), \
             patch.object(srv, "_run_codex", side_effect=capture):
            out = await srv.codex_collab(srv.CollaborateInput(
                problem="a problem long enough", cc_analysis="analysis long enough",
                project_dir=str(tmp_path), session_id="s1"))
        assert "recap failed" in out and "s1-p2" in out
        assert "DECISION-MARKER" in seen["prompt"]

    @pytest.mark.asyncio
    async def test_rollover_success_carries_recap(self, tmp_path):
        import server as srv
        sessions = tmp_path / ".claudex" / "sessions"; sessions.mkdir(parents=True)
        (sessions / "s1.md").write_text(
            f"# Session: s1\n<!-- claudex:rounds={srv.MAX_SESSION_ROUNDS} -->\n")
        seen = {}

        async def capture(prompt, **k):
            seen["prompt"] = prompt
            return "fine"
        with patch.object(srv, "_run_codex_once", new_callable=AsyncMock,
                          return_value="RECAP-MARKER decisions"), \
             patch.object(srv, "_run_codex", side_effect=capture):
            await srv.codex_collab(srv.CollaborateInput(
                problem="a problem long enough", cc_analysis="analysis long enough",
                project_dir=str(tmp_path), session_id="s1"))
        assert "RECAP-MARKER" in seen["prompt"]
        assert (tmp_path / ".claudex" / "recaps" / "s1_recap.md").exists()


class TestV231RolloverState:
    def _at_cap(self, tmp_path, sid="s1"):
        import server as srv
        sessions = tmp_path / ".claudex" / "sessions"; sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f"{sid}.md").write_text(
            f"# Session: {sid}\n<!-- claudex:rounds={srv.MAX_SESSION_ROUNDS} -->\n"
            "\n---\n\n## Round 1\n\nOLD-DECISION\n")
        return sessions

    @pytest.mark.asyncio
    async def test_concurrent_calls_roll_over_once(self, tmp_path):
        import server as srv
        sessions = self._at_cap(tmp_path)
        recap = AsyncMock(return_value="RECAP-ONCE")
        with patch.object(srv, "_run_codex_once", recap), \
             patch.object(srv, "_run_codex", new_callable=AsyncMock, return_value="answer"):
            outs = await asyncio.gather(*[
                srv.codex_collab(srv.CollaborateInput(
                    problem="a problem long enough", cc_analysis="analysis long enough",
                    project_dir=str(tmp_path), session_id="s1"))
                for _ in range(2)])
        assert recap.await_count == 1
        assert all("Session: s1-p2" in o for o in outs)
        assert "continued-in=s1-p2" in (sessions / "s1.md").read_text()
        assert not (sessions / "s1-p3.md").exists()

    @pytest.mark.asyncio
    async def test_carried_decisions_survive_later_rounds(self, tmp_path):
        import server as srv
        self._at_cap(tmp_path)
        prompts = []

        async def capture(prompt, **k):
            prompts.append(prompt)
            return "answer"
        with patch.object(srv, "_run_codex_once", new_callable=AsyncMock, return_value="RECAP-KEEP"), \
             patch.object(srv, "_run_codex", side_effect=capture):
            for _ in range(2):
                await srv.codex_collab(srv.CollaborateInput(
                    problem="a problem long enough", cc_analysis="analysis long enough",
                    project_dir=str(tmp_path), session_id="s1"))
        assert len(prompts) == 2 and all("RECAP-KEEP" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_carried_recap_is_bounded(self, tmp_path):
        import server as srv
        self._at_cap(tmp_path)
        prompts = []

        async def capture(prompt, **k):
            prompts.append(prompt)
            return "answer"
        huge = "R" * (srv.SESSION_MAX_BYTES * 2)
        with patch.object(srv, "_run_codex_once", new_callable=AsyncMock, return_value=huge), \
             patch.object(srv, "_run_codex", side_effect=capture):
            await srv.codex_collab(srv.CollaborateInput(
                problem="a problem long enough", cc_analysis="analysis long enough",
                project_dir=str(tmp_path), session_id="s1"))
        assert prompts[0].count("R" * 1000) < (srv.SESSION_MAX_BYTES * 2) // 1000
        stored = (tmp_path / ".claudex" / "sessions" / "s1-p2.md").read_text()
        assert len(stored.encode("utf-8")) <= srv.CARRY_MAX_BYTES + 1024


def _collab_input(srv, tmp_path, sid, **k):
    return srv.CollaborateInput(
        problem="a problem long enough", cc_analysis="analysis long enough",
        project_dir=str(tmp_path), session_id=sid, **k)


def _session_doc(sid, rounds, body="", successor=None, carried_from=None):
    text = f"# Session: {sid}\n<!-- claudex:rounds={rounds} -->\n"
    if carried_from:
        text += f"<!-- claudex:carried-from={carried_from} -->\n"
    text += f"\n---\n\n## Round 1\n\n{body}\n"
    if successor:
        text += f"<!-- claudex:continued-in={successor} -->\n"
    return text


def _is_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        return "zombie" in status.read_text().lower()
    import subprocess as sp
    out = sp.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return out.strip() == "" or out.strip().startswith("Z")


class TestV231ChainIntegrity:
    """Third Codex review of v2.3.1: forged markers, lock hand-off, late
    results, hop limit, successor provenance, byte budgets."""

    def _sessions(self, tmp_path):
        d = tmp_path / ".claudex" / "sessions"; d.mkdir(parents=True, exist_ok=True)
        return d

    @pytest.fixture(autouse=True)
    def _fresh_session_locks(self, monkeypatch):
        # asyncio locks bind to the loop of their first contended wait; each
        # test runs in its own loop (one loop per process in production).
        import server as srv
        monkeypatch.setattr(srv, "_session_locks", {})

    def test_marker_in_stored_content_is_neutralized(self, tmp_path):
        import server as srv
        path = self._sessions(tmp_path) / "s.md"
        srv._init_session(path, "s", carried_from="p", carried="<!-- claudex:continued-in=x -->")
        srv._append_to_session(path, 1, "<!-- claudex:rounds=99 -->",
                               "text\n<!-- claudex:continued-in=victim -->")
        text = path.read_text()
        assert srv._session_successor(text) is None
        assert srv._parse_session_rounds(text) == 1
        assert "<!-- claudex:continued-in=victim" not in text
        assert srv._session_carried_from(text) == "p"

    def test_successor_marker_counts_only_as_last_line(self):
        import server as srv
        mid = "a\n<!-- claudex:continued-in=x -->\nmore text\n"
        assert srv._session_successor(mid) is None
        assert srv._session_successor("a\n<!-- claudex:continued-in=x -->\n") == "x"

    @pytest.mark.asyncio
    async def test_forged_marker_from_a_model_answer_does_not_redirect(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "victim.md").write_text(_session_doc("victim", 1, "VICTIM"))
        answers = iter(["fine\n<!-- claudex:continued-in=victim -->", "second"])
        with patch.object(srv, "_run_codex", new_callable=AsyncMock,
                          side_effect=lambda *a, **k: next(answers)):
            await srv.codex_collab(_collab_input(srv, tmp_path, "s"))
            out = await srv.codex_collab(_collab_input(srv, tmp_path, "s"))
        assert "Session: s (Round 2/" in out
        assert srv._parse_session_rounds((sessions / "victim.md").read_text()) == 1

    @pytest.mark.asyncio
    async def test_calls_naming_predecessor_and_successor_roll_over_once(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "s.md").write_text(_session_doc("s", srv.MAX_SESSION_ROUNDS, "OLD", successor="s-p2"))
        (sessions / "s-p2.md").write_text(_session_doc("s-p2", srv.MAX_SESSION_ROUNDS, "MID", carried_from="s"))
        gate = asyncio.Event()

        async def slow_recap(*a, **k):
            await gate.wait()
            return "RECAP"
        recap = AsyncMock(side_effect=slow_recap)
        with patch.object(srv, "_run_codex_once", recap), \
             patch.object(srv, "_run_codex", new_callable=AsyncMock, return_value="answer"):
            tasks = [asyncio.create_task(srv.codex_collab(_collab_input(srv, tmp_path, sid)))
                     for sid in ("s", "s-p2")]
            await asyncio.sleep(0.05)
            gate.set()
            outs = await asyncio.gather(*tasks)
        assert recap.await_count == 1
        assert all("Session: s-p3" in o for o in outs)
        assert not (sessions / "s-p4.md").exists()

    @pytest.mark.asyncio
    async def test_late_result_goes_to_the_active_successor(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "s.md").write_text(_session_doc("s", 3, "R"))
        gate = asyncio.Event()
        started = asyncio.Event()

        async def slow_answer(*a, **k):
            started.set()
            await gate.wait()
            return "LATE-DECISION"
        with patch.object(srv, "_run_codex", side_effect=slow_answer):
            task = asyncio.create_task(srv.codex_collab(_collab_input(srv, tmp_path, "s")))
            await started.wait()
            # Meanwhile the session filled up and rolled over to s-p2.
            (sessions / "s-p2.md").write_text(_session_doc("s-p2", 0, "CARRY", carried_from="s"))
            (sessions / "s.md").write_text(
                _session_doc("s", srv.MAX_SESSION_ROUNDS, "R", successor="s-p2"))
            gate.set()
            out = await task
        assert "LATE-DECISION" in (sessions / "s-p2.md").read_text()
        assert "LATE-DECISION" not in (sessions / "s.md").read_text()
        assert "added to 's-p2'" in out and "Session: s-p2" in out

    @pytest.mark.asyncio
    async def test_chain_loop_is_an_error(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "a.md").write_text(_session_doc("a", 1, successor="b"))
        (sessions / "b.md").write_text(_session_doc("b", 1, successor="a"))
        with patch.object(srv, "_run_codex", new_callable=AsyncMock, return_value="x") as run:
            out = await srv.codex_collab(_collab_input(srv, tmp_path, "a"))
        assert out.startswith("Error:") and "loops" in out and run.await_count == 0

    @pytest.mark.asyncio
    async def test_over_long_chain_is_an_error_not_an_old_session(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        ids = ["s"] + [f"s-p{i}" for i in range(2, srv._SESSION_CHAIN_MAX_HOPS + 4)]
        for cur, nxt in zip(ids, ids[1:]):
            (sessions / f"{cur}.md").write_text(_session_doc(cur, 4, successor=nxt))
        (sessions / f"{ids[-1]}.md").write_text(_session_doc(ids[-1], 1))
        with patch.object(srv, "_run_codex", new_callable=AsyncMock, return_value="x") as run:
            out = await srv.codex_collab(_collab_input(srv, tmp_path, "s"))
        assert out.startswith("Error:") and "longer than" in out and run.await_count == 0

    @pytest.mark.asyncio
    async def test_unrelated_existing_successor_is_not_adopted(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "s.md").write_text(_session_doc("s", srv.MAX_SESSION_ROUNDS, "OLD"))
        unrelated = _session_doc("s-p2", 1, "UNRELATED")
        (sessions / "s-p2.md").write_text(unrelated)
        with patch.object(srv, "_run_codex_once", new_callable=AsyncMock, return_value="NEW-CARRY"), \
             patch.object(srv, "_run_codex", new_callable=AsyncMock, return_value="answer"):
            out = await srv.codex_collab(_collab_input(srv, tmp_path, "s"))
        assert "Session: s-p3" in out
        assert (sessions / "s-p2.md").read_text() == unrelated
        assert "NEW-CARRY" in (sessions / "s-p3.md").read_text()
        assert srv._session_successor((sessions / "s.md").read_text()) == "s-p3"

    @pytest.mark.asyncio
    async def test_missing_successor_is_recreated_with_context(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "s.md").write_text(
            _session_doc("s", srv.MAX_SESSION_ROUNDS, "KEEP-ME", successor="s-p2"))
        prompts = []

        async def capture(prompt, **k):
            prompts.append(prompt)
            return "answer"
        with patch.object(srv, "_run_codex", side_effect=capture):
            out = await srv.codex_collab(_collab_input(srv, tmp_path, "s"))
        assert "KEEP-ME" in prompts[0] and "was missing" in out
        assert srv._session_carried_from((sessions / "s-p2.md").read_text()) == "s"

    @pytest.mark.parametrize("unit", ["a", "א"])
    def test_truncate_utf8_stays_within_the_byte_budget(self, unit):
        import server as srv
        out = srv._truncate_utf8(unit * 64_000, srv.SESSION_MAX_BYTES)
        assert len(out.encode("utf-8")) <= srv.SESSION_MAX_BYTES

    @pytest.mark.parametrize("unit", ["a", "א"])
    def test_large_carry_never_starves_the_newest_round(self, tmp_path, unit):
        import server as srv
        path = self._sessions(tmp_path) / "s.md"
        srv._init_session(path, "s", carried_from="p", carried=unit * 100_000)
        for n in range(1, 4):
            srv._append_to_session(path, n, "analysis", unit * 20_000 + f" DECISION-{n}")
        out = srv._truncate_session_content(path.read_text())
        assert len(out.encode("utf-8")) <= srv.SESSION_MAX_BYTES
        assert "DECISION-3" in out

    @pytest.mark.asyncio
    async def test_cancelled_handoff_keeps_other_calls_lock(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        (sessions / "s.md").write_text(_session_doc("s", 4, successor="s-p2"))
        (sessions / "s-p2.md").write_text(_session_doc("s-p2", 1, carried_from="s"))
        owner = srv._get_session_lock("s-p2")
        await owner.acquire()
        try:
            waiter = asyncio.create_task(srv._lock_active_session(
                str(tmp_path), "s", sessions / "s.md"))
            await asyncio.sleep(0.01)
            assert not waiter.done()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner.locked()
            assert not srv._get_session_lock("s").locked()
        finally:
            owner.release()

    @pytest.mark.asyncio
    async def test_recovered_successor_keeps_the_latest_round(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        pred = sessions / "s-p2.md"
        srv._init_session(pred, "s-p2", carried_from="s", carried="X" * 100_000)
        srv._append_to_session(pred, 4, "LATEST-START", "Y" * 40_000 + "LATEST-END")
        pred.write_text(pred.read_text() + "\n<!-- claudex:continued-in=s-p3 -->\n")
        lock, sid, _, recovered, note = await srv._lock_active_session(str(tmp_path), "s-p2", pred)
        lock.release()
        assert sid == "s-p3" and "was missing" in note
        assert "LATEST-START" in recovered and "LATEST-END" in recovered
        assert len(recovered.encode("utf-8")) <= srv.CARRY_MAX_BYTES + 1024

    @pytest.mark.asyncio
    async def test_failed_recap_rollover_keeps_the_latest_round(self, tmp_path):
        import server as srv
        sessions = self._sessions(tmp_path)
        path = sessions / "s.md"
        srv._init_session(path, "s", carried_from="r", carried="X" * 100_000)
        for n in range(1, srv.MAX_SESSION_ROUNDS + 1):
            srv._append_to_session(path, n, f"START-{n}", "Y" * 9_000 + f"END-{n}")
        prompts = []

        async def capture(prompt, **k):
            prompts.append(prompt)
            return "answer"
        with patch.object(srv, "_run_codex_once", new_callable=AsyncMock,
                          return_value="Error: recap failed"), \
             patch.object(srv, "_run_codex", side_effect=capture):
            out = await srv.codex_collab(_collab_input(srv, tmp_path, "s"))
        last = srv.MAX_SESSION_ROUNDS
        successor = (sessions / "s-p2.md").read_text()
        assert f"END-{last}" in successor and f"START-{last}" in successor
        assert f"END-{last}" in prompts[0] and "recap failed" in out

    @pytest.mark.parametrize("budget", [0, 1, 11, 40, 100])
    def test_tiny_truncation_budgets_hold(self, budget):
        import server as srv
        for fn in (srv._truncate_utf8, srv._truncate_middle_utf8):
            assert len(fn("X" * 500, budget).encode("utf-8")) <= budget

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sid", ["foo bar", "foo@bar", "foo_bar"])
    async def test_any_alias_of_an_id_reaches_its_successor(self, tmp_path, sid):
        # Codex review 5: the raw id ('foo bar') was written into markers the
        # parser could not read, so the next call started a third session.
        import server as srv
        recap = AsyncMock(return_value="RECAP-OF-PREDECESSOR")
        prompts = []

        async def answer(prompt, **kwargs):
            prompts.append(prompt)
            return "FIRST-SUCCESSOR-DECISION"
        path = srv._safe_claudex_path(str(tmp_path), "sessions", sid + ".md")
        srv._init_session(path, srv._canonical_session_id(sid))
        for n in range(1, srv.MAX_SESSION_ROUNDS + 1):
            srv._append_to_session(path, n, "old analysis", "old answer")
        with patch.object(srv, "_run_codex_once", recap), \
             patch.object(srv, "_run_codex", side_effect=answer), \
             patch.object(srv, "_get_git_context", AsyncMock(return_value=None)):
            await srv.codex_collab(_collab_input(srv, tmp_path, sid))
            out = await srv.codex_collab(_collab_input(srv, tmp_path, sid))
        assert "FIRST-SUCCESSOR-DECISION" in prompts[1]
        assert recap.await_count == 1
        assert len(list(path.parent.glob("*.md"))) == 2
        assert "Session: foo_bar-p2 (Round 2/" in out

    def test_posix_without_dir_fd_fails_closed(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.setattr(srv, "_HAVE_DIR_FD", False)
        monkeypatch.setattr(srv, "_PATHWISE_FALLBACK", False)
        sessions = self._sessions(tmp_path)
        old = sessions / "old.md"; old.write_text("x"); _make_old(old)
        with pytest.raises(OSError) as exc:
            srv._read_text_nofollow(old)
        assert exc.value.errno == errno.ENOTSUP
        srv._cleanup_old_sessions(tmp_path / ".claudex")
        assert old.exists()
        assert srv._claudex_usage(tmp_path / ".claudex") == {"unavailable": True}


class TestV231GitLifecycle:
    @pytest.mark.asyncio
    async def test_unborn_head_is_attested_not_failed(self, tmp_path):
        import subprocess as sp
        import server as srv
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "a.py").write_text("x = 1\n")
        sp.run(["git", "add", "a.py"], cwd=repo, check=True)
        out = await srv._get_git_diff(str(repo), staged=True)
        assert out is not None and "HEAD none (no commits yet)" in out

    @pytest.mark.asyncio
    async def test_head_failure_on_committed_repo_is_an_error(self, tmp_path, monkeypatch):
        import subprocess as sp
        import server as srv
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "a.py").write_text("x = 1\n")
        sp.run(["git", "add", "-A"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
               cwd=repo, check=True)
        (repo / "a.py").write_text("x = 2\n")
        real = srv._git_run

        async def flaky(project_dir, *args, **k):
            if args[:1] == ("rev-parse",):
                return None, ""  # timed out / could not run
            return await real(project_dir, *args, **k)
        monkeypatch.setattr(srv, "_git_run", flaky)
        out = await srv._get_git_diff(str(repo))
        assert out.startswith("Error:") and "no commits yet" not in out

    @pytest.mark.asyncio
    async def test_head_of_committed_repo_is_a_sha(self, tmp_path):
        import subprocess as sp
        import server as srv
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                "--allow-empty", "-m", "i"], cwd=repo, check=True)
        att = await srv._head_attestation(str(repo))
        assert att and len(att) == 40

    @pytest.mark.asyncio
    async def test_git_timeout_kills_the_whole_process_group(self, tmp_path):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX only")
        pidfile = tmp_path / "child.pid"
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        child = int(pidfile.read_text())
        with pytest.raises(asyncio.TimeoutError):
            await srv._communicate_or_kill(proc, 0.2)
        await asyncio.sleep(0.2)
        try:
            os.kill(child, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:
            # A zombie still answers kill(0); reaped or zombie both mean killed.
            status = Path(f"/proc/{child}/status")
            alive = not (status.exists() and "zombie" in status.read_text().lower())
        assert not alive

    @pytest.mark.asyncio
    async def test_group_is_killed_after_its_leader_exited(self, tmp_path):
        # A filter's background child keeps the pipes open after git exits.
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX only")
        pidfile = tmp_path / "bg.pid"
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", f"sleep 30 & echo $! > {pidfile}; exit 0",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        bg = int(pidfile.read_text())
        with pytest.raises(asyncio.TimeoutError):
            await srv._communicate_or_kill(proc, 0.3)
        for _ in range(20):
            if _is_dead(bg):
                break
            await asyncio.sleep(0.05)
        assert _is_dead(bg)

    def test_run_cleanup_survives_vanishing_entries(self, tmp_path, monkeypatch):
        import server as srv
        claudex = tmp_path / ".claudex"; claudex.mkdir()
        real_scandir = srv.os.scandir

        class Vanishing:
            name = "run-gone"
            def is_dir(self, follow_symlinks=True):
                return True
            def stat(self, follow_symlinks=True):
                raise FileNotFoundError("gone")

        class Ctx:
            def __init__(self, it): self.it = it
            def __enter__(self): return iter(self.it)
            def __exit__(self, *a): return False

        def fake_scandir(fd):
            with real_scandir(fd) as it:
                items = list(it)
            return Ctx([Vanishing(), *items])
        monkeypatch.setattr(srv.os, "scandir", fake_scandir)
        srv._cleanup_old_run_dirs(claudex)  # must not raise

    @pytest.mark.asyncio
    async def test_status_reports_symlinked_claudex(self, tmp_path):
        import server as srv
        outside = tmp_path / "outside"; (outside / "recaps").mkdir(parents=True)
        (outside / "recaps" / "r.md").write_text("x")
        proj = tmp_path / "proj"; proj.mkdir()
        (proj / ".claudex").symlink_to(outside)
        out = await srv.codex_status(srv.StatusInput(project_dir=str(proj)))
        assert "is a symlink: ignored" in out and "Recaps: none" in out

    @pytest.mark.asyncio
    async def test_git_timeout_kills_the_process(self, monkeypatch):
        import server as srv

        class Hung:
            returncode = None
            killed = False
            async def communicate(self):
                await asyncio.sleep(60)
            def kill(self):
                Hung.killed = True
            async def wait(self):
                return -9

        async def spawn(*a, **k):
            return Hung()
        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", spawn)
        out = await srv._git_cmd(str(Path.cwd()), "status", timeout=0.05)
        assert out is None and Hung.killed


class TestV231DiffFailures:
    @pytest.mark.asyncio
    async def test_not_a_repo_is_an_error_not_nothing_to_review(self, tmp_path):
        from server import codex_review_diff, ReviewDiffInput
        plain = tmp_path / "plain"; plain.mkdir()
        (plain / "a.py").write_text("x = 1\n")
        out = await codex_review_diff(ReviewDiffInput(project_dir=str(plain)))
        assert out.startswith("Error:")
        assert "Nothing was reviewed" in out
        assert "Nothing to review" not in out

    @pytest.mark.asyncio
    async def test_git_missing_is_an_error(self, tmp_path, monkeypatch):
        import server as srv
        repo = tmp_path / "r"; repo.mkdir()

        async def no_git(*a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(srv.asyncio, "create_subprocess_exec", no_git)
        out = await srv._get_git_diff(str(repo))
        assert out.startswith("Error:") and "Nothing was reviewed" in out

    @pytest.mark.asyncio
    async def test_untracked_probe_failure_is_an_error(self, tmp_path, monkeypatch):
        import server as srv
        repo = tmp_path / "r"; repo.mkdir()
        monkeypatch.setattr(srv, "_get_git_diff", AsyncMock(return_value=None))
        monkeypatch.setattr(srv, "_git_cmd", AsyncMock(return_value=None))
        out = await srv.codex_review_diff(srv.ReviewDiffInput(project_dir=str(repo)))
        assert out.startswith("Error:") and "Nothing to review" not in out

    @pytest.mark.asyncio
    async def test_clean_repo_still_reports_nothing_to_review(self, tmp_path):
        import subprocess as sp
        from server import codex_review_diff, ReviewDiffInput
        repo = tmp_path / "r"; repo.mkdir()
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                "--allow-empty", "-m", "i"], cwd=repo, check=True)
        out = await codex_review_diff(ReviewDiffInput(project_dir=str(repo)))
        assert "Nothing to review" in out


def test_server_version_matches_manifests():
    import server as srv
    plugin = json.loads((PROJECT_ROOT / ".claude-plugin" / "plugin.json").read_text())
    ext = json.loads((PROJECT_ROOT / "desktop-extension" / "manifest.json").read_text())
    assert srv.SERVER_VERSION == plugin["version"] == ext["version"]


# =========================================================================
# v2.4: roots sources, config file, kill switch, CLI, lockfile
# =========================================================================

def _write_cfg(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


class TestV24ConfigIntegrity:
    """Codex red team of v2.4: HOME/APPDATA redirection, config I/O identity,
    strict schema, cached results after revocation, .gitignore claims."""

    @pytest.fixture
    def acct(self, tmp_path, monkeypatch):
        # The OS account's home is tmp/acct; $HOME points somewhere else.
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX account database")
        acct = tmp_path / "acct"; acct.mkdir()
        fake = tmp_path / "fakehome"; fake.mkdir()
        monkeypatch.setattr(srv, "_account_home", lambda: acct)
        monkeypatch.setenv("HOME", str(fake))
        monkeypatch.setattr(srv, "_config_path", srv._default_config_path)
        return acct

    def test_account_home_ignores_HOME(self, tmp_path, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        before = srv._default_config_path()
        monkeypatch.setenv("HOME", str(tmp_path))
        assert srv._account_home() != tmp_path
        assert srv._default_config_path() == before

    def test_revocation_holds_when_HOME_is_redirected(self, tmp_path, acct, monkeypatch):
        import server as srv
        _write_cfg(srv._default_config_path(), {"version": 1, "deny_all": True, "allowed_roots": []})
        assert str(acct) in str(srv._default_config_path())
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
        assert srv._roots_resolution().source == "revoked"

    def test_protected_dirs_of_the_account_home_stay_protected(self, tmp_path, acct, monkeypatch):
        import server as srv
        ssh = acct / ".ssh" / "keys"; ssh.mkdir(parents=True)
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
        with pytest.raises(ValueError, match="protected"):
            srv._validate_project_dir(str(ssh))
        with pytest.raises(ValueError, match="entire home"):
            srv._validate_project_dir(str(acct))

    @pytest.mark.parametrize("kind", ["dangling", "elsewhere"])
    def test_symlinked_config_file_denies(self, tmp_path, kind, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        cfg = srv._config_path(); cfg.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(cfg.parent, 0o700)
        target = tmp_path / "other.json"
        if kind == "elsewhere":
            target.write_text(json.dumps({"version": 1, "allowed_roots": [str(tmp_path)]}))
        cfg.symlink_to(target)
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        res = srv._roots_resolution()
        assert res.source == "config-error" and res.roots == []

    def test_symlinked_config_folder_denies(self, tmp_path, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        real = tmp_path / "real"; real.mkdir(mode=0o700)
        (real / "config.json").write_text(json.dumps({"version": 1, "allowed_roots": [str(tmp_path)]}))
        link = tmp_path / "linkdir"; link.symlink_to(real)
        monkeypatch.setattr(srv, "_config_path", lambda: link / "config.json")
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        assert srv._roots_resolution().source == "config-error"

    def test_config_writable_by_others_denies(self, tmp_path, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        cfg = srv._config_path()
        _write_cfg(cfg, {"version": 1, "allowed_roots": [str(tmp_path)]})
        os.chmod(cfg, 0o666)
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        res = srv._roots_resolution()
        assert res.source == "config-error" and "other accounts" in res.detail

    def test_fifo_and_oversized_config_deny_without_blocking(self, tmp_path, monkeypatch):
        import server as srv
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs")
        cfg = srv._config_path(); cfg.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(cfg.parent, 0o700)
        os.mkfifo(cfg, 0o600)
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        assert srv._roots_resolution().source == "config-error"
        cfg.unlink()
        cfg.write_text(json.dumps({"version": 1, "allowed_roots": [], "pad": "x" * 70_000}))
        os.chmod(cfg, 0o600)
        assert srv._roots_resolution().source == "config-error"

    @pytest.mark.parametrize("doc", [
        {"version": 1, "deny_all": "true", "allowed_roots": []},
        {"version": 1, "deny_all": 1, "allowed_roots": []},
        {"version": True, "allowed_roots": []},
        {"version": 1, "allowed_roots": ["/tmp/a\x00b"]},
    ])
    def test_malformed_documents_deny_and_never_fall_through(self, tmp_path, doc, monkeypatch):
        import server as srv
        _write_cfg(srv._config_path(), doc)
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
        res = srv._roots_resolution()
        assert res.source == "config-error" and res.roots == []

    def test_writer_refuses_a_symlinked_folder(self, tmp_path, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        real = tmp_path / "victim"; real.mkdir(mode=0o700)
        link = tmp_path / "cfgdir"; link.symlink_to(real)
        monkeypatch.setattr(srv, "_config_path", lambda: link / "config.json")
        with pytest.raises(OSError):
            srv._write_roots_config({"deny_all": True, "allowed_roots": []})
        assert list(real.iterdir()) == []

    def test_writer_makes_private_files(self, tmp_path):
        import server as srv
        path = srv._write_roots_config({"deny_all": False, "allowed_roots": [str(tmp_path)]})
        if os.name != "nt":
            assert stat_mode(path) == 0o600 and stat_mode(path.parent) == 0o700
        assert srv._load_roots_config()[0] == "ok"

    @pytest.mark.asyncio
    async def test_revocation_withholds_cached_results(self, tmp_path, monkeypatch):
        import server as srv
        proj = tmp_path / "p"; proj.mkdir()
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
        job_id = "job-0123456789ab"
        srv._jobs[job_id] = {"project_dir": str(proj), "tool": "plan", "status": "completed",
                             "submitted": 0.0, "finished": 1.0, "started_running": 0.0,
                             "result": "SECRET-RESULT"}
        try:
            ok = await srv.codex_result(srv.JobResultInput(job_id=job_id))
            assert ok == "SECRET-RESULT"
            srv._write_roots_config({"deny_all": True, "allowed_roots": []})
            denied = await srv.codex_result(srv.JobResultInput(job_id=job_id))
            listing = await srv.codex_result(srv.JobResultInput(job_id="list"))
        finally:
            srv._jobs.pop(job_id, None)
        assert denied.startswith("Error:") and "SECRET-RESULT" not in denied
        assert str(proj) not in listing and "no longer allowed" in listing

    @pytest.mark.parametrize("body,state", [
        ("", "unverified"),
        ("*\n!*\n", "unverified"),
        ("*\n!sessions/\n", "unverified"),
        ("# Created by Claudex\n*\n", "ok"),
    ])
    def test_gitignore_state_is_ok_only_for_exactly_star(self, tmp_path, body, state):
        import server as srv
        claudex = tmp_path / ".claudex"; claudex.mkdir()
        (claudex / ".gitignore").write_text(body)
        assert srv._claudex_ignore_state(claudex) == state

    def test_gitignore_missing_is_reported(self, tmp_path):
        import server as srv
        claudex = tmp_path / ".claudex"; claudex.mkdir()
        assert srv._claudex_ignore_state(claudex) == "missing"

    @pytest.mark.asyncio
    async def test_revocation_during_a_result_wait_withholds_it(self, tmp_path, monkeypatch):
        import server as srv
        proj = tmp_path / "p"; proj.mkdir()
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
        job_id = "job-0123456789ac"
        srv._jobs[job_id] = {"project_dir": str(proj), "tool": "plan", "status": "running",
                             "submitted": 0.0, "finished": None, "started_running": 0.0,
                             "result": None}

        async def finish_after_revoke():
            await asyncio.sleep(0.05)
            srv._write_roots_config({"deny_all": True, "allowed_roots": []})
            srv._jobs[job_id].update(status="completed", result="SECRET-LATE", finished=1.0)
        srv._job_tasks[job_id] = asyncio.create_task(finish_after_revoke())
        try:
            out = await srv.codex_result(srv.JobResultInput(job_id=job_id, wait_seconds=5))
        finally:
            srv._jobs.pop(job_id, None)
            srv._job_tasks.pop(job_id, None)
        assert out.startswith("Error:") and "SECRET-LATE" not in out

    def test_writable_ancestor_denies_even_without_a_file(self, tmp_path, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        shared = tmp_path / "shared"; shared.mkdir()
        os.chmod(shared, 0o777)
        monkeypatch.setattr(srv, "_config_path", lambda: shared / "botique-claudex" / "config.json")
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(tmp_path))
        res = srv._roots_resolution()
        assert res.source == "config-error" and "writable by other accounts" in res.detail
        with pytest.raises(OSError):
            srv._write_roots_config({"deny_all": False, "allowed_roots": [str(tmp_path)]})

    def test_symlink_inside_a_writable_folder_denies(self, tmp_path, monkeypatch):
        import server as srv
        if os.name == "nt":
            pytest.skip("POSIX")
        safe = tmp_path / "safe" / "botique-claudex"; safe.mkdir(parents=True, mode=0o700)
        shared = tmp_path / "shared"; shared.mkdir(); os.chmod(shared, 0o777)
        (shared / "cfg").symlink_to(tmp_path / "safe")
        monkeypatch.setattr(srv, "_config_path", lambda: shared / "cfg" / "botique-claudex" / "config.json")
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        assert srv._roots_resolution().source == "config-error"

    def test_prompts_do_not_claim_verbatim(self):
        import server as srv
        src = Path(srv.__file__).read_text()
        assert "Original User Request (verbatim" not in src


def stat_mode(path):
    import stat as _stat
    return _stat.S_IMODE(os.stat(path).st_mode)


class TestV24RootsResolution:
    @pytest.fixture
    def cfg(self, tmp_path):
        import server as srv
        return srv._config_path()

    @pytest.fixture
    def clean_env(self, monkeypatch):
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)

    def test_none_configured_denies(self, clean_env):
        import server as srv
        res = srv._roots_resolution()
        assert res.roots == [] and res.source == "none"

    def test_config_file_supplies_roots(self, tmp_path, cfg, clean_env):
        import server as srv
        proj = tmp_path / "projects"; proj.mkdir()
        _write_cfg(cfg, {"version": 1, "allowed_roots": [str(proj)]})
        res = srv._roots_resolution()
        assert res.roots == [proj.resolve()] and res.source == "config-file"
        assert srv._validate_project_dir(str(proj)) == str(proj.resolve())

    def test_env_beats_config_file(self, tmp_path, cfg, monkeypatch):
        import server as srv
        a = tmp_path / "a"; a.mkdir(); b = tmp_path / "b"; b.mkdir()
        _write_cfg(cfg, {"version": 1, "allowed_roots": [str(a)]})
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(b))
        res = srv._roots_resolution()
        assert res.source == "env" and res.roots == [b.resolve()]

    def test_no_plugin_folder_source(self, tmp_path, cfg, clean_env, monkeypatch):
        # The plugin folder setting was removed before release; a stray env
        # var from a pre-release build must not grant a root.
        import server as srv
        a = tmp_path / "a"; a.mkdir(); b = tmp_path / "b"; b.mkdir()
        _write_cfg(cfg, {"version": 1, "allowed_roots": [str(a)]})
        monkeypatch.setenv("CLAUDEX_PLUGIN_FOLDER", str(b))
        res = srv._roots_resolution()
        assert res.source == "config-file" and res.roots == [a.resolve()]
        assert not hasattr(srv, "PLUGIN_FOLDER_ENV")

    def test_revocation_beats_everything(self, tmp_path, cfg, monkeypatch):
        import server as srv
        a = tmp_path / "a"; a.mkdir()
        _write_cfg(cfg, {"version": 1, "deny_all": True, "allowed_roots": []})
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(a))
        monkeypatch.setattr(srv, "_ARGV_ROOTS", [str(a)])
        res = srv._roots_resolution()
        assert res.roots == [] and res.source == "revoked"
        with pytest.raises(ValueError, match="switched off"):
            srv._validate_project_dir(str(a))

    @pytest.mark.parametrize("content", [
        "{not json",
        json.dumps({"version": 99, "allowed_roots": []}),
        json.dumps({"version": 1, "allowed_roots": "nope"}),
        json.dumps({"version": 1, "allowed_roots": ["relative/x"]}),
        json.dumps(["just", "a", "list"]),
    ])
    def test_malformed_config_denies_everything(self, tmp_path, cfg, monkeypatch, content):
        import server as srv
        a = tmp_path / "a"; a.mkdir()
        cfg.parent.mkdir(parents=True, exist_ok=True); cfg.write_text(content)
        monkeypatch.setenv("CLAUDEX_ALLOWED_ROOTS", str(a))
        res = srv._roots_resolution()
        assert res.roots == [] and res.source == "config-error"

    def test_cloud_default_still_last(self, tmp_path, clean_env, monkeypatch):
        import server as srv
        proj = tmp_path / "cloudproj"; proj.mkdir()
        monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        assert srv._roots_resolution().source == "cloud-default"

    def test_config_path_ignores_state_and_plugin_data_env(self, monkeypatch):
        import importlib
        import server as srv
        real = srv.__dict__["_config_path"]
        # The autouse fixture patched the module attribute; check the original
        # function through a fresh import of the source.
        spec = importlib.util.spec_from_file_location("server_fresh", srv.__file__)
        fresh = importlib.util.module_from_spec(spec); spec.loader.exec_module(fresh)
        monkeypatch.setenv("CLAUDEX_STATE_DIR", "/tmp/elsewhere")
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/tmp/plugin-data")
        path = str(fresh._config_path())
        assert "elsewhere" not in path and "plugin-data" not in path
        assert path.endswith("config.json")
        del real


class TestV24RootsCli:
    def _cli(self, *args):
        import server as srv
        return srv._roots_cli(list(args))

    def test_configure_writes_resolved_roots(self, tmp_path, capsys):
        import server as srv
        proj = tmp_path / "projects"; proj.mkdir()
        assert self._cli("--configure-roots", str(proj)) == 0
        data = json.loads(srv._config_path().read_text())
        assert data["allowed_roots"] == [str(proj.resolve())] and data["deny_all"] is False
        assert oct(srv._config_path().stat().st_mode & 0o777) == "0o600"

    @pytest.mark.parametrize("bad", ["relative", "/definitely/missing/dir", "/"])
    def test_configure_rejects_bad_folders_and_writes_nothing(self, bad, tmp_path, capsys):
        import server as srv
        assert self._cli("--configure-roots", bad) == 2
        assert not srv._config_path().exists()

    def test_configure_rejects_home_and_protected(self, tmp_path, monkeypatch):
        import server as srv
        home = tmp_path / "home"; (home / ".ssh").mkdir(parents=True)
        monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("HOME", str(home))
        assert self._cli("--configure-roots", str(home)) == 2
        assert self._cli("--configure-roots", str(home / ".ssh")) == 2
        assert not srv._config_path().exists()

    def test_revoke_then_reconfigure(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        proj = tmp_path / "p"; proj.mkdir()
        assert self._cli("--revoke-roots") == 0
        assert srv._roots_resolution().source == "revoked"
        assert self._cli("--configure-roots", str(proj)) == 0
        assert srv._roots_resolution().source == "config-file"

    def test_show_roots(self, capsys):
        assert self._cli("--show-roots") == 0
        out = capsys.readouterr().out
        assert "Config file:" in out and "This shell resolves:" in out

    def test_cli_not_exposed_as_tool(self):
        import server as srv
        names = {t.name for t in srv.mcp._tool_manager.list_tools()}
        assert not any("root" in n for n in names)


class TestV24Diagnostics:
    @pytest.mark.asyncio
    async def test_status_reports_source_build_and_config(self, tmp_path, monkeypatch):
        import server as srv
        monkeypatch.setenv("CLAUDEX_DISTRIBUTION", "mcpb")
        out = await srv.codex_status(srv.StatusInput())
        assert "(source: CLAUDEX_ALLOWED_ROOTS)" in out
        assert "desktop extension (.mcpb)" in out
        assert "Server file:" in out and "Roots config:" in out
        assert f"build {srv._build_id()}" in out

    @pytest.mark.asyncio
    async def test_status_deny_all_shows_configure_command(self, monkeypatch):
        import server as srv
        monkeypatch.delenv("CLAUDEX_ALLOWED_ROOTS", raising=False)
        out = await srv.codex_status(srv.StatusInput())
        assert "DENY-ALL" in out and "--configure-roots" in out


class TestV24Lockfile:
    def _uv_new_enough(self):
        uv = shutil.which("uv")
        if not uv:
            return None
        out = subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.split()
        import server as srv
        return uv if len(out) > 1 and srv._version_at_least(out[1], "0.11.4") else None

    def test_metadata_declares_uv_floor(self):
        head = (PROJECT_ROOT / "server" / "server.py").read_text().split("# ///\n")[0]
        assert 'required-version = ">=0.11.4"' in head

    def test_lockfile_shipped_and_small_enough_for_directory(self):
        lock = PROJECT_ROOT / "server" / "server.py.lock"
        assert lock.is_file()
        assert lock.stat().st_size < 256 * 1024  # directory holds non-image files >= 256 KiB

    def test_lockfile_is_fresh(self):
        uv = self._uv_new_enough()
        if not uv:
            pytest.skip("uv >= 0.11.4 not on PATH")
        r = subprocess.run([uv, "lock", "--script", str(PROJECT_ROOT / "server" / "server.py"), "--check"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_every_launcher_is_locked(self):
        for rel in ("install.sh", "cloud/setup.sh"):
            text = (PROJECT_ROOT / rel).read_text()
            assert "uv sync --locked --script" in text, rel
        mf = json.loads((PROJECT_ROOT / "desktop-extension" / "manifest.json").read_text())
        launcher = mf["server"]["mcp_config"]["args"][1]
        assert 'exec uv run --locked --script "${__dirname}/server/server.py"' in launcher
        assert "CLAUDEX_DISTRIBUTION=mcpb" in launcher

    def test_mcpb_bundle_carries_the_lock(self, tmp_path):
        if not shutil.which("zip"):
            pytest.skip("zip not installed")
        work = tmp_path / "repo"
        shutil.copytree(PROJECT_ROOT / "desktop-extension", work / "desktop-extension")
        (work / "server").mkdir()
        for name in ("server.py", "server.py.lock"):
            shutil.copy(PROJECT_ROOT / "server" / name, work / "server" / name)
        env = {**os.environ, "CLAUDEX_SKIP_MCPB_VALIDATE": "1"}
        r = subprocess.run(["bash", str(work / "desktop-extension" / "build.sh")],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        import zipfile
        names = zipfile.ZipFile(work / "desktop-extension" / "claudex.mcpb").namelist()
        assert "server/server.py.lock" in names and "server/server.py" in names

    def test_build_refuses_without_lock(self, tmp_path):
        work = tmp_path / "repo"
        shutil.copytree(PROJECT_ROOT / "desktop-extension", work / "desktop-extension")
        (work / "server").mkdir()
        shutil.copy(PROJECT_ROOT / "server" / "server.py", work / "server" / "server.py")
        env = {**os.environ, "CLAUDEX_SKIP_MCPB_VALIDATE": "1"}
        r = subprocess.run(["bash", str(work / "desktop-extension" / "build.sh")],
                           capture_output=True, text=True, env=env)
        assert r.returncode != 0 and "server.py.lock missing" in r.stderr


# =========================================================================
# Entry point for uv run --script
# =========================================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
