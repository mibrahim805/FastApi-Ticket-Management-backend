from app.api import ticket
from app.main import app


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
