"""Optional typed decision-record schema for autonomous-audit.

Provides a lightweight, stdlib-only validation mechanism to ensure
decision records contain required typed fields before they are hashed and appended.
"""

from typing import Dict, Type


class RecordSchema:
    """A lightweight schema validator for audit records.

    Ensures that a given dictionary contains all required fields with the correct types.
    """

    def __init__(self, fields: Dict[str, Type]):
        """
        :param fields: A mapping of field_name -> expected_type
        """
        self.fields = fields

    def validate(self, record: dict) -> None:
        """Validates that all required fields are present and correctly typed in the record.

        Raises:
            ValueError: If a required field is missing.
            TypeError: If a field has the wrong type.
        """
        for field, expected_type in self.fields.items():
            if field not in record:
                raise ValueError(
                    f"Schema validation failed: missing required field '{field}'"
                )
            if not isinstance(record[field], expected_type):
                raise TypeError(
                    f"Schema validation failed: field '{field}' must be "
                    f"{expected_type.__name__}, got {type(record[field]).__name__}"
                )
