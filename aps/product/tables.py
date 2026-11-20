from sqlalchemy import (JSON, Column, Float, ForeignKey, Index, Integer, LargeBinary,
                        MetaData, String, Table, UniqueConstraint)

metadata = MetaData()
plants = Table(
    "plants", metadata, Column("id", String(40), primary_key=True),
    Column("name", String(100), nullable=False), Column("latest_dataset", String(32)),
    Column("active_plan", String(32)),
)
users = Table(
    "accounts", metadata, Column("id", String(32), primary_key=True),
    Column("plant_id", ForeignKey("plants.id"), nullable=False),
    Column("username", String(100), nullable=False), Column("name", String(100), nullable=False),
    Column("role", String(20), nullable=False), Column("password", String(200)),
    UniqueConstraint("username", name="uq_account_username"),
)
sessions = Table(
    "account_sessions", metadata, Column("key", String(64), primary_key=True),
    Column("user_id", ForeignKey("accounts.id"), nullable=False),
    Column("expires", Float, nullable=False),
)
datasets = Table(
    "data_snapshots", metadata, Column("id", String(32), primary_key=True),
    Column("plant_id", ForeignKey("plants.id"), nullable=False),
    Column("name", String(250), nullable=False), Column("digest", String(64), nullable=False),
    Column("created", Float, nullable=False), Column("info", JSON, nullable=False),
    Column("workbook", LargeBinary, nullable=False),
    UniqueConstraint("plant_id", "digest", name="uq_plant_snapshot_digest"),
)
jobs = Table(
    "planning_jobs", metadata, Column("id", String(32), primary_key=True),
    Column("plant_id", ForeignKey("plants.id"), nullable=False),
    Column("dataset_id", ForeignKey("data_snapshots.id"), nullable=False),
    Column("actor", ForeignKey("accounts.id"), nullable=False),
    Column("request_key", String(80), nullable=False), Column("kind", String(20), nullable=False),
    Column("payload", JSON, nullable=False), Column("status", String(20), nullable=False),
    Column("created", Float, nullable=False), Column("updated", Float, nullable=False),
    Column("attempts", Integer, nullable=False), Column("lease_until", Float, nullable=False),
    Column("output", JSON), Column("error", String(300)),
    UniqueConstraint("plant_id", "request_key", name="uq_plant_job_key"),
    Index("ix_job_claim", "status", "lease_until", "created"),
)
plans = Table(
    "plan_versions", metadata, Column("id", String(32), primary_key=True),
    Column("plant_id", ForeignKey("plants.id"), nullable=False),
    Column("dataset_id", ForeignKey("data_snapshots.id"), nullable=False),
    Column("job_id", ForeignKey("planning_jobs.id"), nullable=False),
    Column("parent_id", String(32)), Column("strategy", String(80), nullable=False),
    Column("created", Float, nullable=False), Column("status", String(20), nullable=False),
    Column("revision", Integer, nullable=False), Column("submitted_by", ForeignKey("accounts.id")),
    Column("state", JSON, nullable=False), Column("result", JSON, nullable=False),
    Index("ix_plan_plant_status", "plant_id", "status", "created"),
)
audit = Table(
    "workflow_audit", metadata, Column("id", String(32), primary_key=True),
    Column("plant_id", ForeignKey("plants.id"), nullable=False),
    Column("user_id", ForeignKey("accounts.id"), nullable=False),
    Column("object_id", String(32), nullable=False), Column("event", String(36), nullable=False),
    Column("note", String(500), nullable=False), Column("created", Float, nullable=False),
    Index("ix_audit_plant_time", "plant_id", "created"),
)
executions = Table(
    "batch_execution", metadata, Column("id", String(32), primary_key=True),
    Column("plant_id", ForeignKey("plants.id"), nullable=False),
    Column("plan_id", ForeignKey("plan_versions.id"), nullable=False),
    Column("batch_key", String(24), nullable=False), Column("line", String(40), nullable=False),
    Column("actual", JSON, nullable=False), Column("revision", Integer, nullable=False),
    Column("user_id", ForeignKey("accounts.id"), nullable=False), Column("created", Float, nullable=False),
    UniqueConstraint("plan_id", "batch_key", name="uq_execution_plan_batch"),
    Index("ix_execution_plan", "plan_id", "plant_id"),
)
