"""
seismograph.engine.audit
=========================
SOC 2 audit-grade incident export for SEISMOGRAPH.

Provides AuditReportGenerator, which constructs a deterministically
checksummed JSON report for any recorded drift alert.  The report
contains:

  - export_timestamp   : naive UTC ISO-8601 string of generation time
  - alert_details      : full alert record as a plain dict
  - baseline_evidence  : up to 50 telemetry signals preceding the alert
  - report_checksum    : SHA-256 hex digest of canonical JSON (sorted keys)
                         over the above three fields (before checksum is
                         appended).  Consumers can verify integrity by
                         re-serialising with json.dumps(..., sort_keys=True)
                         and recomputing sha256.

Alert resolution order
----------------------
The report generator first checks local_drift_alerts (private fleet).
If no match is found it checks public_drift_alerts (quorum-verified).
If neither table has a row for alert_id, AlertNotFoundError is raised
and the gateway returns HTTP 404.

Baseline evidence window
------------------------
Up to 50 TelemetrySignal rows with timestamp < alert.timestamp are
fetched via BaseRepository.get_signals_before_timestamp().  For a
LocalDriftAlert with a fleet_id, the query is additionally filtered to
that fleet so the evidence reflects only the reporting fleet's traffic.

Privacy invariant
-----------------
No raw prompt text or model output is stored in TelemetrySignal (Aegis
invariant).  This module never requests, computes, or transmits raw
text; it only serialises the distributional metrics already stored in
the repository.  Verify on every PR that no raw_output or raw_prompt
key appears in baseline_evidence entries.

#SG-TRACE: REQ-AUDIT-000
#   | assumption: AuditReportGenerator is stateless beyond the repo
#     reference; thread-safe for concurrent export requests at Phase 3
#     load.
#   | test: test_audit_export_local_alert, test_audit_export_public_alert
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from engine.repository import BaseRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AlertNotFoundError(Exception):
    """Raised when alert_id does not match any row in either alert table.

    The gateway maps this to HTTP 404.

    Parameters
    ----------
    alert_id:
        The id that was not found.
    """

    def __init__(self, alert_id: int) -> None:
        msg = f"alert_id={alert_id} not found in local or public alerts"
        super().__init__(msg)
        self.alert_id = alert_id


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------


class AuditReportGenerator:
    """Construct a SHA-256-checksummed SOC 2 audit report for a drift alert.

    Parameters
    ----------
    repo:
        Any BaseRepository implementation.  Requires the three audit
        methods added in P3-004:
          - get_local_alert_by_id
          - get_public_alert_by_id
          - get_signals_before_timestamp

    #SG-TRACE: REQ-AUDIT-000
    #   | assumption: repo is fully initialised; no lazy connection
    #     deferral; safe to call generate() immediately after __init__
    #   | test: test_audit_export_local_alert
    """

    def __init__(self, repo: BaseRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, alert_id: int) -> dict:
        """Build and return the complete audit report dict for alert_id.

        Resolution order: LocalDriftAlert → PublicDriftAlert → raise.

        The returned dict contains:
          export_timestamp, alert_details, baseline_evidence,
          report_checksum.

        report_checksum is the SHA-256 hex digest of
        json.dumps({export_timestamp, alert_details, baseline_evidence},
                   sort_keys=True).

        Parameters
        ----------
        alert_id:
            Integer primary key to look up.

        Returns
        -------
        dict
            Complete audit report with checksum appended.

        Raises
        ------
        AlertNotFoundError
            When alert_id is absent from both alert tables.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: export_timestamp is naive UTC; consistent with
        #     how all other timestamps are stored throughout the system
        #   | test: test_audit_export_local_alert,
        #           test_audit_export_public_alert
        """
        local = self._repo.get_local_alert_by_id(alert_id)
        if local is not None:
            alert_details = {
                "id": local.id,
                "type": "local",
                "timestamp": local.timestamp.isoformat(),
                "model_tuple": local.model_tuple,
                "metric_name": local.metric_name,
                "alert_value": local.alert_value,
                "client_id": local.client_id,
                "fleet_id": local.fleet_id,
            }
            model_tuple: str = local.model_tuple
            fleet_id: str | None = local.fleet_id
            alert_ts: datetime = local.timestamp
            logger.debug(
                "audit.generate | resolved local alert id=%d model=%s",
                alert_id,
                model_tuple,
            )
        else:
            public = self._repo.get_public_alert_by_id(alert_id)
            if public is None:
                raise AlertNotFoundError(alert_id)
            alert_details = {
                "id": public.id,
                "type": "public",
                "timestamp": public.timestamp.isoformat(),
                "model_tuple": public.model_tuple,
                "metric_name": public.metric_name,
                "contributing_org_count": public.contributing_org_count,
            }
            model_tuple = public.model_tuple
            fleet_id = None
            alert_ts = public.timestamp
            logger.debug(
                "audit.generate | resolved public alert id=%d model=%s",
                alert_id,
                model_tuple,
            )

        signals = self._repo.get_signals_before_timestamp(
            model_tuple=model_tuple,
            timestamp=alert_ts,
            fleet_id=fleet_id,
            limit=50,
        )
        baseline_evidence: list[dict] = [
            {
                "batch_id": sig.batch_id,
                "timestamp": sig.timestamp.isoformat(),
                "model_tuple": sig.model_tuple,
                "avg_output_length": sig.avg_output_length,
                "json_success_rate": sig.json_success_rate,
                "result_count": sig.result_count,
            }
            for sig in signals
        ]
        logger.debug(
            "audit.generate | baseline_evidence count=%d for alert_id=%d",
            len(baseline_evidence),
            alert_id,
        )

        export_timestamp: str = (
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        )
        body: dict = {
            "export_timestamp": export_timestamp,
            "alert_details": alert_details,
            "baseline_evidence": baseline_evidence,
        }

        canonical: str = json.dumps(body, sort_keys=True, default=str)
        checksum: str = hashlib.sha256(canonical.encode()).hexdigest()

        body["report_checksum"] = checksum
        logger.info(
            "audit.generate | alert_id=%d checksum=%s...",
            alert_id,
            checksum[:12],
        )
        return body
