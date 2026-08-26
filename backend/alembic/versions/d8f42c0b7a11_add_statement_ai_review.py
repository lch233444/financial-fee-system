"""add separate AI review fields to statement imports

Revision ID: d8f42c0b7a11
Revises: b3c4b22cde0d
Create Date: 2026-08-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f42c0b7a11"
down_revision: Union[str, Sequence[str], None] = "b3c4b22cde0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("statement_imports") as batch_op:
        batch_op.add_column(sa.Column("ai_recognition_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("ai_status", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("ai_model", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("ai_recognized_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_statement_imports_ai_status", ["ai_status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("statement_imports") as batch_op:
        batch_op.drop_index("ix_statement_imports_ai_status")
        batch_op.drop_column("ai_recognized_at")
        batch_op.drop_column("ai_model")
        batch_op.drop_column("ai_status")
        batch_op.drop_column("ai_recognition_json")
