---
name: sdd-validate-impl
description: >-
  Validate implementation against requirements, design, and tasks.
  Checks test coverage, requirements traceability, and design alignment.
argument-hint: "[feature-name] [task-numbers]"
---

# Implementation Validation

<background_information>

- **Mission**: Verify that implementation aligns with approved requirements, design, and tasks
- **Success Criteria**:
  - All specified tasks marked as completed
  - Tests exist and pass for implemented functionality
  - Requirements traceability confirmed (EARS requirements covered)
  - Design structure reflected in implementation
  - No regressions in existing functionality

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (optional): The feature directory name in `docs/tasks/`
2. **Task numbers** (optional): Specific task numbers to validate (e.g., "1.1,1.2")

If inputs were provided with this skill invocation, use them directly.
If no inputs were provided, auto-detect from conversation history or scan for completed tasks.

## Core Task

Validate implementation for feature(s) and task(s) based on approved specifications.

## Execution Steps

### 0. Resolve Spec Path

**Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

### 1. Detect Validation Target

**If no feature name provided**:

- Parse conversation history for `/sdd-spec-impl` invocations
- Extract feature names and task numbers from each execution
- Aggregate all implemented tasks by feature
- Report detected implementations (e.g., "user-auth: 1.1, 1.2, 1.3")
- If no history found, scan `docs/tasks/todo/` and `docs/tasks/done/` for features with completed tasks `[x]`

**If feature provided but no task numbers**:

- Use specified feature
- Detect all completed tasks `[x]` in `{spec_path}/tasks.md`

**If both feature and tasks provided**:

- Validate specified feature and tasks only (e.g., `user-auth 1.1,1.2`)

### 2. Load Context

For each detected feature:

- Read `{spec_path}/spec.json` for metadata
- Read `{spec_path}/requirements.md` for requirements
- Read `{spec_path}/design.md` for design structure
- Read `{spec_path}/tasks.md` for task list
- **Load ALL steering context**: Read entire `docs/steering/` directory including:
  - Default files: `structure.md`, `tech.md`, `product.md`
  - All custom steering files (regardless of mode settings)

### 3. Execute Validation

For each task, verify:

#### Task Completion Check

- Checkbox is `[x]` in tasks.md
- If not completed, flag as "Task not marked complete"

#### Test Coverage Check

- Tests exist for task-related functionality
- Tests pass (no failures or errors)
- Use Bash to run test commands (e.g., `npm test`, `pytest`)
- If tests fail or don't exist, flag as "Test coverage issue"

#### Requirements Traceability

- Identify EARS requirements related to the task
- Search implementation for evidence of requirement coverage
- If requirement not traceable to code, flag as "Requirement not implemented"

#### Design Alignment

- Check if design.md structure is reflected in implementation
- Verify key interfaces, components, and modules exist
- Confirm file structure matches design
- If misalignment found, flag as "Design deviation"

#### Regression Check

- Run full test suite (if available)
- Verify no existing tests are broken
- If regressions detected, flag as "Regression detected"

### 4. Generate Report

Provide summary in the language specified in spec.json:

- Validation summary by feature
- Coverage report (tasks, requirements, design)
- Issues and deviations with severity (Critical/Warning)
- GO/NO-GO decision

## Important Constraints

- **Conversation-aware**: Prioritize conversation history for auto-detection
- **Non-blocking warnings**: Design deviations are warnings unless critical
- **Test-first focus**: Test coverage is mandatory for GO decision
- **Traceability required**: All requirements must be traceable to implementation

</instructions>

## Tool Guidance

- **Conversation parsing**: Extract `/sdd-spec-impl` patterns from history
- **Read context**: Load all specs and steering before validation
- **Bash for tests**: Execute test commands to verify pass status
- **Search for traceability**: Examine codebase for requirement evidence
- **File structure checks**: Verify file structure matches design

## Output Description

Provide output in the language specified in spec.json with:

1. **Detected Target**: Features and tasks being validated (if auto-detected)
2. **Validation Summary**: Brief overview per feature (pass/fail counts)
3. **Issues**: List of validation failures with severity and location
4. **Coverage Report**: Requirements/design/task coverage percentages
5. **Decision**: GO (ready for next phase) / NO-GO (needs fixes)

**Format Requirements**:

- Use Markdown headings and tables for clarity
- Flag critical issues with warning indicators
- Keep summary concise (under 400 words)

## Safety & Fallback

### Error Scenarios

- **No Implementation Found**: If no `/sdd-spec-impl` in history and no `[x]` tasks, report "No implementations detected"
- **Test Command Unknown**: If test framework unclear, warn and skip test validation (manual verification required)
- **Missing Spec Files**: If spec.json/requirements.md/design.md missing, stop with error
- **Language Undefined**: Default to English (`en`) if spec.json doesn't specify language

### Next Steps Guidance

**If GO Decision**:

- Implementation validated and ready for completion
- Run `/sdd-spec-done <feature-name>` to finalize (lint/test/build check, move to done, commit)

**If NO-GO Decision**:

- Address critical issues listed
- Re-run `/sdd-spec-impl <feature-name> <task-numbers>` for fixes
- Re-validate with `/sdd-validate-impl <feature-name> <task-numbers>`

**Note**: Validation is recommended after implementation. Use `/sdd-spec-done` for the final completion workflow.
