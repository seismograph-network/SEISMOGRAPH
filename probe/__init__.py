"""
seismograph.probe
=================
Probe SDK package -- instruments LLM/agent API calls, extracts lightweight
behavioral features, and emits privacy-preserving canary signals.

No raw prompts or outputs leave this package boundary.
All data crossing the boundary is hashed, distributional, or DP-noised.

#SG-TRACE: REQ-PROBE-001
#   | assumption: package boundary == privacy perimeter
#   | test: test_probe_no_raw_leak
"""
