"""add_resource_permissions

Revision ID: a1b2c3d4e5f6
Revises: 870eecb8b196
Create Date: 2026-02-15 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '870eecb8b196'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('resource_permissions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('team_id', sa.UUID(), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.UUID(), nullable=False),
        sa.Column('subject_type', sa.String(length=20), nullable=False),
        sa.Column('subject_id', sa.UUID(), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('granted_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'resource_type', 'resource_id', 'subject_type', 'subject_id', 'action',
            name='uq_resource_subject_action',
        ),
    )
    op.create_index(
        'ix_resource_perms_subject', 'resource_permissions',
        ['subject_type', 'subject_id'], unique=False,
    )
    op.create_index(
        'ix_resource_perms_resource', 'resource_permissions',
        ['resource_type', 'resource_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_resource_perms_resource', table_name='resource_permissions')
    op.drop_index('ix_resource_perms_subject', table_name='resource_permissions')
    op.drop_table('resource_permissions')
