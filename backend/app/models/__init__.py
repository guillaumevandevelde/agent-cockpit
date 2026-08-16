"""Models package.

Importing each module here ensures SQLAlchemy's ``Base.metadata`` knows
about every table before ``init_db`` runs ``Base.metadata.create_all``.
Without these imports, a model file would still exist on disk but its
table wouldn't materialise in the DB on first run.
"""
from app.models.agent_mail import *  # noqa: F401,F403
from app.models.auto_resume import *  # noqa: F401,F403
from app.models.host import *  # noqa: F401,F403
from app.models.mcp_token import *  # noqa: F401,F403
from app.models.recurring_trigger import *  # noqa: F401,F403
from app.models.run_instance import *  # noqa: F401,F403
from app.models.sandcastle import *  # noqa: F401,F403
from app.models.security_audit import *  # noqa: F401,F403
from app.models.security_profile import *  # noqa: F401,F403
