"""Run one provider-neutral worker attempt without mutating task runtime state."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import atomic_write_text, load_json, load_yaml, stable_json


WORKER_FAILURES = {
    "WORKER-COMMAND",
    "WORKER-TIMEOUT",
    "WORKER-EXIT",
    "WORKER-PROTOCOL",
    "WORKER-OUTPUT-LIMIT",
    "WORKER-SECRET-OUTPUT",
    "WORKER-CAPABILITY",
}
SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}
SHELL_META_RE = re.compile(r"[;&|$`()<>`\n\r]")
ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'])(/(?:[^\s\"']+/)*[^\s\"']*)|\b[A-Za-z]:[\\/][^\s\"']*")
CREDENTIAL_ASSIGNMENT_RE = re.compile(r"(?i)\b(?:credential|api[_-]?key|secret|token|password)\s*[:=]\s*[A-Za-z0-9/+=._-]{8,}")


class WorkerAdapterError(ValueError):
    """A request/config/output error that cannot produce a valid attempt result."""

    def __init__(self, failure_class: str, message: str) -> None:
        self.failure_class = failure_class
        self.message = message
        super().__init__(f"{failure_class}: {message}")


class AttemptHeartbeatError(RuntimeError):
    """The supervisor could not renew the lease for a running worker."""


@dataclass(frozen=True)
class AdapterSettings:
    command: tuple[str, ...]
    capabilities: frozenset[str]
    environment_allowlist: tuple[str, ...]
    timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int


def _schema_validator(protocol_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources(
        [
            (common["$id"], Resource.from_contents(common)),
            (schema["$id"], Resource.from_contents(schema)),
        ]
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate_instance(validator: Draft202012Validator, value: Any, failure_class: str, label: str) -> None:
    errors = sorted(validator.iter_errors(value), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        raise WorkerAdapterError(failure_class, f"{label} does not match its versioned schema")


def _parse_config(protocol_root: Path) -> dict[str, Any]:
    try:
        value = load_yaml(protocol_root / "config" / "worker-adapters.yaml")
    except Exception as exc:
        raise WorkerAdapterError("WORKER-COMMAND", "worker adapter configuration cannot be read") from exc
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("adapters"), dict):
        raise WorkerAdapterError("WORKER-COMMAND", "worker adapter configuration is invalid")
    defaults = value.get("defaults", {})
    if not isinstance(defaults, dict):
        raise WorkerAdapterError("WORKER-COMMAND", "worker adapter defaults are invalid")
    return value


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise WorkerAdapterError("WORKER-COMMAND", f"{label} must be a non-empty-string list")
    return tuple(value)


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise WorkerAdapterError("WORKER-COMMAND", f"{label} must be positive")
    return float(value)


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkerAdapterError("WORKER-COMMAND", f"{label} must be a positive integer")
    return value


def _check_argv(command: Sequence[str]) -> tuple[str, ...]:
    if not command or any(not isinstance(item, str) or not item or "\x00" in item for item in command):
        raise WorkerAdapterError("WORKER-COMMAND", "worker command must be a non-empty argv array")
    executable = Path(command[0]).name.lower()
    if executable in SHELL_EXECUTABLES:
        raise WorkerAdapterError("WORKER-COMMAND", "shell interpreter commands are not permitted")
    if any(SHELL_META_RE.search(item) for item in command):
        raise WorkerAdapterError("WORKER-COMMAND", "shell metacharacters are not permitted in argv")
    return tuple(command)


def _settings(
    protocol_root: Path,
    adapter: str,
    command_override: Sequence[str] | None,
    timeout_seconds: float | None,
    max_stdout_bytes: int | None,
    max_stderr_bytes: int | None,
) -> AdapterSettings:
    config = _parse_config(protocol_root)
    defaults = config.get("defaults", {})
    adapter_value = config["adapters"].get(adapter)
    if not isinstance(adapter_value, dict):
        raise WorkerAdapterError("WORKER-COMMAND", "requested worker adapter is not configured")
    configured_command = command_override if command_override is not None else adapter_value.get("command")
    command = _check_argv(_string_list(configured_command, "command"))
    configured_env = adapter_value.get("environment_allowlist", defaults.get("environment_allowlist", []))
    environment_allowlist = _string_list(configured_env, "environment_allowlist") if configured_env else ()
    capabilities = adapter_value.get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(item, str) or not item for item in capabilities):
        raise WorkerAdapterError("WORKER-COMMAND", "adapter capabilities are invalid")
    timeout = timeout_seconds if timeout_seconds is not None else adapter_value.get("timeout_seconds", defaults.get("timeout_seconds"))
    stdout_limit = max_stdout_bytes if max_stdout_bytes is not None else adapter_value.get("max_stdout_bytes", defaults.get("max_stdout_bytes"))
    stderr_limit = max_stderr_bytes if max_stderr_bytes is not None else adapter_value.get("max_stderr_bytes", defaults.get("max_stderr_bytes"))
    return AdapterSettings(
        command=command,
        capabilities=frozenset(capabilities),
        environment_allowlist=environment_allowlist,
        timeout_seconds=_positive_number(timeout, "timeout_seconds"),
        max_stdout_bytes=_positive_integer(stdout_limit, "max_stdout_bytes"),
        max_stderr_bytes=_positive_integer(stderr_limit, "max_stderr_bytes"),
    )


def _resolve_argv(protocol_root: Path, command: Sequence[str]) -> tuple[str, ...]:
    resolved = list(command)
    if Path(resolved[0]).name in {"python", "python3", "python3.11"}:
        resolved[0] = sys.executable
    for index, argument in enumerate(resolved[1:], 1):
        candidate = protocol_root / argument
        if not Path(argument).is_absolute() and candidate.is_file():
            resolved[index] = str(candidate.resolve())
    return tuple(resolved)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _diagnostics(
    *,
    stderr: str = "",
    exit_code: int | None = None,
    signal_number: int | None = None,
    timed_out: bool = False,
    stream: str | None = None,
    byte_count: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "stderr": stderr,
        "exit_code": exit_code,
        "signal": signal_number,
        "timed_out": timed_out,
    }
    if stream is not None:
        value["stream"] = stream
    if byte_count is not None:
        value["bytes"] = byte_count
    return value


def _failure_result(
    request: dict[str, Any], failure_class: str, message: str, diagnostics: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "run_id": request["run_id"],
        "attempt_id": request["attempt_id"],
        "status": "FAILED",
        "summary": "Worker attempt failed before an accepted effect was produced.",
        "effect_key": None,
        "outputs": [],
        "failure": {"class": failure_class, "message": message},
        "human_decision_request": None,
        "diagnostics": diagnostics or _diagnostics(),
    }


def _access_patterns(protocol_root: Path) -> list[re.Pattern[str]]:
    try:
        policy = load_yaml(protocol_root / "config" / "access-policy.yaml") or {}
    except Exception as exc:
        raise WorkerAdapterError("WORKER-COMMAND", "access policy cannot be read") from exc
    patterns: list[re.Pattern[str]] = []
    for item in policy.get("secret_patterns", []) if isinstance(policy, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get("pattern"), str):
            raise WorkerAdapterError("WORKER-COMMAND", "access policy secret pattern is invalid")
        try:
            patterns.append(re.compile(item["pattern"]))
        except re.error as exc:
            raise WorkerAdapterError("WORKER-COMMAND", "access policy secret pattern is invalid") from exc
    patterns.append(CREDENTIAL_ASSIGNMENT_RE)
    return patterns


def _redact_text(value: str, patterns: Iterable[re.Pattern[str]], sensitive_values: Iterable[str]) -> str:
    redacted = value
    for pattern in patterns:
        redacted = pattern.sub("[REDACTED]", redacted)
    for sensitive in sensitive_values:
        if sensitive:
            redacted = redacted.replace(sensitive, "[REDACTED]")
    return ABSOLUTE_PATH_RE.sub(lambda match: "[PATH_REDACTED]", redacted)


def _contains(value: Any, predicate: Any) -> bool:
    if isinstance(value, str):
        return bool(predicate(value))
    if isinstance(value, dict):
        return any(_contains(key, predicate) or _contains(item, predicate) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains(item, predicate) for item in value)
    return False


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def _run_process(
    argv: Sequence[str],
    payload: bytes,
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    heartbeat_callback: Callable[[], None] | None = None,
    heartbeat_interval: float | None = None,
) -> tuple[bytes, bytes, int, bool, str | None]:
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=environment,
            shell=False,
            start_new_session=(os.name == "posix"),
        )
    except (OSError, ValueError) as exc:
        raise WorkerAdapterError("WORKER-COMMAND", "worker process could not be started") from exc
    try:
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
        timed_out = False
        overflow_stream: str | None = None
        stop_at = time.monotonic() + timeout
        next_heartbeat = time.monotonic() + heartbeat_interval if heartbeat_callback and heartbeat_interval else None
        while selector.get_map():
            remaining = stop_at - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                _terminate(process)
                break
            if next_heartbeat is not None and time.monotonic() >= next_heartbeat and process.poll() is None:
                heartbeat_callback()
                next_heartbeat = time.monotonic() + float(heartbeat_interval)
            wait_for = max(0.0, remaining)
            if next_heartbeat is not None:
                wait_for = min(wait_for, max(0.0, next_heartbeat - time.monotonic()))
            events = selector.select(wait_for)
            if not events:
                if next_heartbeat is not None and time.monotonic() >= next_heartbeat and process.poll() is None:
                    heartbeat_callback()
                    next_heartbeat = time.monotonic() + float(heartbeat_interval)
                    continue
                if process.poll() is None:
                    timed_out = True
                    _terminate(process)
                break
            for key, _ in events:
                stream = key.data
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                available = limits[stream] + 1 - len(buffers[stream])
                if available <= 0:
                    overflow_stream = stream
                    _terminate(process)
                    break
                buffers[stream].extend(chunk[:available])
                if len(buffers[stream]) > limits[stream]:
                    overflow_stream = stream
                    _terminate(process)
                    break
            if timed_out or overflow_stream is not None:
                break
        if timed_out or overflow_stream is not None:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            process.wait()
            while selector.get_map():
                for key, _ in selector.select(0):
                    stream = key.data
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    available = limits[stream] + 1 - len(buffers[stream])
                    if available <= 0:
                        overflow_stream = stream
                        continue
                    buffers[stream].extend(chunk[:available])
                    if len(buffers[stream]) > limits[stream]:
                        overflow_stream = stream
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
            key.fileobj.close()
        selector.close()
        return bytes(buffers["stdout"]), bytes(buffers["stderr"]), process.returncode or 0, timed_out, overflow_stream
    except Exception:
        if process.poll() is None:
            _terminate(process)
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        raise


def _output_file_result(path: Path | None, result: dict[str, Any]) -> dict[str, Any]:
    if path is None:
        return result
    payload = stable_json(result)
    encoded = payload.encode("utf-8")
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise WorkerAdapterError("WORKER-PROTOCOL", "existing attempt result cannot be read") from exc
        if existing != encoded:
            raise WorkerAdapterError("WORKER-PROTOCOL", "attempt result already exists with different bytes")
        return result
    try:
        atomic_write_text(path, payload)
    except OSError as exc:
        raise WorkerAdapterError("WORKER-COMMAND", "attempt result cannot be written") from exc
    return result


def run_attempt(
    request_path: Path,
    *,
    adapter: str,
    protocol_root: Path,
    command: Sequence[str] | None = None,
    timeout_seconds: float | None = None,
    max_stdout_bytes: int | None = None,
    max_stderr_bytes: int | None = None,
    output_path: Path | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    heartbeat_interval_seconds: float | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run exactly one attempt and return a schema-valid result when possible."""

    protocol = protocol_root.resolve()
    try:
        request = load_json(request_path)
    except Exception as exc:
        raise WorkerAdapterError("WORKER-PROTOCOL", "attempt request is not valid JSON") from exc
    if not isinstance(request, dict):
        raise WorkerAdapterError("WORKER-PROTOCOL", "attempt request must be a JSON object")
    request_validator = _schema_validator(protocol, "agent-attempt-request")
    _validate_instance(request_validator, request, "WORKER-PROTOCOL", "attempt request")
    try:
        settings = _settings(protocol, adapter, command, timeout_seconds, max_stdout_bytes, max_stderr_bytes)
    except WorkerAdapterError as exc:
        if exc.failure_class not in WORKER_FAILURES:
            raise
        result = _failure_result(request, exc.failure_class, "Worker command configuration is not executable.")
        _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
        return _output_file_result(output_path, result)
    required_capabilities = set(request["capabilities"]["required"])
    if not required_capabilities.issubset(settings.capabilities):
        result = _failure_result(request, "WORKER-CAPABILITY", "Worker capability requirements are unavailable.")
        _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
        return _output_file_result(output_path, result)
    requested_env = set(request["capabilities"]["environment_allowlist"])
    if not requested_env.issubset(set(settings.environment_allowlist)):
        result = _failure_result(request, "WORKER-CAPABILITY", "Worker environment capability is unavailable.")
        _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
        return _output_file_result(output_path, result)
    try:
        deadline = _parse_timestamp(request["deadline"])
    except (TypeError, ValueError) as exc:
        raise WorkerAdapterError("WORKER-PROTOCOL", "attempt deadline is invalid") from exc
    current_time = now() if now is not None else datetime.now(timezone.utc)
    remaining = (deadline - current_time).total_seconds()
    if remaining <= 0:
        result = _failure_result(request, "WORKER-TIMEOUT", "Worker attempt deadline has expired.", _diagnostics(timed_out=True))
        _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
        return _output_file_result(output_path, result)

    workspace = Path(request["attempt_workspace"])
    if not workspace.is_absolute():
        workspace = protocol / workspace
    if not workspace.is_dir():
        result = _failure_result(request, "WORKER-COMMAND", "Worker attempt workspace is unavailable.")
        _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
        return _output_file_result(output_path, result)
    argv = _resolve_argv(protocol, settings.command)
    environment = {name: os.environ[name] for name in settings.environment_allowlist if name in os.environ}
    sensitive_values = tuple(
        value for value in (request["lease"]["token"], *environment.values()) if isinstance(value, str) and value
    )
    patterns = _access_patterns(protocol)
    request_bytes = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        stdout, stderr, returncode, timed_out, overflow_stream = _run_process(
            argv,
            request_bytes,
            workspace,
            environment,
            min(settings.timeout_seconds, remaining),
            settings.max_stdout_bytes,
            settings.max_stderr_bytes,
            heartbeat_callback=heartbeat_callback,
            heartbeat_interval=heartbeat_interval_seconds,
        )
    except WorkerAdapterError as exc:
        result = _failure_result(request, exc.failure_class, "Worker process could not be started.")
        _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
        return _output_file_result(output_path, result)
    stderr_text = _redact_text(stderr.decode("utf-8", errors="replace"), patterns, sensitive_values)
    if overflow_stream == "stdout" or len(stdout) > settings.max_stdout_bytes:
        result = _failure_result(
            request,
            "WORKER-OUTPUT-LIMIT",
            "Worker stdout exceeded the configured limit.",
            _diagnostics(stderr=stderr_text[: settings.max_stderr_bytes], exit_code=returncode, timed_out=timed_out, stream="stdout", byte_count=len(stdout)),
        )
    elif overflow_stream == "stderr" or len(stderr) > settings.max_stderr_bytes:
        result = _failure_result(
            request,
            "WORKER-OUTPUT-LIMIT",
            "Worker stderr exceeded the configured limit.",
            _diagnostics(stderr=stderr_text[: settings.max_stderr_bytes], exit_code=returncode, timed_out=timed_out, stream="stderr", byte_count=len(stderr)),
        )
    elif timed_out:
        result = _failure_result(
            request,
            "WORKER-TIMEOUT",
            "Worker process exceeded the configured deadline.",
            _diagnostics(stderr=stderr_text, exit_code=returncode, timed_out=True),
        )
    elif request["lease"]["token"] in stdout.decode("utf-8", errors="replace") or request["lease"]["token"] in stderr.decode("utf-8", errors="replace"):
        result = _failure_result(
            request,
            "WORKER-PROTOCOL",
            "Worker output contained a lease token.",
            _diagnostics(stderr=stderr_text, exit_code=returncode),
        )
    elif any(pattern.search(stdout.decode("utf-8", errors="replace")) for pattern in patterns) or any(
        pattern.search(stderr.decode("utf-8", errors="replace")) for pattern in patterns
    ) or any(value in stdout.decode("utf-8", errors="replace") or value in stderr.decode("utf-8", errors="replace") for value in sensitive_values[1:]):
        result = _failure_result(
            request,
            "WORKER-SECRET-OUTPUT",
            "Worker output contained a credential or secret pattern.",
            _diagnostics(stderr=stderr_text, exit_code=returncode),
        )
    elif returncode != 0:
        result = _failure_result(
            request,
            "WORKER-EXIT",
            "Worker process exited unsuccessfully.",
            _diagnostics(
                stderr=stderr_text,
                exit_code=returncode if returncode >= 0 else None,
                signal_number=-returncode if returncode < 0 else None,
            ),
        )
    else:
        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            result = _failure_result(request, "WORKER-PROTOCOL", "Worker stdout was not one valid JSON result.", _diagnostics(stderr=stderr_text, exit_code=returncode))
        else:
            if not isinstance(decoded, dict):
                result = _failure_result(request, "WORKER-PROTOCOL", "Worker result must be a JSON object.", _diagnostics(stderr=stderr_text, exit_code=returncode))
            elif decoded.get("run_id") != request["run_id"] or decoded.get("attempt_id") != request["attempt_id"]:
                result = _failure_result(request, "WORKER-PROTOCOL", "Worker result identity does not match the request.", _diagnostics(stderr=stderr_text, exit_code=returncode))
            elif _contains(decoded, lambda item: request["lease"]["token"] in item or bool(ABSOLUTE_PATH_RE.search(item))):
                result = _failure_result(request, "WORKER-PROTOCOL", "Worker result contained a lease token or absolute path.", _diagnostics(stderr=stderr_text, exit_code=returncode))
            elif _contains(decoded, lambda item: any(pattern.search(item) for pattern in patterns) or any(value in item for value in sensitive_values)):
                result = _failure_result(request, "WORKER-SECRET-OUTPUT", "Worker result contained a credential or secret pattern.", _diagnostics(stderr=stderr_text, exit_code=returncode))
            else:
                result = decoded
                result["diagnostics"] = _diagnostics(
                    stderr=_redact_text(str(result["diagnostics"].get("stderr", "")), patterns, sensitive_values),
                    exit_code=returncode,
                    signal_number=result["diagnostics"].get("signal"),
                    timed_out=result["diagnostics"].get("timed_out", False),
                )
                try:
                    _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "worker result")
                except WorkerAdapterError:
                    result = _failure_result(request, "WORKER-PROTOCOL", "Worker result did not match the result schema.", _diagnostics(stderr=stderr_text, exit_code=returncode))
    _validate_instance(_schema_validator(protocol, "agent-attempt-result"), result, "WORKER-PROTOCOL", "adapter result")
    return _output_file_result(output_path, result)


def _parse_command(value: str | None) -> list[str] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise WorkerAdapterError("WORKER-COMMAND", "--command-json must be a JSON argv array") from exc
    if not isinstance(parsed, list):
        raise WorkerAdapterError("WORKER-COMMAND", "--command-json must be a JSON argv array")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one provider-neutral agent attempt")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--adapter", required=True)
    run.add_argument("--protocol-root", type=Path, default=Path(__file__).resolve().parents[1])
    run.add_argument("--command-json")
    run.add_argument("--timeout-seconds", type=float)
    run.add_argument("--max-stdout-bytes", type=int)
    run.add_argument("--max-stderr-bytes", type=int)
    run.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_attempt(
            args.request,
            adapter=args.adapter,
            protocol_root=args.protocol_root,
            command=_parse_command(args.command_json),
            timeout_seconds=args.timeout_seconds,
            max_stdout_bytes=args.max_stdout_bytes,
            max_stderr_bytes=args.max_stderr_bytes,
            output_path=args.output,
        )
    except WorkerAdapterError as exc:
        print(json.dumps({"error": {"class": exc.failure_class, "message": exc.message}}, ensure_ascii=False, sort_keys=True))
        return 2
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
