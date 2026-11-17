from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDTimestampMixin


class Organization(UUIDTimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    reporting_currency: Mapped[str | None] = mapped_column(String(3))
    reporting_preferences: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class User(UUIDTimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class OrganizationMember(UUIDTimestampMixin, Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_organization_user"),
        CheckConstraint("role IN ('owner', 'admin', 'analyst', 'viewer')", name="ck_member_role"),
        CheckConstraint("status IN ('invited', 'active', 'suspended')", name="ck_member_status"),
        Index("ix_members_user_status", "user_id", "status"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")


class DataSource(UUIDTimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (Index("ix_sources_org_type", "organization_id", "source_type"),)

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class IngestionRun(UUIDTimestampMixin, Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "data_source_id", "idempotency_key", name="uq_ingestion_idempotency"),
        CheckConstraint("records_seen >= 0 AND records_accepted >= 0 AND records_rejected >= 0", name="ck_ingestion_counts_nonnegative"),
        Index("ix_ingestion_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    data_source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ActivityRecord(UUIDTimestampMixin, Base):
    __tablename__ = "activity_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "ingestion_run_id", "record_fingerprint", name="uq_activity_source_fingerprint"),
        CheckConstraint("scope IS NULL OR scope IN (1, 2, 3)", name="ck_activity_scope"),
        Index("ix_activity_org_date", "organization_id", "activity_date"),
        Index("ix_activity_org_category_date", "organization_id", "activity_category", "activity_date"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=False, index=True)
    source_record_key: Mapped[str | None] = mapped_column(String(200))
    record_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    activity_category: Mapped[str | None] = mapped_column(String(64))
    activity_date: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    unit: Mapped[str | None] = mapped_column(String(32))
    country_code: Mapped[str | None] = mapped_column(String(2))
    scope: Mapped[int | None] = mapped_column(Integer)
    validation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column()


class EmissionFactor(UUIDTimestampMixin, Base):
    __tablename__ = "emission_factors"
    __table_args__ = (
        CheckConstraint("scope IN (1, 2, 3)", name="ck_factor_scope"),
        CheckConstraint("factor_value >= 0", name="ck_factor_value_nonnegative"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_factor_validity"),
        Index("ix_factors_lookup", "activity_category", "scope", "method", "valid_from", "valid_to"),
    )

    organization_id: Mapped[UUID | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    factor_key: Mapped[str] = mapped_column(String(120), nullable=False)
    activity_category: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[int] = mapped_column(Integer, nullable=False)
    geography_type: Mapped[str] = mapped_column(String(24), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    region_code: Mapped[str | None] = mapped_column(String(80))
    activity_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    factor_value: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    factor_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    publication_version: Mapped[str] = mapped_column(String(120), nullable=False)
    methodology: Mapped[str] = mapped_column(Text, nullable=False)
    quality_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_illustrative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PipelineRun(UUIDTimestampMixin, Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_pipeline_idempotency"),
        CheckConstraint("reporting_period_end >= reporting_period_start", name="ck_pipeline_period"),
        Index("ix_pipeline_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reporting_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    reporting_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    current_stage: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    state_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()


class CalculationResult(Base):
    __tablename__ = "calculation_results"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "activity_record_id", "emission_factor_id", "calculation_version", name="uq_calculation_idempotency"),
        CheckConstraint("co2e_kg >= 0", name="ck_calculation_nonnegative"),
        CheckConstraint("scope IN (1, 2, 3)", name="ck_calculation_scope"),
        Index("ix_calculations_org_created", "organization_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    activity_record_id: Mapped[UUID] = mapped_column(ForeignKey("activity_records.id"), nullable=False)
    emission_factor_id: Mapped[UUID] = mapped_column(ForeignKey("emission_factors.id"), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    activity_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    activity_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    factor_value: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    factor_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    conversion: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    co2e_kg: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    scope: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    calculation_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("attempt > 0", name="ck_agent_attempt_positive"),
        Index("ix_agent_pipeline_created", "pipeline_run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    is_retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class AnomalyRecord(Base):
    __tablename__ = "anomaly_records"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    activity_category: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    deviation: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class SustainabilityInsight(Base):
    __tablename__ = "sustainability_insights"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    insight_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    generation_method: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class KnowledgeDocument(UUIDTimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "content_sha256", name="uq_knowledge_document_org_sha256"),
        Index("ix_knowledge_documents_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class AIInteraction(UUIDTimestampMixin, Base):
    __tablename__ = "ai_interactions"
    __table_args__ = (
        Index("ix_ai_interactions_org_created", "organization_id", "created_at"),
        Index("ix_ai_interactions_org_type", "organization_id", "interaction_type"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    interaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column()
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)


class Report(UUIDTimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (CheckConstraint("period_end >= period_start", name="ck_report_period"),)

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    storage_key: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    calculation_version: Mapped[str | None] = mapped_column(String(32))
    methodology_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_org_created", "organization_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column()
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class BenchmarkDataset(UUIDTimestampMixin, Base):
    __tablename__ = "benchmark_datasets"

    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(nullable=False)
