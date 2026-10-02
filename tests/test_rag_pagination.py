from src.agent.rag_tool import RAGTool


class LargeFakeCollection:
    def __init__(self):
        self.calls = []

    def count(self):
        return 10_000

    def get(self, **kwargs):
        self.calls.append(kwargs)
        assert "limit" in kwargs, "大集合不应先发起无界 get"
        offset = int(kwargs.get("offset") or 0)
        if offset >= 4:
            return {"documents": [], "metadatas": []}
        return {
            "documents": [f"doc-{offset}", f"doc-{offset + 1}"],
            "metadatas": [{"index": offset}, {"index": offset + 1}],
        }


def test_large_collection_uses_pagination_without_unbounded_probe():
    collection = LargeFakeCollection()

    payload = RAGTool()._safe_collection_get(
        collection,
        where_clause={"doc_category": "research"},
        include=["documents", "metadatas"],
        batch_size=2,
    )

    assert payload["documents"] == ["doc-0", "doc-1", "doc-2", "doc-3"]
    assert [call["offset"] for call in collection.calls] == [0, 2, 4]
    assert all(call["where"] == {"doc_category": "research"} for call in collection.calls)
