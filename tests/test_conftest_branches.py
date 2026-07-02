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
