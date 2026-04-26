---
name: sdd-validate-design
description: >-
  Interactive technical design quality review and validation.
  Conducts GO/NO-GO assessment with balanced feedback.
argument-hint: "<feature-name> [--batch]"
---

# Technical Design Validation

<background_information>

- **Mission**: Conduct interactive quality review of technical design to ensure readiness for implementation
- **Success Criteria**:
  - Balanced assessment with strengths recognized
  - Clear GO/NO-GO decision with rationale
  - Actionable feedback for improvements if needed

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
- **Skip** the interactive design review dialogue (Step 4)
- Perform a bulk review of the design against requirements and steering context
- Output the complete review result with GO/NO-GO decision directly
- Do not engage in back-and-forth dialogue with the user

When `--batch` is NOT provided, maintain the default interactive behavior (engage in dialogue throughout the review process).

## Core Task

Interactive design quality review for the specified feature based on approved requirements and design document.

## Execution Steps

1. **Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

2. **Load Context**:
   - Read `{spec_path}/spec.json` for language and metadata
   - Read `{spec_path}/requirements.md` for requirements
   - Read `{spec_path}/research.md` for research findings (if exists)
   - Read `{spec_path}/design.md` for design document
   - **Load ALL steering context**: Read entire `docs/steering/` directory including:
     - Default files: `structure.md`, `tech.md`, `product.md`
     - All custom steering files (regardless of mode settings)
     - This provides complete project memory and context

3. **Read Review Guidelines**:
   - Read `docs/settings/rules/design-review.md` for review criteria and process

4. **Execute Design Review** (skip interactive dialogue in `--batch` mode):
   - Follow design-review.md process: Analysis → Critical Issues → Strengths → GO/NO-GO
   - If `research.md` exists, verify that its key findings (architecture patterns, technology decisions, risks) are reflected in the design document
   - In batch mode: Perform bulk review and output complete results without user dialogue
   - In interactive mode (default): Engage interactively with user
   - Use language specified in spec.json for output

5. **Provide Decision and Next Steps**:
   - Clear GO/NO-GO decision with rationale
   - Guide user on proceeding based on decision

## Important Constraints

- **Quality assurance, not perfection seeking**: Accept acceptable risk
- **Critical focus only**: Significantly impacting success
- **Interactive approach**: Engage in dialogue, not one-way evaluation
- **Balanced assessment**: Recognize both strengths and weaknesses
- **Actionable feedback**: All suggestions must be implementable

</instructions>

## Tool Guidance

- **Read first**: Load all context (spec, steering, rules) before review
- **Grep if needed**: Search codebase for pattern validation or integration checks
- **Interactive**: Engage with user throughout the review process

## Output Description

Provide output in the language specified in spec.json with:

1. **Review Summary**: Brief overview (2-3 sentences) of design quality and readiness
2. **Critical Issues**: Maximum 3, following design-review.md format
3. **Design Strengths**: 1-2 positive aspects
4. **Final Assessment**: GO/NO-GO decision with rationale and next steps

**Format Requirements**:

- Use Markdown headings for clarity
- Follow design-review.md output format
- Keep summary concise

## Safety & Fallback

### Error Scenarios

- **Missing Design**: If design.md doesn't exist, stop with message: "Run `/sdd-spec-design <feature-name>` first to generate design document"
- **Design Not Generated**: If design phase not marked as generated in spec.json, warn but proceed with review
- **Empty Steering Directory**: Warn user that project context is missing and may affect review quality
- **Language Undefined**: Default to English (`en`) if spec.json doesn't specify language

### Next Phase: Task Generation

**If Design Passes Validation (GO Decision)**:

- Review feedback and apply changes if needed
- Run `/sdd-spec-tasks <feature-name>` to generate implementation tasks
- Or `/sdd-spec-tasks <feature-name> -y` to auto-approve and proceed directly

**If Design Needs Revision (NO-GO Decision)**:

- Address critical issues identified
- Re-run `/sdd-spec-design <feature-name>` with improvements
- Re-validate with `/sdd-validate-design <feature-name>`

**Note**: Design validation is recommended but optional. Quality review helps catch issues early.
