"""The wizard's HTTP API, one router per section of the GUI."""

from lab_wizard.wizard.backend.routes import (
    data,
    instruments,
    live,
    measurements,
    procedures,
    runs,
    servers,
    settings,
)

ROUTERS = [
    measurements.router,
    procedures.router,
    runs.router,
    instruments.router,
    servers.router,
    data.router,
    live.router,
    settings.router,
]
