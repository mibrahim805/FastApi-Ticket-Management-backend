import sys
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from bson import ObjectId


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
                elif "$inc" in update:
                    for key, value in update["$inc"].items():
                        document[key] = document.get(key, 0) + value
                self.updated_documents.append((query, update))
                return deepcopy(document)

        if upsert:
            new_doc = deepcopy(query)
            new_doc.setdefault("_id", ObjectId())
            if "$set" in update:
                new_doc.update(update["$set"])
            elif "$inc" in update:
                for key, value in update["$inc"].items():
                    new_doc[key] = new_doc.get(key, 0) + value
            self.documents.append(new_doc)
            return deepcopy(new_doc)

        return None


@pytest.fixture
def fake_collection():
    return FakeCollection()


@pytest.fixture
def board_members(fake_collection):
    return fake_collection.__class__()


@pytest.fixture
def grant_board_role(board_members):
    def grant(board_id, user_id, role="lead"):
        board_object_id = (
            board_id if isinstance(board_id, ObjectId) else ObjectId(board_id)
        )
        user_object_id = user_id if isinstance(user_id, ObjectId) else ObjectId(user_id)
        board_members.insert_one(
            {
                "board_id": board_object_id,
                "user_id": user_object_id,
                "role": role,
            }
        )

    return grant


@pytest.fixture(autouse=True)
def isolate_board_members_collection(monkeypatch, board_members):
    for module_name in (
        "app.db.db",
        "app.api.activity",
        "app.api.board",
        "app.api.comment",
        "app.api.ticket",
        "uploads.ticket_attachment",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "board_members_collection"):
            monkeypatch.setattr(module, "board_members_collection", board_members)

    return board_members


def test_matches_returns_false_when_no_or_clause_matches():
    document = {
        "role": "agent",
        "name": "Ibrahim",
    }

    query = {
        "$or": [
            {"role": "admin"},
            {"name": "Ali"},
        ]
    }

    assert _matches(document, query) is False


def test_matches_returns_true_when_one_or_clause_matches():
    document = {
        "role": "agent",
        "name": "Ibrahim",
    }

    query = {
        "$or": [
            {"role": "admin"},
            {"role": "agent"},
        ]
    }

    assert _matches(document, query) is True
