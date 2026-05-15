from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.git_server.server import GitMCPServer
from testagent.mcp_servers.git_server.tools import git_blame, git_diff, git_log


@pytest.fixture()
def server() -> GitMCPServer:
    return GitMCPServer()


class TestGitDiff:
    async def test_diff_returns_output(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"diff --git a/file.py b/file.py\n+new line", b""))
            mock_subprocess.return_value = mock_proc

            result = await git_diff(repo_path="/fake/repo", commit_a="abc123")

        assert result["exit_code"] == 0
        assert "diff --git" in result["output"]
        assert "+new line" in result["output"]

    async def test_diff_with_cached_flag(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"staged changes", b""))
            mock_subprocess.return_value = mock_proc

            await git_diff(repo_path="/fake/repo", cached=True)

        call_args = mock_subprocess.call_args
        assert call_args[0][0] == "git"
        git_args = list(call_args[0][1:])
        assert "diff" in git_args
        assert "--cached" in git_args

    async def test_diff_with_commit_range(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"diff between commits", b""))
            mock_subprocess.return_value = mock_proc

            await git_diff(repo_path="/fake/repo", commit_a="abc123", commit_b="def456")

        call_args = mock_subprocess.call_args
        git_args = list(call_args[0][1:])
        assert any("abc123..def456" in a for a in git_args)

    async def test_diff_with_specific_path(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"diff for file", b""))
            mock_subprocess.return_value = mock_proc

            await git_diff(repo_path="/fake/repo", path="src/main.py")

        call_args = mock_subprocess.call_args
        git_args = list(call_args[0][1:])
        assert "--" in git_args
        idx = git_args.index("--")
        assert git_args[idx + 1] == "src/main.py"

    async def test_diff_invalid_repo_returns_error(self) -> None:
        with patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=False):
            result = await git_diff(repo_path="/nonexistent/repo")

        assert "error" in result
        assert "does not exist" in result["error"]

    async def test_diff_git_not_found_returns_error(self) -> None:
        target = "testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec"
        with (
            patch(target, side_effect=FileNotFoundError),
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            result = await git_diff(repo_path="/fake/repo")

        assert "error" in result
        assert "Git executable not found" in result["error"]

    async def test_diff_timeout_returns_error(self) -> None:
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            result = await git_diff(repo_path="/fake/repo")

        assert "error" in result
        assert "timed out" in result["error"]
        mock_proc.kill.assert_called_once()


class TestGitBlame:
    async def test_blame_returns_output(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"abc123 (Author 2024-01-01) line content", b""))
            mock_subprocess.return_value = mock_proc

            result = await git_blame(repo_path="/fake/repo", file_path="src/main.py")

        assert result["exit_code"] == 0
        assert "abc123" in result["output"]

    async def test_blame_with_line_range(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"blame output", b""))
            mock_subprocess.return_value = mock_proc

            await git_blame(repo_path="/fake/repo", file_path="src/main.py", start_line=10, end_line=20)

        call_args = mock_subprocess.call_args
        git_args = list(call_args[0][1:])
        assert any("-L10,20" in a for a in git_args)

    async def test_blame_sends_correct_command(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"blame output", b""))
            mock_subprocess.return_value = mock_proc

            await git_blame(repo_path="/fake/repo", file_path="app/utils.py")

        call_args = mock_subprocess.call_args
        assert call_args[0][0] == "git"
        git_args = list(call_args[0][1:])
        assert "blame" in git_args
        assert "--" in git_args
        idx = git_args.index("--")
        assert git_args[idx + 1] == "app/utils.py"


