---
name: sdd-spec-tasks
description: >-
  Generate implementation tasks for an SDD specification.
  Translates technical design into executable, properly-sized work items.
argument-hint: "<feature-name> [-y] [--sequential]"
---

# Implementation Tasks Generator

<background_information>

- **Mission**: Generate detailed, actionable implementation tasks that translate technical design into executable work items
- **Success Criteria**:
  - All requirements mapped to specific tasks
  - Tasks properly sized (1-3 hours each)
  - Clear task progression with proper hierarchy
  - Natural language descriptions focused on capabilities

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (required): The feature directory name in `docs/tasks/`
2. **Auto-approve flag** (optional): `-y` to auto-approve the previous phases
3. **Sequential flag** (optional): `--sequential` to disable parallel task markers

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the feature name.
If the auto-approve flag is not provided, default to interactive approval mode.
If the sequential flag is not provided, default to parallel-aware mode.

## Core Task

Generate implementation tasks for the specified feature based on approved requirements and design.

## Execution Steps

### Step 0: Resolve Spec Path

**Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

### Step 1: Load Context

**Read all necessary context**:

- `{spec_path}/spec.json`, `requirements.md`, `design.md`
- `{spec_path}/tasks.md` (if exists, for merge mode)
- **Entire `docs/steering/` directory** for complete project memory

**Validate approvals**:

- If auto-approve flag was provided: Auto-approve requirements and design in spec.json
- Otherwise: Verify both approved (stop if not, see Safety & Fallback)
- Determine sequential mode based on presence of `--sequential`

### Step 2: Generate Implementation Tasks

**Load generation rules and template**:

- Read `docs/settings/rules/tasks-generation.md` for principles
- If `sequential` is **false**: Read `docs/settings/rules/tasks-parallel-analysis.md` for parallel judgement criteria
- Read `docs/settings/templates/specs/tasks.md` as a **format reference only**. Do NOT copy any of its content into the output. Specifically, the output must not contain: `{{PLACEHOLDER}}` macros (e.g. `{{NUMBER}}`, `{{TASK_DESCRIPTION}}`), the `## Task Format Template` section heading, or blockquote annotations from the template

**Output structure**: The generated `tasks.md` must start with `# Implementation Plan`, followed by `## Tasks` containing only the generated task list. No template sections, placeholders, or formatting examples.

**Generate task list following all rules**:

- Use language specified in spec.json
- Map all requirements to tasks
- When documenting requirement coverage, list numeric requirement IDs only (comma-separated) without descriptive suffixes, parentheses, translations, or free-form labels
- Ensure all design components included
- Verify task progression is logical and incremental
- Collapse single-subtask structures by promoting them to major tasks and avoid duplicating details on container-only major tasks (use template patterns accordingly)
- Apply `(P)` markers to tasks that satisfy parallel criteria (omit markers in sequential mode)
- Mark optional test coverage subtasks with `- [ ]*` only when they strictly cover acceptance criteria already satisfied by core implementation and can be deferred post-MVP
- If existing tasks.md found, merge with new content

### Step 3: Finalize

**Write and update**:

- Create/update `docs/tasks/<feature-name>/tasks.md`
- Update spec.json metadata:
  - Set `phase: "tasks-generated"`
  - Set `approvals.tasks.generated: true, approved: false`
  - Set `approvals.requirements.approved: true`
  - Set `approvals.design.approved: true`
  - Update `updated_at` timestamp

## Critical Constraints

- **Follow rules strictly**: All principles in tasks-generation.md are mandatory
- **Natural Language**: Describe what to do, not code structure details
- **Complete Coverage**: ALL requirements must map to tasks
- **Maximum 2 Levels**: Major tasks and sub-tasks only (no deeper nesting)
- **Sequential Numbering**: Major tasks increment (1, 2, 3...), never repeat
- **Task Integration**: Every task must connect to the system (no orphaned work)
- **TDD Test Deduplication**: Do NOT generate unit test implementations as independent sub-tasks when they are covered by TDD cycles. Describe behaviors to test as regular detail items instead. Integration tests and E2E tests should still be generated as independent tasks

</instructions>

## Tool Guidance

- **Read first**: Load all context, rules, and templates before generation
- **Write last**: Generate tasks.md only after complete analysis and verification

## Output Description

Provide brief summary in the language specified in spec.json:

1. **Status**: Confirm tasks generated at `docs/tasks/<feature-name>/tasks.md`
2. **Task Summary**:
   - Total: X major tasks, Y sub-tasks
   - All Z requirements covered
   - Average task size: 1-3 hours per sub-task
3. **Quality Validation**:
   - All requirements mapped to tasks
   - Task dependencies verified
   - Testing tasks included
4. **Next Action**: Review tasks and proceed when ready

**Format**: Concise (under 200 words)

## Safety & Fallback

### Error Scenarios

**Requirements or Design Not Approved**:

- **Stop Execution**: Cannot proceed without approved requirements and design
- **User Message**: "Requirements and design must be approved before task generation"
- **Suggested Action**: "Run `/sdd-spec-tasks <feature-name> -y` to auto-approve both and proceed"

**Missing Requirements or Design**:

- **Stop Execution**: Both documents must exist
- **User Message**: "Missing requirements.md or design.md at `docs/tasks/<feature-name>/`"
- **Suggested Action**: "Complete requirements and design phases first"

**Incomplete Requirements Coverage**:

- **Warning**: "Not all requirements mapped to tasks. Review coverage."
- **User Action Required**: Confirm intentional gaps or regenerate tasks

**Template/Rules Missing**:

- **User Message**: "Template or rules files missing in `docs/settings/`"
- **Fallback**: Use inline basic structure with warning
- **Suggested Action**: "Check repository setup or restore template files"
- **Missing Numeric Requirement IDs**:
  - **Stop Execution**: All requirements in requirements.md MUST have numeric IDs. If any requirement lacks a numeric ID, stop and request that requirements.md be fixed before generating tasks.

### Next Phase: Implementation

**Before Starting Implementation**:

- **IMPORTANT**: Clear conversation history and free up context before running `/sdd-spec-impl`
- This applies when starting first task OR switching between tasks
- Fresh context ensures clean state and proper task focus

**If Tasks Approved**:

- Execute specific task: `/sdd-spec-impl <feature-name> 1.1` (recommended: clear context between each task)
- Execute multiple tasks: `/sdd-spec-impl <feature-name> 1.1,1.2` (use cautiously, clear context between tasks)
- Without arguments: `/sdd-spec-impl <feature-name>` (executes all pending tasks - NOT recommended due to context bloat)

**If Modifications Needed**:

- Provide feedback and re-run `/sdd-spec-tasks <feature-name>`
- Existing tasks used as reference (merge mode)

**Note**: The implementation phase will guide you through executing tasks with appropriate context and validation.
