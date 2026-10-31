"""Loading the app's golden fixtures and shaping domain output like the app's JSON."""

import dataclasses
import json
from pathlib import Path
from typing import Any

from pydantic.alias_generators import to_camel

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GAME_NOW = 1_795_069_800_000  # 2026-11-19T06:30Z: when the app evaluated the game fixtures


def load(name: str) -> dict[str, Any]:
    """One fixture file."""
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def names(prefix: str) -> list[str]:
    """Fixture names starting with a prefix."""
    return sorted(p.stem for p in FIXTURES.glob(f"{prefix}*.json"))


def camel(value: Any) -> Any:
    """Dataclasses and dicts with snake_case keys, as the app's camelCase JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {to_camel(k) if isinstance(k, str) else k: camel(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [camel(v) for v in value]
    return value
