"""add_outbound_calls_table

Revision ID: 12ba26e77ad3
Revises: 8e505dff2ae6
Create Date: 2025-11-19 14:15:49.217604

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '12ba26e77ad3'
down_revision: Union[str, Sequence[str], None] = '8e505dff2ae6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        'outbound_calls',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('call_sid', sa.String(), nullable=False),
        sa.Column('to_number', sa.String(), nullable=False),
        sa.Column('from_number', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('attempt_number', sa.Integer(), nullable=True),
        sa.Column('lead_data', sa.JSON(), nullable=True),
        sa.Column('duration', sa.Integer(), nullable=True),
        sa.Column('recording_url', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    op.drop_table('outbound_calls')
