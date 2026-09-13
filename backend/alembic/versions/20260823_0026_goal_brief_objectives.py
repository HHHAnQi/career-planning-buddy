"""Generalize Goal Brief from project-only goals to career objectives."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0026"
down_revision: str | None = "20260822_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("goal_briefs", "project_goal", new_column_name="objective")
    op.add_column("goal_briefs", sa.Column("objective_type", sa.String(32)))
    op.create_check_constraint(
        "ck_goal_briefs_objective_type",
        "goal_briefs",
        "objective_type IS NULL OR objective_type IN "
        "('career_plan','project','application','interview','skill_transition')",
    )
    op.execute(
        """
        UPDATE goal_briefs
        SET objective_type = CASE
            WHEN source_message ~* '(面试|interview)' THEN 'interview'
            WHEN source_message ~* '(投递|申请|application|apply)' THEN 'application'
            WHEN source_message ~* '(项目|作品|portfolio|系统|应用)' THEN 'project'
            WHEN source_message ~* '(技能|学习|转型|转行|skill|transition)' THEN 'skill_transition'
            ELSE 'career_plan'
        END
        WHERE objective IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_goal_briefs_objective_type", "goal_briefs", type_="check")
    op.drop_column("goal_briefs", "objective_type")
    op.alter_column("goal_briefs", "objective", new_column_name="project_goal")
