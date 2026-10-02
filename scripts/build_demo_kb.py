"""作品说明：从有版本和身份标注的原创合成报告构建隔离演示知识库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _chunks(text: str, limit: int = 900) -> List[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and len(candidate) > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def load_demo_documents(source_dir: Path) -> List[Tuple[Path, str, List[str]]]:
    manifest = ROOT_DIR / "demo" / "sources.json"
    if source_dir.resolve() == (ROOT_DIR / "demo" / "knowledge").resolve() and manifest.exists():
        files = [ROOT_DIR / item["source_path"] for item in json.loads(manifest.read_text(encoding="utf-8"))["documents"]]
    else:
        files = sorted(source_dir.glob("*.md"))
    if not files:
        raise ValueError(f"未找到演示 Markdown：{source_dir}")
    if len(files) > 100:
        raise ValueError("演示知识库最多允许 100 份文档")

    documents: List[Tuple[Path, str, List[str]]] = []
    for path in files:
        text = path.read_text(encoding="utf-8").strip()
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        chunks = [text]  # 作品说明：每个样例文档对应一页有标注的原始来源。
        if not chunks:
            raise ValueError(f"演示文档为空：{path}")
        documents.append((path, title, chunks))
    return documents


def build_demo_kb(source_dir: Path, output_dir: Path, reset: bool = True) -> Dict[str, object]:
    if not source_dir.is_absolute():
        source_dir = ROOT_DIR / source_dir
    if not output_dir.is_absolute():
        output_dir = ROOT_DIR / output_dir
    output_dir = output_dir.resolve()
    if output_dir != (ROOT_DIR / "data" / "demo_chroma_db").resolve():
        raise ValueError("公开演示仅允许重建 data/demo_chroma_db，防止覆盖既有知识库")
    os.environ["CHROMA_DB_PATH"] = str(output_dir)
    os.environ.setdefault("EMBEDDING_PROVIDER", "bge_local")
    os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    import chromadb

    from src.agent.providers import build_collection_metadata, embedding_fingerprint, resolve_embedding_function

    source_documents = load_demo_documents(source_dir)
    manifest = json.loads((ROOT_DIR / "demo" / "sources.json").read_text(encoding="utf-8"))
    provenance = {str((ROOT_DIR / item["source_path"]).resolve()): item for item in manifest["documents"]}
    for path, _, _ in source_documents:
        item = provenance.get(str(path.resolve()))
        if not item or hashlib.sha256(path.read_bytes()).hexdigest() != item["source_sha256"]:
            raise ValueError(f"文档未登记或内容哈希不匹配：{path.name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(output_dir))
    collection_name = "financial_reports"
    if reset:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    embedding = resolve_embedding_function(purpose="demo_build")
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding,
        metadata=build_collection_metadata(embedding, {"hnsw:space": "cosine", "dataset": "finsight-demo"}),
    )

    ids: List[str] = []
    texts: List[str] = []
    metadatas: List[Dict[str, object]] = []
    for path, title, chunks in source_documents:
        source = str(path.resolve()).replace("\\", "/")
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        identity = provenance[str(path.resolve())]
        for index, chunk in enumerate(chunks):
            ids.append(identity["chunk_id"])
            texts.append(chunk)
            metadatas.append({
                "source": source,
                "source_json": "",
                "source_title": title,
                "source_fingerprint": fingerprint,
                "source_chunk_count": len(chunks),
                "doc_name": path.stem,
                "doc_category": "financial",
                "type": "financial_report_demo",
                "report_kind": "annual",
                "report_period": identity["report_period"],
                "report_year": identity["report_year"],
                "stock_code": identity["stock_code"],
                "stock_abbr": identity["stock_abbr"],
                "document_id": identity["document_id"],
                "chunk_id": identity["chunk_id"],
                "document_version": fingerprint,
                "license": "Apache-2.0",
                "synthetic": True,
                "chunk_index": index,
                "page_start": 1,
                "section_title": title,
                "title_path": title,
            })

    collection.upsert(ids=ids, documents=texts, metadatas=metadatas)
    result: Dict[str, object] = {
        "path": str(output_dir),
        "collection": collection_name,
        "documents": len(source_documents),
        "chunks": collection.count(),
        "embedding_fingerprint": embedding_fingerprint(embedding),
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 FinSight 最小演示知识库")
    parser.add_argument("--source", type=Path, default=ROOT_DIR / "demo" / "knowledge")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "data" / "demo_chroma_db")
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()
    build_demo_kb(args.source, args.output, reset=not args.no_reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
