from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..core import ContractError


_MAX_ARGUMENTS = 32
_MAX_TEXT = 4096
_LOOPBACKS = frozenset({"127.0.0.1", "::1"})
_MLX_CHAT_TEMPLATE_ARGS = '{"enable_thinking":false}'


@dataclass(frozen=True)
class ArgumentRule:
    flag: Optional[str]
    kind: str
    required: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    choices: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    adapter_version: str
    engine_family: str
    command_id: str
    lifecycle_module: str
    manifest_arguments: Tuple[str, ...]
    prefix: Tuple[str, ...]
    arguments: Mapping[str, ArgumentRule]


_COMMON = {
    "host": ArgumentRule("--host", "loopback", required=True),
    "port": ArgumentRule("--port", "integer", required=True, minimum=1, maximum=65535),
    "model": ArgumentRule("--model", "path", required=True),
}


def _rules(*, executable_field: str, extras: Mapping[str, ArgumentRule]) -> Dict[str, ArgumentRule]:
    result = {executable_field: ArgumentRule(None, "executable", required=True), **_COMMON}
    result.update(extras)
    return result


ADAPTER_REGISTRY: Mapping[str, AdapterRegistration] = {
    "turnvector-openai-server": AdapterRegistration(
        adapter_id="turnvector-openai-server-adapter-v1",
        adapter_version="v1",
        engine_family="turnvector",
        command_id="turnvector-openai-server",
        lifecycle_module="adapters.cross_engine.turnvector",
        manifest_arguments=("serve", "--model", "{model_root}", "--host", "127.0.0.1", "--port", "{port}"),
        prefix=("serve",),
        arguments=_rules(
            executable_field="executable",
            extras={
                "config": ArgumentRule("--config", "path"),
                "state_root": ArgumentRule("--state-root", "path"),
                "workers": ArgumentRule("--workers", "integer", minimum=1, maximum=256),
            },
        ),
    ),
    "ax-engine-openai-server": AdapterRegistration(
        adapter_id="ax-engine-openai-server-adapter-v1",
        adapter_version="v1",
        engine_family="ax-engine",
        command_id="ax-engine-openai-server",
        lifecycle_module="adapters.cross_engine.ax_engine",
        manifest_arguments=("serve", "{model_root}", "--host", "127.0.0.1", "--port", "{port}"),
        prefix=("serve",),
        arguments=_rules(
            executable_field="executable",
            extras={
                "model": ArgumentRule(None, "path", required=True),
                "hf_cache_root": ArgumentRule("--hf-cache-root", "path"),
                "offline": ArgumentRule("--offline", "boolean"),
            },
        ),
    ),
    "mlx-lm-openai-server": AdapterRegistration(
        adapter_id="mlx-lm-openai-server-adapter-v1",
        adapter_version="v1",
        engine_family="mlx-lm",
        command_id="mlx-lm-openai-server",
        lifecycle_module="adapters.cross_engine.mlx_lm",
        manifest_arguments=(
            "--model",
            "{model_root}",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
            "--chat-template-args",
            _MLX_CHAT_TEMPLATE_ARGS,
        ),
        prefix=("--chat-template-args", _MLX_CHAT_TEMPLATE_ARGS),
        arguments=_rules(
            executable_field="executable",
            extras={
                "adapter_path": ArgumentRule("--adapter-path", "path"),
                "chat_template": ArgumentRule("--chat-template", "path"),
                "trust_remote_code": ArgumentRule("--trust-remote-code", "boolean"),
            },
        ),
    ),
    "llama-cpp-openai-server": AdapterRegistration(
        adapter_id="llama-cpp-openai-server-adapter-v1",
        adapter_version="v1",
        engine_family="llama.cpp",
        command_id="llama-cpp-openai-server",
        lifecycle_module="adapters.cross_engine.llama_cpp",
        manifest_arguments=("--model", "{model_root}/model.gguf", "--host", "127.0.0.1", "--port", "{port}"),
        prefix=(),
        arguments=_rules(
            executable_field="executable",
            extras={
                "context_length": ArgumentRule("--ctx-size", "integer", minimum=1, maximum=1048576),
                "parallel": ArgumentRule("--parallel", "integer", minimum=1, maximum=256),
                "seed": ArgumentRule("--seed", "integer", minimum=0, maximum=2147483647),
                "chat_template": ArgumentRule("--chat-template-file", "path"),
                "no_mmap": ArgumentRule("--no-mmap", "boolean"),
                "mlock": ArgumentRule("--mlock", "boolean"),
            },
        ),
    ),
}

