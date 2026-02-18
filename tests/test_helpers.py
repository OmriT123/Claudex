# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pytest>=8.0.0",
#     "pytest-asyncio>=0.24.0",
#     "pydantic>=2.0.0",
#     "mcp[cli]>=1.0.0",
# ]
# ///
"""
Tests for Claudex MCP server helpers.

Run with:
    uv run --script tests/test_helpers.py

Or directly:
    uv run pytest tests/test_helpers.py -v
"""

import os
import re
import sys
from pathlib import Path

import pytest

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
    _auto_session_id,
    _check_codex_version,
    _version_cache,
    RequestType,
    COLLAB_PERSONAS,
    MAX_SESSION_ROUNDS,
    SESSION_MAX_BYTES,
)
from pydantic import ValidationError
from server import (
    SecondOpinionInput,
    ParallelPlanInput,
    CollaborateInput,
    StatusInput,
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
# Entry point for uv run --script
# =========================================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
