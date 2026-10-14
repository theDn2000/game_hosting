# Claude Code Project Instructions

## Project Goal

Build `game_hosting`: a commercially viable, recurring-revenue game-server hosting platform, starting with Minecraft Java Edition. The product should progressively support automated provisioning, server lifecycle management, a clear web control panel, persistent worlds, backups, monitoring, and eventually multiple infrastructure nodes.

This is a real product intended to evolve into production. Optimize for useful progress without creating disposable architecture or prematurely building a large platform.

## How We Work

- Work in small, coherent, verifiable tasks with explicit acceptance criteria.
- Before a complex, cross-cutting, destructive, or architecture-changing task, inspect the repository and propose a concise plan. Wait for approval before implementing that task.
- For routine, bounded tasks with clear acceptance criteria, implement directly.
- Inspect existing code and documentation before making assumptions. Do not claim files, features, tests, or commands exist or work unless verified.
- Prefer the smallest complete solution that fits the current phase.
- Explain important technical decisions and trade-offs briefly. The user understands engineering and programming; avoid unnecessary beginner-level explanations.
- Do not stop at suggestions or snippets when the task asks for implementation. Make the changes in the repository, run relevant checks, and report the actual results.
- If blocked, ask one focused question or state the blocker and what is needed.
- Never silently expand the scope of a task.

## Language and Writing Conventions

- Write source code, identifiers, comments, docstrings, tests, and technical documentation in English.
- Keep existing user-facing product text in its established language unless the task explicitly changes it.
- The existing README may remain in Spanish; do not translate it merely for consistency.
- Use clear, descriptive names and conventional Python formatting.
- Prefer concise documentation that records durable decisions rather than narrating every implementation detail.

## Architecture and Hard Rules

- Follow `docs/ARCHITECTURE.md` for the current architecture, target architecture, dependency rules, security constraints, and recorded decisions.
- Follow `docs/ROADMAP.md` for phase order, current milestone, acceptance criteria, and scope.
- Keep business/application logic separate from infrastructure-specific details where doing so provides a real seam for change.
- The intended dependency direction is API/interface -> application service -> infrastructure adapter. Infrastructure details must not leak unnecessarily into business logic.
- The frontend must never communicate directly with Docker Engine.
- Never expose the Docker Engine socket or an unauthenticated Docker API to the public internet.
- Do not hard-code secrets, credentials, private keys, or production tokens. Do not commit local environment files, virtual environments, server worlds, backups, or other runtime data.
- Do not add dependencies, frameworks, services, or infrastructure without explaining the concrete need and considering a simpler alternative.
- Do not introduce Kubernetes, Redis, message queues, or distributed infrastructure until a demonstrated requirement justifies them.
- Do not build production billing, authentication, multi-node orchestration, or other later-phase systems ahead of the roadmap.
- Preserve persistent game data independently of container lifecycle. Never delete or overwrite world data as part of a routine container operation.
- Use explicit, understandable error handling. Do not hide unexpected failures behind broad `except Exception` handlers or misleading success messages.
- Avoid breaking changes to established interfaces without a clear reason and approval when the change is significant.
- Treat all input as untrusted at API boundaries; apply authentication, authorization, validation, and resource limits when those boundaries are introduced.

## Testing and Verification

- Add or update automated tests for meaningful behavior and bug fixes.
- Prefer unit tests for application logic and isolated adapters; use integration tests when they verify real interactions that unit tests cannot cover.
- Keep tests deterministic and avoid requiring a running Minecraft server or external service for ordinary unit tests.
- For Docker-related behavior, use mocks/fakes for unit tests where appropriate and clearly distinguish those from real Docker integration checks.
- Run the narrowest relevant checks first, then broader checks when practical.
- Before reporting a task as complete, verify the changed behavior with tests, lint/type checks if configured, or an explicit manual check when automation is not yet available.
- Report exactly which commands were run and whether they passed. If a check was not run, say so.
- Do not claim production readiness based only on a passing local test.

## Documentation Maintenance

After each completed task:
- Update `docs/ARCHITECTURE.md` if the implementation, dependencies, security posture, data model, extension points, or architectural decisions changed.
- Update `docs/ROADMAP.md` to reflect milestone status, evidence for completion, the current phase, the immediate next step, and any newly discovered open questions or out-of-scope items.
- Update this file only when a durable project-wide rule changes; do not turn it into a task log.
- Keep documentation consistent with the code. Distinguish verified current state from planned target state.

## Task Completion Report

At the end of each task, report:
1. What changed and why.
2. Files created, modified, or deleted.
3. Tests/checks executed and their actual results.
4. Important decisions, assumptions, risks, or unverified items.
5. Documentation updates made.
6. The recommended immediate next step.

Do not begin the next task automatically unless explicitly asked.
