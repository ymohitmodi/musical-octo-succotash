---
name: fund-playbook
description: Read, explain, or carefully evolve the fund's investment doctrine (hedgefund/doctrine/*.md) and pre-buy checklist — the encoded teaching every AI agent reads before analyzing. Use when the user wants to teach the fund something, change its investment philosophy, review what the agents are taught, or add a lesson permanently.
---

# Fund Playbook — the encoded teaching

The fund's edge lives in `hedgefund/doctrine/*.md`: written distillations of
value-investing craft (valuation, forensic accounting, moats, cycles,
portfolio construction, decision process) that agents LITERALLY read in
their system prompts, plus `config/checklist.yaml` — the machine-enforced
pre-buy checklist. `hedgefund/doctrine/__init__.py:AGENT_DOCTRINE` maps
disciplines to agents.

## Reading / explaining
1. To show what an agent is taught: read the files listed for it in
   `AGENT_DOCTRINE`, plus recent rows from the `lessons` table (institutional
   memory — post-mortems injected automatically).
2. Explain doctrine in the author's spirit: these are decision rules, not
   trivia. Cite the file and section.

## Editing doctrine (do this with the care of editing a constitution)
1. Doctrine is HAND-EDITED ONLY: evolution never touches it and no LLM
   output gets pasted in verbatim without human-quality review. Keep each
   file dense, imperative, and under ~6000 chars (the loader truncates).
2. Additions must be decision-relevant ("demand X, reject Y when Z"), not
   commentary. Test the tone against existing files.
3. Never weaken: the margin-of-safety floor, the forensic veto, the
   fail-closed defaults, or the "macro never picks stocks" rule. Those
   mirror the constitution; changing them requires changing BOTH and
   explicit user confirmation.
4. Checklist edits: adding items is cheap; REMOVING a critical item or
   flipping critical->false requires the user to say so explicitly after
   you state what disaster class that item exists to prevent.
5. After any edit run: `python -m unittest discover tests` and
   `python -c "from hedgefund import doctrine; print(len(doctrine.for_agent('pm')))"`
   (must be > 0 and < ~20000 chars).

## Teaching the fund a lesson permanently
- One-off lesson → insert via post-mortem flow is automatic; for a manual
  lesson, add a row to `lessons` (ticker '_MANUAL_', category 'process').
- A PATTERN of lessons (3+ post-mortems rhyming) → promote it into the
  relevant doctrine file; that is how tuition becomes curriculum.