ENGINE_FAMILY_COMMANDS = {
    registration.engine_family: command_id for command_id, registration in ADAPTER_REGISTRY.items()
}


def registered_command_ids() -> Tuple[str, ...]:
    return tuple(sorted(ADAPTER_REGISTRY))


def registration_for(command_id: str, engine_family: Optional[str] = None) -> AdapterRegistration:
    if not isinstance(command_id, str) or command_id not in ADAPTER_REGISTRY:
        raise ContractError("target selects an unregistered lifecycle command")
    registration = ADAPTER_REGISTRY[command_id]
    if engine_family is not None and registration.engine_family != engine_family:
        raise ContractError("registered lifecycle command does not match engine_family")
    return registration


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > _MAX_TEXT:
        raise ContractError(f"{where} must be a bounded non-empty string")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ContractError(f"{where} contains a forbidden control character")
    return value


def _argument_value(name: str, value: Any, rule: ArgumentRule) -> str:
    where = f"arguments.{name}"
    if rule.kind in {"path", "executable"}:
        text = _text(value, where)
        path = Path(text)
        if not path.is_absolute():
            raise ContractError(f"{where} must be an absolute path")
        return str(path)
    if rule.kind == "loopback":
        text = _text(value, where)
        if text not in _LOOPBACKS:
            raise ContractError(f"{where} must be a literal loopback address")
        return text
    if rule.kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError(f"{where} must be an integer")
        if rule.minimum is not None and value < rule.minimum:
            raise ContractError(f"{where} is below its registered bound")
        if rule.maximum is not None and value > rule.maximum:
            raise ContractError(f"{where} exceeds its registered bound")
        return str(value)
    if rule.kind == "choice":
        text = _text(value, where)
        if text not in rule.choices:
            raise ContractError(f"{where} is not a registered value")
        return text
    raise ContractError(f"adapter registry has unknown argument kind {rule.kind!r}")


def _substitute(token: str, bindings: Mapping[str, Any], where: str) -> str:
    if token == _MLX_CHAT_TEMPLATE_ARGS:
        return token
    rendered = token
    for name in ("model_root", "target_checkout", "python_executable", "port"):
        marker = "{" + name + "}"
        if marker in rendered:
            if name not in bindings:
                raise ContractError(f"{where} requires runtime binding {name!r}")
            value = _text(str(bindings[name]), f"runtime_bindings.{name}")
            if name == "port":
                try:
                    port = int(value)
                except ValueError as error:
                    raise ContractError("runtime_bindings.port must be an integer") from error
                if not 1 <= port <= 65535 or value != str(port):
                    raise ContractError("runtime_bindings.port must be in [1, 65535]")
            elif not Path(value).is_absolute():
                raise ContractError(f"runtime_bindings.{name} must be an absolute path")
            rendered = rendered.replace(marker, value)
    if "{" in rendered or "}" in rendered:
        raise ContractError(f"{where} contains an unregistered placeholder")
    return _text(rendered, where)


