---
name: sdd-spec-init
description: >-
  Initialize a new SDD specification with detailed project description.
  Creates directory structure and metadata for a new feature specification.
argument-hint: "<project-description>"
---

# Spec Initialization

<background_information>

- **Mission**: Initialize the first phase of spec-driven development by creating directory structure and metadata for a new specification
- **Success Criteria**:
  - Generate appropriate feature name from project description
  - Create unique spec structure without conflicts
  - Provide clear path to next phase (requirements generation)

</background_information>

<instructions>

## Input

This skill expects:
1. **Project description** (required): A description of the feature or project to initialize

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the project description.

## Core Task

Generate a unique feature name from the project description and initialize the specification structure.

## Execution Steps

1. **Generate Date-Prefixed Name**: Create feature name in `YYYY-MM-DD-[feature-name]` format using today's date (e.g., `2026-02-02-add-auth`). If same-day duplicates exist, append sequence number (e.g., `2026-02-02-01-add-auth`).
2. **Check Uniqueness**: Verify `docs/tasks/todo/` and `docs/tasks/done/` for naming conflicts
3. **Create Directory**: `docs/tasks/todo/[date-prefixed-feature-name]/`
4. **Initialize Files Using Templates**:
   - Read `docs/settings/templates/specs/init.json`
   - Read `docs/settings/templates/specs/requirements-init.md`
   - Replace placeholders:
     - `{{FEATURE_NAME}}` → generated feature name
     - `{{TIMESTAMP}}` → current ISO 8601 timestamp
     - `{{PROJECT_DESCRIPTION}}` → the provided project description
   - Write `spec.json` and `requirements.md` to spec directory

## Important Constraints

- DO NOT generate requirements/design/tasks at this stage
- Follow stage-by-stage development principles
- Maintain strict phase separation
- Only initialization is performed in this phase

</instructions>

## Tool Guidance

- Use file search tools to check existing spec directories for name uniqueness
- Read templates: `init.json` and `requirements-init.md`
- Write to create spec.json and requirements.md after placeholder replacement
- Perform validation before any file write operation

## Output Description

Provide output in the language specified in `spec.json` with the following structure:

1. **Generated Feature Name**: `feature-name` format with 1-2 sentence rationale
2. **Project Summary**: Brief summary (1 sentence)
3. **Created Files**: Bullet list with full paths
4. **Next Step**: Command block showing `/sdd-spec-requirements <feature-name>`
5. **Notes**: Explain why only initialization was performed (2-3 sentences on phase separation)

**Format Requirements**:

- Use Markdown headings (##, ###)
- Wrap commands in code blocks
- Keep total output concise (under 250 words)
- Use clear, professional language per `spec.json.language`

## Safety & Fallback

- **Ambiguous Feature Name**: If feature name generation is unclear, propose 2-3 options and ask user to select
- **Template Missing**: If template files don't exist in `docs/settings/templates/specs/`, report error with specific missing file path and suggest checking repository setup
- **Directory Conflict**: If feature name already exists, append numeric suffix (e.g., `feature-name-2`) and notify user of automatic conflict resolution
- **Write Failure**: Report error with specific path and suggest checking permissions or disk space
