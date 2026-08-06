export function fetchWithConfig<T = any>(
    url: string,
    method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH',
    body?: Record<string, any> | null
): Promise<T> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const controller = new AbortController();
    const signal = controller.signal;

    const config: RequestInit = {
        method,
        signal,
        headers
    };

    if (body) {
        config.body = JSON.stringify(body);
    }

    const result_promise = fetch(url, config)
        .then(async (response: Response) => {
            if (!response.ok) {
                // Try to extract FastAPI's JSON error detail; fall back to raw text.
                let detail = '';
                try {
                    const body = await response.clone().json();
                    detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body);
                } catch {
                    try {
                        detail = await response.text();
                    } catch {
                        detail = '';
                    }
                }
                throw new Error(
                    `HTTP ${response.status}${detail ? `: ${detail}` : ''}`
                );
            }
            return response.json() as Promise<T>;
        })
        .catch((error: unknown) => {
            const errorMessage = error instanceof Error ? error.message : 'Unknown error';
            console.error('Fetch error:', error);
            throw new Error(`Failed to fetch: ${errorMessage}`);
        });

    return result_promise;
}
