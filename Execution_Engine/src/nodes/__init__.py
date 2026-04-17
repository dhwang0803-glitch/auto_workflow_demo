"""Node plugin package.

Importing this package triggers self-registration of every built-in node
type onto the global `registry`. Celery Worker / Agent processes only
need `import src.nodes` (directly or transitively via `src.container`)
for the full catalog to be available.

Registration order is irrelevant — each node file calls `registry.register()`
at module scope after its class definition.
"""
# Flow / Logic
from src.nodes import condition  # noqa: F401
from src.nodes import code  # noqa: F401
from src.nodes import delay  # noqa: F401
from src.nodes import loop_items  # noqa: F401
from src.nodes import merge  # noqa: F401

# Data Transform
from src.nodes import filter as _filter  # noqa: F401
from src.nodes import transform  # noqa: F401

# HTTP / Database
from src.nodes import db_query  # noqa: F401
from src.nodes import http_request  # noqa: F401

# Messaging
from src.nodes import discord_notify  # noqa: F401
from src.nodes import email_send  # noqa: F401
from src.nodes import slack  # noqa: F401

# LLM
from src.nodes import anthropic_chat  # noqa: F401
from src.nodes import openai_chat  # noqa: F401

# CRM / PM + Dev Tools
from src.nodes import airtable_create_record  # noqa: F401
from src.nodes import airtable_list_records  # noqa: F401
from src.nodes import github_create_issue  # noqa: F401
from src.nodes import hubspot_create_contact  # noqa: F401
from src.nodes import linear_create_issue  # noqa: F401
from src.nodes import notion_create_page  # noqa: F401
from src.nodes import notion_query_database  # noqa: F401
