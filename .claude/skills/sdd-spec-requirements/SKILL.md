---
name: sdd-spec-requirements
description: >-
  Generate comprehensive requirements for an SDD specification.
  Creates testable requirements in EARS format based on the project description.
argument-hint: "<feature-name> [--batch]"
---

# Requirements Generation

<background_information>

- **Mission**: Generate comprehensive, testable requirements in EARS format based on the project description from spec initialization
- **Success Criteria**:
  - Create complete requirements document aligned with steering context
  - Follow the project's EARS patterns and constraints for all acceptance criteria
  - Focus on core functionality without implementation details
  - Update metadata to track generation status

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (required): The feature directory name in `docs/tasks/`
2. **--batch** (optional): Non-interactive batch mode flag

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the feature name.

### Batch Mode (`--batch`)

When `--batch` flag is provided, the skill runs in non-interactive batch mode:
- **Skip** the interactive requirements clarification dialogue (Step 4)
- **Skip** the completeness confirmation dialogue
- Generate requirements directly from available information (project description + codebase + steering context)
- Output the generated requirements without waiting for user feedback

When `--batch` is NOT provided, maintain the default interactive behavior (dialogue with user for clarification and completeness checks).

## Core Task

Generate complete requirements for the specified feature based on the project description in requirements.md.

## Execution Steps

1. **Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

2. **Load Context**:
   - Read `{spec_path}/spec.json` for language and metadata
   - Read `{spec_path}/requirements.md` for project description
   - **Load ALL steering context**: Read entire `docs/steering/` directory including:
     - Default files: `structure.md`, `tech.md`, `product.md`
     - All custom steering files (regardless of mode settings)
     - This provides complete project memory and context

3. **Read Guidelines**:
   - Read `docs/settings/rules/ears-format.md` for EARS syntax rules
   - Read `docs/settings/templates/specs/requirements.md` for document structure

4. **Clarify Requirements** (skip in `--batch` mode):
   - In batch mode: Skip this step entirely. Generate requirements using all available information (project description, codebase analysis, steering context) without user dialogue.
   - In interactive mode (default): Clarify requirements through dialogue with the user (project owner)
   - Focus on WHAT the system must do, not HOW
   - Ensure all acceptance criteria follow EARS format

5. **Generate Requirements**:
   - Create initial requirements based on project description
   - Group related functionality into logical requirement areas
   - Apply EARS format to all acceptance criteria
   - Use language specified in spec.json

6. **Update Metadata**:
   - Set `phase: "requirements-generated"`
   - Set `approvals.requirements.generated: true`
   - Update `updated_at` timestamp

## Important Constraints

- Focus on WHAT, not HOW (no implementation details)
- Requirements must be testable and verifiable
- Choose appropriate subject for EARS statements (system/service name for software)
- Generate initial version first, then iterate with user feedback (no sequential questions upfront)
- Requirement headings in requirements.md MUST include a leading numeric ID only (for example: "Requirement 1", "1.", "2 Feature ..."); do not use alphabetic IDs like "Requirement A".

</instructions>

## Tool Guidance

- **Read first**: Load all context (spec, steering, rules, templates) before generation
- **Write last**: Update requirements.md only after complete generation
- Use **WebSearch/WebFetch** only if external domain knowledge needed

## Output Description

Provide output in the language specified in spec.json with:

1. **Generated Requirements Summary**: Brief overview of major requirement areas (3-5 bullets)
2. **Document Status**: Confirm requirements.md updated and spec.json metadata updated
3. **Next Steps**: Guide user on how to proceed (approve and continue, or modify)

**Format Requirements**:

- Use Markdown headings for clarity
- Include file paths in code blocks
- Include all URL references if WebSearch/WebFetch used
- Keep summary concise (under 300 words)

## Safety & Fallback

### Error Scenarios

- **Missing Project Description**: If requirements.md lacks project description, ask user for feature details
- **Ambiguous Requirements**: Propose initial version and iterate with user rather than asking many upfront questions
- **Template Missing**: If template files don't exist, use inline fallback structure with warning
- **Language Undefined**: Default to English (`en`) if spec.json doesn't specify language
- **Incomplete Requirements**: After generation, explicitly ask user if requirements cover all expected functionality (skip completeness confirmation in `--batch` mode — output results directly)
- **Steering Directory Empty**: Warn user that project context is missing and may affect requirement quality
- **Non-numeric Requirement Headings**: If existing headings do not include a leading numeric ID (for example, they use "Requirement A"), normalize them to numeric IDs and keep that mapping consistent (never mix numeric and alphabetic labels).

### Next Phase: Research & Design

**If Requirements Approved**:

- Review generated requirements at `docs/tasks/<feature-name>/requirements.md`
- **Optional Gap Analysis** (for existing codebases):
  - Run `/sdd-validate-gap <feature-name>` to analyze implementation gap with current code
  - Identifies existing components, integration points, and implementation strategy
  - Recommended for brownfield projects; skip for greenfield
- Run `/sdd-spec-research <feature-name>` to execute research & discovery (generates research.md)
- Then `/sdd-spec-design <feature-name> -y` to proceed to design phase (uses research.md)

**If Modifications Needed**:

- Provide feedback and re-run `/sdd-spec-requirements <feature-name>`

**Note**: Approval is mandatory before proceeding to design phase.
