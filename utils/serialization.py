from __future__ import annotations

from dataclasses import fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, Mapping, TypeVar, Union, get_args, get_origin, get_type_hints


T = TypeVar("T", bound="JsonDataclassMixin")


class JsonDataclassMixin:
    """Mixin that provides predictable dict/JSON conversion for dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        return {
            field.name: _serialize_value(getattr(self, field.name))
            for field in fields(self)
        }

    @classmethod
    def from_dict(cls: type[T], payload: Mapping[str, Any] | T) -> T:
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError(f"{cls.__name__}.from_dict expects a mapping payload.")
        return _deserialize_dataclass(cls, payload)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, JsonDataclassMixin):
        return value.to_dict()
    if is_dataclass(value):
        return {
            field.name: _serialize_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    return value


@lru_cache(maxsize=None)
def _field_hints(cls: type[Any]) -> dict[str, Any]:
    return get_type_hints(cls)


def _deserialize_dataclass(cls: type[T], payload: Mapping[str, Any]) -> T:
    hints = _field_hints(cls)
    kwargs: dict[str, Any] = {}

    for field in fields(cls):
        if field.name not in payload:
            continue
        expected_type = hints.get(field.name, Any)
        kwargs[field.name] = _deserialize_value(expected_type, payload[field.name])

    return cls(**kwargs)


def _deserialize_value(expected_type: Any, value: Any) -> Any:
    if value is None:
        return None

    if expected_type is Any:
        return value

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    if origin is None:
        if expected_type is Path:
            return Path(value)
        if isinstance(expected_type, type) and issubclass(
            expected_type, JsonDataclassMixin
        ):
            return expected_type.from_dict(value)
        return value

    if origin in (list, tuple, set):
        item_type = args[0] if args else Any
        items = [_deserialize_value(item_type, item) for item in value]
        if origin is tuple:
            return tuple(items)
        if origin is set:
            return set(items)
        return items

    if origin is dict:
        key_type = args[0] if len(args) > 0 else Any
        value_type = args[1] if len(args) > 1 else Any
        return {
            _deserialize_value(key_type, key): _deserialize_value(value_type, item)
            for key, item in value.items()
        }

    if origin in (Union, UnionType):
        non_none_args = [arg for arg in args if arg is not type(None)]
        if not non_none_args:
            return value
        return _deserialize_value(non_none_args[0], value)

    return value
