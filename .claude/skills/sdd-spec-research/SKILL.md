---
name: sdd-spec-research
description: >-
  Execute independent research for an SDD specification.
  Investigates existing codebase and best practices, generating research.md.
argument-hint: "<feature-name> [-y]"
---

# Spec Research

<background_information>

- **Mission**: Generate comprehensive research document (research.md) that captures discovery findings, architectural investigations, and design recommendations
- **Success Criteria**:
  - Appropriate discovery process executed based on Feature Type classification
  - Current codebase analysis and best practice research completed
  - Research findings structured in research.md template format
  - Findings provide sufficient context for the subsequent design phase
  - spec.json is NOT updated (research.md existence is the sole completion indicator)

</background_information>

<instructions>

## Input

This skill expects:
1. **Feature name** (required): The feature directory name in `docs/tasks/`
2. **Auto-approve flag** (optional): `-y` to auto-approve the previous phase (requirements)

If inputs were provided with this skill invocation, use them directly.
Otherwise, ask the user for the feature name.
If the auto-approve flag is not provided, default to interactive approval mode.

## Core Task

Investigate the existing codebase and research best practices to generate a structured research document (research.md) for the specified feature.

## Execution Steps

### Step 0: Resolve Spec Path

**Resolve Spec Path**: Look for the feature directory in `docs/tasks/todo/<feature-name>/` first, then `docs/tasks/done/<feature-name>/`. Use whichever exists. If neither exists, report an error.

### Step 1: Load Context

**Read all necessary context**:

- `{spec_path}/spec.json` for language and metadata
- `{spec_path}/requirements.md` for project requirements
- `{spec_path}/gap-analysis.md` (if exists) for existing gap analysis results
- **Entire `docs/steering/` directory** for complete project memory:
  - Default files: `structure.md`, `tech.md`, `product.md`
  - All custom steering files
- `docs/settings/templates/specs/research.md` for research document structure
- `docs/settings/rules/design-discovery-full.md` for full discovery rules
- `docs/settings/rules/design-discovery-light.md` for light discovery rules

**Validate requirements approval**:

- If auto-approve flag was provided: Auto-approve requirements in spec.json (set `approvals.requirements.approved: true`)
- Otherwise: Verify that `approvals.requirements.approved` is `true` in spec.json
- If requirements are not approved: Stop execution (see Safety & Fallback)

### Step 2: Discovery & Analysis

**Critical: This phase ensures research is based on complete, accurate information.**

1. **Classify Feature Type**:
   - **New Feature** (greenfield) → Full discovery required
   - **Extension** (existing system) → Integration-focused discovery
   - **Simple Addition** (CRUD/UI) → Minimal or no discovery
   - **Complex Integration** → Comprehensive analysis required

2. **Execute Appropriate Discovery Process**:

   **For Complex/New Features (Full Discovery)**:
   - Read and execute `docs/settings/rules/design-discovery-full.md`
   - Conduct thorough research using WebSearch/WebFetch:
     - Latest architectural patterns and best practices
     - External dependency verification (APIs, libraries, versions, compatibility)
     - Official documentation, migration guides, known issues
     - Performance benchmarks and security considerations

   **For Extensions (Light Discovery)**:
   - Read and execute `docs/settings/rules/design-discovery-light.md`
   - Focus on integration points, existing patterns, compatibility
   - Use Grep to analyze existing codebase patterns

   **For Simple Additions (Minimal Discovery)**:
   - Skip formal discovery, quick pattern check only

3. **Incorporate Gap Analysis** (if available):
   - If `gap-analysis.md` was loaded in Step 1, use its findings as additional research context
   - Cross-reference gap analysis results with discovery findings
   - Prioritize addressing identified gaps in the research

4. **Current State Analysis**:
   - Analyze existing codebase structure and architecture patterns
   - Map reusable components, services, and utilities
   - Identify domain boundaries and data flows
   - Document integration points and dependencies
   - Determine approach: extend vs refactor vs wrap

5. **Best Practice Research**:
   - Research technology selection rationale and recommended patterns
   - Investigate reference implementations and industry standards
   - Use WebSearch/WebFetch for external dependencies, APIs, and latest best practices
   - Document security considerations and performance implications

