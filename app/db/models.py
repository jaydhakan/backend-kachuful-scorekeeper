"""Tables. Constraint names come from the naming convention, so migrations stay predictable."""

import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Double,
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
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID
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
        # The server's circle of a user starts from the players they created.
        Index("ix_players_created_by", "created_by"),
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
        CheckConstraint(
            "kind IN ('kachuful', 'kaali-teeri', 'ishaara', 'spades', 'spades-online')",
            name="kind",
        ),
        # Sync and list order per game; also serves `updatedSince` tombstones, so it is not partial.
        Index("ix_games_kind_updated_at_id", "kind", "updated_at", "id"),
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
        # An open Spades table's join code is unique.
        Index(
            "uq_games_spades_code",
            text("(summary ->> 'code')"),
            unique=True,
            postgresql_where=text(
                "kind = 'spades-online' AND status = 'active' AND deleted_at IS NULL"
            ),
        ),
        Index(
            "ix_games_auto_end",
            "client_updated_at",
            postgresql_where=text("status = 'active' AND ended_at IS NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # A `GameKind` value; the payload's shape and every derived table depend on it.
    kind: Mapped[str] = mapped_column(Text, server_default=text("'kachuful'"))
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


class KtGamePlayer(Base):
    """One player's whole Kaali Teeri game; rebuilt from the payload on every upload."""

    __tablename__ = "kt_game_players"
    __table_args__ = (
        Index(
            "ix_kt_game_players_player_id",
            "player_id",
            postgresql_include=["game_id", "points", "place", "last_done_at"],
        ),
        Index(
            "ix_kt_game_players_first_done_at", "first_done_at", postgresql_include=["player_id"]
        ),
        Index(
            "ix_kt_game_players_ended_at",
            "ended_at",
            postgresql_include=["player_id", "won"],
            postgresql_where=text("ended_at IS NOT NULL"),
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    seat: Mapped[int] = mapped_column(SmallInteger)
    points: Mapped[int] = mapped_column(Integer)
    place: Mapped[int | None] = mapped_column(SmallInteger)
    won: Mapped[bool]
    hands_bid: Mapped[int] = mapped_column(Integer)
    bids_made: Mapped[int] = mapped_column(Integer)
    times_partner: Mapped[int] = mapped_column(Integer)
    partner_made: Mapped[int] = mapped_column(Integer)
    double_slots: Mapped[int] = mapped_column(Integer)
    sweeps: Mapped[int] = mapped_column(Integer)
    first_done_at: Mapped[datetime]
    last_done_at: Mapped[datetime]
    # The finish time once the game is finished (Kaali Teeri games never auto-end).
    ended_at: Mapped[datetime | None]


class KtHand(Base):
    """One done Kaali Teeri hand, players by registry id; rebuilt on every upload."""

    __tablename__ = "kt_hands"
    __table_args__ = (
        CheckConstraint("sweep IN ('none', 'bidders', 'defenders')", name="sweep"),
        Index("ix_kt_hands_done_at", "done_at"),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    hand_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    bidder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    bid: Mapped[int] = mapped_column(SmallInteger)
    forced: Mapped[bool]
    trump: Mapped[str] = mapped_column(Text)
    partner_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    # {playerId: slots} for the bidding team.
    slots: Mapped[dict[str, int]] = mapped_column(JSONB)
    team_points: Mapped[int] = mapped_column(SmallInteger)
    made: Mapped[bool]
    sweep: Mapped[str] = mapped_column(Text)
    # {playerId: points} for every seat: kt_player_day sums these.
    scores: Mapped[dict[str, int]] = mapped_column(JSONB)
    # The hand's doneAt, else the game's updatedAt (a done hand always counts on some day).
    done_at: Mapped[datetime]


class KtPlayerDay(Base):
    """Per player per APP_TZ day Kaali Teeri rollup, rebuilt for the days an upload touches."""

    __tablename__ = "kt_player_day"
    __table_args__ = (
        Index(
            "ix_kt_player_day_day",
            "day",
            postgresql_include=[
                "player_id",
                "games",
                "wins",
                "points",
                "hands_bid",
                "bids_made",
                "partner_hands",
                "partner_made",
            ],
        ),
    )

    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    games: Mapped[int] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    hands_bid: Mapped[int] = mapped_column(Integer)
    bids_made: Mapped[int] = mapped_column(Integer)
    partner_hands: Mapped[int] = mapped_column(Integer)
    partner_made: Mapped[int] = mapped_column(Integer)


class IshaaraKey(Base):
    """An Ishaara game's key: 25 identities, apart from the public state in `games.payload`."""

    __tablename__ = "ishaara_keys"

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class IshaaraRole(Base):
    """One seat of an Ishaara game: its team and role, and the account linked to it at creation."""

    __tablename__ = "ishaara_roles"
    __table_args__ = (
        CheckConstraint("team IN ('sun', 'moon')", name="team"),
        CheckConstraint("role IN ('clue-giver', 'guesser')", name="role"),
        Index(
            "uq_ishaara_roles_clue_giver",
            "game_id",
            "team",
            unique=True,
            postgresql_where=text("role = 'clue-giver'"),
        ),
        Index("ix_ishaara_roles_player_id", "player_id", postgresql_include=["game_id"]),
        Index(
            "ix_ishaara_roles_user_id_clue_giver",
            "user_id",
            postgresql_include=["game_id", "team"],
            postgresql_where=text("role = 'clue-giver'"),
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    team: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)


class IshaaraAction(Base):
    """An applied Ishaara action: the audit log, and what makes a retried `actionId` a no-op."""

    __tablename__ = "ishaara_actions"
    __table_args__ = (Index("uq_ishaara_actions_game_id_seq", "game_id", "seq", unique=True),)

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    action_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    # The action as sent, without the key (actions never carry it).
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime]
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


_ISHAARA_ROW_COLUMNS = [
    "game_id",
    "team",
    "role",
    "won",
    "words_found",
    "wrong_picks",
    "trap_hit",
    "clues_given",
    "avg_clue_number",
    "penalties",
]


class IshaaraGamePlayer(Base):
    """One seat of a finished (not abandoned) Ishaara game: the domain's player row."""

    __tablename__ = "ishaara_game_players"
    __table_args__ = (
        Index(
            "ix_ishaara_game_players_player_id",
            "player_id",
            postgresql_include=[*_ISHAARA_ROW_COLUMNS, "finished_at"],
        ),
        Index(
            "ix_ishaara_game_players_finished_at",
            "finished_at",
            postgresql_include=["player_id", *_ISHAARA_ROW_COLUMNS],
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    team: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    won: Mapped[bool]
    words_found: Mapped[int] = mapped_column(SmallInteger)
    wrong_picks: Mapped[int] = mapped_column(SmallInteger)
    trap_hit: Mapped[bool]
    clues_given: Mapped[int] = mapped_column(SmallInteger)
    avg_clue_number: Mapped[float | None] = mapped_column(Double)
    penalties: Mapped[int] = mapped_column(SmallInteger)
    finished_at: Mapped[datetime]


class IshaaraPlayerDay(Base):
    """Per player per APP_TZ day Ishaara rollup (the domain's tallies), rebuilt when a game ends."""

    __tablename__ = "ishaara_player_day"
    __table_args__ = (Index("ix_ishaara_player_day_day", "day"),)

    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    games: Mapped[int] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer)
    words_found: Mapped[int] = mapped_column(Integer)
    wrong_picks: Mapped[int] = mapped_column(Integer)
    trap_hits: Mapped[int] = mapped_column(Integer)
    clue_games: Mapped[int] = mapped_column(Integer)
    clue_wins: Mapped[int] = mapped_column(Integer)
    clues_given: Mapped[int] = mapped_column(Integer)
    # The sum and count of per-game average clue numbers, so a period's mean is exact.
    avg_clue_sum: Mapped[float] = mapped_column(Double)
    avg_clue_games: Mapped[int] = mapped_column(Integer)
    penalties: Mapped[int] = mapped_column(Integer)


_SPADES_ROW_COLUMNS = [
    "game_id",
    "kind",
    "seat",
    "team",
    "partner_id",
    "result",
    "score",
    "opp_score",
    "hands",
    "bids",
    "bids_made",
    "nils",
    "nils_made",
    "blind_nils",
    "blind_nils_made",
    "bags",
    "sets",
]


class SpadesSeat(Base):
    """A seat of a table-mode Spades game (kind `spades`): the registry's seat table for it."""

    __tablename__ = "spades_seats"
    __table_args__ = (
        Index(
            "ix_spades_seats_player_id",
            "player_id",
            postgresql_include=["game_id", "last_done_at"],
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    seat: Mapped[int] = mapped_column(SmallInteger)
    last_done_at: Mapped[datetime | None]


class SpadesTableSeat(Base):
    """A player at an online Spades table (kind `spades-online`); `seat` is null once replaced.

    The registry's seat table for the kind, and where each seat's account and presence live.
    """

    __tablename__ = "spades_table_seats"
    __table_args__ = (
        Index(
            "uq_spades_table_seats_game_id_seat",
            "game_id",
            "seat",
            unique=True,
            postgresql_where=text("seat IS NOT NULL"),
        ),
        Index(
            "ix_spades_table_seats_player_id",
            "player_id",
            postgresql_include=["game_id", "last_done_at"],
        ),
        Index(
            "ix_spades_table_seats_user_id",
            "user_id",
            postgresql_include=["game_id", "seat"],
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    seat: Mapped[int | None] = mapped_column(SmallInteger)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_done_at: Mapped[datetime | None]
    # Stamped by the seat's stream (open and pings) and its actions: the timer-off away check.
    last_seen_at: Mapped[datetime | None]


class SpadesHand(Base):
    """One seat's private cards at an online table; never in `games.payload`."""

    __tablename__ = "spades_hands"

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    seat: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    cards: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class SpadesAction(Base):
    """An applied Spades action: the audit log, and what makes a retried `actionId` a no-op."""

    __tablename__ = "spades_actions"
    __table_args__ = (Index("uq_spades_actions_game_id_seq", "game_id", "seq", unique=True),)

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    action_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime]
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class SpadesGamePlayer(Base):
    """One player of a finished (not cancelled) Spades game of either kind, for boards and stats.

    `result` is null for someone replaced before the end: their hands count, the game doesn't.
    """

    __tablename__ = "spades_game_players"
    __table_args__ = (
        CheckConstraint("kind IN ('spades', 'spades-online')", name="kind"),
        CheckConstraint("team IN ('a', 'b')", name="team"),
        CheckConstraint("result IN ('win', 'loss', 'draw')", name="result"),
        Index(
            "ix_spades_game_players_player_id",
            "player_id",
            postgresql_include=[*_SPADES_ROW_COLUMNS, "finished_at"],
        ),
        Index(
            "ix_spades_game_players_finished_at",
            "finished_at",
            postgresql_include=["player_id", *_SPADES_ROW_COLUMNS],
        ),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    seat: Mapped[int] = mapped_column(SmallInteger)
    team: Mapped[str] = mapped_column(Text)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    result: Mapped[str | None] = mapped_column(Text)
    ended_by: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    opp_score: Mapped[int] = mapped_column(Integer)
    hands: Mapped[int] = mapped_column(Integer)
    bids: Mapped[int] = mapped_column(Integer)
    bids_made: Mapped[int] = mapped_column(Integer)
    nils: Mapped[int] = mapped_column(Integer)
    nils_made: Mapped[int] = mapped_column(Integer)
    blind_nils: Mapped[int] = mapped_column(Integer)
    blind_nils_made: Mapped[int] = mapped_column(Integer)
    bags: Mapped[int] = mapped_column(Integer)
    sets: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[datetime]


# The game an achievement, feed item or closed period belongs to (both Spades kinds are `spades`).
_GAMES = "game IN ('kachuful', 'spades', 'kaali-teeri', 'ishaara')"


class Achievement(Base):
    """An earned achievement, daily award or season award. Unique per key, NULLs equal."""

    __tablename__ = "achievements"
    __table_args__ = (
        CheckConstraint(_GAMES, name="game"),
        Index(
            "uq_achievements_key",
            "game",
            "player_id",
            "code",
            "game_id",
            "round_index",
            "period",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_achievements_player_id_game_earned_at",
            "player_id",
            "game",
            text("earned_at DESC"),
            text("id DESC"),
        ),
        Index("ix_achievements_code_period", "code", "period"),
        Index("ix_achievements_game_id", "game_id"),
        Index("ix_achievements_earned_at_id", text("earned_at DESC"), text("id DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    game: Mapped[str] = mapped_column(Text, server_default=text("'kachuful'"))
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
            "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown', "
            "'night', 'gift')",
            name="kind",
        ),
        CheckConstraint(_GAMES, name="game"),
        Index("ix_activity_created_at_id", text("created_at DESC"), text("id DESC")),
        Index("ix_activity_game_created_at_id", "game", text("created_at DESC"), text("id DESC")),
        Index("ix_activity_game_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game: Mapped[str] = mapped_column(Text, server_default=text("'kachuful'"))
    kind: Mapped[str] = mapped_column(Text)
    player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    achievement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE")
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SikkeEntry(Base):
    """One Sikke credit of a registry player (rewards plan §9). Recomputed, so unique per key."""

    __tablename__ = "sikke_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('finish', 'win', 'achievement', 'streak', 'bounty', 'sitara')",
            name="kind",
        ),
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint(f"game IS NULL OR {_GAMES}", name="game"),
        Index("uq_sikke_entries_player_id_key", "player_id", "key", unique=True),
        Index(
            "ix_sikke_entries_player_id_earned_at",
            "player_id",
            text("earned_at DESC"),
            text("id DESC"),
        ),
        # `GET /api/games/{id}/sikke`, and the cascades from games and achievements.
        Index("ix_sikke_entries_game_id", "game_id"),
        Index("ix_sikke_entries_achievement_id", "achievement_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(Integer)
    # The progression game (both Spades kinds are `spades`).
    game: Mapped[str | None] = mapped_column(Text)
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    achievement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE")
    )
    # The source's time: the game's end or the achievement's earning.
    earned_at: Mapped[datetime]
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Gift(Base):
    """An item one player sent another (rewards finish §5); the receiver owns it at once.

    The id is the sender's `giftId`, so a resend finds the first gift.
    """

    __tablename__ = "gifts"
    __table_args__ = (
        CheckConstraint("variant >= 0", name="variant"),
        Index(
            "ix_gifts_to_player_id_sent_at_id",
            "to_player_id",
            text("sent_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_gifts_from_player_id_sent_at_id",
            "from_player_id",
            text("sent_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_gifts_to_player_id_unopened",
            "to_player_id",
            postgresql_where=text("opened_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    from_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    to_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    sent_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    item_id: Mapped[str] = mapped_column(Text)
    variant: Mapped[int] = mapped_column(SmallInteger)
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now())
    opened_at: Mapped[datetime | None]


class SikkeSpend(Base):
    """Sikke spent on an item, bought or given (rewards finish §4). Append-only: no recompute
    touches it; a balance is lifetime earned minus these amounts."""

    __tablename__ = "sikke_spends"
    __table_args__ = (
        CheckConstraint("kind IN ('purchase', 'gift')", name="kind"),
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("variant >= 0", name="variant"),
        CheckConstraint("kind = 'gift' OR gift_id IS NULL", name="gift_id"),
        Index(
            "uq_sikke_spends_player_id_idempotency_key", "player_id", "idempotency_key", unique=True
        ),
        Index("ix_sikke_spends_gift_id", "gift_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str] = mapped_column(Text)
    variant: Mapped[int] = mapped_column(SmallInteger)
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Null on a gift spend once the gift went with its receiver (an erased player).
    gift_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gifts.id", ondelete="SET NULL"))
    # The client's purchaseId or giftId: the same key returns the first result.
    idempotency_key: Mapped[str] = mapped_column(Text)


class WardrobeLook(Base):
    """The look a player wears: the wire's Look, camelCase."""

    __tablename__ = "wardrobe_looks"

    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    look: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WardrobeSavedLook(Base):
    """One of a player's three saved looks (it may hold items not owned yet)."""

    __tablename__ = "wardrobe_saved_looks"
    __table_args__ = (CheckConstraint("slot BETWEEN 0 AND 2", name="slot"),)

    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    look: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


_CROWN_KINDS = "kind IN ('medal1', 'medal2', 'medal3', 'sitara', 'legend')"


class Crown(Base):
    """Who holds each title of a game now (rewards plan §6); empty `holders` when nobody does."""

    __tablename__ = "crowns"
    __table_args__ = (
        CheckConstraint(_CROWN_KINDS, name="kind"),
        CheckConstraint(_GAMES, name="game"),
    )

    game: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    # Sorted registry ids; no FK on an array, so reads follow merges.
    holders: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    since: Mapped[datetime]
    since_game: Mapped[uuid.UUID | None]
    defences: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class CrownReign(Base):
    """A closed reign: who held a title from `since` until `until`."""

    __tablename__ = "crown_reigns"
    __table_args__ = (
        CheckConstraint(_CROWN_KINDS, name="kind"),
        CheckConstraint(_GAMES, name="game"),
        Index("ix_crown_reigns_game_kind_until", "game", "kind", text("until DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    game: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    holders: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    since: Mapped[datetime]
    until: Mapped[datetime]
    since_game: Mapped[uuid.UUID | None]
    until_game: Mapped[uuid.UUID | None]
    defences: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Night(Base):
    """A closed night (rewards plan §7.2): its games, players and Wrapped story.

    The id is uuid5 of the night's earliest game, so a recompute keeps it while that game stands.
    """

    __tablename__ = "nights"
    __table_args__ = (
        Index("ix_nights_players", "players", postgresql_using="gin"),
        Index("ix_nights_games", "games", postgresql_using="gin"),
        Index("ix_nights_ended_at_id", text("ended_at DESC"), text("id DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime]
    players: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    games: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    closed_at: Mapped[datetime | None]


class ClosedPeriod(Base):
    """A finalised day or season of one game: its awards (and season standings), frozen."""

    __tablename__ = "closed_periods"
    __table_args__ = (CheckConstraint("kind IN ('day', 'season')", name="kind"),)

    game: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("'kachuful'"))
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
    # Live links only: spectators may react; only signed-in members may watch.
    reactions: Mapped[bool] = mapped_column(server_default=true())
    members_only: Mapped[bool] = mapped_column(server_default=false())


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


class SeatInvite(Base):
    """A host invites a signed-in person to a seat of a server-run game.

    Expiry is worked out on read; a stored `pending` row may already count as expired.
    """

    __tablename__ = "seat_invites"
    __table_args__ = (
        CheckConstraint("kind IN ('ishaara', 'spades-online')", name="kind"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')", name="status"
        ),
        Index(
            "uq_seat_invites_pending",
            "game_id",
            "to_user",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_seat_invites_to_user_status", "to_user", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    kind: Mapped[str] = mapped_column(Text)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    seat: Mapped[str] = mapped_column(Text)
    from_user: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    to_user: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    responded_at: Mapped[datetime | None]
