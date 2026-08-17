#!/usr/bin/env python3
"""
cors_analyzer.py - Independent CORS exploitation-vector analyzer.

Re-derives CORS risk from first principles (Fetch spec) rather than trusting
a single "ACAO: *" observation. Given observed response headers for several
probe conditions, it classifies the configuration against EVERY known
cross-origin exploitation vector and returns a per-vector verdict:

    EXPLOITABLE      - a browser-realizable cross-origin read/effect exists
    NOT_EXPLOITABLE  - blocked by spec / config; no browser realization
    NEEDS_AUTHZ_DATA - CORS is permissive but impact depends on whether the
                       credential-less data is actually sensitive (authz layer)
    N/A              - vector not applicable to observed data

Pure/deterministic so it can be unit-tested offline and reused by the live
probe (probe_cors_live.py).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Probe:
    """One observed request/response condition."""
    label: str
    request_origin: Optional[str]          # Origin header we sent (None = omitted)
    sent_credentials: bool                 # did the (hypothetical) browser send cookies?
    status: int
    acao: Optional[str]                    # Access-Control-Allow-Origin (effective single value)
    acao_values: Optional[list] = None     # ALL ACAO header occurrences (duplicate detection)
    acac: Optional[str] = None             # Access-Control-Allow-Credentials
    vary: Optional[str] = None             # Vary
    acam: Optional[str] = None             # Access-Control-Allow-Methods
    acah: Optional[str] = None             # Access-Control-Allow-Headers
    body_is_sensitive: Optional[bool] = None  # is the returned body sensitive/PII?


@dataclass
class VectorVerdict:
    vector: str
    verdict: str
    rationale: str
    evidence: List[str] = field(default_factory=list)


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _acao_is_malformed(p: "Probe") -> bool:
    """Multiple distinct ACAO header values are non-conformant; browsers fail
    the CORS check and expose nothing (Fetch spec: header must be a single value)."""
    vals = p.acao_values
    if not vals:
        return False
    distinct = {_norm(v) for v in vals if _norm(v)}
    return len(distinct) > 1


def _browser_can_read(p: Probe) -> bool:
    """Model the Fetch-spec CORS check: can attacker JS READ this response?

    Rules (per WHATWG Fetch / CORS):
      - No ACAO -> blocked.
      - Credentialed request: ACAO MUST be the exact origin (not '*'),
        AND ACAC must be 'true'. '*' + credentials => browser blocks read.
      - Non-credentialed request: ACAO '*' OR exact-origin match allows read.
      - ACAO must match the request Origin (or be '*') to allow read.
    """
    if _acao_is_malformed(p):
        return False                           # duplicate/multiple ACAO -> browser rejects
    acao = _norm(p.acao)
    if not acao:
        return False
    origin = _norm(p.request_origin)
    if p.sent_credentials:
        if acao == "*":
            return False                       # spec: wildcard + creds is blocked
        if _norm(p.acac) != "true":
            return False                       # creds require ACAC:true
        return acao == origin or (acao == "null" and origin == "null")
    # non-credentialed
    if acao == "*":
        return True
    return acao == origin or (acao == "null" and origin == "null")


def _reflects_arbitrary_origin(probes: List[Probe]) -> bool:
    """True if the server echoes an attacker-controlled Origin back in ACAO."""
    for p in probes:
        o = _norm(p.request_origin)
        if o and o not in ("", "null") and _norm(p.acao) == o:
            return True
    return False


def _allows_null_origin(probes: List[Probe]) -> bool:
    for p in probes:
        if _norm(p.request_origin) == "null" and _norm(p.acao) == "null":
            return True
    return False


def analyze(probes: List[Probe]) -> List[VectorVerdict]:
    verdicts: List[VectorVerdict] = []
    reflects = _reflects_arbitrary_origin(probes)
    allows_null = _allows_null_origin(probes)
    any_wildcard = any(_norm(p.acao) == "*" for p in probes)
    any_acac_true = any(_norm(p.acac) == "true" for p in probes)

    # V1: Credentialed theft via arbitrary-origin reflection + ACAC:true (classic critical)
    cred_read = [p for p in probes if p.sent_credentials and _browser_can_read(p)]
    if cred_read:
        verdicts.append(VectorVerdict(
            "V1 credentialed-origin-reflection",
            "EXPLOITABLE",
            "Server reflects arbitrary Origin with ACAC:true; attacker JS reads "
            "victim's authenticated response cross-origin.",
            [p.label for p in cred_read]))
    else:
        verdicts.append(VectorVerdict(
            "V1 credentialed-origin-reflection",
            "NOT_EXPLOITABLE",
            "No condition allows a credentialed cross-origin READ (needs exact-"
            "origin echo + ACAC:true).",
            []))

    # V2: Wildcard '*' + credentials (spec-impossible theft) -> confirm blocked
    v2_conf = [p for p in probes if _norm(p.acao) == "*" and _norm(p.acac) == "true"]
    if v2_conf:
        verdicts.append(VectorVerdict(
            "V2 wildcard+credentials",
            "NOT_EXPLOITABLE",
            "Misconfig present (ACAO:* AND ACAC:true) but browsers block reading a "
            "wildcard response to a credentialed request; no theft. Still fix it.",
            [p.label for p in v2_conf]))
    else:
        verdicts.append(VectorVerdict(
            "V2 wildcard+credentials",
            "N/A",
            "No response combined ACAO:* with ACAC:true.",
            []))

    # V3: Wildcard '*' exposing credential-LESS data (impact = authz question)
    if any_wildcard and not any_acac_true:
        wildcard_probes = [p for p in probes if _norm(p.acao) == "*"]
        sens = [p for p in wildcard_probes if p.body_is_sensitive]
        if sens:
            verdicts.append(VectorVerdict(
                "V3 wildcard-exposes-guest-data",
                "EXPLOITABLE",
                "ACAO:* lets ANY site's JS read this endpoint; body contains "
                "sensitive/PII data readable without credentials => real leak.",
                [p.label for p in sens]))
        else:
            verdicts.append(VectorVerdict(
                "V3 wildcard-exposes-guest-data",
                "NEEDS_AUTHZ_DATA",
                "ACAO:* makes credential-less data world-readable via JS. Impact "
                "hinges on whether that data is sensitive (authorization layer). "
                "Not a CORS-only critical unless data is sensitive.",
                [p.label for p in wildcard_probes]))
    else:
        verdicts.append(VectorVerdict(
            "V3 wildcard-exposes-guest-data",
            "N/A",
            "No non-credentialed wildcard exposure observed.",
            []))

    # V4: null-origin allowlist (sandbox iframe / file:// abuse)
    if allows_null:
        null_probes = [p for p in probes if _norm(p.request_origin) == "null"]
        null_malformed = any(_acao_is_malformed(p) for p in null_probes)
        null_cred = [p for p in null_probes if p.sent_credentials
                     and _norm(p.acac) == "true" and not _acao_is_malformed(p)]
        if null_malformed and not null_cred:
            v4_verdict, v4_reason = "NEEDS_VERIFICATION", (
                "Server intends to allow Origin: null (with ACAC:true) but emits it "
                "as a duplicate ACAO alongside '*', which compliant browsers reject. "
                "Misconfiguration is real; confirm client behavior before rating.")
        else:
            v4_verdict = "EXPLOITABLE" if null_cred else "NEEDS_AUTHZ_DATA"
            v4_reason = ("Server allows Origin: null. Attackers forge null origin via "
                         "sandboxed iframe/data:/file:. Critical if paired with ACAC:true.")
        verdicts.append(VectorVerdict("V4 null-origin-allowed", v4_verdict, v4_reason,
                                      [p.label for p in null_probes]))
    else:
        verdicts.append(VectorVerdict(
            "V4 null-origin-allowed",
            "NOT_EXPLOITABLE",
            "Origin: null is not reflected/allowed.",
            []))

    # V5: Cache poisoning via reflected ACAO without Vary: Origin
    if reflects:
        has_vary = any("origin" in _norm(p.vary) for p in probes
                       if _norm(p.request_origin) and _norm(p.acao) == _norm(p.request_origin))
        refl_malformed = all(_acao_is_malformed(p) for p in probes
                             if _norm(p.request_origin) and _norm(p.acao) == _norm(p.request_origin))
        v5_verdict = ("NEEDS_VERIFICATION" if (not has_vary and refl_malformed)
                      else ("EXPLOITABLE" if not has_vary else "NOT_EXPLOITABLE"))
        verdicts.append(VectorVerdict(
            "V5 cache-poisoning-missing-vary",
            v5_verdict,
            "Reflected ACAO without 'Vary: Origin' can be cached and served to "
            "other origins (shared/CDN cache poisoning)."
            if not has_vary else
            "Reflected ACAO but 'Vary: Origin' present; cache keys on Origin.",
            [p.label for p in probes if reflects]))
    else:
        verdicts.append(VectorVerdict(
            "V5 cache-poisoning-missing-vary",
            "N/A",
            "No arbitrary-origin reflection; wildcard/static ACAO not origin-"
            "cacheable in the dangerous way.",
            []))

    # V6: State-changing methods exposed cross-origin (CSRF-via-CORS amplification)
    dangerous_methods = {"put", "delete", "patch"}
    v6 = []
    for p in probes:
        methods = {m.strip().lower() for m in (p.acam or "").split(",") if m.strip()}
        if methods & dangerous_methods and _browser_can_read(p):
            v6.append(p.label)
    if v6:
        verdicts.append(VectorVerdict(
            "V6 state-changing-methods",
            "EXPLOITABLE",
            "ACAM advertises PUT/DELETE/PATCH on a CORS-readable endpoint; enables "
            "cross-origin state change/read of the result.",
            v6))
    else:
        verdicts.append(VectorVerdict(
            "V6 state-changing-methods",
            "NOT_EXPLOITABLE",
            "No dangerous methods advertised on a cross-origin-readable response.",
            []))

    # V7: Malformed duplicate ACAO (server sends both '*' and a reflected origin)
    dup = [p for p in probes if _acao_is_malformed(p)]
    if dup:
        verdicts.append(VectorVerdict(
            "V7 duplicate-acao-malformed",
            "NEEDS_VERIFICATION",
            "Endpoint emits MULTIPLE Access-Control-Allow-Origin values (e.g. '*' "
            "AND a reflected origin) plus ACAC:true/ACAM:*/ACAH:*. Spec-compliant "
            "browsers reject this and expose nothing, but the intent to reflect "
            "arbitrary origins with credentials is a real server misconfiguration; "
            "non-conformant clients/proxies or a config change collapsing to a "
            "single reflected value would make it a critical credentialed leak. "
            "Confirm effective browser behavior before rating severity.",
            [p.label for p in dup]))

    return verdicts


def summarize(verdicts: List[VectorVerdict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in verdicts:
        out[v.verdict] = out.get(v.verdict, 0) + 1
    return out


def render(verdicts: List[VectorVerdict]) -> str:
    lines = []
    for v in verdicts:
        lines.append("[%-16s] %s" % (v.verdict, v.vector))
        lines.append("    " + v.rationale)
        if v.evidence:
            lines.append("    evidence: " + ", ".join(v.evidence))
    return "\n".join(lines)


if __name__ == "__main__":
    import json, sys
    data = json.load(sys.stdin)
    probes = [Probe(**d) for d in data]
    verdicts = analyze(probes)
    print(render(verdicts))
    print("\nSUMMARY:", summarize(verdicts))
