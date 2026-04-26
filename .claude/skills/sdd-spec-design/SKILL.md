---
name: sdd-spec-design
description: >-
  Create comprehensive technical design for an SDD specification.
  Translates requirements (WHAT) into architectural design (HOW).
argument-hint: "<feature-name> [-y]"
---

# Technical Design

<background_information>

- **Mission**: Generate comprehensive technical design document that translates requirements (WHAT) into architectural design (HOW)
- **Success Criteria**:
  - All requirements mapped to technical components with clear interfaces
  - Research findings from `research.md` integrated into design decisions
  - Design aligns with steering context and existing patterns
  - Discuss design with users for clarity and approval
  - Visual diagrams included for complex architectures

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (required): The feature directory name in `docs/tasks/`
2. **Auto-approve flag** (optional): `-y` to auto-approve the previous phase

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the feature name.
If the auto-approve flag is not provided, default to interactive approval mode.

## Core Task

Understand requirements and leverage existing research findings from `research.md`.
Concretize the design through dialogue with users.
Write technical design document for the specified feature based on approved requirements.

## Execution Steps

### Step 0: Resolve Spec Path

**Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

### Step 1: Load Context

**Read all necessary context**:

- `{spec_path}/spec.json`, `requirements.md`, `design.md` (if exists)
- **Entire `docs/steering/` directory** for complete project memory
- `docs/settings/templates/specs/design.md` for document structure
- `docs/settings/rules/design-principles.md` for design principles

**Check for research.md**:

- Check if `{spec_path}/research.md` exists
- If it exists, read it and include its contents as discovery/research context for design generation

**Validate requirements approval**:

- If auto-approve flag was provided: Auto-approve requirements in spec.json
- Otherwise: Verify approval status (stop if unapproved, see Safety & Fallback)

### Step 2: Load Research Context

**Use the research results from `research.md` as design input. Do NOT conduct independent discovery or external research.**

1. **If `research.md` exists** (checked in Step 1):
   - Use the contents of `research.md` as the discovery/research findings for design generation
   - Extract key findings: architecture patterns, technology decisions, integration points, risks, and design recommendations
   - Retain these findings for Step 3

2. **If `research.md` does NOT exist**:
   - Display warning: "research.md が未生成です。`/sdd-spec-research <feature-name>` の実行を推奨します。調査結果なしで設計を続行します。"
   - Continue with design generation without discovery findings
   - Do NOT perform Feature Type classification, Discovery process, or external research (WebSearch/WebFetch)

### Step 3: Generate Design Document

1. **Load Design Template and Rules**:

- Read `docs/settings/templates/specs/design.md` for structure
- Read `docs/settings/rules/design-principles.md` for principles

2. **Generate Design Document**:

- **Follow specs/design.md template structure and generation instructions strictly**
- **Integrate all discovery findings**: Use researched information (APIs, patterns, technologies) throughout component definitions, architecture decisions, and integration points
- If existing design.md found in Step 1, use it as reference context (merge mode)
- Apply design rules: Type Safety, Visual Communication, Formal Tone
- Use language specified in spec.json
- Ensure sections reflect updated headings ("Architecture Pattern & Boundary Map", "Technology Stack & Alignment", "Components & Interface Contracts") and reference supporting details from `research.md`

3. **Update Metadata** in spec.json:

- Set `phase: "design-generated"`
- Set `approvals.design.generated: true, approved: false`
- Set `approvals.requirements.approved: true`
- Update `updated_at` timestamp

## Critical Constraints

- **Type Safety**:
  - Enforce strong typing aligned with the project's technology stack.
  - For statically typed languages, define explicit types/interfaces and avoid unsafe casts.
  - For TypeScript, never use `any`; prefer precise types and generics.
  - For dynamically typed languages, provide type hints/annotations where available (e.g., Python type hints) and validate inputs at boundaries.
  - Document public interfaces and contracts clearly to ensure cross-component type safety.
- **Research Context**: Use findings from `research.md` as the basis for design decisions. Do not conduct independent external research
- **Steering Alignment**: Respect existing architecture patterns from steering context
- **Template Adherence**: Follow specs/design.md template structure and generation instructions strictly
- **Design Focus**: Architecture and interfaces ONLY, no implementation code
- **Requirements Traceability IDs**: Use numeric requirement IDs only (e.g. "1.1", "1.2", "3.1", "3.3") exactly as defined in requirements.md. Do not invent new IDs or use alphabetic labels.

</instructions>

## Tool Guidance

- **Read first**: Load all context before taking action (specs, steering, templates, rules)
- **Read research.md**: If `research.md` exists in the feature directory, read it first and use its contents as the sole source of discovery/research context. Do NOT use WebSearch or WebFetch for independent research
- **Analyze existing code**: Use Grep to find patterns and integration points in codebase
- **Write last**: Generate design.md only after loading all context including research findings

## Output Description

**Command execution output** (separate from design.md content):

Provide brief summary in the language specified in spec.json:

1. **Status**: Confirm design document generated at `docs/tasks/<feature-name>/design.md`
2. **Research Context**: Whether `research.md` was available and used
3. **Key Findings**: 2-3 critical insights from `research.md` that shaped the design (if available)
4. **Next Action**: Approval workflow guidance (see Safety & Fallback)

**Format**: Concise Markdown (under 200 words) - this is the command output, NOT the design document itself

**Note**: The actual design document follows `docs/settings/templates/specs/design.md` structure.

## Safety & Fallback

### Error Scenarios

**Requirements Not Approved**:

- **Stop Execution**: Cannot proceed without approved requirements
- **User Message**: "Requirements not yet approved. Approval required before design generation."
- **Suggested Action**: "Run `/sdd-spec-design <feature-name> -y` to auto-approve requirements and proceed"

**Missing Requirements**:

- **Stop Execution**: Requirements document must exist
- **User Message**: "No requirements.md found at `docs/tasks/<feature-name>/requirements.md`"
- **Suggested Action**: "Run `/sdd-spec-requirements <feature-name>` to generate requirements first"

**Template Missing**:

- **User Message**: "Template file missing at `docs/settings/templates/specs/design.md`"
- **Suggested Action**: "Check repository setup or restore template file"
- **Fallback**: Use inline basic structure with warning

**Steering Context Missing**:

- **Warning**: "Steering directory empty or missing - design may not align with project standards"
- **Proceed**: Continue with generation but note limitation in output

**Invalid Requirement IDs**:
  - **Stop Execution**: If requirements.md is missing numeric IDs or uses non-numeric headings (for example, "Requirement A"), stop and instruct the user to fix requirements.md before continuing.

### Next Phase: Task Generation

**If Design Approved**:

- Review generated design at `docs/tasks/<feature-name>/design.md`
- **Optional**: Run `/sdd-validate-design <feature-name>` for interactive quality review
- Then `/sdd-spec-tasks <feature-name> -y` to generate implementation tasks

**If Modifications Needed**:

- Provide feedback and re-run `/sdd-spec-design <feature-name>`
- Existing design used as reference (merge mode)

**Note**: Design approval is mandatory before proceeding to task generation.
