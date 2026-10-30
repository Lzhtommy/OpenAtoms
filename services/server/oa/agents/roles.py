"""Declarative role definitions (ARCHITECTURE §6.2, CONTRACT §4)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"

MEDIA_TOOLS = ["imagecreator_generate_image", "imageanalyzer_analyze_images", "pdfanalyzer_analyze_pdfs",
               "documentextractor_extract_documents", "markdownpdfcreator_convert_file"]
BACKEND_TOOLS = ["backendmanager_create_table", "backendmanager_get_schemas", "backendmanager_insert_table_data"]


@dataclass(frozen=True)
class RoleDef:
    key: str  # history key, = name
    name: str
    role: str
    prompt_file: str
    tools: tuple[str, ...]
    max_steps: int = 30

    @property
    def system_prompt(self) -> str:
        return _prompt(self.prompt_file)


@cache
def _prompt(file: str) -> str:
    common = (PROMPTS / "_common.md").read_text()
    return (PROMPTS / file).read_text() + "\n\n" + common


MIKE = RoleDef(
    "Mike", "Mike", "Team Leader", "team_leader.md",
    ("plan_append_task", "plan_reset_task", "mention", "ask_human", "ask_questions", "reply_to_human",
     "plan_finish_current_task"),
    max_steps=12,
)
EMMA = RoleDef(
    "Emma", "Emma", "Product Manager", "product_manager.md",
    ("editor_write", "editor_read", "searchenhancedqa_run", "plan_finish_current_task"),
    max_steps=15,
)
BOB = RoleDef(
    "Bob", "Bob", "Architect", "architect.md",
    ("editor_write", "editor_read", "terminal_run", "plan_finish_current_task"),
    max_steps=15,
)
ALEX = RoleDef(
    "Alex", "Alex", "Engineer", "engineer.md",
    ("editor_read", "editor_write", "editor_edit_file_by_replace", "editor_append_file", "terminal_run",
     "checkui_run", *BACKEND_TOOLS, *MEDIA_TOOLS, "deployer_deploy_to_public", "ask_human",
     "plan_finish_current_task", "reply_to_human"),
    max_steps=40,
)
DAVID = RoleDef(
    "David", "David", "Data Analyst", "data_analyst.md",
    ("dataanalyst_write_and_exec_code", "browser_goto", "browser_click", "browser_scroll", "browser_type",
     "searchenhancedqa_run", "editor_write", "editor_read", *MEDIA_TOOLS, "plan_finish_current_task"),
    max_steps=20,
)

IRIS = RoleDef(
    "Iris", "Iris", "Deep Researcher", "deep_researcher.md",
    ("searchenhancedqa_run", "searchnavigator_next_query", "browser_goto", "browser_scroll", "browser_click",
     "editor_write", "editor_read", "researchexecutor_finalize_article", "plan_finish_current_task", "reply_to_human"),
    max_steps=25,
)
SARAH = RoleDef(
    "Sarah", "Sarah", "SEO Specialist", "seo_specialist.md",
    ("seo_audit", "editor_read", "editor_write", "editor_edit_file_by_replace", "plan_finish_current_task",
     "reply_to_human"),
    max_steps=15,
)
ADRIAN = RoleDef(
    "Adrian", "Adrian", "Ads Specialist", "ads_specialist.md",
    ("ads_generate_campaign", "editor_read", "editor_write", "searchenhancedqa_run", "plan_finish_current_task",
     "reply_to_human"),
    max_steps=12,
)

ROLES: dict[str, RoleDef] = {r.role: r for r in (MIKE, EMMA, BOB, ALEX, DAVID, IRIS, SARAH, ADRIAN)}
BY_NAME: dict[str, RoleDef] = {r.name.lower(): r for r in ROLES.values()}


def find_role(who: str | None) -> RoleDef | None:
    """Accept a name (David), a role (Data Analyst) or loose variants (@david, engineer)."""
    if not who:
        return None
    w = who.strip().lstrip("@").strip().lower()
    if w in BY_NAME:
        return BY_NAME[w]
    for r in ROLES.values():
        if r.role.lower() == w or w in r.role.lower():
            return r
    aliases = {"pm": EMMA, "product": EMMA, "architect": BOB, "dev": ALEX, "developer": ALEX, "analyst": DAVID,
               "leader": MIKE, "tl": MIKE, "researcher": IRIS, "deep researcher": IRIS, "seo": SARAH,
               "ads": ADRIAN, "marketing": ADRIAN, "research": DAVID}
    return aliases.get(w)
