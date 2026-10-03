"""Authenticated encryption for persisted configuration and recovery snapshots."""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, field_validator


class SecretStorageConfig(BaseModel):
    enabled: bool = False
    key_env: str = "APIM_CONFIG_ENCRYPTION_KEY"

    @field_validator("key_env")
    @classmethod
    def validate_key_reference(cls, value: str) -> str:
        if not value.startswith("APIM_") or not value.replace("_", "").isalnum():
            raise ValueError("Encryption key reference must be an APIM_ environment variable")
        return value


def _cipher(key_env: str) -> Fernet:
    key = os.getenv(key_env, "")
    if not key:
        raise ValueError(f"Configuration encryption requires {key_env}")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("Configuration encryption key must be a Fernet key") from exc


def encrypt_config(payload: dict[str, Any], key_env: str) -> dict[str, Any]:
    token = _cipher(key_env).encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return {"format": "apim-encrypted-config-v1", "key_env": key_env, "ciphertext": token}


def decrypt_config(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("format") != "apim-encrypted-config-v1":
        return payload
    key_env = payload.get("key_env", "APIM_CONFIG_ENCRYPTION_KEY")
    if not isinstance(key_env, str) or not key_env.startswith("APIM_"):
        raise ValueError("Invalid configuration encryption key reference")
    try:
        data = json.loads(_cipher(key_env).decrypt(payload["ciphertext"].encode()))
    except (InvalidToken, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("Encrypted configuration failed authentication") from exc
    if not isinstance(data, dict):
        raise ValueError("Encrypted configuration must contain an object")
    return data
