"""
Multi-Writer Command Queue Processor
Consumes and executes external command intents submitted to the SQLite ledger commands table.
"""
import logging
from typing import Optional, Dict, Any, List
from src.ledger.database import SQLiteLedger
from src.adapters.binance_client import BinanceSpotAdapter

logger = logging.getLogger("accretion.runtime.commands")

class CommandProcessor:
    """
    Processes external commands (e.g. from an Android app, CLI tool, or admin dashboard).
    """
    def __init__(self, ledger: SQLiteLedger, adapter: Optional[BinanceSpotAdapter] = None):
        self.ledger = ledger
        self.adapter = adapter
        self.is_paused = False

    def process_pending_commands(self) -> int:
        """
        Polls and executes all pending commands in the commands table.
        """
        pending = self.ledger.get_pending_commands()
        if not pending:
            return 0

        executed_count = 0
        for cmd in pending:
            cmd_id = cmd['command_id']
            cmd_type = cmd['command_type'].upper()
            payload = cmd.get('payload', {})
            logger.info(f"Processing command [{cmd_id}] Type={cmd_type} Sender={cmd.get('sender')}")

            try:
                result = self._execute_command(cmd_type, payload)
                self.ledger.update_command_status(cmd_id, status='COMPLETED', result=result)
                executed_count += 1
                logger.info(f"Command [{cmd_id}] COMPLETED: {result}")
            except Exception as e:
                logger.error(f"Command [{cmd_id}] FAILED: {e}")
                self.ledger.update_command_status(cmd_id, status='FAILED', result={'error': str(e)})

        return executed_count

    def _execute_command(self, cmd_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches command to internal handlers."""
        if cmd_type == 'PAUSE':
            self.is_paused = True
            return {'status': 'PAUSED', 'message': 'Bot operations paused.'}

        elif cmd_type == 'RESUME':
            self.is_paused = False
            return {'status': 'RESUMED', 'message': 'Bot operations resumed.'}

        elif cmd_type == 'CANCEL_ORDER':
            if not self.adapter:
                raise ValueError("No exchange adapter configured to execute CANCEL_ORDER.")
            symbol = payload.get('symbol')
            order_id = payload.get('order_id')
            client_order_id = payload.get('client_order_id')
            if not symbol or (not order_id and not client_order_id):
                raise ValueError("CANCEL_ORDER requires 'symbol' and 'order_id' or 'client_order_id'.")
            res = self.adapter.cancel_order(symbol=symbol, order_id=order_id, client_order_id=client_order_id)
            return {'status': 'CANCELED', 'exchange_response': res}

        elif cmd_type == 'PING':
            return {'status': 'PONG', 'is_paused': self.is_paused}

        elif cmd_type == 'LOG':
            msg = payload.get('message', '')
            level = payload.get('level', 'INFO')
            self.ledger.log_event(level=level, component="EXTERNAL", message=msg, metadata=payload)
            return {'status': 'LOGGED', 'message': msg}

        else:
            raise ValueError(f"Unknown or unsupported command type: {cmd_type}")
