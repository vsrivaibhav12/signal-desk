"""Errors shared by every price source (Kotak Neo, Fyers)."""


class BrokerError(Exception):
    pass


class AuthError(BrokerError):
    """The broker turned down our credentials or session."""
