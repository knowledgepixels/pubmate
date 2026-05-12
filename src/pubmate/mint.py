import hashlib
import logging
import os
import re
import json

from typing import Optional, Set
from urllib.parse import urlsplit
from ulid import ULID, constants
from uuid import uuid4

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class IdentifierGenerator:
    def __init__(
        self,
        namespace: str,
        type_prefix: Optional[str] = None,
    ):
        self.namespace = namespace
        self.type_prefix = type_prefix
        self.registered_ids: Set[str] = set()

    def is_id_available(self, identifier: str) -> bool:
        if identifier in self.registered_ids:
            return False

        return True

    def is_namespace_id(self, key: str):
        if key.startswith(self.namespace):
            return True
        return False

    def is_valid_uri(self, key: str) -> bool:
        if not isinstance(key, str):
            return False
        if re.search(r"[\x00-\x20]", key):
            return False

        parsed = urlsplit(key)
        if not parsed.scheme:
            return False
        if parsed.scheme in {"http", "https"} and not parsed.netloc:
            return False

        return True

    def is_valid_id(self, key: str, method: str = "ulid") -> bool:
        # Check if key starts with namespace
        if not key.startswith(self.namespace):
            return False

        # Remove namespace to get the remaining part
        remaining = key[len(self.namespace) :]

        # Define expected unique part patterns based on method
        if method == "uuid":
            unique_pattern = r"^[0-9a-f]{8}$"  # 8 hex chars
        elif method == "hash":
            unique_pattern = r"^[0-9a-f]{10}$"  # 10 hex chars
        elif method == "ulid":
            unique_pattern = r"^[0-9A-HJKMNP-TV-Z]{26}$"  # full ULID
        else:
            raise NotImplementedError

        # Check pattern based on whether type_prefix is used
        if self.type_prefix is not None:
            # Expected format: {type_prefix}-{unique_part}
            expected_prefix = f"{self.type_prefix}-"
            if not remaining.startswith(expected_prefix):
                return False
            # Extract unique part after type_prefix and dash
            unique_part = remaining[len(expected_prefix) :]
        else:
            # Expected format: {unique_part} directly
            unique_part = remaining

        # Validate the unique part matches the expected pattern
        return bool(re.match(unique_pattern, unique_part))

    def register_id(self, identifier: str) -> None:
        self.registered_ids.add(identifier)

    def hash_dict(self, data: dict) -> str:
        # Convert dict to canonical JSON (sorted keys, no whitespace)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def random_ulid(self) -> str:
        timestamp = ULID.provider.timestamp().to_bytes(constants.TIMESTAMP_LEN, "big")
        randomness = os.urandom(constants.RANDOMNESS_LEN)
        return str(ULID.from_bytes(timestamp + randomness))

    def generate_id(
        self,
        entity: dict,
        method: str = "ulid",
        check_collision: bool = True,
        max_attempts: int = 10,
        preflabel: str = "name",
    ) -> str:
        """
        Generate a structured identifier for a vocabulary term with collision detection.

        Args:
            term_name: The name of the term
            method: ID generation method ('ulid', 'uuid', or 'hash')
            check_collision: Whether to check for collisions
            max_attempts: Maximum number of attempts to generate a unique ID

        Returns:
            A structured identifier that's unique and available
        """
        attempts = 0

        while attempts < max_attempts:
            # Generate unique part based on method
            if method == "uuid":
                unique_part = str(uuid4())[:8]  # First 8 chars of UUID
            elif method == "ulid":
                unique_part = self.random_ulid()

            elif method == "hash":
                # Create a hash of the term name, possibly with a salt for retry attempts
                if attempts > 0:
                    salted_entity = {**entity, "_collision": attempts}
                else:
                    salted_entity = entity

                hash_str = self.hash_dict(salted_entity)
                unique_part = hash_str[:10]

            else:
                raise ValueError(f"Unknown method: {method}. Available methods: ulid, uuid, hash")

            # Construct the full identifier
            if self.type_prefix is None:
                identifier = f"{self.namespace}{unique_part}"
            else:
                identifier = f"{self.namespace}{self.type_prefix}-{unique_part}"

            # Check if this identifier is available
            if check_collision:
                if self.is_id_available(identifier):
                    # Register the ID as used
                    self.register_id(identifier)
                    return identifier
            else:
                return identifier

            # If we got here, there was a collision - try again
            attempts += 1

        # If we exhausted all attempts, raise an error
        label_value = entity.get(preflabel, "<missing>")
        raise RuntimeError(f"Could not generate a unique identifier for '{label_value}' after {max_attempts} attempts")
