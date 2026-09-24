# Running measurements, and plotting them live

> **Status: planned, nothing built.** `/api/projects` lists generated projects
> and the Projects page renders them, but nothing runs one: there is no launch
> endpoint and no run view. Both plotters
> ([`mpl_plotter.py`](../lab_wizard/lib/plotters/mpl_plotter.py),
> [`bokeh_plotter.py`](../lab_wizard/lib/plotters/bokeh_plotter.py)) are
> placeholders that store the last payload and print. Nothing outside the tests
> consumes the procedure's status bus, so there is no live progress view either.
>
> **This is a startup document**, written for an engineer or agent picking this
> up cold. The original brief is kept word for word in §11.
>
> It builds on [`semantic_data_plan.md`](semantic_data_plan.md), which defines
> the message stream a run emits (its §4), the point rows (§5), default `plots:`
> (§9) and the plot evaluator (§11). Do that plan's Phases 1–3 first.

---

## 1. What must work

| Case | How the run starts | What the user sees |
|---|---|---|
| **A** | **Run** button in the wizard | A Run page in the wizard: progress, live plots, log, Stop, and a link to the run in the Data viewer afterwards |
| **B** | `python <project>_setup.py` at the lab computer, with a **matplotlib** plotter configured | A native plot window updating live; no browser |
| **C** | Same, with the **web** plotter configured, **locally** | A window (pywebview) with the live web plot |
| **D** | Same, with the web plotter, **over SSH** | A printed link, and the `ssh -L` command that makes it reachable |

In every case the plot drawn is the procedure's **first `plots:` entry**
(semantic_data_plan §9), and the other entries are available as tabs where
there is a page to put tabs on. A plotter can name a different plot.

---

## 2. The decisions

