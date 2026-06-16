"""
seismograph.engine
==================
Correlation engine package -- receives privacy-preserving feature vectors
from the ingestion gateway, runs change-point detection, and scores
cross-observer agreement before promoting any drift alert.

Single-org signals are NEVER promoted to public drift alerts.
Cross-observer agreement scoring gates every public alert.

#SG-TRACE: REQ-ENGINE-001
#   | assumption: correlation happens server-side on aggregated hashes only
#   | test: test_engine_no_single_org_promotion
"""
