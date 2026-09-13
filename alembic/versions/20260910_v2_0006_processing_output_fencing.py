"""Fence current Processing outputs when desired work is superseded.

Revision ID: 20260910_v2_0006
Revises: 20260908_v2_0005
"""

from alembic import op

revision = "20260910_v2_0006"
down_revision = "20260908_v2_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION careerops_v2.invalidate_stale_processing_outputs()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                DELETE FROM careerops_v2.match_results
                WHERE vacancy_id = NEW.vacancy_id
                  AND binding_id = NEW.binding_id
                  AND processing_job_id <> NEW.id;

                UPDATE careerops_v2.application_candidates
                SET status = 'withdrawn', updated_at = now()
                WHERE vacancy_id = NEW.vacancy_id
                  AND binding_id = NEW.binding_id
                  AND processing_job_id <> NEW.id
                  AND status <> 'withdrawn';
                RETURN NEW;
            END IF;

            IF NEW.status = 'pending'
               AND OLD.status = 'cancelled'
               AND (
                    OLD.error_category = 'superseded'
                    OR OLD.error_category LIKE 'reconciliation.%'
               ) THEN
                DELETE FROM careerops_v2.match_results
                WHERE vacancy_id = NEW.vacancy_id
                  AND binding_id = NEW.binding_id
                  AND processing_job_id <> NEW.id;

                UPDATE careerops_v2.application_candidates
                SET status = 'withdrawn', updated_at = now()
                WHERE vacancy_id = NEW.vacancy_id
                  AND binding_id = NEW.binding_id
                  AND processing_job_id <> NEW.id
                  AND status <> 'withdrawn';
            END IF;

            IF NEW.status = 'cancelled'
               AND OLD.status <> 'cancelled'
               AND (
                    NEW.error_category = 'superseded'
                    OR NEW.error_category LIKE 'reconciliation.%'
               ) THEN
                DELETE FROM careerops_v2.match_results
                WHERE vacancy_id = NEW.vacancy_id
                  AND binding_id = NEW.binding_id
                  AND processing_job_id = NEW.id;

                UPDATE careerops_v2.application_candidates
                SET status = 'withdrawn', updated_at = now()
                WHERE vacancy_id = NEW.vacancy_id
                  AND binding_id = NEW.binding_id
                  AND processing_job_id = NEW.id
                  AND status <> 'withdrawn';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_processing_jobs_invalidate_stale_outputs
        AFTER INSERT OR UPDATE OF status, error_category
        ON careerops_v2.processing_jobs
        FOR EACH ROW
        EXECUTE FUNCTION careerops_v2.invalidate_stale_processing_outputs();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_processing_jobs_invalidate_stale_outputs "
        "ON careerops_v2.processing_jobs"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS careerops_v2.invalidate_stale_processing_outputs()"
    )
