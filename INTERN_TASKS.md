# Intern Tasks Review

The following review items are implemented in the current codebase:

| Item | Current status | Current reference |
| --- | --- | --- |
| Project-role enum deduplication | Complete | `app/models/project_member.py`, `app/models/project.py` |
| Subtask `parent_id` | Complete | `app/models/ticket.py`, `app/api/ticket.py` |
| Project components and component filtering | Complete | `app/api/project.py`, `app/api/ticket.py` |
| Incremental workflow-transition editing | Complete | `app/api/project.py`, `app/models/project.py` |
| Super-admin ticket access passthrough | Complete | `app/api/permissions.py`, `app/api/ticket.py` |
| Dependency-aware health/readiness endpoints | Complete | `app/main.py` (`/health/live`, `/health/ready`) |
| Compatible bcrypt pin | Complete | `requirements.txt` (`bcrypt==4.0.1`) |

The container health checks use `/health/ready`, which checks MongoDB and
attachment storage. `/health/live` is reserved for process liveness checks.

The repaired test suite currently reports **360 passed**.

Recent implementation commits include:

- `9edc303` — dependency-aware health/readiness checks
- `6f1df8e` — controlled project components
- `6f2d5cd` — incremental workflow transition updates
- `15c78df` — project member and role management completion
- `b8d888c` — expanded regression coverage
