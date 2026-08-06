"""Minimal CRM demo service for ProjectSpace switch tests."""


def lookup_contact(email: str) -> dict[str, str]:
    """Look up a CRM contact by email.

    route: GET /contacts
    """
    return {"email": email, "status": "active"}
