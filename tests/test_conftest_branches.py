import conftest
from bson import ObjectId


def test_matches_or_clause():
    doc = {"a": 1, "b": 2}
    assert conftest._matches(doc, {"$or": [{"a": 1}, {"b": 3}]})
    assert not conftest._matches(doc, {"$or": [{"a": 9}, {"b": 3}]})


def test_update_delete_with_objectid():
    fc = conftest.FakeCollection([{"_id": ObjectId(), "a": 1}])
    oid = fc.documents[0]["_id"]
    fc.update_one(oid, {"$set": {"a": 2}})
    assert fc.documents[0]["a"] == 2
    fc.delete_one(oid)
    assert len(fc.documents) == 0


def test_matches_and_clause_returns_false():
    """Cover conftest._matches $and branch when a clause does not match."""
    doc = {"role": "agent", "name": "Ibrahim"}
    query = {"$and": [{"role": "agent"}, {"name": "Ali"}]}
    assert conftest._matches(doc, query) is False


def test_matches_and_clause_returns_true():
    """Cover the $and continue branch when all clauses match."""
    doc = {"role": "agent", "name": "Ibrahim"}
    query = {"$and": [{"role": "agent"}, {"name": "Ibrahim"}]}
    assert conftest._matches(doc, query) is True


def test_find_one_and_update_with_objectid_query():
    """Cover find_one_and_update when query is a bare ObjectId."""
    oid = ObjectId()
    fc = conftest.FakeCollection([{"_id": oid, "val": 1}])
    result = fc.find_one_and_update(oid, {"$set": {"val": 42}})
    assert result["val"] == 42
    assert fc.documents[0]["val"] == 42


def test_find_one_and_update_returns_none_when_no_match():
    """Cover find_one_and_update returning None (no match, no upsert)."""
    fc = conftest.FakeCollection([{"_id": ObjectId(), "val": 1}])
    result = fc.find_one_and_update(
        {"_id": ObjectId()}, {"$set": {"val": 99}}, upsert=False
    )
    assert result is None
