import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from bson import ObjectId

os.environ.setdefault(
    "SECRET_KEY",
    "test-only-secret-key-that-is-longer-than-thirty-two-characters",
)
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(document, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, clause) for clause in expected):
                return False
            continue
        if isinstance(expected, dict) and "$regex" in expected:
            value = document.get(key)
            if value is None or not expected["$regex"].search(str(value)):
                return False
            continue
        if isinstance(expected, dict) and "$in" in expected:
            if document.get(key) not in expected["$in"]:
                return False
            continue
        if isinstance(expected, dict) and "$all" in expected:
            value = document.get(key)
            if not isinstance(value, list) or not all(
                item in value for item in expected["$all"]
            ):
                return False
            continue
        if document.get(key) != expected:
            return False
    return True


@dataclass
class _InsertOneResult:
    inserted_id: ObjectId


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]):
        self._documents = documents
        self._sort_field = None
        self._sort_order = 1

    def sort(self, field, order=1):
        self._sort_field = field
        self._sort_order = order
        if self._sort_field:
            self._documents = sorted(
                self._documents,
                key=lambda x: x.get(self._sort_field, ""),
                reverse=(order == -1),
            )
        return self

    def skip(self, value):
        self._documents = self._documents[value:]
        return self

    def limit(self, value):
        self._documents = self._documents[:value]
        return self

    def __iter__(self):
        return iter(self._documents)


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]] | None = None):
        self.documents = [deepcopy(doc) for doc in documents or []]
        self.inserted_documents: list[dict[str, Any]] = []
        self.updated_documents: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.deleted_queries: list[dict[str, Any]] = []

    def find_one(self, query, projection=None):
        if isinstance(query, ObjectId):
            query = {"_id": query}
        for document in self.documents:
            if _matches(document, query):
                result = deepcopy(document)
                if projection:
                    included_fields = {
                        key for key, included in projection.items() if included
                    }
                    included_fields.add("_id")
                    result = {
                        key: value
                        for key, value in result.items()
                        if key in included_fields
                    }
                return result
        return None

    def insert_one(self, document):
        stored = deepcopy(document)
        stored.setdefault("_id", ObjectId())
        self.documents.append(stored)
        self.inserted_documents.append(stored)
        return _InsertOneResult(stored["_id"])

    def update_one(self, query, update):
        if isinstance(query, ObjectId):
            query = {"_id": query}
        for document in self.documents:
            if _matches(document, query):
                if "$set" in update:
                    document.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        document.pop(key, None)
                if "$addToSet" in update:
                    for key, value in update["$addToSet"].items():
                        values = (
                            value.get("$each", [])
                            if isinstance(value, dict)
                            else [value]
                        )
                        target = document.setdefault(key, [])
                        for item in values:
                            if item not in target:
                                target.append(item)
                if "$pull" in update:
                    for key, value in update["$pull"].items():
                        document[key] = [
                            item for item in document.get(key, []) if item != value
                        ]
                self.updated_documents.append((query, update))
                return

    def delete_one(self, query):
        if isinstance(query, ObjectId):
            query = {"_id": query}
        self.deleted_queries.append(query)
        self.documents = [doc for doc in self.documents if not _matches(doc, query)]

    def delete_many(self, query):
        self.deleted_queries.append(query)
        self.documents = [doc for doc in self.documents if not _matches(doc, query)]

    def count_documents(self, query):
        return sum(1 for document in self.documents if _matches(document, query))

    def aggregate(self, pipeline):
        return []

    def find(self, query=None):
        query = query or {}
        return FakeCursor([doc for doc in self.documents if _matches(doc, query)])

    def find_one_and_update(self, query, update, upsert=False, return_document=True):
        if isinstance(query, ObjectId):
            query = {"_id": query}
        for document in self.documents:
            if _matches(document, query):
                if "$set" in update:
                    document.update(update["$set"])
                if "$inc" in update:
                    for key, value in update["$inc"].items():
                        document[key] = document.get(key, 0) + value
                self.updated_documents.append((query, update))
                return deepcopy(document)

        if upsert:
            new_doc = deepcopy(query)
            new_doc.setdefault("_id", ObjectId())
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            if "$inc" in update:
                for key, value in update["$inc"].items():
                    new_doc[key] = new_doc.get(key, 0) + value
            self.documents.append(new_doc)
            return deepcopy(new_doc)

        return None


