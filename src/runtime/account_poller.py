"""
Account State Poller
Executes scheduled reconciliation cycles (~1m) to keep the local ledger in sync with exchange state.
"""
import time
import logging
from typing import Dict, Any
from src.ledger.reconciliation import ReconciliationEngine

logger = logging.getLogger("accretion.runtime.account")

class AccountPoller:
    """
    Triggers account reconciliation and persistence every ~60 seconds.
    """
    def __init__(self, reconciliation_engine: ReconciliationEngine):
        self.reconciliation_engine = reconciliation_engine
        self.last_poll_time = 0.0

    def poll_and_reconcile(self) -> Dict[str, Any]:
        """
        Executes a single account reconciliation run.
        """
        start = time.time()
        summary = self.reconciliation_engine.reconcile_all()
        elapsed = time.time() - start
        
        logger.info(
            f"Account Reconciliation completed in {elapsed:.2f}s | "
            f"Balances: {summary['balances_updated']} | "
            f"Orders Checked: {summary['orders_checked']} (Healed: {summary['orders_healed']}) | "
            f"New Fills: {summary['new_fills_recorded']} | "
            f"Errors: {len(summary['errors'])}"
        )
        self.last_poll_time = time.time()
        return summary