class TestGitLog:
    async def test_log_returns_commits(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"abc123 commit message\n def456 another commit", b""))
            mock_subprocess.return_value = mock_proc

            result = await git_log(repo_path="/fake/repo", max_count=5)

        assert result["exit_code"] == 0
        assert "abc123" in result["output"]

    async def test_log_with_branch_and_author(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"log output", b""))
            mock_subprocess.return_value = mock_proc

            await git_log(
                repo_path="/fake/repo",
                branch="main",
                author="John",
                since="2024-01-01",
                until="2024-12-31",
            )

        call_args = mock_subprocess.call_args
        assert call_args[0][0] == "git"
        git_args = list(call_args[0][1:])
        assert "log" in git_args
        assert "--max-count=10" in git_args
        assert "main" in git_args
        assert "--since" in git_args
        since_idx = git_args.index("--since")
        assert git_args[since_idx + 1] == "2024-01-01"
        assert "--until" in git_args
        until_idx = git_args.index("--until")
        assert git_args[until_idx + 1] == "2024-12-31"
        assert "--author" in git_args
        author_idx = git_args.index("--author")
        assert git_args[author_idx + 1] == "John"

    async def test_log_with_custom_format(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"%h %s", b""))
            mock_subprocess.return_value = mock_proc

            await git_log(repo_path="/fake/repo", max_count=20, format_str="%h %s")

        call_args = mock_subprocess.call_args
        git_args = list(call_args[0][1:])
        assert "--max-count=20" in git_args
        assert "--format=%h %s" in git_args

    async def test_log_with_file_path(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"log for file", b""))
            mock_subprocess.return_value = mock_proc

            await git_log(repo_path="/fake/repo", file_path="README.md")

        call_args = mock_subprocess.call_args
        git_args = list(call_args[0][1:])
        assert "--" in git_args
        idx = git_args.index("--")
        assert git_args[idx + 1] == "README.md"

    async def test_log_git_error_returns_error(self) -> None:
        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec") as mock_subprocess,
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 128
            mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repository"))
            mock_subprocess.return_value = mock_proc

            result = await git_log(repo_path="/fake/repo")

        assert "error" in result
        assert "exit code 128" in result["error"]


class TestGitMCPServer:
    def test_server_name_is_git_server(self, server: GitMCPServer) -> None:
        assert server.server_name == "git_server"

    async def test_list_tools_returns_three_tools(self, server: GitMCPServer) -> None:
        tools = await server.list_tools()
        assert len(tools) == 3
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"git_diff", "git_blame", "git_log"}

    async def test_list_tools_input_schemas_have_required_fields(self, server: GitMCPServer) -> None:
        tools = await server.list_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "required" in tool["inputSchema"]

    async def test_call_tool_git_diff_dispatches(self, server: GitMCPServer) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"diff output", b""))

        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            raw_result = await server.call_tool(
                "git_diff",
                {"repo_path": "/fake/repo", "commit_a": "abc123"},
            )

        result = json.loads(str(raw_result))
        assert result["exit_code"] == 0
        assert result["output"] == "diff output"

    async def test_call_tool_git_blame_dispatches(self, server: GitMCPServer) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"blame output", b""))

        with (
            patch("testagent.mcp_servers.git_server.tools.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("testagent.mcp_servers.git_server.tools.os.path.isdir", return_value=True),
        ):
            raw_result = await server.call_tool(
                "git_blame",
                {"repo_path": "/fake/repo", "file_path": "src/main.py"},
            )

        result = json.loads(str(raw_result))
        assert result["exit_code"] == 0

    async def test_call_tool_unknown_tool_returns_error(self, server: GitMCPServer) -> None:
        raw_result = await server.call_tool("unknown_tool", {})
        result = json.loads(str(raw_result))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_list_resources_returns_repo_structure_and_branches(self, server: GitMCPServer) -> None:
        resources = await server.list_resources()
        assert len(resources) == 2
        uris = {r["uri"] for r in resources}
        assert uris == {"repo://structure", "repo://branches"}

    async def test_list_resources_have_required_fields(self, server: GitMCPServer) -> None:
        resources = await server.list_resources()
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "mimeType" in resource
            assert "description" in resource

    async def test_health_check_returns_true_when_git_available(self, server: GitMCPServer) -> None:
        with patch("shutil.which", return_value="/usr/bin/git"):
            result = await server.health_check()
            assert result is True

    async def test_health_check_returns_false_when_git_unavailable(self, server: GitMCPServer) -> None:
        with patch("shutil.which", return_value=None):
            result = await server.health_check()
            assert result is False

    async def test_health_check_handles_exception(self, server: GitMCPServer) -> None:
        with patch("shutil.which", side_effect=Exception("unexpected")):
            result = await server.health_check()
            assert result is False
