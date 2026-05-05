from __future__ import annotations

import asyncio
import os
from typing import Any


async def _run_git_command(
    repo_path: str,
    args: list[str],
    timeout: int = 30,
) -> dict[str, Any]:
    if not os.path.isdir(repo_path):
        return {"error": f"Repository path does not exist: {repo_path}"}

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"Git command timed out after {timeout}s"}

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            return {"error": f"Git command failed (exit code {proc.returncode}): {error_msg}"}

        output = stdout.decode("utf-8", errors="replace")
        return {"output": output, "exit_code": proc.returncode}
    except FileNotFoundError:
        return {"error": "Git executable not found. Ensure git is installed and in PATH."}
    except Exception as e:
        return {"error": str(e)}


async def git_diff(
    repo_path: str,
    args: list[str] | None = None,
    commit_a: str | None = None,
    commit_b: str | None = None,
    path: str | None = None,
    cached: bool = False,
) -> dict[str, Any]:
    cmd_args: list[str] = ["diff"]
    if cached:
        cmd_args.append("--cached")
    if commit_a is not None and commit_b is not None:
        cmd_args.append(f"{commit_a}..{commit_b}")
    elif commit_a is not None:
        cmd_args.append(commit_a)
    if args is not None:
        cmd_args.extend(args)
    if path is not None:
        cmd_args.append("--")
        cmd_args.append(path)

    return await _run_git_command(repo_path, cmd_args)


async def git_blame(
    repo_path: str,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    args: list[str] | None = None,
) -> dict[str, Any]:
    cmd_args: list[str] = ["blame"]
    if start_line is not None and end_line is not None:
        cmd_args.extend([f"-L{start_line},{end_line}"])
    elif start_line is not None:
        cmd_args.extend([f"-L{start_line},+1"])
    if args is not None:
        cmd_args.extend(args)
    cmd_args.append("--")
    cmd_args.append(file_path)

    return await _run_git_command(repo_path, cmd_args)


async def git_log(
    repo_path: str,
    max_count: int = 10,
    branch: str | None = None,
    file_path: str | None = None,
    since: str | None = None,
    until: str | None = None,
    author: str | None = None,
    format_str: str | None = None,
    args: list[str] | None = None,
) -> dict[str, Any]:
    cmd_args: list[str] = ["log"]
    if max_count > 0:
        cmd_args.extend([f"--max-count={max_count}"])
    if branch is not None:
        cmd_args.append(branch)
    if since is not None:
        cmd_args.extend(["--since", since])
    if until is not None:
        cmd_args.extend(["--until", until])
    if author is not None:
        cmd_args.extend(["--author", author])
    if format_str is not None:
        cmd_args.extend([f"--format={format_str}"])
    if args is not None:
        cmd_args.extend(args)
    if file_path is not None:
        cmd_args.append("--")
        cmd_args.append(file_path)

    return await _run_git_command(repo_path, cmd_args)
