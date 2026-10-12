"""web_recorder source + nullable meeting_url + language column + participants partial unique index

Schema changes for the in-browser web recorder feature plus general cleanup
of the meeting_url contract that's been carrying a `"upload://local"`
placeholder since the upload feature shipped.

Changes:

* meetings.language (NEW): nullable VARCHAR(16) holding a BCP-47 language
  code or NULL ("multilingual / auto-detect"). NULL preserves today's
  behavior for every existing row. Used by the /record page language
  picker and forwarded to transcription params by tab_audio.py.

* meetings.meeting_url: drop NOT NULL. Honest schema — uploads and the
  new web_recorder source have no real meeting URL. Backfills the
  existing "upload://local" placeholder rows to NULL in a one-shot pass.

* meeting_participants partial unique index on
  (meeting_id, LOWER(platform_display_name), mapping_source)
  WHERE diarization_label IS NULL. Makes participant inserts idempotent
  under retry. Co-exists with the existing post-diarization unique
  constraint on (meeting_id, diarization_label). Includes a pre-dedup
  pass to remove existing pre-diarization duplicates before adding the
  index.

Note: meetings.source and meetings.capture_mode are free-text VARCHARs
today with no DB-level CHECK constraint, AND the Pydantic schema declares
them as plain `str` (not Literal). The new "web_recorder" value rides
into production via the explicit `body.source == "web_recorder" and
body.meeting_url` assertion in the create_meeting handler; arbitrary
source strings are not currently rejected at the schema level. Tightening
to Literal[...] enums is a follow-up. If we ever add CHECK constraints
on these columns later, this migration would need a follow-up to include
the new values.

Revision ID: 040_web_recorder_source
Revises: 039_notetaker_bot_calendar
Create Date: 2026-05-23 18:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "040_web_recorder_source"
down_revision: Union[str, None] = "039_notetaker_bot_calendar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── meetings.language ───────────────────────────────────────────
    # Nullable VARCHAR(16). NULL means "multilingual / auto-detect"
    # which is today's hardcoded behavior for every meeting.
    op.add_column(
        "meetings",
        sa.Column("language", sa.String(length=16), nullable=True),
    )

    # ── meetings.meeting_url: drop NOT NULL + backfill placeholder ──
    # The upload endpoint at core_api/app/api/meetings.py used to write
    # "upload://local" as a placeholder because uploaded files have no
    # real meeting URL. The new web_recorder source also has no URL.
    # Honest schema: NULL means "no meeting URL exists for this row."
    op.alter_column(
        "meetings",
        "meeting_url",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.execute(
        """
        UPDATE meetings
        SET meeting_url = NULL
        WHERE meeting_url = 'upload://local'
        """
    )

    # ── meeting_participants pre-dial partial unique index ──────────
    # Removes existing pre-diarization duplicates first so the unique
    # index can be created. Keeps the earliest row, breaking same-
    # millisecond ties on id (UUID v4 — random but stable).
    #
    # Restricts dedup to rows where platform_display_name IS NOT NULL
    # because LOWER(NULL) = NULL, and NULLs are treated as distinct by
    # Postgres unique indexes — so NULL-named rows never collide with
    # the partial unique index anyway.
    op.execute(
        """
        DELETE FROM meeting_participants p1
        USING meeting_participants p2
        WHERE p1.diarization_label IS NULL
          AND p2.diarization_label IS NULL
          AND p1.meeting_id = p2.meeting_id
          AND p1.platform_display_name IS NOT NULL
          AND p2.platform_display_name IS NOT NULL
          AND LOWER(p1.platform_display_name) = LOWER(p2.platform_display_name)
          AND p1.mapping_source = p2.mapping_source
          AND (
                p1.created_at > p2.created_at
             OR (p1.created_at = p2.created_at AND p1.id > p2.id)
          )
        """
    )

    # Partial unique index — applies only to pre-diarization rows
    # (where diarization_label IS NULL). Co-exists with the existing
    # uq_meeting_participants_meeting_label constraint which targets
    # post-diarization rows.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_meeting_participants_predial_name
        ON meeting_participants (
            meeting_id,
            LOWER(platform_display_name),
            mapping_source
        )
        WHERE diarization_label IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_meeting_participants_predial_name")
    # NOT NULL is intentionally NOT re-imposed on downgrade. Any web_recorder
    # or upload meeting written post-migration has meeting_url=NULL; flipping
    # the constraint back would raise on the existing data. If a hard rollback
    # to the pre-040 schema is required, first backfill those rows with a
    # sentinel string (e.g. UPDATE … SET meeting_url='downgrade://placeholder'
    # WHERE meeting_url IS NULL) THEN alter the column. Doing it here would
    # break the migration mid-flight.
    op.drop_column("meetings", "language")
