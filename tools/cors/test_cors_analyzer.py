#!/usr/bin/env python3
"""Unit tests for cors_analyzer: one case per vector + tricky edge cases.

Each test asserts the verdict for a specific vector under a specific header
combination, so the detection logic is proven correct BEFORE any live probe.
"""
import unittest
from cors_analyzer import Probe, analyze


def verdict_for(probes, vector_prefix):
    for v in analyze(probes):
        if v.vector.startswith(vector_prefix):
            return v.verdict
    raise AssertionError("vector not found: " + vector_prefix)


class TestCorsAnalyzer(unittest.TestCase):

    # ---- V1: credentialed origin reflection (the true critical) ----
    def test_v1_reflection_with_creds_is_exploitable(self):
        p = [Probe("cred+reflect", "https://evil.example", True, 200,
                   acao="https://evil.example", acac="true")]
        self.assertEqual(verdict_for(p, "V1"), "EXPLOITABLE")

    def test_v1_reflection_without_acac_not_exploitable(self):
        # Origin echoed but ACAC missing -> browser won't expose creds response
        p = [Probe("reflect-no-acac", "https://evil.example", True, 200,
                   acao="https://evil.example", acac=None)]
        self.assertEqual(verdict_for(p, "V1"), "NOT_EXPLOITABLE")

    # ---- V2: wildcard + credentials is a spec dead-end ----
    def test_v2_wildcard_plus_creds_blocked_by_browser(self):
        p = [Probe("wild+creds", "https://evil.example", True, 200,
                   acao="*", acac="true")]
        self.assertEqual(verdict_for(p, "V2"), "NOT_EXPLOITABLE")
        # and it must NOT be counted as a V1 credentialed read
        self.assertEqual(verdict_for(p, "V1"), "NOT_EXPLOITABLE")

    # ---- V3: wildcard exposing credential-less data ----
    def test_v3_wildcard_nonsensitive_needs_authz_data(self):
        p = [Probe("wild-guest", "https://evil.example", False, 200,
                   acao="*", body_is_sensitive=False)]
        self.assertEqual(verdict_for(p, "V3"), "NEEDS_AUTHZ_DATA")

    def test_v3_wildcard_sensitive_is_exploitable(self):
        p = [Probe("wild-pii", "https://evil.example", False, 200,
                   acao="*", body_is_sensitive=True)]
        self.assertEqual(verdict_for(p, "V3"), "EXPLOITABLE")

    def test_v3_unknown_sensitivity_defaults_needs_authz(self):
        # body_is_sensitive=None (unknown) must NOT be auto-flagged exploitable
        p = [Probe("wild-unknown", "https://evil.example", False, 200,
                   acao="*", body_is_sensitive=None)]
        self.assertEqual(verdict_for(p, "V3"), "NEEDS_AUTHZ_DATA")

    # ---- V4: null origin ----
    def test_v4_null_with_creds_exploitable(self):
        p = [Probe("null+creds", "null", True, 200, acao="null", acac="true")]
        self.assertEqual(verdict_for(p, "V4"), "EXPLOITABLE")

    def test_v4_null_without_creds_needs_authz(self):
        p = [Probe("null-nocreds", "null", False, 200, acao="null")]
        self.assertEqual(verdict_for(p, "V4"), "NEEDS_AUTHZ_DATA")

    # ---- V5: cache poisoning ----
    def test_v5_reflection_without_vary_is_poisonable(self):
        p = [Probe("reflect-novary", "https://evil.example", False, 200,
                   acao="https://evil.example", vary=None)]
        self.assertEqual(verdict_for(p, "V5"), "EXPLOITABLE")

    def test_v5_reflection_with_vary_ok(self):
        p = [Probe("reflect-vary", "https://evil.example", False, 200,
                   acao="https://evil.example", vary="Origin")]
        self.assertEqual(verdict_for(p, "V5"), "NOT_EXPLOITABLE")

    # ---- V6: state-changing methods cross-origin ----
    def test_v6_delete_on_readable_endpoint(self):
        p = [Probe("delete", "https://evil.example", False, 200,
                   acao="*", acam="GET, POST, DELETE")]
        self.assertEqual(verdict_for(p, "V6"), "EXPLOITABLE")

    def test_v6_safe_methods_only(self):
        p = [Probe("safe", "https://evil.example", False, 200,
                   acao="*", acam="GET, POST, HEAD")]
        self.assertEqual(verdict_for(p, "V6"), "NOT_EXPLOITABLE")

    # ---- edge: no ACAO at all => nothing exploitable ----
    def test_no_acao_all_safe(self):
        p = [Probe("nocors", "https://evil.example", True, 200, acao=None)]
        for pre in ("V1", "V4", "V6"):
            self.assertEqual(verdict_for(p, pre), "NOT_EXPLOITABLE")

    # ---- edge: case-insensitive header values ----
    def test_case_insensitive_acac(self):
        p = [Probe("mixedcase", "https://EVIL.example", True, 200,
                   acao="https://evil.example", acac="TRUE")]
        # origins differ only by case -> treated equal after normalization
        self.assertEqual(verdict_for(p, "V1"), "EXPLOITABLE")

    # ---- V7: duplicate/malformed ACAO (the real globe.gov shape) ----
    def test_v7_duplicate_acao_neutralizes_v1(self):
        # '*' AND reflected origin + ACAC:true: browser rejects -> V1 not exploitable
        p = [Probe("dup", "https://evil.example", True, 500,
                   acao="https://evil.example",
                   acao_values=["*", "https://evil.example"], acac="true")]
        self.assertEqual(verdict_for(p, "V1"), "NOT_EXPLOITABLE")
        self.assertEqual(verdict_for(p, "V7"), "NEEDS_VERIFICATION")

    def test_v7_single_acao_not_flagged(self):
        p = [Probe("single", "https://evil.example", True, 200,
                   acao="https://evil.example",
                   acao_values=["https://evil.example"], acac="true")]
        # single reflected value with creds -> genuinely exploitable, no V7
        self.assertEqual(verdict_for(p, "V1"), "EXPLOITABLE")
        for v in analyze(p):
            self.assertFalse(v.vector.startswith("V7"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
