"""Retrieval-Augmented Generation (RAG) over resolved incidents.

Pipeline:  resolved incident -> document text -> embedding vector -> stored in `incident_embeddings`
           new incident      -> query text    -> embedding vector -> cosine similarity -> top-k past incidents
           top-k (with similarity score + the remediation that actually worked) are injected into the LLM prompt.

Embedding model ("hash-ngram-v1"): domain-synonym normalisation + hashed word 1-2-grams + hashed char 3-5-grams,
L2-normalised. It is deterministic, needs no model download, no GPU and no PostgreSQL extension, and it matches
paraphrases ("out of memory" ~ "memory leak", "busy loop" ~ "cpu spin") through the synonym map and sub-word grams.
It is lexical-semantic, NOT a neural embedding: to upgrade, replace `embed()` with a sentence-transformer or an
embedding API and (optionally) move the column to pgvector - nothing else changes.
"""
import logging
import re

import numpy as np
from psycopg2.extras import Json
from sklearn.feature_extraction.text import HashingVectorizer

from database.db import fetch_all, fetch_one, execute

log = logging.getLogger("aiopsguard.rag")

EMBED_MODEL = "hash-ngram-v1"
DIM_WORD, DIM_CHAR = 512, 512
MIN_SIMILARITY = 0.18
CATEGORY_BOOST = 0.12

_word = HashingVectorizer(n_features=DIM_WORD, analyzer="word", ngram_range=(1, 2), alternate_sign=False,
                          norm="l2", stop_words="english")
_char = HashingVectorizer(n_features=DIM_CHAR, analyzer="char_wb", ngram_range=(3, 5), alternate_sign=False, norm="l2")

# canonical-concept map: different wordings of the same operational concept collapse to one token
SYNONYMS = [
    (r"out of memory|\boom\b|memory leak\w*|leak(?:ed|ing)? memory|ram exhaust\w*|memory exhaust\w*|memory pressure|"
     r"cannot allocate|memoryerror|swap(?:ping)? heav\w*", " conceptmemory "),
    (r"cpu saturat\w*|cpu spike|cpu spin\w*|busy[- ]loop|tight loop|runaway|infinite loop|high cpu|"
     r"processor (?:load|usage)|all cores", " conceptcpu "),
    (r"timed? ?out|timeouts?|deadline exceeded", " concepttimeout "),
    (r"connection (?:refused|reset|lost|pool)\w*|pool exhaust\w*|too many connections", " conceptconnection "),
    (r"no space left|disk full|disk usage|low disk|out of disk", " conceptdisk "),
    (r"deadlock\w*|lock contention|lock wait", " conceptlock "),
    (r"segfault|segmentation fault|core dumped|crash\w*", " conceptcrash "),
    (r"network (?:saturat|spike|traffic)\w*|bandwidth|packet loss|throughput", " conceptnetwork "),
    (r"disk i/?o|iops|read/write (?:spike|storm)|io wait|iowait", " conceptdiskio "),
]
_SYN = [(re.compile(p, re.I), r) for p, r in SYNONYMS]


def normalise(text):
    text = str(text or "").lower()
    for rx, rep in _SYN:
        text = rx.sub(rep, text)
    return re.sub(r"[^a-z0-9_ ]+", " ", text)


