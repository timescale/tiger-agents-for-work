from psycopg.types.json import Jsonb
from pydantic import BaseModel


def serialize_to_jsonb(model: BaseModel) -> Jsonb:
    """Convert a Pydantic BaseModel to a PostgreSQL Jsonb object."""
    return Jsonb(model.model_dump())


def file_type_supported(mimetype: str) -> bool:
    return mimetype == "application/pdf" or mimetype.startswith(("text/", "image/"))