| # | Decision | Why |
|---|---|---|
| R1 | **One wire format for the run's messages**: JSON, one object per bus message. | The Run page, the standalone web plot and any later tool all read the same thing. |
| R2 | **The run process serves its own event stream** on a local websocket when asked to (`EventPublisher` sink). | One mechanism for cases A, C and D. No second transport for the wizard. |
| R3 | **One live page, two hosts.** A single frontend route renders progress and plots from an event stream. The wizard serves it in case A (relaying the child's stream); the run process serves it in cases C and D. | The live view is written once. |
| R4 | **Plot series are computed in Python** by the shared plot evaluator and sent ready to draw. The browser and matplotlib only draw. | Derived columns and `where` filters behave identically live, afterwards in the viewer, and in the notebook. |
| R5 | **Wizard-launched runs are detached subprocesses**, found again after a wizard restart, and stopped with SIGINT. | A wizard crash must not kill a run mid-sweep; SIGINT already reaches the safe-state guards. |
| R6 | **In case A, a configured web plotter does nothing extra.** The Run page already shows the plots. | No duplicate windows. |
| R7 | **A window that needs the main thread runs there, and the procedure moves to a worker thread.** | macOS requires GUI windows (matplotlib, pywebview) on the main thread. |

---

## 3. The wire format

One JSON object per message, tagged with a version and a type:

```json
{"v":1,"type":"run_started","run_id":41,"procedure":"mcr_curve","device":"A7",
 "columns":{"attenuation_db":{"unit":"dB"},"count_rate":{"unit":"Hz"}},
 "plots":[{"name":"MCR","x":"attenuation_db","y":["count_rate"],"where":{"phase":"signal"}}]}
{"v":1,"type":"step_began","path":"sequence/…/sweep[0]/sequence#2","kind":"sequence","t":"…"}
{"v":1,"type":"step_progress","path":"sequence/…/wait[1]","fraction":0.4,"detail":null}
{"v":1,"type":"point","seq":3,"t":"…","steps":["…/count[2]","…/read_voltage[3]"],
 "values":{"phase":"signal","attenuation_db":5.0,"counts":12404,"count_rate":248080.0,"device_voltage":0.0}}
{"v":1,"type":"plot","name":"MCR","series":[{"label":"","x":[20.0,10.0,5.0],"y":[7740.0,80080.0,248080.0]}]}
{"v":1,"type":"step_ended","path":"…","status":"success","t":"…"}
{"v":1,"type":"run_ended","status":"success","t":"…"}
```

`plot` messages are not bus messages. The publisher produces them by running
the plot evaluator over the points so far, recomputing whole plots (not
appending), throttled to about 5 per second. Recomputing keeps per-run
reductions such as `max(...)` correct as the run grows. `run_id` comes from the
database recorder, which handles `RunStarted` first
(semantic_data_plan §4).

---

## 4. The `EventPublisher` sink

- Enabled when the environment has `LAB_WIZARD_EVENTS=127.0.0.1:<port>` (case A)
  or a web plotter is configured outside the wizard (cases C, D).
- Runs a small Starlette/uvicorn app on a background thread (FastAPI and uvicorn
  are already dependencies), with one websocket route, `/events`.
- **Replays on connect.** It keeps every message of the run, so a page opened
  mid-run, or reloaded, receives the history and then continues live. If the
  buffer grows large, `step_progress` messages are dropped from it first.
- Binds to `127.0.0.1` only, never `0.0.0.0`. Remote viewing goes through the
  wizard's proxy (case A) or an SSH tunnel (case D).
- In cases C and D it also serves the built frontend's live route (§5) as
  static files, from the same directory the wizard backend serves
  (`lab_wizard/wizard/backend/static`).

---

## 5. The live page and the plot component

- **`BokehPlot.svelte`**: one component that draws a list of series with
  BokehJS, replacing its `ColumnDataSource` data on each `plot` message. This is
  the `tag_gui` pattern (BokehJS in the browser fed over a websocket, see
  `tag_gui/frontend/src/components/CountRate.svelte`), not a Bokeh server. The
  Data viewer (semantic_data_plan Phase 7) uses the same component, so live and
  recorded plots look the same. `tag_gui` loads vendored `bokeh-3.0.1.min.js`
  files through script tags; try bundling a pinned BokehJS through Vite first,
  and vendor the scripts the same way if that fights the build.
- **`routes/live`**: takes the event stream's URL, renders a progress tree from
  the step messages, the plots as tabs, and the latest point's values. With no
  wizard backend present it must still work, because the run process serves it
  alone in cases C and D.
- **`routes/run/[project]`** (case A) wraps the live view with the controls in §6.

---

## 6. Case A: running from the wizard

**Launch.** `POST /api/projects/{name}/run`:

- chooses a free local port;
- starts `python <project>_setup.py` with the project directory as its working
  directory, `start_new_session=True` (detached, as `server_control.py` does
  for the server's detached mode), `PYTHONUNBUFFERED=1`, and
  `LAB_WIZARD_EVENTS=127.0.0.1:<port>`;
- sends stdout and stderr to `<logs_dir>/runs/<project>_<time>.log`;
- writes `<logs_dir>/runs/active/<pid>.json` (`pid`, `port`, `project`,
  `started_at`, and `run_id` once known), removed when the process exits.

**Re-attach.** On startup the wizard reads `runs/active/`, drops entries whose
pid is gone, and lists the rest as running.

**Relay.** `ws /api/runs/{pid}/events` proxies the child's `/events`, so a
browser on another machine reaches it through the wizard.

**Stop.** `POST /api/runs/{pid}/stop` sends SIGINT. The child sees a
`KeyboardInterrupt`; `RunLifecycle.run` catches `BaseException` and puts every
instrument in its safe state, and `SafeGuard` exits run in `finally` blocks
([lifecycle.py](../lab_wizard/lib/task_adapters/lifecycle.py)). A second Stop
after a timeout offers SIGKILL, with a warning that safe states will not run.
Never SIGKILL first.

**The Run page:**

- **Before launch:** the project's `run:` block (device, operator, notes) and
  `measurement.params`, editable with the params editors the procedure composer
  already has, saved to the project YAML before launch (§9, Q1).
- **During:** the live view (§5), the log tail, Stop.
- **After:** status, and "Open in Data viewer" for the recorded run (by `run_id`).

---

## 7. The plotters

Both plotters are sinks. Each takes a `plot` param naming a `plots:` entry
(empty means the first) and draws the evaluator's series. Neither touches raw
points.

### `MplPlotter`: a native window (case B)

- If the plotter is configured and a display is available, the generated
  entry point runs the procedure on a worker thread (`ProcedureRunner.start`
  exists) and keeps the main thread for matplotlib: a loop that drains the
  plotter's queue, redraws, and calls `plt.pause`.
- Ctrl-C lands on the main thread. It calls `runner.abort()`, joins the worker,
  and re-raises, so `RunLifecycle` still enters the safe state.
- When the run ends, the window stays open until it is closed (§9, Q2).
- With no display (SSH without X forwarding), it logs one warning and does
  nothing. The run is unaffected.

### `WebPlotter` (cases C and D)

- Launched by the wizard (`LAB_WIZARD_EVENTS` set): does nothing (R6).
- Otherwise it enables the `EventPublisher` and then either:
  - **locally**, opens the live page in a pywebview window (already a
    dependency; the wizard itself uses it in `backend/main.py`). That window
    takes the main thread, so the procedure moves to a worker thread exactly as
    for matplotlib. If a matplotlib window is also configured, only one can have
    the main thread, so the web plot opens in the default browser instead.
  - **over SSH** (`SSH_CONNECTION` is set): prints the URL and the tunnel
    command, for example
    `ssh -L 8765:127.0.0.1:8765 lab-computer`, then
    `http://127.0.0.1:8765/live`.
- After the run ends, the process keeps serving until the window is closed, or
  until Enter or Ctrl-C in the terminal case (§9, Q2).

The Bokeh `url` param (a Bokeh server address) goes away: there is no Bokeh
server in this design.

---

## 8. The work, in order

After semantic_data_plan Phases 1–3 (the stream, the recorder, the plot
evaluator). These phases can run alongside its Phases 4–5.

1. **Wire format and `EventPublisher`**: serialization of every message, the
   replay buffer, throttled `plot` messages. Tests: a client connecting mid-run
   receives the full history; `plot` series equal the evaluator's output on the
   same points.
2. **`BokehPlot.svelte` and `routes/live`**, working against a recorded event
   file so they can be built without hardware.
3. **`MplPlotter`**: the worker-thread entry point and Ctrl-C handling. Tests
   with a non-interactive backend: the plotter receives series; Ctrl-C mid-run
   records `aborted` and reaches the safe state.
4. **`WebPlotter`**: standalone serving, pywebview locally, the SSH message.
5. **Launching from the wizard**: the endpoint, the active-run registry, the
   relay, Stop. Tests: launch a simulated-rack project, read its events through
   the relay, stop it, and check the run is recorded as `aborted`.
6. **The Run page** and a Run button on the Projects page and at the end of
   Create Measurement.

Generated projects get `attach_sinks` (semantic_data_plan §4) and the threaded
entry point from codegen. Regenerating a project picks both up; nothing about
them lives in project-specific code.

---

## 9. Open decisions

1. **Params editing on the Run page.** (open) Proposed: edit the project YAML
   in place and save before launch. The run records what it ran with, so
   history is not lost. The alternative, per-run overrides that leave the YAML
   untouched, means the YAML stops describing what the project does.
2. **After the run, keep the plot window open?** (open) Proposed: yes, until
   it is closed. Add a `--no-wait` flag, and never wait when stdin is not a
   terminal.
3. **Queue runs?** (open) Proposed: not now. Instrument claims already refuse a
   second run that needs the same hardware; a queue can come later.

---

## 10. Traps

- **The main thread belongs to the window.** matplotlib and pywebview both need
  it on macOS; the procedure moves to a worker. Ctrl-C then arrives on the main
  thread and has to be passed on as `runner.abort()`.
- **`abort()` is cooperative.** A step blocked in a 10 s count finishes its
  count before the run stops. Stop must say "stopping…", not claim it stopped.
- **Ctrl-C records `failed` today.** `ProcedureRunner.run` catches `Exception`,
  not `BaseException` (semantic_data_plan Phase 1 fixes it).
- **Never SIGKILL first.** It skips every safe state.
- **Windows** has no SIGINT for another process group; use
  `CREATE_NEW_PROCESS_GROUP` and `CTRL_BREAK_EVENT`, and handle `SIGBREAK` in
  the entry point.
- **Bind to 127.0.0.1.** An event server on `0.0.0.0` exposes the run to the
  network.
- **Unbuffered output.** Without `PYTHONUNBUFFERED=1` the log tail lags by a
  buffer's worth.
- **Arrays in points.** A histogram rides along in each `point` message (about
  20 KB); fine. Scope traces at MB scale would need to be left out of `point`
  messages and only sent inside `plot` series.
- **A wizard restart mid-run** must find the run again (the active registry),
  not start a second one.

---

## 11. The original brief (kept as written)

You will notice that this repo/tool 'lab wizard' has a way of creating projects that are pre-written using a GUI that works to load instruments, database, and plotting systems using a yaml config tree for setting and parameter handling. 

Right now, the GUI does not support directly running of created projects after they have been created by workflows like the "create measurement" workflow. 

I would like to extend the GUI and backend systems to support running measurements. Either through a new button on the home page (like "View & Run a Project"), or as a final step in the Create Measurement flow. 

Notice that there are some bits of 'plotters' which are systems that take data emitted by a measurement and plot it somewhere. The idea is to have a local matplotlib plotter, and a web based plotter that uses the bokeh library underneath (see this repo for context about how to use bokeh as purely a frontend plotting library. I choose it because its a low setup high performance plotting library that uses webgl canvas: /Users/andrew/Documents/PROGRAM_LOCAL/tag_gui) 

Given all this, I would like to create a new webpage that can be accesible from (1) the main page following a "pick measurements" page and (2) at the end of the "create measurement". Keep in mind, for (1) you will need to make a new webpage that scans/scrapes the folders in the projects directory
