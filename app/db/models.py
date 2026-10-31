"""Tables. Constraint names come from the naming convention, so migrations stay predictable."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, CheckConstraint, DateTime, MetaData, SmallInteger, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with the naming convention and aware timestamps."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


class AppState(Base):
    """One row: the global data version (ETags) and the last backup time."""

    __tablename__ = "app_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    data_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    last_backup_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
