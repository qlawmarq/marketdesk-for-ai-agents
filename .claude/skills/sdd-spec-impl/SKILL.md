---
name: sdd-spec-impl
description: >-
  Execute SDD spec tasks using TDD methodology.
  Implements approved tasks following Red-Green-Refactor cycle.
argument-hint: "<feature-name> [task-numbers]"
---

# Implementation Task Executor

<background_information>

- **Mission**: Execute implementation tasks based on approved specifications
- **Success Criteria**:
  - Code passes all tests with no regressions
  - Tasks marked as completed in tasks.md
  - Implementation aligns with design and requirements

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (required): The feature directory name in `docs/tasks/`
2. **Task numbers** (optional): Specific task numbers to execute (e.g., "1.1" or "1,2,3")

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the feature name.
If task numbers are not provided, all pending tasks will be executed.

## Core Task

Execute implementation tasks for the specified feature using Test-Driven Development.

## Execution Steps

### Step 0: Resolve Spec Path

**Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

### Step 1: Load Context

**Read all necessary context**:

- `{spec_path}/spec.json`, `requirements.md`, `design.md`, `tasks.md`
- **Entire `docs/steering/` directory** for complete project memory

**Validate approvals**:

- Verify tasks are approved in spec.json (stop if not, see Safety & Fallback)

### Step 2: Select Tasks

**Determine which tasks to execute**:

- If task numbers were provided: Execute specified task numbers (e.g., "1.1" or "1,2,3")
- Otherwise: Execute all pending tasks (unchecked `- [ ]` in tasks.md)

### Step 3: Execute Tasks

For each selected task, first judge whether the task involves **testable logic** (functions, classes, algorithms, data transformations) or **non-testable changes** (config values, text/prompt edits, file moves, simple field changes).

#### When testable logic exists → TDD (Red-Green-Refactor)

1. **RED - Write Failing Test**:
   - Write test for the next small piece of functionality
   - Test should fail (code doesn't exist yet)
   - Use descriptive test names

2. **GREEN - Write Minimal Code**:
   - Implement simplest solution to make test pass
   - Focus only on making THIS test pass
   - Avoid over-engineering

3. **REFACTOR - Clean Up**:
   - Improve code structure and readability
   - Remove duplication
   - Ensure all tests still pass after refactoring

#### When no testable logic exists → Direct Implementation

- Apply the change directly
- Run existing tests to confirm no regressions

#### Always

1. **VERIFY**: All existing tests pass, no regressions
2. **POST-TASK REFACTORING REVIEW** (after all sub-tasks of a major task are complete):
   - **REVIEW**: Evaluate refactoring needs from the following perspectives:
     - Duplication: Are there similar patterns introduced across sub-tasks?
     - Naming: Do variable/function/module names accurately reflect intent?
     - Simplification: Is there unnecessary complexity or indirection?
     - Separation of concerns: Are responsibilities properly separated?
   - **EXECUTE** (if refactoring needed): Perform refactoring, then run all tests to confirm they pass
   - **SKIP** (if no refactoring needed): Mark review as complete and proceed to next major task
   - _Note: This is a bird's-eye review layer distinct from TDD's per-cycle Refactor step, which focuses on local improvements within individual test cycles_
3. **MARK COMPLETE**: Update checkbox from `- [ ]` to `- [x]` in tasks.md

## Critical Constraints

- **TDD when warranted**: Use TDD only when the task introduces testable logic. Do NOT write tests that merely assert config values, string literals, or file contents
- **Task Scope**: Implement only what the specific task requires
- **No Regressions**: Existing tests must continue to pass
- **Design Alignment**: Implementation must follow design.md specifications

</instructions>

## Tool Guidance

- **Read first**: Load all context before implementation
- **Test first**: Write tests before code only when testable logic exists
- Use **WebSearch/WebFetch** for library documentation when needed

## Output Description

Provide brief summary in the language specified in spec.json:

1. **Tasks Executed**: Task numbers and test results
2. **Status**: Completed tasks marked in tasks.md, remaining tasks count

**Format**: Concise (under 150 words)

## Safety & Fallback

### Error Scenarios

**Tasks Not Approved or Missing Spec Files**:

- **Stop Execution**: All spec files must exist and tasks must be approved
- **Suggested Action**: "Complete previous phases: `/sdd-spec-requirements`, `/sdd-spec-design`, `/sdd-spec-tasks`"

**Test Failures**:

- **Stop Implementation**: Fix failing tests before continuing
- **Action**: Debug and fix, then re-run

### Task Execution

**Execute specific task(s)**:

- `/sdd-spec-impl <feature-name> 1.1` - Single task
- `/sdd-spec-impl <feature-name> 1,2,3` - Multiple tasks

**Execute all pending**:

- `/sdd-spec-impl <feature-name>` - All unchecked tasks

### After All Tasks Completed

- Optional validation: `/sdd-validate-impl <feature-name>` for mid-implementation quality check
- **Finalize feature**: `/sdd-spec-done <feature-name>` to verify quality, move spec to done, and commit