def embed(text):
    """Return an L2-normalised float32 vector of length DIM_WORD + DIM_CHAR."""
    t = normalise(text)
    w = _word.transform([t]).toarray()[0]
    c = _char.transform([t]).toarray()[0]
    v = np.concatenate([0.65 * w, 0.35 * c]).astype(np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def cosine(a, b):
    return float(np.dot(a, b))   # both already unit length


# ---------- document / query text ----------
def doc_text(incident, reasons=(), processes=()):
    """Text that represents an incident for indexing OR querying (symmetry matters for good retrieval)."""
    parts = [incident.get("category") or "", incident.get("title") or "", incident.get("root_cause") or "",
             incident.get("explanation") or "", " ".join(reasons), " ".join(p for p in processes)]
    return " . ".join(x for x in parts if x)


def query_text(evidence, category):
    procs = [f"{p['name']}" for p in evidence.get("processes", [])[:3]]
    logs = [l["message"] for l in evidence.get("logs", []) if l.get("level") in ("ERROR", "CRITICAL")][:3]
    return doc_text({"category": category, "title": evidence["incident"]["title"]},
                    reasons=evidence.get("reasons", []) + logs, processes=procs)


# ---------- indexing ----------
def index_incident(incident_id):
    """(Re)build the embedding for one incident. Safe to call repeatedly; never raises to the caller."""
    try:
        inc = fetch_one("SELECT incident_id, category, title, root_cause, explanation, trigger_metric_id "
                        "FROM incidents WHERE incident_id = %s", (incident_id,))
        if not inc:
            return False
        reasons = []
        for r in fetch_all("SELECT reasons FROM predictions WHERE incident_id = %s ORDER BY created_at DESC LIMIT 10",
                           (incident_id,)):
            reasons += [x for x in r["reasons"] if x not in reasons]
        procs = []
        if inc["trigger_metric_id"]:
            procs = [p["name"] for p in fetch_all(
                "SELECT name FROM processes WHERE metric_id = %s ORDER BY cpu_percent DESC LIMIT 3",
                (inc["trigger_metric_id"],))]
        text = doc_text(inc, reasons, procs)
        vec = embed(text).tolist()
        execute("""INSERT INTO incident_embeddings (incident_id, doc_text, embedding, model)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (incident_id) DO UPDATE SET doc_text = EXCLUDED.doc_text,
                        embedding = EXCLUDED.embedding, model = EXCLUDED.model, created_at = now()""",
                (incident_id, text, vec, EMBED_MODEL))
        return True
    except Exception:
        log.exception("could not index incident %s", incident_id)
        return False


def reindex_missing(limit=500):
    """Backfill embeddings for resolved incidents that have none (e.g. seeded or pre-RAG history)."""
    rows = fetch_all("""SELECT i.incident_id FROM incidents i LEFT JOIN incident_embeddings e USING (incident_id)
                        WHERE i.status IN ('resolved', 'closed') AND i.root_cause IS NOT NULL
                          AND (e.incident_id IS NULL OR e.model <> %s) LIMIT %s""", (EMBED_MODEL, limit))
    return sum(1 for r in rows if index_incident(r["incident_id"]))


# ---------- retrieval ----------
def search(query, exclude_id=None, category=None, limit=3, min_similarity=MIN_SIMILARITY):
    """Top-k resolved incidents by cosine similarity (+ small boost for the same category)."""
    reindex_missing(50)
    rows = fetch_all(
        """SELECT e.incident_id, e.embedding, i.title, i.category, i.root_cause, i.recommendation, i.detected_at,
                  i.resolved_at,
                  (SELECT string_agg(a.target_name, ', ') FROM incident_actions a
                    WHERE a.incident_id = i.incident_id AND a.status = 'executed') AS worked_action
           FROM incident_embeddings e JOIN incidents i USING (incident_id)
           WHERE i.status IN ('resolved', 'closed') AND e.model = %s
             AND (%s::int IS NULL OR i.incident_id <> %s::int)
           ORDER BY i.detected_at DESC LIMIT 500""", (EMBED_MODEL, exclude_id, exclude_id))
    if not rows:
        return []
    q = embed(query)
    scored = []
    for r in rows:
        sim = cosine(q, np.asarray(r["embedding"], dtype=np.float32))
        if category and r["category"] == category:
            sim = min(1.0, sim + CATEGORY_BOOST)
        if sim >= min_similarity:
            scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for sim, r in scored[:limit]:
        out.append({"incident_id": r["incident_id"], "similarity": round(sim, 3), "title": r["title"],
                    "category": r["category"], "root_cause": r["root_cause"],
                    "recommendation": r["recommendation"], "detected_at": r["detected_at"],
                    "remediation_that_worked": (f"Terminated {r['worked_action']}" if r["worked_action"] else None)})
    return out
