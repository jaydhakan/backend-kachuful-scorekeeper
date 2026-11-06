"""Tables. Constraint names come from the naming convention, so migrations stay predictable."""

import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
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


class AppSetting(Base):
    """An app-wide setting an admin changes at runtime; a missing key means its default."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AppSettingAudit(Base):
    """One row per change of an app setting: who changed it, when, from what to what."""

    __tablename__ = "app_settings_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text)
    # The value in effect before, the default included, so it is never missing.
    old_value: Mapped[Any] = mapped_column(JSONB)
    new_value: Mapped[Any] = mapped_column(JSONB)
    changed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AdminAudit(Base):
    """One row per destructive admin action: who did what to whom, and the counts."""

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)
    # The account's email or the user id: the row outlives the account it describes.
    target: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    """An account. Created by accepting an invite (or by seed-admin, without a password)."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'member')", name="role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    role: Mapped[str] = mapped_column(Text, server_default="member")
    password_hash: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(server_default=true())
    player_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("players.id", use_alter=True), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime | None]
    # The wire's Preferences without updatedAt: `{theme?, setup?}`, camelCase.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    preferences_updated_at: Mapped[datetime | None]


class Invite(Base):
    """A one-time link to create an account; only the token's SHA-256 is stored."""

    __tablename__ = "invites"
    __table_args__ = (
        Index(
            "ix_invites_email_pending",
            "email",
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(CITEXT)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class PasswordReset(Base):
    """A one-time password reset link made by the admin."""

    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class Session(Base):
    """A signed-in browser; the cookie holds the token, the row only its hash."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(index=True)
    user_agent: Mapped[str | None] = mapped_column(Text)


class RateLimit(Base):
    """Fixed-window request counters."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(Integer, server_default="0")


class Player(Base):
    """A registry player. The id is generated by the client."""

    __tablename__ = "players"
    __table_args__ = (
        Index(
            "uq_players_name_key_active",
            "name_key",
            unique=True,
            postgresql_where=text("merged_into IS NULL"),
        ),
        Index("ix_players_updated_at_id", "updated_at", "id"),
        Index(
            "uq_players_email_active",
            "email",
            unique=True,
            postgresql_where=text("merged_into IS NULL AND email IS NOT NULL"),
        ),
        Index(
            "ix_players_name_key_trgm",
            "name_key",
            postgresql_using="gin",
            postgresql_ops={"name_key": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    # Identifies the person (with the name); never used to send mail.
    email: Mapped[str | None] = mapped_column(CITEXT)
    photo_rev: Mapped[int] = mapped_column(Integer, server_default="0")
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    emoji: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", use_alter=True))
    merged_into: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    # Set when the account behind it was deleted and other people's games still use it.
    anonymised_at: Mapped[datetime | None]
    # Maintained on every game write so summaries never count rows on read.
    games_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_played_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PlayerAlias(Base):
    """Another name a player is known by. Its name key is reserved like a display name.

    A trigger (migration 0011) refuses a name key that another live player holds as name or alias.
    """

    __tablename__ = "player_aliases"
    __table_args__ = (
        # Aliases always belong to live players (a merge moves them), so the key is unique outright.
        Index("uq_player_aliases_name_key", "name_key", unique=True),
        Index("ix_player_aliases_player_id", "player_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Game(Base):
    """A game stored whole as the app's JSON, plus the columns lists and jobs filter on."""

    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'finished')", name="status"),
        # Sync and list order; also serves `updatedSince` tombstones, so it is not partial.
        Index("ix_games_updated_at_id", "updated_at", "id"),
        Index("ix_games_owner_id_updated_at", "owner_id", text("updated_at DESC")),
        # A user's unfinished games, newest change first: the Home screen's resume card.
        Index(
            "ix_games_owner_id_active",
            "owner_id",
            text("updated_at DESC"),
            text("id DESC"),
            postgresql_where=text("status = 'active' AND ended_at IS NULL AND deleted_at IS NULL"),
        ),
        Index("ix_games_ended_at_live", "ended_at", postgresql_where=text("deleted_at IS NULL")),
        # `GET /api/games?involving=true`: games a player sits in, deleted ones included.
        Index(
            "ix_games_payload_players",
            text("(payload -> 'players') jsonb_path_ops"),
            postgresql_using="gin",
        ),
        Index(
            "ix_games_auto_end",
            "client_updated_at",
            postgresql_where=text("status = 'active' AND ended_at IS NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    rev: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    player_count: Mapped[int] = mapped_column(SmallInteger)
    round_count: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime]
    client_updated_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    auto_ended: Mapped[bool] = mapped_column(server_default=false())
    last_done_at: Mapped[datetime | None]
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None]


class GamePlayer(Base):
    """One player's whole game; rebuilt from the payload on every upload."""

    __tablename__ = "game_players"
    __table_args__ = (
        Index(
            "ix_game_players_player_id",
            "player_id",
            postgresql_include=[
                "game_id",
                "points",
                "place",
                "won",
                "rounds",
                "first_done_at",
                "last_done_at",
                "ended_at",
            ],
        ),
        Index(
            "ix_game_players_ended_at",
            "ended_at",
            postgresql_include=["player_id", "place", "won"],
            postgresql_where=text("ended_at IS NOT NULL"),
        ),
        Index(
            "ix_game_players_last_done_at",
            "last_done_at",
            postgresql_include=["player_id", "first_done_at"],
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    local_id: Mapped[str] = mapped_column(Text, primary_key=True)
    seat: Mapped[int] = mapped_column(SmallInteger)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    name_snapshot: Mapped[str] = mapped_column(Text)
    emoji: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer)
    place: Mapped[int | None] = mapped_column(SmallInteger)
    won: Mapped[bool]
    last: Mapped[bool]
    rounds: Mapped[int] = mapped_column(Integer)
    made: Mapped[int] = mapped_column(Integer)
    missed: Mapped[int] = mapped_column(Integer)
    best_round: Mapped[int | None] = mapped_column(Integer)
    worst_round: Mapped[int | None] = mapped_column(Integer)
    longest_hot: Mapped[int] = mapped_column(Integer)
    longest_cold: Mapped[int] = mapped_column(Integer)
    hook_hits: Mapped[int] = mapped_column(Integer)
    first_done_at: Mapped[datetime | None]
    last_done_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]


class RoundScore(Base):
    """One player's part in one done round; rebuilt from the payload on every upload."""

    __tablename__ = "round_scores"
    __table_args__ = (
        Index(
            "ix_round_scores_player_id_done_at",
            "player_id",
            "done_at",
            postgresql_include=[
                "points",
                "outcome",
                "run_hot",
                "run_cold",
                "hook_hits",
                "bid",
                "cards",
                "trump",
                "is_dealer",
            ],
        ),
        Index("ix_round_scores_done_at", "done_at"),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    round_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    done_at: Mapped[datetime]
    cards: Mapped[int] = mapped_column(SmallInteger)
    max_cards: Mapped[int] = mapped_column(SmallInteger)
    trump: Mapped[str] = mapped_column(Text)
    bid: Mapped[int] = mapped_column(SmallInteger)
    outcome: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer)
    is_dealer: Mapped[bool]
    hook_hits: Mapped[int] = mapped_column(SmallInteger)
    run_hot: Mapped[int] = mapped_column(Integer)
    run_cold: Mapped[int] = mapped_column(Integer)
    table_size: Mapped[int] = mapped_column(SmallInteger)
    made_count: Mapped[int] = mapped_column(SmallInteger)


class PlayerDay(Base):
    """Per player per APP_TZ day rollup, rebuilt for the days an upload touches (plan §15.3)."""

    __tablename__ = "player_day"
    __table_args__ = (
        Index(
            "ix_player_day_day",
            "day",
            postgresql_include=[
                "player_id",
                "rounds",
                "points",
                "made",
                "wins",
                "best_round",
                "worst_round",
                "max_hot",
                "max_cold",
                "hook_hits",
            ],
        ),
    )

    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    rounds: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    made: Mapped[int] = mapped_column(Integer)
    missed: Mapped[int] = mapped_column(Integer)
    best_round: Mapped[int | None] = mapped_column(Integer)
    worst_round: Mapped[int | None] = mapped_column(Integer)
    max_hot: Mapped[int] = mapped_column(Integer)
    max_cold: Mapped[int] = mapped_column(Integer)
    hook_hits: Mapped[int] = mapped_column(Integer)
    # Sum of positive round scores: the "points earned" career milestones count these.
    pos_points: Mapped[int] = mapped_column(Integer, server_default="0")
    games: Mapped[int] = mapped_column(Integer)
    games_ended: Mapped[int] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer)


class Achievement(Base):
    """An earned achievement, daily award or season award. Unique per key, NULLs equal."""

    __tablename__ = "achievements"
    __table_args__ = (
        Index(
            "uq_achievements_key",
            "player_id",
            "code",
            "game_id",
            "round_index",
            "period",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_achievements_player_id_earned_at",
            "player_id",
            text("earned_at DESC"),
            text("id DESC"),
        ),
        Index("ix_achievements_code_period", "code", "period"),
        Index("ix_achievements_game_id", "game_id"),
        Index("ix_achievements_earned_at_id", text("earned_at DESC"), text("id DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    code: Mapped[str] = mapped_column(Text)
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    round_index: Mapped[int | None] = mapped_column(Integer)
    period: Mapped[str | None] = mapped_column(Text)
    earned_at: Mapped[datetime]
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Activity(Base):
    """The feed. Achievement rows go when their achievement is un-earned (cascade)."""

    __tablename__ = "activity"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined')",
            name="kind",
        ),
        Index("ix_activity_created_at_id", text("created_at DESC"), text("id DESC")),
        Index("ix_activity_game_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text)
    player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    achievement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE")
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ClosedPeriod(Base):
    """A finalised day or season: its awards (and season standings), frozen."""

    __tablename__ = "closed_periods"
    __table_args__ = (CheckConstraint("kind IN ('day', 'season')", name="kind"),)

    period: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    closed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ShareLink(Base):
    """A live or recap link. The token is stored plain: the owner shows it again as a QR."""

    __tablename__ = "share_links"
    __table_args__ = (
        CheckConstraint("kind IN ('live', 'recap')", name="kind"),
        Index(
            "ix_share_links_game_id_kind_active",
            "game_id",
            "kind",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]


class PlayerPhoto(Base):
    """A player's photo: 256x256 WebP, re-encoded on upload (no EXIF)."""

    __tablename__ = "player_photos"

    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    image: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SeatClaim(Base):
    """ "That seat is me": a signed-in person asks to take over a player that has no account."""

    __tablename__ = "seat_claims"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        Index(
            "uq_seat_claims_pending",
            "player_id",
            "claimant_user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    claimant_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None]
