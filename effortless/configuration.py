import logging
from typing import Any, Dict, List, Optional
import os

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

class EffortlessConfig:
    """
    Configuration class for EffortlessDB.

    This class holds various configuration options for an EffortlessDB instance.
    """

    CURRENT_VERSION: str = "1.3.0"

    def __init__(
        self,
        *,
        debug: bool = False,
        required_fields: List[str] = [],
        max_size: Optional[float] = None,
        version: str = CURRENT_VERSION,
        backup_path: Optional[str] = None,
        backup_interval: int = 1,
        encrypted: bool = False,
        compressed: bool = False,
        readonly: bool = False,
    ):
        """
        Initialize an EffortlessConfig instance.

        Args:
            debug (bool): Enable debug mode. Defaults to False.
            required_fields (List[str]): List of required fields for each entry. Defaults to an empty list.
            max_size (Optional[int]): Maximum size of the database in MB. Defaults to None (no limit).
            version (str): Version of the configuration.
            backup_path (Optional[str]): Path to backup location. Defaults to None (no backup).
            backup_interval (int): Number of operations between backups. Defaults to 1.
            encrypted (bool): Whether the database should be encrypted. Defaults to False.
            compressed (bool): Whether the database should be compressed. Defaults to False.
            readonly (bool): Whether the database is in read-only mode. Defaults to False.
        """
        self.debug = debug
        self.required_fields = required_fields
        self.max_size = max_size
        self.version = version
        self.backup_path = backup_path
        self.backup_interval = backup_interval
        self.encrypted = encrypted
        self.compressed = compressed
        self.readonly = readonly

    @property
    def debug(self) -> bool:
        return self._debug

    @debug.setter
    def debug(self, value: bool) -> None:
        self._debug = bool(value)
        
        if self._debug:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

    @property
    def required_fields(self) -> List[str]:
        return self._required_fields

    @required_fields.setter
    def required_fields(self, value: List[str]) -> None:
        self._required_fields = list(value)

    @property
    def max_size(self) -> Optional[float]:
        return self._max_size

    @max_size.setter
    def max_size(self, value: Optional[float]) -> None:
        if value is not None and value <= 0:
            raise ValueError("max_size must be a positive integer")
        self._max_size = value

    @property
    def version(self) -> str:
        return self._version

    @version.setter
    def version(self, value: str) -> None:
        if len(value.split(".")) < 3:
            raise ValueError(
                f"Version specification must be a valid Effortless version, i.e. {EffortlessConfig.CURRENT_VERSION}"
            )
        self._version = value

    @property
    def backup_path(self) -> Optional[str]:
        return self._backup_path

    @backup_path.setter
    def backup_path(self, value: Optional[str]) -> None:
        self._backup_path = value

    @property
    def backup_interval(self) -> int:
        return self._backup_interval

    @backup_interval.setter
    def backup_interval(self, value: int) -> None:
        if value <= 0:
            raise ValueError("Backup interval must be a positive integer")
        self._backup_interval = value

    @property
    def encrypted(self) -> bool:
        return self._encrypted

    @encrypted.setter
    def encrypted(self, value: bool) -> None:
        self._encrypted = bool(value)

    @property
    def compressed(self) -> bool:
        return self._compressed

    @compressed.setter
    def compressed(self, value: bool) -> None:
        self._compressed = bool(value)

    @property
    def readonly(self) -> bool:
        return self._readonly

    @readonly.setter
    def readonly(self, value: bool) -> None:
        self._readonly = bool(value)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the configuration to a dictionary.

        Returns:
            Dict[str, Any]: A dictionary representation of the configuration.
        """
        return {
            "debug": self.debug,
            "required_fields": self.required_fields,
            "max_size": self.max_size,
            "version": self.version,
            "backup_path": self.backup_path,
            "backup_interval": self.backup_interval,
            "encrypted": self.encrypted,
            "compressed": self.compressed,
            "readonly": self.readonly,
        }
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "EffortlessConfig":
        """
        Create an EffortlessConfig instance from a dictionary.

        Args:
            config_dict (Dict[str, Any]): A dictionary containing configuration options.

        Returns:
            EffortlessConfig: An instance of EffortlessConfig.
        """
        # For backwards compatibility, map old keys to new keys
        key_mapping = {
            "dbg": "debug",
            "rq": "required_fields",
            "ms": "max_size",
            "v": "version",
            "bp": "backup_path",
            "bpi": "backup_interval",
            "enc": "encrypted",
            "cmp": "compressed",
            "ro": "readonly",
        }

        # Create a new dictionary with updated keys
        updated_dict = {}
        for key, value in config_dict.items():
            updated_dict[key_mapping.get(key, key)] = value

        return cls(**updated_dict)

    @staticmethod
    def default_headers() -> Dict[str, Any]:
        """
        Create a dictionary with default headers.

        Note:
            Mainly used for internal unit testing. If you want a default config, just create one with EffortlessConfig().

        Returns:
            Dict[str, Dict[str, Any]]: A dictionary containing default headers.
        """
        return {"headers": EffortlessConfig().to_dict()}

    def validate_db(self, db) -> None:
        """
        Validate the database against the current configuration.

        Args:
            db (EffortlessDB): The database to validate.

        Raises:
            ValueError: If the database size exceeds max_size or if any entry is missing required fields.
        """
        
        if self.debug:
            logger.debug(f"Validating database with max_size={self.max_size} and required_fields={self.required_fields}.")

        # Check max_size
        if self.max_size is not None:
            current_size = os.path.getsize(db._storage_file) / (
                1024 * 1024
            )  # Size in MB
            if current_size > self.max_size:
                raise ValueError(
                    f"Configuration rejected: database size ({current_size:.2f} MB) exceeds the new max size ({self.max_size} MB)"
                )

        # Check required fields
        if self.required_fields:
            for entry in db.get_all():
                missing_fields = [
                    field for field in self.required_fields if field not in entry
                ]
                if missing_fields:
                    raise ValueError(
                        f"Configuration rejected: the database is missing new required fields: {', '.join(missing_fields)}"
                    )
