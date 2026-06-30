"""
identity.py — cross-source candidate grouping/dedup (PROJECT_CONTEXT.md §10).

Deterministic, explainable grouping rather than fuzzy/ML matching, per the design's
explicit scope cut: email match, then phone match, then a guarded name-only fallback
that never merges two records when the name match is ambiguous (i.e. would silently
conflate two different people). See test scenario: two "Bob Lee" CSV rows with
different emails/phones must NOT be merged.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from transformer.models import PartialRecord


def _email_key(email: str | None) -> str | None:
    if not email:
        return None
    return email.strip().lower() or None


def _phone_key(phone: str | None) -> str | None:
    """Loose digits-only key for *grouping* purposes (not E.164 normalization,
    which happens later in merge.py via normalize.py). Uses the last 10 digits so
    "+1 415 555 0142" and "415-555-0142" key the same without needing a region."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return None
    return digits[-10:]


def _name_key(name: str | None) -> str | None:
    if not name:
        return None
    collapsed = re.sub(r"\s+", " ", name.strip().lower())
    return collapsed or None


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[rj] = ri


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "candidate"


def _make_candidate_id(records: list[PartialRecord]) -> str:
    name = next((r.full_name for r in records if r.full_name), None) or "unknown"
    slug = _slugify(name)
    source_ids = sorted(r.source_id for r in records)
    digest = hashlib.sha1("|".join(source_ids).encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"


def group_records(records: list[PartialRecord]) -> dict[str, list[PartialRecord]]:
    """Group PartialRecords across all sources into per-candidate clusters.

    Pass 1/2: union records sharing a normalized email or phone key -- the strong,
    unambiguous signals.
    Pass 3: for clusters with NO email/phone at all (so nothing strong to match on),
    attach to an existing identity-bearing cluster by name ONLY if exactly one such
    cluster has that name -- if the name is ambiguous (zero or multiple matching
    identity-bearing clusters), leave it as its own cluster rather than guess.
    """
    n = len(records)
    if n == 0:
        return {}

    uf = _UnionFind(n)
    email_to_idx: dict[str, int] = {}
    phone_to_idx: dict[str, int] = {}

    for i, r in enumerate(records):
        for e in r.emails:
            k = _email_key(e)
            if k:
                if k in email_to_idx:
                    uf.union(i, email_to_idx[k])
                else:
                    email_to_idx[k] = i
        for p in r.phones:
            k = _phone_key(p)
            if k:
                if k in phone_to_idx:
                    uf.union(i, phone_to_idx[k])
                else:
                    phone_to_idx[k] = i

    def _clusters() -> dict[int, list[int]]:
        c: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            c[uf.find(i)].append(i)
        return c

    clusters = _clusters()

    # Name-only fallback for identity-less clusters (§10 step 3).
    name_to_identity_roots: dict[str, set[int]] = defaultdict(set)
    for root, idxs in clusters.items():
        has_identity = any(records[i].emails or records[i].phones for i in idxs)
        if not has_identity:
            continue
        for i in idxs:
            nk = _name_key(records[i].full_name)
            if nk:
                name_to_identity_roots[nk].add(root)

    for root, idxs in list(clusters.items()):
        has_identity = any(records[i].emails or records[i].phones for i in idxs)
        if has_identity:
            continue
        for i in idxs:
            nk = _name_key(records[i].full_name)
            candidate_roots = name_to_identity_roots.get(nk, set()) if nk else set()
            if len(candidate_roots) == 1:
                uf.union(i, next(iter(candidate_roots)))

    final_clusters = _clusters()
    result: dict[str, list[PartialRecord]] = {}
    for idxs in final_clusters.values():
        group = [records[i] for i in idxs]
        candidate_id = _make_candidate_id(group)
        result[candidate_id] = group
    return result