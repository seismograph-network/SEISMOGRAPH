"""
seismograph.gateway
===================
Ingestion gateway package -- validates, authenticates, and routes incoming
probe batches from the federated SEISMOGRAPH network.

Security contract:
  - Every batch MUST carry a valid Ed25519 probe signature.
  - Unsigned or malformed batches are REJECTED with a logged error;
    no partial ingestion occurs.
  - Raw prompt/output data is rejected at this boundary.

#SG-TRACE: REQ-GW-001
#   | assumption: Ed25519 signature verification is the sole batch
#     authentication mechanism in Phase 0
#   | test: test_gateway_rejects_unsigned_batch
"""
