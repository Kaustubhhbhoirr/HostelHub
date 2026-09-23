"""
firebase_accounts.py — The sign-in accounts themselves (Firebase Authentication).

HostelHub keeps no passwords. When the warden adds a student, the account is
created in Firebase Authentication with the Admin SDK, and the student can then
sign in with that email and password or with Google (Firebase links the two
because they share the address).

Firestore keeps who the person is (name, role, room); Firebase Authentication
keeps how they prove it.
"""

from flask import current_app


class AccountError(Exception):
    """The Firebase Authentication account could not be created or changed."""


def firebase_auth():
    try:
        import firebase_admin
        from firebase_admin import auth
    except ImportError as error:                      # pragma: no cover - listed in requirements.txt
        raise AccountError("firebase-admin is not installed.") from error
    if not firebase_admin._apps:
        raise AccountError("Firebase is not configured on this server.")
    return auth


def find_account(email):
    """The Firebase account for this email address, or None."""
    auth = firebase_auth()
    try:
        return auth.get_user_by_email(email)
    except auth.UserNotFoundError:
        return None
    except Exception as error:                        # network or permission problem
        raise AccountError(f"Firebase Authentication is unreachable ({type(error).__name__}).") from error


def create_account(email, password, name=None):
    """Create the sign-in account and return (uid, created).

    `created` is False when Firebase already knew the address (for example the
    person has signed in with Google before): the password is then added to that
    same account instead, so the person never ends up with two accounts.

    The address is marked as verified because the hostel office vouches for it.
    This matters for linking: when an account with an UNVERIFIED address later
    signs in with Google, Firebase drops its password, while a verified account
    simply gains Google as a second way to sign in.
    """
    auth = firebase_auth()
    existing = find_account(email)
    try:
        if existing is not None:
            auth.update_user(existing.uid, password=password, email_verified=True,
                             display_name=name or existing.display_name)
            return existing.uid, False
        account = auth.create_user(email=email, password=password, display_name=name, email_verified=True)
        return account.uid, True
    except auth.EmailAlreadyExistsError as error:     # pragma: no cover - handled above
        raise AccountError("That email address is already in use.") from error
    except ValueError as error:
        raise AccountError(str(error)) from error
    except Exception as error:
        raise AccountError(f"Firebase Authentication refused the account ({type(error).__name__}).") from error


def update_account(uid, email=None, password=None, name=None):
    """Change the email, password or name of an existing account (warden's edit form).

    Returns the uid. If the account no longer exists in Firebase, a new one is
    created when a password is given.
    """
    auth = firebase_auth()
    changes = {key: value for key, value in (("email", email), ("password", password),
                                              ("display_name", name)) if value}
    if email:
        changes["email_verified"] = True
    try:
        if uid:
            try:
                auth.update_user(uid, **changes)
                return uid
            except auth.UserNotFoundError:
                pass
        existing = find_account(email) if email else None
        if existing is not None:
            auth.update_user(existing.uid, **changes)
            return existing.uid
        if not password:
            raise AccountError("This student has no sign-in account yet. Enter a password to create one.")
        return auth.create_user(email=email, password=password, display_name=name, email_verified=True).uid
    except AccountError:
        raise
    except auth.EmailAlreadyExistsError as error:
        raise AccountError("Another sign-in account already uses this email address.") from error
    except ValueError as error:
        raise AccountError(str(error)) from error
    except Exception as error:
        raise AccountError(f"The sign-in account could not be changed ({type(error).__name__}).") from error


def set_enabled(email, enabled):
    """Enable or disable signing in (used when a student is deactivated).

    Best effort: HostelHub also refuses deactivated accounts itself, so a Firebase
    problem here is logged instead of stopping the warden's action.
    """
    try:
        account = find_account(email)
        if account is not None:
            firebase_auth().update_user(account.uid, disabled=not enabled)
    except (AccountError, Exception) as error:
        current_app.logger.warning("Could not %s the Firebase account for %s: %s",
                                   "enable" if enabled else "disable", email, error)


def delete_account(email):
    """Remove the sign-in account (used when a student record is deleted)."""
    try:
        account = find_account(email)
        if account is not None:
            firebase_auth().delete_user(account.uid)
    except (AccountError, Exception) as error:
        current_app.logger.warning("Could not delete the Firebase account for %s: %s", email, error)
