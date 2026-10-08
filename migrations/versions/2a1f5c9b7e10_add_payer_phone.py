"""add payer phone number to payments

Revision ID: 2a1f5c9b7e10
Revises: 7cedd68f9064
"""
from alembic import op
import sqlalchemy as sa

revision = "2a1f5c9b7e10"
down_revision = "7cedd68f9064"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(sa.Column("payer_phone_number", sa.String(length=32), nullable=True))

def downgrade():
    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_column("payer_phone_number")
