# Requirements Review

The previously partial requirements below are now implemented:

- Project roles have a single canonical enum definition.
- Tickets support `parent_id` for subtasks.
- Projects own named components; ticket creation, editing, listing, and
  filtering enforce project component references.
- Workflow transitions can be added, edited, and removed incrementally without
  replacing the complete transition set.
- Super administrators retain ticket access through the authorization
  passthrough.
- `/health/live` reports process liveness and `/health/ready` verifies MongoDB
  and attachment-storage readiness.
- `requirements.txt` pins the compatible `bcrypt==4.0.1` release.

Current container configuration uses `GET /health/ready` in both `Dockerfile`
and `docker-compose.yml`.

Current verification: **362 tests passed** with `PYTHONPATH=. pytest -q`.
