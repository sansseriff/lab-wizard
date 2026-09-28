/** One run, followed live over /api/live/runs/{id} (backend/live.py).
 *
 * The server watches the run in the lab database and sends what changed; this
 * applies it. The same messages describe a finished run, so a page showing the
 * last run and a page following a new one are the same page.
 */
import type { LineShape, PlotSpec, RunDetail, RunRow, Series, Step } from '$lib/data/model';

export type LivePlot = {
	name: string | null;
	spec: PlotSpec;
	series: Series[];
	units: Record<string, string | null>;
	shape: LineShape;
	error?: string;
};

type Message =
	| ({ type: 'run' } & RunDetail)
	| { type: 'status'; run: RunRow }
	| { type: 'steps'; steps: (Step & { id: number })[] }
	| { type: 'plots'; plots: LivePlot[] }
	| { type: 'end' }
	| { type: 'error'; message: string };

export class LiveRun {
	detail = $state<RunDetail | null>(null);
	run = $state<RunRow | null>(null);
	steps = $state<Step[]>([]);
	plots = $state<LivePlot[]>([]);
	ended = $state(false);
	error = $state('');
	/** Ticks while the run goes, so a running step's bar grows. */
	now = $state(Date.now());

	#socket: WebSocket | null = null;
	#index = new Map<number, number>();
	#clock: ReturnType<typeof setInterval> | undefined;
	#retry: ReturnType<typeof setTimeout> | undefined;
	#closed = false;

	constructor(readonly runId: number) {}

	get running(): boolean {
		return this.run?.status === 'running';
	}

	connect(): void {
		this.#closed = false;
		const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
		const socket = new WebSocket(`${scheme}://${location.host}/api/live/runs/${this.runId}`);
		this.#socket = socket;
		socket.onmessage = (event) => this.#apply(JSON.parse(event.data) as Message);
		socket.onclose = () => {
			// A dropped connection mid-run (a wizard restart) picks up again: the
			// server resends everything to a new connection.
			if (!this.#closed && !this.ended && !this.error) {
				this.#retry = setTimeout(() => {
					this.#reset();
					this.connect();
				}, 1000);
			}
		};
		clearInterval(this.#clock);
		this.#clock = setInterval(() => {
			if (this.running) this.now = Date.now();
		}, 500);
	}

	close(): void {
		this.#closed = true;
		clearInterval(this.#clock);
		clearTimeout(this.#retry);
		this.#socket?.close();
	}

	#reset(): void {
		this.steps = [];
		this.#index.clear();
	}

	#apply(message: Message): void {
		switch (message.type) {
			case 'run': {
				const { type: _type, ...detail } = message;
				this.detail = detail;
				this.run = detail.run;
				break;
			}
			case 'status':
				this.run = message.run;
				this.now = Date.now();
				break;
			case 'steps':
				for (const step of message.steps) {
					const at = this.#index.get(step.id);
					if (at === undefined) {
						this.#index.set(step.id, this.steps.length);
						this.steps.push(step);
					} else {
						this.steps[at] = step;
					}
				}
				break;
			case 'plots':
				this.plots = message.plots;
				break;
			case 'end':
				this.ended = true;
				this.now = Date.now();
				clearInterval(this.#clock);
				break;
			case 'error':
				this.error = message.message;
				break;
		}
	}
}
