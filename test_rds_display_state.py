import os
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

with patch.dict(os.environ, {"ROTATE_SECONDS": "10"}):
    from zara_songname_to_rds import record_rds_display_text


class RdsDisplayStateTests(unittest.TestCase):
    def setUp(self):
        self.database = sqlite3.connect(":memory:")
        self.addCleanup(self.database.close)
        self.database.execute("""
            CREATE TABLE rds_display_state (
                id smallint NOT NULL PRIMARY KEY,
                current_text varchar(64) NOT NULL,
                override_text varchar(64),
                override_lifetime varchar(20) NOT NULL,
                updated_at timestamp NOT NULL
            )
        """)
        self.connection = MagicMock()
        cursor = self.connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = lambda sql, params: self.database.execute(
            sql.replace("%s", "?"), params
        )

    def test_creating_first_display_state_supplies_required_fields(self):
        record_rds_display_text(self.connection, "Current track")

        row = self.database.execute(
            "SELECT current_text, override_text, override_lifetime, updated_at "
            "FROM rds_display_state WHERE id = 1"
        ).fetchone()
        self.assertEqual(row[:3], ("Current track", None, "until_cleared"))
        self.assertIsNotNone(row[3])

    def test_updating_display_preserves_override_and_duration(self):
        self.database.execute("""
            INSERT INTO rds_display_state
                (id, current_text, override_text, override_lifetime, updated_at)
            VALUES
                (1, 'Old track', 'Engineer message', 'until_next_song', CURRENT_TIMESTAMP)
        """)

        record_rds_display_text(self.connection, "x" * 65)

        row = self.database.execute(
            "SELECT current_text, override_text, override_lifetime "
            "FROM rds_display_state WHERE id = 1"
        ).fetchone()
        self.assertEqual(row, ("x" * 64, "Engineer message", "until_next_song"))


if __name__ == "__main__":
    unittest.main()
