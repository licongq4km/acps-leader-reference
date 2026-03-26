"""
tools.py — LangChain tool definitions for the ACPS Leader Agent.

Every tool always returns a JSON string and NEVER raises an
exception. Errors are encoded as {"success": false, "error": "...", "error_type": "..."}
so the agent can decide whether to retry, use a fallback, or inform the user.

Tools:
    read_file          — read any file under the agent directory
    write_file         — write / overwrite a file (creates parent dirs)
    make_dir           — create a directory tree
    exists             — check whether a path exists
    run_python         — execute a script from skills
    generate_response  — deliver the final reply to the user (MUST be called for
                        every user-visible message)
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.types import interrupt

from config import BACKEND_DIR, SCRIPTS_DIR, LEADER_AIC
from logger import get_logger

log = get_logger("tools")


def _ok(**fields: Any) -> str:
    """Return a JSON success response."""
    return json.dumps({"success": True, **fields}, ensure_ascii=False)


def _err(error: str, error_type: str, **fields: Any) -> str:
    """Return a JSON error response. Never raises."""
    return json.dumps(
        {"success": False, "error": error, "error_type": error_type, **fields},
        ensure_ascii=False,
    )


def _resolve(path: str) -> Path:
    """Resolve a path relative to BACKEND_DIR if it is not absolute."""
    p = Path(path)
    return (BACKEND_DIR / p).resolve() if not p.is_absolute() else p.resolve()


@tool
def read_file(path: str) -> str:
    """Read and return the full text content of a file as JSON.

    Returns {"success": true, "content": "..."} on success.
    Returns {"success": false, "error": "...", "error_type": "..."} on failure.

    Use this to read SKILL.md, references/*.md, or any state/**/*.json file.
    Accepts paths absolute or relative to the agent directory.
    """
    resolved = _resolve(path)
    log.info("read_file  path=%s", resolved)
    try:
        content = resolved.read_text(encoding="utf-8")
        log.debug("read_file  OK  bytes=%d", len(content))
        return _ok(content=content, path=str(resolved))
    except FileNotFoundError:
        log.warning("read_file  NOT_FOUND  path=%s", resolved)
        return _err(
            error=f"File not found: {resolved}",
            error_type="file_not_found",
            path=str(resolved),
            hint="Check the path and use exists() to verify before reading.",
        )
    except PermissionError:
        log.error("read_file  PERMISSION_DENIED  path=%s", resolved)
        return _err(
            error=f"Permission denied reading: {resolved}",
            error_type="permission_denied",
            path=str(resolved),
            hint="Tell the user about the permission error.",
        )
    except Exception as e:
        log.error("read_file  EXCEPTION  path=%s  error=%s", resolved, e)
        return _err(error=str(e), error_type="io_error", path=str(resolved))



@tool
def write_file(path: str, content: str) -> str:
    """Write text content to a file, creating parent directories as needed.

    Returns {"success": true, "bytes_written": N, "path": "..."} on success.
    Returns {"success": false, "error": "...", "error_type": "..."} on failure.

    Use this to cache ACS documents (state/discovery/<aic>.json)
    or task state (state/tasks/<task_id>.json).
    Accepts paths absolute or relative to the agent directory.
    """
    resolved = _resolve(path)
    log.info("write_file  path=%s  bytes=%d", resolved, len(content))
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        log.debug("write_file  OK")
        return _ok(bytes_written=len(content), path=str(resolved))
    except PermissionError:
        log.error("write_file  PERMISSION_DENIED  path=%s", resolved)
        return _err(
            error=f"Permission denied writing to: {resolved}",
            error_type="permission_denied",
            path=str(resolved),
            hint="Tell the user about the permission error.",
        )
    except Exception as e:
        log.error("write_file  EXCEPTION  path=%s  error=%s", resolved, e)
        return _err(error=str(e), error_type="io_error", path=str(resolved))


@tool
def make_dir(path: str) -> str:
    """Create a directory (and all intermediate parents) if it does not exist.

    Returns {"success": true, "path": "..."} on success.
    Returns {"success": false, "error": "...", "error_type": "..."} on failure.

    Accepts paths absolute or relative to the agent directory.
    """
    resolved = _resolve(path)
    log.info("make_dir  path=%s", resolved)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        log.debug("make_dir  OK")
        return _ok(path=str(resolved))
    except Exception as e:
        log.error("make_dir  EXCEPTION  path=%s  error=%s", resolved, e)
        return _err(error=str(e), error_type="io_error", path=str(resolved))


# ---------------------------------------------------------------------------
# Tool: exists
# ---------------------------------------------------------------------------

@tool
def exists(path: str) -> str:
    """Check whether a file or directory exists.

    Returns {"success": true, "exists": true/false, "path": "..."}.
    Always succeeds (never returns success=false).

    Accepts paths absolute or relative to the agent directory.
    """
    resolved = _resolve(path)
    found = resolved.exists()
    log.info("exists  path=%s  found=%s", resolved, found)
    return _ok(exists=found, path=str(resolved))


@tool
def run_python(script_name: str, script_args: dict[str, Any]) -> str:
    """Execute one of the ACPS skill scripts and return its JSON output.

    script_name  : filename only, e.g. 'discover.py', 'start_task.py'
    script_args  : mapping of CLI argument names to values, e.g.
                   {"query": "chess game", "limit": 3}
                   {"aic": "1.2.156...", "task_description": "...", "session_id": "..."}

    Returns {"success": true, "message": ...} on success. If stdout is valid JSON,
    message is the parsed value; otherwise message is the raw stdout text. If there
    is no stdout, message explains that the script produced no output.
    Returns {"success": false, ...} on any failure — never raises.
    The tool automatically injects --leader_aic if not already set.
    """
    script_path = SCRIPTS_DIR / script_name
    log.info("run_python  script=%s  input_args=%s", script_name, script_args)

    if not script_path.exists():
        available = [p.name for p in SCRIPTS_DIR.glob("*.py")]
        log.error("run_python  SCRIPT_NOT_FOUND  script=%s  available=%s",
                  script_name, available)
        return _err(
            error=f"Script not found: {script_name}",
            error_type="script_not_found",
            available_scripts=available,
            hint="Use one of the listed script names exactly.",
        )

    # Inject leader_aic default
    effective: dict[str, Any] = dict(script_args)
    if "leader_aic" not in effective:
        effective["leader_aic"] = LEADER_AIC

    # Build CLI arg list: {"key": "val"} → ["--key", "val"]
    cli_args: list[str] = []
    for key, val in effective.items():
        cli_args.extend([f"--{key}", str(val)])

    cmd = [sys.executable, str(script_path)] + cli_args
    log.debug("run_python  cmd=%s", " ".join(cmd))

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(SCRIPTS_DIR),
            env=env,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0 and not stdout:
            log.error("run_python  SCRIPT_ERROR  script=%s  rc=%d  stderr=%s",
                      script_name, proc.returncode, stderr[:1000])
            return _err(
                error=stderr or f"Script exited with code {proc.returncode}",
                error_type="script_error",
                script=script_name,
                exit_code=proc.returncode,
                hint="Check script arguments and retry.",
            )

        if stderr:
            log.debug("run_python  stderr(non-fatal)  script=%s  stderr=%s",
                      script_name, stderr[:300])

        if stdout:
            try:
                msg: Any = json.loads(stdout)
            except json.JSONDecodeError:
                msg = stdout
        else:
            msg = "Script completed with no output"
        output = json.dumps({"success": True, "message": msg}, ensure_ascii=False)
        log.info("run_python  OK  script=%s  preview=%s", script_name, output[:200])
        return output

    except subprocess.TimeoutExpired:
        log.error("run_python  TIMEOUT  script=%s", script_name)
        return _err(
            error="Script timed out after 600 seconds",
            error_type="timeout",
            script=script_name,
            hint="The partner agent may be slow or unreachable. Try again or cancel.",
        )
    except Exception as e:
        log.error("run_python  EXCEPTION  script=%s  error=%s", script_name, e,
                  exc_info=True)
        return _err(
            error=str(e),
            error_type="execution_error",
            script=script_name,
        )


_VALID_RESPONSE_TYPES = {"result", "error", "question", "info"}

@tool
def generate_response(message: str, response_type: Literal["result", "error", "question", "info"] = "info") -> str:
    """Deliver a reply to the user. This tool MUST be called for every message
    that is intended for the user — task results, error notices, clarifying
    questions, or plain information.

    After delivering the message, execution pauses to wait for the user's next
    input. The user's reply is returned as a string so you can continue the
    conversation based on what the user said.

    Parameters
    ----------
    message       : The human-readable text to show the user. Write in the
                    same language the user used. Do NOT expose raw protocol
                    fields (AICs, raw_payload, endpoint URLs) unless the user
                    explicitly asked for them.
    response_type : One of:
                      "result"   — task outcome or discovery result
                      "error"    — something went wrong; explain and suggest next steps
                      "question" — you need more information from the user
                      "info"     — general status update or neutral message

    Returns the user's next message as a string so the conversation can continue.
    """
    rtype = response_type if response_type in _VALID_RESPONSE_TYPES else "info"
    log.info("generate_response  type=%s  preview=%s", rtype, message[:200])

    user_reply = interrupt({"message": message, "response_type": rtype})
    log.info("generate_response  resumed with user_reply=%s", str(user_reply)[:200])

    return json.dumps({
        "success": True,
        "delivered": True,
        "response_type": rtype,
        "user_reply": user_reply,
    })

ALL_TOOLS = [read_file, write_file, make_dir, exists, run_python, generate_response]
