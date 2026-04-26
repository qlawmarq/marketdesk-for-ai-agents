---
name: sdd-spec-done
description: >-
  Finalize an SDD feature: verify implementation quality, move spec to done, and commit.
  Runs lint/test/build checks, validates requirements and design alignment, then completes the feature.
argument-hint: "<feature-name>"
---

# Feature Completion

<background_information>

- **Mission**: Verify implementation quality comprehensively, then finalize the feature by moving the spec to done and creating a git commit
- **Success Criteria**:
  - All tasks marked as completed in tasks.md
  - Requirements traceability confirmed
  - Design alignment verified
  - Lint, tests, and build pass without issues
  - Code is clean and does not require refactoring
  - Spec moved from `docs/tasks/todo/` to `docs/tasks/done/`
  - Changes committed with project-consistent commit message

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (required): The feature directory name in `docs/tasks/`

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the feature name.

## Core Task

Run comprehensive quality verification on the completed feature. If all checks pass, finalize by moving the spec to done and committing. If any check fails, report issues and suggest corrective actions.

## Execution Steps

### Step 0: Resolve Spec Path

Look for the feature directory in `docs/tasks/todo/<feature-name>/` **only**. Features already in `docs/tasks/done/` are already completed and cannot be finalized again. If not found in `todo/`, report an error.

### Step 1: Load Context

**Read all necessary context**:

- `{spec_path}/spec.json` for metadata and language
- `{spec_path}/requirements.md` for requirements
- `{spec_path}/design.md` for design structure
- `{spec_path}/tasks.md` for task list
- **Entire `docs/steering/` directory** for complete project memory

### Step 2: Verification

Execute all verification checks sequentially. Collect all issues before making a decision.

#### 2a. Task Completion Check

- Parse tasks.md for all checkboxes
- ALL tasks must be `[x]` (completed)
- If any `[ ]` remain, flag as **Critical**: "Incomplete tasks found"

#### 2b. Requirements Traceability

- Identify all EARS requirements from requirements.md
- Search implementation for evidence of each requirement
- If a requirement is not traceable to code, flag as **Critical**: "Requirement not implemented"

#### 2c. Design Alignment

- Check if design.md structure is reflected in implementation
- Verify key interfaces, components, and modules exist
- Confirm file structure matches design
- If misalignment found, flag as **Warning**: "Design deviation"

#### 2d. Lint Check

- Detect lint command from project configuration:
  1. `package.json` scripts (`lint`, `lint:check`)
  2. `Makefile` targets (`lint`)
  3. `pyproject.toml` / `Cargo.toml` equivalents
  4. Steering context (`docs/steering/tech.md`)
- Run detected lint command
- If lint fails, flag as **Critical**: "Lint errors detected"
- If no lint command detected, flag as **Info**: "No lint configuration found — skipping"

#### 2e. Test Check

- Detect test command from project configuration:
  1. `package.json` scripts (`test`, `test:unit`)
  2. `Makefile` targets (`test`)
  3. `pyproject.toml` / `Cargo.toml` equivalents
  4. Steering context (`docs/steering/tech.md`)
- Run detected test command
- If tests fail, flag as **Critical**: "Test failures detected"
- If no test command detected, flag as **Warning**: "No test configuration found — manual verification required"

#### 2f. Build Check

- Detect build command from project configuration:
  1. `package.json` scripts (`build`, `compile`)
  2. `Makefile` targets (`build`)
  3. `pyproject.toml` / `Cargo.toml` equivalents
  4. Steering context (`docs/steering/tech.md`)
- Run detected build command
- If build fails, flag as **Critical**: "Build errors detected"
- If no build command detected, flag as **Info**: "No build configuration found — skipping"

#### 2g. Code Quality Review

- Review implemented code for the feature's tasks
- Check for:
  - Code duplication that should be extracted
  - Overly complex methods that need simplification
  - Missing error handling at system boundaries
  - Inconsistent patterns compared to existing codebase
- If refactoring is needed, flag as **Warning**: "Refactoring recommended" with specific suggestions

### Step 3: GO/NO-GO Decision

**GO Criteria**: Zero Critical issues.

**If NO-GO**:

- Present all issues categorized by severity (Critical / Warning / Info)
- For each issue, provide:
  - Description of the problem
  - Specific file(s) or location(s) affected
  - Suggested corrective action
