/**
 * OpenAI-compatible provider — works with OpenAI, OllaBridge, and Ollama.
 *
 * Uses native fetch + manual SSE parsing (no heavy dependencies).
 * Supports streaming chat completions with tool use.
 */

import type {
    LLMProvider,
    LLMProviderOptions,
    Message,
    ToolDefinition,
    StreamChunk,
    MessageContent,
} from './interface';

// ---------------------------------------------------------------------------
// Helpers: convert our generic types to the OpenAI wire format
// ---------------------------------------------------------------------------

interface OAIMessage {
    role: string;
    content?: string | null;
    tool_calls?: Array<{
        id: string;
        type: 'function';
        function: { name: string; arguments: string };
    }>;
    tool_call_id?: string;
}

function toOpenAIMessages(messages: Message[]): OAIMessage[] {
    const out: OAIMessage[] = [];

    for (const m of messages) {
        if (typeof m.content === 'string') {
            out.push({ role: m.role, content: m.content });
            continue;
        }

        // Array content — split tool_calls into assistant message + tool results
        const texts: string[] = [];
        const toolCalls: OAIMessage['tool_calls'] = [];
        const toolResults: OAIMessage[] = [];

        for (const part of m.content as MessageContent[]) {
            if (part.type === 'text') {
                texts.push(part.text);
            } else if (part.type === 'tool_call') {
                toolCalls.push({
                    id: part.id,
                    type: 'function',
                    function: { name: part.name, arguments: part.arguments },
                });
            } else if (part.type === 'tool_result') {
                toolResults.push({
                    role: 'tool',
                    tool_call_id: part.tool_call_id,
                    content: part.content,
                });
            }
        }

        if (toolCalls.length > 0) {
            out.push({
                role: 'assistant',
                content: texts.length > 0 ? texts.join('') : null,
                tool_calls: toolCalls,
            });
        } else if (texts.length > 0) {
            out.push({ role: m.role, content: texts.join('') });
        }

        out.push(...toolResults);
    }

    return out;
}

function toOpenAITools(tools: ToolDefinition[]): any[] {
    return tools.map((t) => ({
        type: 'function',
        function: {
            name: t.name,
            description: t.description,
            parameters: t.parameters,
        },
    }));
}

// ---------------------------------------------------------------------------
// SSE line parser
// ---------------------------------------------------------------------------

async function* parseSSE(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    signal: AbortSignal,
): AsyncIterable<any> {
    const decoder = new TextDecoder();
    let buffer = '';

    while (!signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith(':')) continue;

            if (trimmed.startsWith('data: ')) {
                const data = trimmed.slice(6);
                if (data === '[DONE]') return;
                try {
                    yield JSON.parse(data);
                } catch {
                    // Skip malformed JSON
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class OpenAIProvider implements LLMProvider {
    readonly name: string;
    private _opts: LLMProviderOptions;

    constructor(opts: LLMProviderOptions, name = 'openai') {
        this._opts = opts;
        this.name = name;
    }

    async *streamChat(
        messages: Message[],
        tools: ToolDefinition[],
        signal: AbortSignal,
    ): AsyncIterable<StreamChunk> {
        const baseUrl = (this._opts.baseUrl || 'https://api.openai.com/v1').replace(/\/$/, '');

        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
        };
        if (this._opts.apiKey) {
            headers['Authorization'] = `Bearer ${this._opts.apiKey}`;
        }

        const body: any = {
            model: this._opts.model,
            messages: toOpenAIMessages(messages),
            stream: true,
            max_tokens: this._opts.maxTokens || 4096,
        };

        if (this._opts.temperature !== undefined) {
            body.temperature = this._opts.temperature;
        }

        if (tools.length > 0) {
            body.tools = toOpenAITools(tools);
        }

        const res = await fetch(`${baseUrl}/chat/completions`, {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
            signal,
        });

        if (!res.ok) {
            const errText = await res.text().catch(() => `HTTP ${res.status}`);
            yield { type: 'error', error: `${res.status}: ${errText}` };
            return;
        }

        if (!res.body) {
            yield { type: 'error', error: 'No response body' };
            return;
        }

        const reader = res.body.getReader();

        // Track tool calls being assembled across chunks
        const pendingToolCalls = new Map<
            number,
            { id: string; name: string; arguments: string }
        >();

        let totalUsage: StreamChunk['type'] extends 'done' ? any : undefined;

        for await (const chunk of parseSSE(reader, signal)) {
            if (signal.aborted) break;

            // Usage info (some providers include it)
            if (chunk.usage) {
                totalUsage = chunk.usage;
            }

            const delta = chunk.choices?.[0]?.delta;
            if (!delta) continue;

            // Text content
            if (delta.content) {
                yield { type: 'text_delta', text: delta.content };
            }

            // Tool calls (streamed incrementally)
            if (delta.tool_calls) {
                for (const tc of delta.tool_calls) {
                    const idx = tc.index ?? 0;

                    if (tc.id) {
                        // New tool call starting
                        pendingToolCalls.set(idx, {
                            id: tc.id,
                            name: tc.function?.name || '',
                            arguments: tc.function?.arguments || '',
                        });
                        if (tc.function?.name) {
                            yield { type: 'tool_call_start', id: tc.id, name: tc.function.name };
                        }
                    } else {
                        // Continuation of existing tool call
                        const existing = pendingToolCalls.get(idx);
                        if (existing) {
                            if (tc.function?.name) {
                                existing.name += tc.function.name;
                            }
                            if (tc.function?.arguments) {
                                existing.arguments += tc.function.arguments;
                                yield {
                                    type: 'tool_call_delta',
                                    id: existing.id,
                                    arguments_delta: tc.function.arguments,
                                };
                            }
                        }
                    }
                }
            }

            // Check for finish reason
            const finish = chunk.choices?.[0]?.finish_reason;
            if (finish === 'tool_calls' || finish === 'stop') {
                // Emit tool_call_end for all pending tool calls
                for (const [, tc] of pendingToolCalls) {
                    yield { type: 'tool_call_end', id: tc.id };
                }
                pendingToolCalls.clear();
            }
        }

        yield { type: 'done', usage: totalUsage };
    }
}
