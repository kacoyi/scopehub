# ScopeHub contributor guidance

Follow the shared workspace policy in `/home/loong/ai-workspace/AGENTS.md` when working in this workspace. Project requirements and the engineering contract remain authoritative for ScopeHub.

## Language

- Use English for repository content, including documentation, specifications, source comments, new identifiers, commit messages, and GitHub issues and pull requests.
- Keep protocol identifiers, file paths, and technical names consistent with the engineering contract.
- Communicate with the project owner in Chinese unless they request another language. This conversation preference does not change the repository language.

## Project status

Follow `engineering-contract-v1.json` and `contracts/contract.json`. The repository contains an M0 scaffold, not a working market application. Keep unimplemented operations and unverified Windows acceptance explicit. Real data and cloud models are not configured.

## Product intent

- Build for close human–AI financial collaboration: market understanding, analysis, decision-making, and authorized operations. Stocks and futures are the initial asset classes, with room for additional types.
- Select features and complexity for concrete collaboration needs. A lightweight footprint is not a mandatory objective; avoid unnecessary features and speculative infrastructure.
- The first chart-and-indicator workflow is an initial delivery scenario. Keep the product and its modules open to revision, replacement, and removal.
- Product AI should actively investigate, compute, challenge assumptions, and perform authorized operations. Human ownership of investment judgments does not imply a guarantee of profit or an independent capital-management mandate for the AI.
- For the product AI, acceptance of market risk grants no additional permissions. Keep investment judgment, operation authorization, and system correctness distinct; data and execution defects remain engineering failures to prevent and correct.

## Execution

- Start with `pnpm project:status`; use `project/tasks.json` for task scope, dependencies, acceptance and the next action. Maintain meaningful work items and update the focus before handing off; do not rely on conversation memory alone.
- Use `pnpm project:context [task-id]` for the relevant task, decisions, scenarios, risks and checks. Read only the additional source needed for the next outcome; context records are an index, not a substitute for inspecting implementation.
- Before activating meaningful work, add its `plan`: observable outcome, change types, relevant context, hypotheses, failure risks with checks and recovery direction. Keep routine edits within the current task. Choose a small demonstrable slice and investigate consequential uncertainty early. Revise unfinished plans when evidence changes; preserve accepted history.
- `project/delivery.json` maps product scenarios and change types to checks. `project/feedback.json` records consequential defects, failed assumptions and owner feedback with corrective tasks. Address feedback through completed work and regression evidence; document deliberate deferrals. Do not invent user validation or count a scenario definition as observed behavior.
- `project/checks.json` is the harness catalog and acceptance mapping. Register implemented checks there as features arrive; planned and blocked checks never count as passed.
- Archive passing task evidence with `pnpm project:record <task-id> --report <report-path>`, attach the returned path and digest to the task, then mark it done. Completed task definitions and accepted ADR rationale are historical records; use new tasks or superseding ADRs for changed outcomes or decisions. See ADR 0004 for scope and lifecycle requirements.
- Use `uv sync --locked` and `pnpm install --frozen-lockfile` for setup. Python is 3.12; Node is 24; use the pinned pnpm version.
- Run `pnpm check` for the local harness. Reports and logs are retained under `.reports/runs/<run-id>` with a latest copy at `.reports/test-report.json`; `pnpm project:status` reports whether the latest result matches current execution inputs. A blocked check is not a pass. Native Windows validation is required for M0 completion.
- Use `pnpm project:doctor` for toolchain diagnostics and `pnpm check:fast` for early feedback; `pnpm check --task <id>` runs that task's checks plus environment and project validation. Selected checks report every omission explicitly. Complete required task checks locally or with matching hosted evidence before marking work done; keep local fast and affected checks, without duplicating expensive hosted checks unless debugging needs them. Retain the failed report when changing direction. Use `uv run python` for repository Python commands so a shell shim does not select another environment.
- Follow `project/delivery.json` source-control policy. Commit coherent validated outcomes, recovery checkpoints and handoffs on a task branch. Use `pnpm project:checkpoint` to inspect recoverability, local upstream refs and check timings. A local commit, remote push, CI pass, task completion, merge and release establish different states.
- Within owner-authorized publication scope, push meaningful checkpoints and create or update a draft PR; an ordinary task-branch push without a PR does not trigger the current workflow. Do not ask again for permission already granted for that repository and branch scope. Initial upload of previously local content to a public remote still needs publication authorization when none exists.
- Use `pnpm project:ci` to inspect the latest workflow for the current clean commit with the official `gh` account workflow. Missing, queued, cancelled, stale and failed runs are not passes. Continue independent work while CI runs, then revisit failures and the current commit before integration. Do not merge or release solely because a branch was pushed.
- Download observed CI artifacts into `.reports/ci/<run-id>/<platform>` and archive with `pnpm project:record <task-id> --report <artifact-dir>/test-report.json --artifact-root <artifact-dir>`. Do not overwrite local latest reports. Import checks source and task/decision identity, CI provenance and log digests; authenticated artifact retrieval remains the trust boundary.
- Run `pnpm project:history --base <reviewed-commit>` when a review base is available. CI compares accepted tasks and ADRs against the PR base or previous pushed revision. A missing base is reported as not applicable, never as a completed comparison. The local uncommitted state is not recoverable Git history.
- `pnpm project:readiness` exposes remaining scenario, milestone, release and unresolved-feedback gates. A clean committed source, actual packaged Windows acceptance, retained artifacts and owner product review are release requirements. Fast checks, compilation and mock evaluation each establish only their own scope.
- Expensive packaged acceptance uses `pnpm check --profile release --require-windows` or the CI manual `release` profile. Checks with `cadence: release` are omitted from the default change harness and remain visibly unverified. Run a check explicitly through the affected task when a dependency or packaging change needs it sooner. Do not require a two-hour soak test for every ordinary edit.
- Edit canonical JSON Schemas in `contracts/schemas/protocol.schema.json`; run `pnpm generate`. Never edit generated types or operation schema projections. Add shared valid/invalid vectors when the wire contract changes.
- Follow dependency boundaries checked by `scripts/check_architecture.py`. Add implementation modules when a use case needs them, not as placeholders.
- Choose external data channels, products, services, libraries, shared capabilities, or local code by concrete fit and lifetime cost. Reuse suitable capabilities; build focused implementations when existing options are unsuitable or more burdensome. Keep integrations replaceable and evaluation proportional to their impact.
- Keep information channels independently replaceable and able to coexist. Omniscience is an optional candidate; no named provider is a mandatory gateway. Preserve source provenance and capability differences, and add adapters when a concrete workflow needs them.
- AI tools and user actions must share the Rust operation registry. Schema availability does not grant permission or mean an operation is implemented.
- Keep repository content in English and owner communication in Chinese. Store decisions and status as JSON; do not add prose guides or README files. Preserve legally required licenses and notices.
- Language responsibilities, process boundaries, permission changes and protocol changes follow the versioned decision policy in the engineering contract. Routine implementation inside existing boundaries needs no additional approval.
- No trading, real provider scraping, cloud model calls, generated Python execution, or credential handling is part of M0. Apply the parent `ai-cap` rule when a later task actually uses shared capabilities.
