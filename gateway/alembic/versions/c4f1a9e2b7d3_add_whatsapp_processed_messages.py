"""add whatsapp processed messages

Revision ID: c4f1a9e2b7d3
Revises: 3a7f67d09efe
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1a9e2b7d3'
down_revision: Union[str, None] = '3a7f67d09efe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('whatsapp_processed_messages',
    sa.Column('message_id', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('message_id')
    )


def downgrade() -> None:
    op.drop_table('whatsapp_processed_messages')
