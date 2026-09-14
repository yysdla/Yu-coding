"""Errors raised by the Obsidian Vault boundary."""


class ObsidianError(ValueError):
    """Base error for invalid Vault configuration or content."""


class VaultConfigurationError(ObsidianError):
    """The Vault configuration is missing or escapes its allowed root."""


class FrontmatterError(ObsidianError):
    """Frontmatter is malformed or uses an unsupported value."""
