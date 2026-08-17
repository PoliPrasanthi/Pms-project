import sys
import os
from logging.config import fileConfig

sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.core.config import settings
from app.core.database import Base
from app.models.masters import UserStatus, Skill, Status, Priority
from app.models.roles import Role
from app.models.user import User, user_team_link
from app.models.team import Team
from app.models.template import ProjectTemplate, TemplateTask
from app.models.project import Project, ProjectMember
from app.models.task import Task
from app.models.issue import Issue
from app.models.timelog import TimeLog
from app.models.milestone import Milestone
from app.models.task_list import TaskList
from app.models.document import Document
from app.models.project_group import ProjectGroup
from app.models.audit import AuditFieldsMapping, AuditLogs, AuditLogDetails, AuditMetaDataInfo
from app.models.master import MasterLookup
from app.models.timesheet import Timesheet

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:

    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.DATABASE_URL
    
    from app.core.database import sync_engine
    connectable = sync_engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
