"""
Unit tests for external CommandProcessor
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock
from src.ledger.database import SQLiteLedger
from src.runtime.command_processor import CommandProcessor

class TestCommandProcessor(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cmd_ledger.sqlite")
        self.ledger = SQLiteLedger(self.db_path)
        self.adapter = MagicMock()
        self.processor = CommandProcessor(ledger=self.ledger, adapter=self.adapter)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_pause_and_resume(self):
        # 1. Submit PAUSE
        cmd_id_1 = self.ledger.submit_command("ANDROID_APP", "PAUSE")
        processed = self.processor.process_pending_commands()
        self.assertEqual(processed, 1)
        self.assertTrue(self.processor.is_paused)

        # Verify command status
        with self.ledger._get_connection() as conn:
            row = conn.execute("SELECT status FROM commands WHERE command_id = ?", (cmd_id_1,)).fetchone()
            self.assertEqual(row['status'], 'COMPLETED')

        # 2. Submit RESUME
        cmd_id_2 = self.ledger.submit_command("CLI", "RESUME")
        processed_2 = self.processor.process_pending_commands()
        self.assertEqual(processed_2, 1)
        self.assertFalse(self.processor.is_paused)

    def test_cancel_order_command(self):
        self.adapter.cancel_order.return_value = {'symbol': 'BTCUSDT', 'orderId': 12345, 'status': 'CANCELED'}
        
        cmd_id = self.ledger.submit_command(
            sender="ADMIN_UI",
            command_type="CANCEL_ORDER",
            payload={'symbol': 'BTCUSDT', 'order_id': '12345'}
        )

        processed = self.processor.process_pending_commands()
        self.assertEqual(processed, 1)
        self.adapter.cancel_order.assert_called_once_with(symbol='BTCUSDT', order_id='12345', client_order_id=None)

    def test_invalid_command_failure(self):
        cmd_id = self.ledger.submit_command("TESTER", "UNKNOWN_ACTION")
        processed = self.processor.process_pending_commands()
        self.assertEqual(processed, 0)

        with self.ledger._get_connection() as conn:
            row = conn.execute("SELECT status, result_json FROM commands WHERE command_id = ?", (cmd_id,)).fetchone()
            self.assertEqual(row['status'], 'FAILED')

if __name__ == '__main__':
    unittest.main()
