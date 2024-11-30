# effortlessdb/effortless.py
import json
import os
import logging
from typing import Any, Dict, List, Optional, Union
import zlib
import base64
import threading
import shutil
from effortless.configuration import EffortlessConfig
from effortless.search import Query
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class EffortlessDB:
    def __init__(self, db_name: str = "db", encryption_key: Optional[str] = None):
        """
        Initialize an EffortlessDB instance.

        This constructor sets up a new database with the given name. If no name is provided,
        it defaults to "db". It sets up the storage and performs initial auto-configuration.

        Args:
            db_name (str, optional): The name of the database. Defaults to "db".
            encryption_key (str, optional): The encryption key for the database.

        """
        self._config = EffortlessConfig()
        self._encryption_key = None
        if encryption_key:
            self.encrypt(encryption_key)
        self.set_storage(db_name)
        self._autoconfigure()
        self._operation_count = 0
        self._backup_thread = None
        
        logger.debug(f"EffortlessDB initialized with db_name={db_name}, encryption_key={encryption_key}")

    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, new_config: EffortlessConfig):
        if not isinstance(new_config, EffortlessConfig):
            raise TypeError("Config must be an EffortlessConfig object")
        new_config.validate_db(self)
        self._config = new_config
        data = self._read_db()
        data["headers"] = self._config.to_dict()
        self._write_db(data)

    @property
    def encryption_key(self):
        """
        Get the current encryption key.

        Returns:
            Optional[str]: The current encryption key, or None if encryption is not set.
        """
        return self._encryption_key

    @encryption_key.setter
    def encryption_key(self, new_key: str):
        """
        Set a new encryption key.

        This setter is a convenience method that calls set_encryption_key().

        Args:
            new_key (Optional[str]): The new encryption key to set.
        """
        self.encrypt(new_key)

    def encrypt(self, new_key: str) -> None:
        """
        Set a new encryption key and re-encrypt the database if necessary.

        If the database is already encrypted, this method will attempt to decrypt
        with both the old and new keys, then re-encrypt with the new key.

        Args:
            new_key (str): The new encryption key to set.

        Raises:
            TypeError: If the new key is not a string (when not None).
            ValueError: If unable to decrypt the database with either the old or new key.
        """

        if not isinstance(new_key, str):
            raise TypeError("Encryption key must be a string")

        old_key = self._encryption_key
        self._encryption_key = new_key

        if self.config.encrypted:
            try:
                self._reencrypt_db(old_key, new_key)
            except ValueError as e:
                self._encryption_key = old_key  # Revert to old key
                logger.error(f"Failed to re-encrypt the database: {e}")
                raise e
        else:
            self.config.encrypted = True
            logger.info(f"Encryption key set for new database encryption.")

    def _reencrypt_db(self, old_key: Optional[str], new_key: str) -> None:
        """
        Re-encrypt the database with a new key.

        This method is called internally when changing the encryption key.

        Args:
            old_key (Optional[str]): The previous encryption key.
            new_key (str): The new encryption key.

        Raises:
            ValueError: If unable to decrypt the database with either the old or new key.
        """
        try:
            data = self._read_db(try_keys=[old_key, new_key])
            self._write_db(data, force_encrypt=True)
            logger.info("Database successfully re-encrypted with new key.")
        except ValueError as e:
            logger.error(f"Failed to re-encrypt the database: {e}")
            raise

    @staticmethod
    def default_db():
        """
        Create and return a default database structure.

        This method generates a dictionary representing an empty database with default headers.
        This is mainly used for test cases and you probably don't need it.

        Returns:
            dict: A dictionary with 'headers' (default configuration) and an empty 'content'.
        """
        ddb: Dict[str, Any] = {"headers": EffortlessConfig().to_dict()}
        ddb["content"] = []
        return ddb

    def set_directory(self, directory: str) -> None:
        """
        Set the directory for the database file.

        This method specifies where the database file should be stored. It updates the
        internal storage path and triggers a reconfiguration of the database.

        Args:
            directory (str): The directory path where the database file will be stored. Use set_storage to set the filename.

        Raises:
            TypeError: If directory is not a string.
            ValueError: If directory is empty or does not exist.
        """
        if not isinstance(directory, str):
            raise TypeError("The database directory must be a string")
        if not directory:
            raise ValueError("The database directory cannot be empty.")
        if not os.path.isdir(directory):
            raise ValueError(f"The database path ({directory}) does not exist.")

        self._storage_directory = directory
        self._update_storage_file()
        logger.info(f"Database directory set to {directory}")


    def set_storage(self, db_name: str) -> None:
        """
        Set the storage file for the database.

        This method determines the filename for the database storage. It appends
        the '.effortless' extension to the provided name and updates the storage configuration.

        Args:
            db_name (str): The name of the database file (without extension).
                           This will be used as the prefix for the .effortless file.

        Raises:
            TypeError: If db_name is not a string.
            ValueError: If db_name is empty or contains invalid characters.

        Note:
            The actual file will be named '{db_name}.effortless'.
        """
        if not isinstance(db_name, str):
            raise TypeError("The database name must be a string")
        if not db_name:
            raise ValueError("Database name cannot be empty")
        if not all(c.isalnum() or c in "-_" for c in db_name):
            raise ValueError(
                "Database name must contain only alphanumeric characters, underscores, or dashes"
            )

        self._storage_filename = f"{db_name}.effortless"
        self._update_storage_file()
        logger.info(f"Storage file set to {self._storage_filename}")


    def _update_storage_file(self) -> None:
        """
        Update the _storage_file based on the current directory and filename.

        This internal method combines the storage directory (if set) with the filename
        to create the full path for the database file. It then triggers an auto-configuration
        to ensure the database is properly set up for the new location.

        Note:
            This method is called internally when the storage location changes.
        """
        if hasattr(self, "_storage_directory"):
            self._storage_file = os.path.join(
                self._storage_directory, self._storage_filename
            )
        else:
            self._storage_file = self._storage_filename

        self._autoconfigure()  # configure EffortlessConfig to the new file's configuration
        logger.debug(f"Storage file path updated to {self._storage_file}")

    def _autoconfigure(self) -> None:
        """
        Ensure the database has a valid configuration in its headers.

        This method checks if the database file has a valid configuration. If not,
        it creates a default configuration and writes it to the file. It then
        updates the internal configuration object to match the file's configuration.

        Note:
            This method is called internally during initialization and when the storage changes.
        """
        data = self._read_db()
        current_config = EffortlessConfig.from_dict(data["headers"])

        if current_config.version != EffortlessConfig.CURRENT_VERSION:
            self._migrate()

        self._update_config()

    def _migrate(self):
        data = self._read_db()
        config = EffortlessConfig.from_dict(data.get("headers", {}))
        if config.version == EffortlessConfig.CURRENT_VERSION:
            pass
        elif config.version.startswith("1.2"):
            content = data["content"]
        else:
            content = data["1"]
        config.version = EffortlessConfig.CURRENT_VERSION
        new_data = {"headers": config.to_dict(), "content": content}
        self._write_db(new_data)
        logger.info("Database migration completed to current version.")

    def _update_config(self):
        """
        Update the internal configuration object based on the database file.
        """
        data = self._read_db()
        try:
            new_config = EffortlessConfig.from_dict(data["headers"])
            new_config.validate_db(self)
            self._config = new_config
            logger.debug("Configuration updated successfully.")
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid configuration in database file: {e}")
            # Optionally, you could reset to a default configuration here

    def configure(self, new_config: EffortlessConfig) -> None:
        """
        Update the database configuration.
        """
        if not isinstance(new_config, EffortlessConfig):
            raise TypeError("New configuration must be an EffortlessConfig object")

        new_config.validate_db(self)

        data = self._read_db()
        data["headers"] = new_config.to_dict()
        self._write_db(data, write_in_readonly=True)
        self._config = new_config
        logger.info(f"Database configuration updated successfully.")

    def get_all(self) -> List[Dict[str, Any]]:
        """
        Retrieve all records from the database.

        This method returns all the data stored in the database, excluding the configuration.
        The returned list contains all records in the database.

        Returns:
            List[Dict[str, Any]]: A list where each entry is a record in the database.
        """
        logger.debug("Fetching all records from the database.")
        return self._read_db()["content"]

    def filter(self, query: Query) -> List[Dict[str, Any]]:
        """
        Filter the database records based on a given query.

        This method applies the provided query to all records in the database and returns
        the matching results.

        Args:
            query (Query): A Query object defining the filter criteria.

        Returns:
            List[Dict[str, Any]]: A list of records that match the query criteria.
        """
        logger.debug(f"Filtering records based on query: {query}")
        return [entry for entry in self.get_all() if query.match(entry)]

    def add(self, entry: dict) -> None:
        """
        Add a new entry to the database.

        This method adds a new record to the database. It performs several checks:
        - Ensures the entry is a dictionary
        - Verifies that all required fields (as per configuration) are present
        - Checks if the entry is JSON-serializable
        - Verifies that adding the entry won't exceed the configured max size (if set)

        Args:
            entry (dict): The entry to be added to the database.

        Raises:
            TypeError: If the entry is not a dictionary.
            ValueError: If a required field is missing, if the entry is not JSON-serializable,
                        or if adding the entry would exceed the max size limit.

        Note:
            This method also triggers a backup if the backup conditions are met.
        """
        if not isinstance(entry, dict):
            raise TypeError("Entry must be a dictionary")

        for field in self.config.required_fields:
            if field not in entry:
                raise ValueError(
                    f"Field '{field}' is configured to be required in this database"
                )

        try:
            json.dumps(entry)
        except (TypeError, ValueError):
            raise ValueError("Entry must be JSON-serializable")

        data = self._read_db()

        if self.config.max_size:
            current_size = os.path.getsize(self._storage_file) / (
                1024 * 1024
            )  # Size in MB
            new_size = current_size + len(json.dumps(entry)) / (1024 * 1024)
            if new_size > self.config.max_size:
                raise ValueError(
                    f"The requested operation would increase the size of the database past the configured max db size ({self.config.max_size} MB)."
                )

        data["content"].append(entry)
        self._write_db(data)
        self._handle_backup()
        logger.debug(f"Entry added to database: {entry}")

    def wipe(self, wipe_readonly: bool = False) -> None:
        """
        Clear all data from the database.

        This method removes all content and headers from the database, resetting it to its initial state.

        Args:
            wipe_readonly (bool, optional): If True, allows wiping even if the database is in read-only mode.
                                            Defaults to False.

        Note:
            Use this method with caution as it permanently deletes all data in the database. This will not wipe backups.
        """
        self._write_db(
            {"headers": EffortlessConfig().to_dict(), "content": []},
            write_in_readonly=wipe_readonly,
        )
        self._update_config()
        logger.info("Database wiped successfully.")

    def _read_db(
        self, try_keys: Optional[List[Optional[str]]] = None
    ) -> Dict[str, Any]:
        """
        Read the contents of the database file.

        This internal method reads the database file, handling decryption and decompression
        if these features are enabled in the configuration.

        Returns:
            Dict[str, Any]: A dictionary containing the database headers and content.

        Raises:
            IOError: If there's an error reading the file.
            json.JSONDecodeError: If the file content is not valid JSON.

        Note:
            If the database file doesn't exist, it returns a default empty database structure.
        """
        try:
            if not os.path.exists(self._storage_file):
                self._write_db(
                    {"headers": EffortlessConfig().to_dict(), "content": []},
                    write_in_readonly=True,
                )
                return {"headers": EffortlessConfig().to_dict(), "content": []}

            with open(self._storage_file, "rb") as f:
                data = json.loads(f.read().decode())

            headers = data["headers"]
            content = data["content"]

            if headers.get("encrypted"):
                if try_keys is None:
                    try_keys = [self._encryption_key]

                decrypted = False
                if isinstance(content, str):
                    for key in try_keys:
                        if key is not None:
                            try:
                                content = self._decrypt_data(content, key)
                                decrypted = True
                                break
                            except InvalidToken:
                                continue
                else:
                    logger.warning("Content is not encrypted despite encryption flag")

                if not decrypted and headers.get("encrypted"):
                    raise ValueError("Unable to decrypt database with provided keys")

            if headers.get("compressed"):
                if isinstance(content, str):
                    content = self._decompress_data(content)
                else:
                    logger.warning("Content is not compressed despite compression flag")

            return {"headers": headers, "content": content}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error reading database: {str(e)}")
            raise

    def _write_db(
        self,
        data: Dict[str, Any],
        write_in_readonly: bool = False,
        force_encrypt: bool = False,
    ) -> None:
        """
        Write data to the database file.

        This internal method writes the provided data to the database file, handling
        compression and encryption if these features are enabled in the configuration.

        Args:
            data (Dict[str, Any]): The data to write to the database.
            write_in_readonly (bool, optional): If True, allows writing even if the database
                                                is in read-only mode. Defaults to False.

        Raises:
            ValueError: If attempting to write to a read-only database without permission.
            IOError: If there's an error writing to the file.

        Note:
            This method is used internally for all database write operations.
        """
        try:
            if self.config.readonly and not write_in_readonly:
                raise ValueError("Database is in read-only mode")

            headers = data["headers"]
            content = data["content"]

            if headers.get("compressed"):
                content = self._compress_data(content)

            if headers.get("encrypted") or force_encrypt:
                if self._encryption_key is None:
                    raise ValueError(
                        "Encryption key is required to write encrypted data"
                    )
                content = self._encrypt_data(content, self._encryption_key)
                headers["encrypted"] = True

            final_data = json.dumps(
                {"headers": headers, "content": content}, indent=2
            ).encode()

            with open(self._storage_file, "wb") as f:
                f.write(final_data)

            logger.debug(f"Data written to {self._storage_file}")
        except IOError as e:
            logger.error(f"Error writing to database: {str(e)}")
            raise

    def _handle_backup(self) -> None:
        """
        Handle database backup based on configuration.

        This method is called after each write operation. It increments an operation counter
        and triggers a backup when the counter reaches the configured backup interval.

        Note:
            Backups are performed in a separate thread to avoid blocking the main operation.
        """
        self._operation_count += 1
        if (
            self.config.backup_path
            and self._operation_count >= self.config.backup_interval
        ):
            self._operation_count = 0
            logger.debug(f"Backup interval reached. Starting backup...")

            # If a backup thread is already running, we can stop it
            if self._backup_thread and self._backup_thread.is_alive():
                self._backup_thread.join(timeout=0)  # Non-blocking join
                if self._backup_thread.is_alive() and self.config.debug:
                    logger.debug("Previous backup thread is alive and not stopping")

            # Start a new backup thread
            self._backup_thread = threading.Thread(target=self._backup)
            self._backup_thread.start()

    def finish_backup(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for any ongoing backup operation to complete.

        This method blocks until the current backup thread (if any) has finished.

        Args:
            timeout (float, optional): Maximum time to wait for the backup to complete, in seconds.
                                       If None, wait indefinitely. Defaults to None.

        Returns:
            bool: True if the backup completed (or there was no backup running),
                  False if the timeout was reached before the backup completed.
        """
        if self._backup_thread and self._backup_thread.is_alive():
            logger.debug(f"Waiting for backup thread to finish...")
            self._backup_thread.join(timeout)
            if not self._backup_thread.is_alive():
                logger.debug("Backup completed.")
            else:
                logger.warning(f"Backup thread did not complete within timeout of {timeout}s.")
            return not self._backup_thread.is_alive()
        logger.debug("No backup thread is running.")
        return True

    def _backup(self) -> bool:
        """
        Perform a database backup.

        This method creates a copy of the database file in the configured backup location.
        It's typically called by _handle_backup() in a separate thread.

        Note:
            If the backup fails, an error is logged but no exception is raised to the caller.
        """
        if self.config.backup_path:
            try:
                if not os.path.exists(self.config.backup_path) or not os.access(
                    self.config.backup_path, os.W_OK
                ):
                    raise IOError(
                        f"Backup directory {self.config.backup_path} is not writable or does not exist."
                    )

                backup_path = os.path.join(
                    self.config.backup_path, os.path.basename(self._storage_file)
                )
                shutil.copy2(self._storage_file, backup_path)
                logger.debug(f"Database backed up to {backup_path}")
                return True  # Indicate success
            except IOError as e:
                logger.error(f"Backup failed: {str(e)}")
                return False  # Indicate failure

        logger.warning("Backup path not configured, skipping backup.")
        return False

    def _compress_data(self, data: Union[str, List[Dict[str, Any]]]) -> str:
        """
        Compress the given data and return as a base64-encoded string.

        Args:
            data (Union[str, List[Dict[str, Any]]]): The data to be compressed.

        Returns:
            str: A base64-encoded string of the compressed data.
        """
        if isinstance(data, list):
            data = json.dumps(data)
        compressed = zlib.compress(data.encode())
        encoded_data = base64.b64encode(compressed).decode()
        logger.debug(f"Data compressed to {len(encoded_data)} characters.")
        return base64.b64encode(compressed).decode()

    def _decompress_data(self, data: str) -> List[Dict[str, Any]]:
        """
        Decompress the given string data.

        This method decompresses the data using zlib, and then parses it as JSON.

        Args:
            data (str): The compressed data as a string.

        Returns:
            List[Dict[str, Any]]: The decompressed and parsed data.
        """
        decompressed = zlib.decompress(base64.b64decode(data))
        decoded_data = json.loads(decompressed.decode())
        logger.debug(f"Data decompressed and parsed. {len(decoded_data)} entries found.")
        return decoded_data

    def _encrypt_data(self, data: Union[str, Dict[str, Any]], key: str) -> str:
        """
        Encrypt the given data using the provided key.

        Args:
            data (Union[str, Dict[str, Any]]): The data to encrypt.
            key (str): The encryption key.

        Returns:
            str: The encrypted data as a string.
        """
        fernet = Fernet(self._get_fernet_key(key))
        encrypted_data = fernet.encrypt(json.dumps(data).encode()).decode()
        logger.debug("Data encrypted successfully.")
        return encrypted_data

    def _decrypt_data(self, data: str, key: str) -> Dict[str, Any]:
        """
        Decrypt the given data using the provided key.

        Args:
            data (str): The encrypted data.
            key (str): The decryption key.

        Returns:
            Dict[str, Any]: The decrypted data.

        Raises:
            InvalidToken: If the decryption fails due to an invalid key.
        """
        fernet = Fernet(self._get_fernet_key(key))
        try:
            decrypted_data = fernet.decrypt(data.encode()).decode()
            logger.debug("Data decrypted successfully.")
            return json.loads(decrypted_data)
        except InvalidToken:
            logger.error("Decryption failed: Invalid encryption key.")
            raise

    @staticmethod
    def _get_fernet_key(key: str) -> bytes:
        """
        Generate a Fernet-compatible key from the given string key.

        Args:
            key (str): The original key string.

        Returns:
            bytes: A URL-safe base64-encoded 32-byte key for Fernet.
        """
        return base64.urlsafe_b64encode(key.encode().ljust(32)[:32])

    def update(self, update_data: Dict[str, Any], condition: Query) -> bool:
        """
        Update a single entry in the database that matches the given condition.

        Args:
            update_data (Dict[str, Any]): The data to update the matching entry with.
            condition (Query): A Query object defining the condition to match.

        Returns:
            bool: True if an entry was updated, False if no matching entry was found.

        Raises:
            ValueError: If more than one entry matches the condition.
        """
        data = self._read_db()
        matching_entries = [
            entry for entry in data["content"] if condition.match(entry)
        ]

        if len(matching_entries) > 1:
            raise ValueError(
                "More than one entry matches the given condition. If you want to update multiple entries at once, use batch() instead."
            )
        elif len(matching_entries) == 0:
            return False

        index = data["content"].index(matching_entries[0])
        data["content"][index].update(update_data)
        self._write_db(data)
        self._handle_backup()
        logger.debug(f"Entry updated with data: {update_data}")
        return True

    def batch(self, update_data: Dict[str, Any], condition: Query) -> int:
        """
        Update all entries in the database that match the given condition.

        Args:
            update_data (Dict[str, Any]): The data to update the matching entries with.
            condition (Query): A Query object defining the condition to match.

        Returns:
            int: The number of entries that were updated.
        """
        data = self._read_db()
        updated_count = 0

        for entry in data["content"]:
            if condition.match(entry):
                entry.update(update_data)
                updated_count += 1

        if updated_count > 0:
            self._write_db(data)
            self._handle_backup()
            logger.debug(f"{updated_count} entries updated.")
        else:
            logger.debug("No entries updated.")

        return updated_count

    def remove(self, condition: Query) -> bool:
        """
        Remove a single entry from the database that matches the given condition.

        Args:
            condition (Query): A Query object defining the condition to match.

        Returns:
            bool: True if an entry was removed, False if no matching entry was found.

        Raises:
            ValueError: If more than one entry matches the condition.
        """
        data = self._read_db()
        matching_entries = [
            entry for entry in data["content"] if condition.match(entry)
        ]

        if len(matching_entries) > 1:
            logger.error("More than one entry matches the given condition.")
            raise ValueError(
                "More than one entry matches the given condition. If you want to remove multiple entries at once, use erase() instead."
            )
        elif len(matching_entries) == 0:
            logger.debug("No matching entry found to remove.")
            return False

        data["content"].remove(matching_entries[0])
        self._write_db(data)
        self._handle_backup()
        logger.debug("Entry removed from the database.")
        return True

    def erase(self, condition: Query) -> int:
        """
        Erase all entries from the database that match the given condition.

        Args:
            condition (Query): A Query object defining the condition to match.

        Returns:
            int: The number of entries that were removed.
        """
        data = self._read_db()
        original_length = len(data["content"])
        data["content"] = [
            entry for entry in data["content"] if not condition.match(entry)
        ]
        removed_count = original_length - len(data["content"])

        if removed_count > 0:
            self._write_db(data)
            self._handle_backup()
            logger.debug(f"Erased {removed_count} entries from the database.")
        else:
            logger.debug("No entries matching the condition to erase.")

        return removed_count

    def search(self, query: Query) -> Optional[Dict[str, Any]]:
        """
        Search for a single entry in the database that matches the given query.

        This method is similar to filter(), but returns only one matching entry
        instead of a list of all matching entries.

        Args:
            query (Query): A Query object defining the search criteria.

        Returns:
            Optional[Dict[str, Any]]: The first entry that matches the query,
                                      or None if no matching entry is found.

        Raises:
            ValueError: If more than one entry matches the query.
        """
        matching_entries = self.filter(query)

        if len(matching_entries) > 1:
            raise ValueError(
                "More than one entry matches the given query. Use filter() if you want to retrieve multiple entries."
            )
        elif len(matching_entries) == 0:
            return None

        return matching_entries[0]

    def unencrypt(self) -> None:
        """
        Permanently unencrypt the database using the key if it's currently encrypted.

        Raises:
            ValueError: If no encryption key is set.
        """
        data = self._read_db()
        if not data["headers"]["encrypted"]:
            self._encryption_key = None
            logger.debug("Database is not encrypted, nothing to unencrypt.")
            return
        if self._encryption_key is None:
            raise ValueError("No encryption key set")

        self.config.encrypted = False
        data["headers"]["encrypted"] = False
        self._write_db(data)
        self._encryption_key = None
        logger.info("Database unencrypted successfully")


class DocumentationDB(EffortlessDB):
    def __init__(self):
        super().__init__("db")
        self._config.encrypted = False

    def encrypt(self, new_key: str) -> None:
        raise NotImplementedError(
            "The base db cannot be encrypted. Create an EffortlessDB(encryption_key) instead!"
        )

    @property
    def encryption_key(self):
        return None

    @encryption_key.setter
    def encryption_key(self, new_key: Optional[str]):
        raise NotImplementedError(
            "The base db cannot be encrypted. Create an EffortlessDB(encryption_key) instead!"
        )

    def _reencrypt_db(self, old_key: Optional[str], new_key: str) -> None:
        raise NotImplementedError(
            "The base db cannot be encrypted. Create an EffortlessDB(encryption_key) instead!"
        )

    def unencrypt(self) -> None:
        pass


db = DocumentationDB()
