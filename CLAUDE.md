# Claude Code instructions

`AGENTS.md` is the shared authoritative instruction file for both Codex and Claude Code. Read it in full before acting.

For repository completion work:

1. Read `docs/20260811-agentic-art-research-repository-execution-plan.md` in full.
2. Read `execution/task-queue.yaml` and `execution/state.yaml`.
3. Claim the lowest-ID `READY` task whose dependencies are `DONE`.
4. Implement it without asking for the next step.
5. Run the required validation and tests.
6. Update the plan, queue, state, discoveries, decisions, and next resume point.

Do not store private raw data, change the system design silently, or mark a task complete without its acceptance behavior.

