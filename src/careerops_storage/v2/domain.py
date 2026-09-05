"""Identity registry, Spark-owned current objects and separately owned resume policy."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP

from .metadata import current_provenance, metadata, numeric_id, timestamps

sources = Table(
    "sources",
    metadata,
    numeric_id(),
    Column("source_key", Text, nullable=False, unique=True),
    *timestamps(),
    CheckConstraint("length(btrim(source_key)) > 0", name="source_key"),
)

accounts = Table(
    "accounts",
    metadata,
    numeric_id(),
    Column("source_id", BigInteger, ForeignKey(sources.c.id), nullable=False),
    Column("account_key", Text, nullable=False),
    *timestamps(),
    UniqueConstraint("source_id", "account_key"),
    UniqueConstraint("id", "source_id"),
    CheckConstraint("length(btrim(account_key)) > 0", name="account_key"),
)

profiles = Table(
    "profiles",
    metadata,
    numeric_id(),
    Column("source_id", BigInteger, nullable=False),
    Column("account_id", BigInteger, nullable=False),
    Column("profile_key", Text, nullable=False),
    *timestamps(),
    ForeignKeyConstraint(["account_id", "source_id"], [accounts.c.id, accounts.c.source_id]),
    UniqueConstraint("source_id", "profile_key"),
    UniqueConstraint("id", "account_id", "source_id"),
    CheckConstraint("length(btrim(profile_key)) > 0", name="profile_key"),
)
Index("ix_profiles_account", profiles.c.account_id)

employers = Table(
    "employers",
    metadata,
    numeric_id(),
    Column("source_id", BigInteger, ForeignKey(sources.c.id), nullable=False),
    Column("source_employer_id", Text, nullable=False),
    Column("name", Text),
    Column("site_url", Text),
    *current_provenance(),
    *timestamps(),
    UniqueConstraint("source_id", "source_employer_id"),
    UniqueConstraint("id", "source_id"),
    CheckConstraint("length(btrim(source_employer_id)) > 0", name="external_identity"),
)

vacancies = Table(
    "vacancies",
    metadata,
    numeric_id(),
    Column("source_id", BigInteger, ForeignKey(sources.c.id), nullable=False),
    Column("source_vacancy_id", Text, nullable=False),
    Column("employer_id", BigInteger),
    Column("title", Text),
    Column("description", Text),
    Column("location", Text),
    Column("remote", Boolean),
    Column("employment_type", Text),
    Column("experience", Text),
    Column("skill_keys", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("salary_from", Numeric(18, 2)),
    Column("salary_to", Numeric(18, 2)),
    Column("salary_currency", Text),
    Column("archived", Boolean),
    Column("closed_for_applicants", Boolean),
    Column("published_at", TIMESTAMP(timezone=True)),
    *current_provenance(),
    *timestamps(),
    ForeignKeyConstraint(["employer_id", "source_id"], [employers.c.id, employers.c.source_id]),
    UniqueConstraint("source_id", "source_vacancy_id"),
    CheckConstraint("length(btrim(source_vacancy_id)) > 0", name="external_identity"),
    CheckConstraint(
        "(salary_from IS NULL OR salary_from >= 0) AND (salary_to IS NULL OR salary_to >= 0) "
        "AND (salary_from IS NULL OR salary_to IS NULL OR salary_to >= salary_from)",
        name="salary_range",
    ),
)
Index("ix_vacancies_employer", vacancies.c.employer_id)

resumes = Table(
    "resumes",
    metadata,
    numeric_id(),
    Column("source_id", BigInteger, nullable=False),
    Column("account_id", BigInteger, nullable=False),
    Column("profile_id", BigInteger, nullable=False),
    Column("source_resume_id", Text, nullable=False),
    Column("title", Text),
    Column("normalized_text", Text),
    Column("skill_keys", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("upstream_status", Text),
    Column("lifecycle", Text, nullable=False, server_default=text("'unknown'")),
    Column("present_in_upstream", Boolean),
    Column("inactive_at", TIMESTAMP(timezone=True)),
    *current_provenance(),
    *timestamps(),
    ForeignKeyConstraint(
        ["profile_id", "account_id", "source_id"],
        [profiles.c.id, profiles.c.account_id, profiles.c.source_id],
    ),
    UniqueConstraint("account_id", "source_resume_id"),
    UniqueConstraint("id", "account_id"),
    CheckConstraint("length(btrim(source_resume_id)) > 0", name="external_identity"),
    CheckConstraint(
        "(lifecycle = 'unknown' AND present_in_upstream IS NULL AND inactive_at IS NULL) OR "
        "(lifecycle = 'active' AND present_in_upstream IS TRUE AND inactive_at IS NULL) OR "
        "(lifecycle = 'deleted' AND present_in_upstream IS FALSE AND inactive_at IS NOT NULL)",
        name="lifecycle",
    ),
)
Index("ix_resumes_profile", resumes.c.profile_id)

resume_bindings = Table(
    "resume_bindings",
    metadata,
    numeric_id(),
    Column("account_id", BigInteger, nullable=False),
    Column("resume_id", BigInteger, nullable=False, unique=True),
    Column("binding_key", Text, nullable=False),
    Column("binding_version", Integer, nullable=False),
    Column("target_key", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("false")),
    Column("auto_apply", Boolean, nullable=False, server_default=text("false")),
    Column("query_set_keys", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    *timestamps(),
    ForeignKeyConstraint(["resume_id", "account_id"], [resumes.c.id, resumes.c.account_id]),
    UniqueConstraint("account_id", "binding_key"),
    CheckConstraint("binding_version >= 1", name="binding_version"),
    CheckConstraint(
        "length(btrim(binding_key)) > 0 AND length(btrim(target_key)) > 0", name="binding_keys"
    ),
    CheckConstraint("NOT auto_apply OR enabled", name="auto_apply_requires_enabled"),
)
