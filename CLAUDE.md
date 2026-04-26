<!-- SDD:BEGIN -->
# AI-DLC and Spec-Driven Development

Spec Driven Development implementation on AI-DLC (AI Development Life Cycle)

## Project Context

### Paths

- Steering: `docs/steering/`
- Specs: `docs/tasks/`

### Steering vs Specification

**Steering** (`docs/steering/`) - Guide AI with project-wide rules and context
**Specs** (`docs/tasks/`) - Formalize development process for individual features

### Active Specifications

- Check `docs/tasks/` for active specifications
- Use `/sdd-spec-status <feature-name>` to check progress

## Development Guidelines

- Think in English, generate responses in English. All Markdown content written to project files (e.g., requirements.md, design.md, tasks.md, research.md, validation reports) MUST be written in the target language configured for this specification (see spec.json.language).

## Minimal Workflow

- Phase 0 (optional): `/sdd-steering`, `/sdd-steering-custom`
- Phase 1 (Specification):
  - `/sdd-spec-init "description"`
  - `/sdd-spec-requirements <feature-name>`
  - `/sdd-validate-gap <feature-name>` (optional: for existing codebase)
  - `/sdd-spec-research <feature-name>` (research & discovery)
  - `/sdd-spec-design <feature-name> [-y]`
  - `/sdd-validate-design <feature-name>` (optional: design review)
  - `/sdd-spec-tasks <feature-name> [-y]`
- Phase 2 (Implementation): `/sdd-spec-impl <feature-name> [tasks]`
  - `/sdd-validate-impl <feature-name>` (optional: mid-implementation validation)
- Phase 3 (Completion): `/sdd-spec-done <feature-name>`
- Progress check: `/sdd-spec-status <feature-name>` (use anytime)

## Development Rules

- Workflow: Requirements → Research → Design → Tasks → Implementation → Completion
- Human review required each phase; use `-y` only for intentional fast-track
- Keep steering current and verify alignment with `/sdd-spec-status`
- Follow the user's instructions precisely, and within that scope act autonomously: gather the necessary context and complete the requested work end-to-end in this run, asking questions only when essential information is missing or the instructions are critically ambiguous.

## Steering Configuration

- Load entire `docs/steering/` as project memory
- Default files: `product.md`, `tech.md`, `structure.md`
- Custom files are supported (managed via `/sdd-steering-custom`)
<!-- SDD:END -->
