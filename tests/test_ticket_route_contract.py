import ast
import inspect

from app.api import ticket
from app.main import app

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PROTECTED_PREFIXES = (
    "/projects",
    "/tickets",
    "/comments",
    "/attachments",
    "/sprints",
)
AUTHORIZATION_FUNCTIONS = frozenset(
    {"require_capability", "require_role", "ensure_role"}
)


def _called_names(function) -> set[str]:
    """Return names called by a function without executing application code."""
    try:
        tree = ast.parse(inspect.getsource(function))
    except (OSError, TypeError, IndentationError):
        return set()
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _reaches_authorization(function, seen=None) -> bool:
    """Follow local helper calls until a capability/role guard is found."""
    seen = set() if seen is None else seen
    if function in seen:
        return False
    seen.add(function)

    called_names = _called_names(function)
    if called_names & AUTHORIZATION_FUNCTIONS:
        return True

    namespace = vars(inspect.getmodule(function))
    return any(
        callable(candidate) and _reaches_authorization(candidate, seen)
        for name, candidate in namespace.items()
        if name in called_names and inspect.isfunction(candidate)
    )


def test_ticket_route_contract_keeps_static_routes_before_project_key_fallback():
    """Static ticket routes must not be captured by /tickets/{project_key}."""
    routes = {
        (route.path, method): route
        for route in ticket.router.routes
        for method in route.methods or ()
    }

    assert ("/tickets/search", "GET") in routes
    assert ("/tickets/detail", "GET") in routes
    assert ("/tickets/{project_key}", "GET") in routes
    assert routes[("/tickets/search", "GET")].endpoint.__name__ == "search_tickets"
    assert routes[("/tickets/detail", "GET")].endpoint.__name__ == "get_ticket_details"
    assert routes[("/tickets/{project_key}", "GET")].endpoint.__name__ == "get_tickets"

    paths = app.openapi()["paths"]
    assert set(paths["/tickets/search"]) == {"get"}
    assert set(paths["/tickets/detail"]) == {"get"}
    assert set(paths["/tickets/{project_key}"]) == {"get"}

    ordered_paths = [route.path for route in ticket.router.routes]
    assert ordered_paths.index("/tickets/search") < ordered_paths.index(
        "/tickets/{project_key}"
    )


def test_state_changing_project_routes_have_authorization_contract():
    """Every state-changing project-domain route must enforce a role/capability."""
    violations = []
    for route in app.routes:
        methods = (getattr(route, "methods", None) or set()) & MUTATING_METHODS
        if not methods or not route.path.startswith(PROTECTED_PREFIXES):
            continue

        dependency_names = {
            dependency.call.__name__
            for dependency in route.dependant.dependencies
            if getattr(dependency.call, "__name__", None)
        }
        has_authenticated_user = "get_current_user" in dependency_names or any(
            "get_current_user" in _called_names(dependency.call)
            for dependency in route.dependant.dependencies
        )
        if not has_authenticated_user or not _reaches_authorization(route.endpoint):
            violations.append(
                f"{','.join(sorted(methods))} {route.path} "
                f"(dependencies={sorted(dependency_names)})"
            )

    assert not violations, "Unprotected state-changing routes:\n" + "\n".join(
        violations
    )
