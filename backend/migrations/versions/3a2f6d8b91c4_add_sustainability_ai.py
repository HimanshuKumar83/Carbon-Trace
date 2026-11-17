"""add sustainability knowledge and AI interactions

Revision ID: 3a2f6d8b91c4
Revises: 0d8df371114c
Create Date: 2026-11-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "3a2f6d8b91c4"
down_revision = "0d8df371114c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "content_sha256", name="uq_knowledge_document_org_sha256"),
    )
    op.create_index("ix_knowledge_documents_organization_id", "knowledge_documents", ["organization_id"])
    op.create_index("ix_knowledge_documents_org_status", "knowledge_documents", ["organization_id", "status"])
    op.create_table(
        "ai_interactions",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("interaction_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_interactions_organization_id", "ai_interactions", ["organization_id"])
    op.create_index("ix_ai_interactions_org_created", "ai_interactions", ["organization_id", "created_at"])
    op.create_index("ix_ai_interactions_org_type", "ai_interactions", ["organization_id", "interaction_type"])


def downgrade() -> None:
    op.drop_index("ix_ai_interactions_org_type", table_name="ai_interactions")
    op.drop_index("ix_ai_interactions_org_created", table_name="ai_interactions")
    op.drop_index("ix_ai_interactions_organization_id", table_name="ai_interactions")
    op.drop_table("ai_interactions")
    op.drop_index("ix_knowledge_documents_org_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_organization_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
