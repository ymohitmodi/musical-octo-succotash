"""The doctrine library: the fund's written investment teaching.

World-class small funds run on written, teachable doctrine — memos,
checklists, principles — not on individual brilliance. This package encodes
that doctrine as markdown the agents LITERALLY READ: each agent's system
prompt embeds the disciplines mapped to its role, so every analysis is made
by a model that has just re-read the playbook.

Doctrine is versioned in git and hand-edited by humans only. Evolution never
touches it (it tunes numeric genomes only); LLMs never rewrite it.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent

# which disciplines each agent studies before every analysis
AGENT_DOCTRINE: dict[str, list[str]] = {
    "fundamental": ["valuation", "process"],
    "forensic": ["forensic", "process"],
    "moat": ["quality_moats", "process"],
    "bear": ["quality_moats", "cycles_macro", "process"],
    "macro": ["cycles_macro", "edge"],
    "pm": ["portfolio", "edge", "process"],
    "critic": ["process", "portfolio"],
}

_MAX_CHARS_PER_FILE = 6000  # keep prompts bounded


def load(discipline: str) -> str:
    path = _DIR / f"{discipline}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:_MAX_CHARS_PER_FILE]


def for_agent(agent_name: str) -> str:
    parts = [load(d) for d in AGENT_DOCTRINE.get(agent_name, [])]
    text = "\n\n".join(p for p in parts if p)
    if not text:
        return ""
    return ("\n\n===== FUND DOCTRINE (your training — apply it, cite it) =====\n"
            + text)