def build_target_argv(
    command_id: str,
    arguments: Any,
    *,
    executable: Optional[str] = None,
    runtime_bindings: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, ...]:
    """Build a shell-free argv from a closed mapping or exact manifest template."""
    registration = registration_for(command_id)
    if isinstance(arguments, Mapping):
        if len(arguments) > _MAX_ARGUMENTS:
            raise ContractError("adapter arguments exceed the registered bound")
        unknown = sorted(set(arguments) - set(registration.arguments))
        missing = sorted(
            name for name, rule in registration.arguments.items() if rule.required and name not in arguments
        )
        if unknown:
            raise ContractError(f"adapter arguments have unknown fields: {', '.join(unknown)}")
        if missing:
            raise ContractError(f"adapter arguments are missing fields: {', '.join(missing)}")
        executable_name = "python_executable" if "python_executable" in registration.arguments else "executable"
        resolved_executable = _argument_value(
            executable_name, arguments[executable_name], registration.arguments[executable_name]
        )
        argv = [resolved_executable, *registration.prefix]
        for name, rule in registration.arguments.items():
            if name == executable_name or name not in arguments:
                continue
            value = arguments[name]
            if rule.kind == "boolean":
                if not isinstance(value, bool):
                    raise ContractError(f"arguments.{name} must be a boolean")
                if value:
                    assert rule.flag is not None
                    argv.append(rule.flag)
                continue
            rendered = _argument_value(name, value, rule)
            if rule.flag is None:
                argv.append(rendered)
            else:
                argv.extend((rule.flag, rendered))
        return tuple(argv)

    if isinstance(arguments, (str, bytes)) or not isinstance(arguments, Sequence):
        raise ContractError("adapter arguments must be an object or registered template array")
    manifest_arguments = tuple(arguments)
    if manifest_arguments != registration.manifest_arguments:
        raise ContractError("adapter argv template does not exactly match the registered command")
    if executable is None or runtime_bindings is None:
        raise ContractError("manifest argv resolution requires executable and runtime bindings")
    bindings = dict(runtime_bindings)
    resolved_executable = _substitute(executable, bindings, "executable")
    resolved = tuple(
        _substitute(token, bindings, f"adapter.arguments[{index}]")
        for index, token in enumerate(manifest_arguments)
    )
    return (resolved_executable, *resolved)


def lifecycle_adapter_argv(command_id: str) -> Tuple[str, ...]:
    registration = registration_for(command_id)
    return (sys.executable, "-B", "-m", registration.lifecycle_module, "--command-id", command_id)


def resolve_target_adapter(
    target: Any, runtime_bindings: Optional[Mapping[str, Any]] = None
) -> Tuple[AdapterRegistration, Tuple[str, ...]]:
    def field(name: str, default: Any = None) -> Any:
        if isinstance(target, Mapping):
            return target.get(name, default)
        return getattr(target, name, default)

    adapter = field("adapter", {})
    if not isinstance(adapter, Mapping):
        adapter = {}
    command_id = field("command_id", adapter.get("registered_command", adapter.get("command_id")))
    arguments = field("adapter_arguments", adapter.get("arguments", field("arguments")))
    engine_family = field("engine_family")
    registration = registration_for(command_id, engine_family)
    if isinstance(arguments, Mapping):
        return registration, build_target_argv(command_id, arguments)
    executables = field("executables", ())
    if not isinstance(executables, Sequence) or isinstance(executables, (str, bytes)) or len(executables) != 1:
        raise ContractError("registered target must bind exactly one executable")
    executable = executables[0]
    executable_path = executable.get("path") if isinstance(executable, Mapping) else getattr(executable, "path", None)
    return registration, build_target_argv(
        command_id,
        arguments,
        executable=executable_path,
        runtime_bindings=runtime_bindings,
    )


__all__ = [
    "ADAPTER_REGISTRY",
    "ENGINE_FAMILY_COMMANDS",
    "AdapterRegistration",
    "ArgumentRule",
    "build_target_argv",
    "lifecycle_adapter_argv",
    "registered_command_ids",
    "registration_for",
    "resolve_target_adapter",
]