@pytest.fixture
def fake_collection():
    return FakeCollection()


@pytest.fixture
def project_members(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def issue_links(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def transitions(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def sprints(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def ticket_activities(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def notification_preferences(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def saved_filters(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def rate_limits(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def grant_project_role(project_members):
    def grant(project_id, user_id, role="lead"):
        project_object_id = (
            project_id if isinstance(project_id, ObjectId) else ObjectId(project_id)
        )
        user_object_id = user_id if isinstance(user_id, ObjectId) else ObjectId(user_id)
        project_members.insert_one(
            {
                "project_id": project_object_id,
                "user_id": user_object_id,
                "role": role,
            }
        )

    return grant


@pytest.fixture(autouse=True)
def isolate_project_members_collection(monkeypatch, project_members):
    for module_name in (
        "app.db.db",
        "app.api.activity",
        "app.api.project",
        "app.api.comment",
        "app.api.notifications",
        "app.api.reports",
        "app.api.sprint",
        "app.api.ticket",
        "app.api.ticket_attachment",
        "uploads.ticket_attachment",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "project_members_collection"):
            monkeypatch.setattr(module, "project_members_collection", project_members)

    return project_members


@pytest.fixture(autouse=True)
def isolate_notification_preferences_collection(
    monkeypatch,
    notification_preferences,
):
    for module_name in (
        "app.api.comment",
        "app.api.notifications",
        "app.api.ticket",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(
            module,
            "notification_preferences_collection",
        ):
            monkeypatch.setattr(
                module,
                "notification_preferences_collection",
                notification_preferences,
            )

    return notification_preferences


@pytest.fixture(autouse=True)
def isolate_saved_filters_collection(monkeypatch, saved_filters):
    module = sys.modules.get("app.api.filters")
    if module is not None:
        monkeypatch.setattr(module, "saved_filters_collection", saved_filters)

    return saved_filters


@pytest.fixture(autouse=True)
def isolate_rate_limits_collection(monkeypatch, rate_limits):
    module = sys.modules.get("app.api.rate_limit")
    if module is not None:
        monkeypatch.setattr(module, "rate_limits_collection", rate_limits)

    return rate_limits


@pytest.fixture(autouse=True)
def isolate_issue_links_collection(monkeypatch, issue_links):
    for module_name in (
        "app.db.db",
        "app.api.ticket",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "issue_links_collection"):
            monkeypatch.setattr(module, "issue_links_collection", issue_links)

    return issue_links


@pytest.fixture(autouse=True)
def isolate_transitions_collection(monkeypatch, transitions):
    for module_name in (
        "app.db.db",
        "app.api.project",
        "app.api.ticket",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "transitions_collection"):
            monkeypatch.setattr(module, "transitions_collection", transitions)

    return transitions


@pytest.fixture(autouse=True)
def isolate_sprints_collection(monkeypatch, sprints):
    for module_name in (
        "app.db.db",
        "app.api.project",
        "app.api.reports",
        "app.api.sprint",
        "app.api.ticket",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "sprints_collection"):
            monkeypatch.setattr(module, "sprints_collection", sprints)

    return sprints


@pytest.fixture(autouse=True)
def isolate_ticket_activity_collection(monkeypatch, ticket_activities):
    module = sys.modules.get("app.api.activity")
    if module is not None:
        monkeypatch.setattr(
            module,
            "ticket_activity_collection",
            ticket_activities,
        )

    return ticket_activities
