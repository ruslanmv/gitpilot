/**
 * Anthropic / Claude provider with streaming + tool use.
 *
 * Uses the Anthropic Messages API with native fetch + manual SSE parsing.
 * Converts between our generic types and the Anthropic wire format.
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
// Helpers: convert generic types → Anthropic wire format
// ---------------------------------------------------------------------------

interface AnthropicMessage {
    role: 'user' | 'assistant';
    content: string | AnthropicContentBlock[];
}

type AnthropicContentBlock =
    | { type: 'text'; text: string }
    | { type: 'tool_use'; id: string; name: string; input: any }
    | { type: 'tool_result'; tool_use_id: string; content: string; is_error?: boolean };

function toAnthropicMessages(messages: Message[]): {
    system?: string;
    messages: AnthropicMessage[];
} {
    let system: string | undefined;
    const out: AnthropicMessage[] = [];

    for (const m of messages) {
        // Extract system prompt
        if (m.role === 'system') {
            system = typeof m.content === 'string'
                ? m.content
                : (m.content as MessageContent[]).filter(c => c.type === 'text').map(c => (c as any).text).join('\n');
            continue;
        }

        if (typeof m.content === 'string') {
            out.push({ role: m.role as 'user' | 'assistant', content: m.content });
            continue;
        }

        // Array content
        const blocks: AnthropicContentBlock[] = [];
        for (const part of m.content as MessageContent[]) {
            if (part.type === 'text') {
                blocks.push({ type: 'text', text: part.text });
            } else if (part.type === 'tool_call') {
                let input: any = {};
                try { input = JSON.parse(part.arguments); } catch { /* empty */ }
                blocks.push({ type: 'tool_use', id: part.id, name: part.name, input });
            } else if (part.type === 'tool_result') {
                blocks.push({
                    type: 'tool_result',
                    tool_use_id: part.tool_call_id,
                    content: part.content,
                    is_error: part.is_error,
                });
            }
        }

        if (blocks.length > 0) {
            // Tool results must be role=user in Anthropic's format
            const hasToolResult = blocks.some(b => b.type === 'tool_result');
            out.push({
                role: hasToolResult ? 'user' : (m.role as 'user' | 'assistant'),
                content: blocks,
            });
        }
    }

    return { system, messages: out };
}

function toAnthropicTools(tools: ToolDefinition[]): any[] {
    return tools.map(t => ({
        name: t.name,
        description: t.description,
        input_schema: t.parameters,
    }));
}

// ---------------------------------------------------------------------------
// SSE parser for Anthropic's streaming format
// ---------------------------------------------------------------------------

async function* parseAnthropicSSE(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    signal: AbortSignal,
): AsyncIterable<{ event: string; data: any }> {
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';

    while (!signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            const trimmed = line.trim();

            if (trimmed.startsWith('event: ')) {
                currentEvent = trimmed.slice(7);
            } else if (trimmed.startsWith('data: ')) {
                const data = trimmed.slice(6);
                try {
                    yield { event: currentEvent, data: JSON.parse(data) };
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

export class AnthropicProvider implements LLMProvider {
    readonly name = 'anthropic';
    private _opts: LLMProviderOptions;

    constructor(opts: LLMProviderOptions) {
        this._opts = opts;
    }

    async *streamChat(
        messages: Message[],
        tools: ToolDefinition[],
        signal: AbortSignal,
    ): AsyncIterable<StreamChunk> {
        const baseUrl = (this._opts.baseUrl || 'https://api.anthropic.com').replace(/\/$/, '');

        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01',
        };
        if (this._opts.apiKey) {
            headers['x-api-key'] = this._opts.apiKey;
        }

        const { system, messages: anthropicMsgs } = toAnthropicMessages(messages);

        const body: any = {
            model: this._opts.model,
            messages: anthropicMsgs,
            max_tokens: this._opts.maxTokens || 4096,
            stream: true,
        };

        if (system) {
            body.system = system;
        }

        if (this._opts.temperature !== undefined) {
            body.temperature = this._opts.temperature;
        }

        if (tools.length > 0) {
            body.tools = toAnthropicTools(tools);
        }

        const res = await fetch(`${baseUrl}/v1/messages`, {
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
        let currentToolId = '';
        let currentToolName = '';

        for await (const { event, data } of parseAnthropicSSE(reader, signal)) {
            if (signal.aborted) break;

            switch (event) {
                case 'content_block_start': {
                    const block = data.content_block;
                    if (block?.type === 'tool_use') {
                        currentToolId = block.id;
                        currentToolName = block.name;
                        yield { type: 'tool_call_start', id: block.id, name: block.name };
                    }
                    break;
                }

                case 'content_block_delta': {
                    const delta = data.delta;
                    if (delta?.type === 'text_delta') {
                        yield { type: 'text_delta', text: delta.text };
                    } else if (delta?.type === 'input_json_delta') {
                        yield {
                            type: 'tool_call_delta',
                            id: currentToolId,
                            arguments_delta: delta.partial_json,
                        };
                    }
                    break;
                }

                case 'content_block_stop': {
                    if (currentToolId) {
                        yield { type: 'tool_call_end', id: currentToolId };
                        currentToolId = '';
                        currentToolName = '';
                    }
                    break;
                }

                case 'message_delta': {
                    // May contain usage info
                    break;
                }

                case 'message_stop': {
                    const usage = data.usage || data.message?.usage;
                    yield {
                        type: 'done',
                        usage: usage
                            ? { prompt_tokens: usage.input_tokens, completion_tokens: usage.output_tokens }
                            : undefined,
                    };
                    return;
                }

                case 'error': {
                    yield { type: 'error', error: data.error?.message || 'Unknown Anthropic error' };
                    return;
                }
            }
        }

        yield { type: 'done' };
    }
}
