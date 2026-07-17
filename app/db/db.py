from pymongo import ASCENDING, MongoClient

client = MongoClient("mongodb://localhost:27017")


db = client["mydatabase"]


user_collection = db["users"]
projects_collection = db["projects"]
ticket_collection = db["tickets"]
comments_collection = db["comments"]
notifications_collection = db["notifications"]
reset_tokens_collection = db["reset_tokens"]
statuses_collection = db["statuses"]
attachment_collection = db["attachments"]
ticket_activity_collection = db["ticket_activity"]
counters_collection = db["counters"]
project_members_collection = db["project_members"]
issue_links_collection = db["issue_links"]


PROJECT_CODE_UNIQUE_INDEX = "projects_code_unique"
PROJECT_CODE_COLLATION = {"locale": "en", "strength": 2}
ISSUE_LINK_UNIQUE_INDEX = "issue_links_from_to_type_unique"
ISSUE_LINK_FROM_TICKET_INDEX = "issue_links_from_ticket"
ISSUE_LINK_TO_TICKET_INDEX = "issue_links_to_ticket"


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
