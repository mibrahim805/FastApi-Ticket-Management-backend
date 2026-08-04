# Roles and Permissions

## 1. Authorization model

The application uses global roles for system-wide administration and project
memberships for project-scoped access. Ordinary users receive access through a
`lead`, `member`, or `reporter` membership in each project.

## 2. Global roles

- `super_admin` manages system-wide configuration and has the global override.
- `admin` manages administrative user operations and system-wide reports.
- Ordinary accounts have no global role-based project access.

## 3. Project roles

- `lead` administers a project and receives every runtime capability.
- `member` performs normal ticket and collaboration work.
- `reporter` creates and follows tickets with restricted project access.

Project-specific grants and revocations may change the effective capabilities
of a role without changing that role in other projects. `manage_project` cannot
be overridden.

## 4. Contextual authorization

Some actions depend on both a project capability and the resource context. For
example, ticket creators or current assignees may upload attachments even when
their project role does not grant the general upload capability. These actor
exceptions are evaluated by dedicated authorization helpers.

## 5. Route authorization contract

Every state-changing route under `/projects`, `/tickets`, `/comments`,
`/attachments`, and `/sprints` must authenticate the caller and reach a
capability or role guard. `tests/test_ticket_route_contract.py` enforces this
contract, including guards reached through endpoint helper functions.

## 6. Developer verification

Install the repository hooks once in each clone:

```bash
pre-commit install
```

Run all hooks and tests with:

```bash
pre-commit run --all-files
python -m pytest -q
```

## 7. Resolved design decisions

The `Permission` and `Capability` enums are intentionally separate. This is a
settled design decision, not an open follow-up item:

- `Permission` is the legacy/configurable project-role grant vocabulary used by
  the named permissions response, including `my-permissions`.
- `Capability` is the runtime endpoint-authorization vocabulary. It also covers
  endpoint-specific and contextual operations such as ticket transitions and
  attachment exceptions.

Keeping the two vocabularies separate preserves the existing permissions API
while allowing runtime authorization rules to remain explicit. Any future
consolidation would be a separately scoped migration with API compatibility and
stored override migration requirements.

As of 2026-08-04, the local pre-commit hooks pass and the full test suite reports
`338 passed`.