6. **Retain Discovery Findings for Step 3**:
   - External API contracts and constraints
   - Technology decisions with rationale
   - Existing patterns to follow or extend
   - Integration points and dependencies
   - Identified risks and mitigation strategies
   - Potential architecture patterns and boundary options
   - Parallelization considerations for future tasks

### Step 3: Generate research.md

**Using the template loaded in Step 1 (`docs/settings/templates/specs/research.md`) and the discovery findings from Step 2, generate research.md.**

1. **Language Compliance**: Write all content in the language specified by `spec.json.language` (e.g., `"ja"` means Japanese).

2. **Populate Template Sections**:
   - **Summary**: Fill in `feature-name`, discovery scope (from Feature Type classification), and 3 key findings
   - **Research Log**: Create subsections for each major investigation topic from Step 2. Each subsection must include Context, Sources Consulted, Findings, and Implications
   - **Architecture Pattern Evaluation**: Document candidate patterns considered during research with their strengths and risks
   - **Design Decisions**: Record significant decisions that will inform `design.md`. Include context, alternatives considered, selected approach, rationale, trade-offs, and follow-up items
   - **Risks & Mitigations**: List identified risks with proposed mitigations
   - **References**: Provide links and citations to sources consulted

3. **Include Design Recommendations**: Based on the research findings, explicitly state actionable recommendations for the design phase. These should appear in the Design Decisions section and summarize:
   - Recommended architecture patterns and why
   - Integration strategy with existing codebase
   - Technology choices with evidence-based rationale
   - Areas requiring special attention during design

4. **Write research.md**: Output the completed document to `{spec_path}/research.md` using the Write tool.

5. **Do NOT update spec.json**: The existence of research.md is the sole indicator of research completion. No phase transition or approval state changes are needed.

## Critical Constraints

- **No spec.json update**: Do NOT modify spec.json. The existence of research.md is the sole indicator of research completion.
- **Steering alignment**: Respect existing architecture patterns from steering context
- **Template adherence**: Follow `docs/settings/templates/specs/research.md` structure
- **Language compliance**: Use the language specified in `spec.json.language`

</instructions>

## Tool Guidance

- **Read first**: Load all context (spec, steering, rules, templates, gap-analysis) before taking action
- **Research when uncertain**: Use WebSearch/WebFetch for external dependencies, APIs, and latest best practices
- **Analyze existing code**: Use Grep to find patterns and integration points in codebase
- **Write last**: Generate research.md only after all research and analysis complete

## Output Description

Provide brief summary in the language specified in spec.json:

1. **Status**: Confirm research document generated at `docs/tasks/<feature-name>/research.md`
2. **Discovery Type**: Which discovery process was executed (full/light/minimal)
3. **Key Findings**: 2-3 critical insights that will inform the design
4. **Next Action**: Guidance for next step

**Format**: Concise Markdown (under 200 words)

## Safety & Fallback

### Error Scenarios

**Requirements Not Approved**:

- **Stop Execution**: Cannot proceed without approved requirements
- **User Message**: "Requirements not yet approved. Approval required before research execution."
- **Suggested Action**: "Run `/sdd-spec-research <feature-name> -y` to auto-approve requirements and proceed"

**Missing Requirements**:

- **Stop Execution**: Requirements document must exist
- **User Message**: "No requirements.md found at `docs/tasks/<feature-name>/requirements.md`"
- **Suggested Action**: "Run `/sdd-spec-requirements <feature-name>` to generate requirements first"

**Spec Directory Not Found**:

- **Stop Execution**: Feature directory must exist
- **User Message**: "No spec directory found for `<feature-name>` in `docs/tasks/todo/` or `docs/tasks/done/`"
- **Suggested Action**: "Run `/sdd-spec-init <description>` to initialize a new specification"

**Template Missing**:

- **User Message**: "Template file missing at `docs/settings/templates/specs/research.md`"
- **Suggested Action**: "Check repository setup or restore template file"
- **Fallback**: Use inline basic structure with warning

**Steering Context Missing**:

- **Warning**: "Steering directory empty or missing - research may not align with project standards"
- **Proceed**: Continue with research but note limitation in output

### Next Phase: Design Generation

**After Research Completed**:

- Review generated research at `docs/tasks/<feature-name>/research.md`
- Then `/sdd-spec-design <feature-name> -y` to proceed to design phase
- spec-design will automatically read research.md as input

**If Re-research Needed**:

- Delete `research.md` and re-run `/sdd-spec-research <feature-name> -y`
