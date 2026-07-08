from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from bson import ObjectId


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, clause) for clause in expected):
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

    def find_one(self, query):
        if isinstance(query, ObjectId):
            query = {"_id": query}
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
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
