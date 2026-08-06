from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient

from app.config import get_settings

settings = get_settings()
client = MongoClient(settings.mongo_uri)

db = client[settings.mongo_db_name]


user_collection = db["users"]
projects_collection = db["projects"]
ticket_collection = db["tickets"]
comments_collection = db["comments"]
notifications_collection = db["notifications"]
notification_preferences_collection = db["notification_preferences"]
saved_filters_collection = db["saved_filters"]
rate_limits_collection = db["rate_limits"]
reset_tokens_collection = db["reset_tokens"]
statuses_collection = db["statuses"]
attachment_collection = db["attachments"]
ticket_activity_collection = db["ticket_activity"]
project_activity_collection = db["project_activity"]
counters_collection = db["counters"]
project_members_collection = db["project_members"]
project_permission_overrides_collection = db["project_permission_overrides"]
issue_links_collection = db["issue_links"]
transitions_collection = db["transitions"]
project_components_collection = db["project_components"]
sprints_collection = db["sprints"]


PROJECT_CODE_UNIQUE_INDEX = "projects_code_unique"
PROJECT_CODE_COLLATION = {"locale": "en", "strength": 2}
ISSUE_LINK_UNIQUE_INDEX = "issue_links_from_to_type_unique"
ISSUE_LINK_FROM_TICKET_INDEX = "issue_links_from_ticket"
ISSUE_LINK_TO_TICKET_INDEX = "issue_links_to_ticket"
TRANSITION_PROJECT_NAME_INDEX = "transitions_project_name"
TRANSITION_PROJECT_SOURCE_INDEX = "transitions_project_source"
NOTIFICATION_PREFERENCE_UNIQUE_INDEX = "notification_preferences_user_project_unique"
SAVED_FILTER_USER_NAME_INDEX = "saved_filters_user_name_unique"
RATE_LIMIT_EXPIRY_INDEX = "rate_limits_expiry_ttl"
SPRINT_PROJECT_NAME_INDEX = "sprints_project_name_unique"
SPRINT_ACTIVE_PROJECT_INDEX = "sprints_one_active_per_project"
TICKET_SPRINT_BOARD_INDEX = "tickets_sprint_status_position"
TICKET_BACKLOG_ORDER_INDEX = "tickets_project_backlog_position"
SUPER_ADMIN_UNIQUE_INDEX = "users_one_super_admin"
LEGACY_INERT_GLOBAL_ROLES = frozenset({"user", "customer", "agent"})


def ensure_database_indexes() -> None:
    """Create the indexes required for application-level invariants."""
    user_collection.create_index(
        [("role", ASCENDING)],
        name=SUPER_ADMIN_UNIQUE_INDEX,
        unique=True,
        partialFilterExpression={"role": "super_admin"},
    )
    projects_collection.create_index(
        [("code", ASCENDING)],
        name=PROJECT_CODE_UNIQUE_INDEX,
        unique=True,
        collation=PROJECT_CODE_COLLATION,
    )
    issue_links_collection.create_index(
        [
            ("from_ticket_id", ASCENDING),
            ("to_ticket_id", ASCENDING),
            ("link_type", ASCENDING),
        ],
        name=ISSUE_LINK_UNIQUE_INDEX,
        unique=True,
    )
    issue_links_collection.create_index(
        [("from_ticket_id", ASCENDING)],
        name=ISSUE_LINK_FROM_TICKET_INDEX,
    )
    issue_links_collection.create_index(
        [("to_ticket_id", ASCENDING)],
        name=ISSUE_LINK_TO_TICKET_INDEX,
    )
    transitions_collection.create_index(
        [("project_id", ASCENDING), ("name", ASCENDING)],
        name=TRANSITION_PROJECT_NAME_INDEX,
    )
    transitions_collection.create_index(
        [("project_id", ASCENDING), ("from_status_ids", ASCENDING)],
        name=TRANSITION_PROJECT_SOURCE_INDEX,
    )
    notification_preferences_collection.create_index(
        [("user_id", ASCENDING), ("project_id", ASCENDING)],
        name=NOTIFICATION_PREFERENCE_UNIQUE_INDEX,
        unique=True,
    )
    saved_filters_collection.create_index(
        [("user_id", ASCENDING), ("name_key", ASCENDING)],
        name=SAVED_FILTER_USER_NAME_INDEX,
        unique=True,
    )
    rate_limits_collection.create_index(
        [("expires_at", ASCENDING)],
        name=RATE_LIMIT_EXPIRY_INDEX,
        expireAfterSeconds=0,
    )
    sprints_collection.create_index(
        [("project_id", ASCENDING), ("name_key", ASCENDING)],
        name=SPRINT_PROJECT_NAME_INDEX,
        unique=True,
    )
    sprints_collection.create_index(
        [("project_id", ASCENDING), ("status", ASCENDING)],
        name=SPRINT_ACTIVE_PROJECT_INDEX,
        unique=True,
        partialFilterExpression={"status": "active"},
    )
    ticket_collection.create_index(
        [
            ("project_id", ASCENDING),
            ("sprint_id", ASCENDING),
            ("current_status_id", ASCENDING),
            ("board_position", ASCENDING),
        ],
        name=TICKET_SPRINT_BOARD_INDEX,
    )
    ticket_collection.create_index(
        [
            ("project_id", ASCENDING),
            ("sprint_id", ASCENDING),
            ("backlog_position", ASCENDING),
        ],
        name=TICKET_BACKLOG_ORDER_INDEX,
    )


def migrate_legacy_global_roles() -> int:
    """Remove historical role labels that never granted any authorization."""
    migrated_count = 0
    legacy_users = user_collection.find(
        {"role": {"$in": list(LEGACY_INERT_GLOBAL_ROLES)}}
    )
    for user in legacy_users:
        user_collection.update_one(
            {"_id": user["_id"]},
            {"$unset": {"role": ""}},
        )
        migrated_count += 1
    return migrated_count


def migrate_legacy_workflow_transitions() -> int:
    """Move previously embedded project transitions into their own collection."""
    migrated_count = 0
    for project in projects_collection.find({}):
        if "workflow_transitions" not in project:
            continue

        legacy_transitions = project.get("workflow_transitions") or []
        has_collection_workflow = transitions_collection.count_documents(
            {"project_id": project["_id"]}
        )
        if not has_collection_workflow:
            for order, legacy_transition in enumerate(legacy_transitions):
                source_ids = legacy_transition.get("from_status_ids")
                if source_ids is None and legacy_transition.get("from_status_id"):
                    source_ids = [legacy_transition["from_status_id"]]
                guard = legacy_transition.get(
                    "guard",
                    legacy_transition.get("guards", {}),
                )
                if not (
                    legacy_transition.get("name")
                    and source_ids
                    and legacy_transition.get("to_status_id")
                ):
                    continue

                transitions_collection.insert_one(
                    {
                        "project_id": project["_id"],
                        "name": legacy_transition["name"],
                        "from_status_ids": list(dict.fromkeys(map(str, source_ids))),
                        "to_status_id": str(legacy_transition["to_status_id"]),
                        "guard": {
                            "allowed_roles": guard.get(
                                "allowed_roles",
                                ["lead", "member"],
                            ),
                            "requires_assignee": guard.get(
                                "requires_assignee",
                                False,
                            ),
                        },
                        "order": order,
                        "created_at": datetime.now(timezone.utc),
                        "created_by": project.get("created_by"),
                    }
                )
                migrated_count += 1

        projects_collection.update_one(
            {"_id": project["_id"]},
            {"$unset": {"workflow_transitions": ""}},
        )

    return migrated_count
