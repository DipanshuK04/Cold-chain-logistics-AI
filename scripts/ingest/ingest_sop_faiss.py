import hashlib
import json
from pathlib import Path

import pandas as pd
import pypdf

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


ROOT = Path(__file__).resolve().parents[2]

POLICY_DIR = ROOT / "data" / "policy"
CACHE_DIR = ROOT / "data" / "cache"
CACHE_FILE = CACHE_DIR / "ingestion_hash_cache.json"
INDEX_DIR = ROOT / "data" / "vectorstore" / "sop_faiss"

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80

SUPPORTED = {".md", ".txt", ".pdf", ".csv", ".xlsx"}

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)

header_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[
        ("#", "header_1"),
        ("##", "header_2"),
        ("###", "header_3"),
    ]
)

embeddings = HuggingFaceEmbeddings(
    model_name=MODEL_NAME,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

# FILES 

def file_hash(path: Path) -> str:
    """Fingerprint the file's current contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_id_prefix(relative_path: str) -> str:
    """Stable, filesystem-independent identifier for a source file."""
    return hashlib.sha256(
        relative_path.encode("utf-8")
    ).hexdigest()[:20]

# PARSE AND CHUNK
def parse_document(path: Path) -> list[Document]:
    ext = path.suffix.lower()
    docs = []

    if ext == ".md":
        text = path.read_text(encoding="utf-8")
        docs = header_splitter.split_text(text)

    elif ext == ".txt":
        docs = [
            Document(page_content=path.read_text(encoding="utf-8"))
        ]

    elif ext == ".pdf":
        reader = pypdf.PdfReader(str(path))

        for page_no, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""

            if text.strip():
                docs.append(
                    Document(
                        page_content=text,
                        metadata={"page_number": page_no},
                    )
                )

    elif ext in {".csv", ".xlsx"}:
        df = (
            pd.read_csv(path)
            if ext == ".csv"
            else pd.read_excel(path)
        )

        for row_no, (_, row) in enumerate(df.iterrows(), start=1):
            values = [
                f"{col}: {val}"
                for col, val in row.items()
                if pd.notna(val) and str(val).strip()
            ]

            if values:
                docs.append(
                    Document(
                        page_content=" | ".join(values),
                        metadata={"row_number": row_no},
                    )
                )

    else:
        return []

    chunks = splitter.split_documents(docs)
    result = []

    relative_path = path.relative_to(POLICY_DIR).as_posix()

    for chunk_no, chunk in enumerate(chunks):
        chunk.page_content = chunk.page_content.strip()

        if not chunk.page_content:
            continue

        chunk.metadata.update({
            "source_file": relative_path,
            "file_format": ext.lstrip(".").upper(),
            "document_type": "Compliance Asset",
            "chunk_index": chunk_no,
        })

        result.append(chunk)

    return result


def chunk_ids(relative_path: str, count: int) -> list[str]:
    prefix = file_id_prefix(relative_path)
    return [f"{prefix}-chunk-{i}" for i in range(count)]


# --------------------------------------------------
# 4. Cache helpers
# --------------------------------------------------

def save_cache(data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = CACHE_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(CACHE_FILE)


def index_exists() -> bool:
    return (
        (INDEX_DIR / "index.faiss").exists()
        and (INDEX_DIR / "index.pkl").exists()
    )


# --------------------------------------------------
# 5. Incremental ingestion
# --------------------------------------------------

def main():
    if not POLICY_DIR.exists():
        raise FileNotFoundError(f"Missing folder: {POLICY_DIR}")

    files = {
        path.relative_to(POLICY_DIR).as_posix(): path
        for path in POLICY_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED
    }

    if not files:
        raise ValueError(f"No supported policy files in {POLICY_DIR}")

    config = {
        "model": MODEL_NAME,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "cache_version": 1,
    }

    # Load cache. An incompatible cache requires a rebuild.
    old_cache = None

    if CACHE_FILE.exists() and index_exists():
        old_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))

    can_increment = (
        old_cache is not None
        and old_cache.get("config") == config
    )

    if can_increment:
        store = FAISS.load_local(
            str(INDEX_DIR),
            embeddings,
            allow_dangerous_deserialization=True,
        )
        entries = old_cache["files"]
        print("Loaded existing FAISS index and cache.")

    else:
        print("No compatible cache/index: rebuilding from source files.")
        entries = {}
        store = None

    # Rebuild the entire index when starting or changing config.
    if not can_increment:
        all_docs = []
        new_entries = {}

        for relative_path, path in sorted(files.items()):
            print(f"Processing: {relative_path}")

            chunks = parse_document(path)
            ids = chunk_ids(relative_path, len(chunks))

            new_entries[relative_path] = {
                "sha256": file_hash(path),
                "ids": ids,
            }

            all_docs.extend(chunks)
            # Keep IDs aligned with their corresponding documents.
            # new_entries[relative_path]["ids"] = ids

        if not all_docs:
            raise ValueError("No usable text extracted from policy files.")

        # Reconstruct stable chunk IDs in the same order as the documents.
        all_ids = []
        for relative_path in sorted(files):
            all_ids.extend(new_entries[relative_path]["ids"])

        store = FAISS.from_documents(
            documents=all_docs,
            embedding=embeddings,
            ids=all_ids,
        )

        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        store.save_local(str(INDEX_DIR))

        save_cache({
            "config": config,
            "files": new_entries,
        })

        print(f"Full rebuild complete: {len(all_docs)} chunks.")
        return

    # Remove vectors for files that no longer exist.
    deleted = set(entries) - set(files)

    for relative_path in sorted(deleted):
        old_ids = entries[relative_path].get("ids", [])

        if old_ids:
            store.delete(ids=old_ids)

        del entries[relative_path]
        print(f"Deleted source: {relative_path}")

    # Process only new or modified files.
    for relative_path, path in sorted(files.items()):
        current_hash = file_hash(path)
        previous = entries.get(relative_path, {})

        if previous.get("sha256") == current_hash:
            print(f"Skipped unchanged: {relative_path}")
            continue

        print(f"Processing changed/new file: {relative_path}")

        # Remove the old version before adding its replacement.
        old_ids = previous.get("ids", [])

        if old_ids:
            store.delete(ids=old_ids)

        chunks = parse_document(path)
        ids = chunk_ids(relative_path, len(chunks))

        if chunks:
            store.add_documents(documents=chunks, ids=ids)

        entries[relative_path] = {
            "sha256": current_hash,
            "ids": ids,
        }

        print(f"  Stored {len(chunks)} chunk(s).")

    # Persist the vector index and then its cache.
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    store.save_local(str(INDEX_DIR))

    save_cache({
        "config": config,
        "files": entries,
    })

    print("Incremental ingestion complete.")


if __name__ == "__main__":
    main()
