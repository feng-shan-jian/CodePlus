"""Shared immutable wire records and versioned canonical fingerprints."""

import hashlib
import json
from typing import Annotated, Any, Literal, Mapping, Self, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator

Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class Record(BaseModel):
    """Strict, closed, deeply immutable when composed from records and tuples.

    Use model_validate_json for wire data. Pydantic's trusted model_construct
    and object.__setattr__ are not untrusted-data entry points.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always",
        validate_default=True, allow_inf_nan=False,
    )
    schema_version: Literal[1] = 1

    @field_validator("schema_version", "source_key_version", "fingerprint_version", "dimension",
                     "logits_to_keep", "truncation", "use_cache", "trust_remote_code",
                     "local_files_only", "add_special_tokens", mode="before", check_fields=False)
    @classmethod
    def strict_literals(cls, value: Any, info: ValidationInfo) -> Any:
        # Python equates True and 1; wire versions/dimensions must not do so.
        field = cls.model_fields[info.field_name]
        if get_origin(field.annotation) is Literal:
            choices = get_args(field.annotation)
            if not any(type(value) is type(choice) and value == choice for choice in choices):
                raise ValueError(f"invalid literal type or value for {info.field_name}")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported schema_version; expected integer 1")
        return value

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        # The normal Pydantic update shortcut skips validation, unsafe for snapshots.
        data = self.model_dump(mode="python", round_trip=True)
        data.update(update or {})
        return type(self).model_validate(data)


def canonical_json(value: Record | dict[str, Any]) -> str:
    """UTF-8, sorted object keys, no whitespace/NaN; array order is meaningful."""
    if isinstance(value, Record):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(scope: str, value: Record | dict[str, Any]) -> str:
    """Domain-separated SHA-256 with an explicit canonical format version."""
    payload = {"fingerprint_version": 1, "scope": scope,
               "value": value.model_dump(mode="json") if isinstance(value, Record) else value}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
