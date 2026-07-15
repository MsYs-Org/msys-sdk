"""Framework-neutral application-side navigation contract helpers."""

from __future__ import annotations

from typing import Any, Callable


APPLICATION_NAVIGATION_INTERFACE = "org.msys.application-navigation.v1"
NAVIGATION_BACK_METHOD = "navigation_back"

NavigationBackCallback = Callable[[], bool]


def application_navigation_handler(
    navigate_back: NavigationBackCallback,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build an inbound mIPC handler for application-local Back navigation.

    ``navigate_back`` must return true only when it consumed Back inside the
    application. A false result lets the window manager restore the previous
    task or Home. UI toolkits which require main-thread access should marshal
    the callback to that thread before returning its result.
    """

    if not callable(navigate_back):
        raise TypeError("navigate_back must be callable")

    def handle(message: dict[str, Any]) -> dict[str, Any]:
        if str(message.get("method") or "") != NAVIGATION_BACK_METHOD:
            return {"handled": False, "reason": "method-not-supported"}
        return {"handled": navigate_back() is True}

    return handle
