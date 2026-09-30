"""Request-owned metadata filters shared by Milvus and authoritative reads."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .store import ChunkSnapshot


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    category: str | None = Field(default=None, max_length=255)
    product_category: str | None = Field(default=None, max_length=128)
    content_type: str | None = Field(default=None, max_length=32)
    is_key_clause: bool | None = None

    @field_validator("category", "product_category", "content_type")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("Filter values must be nonblank")
        return value

    def matches(self, chunk: ChunkSnapshot) -> bool:
        return all(
            getattr(chunk, name) == value
            for name, value in self.model_dump(exclude_none=True).items()
        )


def compile_filter(filters: SearchFilters) -> tuple[str, dict]:
    values = filters.model_dump(exclude_none=True)
    return " and ".join(f"{name} == {{{name}}}" for name in values), values
