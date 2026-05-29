---
icon: lucide/server-cog
---

# Running & consuming servers

This page is the operational guide: how to run this workstation's instrument
server, and how to register and consume *other* machines' servers.

## Running the server (host side)

### From the GUI

`/manage_permissions` ("Server & Permissions") starts and stops the server via
[`server_control.py`](../../lab_wizard/wizard/backend/server_control.py). Two
lifecycle modes:

| Mode | Behavior |
|---|---|
| **managed** (default) | runs as a child of the wizard; closing the wizard stops it |
| **detached** | runs in its own session (`start_new_session`) and survives wizard exit, like a daemon |

A pid file (`config/server/.server.pid`) records the running server's pid, bind,
and mode, so status survives a wizard restart. `server_status` reconciles the pid
file with reality (a dead pid is cleaned up). Only one server per workstation
(one bind address) is expected; starting refuses if the port is already taken by
something else.

Relevant endpoints: `GET /api/server/status`, `POST /api/server/{start,stop,restart}`,
`GET /api/server/suggest-port`, `PUT /api/server/bind`. **Restart** is how you
apply edited [permission rules](permissions.md) — the server reads them at boot.

### From the command line

```bash
python -m lab_wizard.lib.server.server --config lab_wizard/config/server/server.yaml
# or the installed console script:
lab_wizard_server --config lab_wizard/config/server/server.yaml
```

### `server.yaml`

```yaml
server:
  bind: tcp://0.0.0.0:12300     # required: ZMQ bind address
  # config_dir: ..              # optional; defaults to the parent of server/
  # exp_yaml: ../../projects/foo/foo.yaml   # optional single-project override
permissions:                    # optional; authored by the GUI
  state_defaults: { ... }
  rules: [ ... ]
```

On boot the server logs every registered `inst://` path (using static metadata —
no hardware opened), every named attribute, and every loaded permission rule.

## Consuming remote servers (client side)

`/manage_remote_servers` maintains this machine's **address book** of servers it
wants to *use*, in `config/remote/servers.yaml`, via
[`remote_servers.py`](../../lab_wizard/wizard/backend/remote_servers.py):

```yaml
servers:
  - name: cryo-rack
    url: tcp://10.0.0.5:12300
```

| Endpoint | Effect |
|---|---|
| `GET /api/remote-servers` | list registered servers |
| `POST /api/remote-servers` | add (or update by name) |
| `DELETE /api/remote-servers/{name}` | remove |
| `POST /api/remote-servers/test` | live-test a URL: connects and lists its attributes (never raises) |

This address book is purely a client concern — it never feeds the server-side
permission gate.

### How remote instruments reach a measurement

During [measurement creation](../wizard/creating-measurements.md), the wizard
calls `list_remote_attributes` to enumerate the named attributes on every
reachable registered server, each tagged with its `behavior_abc`. Those are
offered as candidates for any matching requirement, alongside local instruments.
Selecting a remote attribute generates `from_attribute`-style setup code, and you
run the project with:

```bash
python <measurement>_setup.py --remote tcp://10.0.0.5:12300
```

The generated `RemoteResources.connect(url)` + `resources.from_attribute(name)` produces typed
proxies that satisfy the measurement's behavior ABCs — see
[Remote control](architecture.md).

!!! note "Server robustness is still basic"
    The server is currently a single-threaded poll loop with no per-connection
    concurrency, no graceful instrument shutdown, no client reconnect, and no
    hot-reload of permissions. These are tracked in the [Roadmap](../roadmap.md).
