---
name: sdd-validate-tasks
description: >-
  Interactive task quality review and validation.
  Ensures consistency across documentation and readiness for implementation.
argument-hint: "<feature-name> [--batch]"
---

# Task Validation

<background_information>

- **Mission**: Conduct a review of the implementation plan to ensure consistency across documentation and readiness for implementation.
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
- **Skip** the interactive task review dialogue (Step 4)
- Perform a bulk review of tasks against requirements, design, and steering context
- Output the complete review result with GO/NO-GO decision directly
- Do not engage in back-and-forth dialogue with the user

When `--batch` is NOT provided, maintain the default interactive behavior (engage in dialogue throughout the review process).

## Core Task

Interactive implementation task review for the specified feature based on approved requirements and design document.

## Execution Steps

1. **Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

2. **Load Context**:
   - Read `{spec_path}/spec.json` for language and metadata
   - Read `{spec_path}/requirements.md` for requirements
   - Read `{spec_path}/design.md` for design document
   - **Load ALL steering context**: Read entire `docs/steering/` directory including:
     - Default files: `structure.md`, `tech.md`, `product.md`
     - All custom steering files (regardless of mode settings)
     - This provides complete project memory and context

3. **Read Review Guidelines**:
   - Read `docs/settings/rules/tasks-generation.md` for review criteria and process

4. **Execute Task Review** (skip interactive dialogue in `--batch` mode):
   - Review implementation tasks using tasks-generation.md process
   - Ensure there are no issues with consistency between documents, no overly burdensome tasks, and no ambiguous tasks or designs.
   - In batch mode: Perform bulk review and output complete results without user dialogue
   - In interactive mode (default): Engage interactively with user
   - Use language specified in spec.json for output

5. **Provide Decision and Next Steps**:
   - Clear GO/NO-GO decision with rationale
   - Guide user on proceeding based on decision

## Important Constraints

- **Quality assurance, not perfection seeking**: Accept acceptable risk
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

1. **Review Summary**: Brief overview (2-3 sentences) of task quality and readiness
2. **Critical Issues**: Maximum 3, following tasks-generation.md format
3. **Task Strengths**: 1-2 positive aspects
4. **Final Assessment**: GO/NO-GO decision with rationale and next steps

**Format Requirements**:

- Use Markdown headings for clarity
- Follow design-review.md output format
- Keep summary concise

## Safety & Fallback

### Error Scenarios

- **Missing Tasks**: If tasks.md doesn't exist, stop with message: "Run `/sdd-spec-tasks <feature-name>` first to generate implementation tasks"
- **Missing Design**: If design.md doesn't exist, stop with message: "Run `/sdd-spec-design <feature-name>` first to generate design document"
- **Design Not Generated**: If design phase not marked as generated in spec.json, warn but proceed with review
- **Empty Steering Directory**: Warn user that project context is missing and may affect review quality
- **Language Undefined**: Default to English (`en`) if spec.json doesn't specify language

### Next Phase: Task Generation

**If Task Passes Validation (GO Decision)**:

- Review feedback and apply changes if needed
- Run `/sdd-spec-impl <feature-name>` to execute implementation tasks

**If Task Needs Revision (NO-GO Decision)**:

- Address critical issues identified
- Re-run `/sdd-spec-tasks <feature-name>` with improvements
- Re-validate with `/sdd-validate-tasks <feature-name>`

**Note**: Task validation is recommended but optional. Quality review helps catch issues early.
