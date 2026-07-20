from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient

client = MongoClient("mongodb://localhost:27017")


db = client["mydatabase"]


user_collection = db["users"]
projects_collection = db["projects"]
ticket_collection = db["tickets"]
comments_collection = db["comments"]
notifications_collection = db["notifications"]
notification_preferences_collection = db["notification_preferences"]
saved_filters_collection = db["saved_filters"]
reset_tokens_collection = db["reset_tokens"]
statuses_collection = db["statuses"]
attachment_collection = db["attachments"]
ticket_activity_collection = db["ticket_activity"]
counters_collection = db["counters"]
project_members_collection = db["project_members"]
issue_links_collection = db["issue_links"]
transitions_collection = db["transitions"]


PROJECT_CODE_UNIQUE_INDEX = "projects_code_unique"
PROJECT_CODE_COLLATION = {"locale": "en", "strength": 2}
ISSUE_LINK_UNIQUE_INDEX = "issue_links_from_to_type_unique"
ISSUE_LINK_FROM_TICKET_INDEX = "issue_links_from_ticket"
ISSUE_LINK_TO_TICKET_INDEX = "issue_links_to_ticket"
TRANSITION_PROJECT_NAME_INDEX = "transitions_project_name"
TRANSITION_PROJECT_SOURCE_INDEX = "transitions_project_source"
NOTIFICATION_PREFERENCE_UNIQUE_INDEX = "notification_preferences_user_project_unique"
SAVED_FILTER_USER_NAME_INDEX = "saved_filters_user_name_unique"


def ensure_database_indexes() -> None:
    """Create the indexes required for application-level invariants."""
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
