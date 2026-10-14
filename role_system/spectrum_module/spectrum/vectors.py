"""可选语义索引；标准库余弦检索，失败不影响关键词记忆。"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
from ...ports import logger
AVAILABLE = True
FILE_FMT = "{db}.vectors_{table}.json"

class VecSidecar:
    def __init__(self, db_path: Path, table: str):
        self.table = table
        self.path = db_path.parent / FILE_FMT.format(db=db_path.name, table=table)
        self.ids = None
        self.vecs = None
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.ids, self.vecs = data["ids"], data["vecs"]
        self._loaded = True

    def save(self):
        if not self._loaded:
            return
        if not self.ids:
            self.path.unlink(missing_ok=True)
            return
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"ids": self.ids, "vecs": self.vecs}), encoding="utf-8")
        os.replace(temporary, self.path)

    def clear(self):
        self.ids, self.vecs = [], []
        self._loaded = True
        self.save()

    def missing(self, rows):
        self.load()
        have = set(self.ids or [])
        return [(i, t) for i, t in rows if i not in have and str(t or "").strip()]

    def add(self, pairs):
        self.load()
        self.ids, self.vecs = self.ids or [], self.vecs or []
        have = set(self.ids)
        count = 0
        for mid, vector in pairs:
            v = [float(x) for x in vector]
            if not v or mid in have or not all(math.isfinite(x) for x in v):
                continue
            if self.vecs and len(v) != len(self.vecs[0]):
                raise ValueError("嵌入维度改变；需重建语义索引")
            self.ids.append(int(mid)); self.vecs.append(v); have.add(mid); count += 1
        self.save()
        return count

    def drop(self, ids):
        self.load()
        kill = set(ids)
        pairs = [(i,v) for i,v in zip(self.ids or [], self.vecs or []) if i not in kill]
        self.ids = [i for i,v in pairs]; self.vecs = [v for i,v in pairs]
        self.save()

    def prune(self, live):
        self.load()
        removed = set(self.ids or []) - live
        self.drop(removed)
        return len(removed)

    def search(self, qvec, k):
        self.load()
        q = [float(x) for x in qvec]
        qnorm = math.sqrt(sum(x*x for x in q))
        if k <= 0 or not qnorm or not self.vecs or len(q) != len(self.vecs[0]):
            return []
        def score(v):
            norm = math.sqrt(sum(x*x for x in v))
            return sum(a*b for a,b in zip(q,v))/(qnorm*norm) if norm else 0.0
        ranked = sorted(zip(self.ids,self.vecs), key=lambda pair: score(pair[1]), reverse=True)
        return [i for i,v in ranked[:k]]

async def embed_query(ctx, text: str):
    if not text:
        return None
    try:
        getter = getattr(ctx, "get_all_embedding_providers", None)
        provs = getter() if callable(getter) else None
        if not provs:
            return None
        v = await provs[0].get_embedding(str(text)[:500])
        return list(v) if v else None
    except Exception:
        logger.debug("[spectrum·向量] query 向量取不到，退回关键词", exc_info=True)
        return None


async def embed_many(ctx, texts: list[str]):
    if not texts:
        return None
    try:
        getter = getattr(ctx, "get_all_embedding_providers", None)
        provs = getter() if callable(getter) else None
        if not provs:
            return None
        return await provs[0].get_embeddings([str(t)[:500] for t in texts])
    except Exception:
        logger.warning("[spectrum·向量] 批量取向量失败，这批跳过", exc_info=True)
        return None


async def backfill(ctx, store, tables=("memories", "person_memories"),
                   batch: int = 32, max_rounds: int = 0) -> int:
    """从待编码文本分批获取嵌入并写回索引，达到轮数上限或无进展时结束。"""
    if not tables:
        return 0
    total = 0
    try:
        for table in tables:
            rounds = 0
            while not max_rounds or rounds < max_rounds:
                rounds += 1
                pend = store.pending_vectors(table, limit=batch)
                if not pend:
                    break
                vecs = await embed_many(ctx, [t for _, t in pend])
                if not vecs or len(vecs) != len(pend):
                    break
                n = store.add_vectors(table, list(zip([i for i, _ in pend], vecs)))
                total += n
                if n <= 0:
                    break
    except Exception:
        logger.exception("[spectrum·向量] 回填出错（只是这一层没铺满，关键词照旧）")
    return total


def status():
    return {"available": True, "backend": "stdlib", "numpy": None}
