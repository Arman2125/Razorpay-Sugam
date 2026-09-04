"""add conversation messages

Revision ID: 3a7f67d09efe
Revises: 8128b4d7a626
Create Date: 2026-09-05 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '3a7f67d09efe'
down_revision: Union[str, None] = '8128b4d7a626'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('conversation_messages',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('whatsapp_number', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('tool_call_id', sa.Text(), nullable=True),
    sa.Column('tool_name', sa.Text(), nullable=True),
    sa.Column('tool_arguments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversation_messages_whatsapp_number'), 'conversation_messages', ['whatsapp_number'], unique=False)
    op.create_index(op.f('ix_conversation_messages_created_at'), 'conversation_messages', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_conversation_messages_created_at'), table_name='conversation_messages')
    op.drop_index(op.f('ix_conversation_messages_whatsapp_number'), table_name='conversation_messages')
    op.drop_table('conversation_messages')
