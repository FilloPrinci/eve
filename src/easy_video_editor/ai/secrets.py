"""API keys in the OS keyring (SPEC §7.5): never in project files, settings files or logs."""

from __future__ import annotations

from easy_video_editor.core.errors import EveError

SERVICE = "EVE"


def get_api_key(provider: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, provider)
    except Exception as error:  # keyring backends raise many different errors
        raise EveError(f"Cannot read the API key from the system keyring: {error}") from error


def set_api_key(provider: str, key: str) -> None:
    try:
        import keyring

        if key:
            keyring.set_password(SERVICE, provider, key)
        else:
            keyring.delete_password(SERVICE, provider)
    except Exception as error:
        raise EveError(f"Cannot store the API key in the system keyring: {error}") from error