- Suggest next steps: fix issues, then re-run `/sdd-spec-done <feature-name>`
- **Stop execution here** — do not proceed to Step 4

**If GO**:

- Present verification summary showing all checks passed
- Proceed to Step 4

### Step 4: Completion

#### 4a. Update Metadata

- Update `spec.json`:
  - Set `phase: "done"`
  - Update `updated_at` timestamp

#### 4b. Move Spec to Done

- Move `docs/tasks/todo/<feature-name>/` to `docs/tasks/done/<feature-name>/`

#### 4c. Detect Commit Message Style

- Analyze recent git history:
  ```bash
  git log --oneline -20
  ```
- Detect dominant pattern:
  - Conventional Commits (`feat:`, `fix:`, `chore:`, etc.) → use matching format
  - Scope usage (`feat(scope):`) → include feature name as scope
  - No clear pattern → default to Conventional Commits
- Determine commit type from feature context:
  - New feature → `feat`
  - Bug fix → `fix`
  - Refactoring → `refactor`
  - Default → `feat`

#### 4d. Stage and Commit

- Check `git status` for current working tree state
- Stage changes relevant to this feature:
  - The moved spec directory (`docs/tasks/done/<feature-name>/`)
  - Implementation code changes related to the feature's tasks
- If unrelated unstaged changes exist, warn the user and exclude them
- Commit with detected style, e.g.:
  ```
  feat(<short-name>): <description from spec.json>
  ```
- **Do NOT push** — leave that to the user

## Critical Constraints

- **todo/ only**: Only finalize features in `docs/tasks/todo/` — never re-process `done/`
- **All checks must pass**: Zero Critical issues for GO decision
- **No auto-push**: Commit locally only; pushing is the user's responsibility
- **Scoped commits**: Only stage changes related to this feature
- **Non-destructive**: If anything fails, the spec stays in `todo/` untouched

</instructions>

## Tool Guidance

- **Read first**: Load all context (spec, steering, implementation) before verification
- **Bash for checks**: Execute lint, test, and build commands via Bash
- **Grep/Read for traceability**: Search codebase for requirement and design evidence
- **Bash for git**: Use git commands for commit style detection, staging, and committing

## Output Description

Provide output in the language specified in spec.json:

### If NO-GO

1. **Verification Summary**: Table of all checks with pass/fail status
2. **Issues**: List of issues by severity with descriptions and suggestions
3. **Next Steps**: Specific commands to fix issues and re-run

**Format**: Markdown with severity indicators, under 500 words

### If GO

1. **Verification Summary**: Table of all checks — all passed
2. **Completion Actions**: Confirm spec moved and commit created
3. **Commit Details**: Show commit hash and message

**Format**: Concise Markdown, under 300 words

## Safety & Fallback

### Error Scenarios

**Feature Not Found in todo/**:

- **Stop Execution**: Cannot finalize a feature that doesn't exist in todo/
- **Check done/**: If found in `docs/tasks/done/`, report "Feature already completed"
- **Neither**: Report "Feature not found. Check available specs with `/sdd-spec-status`"

**Incomplete Tasks**:

- **NO-GO**: List remaining tasks with their descriptions
- **Suggested Action**: "Complete remaining tasks with `/sdd-spec-impl <feature-name> <task-numbers>`, then re-run `/sdd-spec-done <feature-name>`"

**Lint/Test/Build Failures**:

- **NO-GO**: Show command output with error details
- **Suggested Action**: "Fix the reported errors, then re-run `/sdd-spec-done <feature-name>`"

**Unrelated Changes in Working Tree**:

- **Warning**: "Unrelated changes detected in working tree. These will not be included in the commit."
- **Proceed**: Continue with only feature-related changes

**No Unstaged Implementation Changes**:

- **Info**: If all implementation code is already committed, only the spec move will be committed
- **Proceed**: This is a valid scenario (user committed implementation incrementally)

**Git Not Clean for Spec Move**:

- **Warning**: If `docs/tasks/todo/<feature-name>/` has uncommitted modifications, include them in the commit

### Workflow Integration

**Before Running spec-done**:

- Complete all implementation tasks: `/sdd-spec-impl <feature-name>`
- Optional mid-implementation validation: `/sdd-validate-impl <feature-name>`

**After Successful Completion**:

- Feature is finalized and committed
- Spec is archived in `docs/tasks/done/<feature-name>/`
- Ready to start next feature with `/sdd-spec-init "description"`
