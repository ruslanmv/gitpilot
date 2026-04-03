/**
 * Shared types and interfaces for all LLM providers.
 *
 * Every provider (OpenAI, Anthropic, Ollama, OllaBridge) implements
 * the LLMProvider interface so the agent loop is provider-agnostic.
 */

// ---------------------------------------------------------------------------
// Message types (conversation history)
// ---------------------------------------------------------------------------

export interface TextContent {
    type: 'text';
    text: string;
}

export interface ToolCallContent {
    type: 'tool_call';
    id: string;
    name: string;
    arguments: string; // JSON string
}

export interface ToolResultContent {
    type: 'tool_result';
    tool_call_id: string;
    content: string;
    is_error?: boolean;
}

export type MessageContent = TextContent | ToolCallContent | ToolResultContent;

export interface Message {
    role: 'system' | 'user' | 'assistant' | 'tool';
    content: string | MessageContent[];
}

// ---------------------------------------------------------------------------
// Tool definitions (sent to the LLM)
// ---------------------------------------------------------------------------

export interface ToolParameter {
    type: string;
    description: string;
    enum?: string[];
}

export interface ToolDefinition {
    name: string;
    description: string;
    parameters: {
        type: 'object';
        properties: Record<string, ToolParameter>;
        required?: string[];
    };
}

// ---------------------------------------------------------------------------
// Stream chunks (yielded by the provider during streaming)
// ---------------------------------------------------------------------------

export interface TextDelta {
    type: 'text_delta';
    text: string;
}

export interface ToolCallStart {
    type: 'tool_call_start';
    id: string;
    name: string;
}

export interface ToolCallDelta {
    type: 'tool_call_delta';
    id: string;
    arguments_delta: string;
}

export interface ToolCallEnd {
    type: 'tool_call_end';
    id: string;
}

export interface StreamDone {
    type: 'done';
    usage?: { prompt_tokens: number; completion_tokens: number };
}

export interface StreamError {
    type: 'error';
    error: string;
}

export type StreamChunk =
    | TextDelta
    | ToolCallStart
    | ToolCallDelta
    | ToolCallEnd
    | StreamDone
    | StreamError;

// ---------------------------------------------------------------------------
// Provider interface
// ---------------------------------------------------------------------------

export interface LLMProviderOptions {
    apiKey?: string;
    baseUrl?: string;
    model: string;
    maxTokens?: number;
    temperature?: number;
}

export interface LLMProvider {
    readonly name: string;

    /**
     * Stream a chat completion with optional tool use.
     * Yields StreamChunk events that the agent loop consumes.
     */
    streamChat(
        messages: Message[],
        tools: ToolDefinition[],
        signal: AbortSignal,
        options?: Partial<LLMProviderOptions>,
    ): AsyncIterable<StreamChunk>;
}

// ---------------------------------------------------------------------------
// Agent loop event types (consumed by the chat UI)
// ---------------------------------------------------------------------------

export interface AgentTextEvent {
    type: 'agent_text';
    text: string;
}

export interface AgentToolStartEvent {
    type: 'agent_tool_start';
    id: string;
    name: string;
    arguments: Record<string, unknown>;
}

export interface AgentToolResultEvent {
    type: 'agent_tool_result';
    id: string;
    name: string;
    result: string;
    is_error: boolean;
}

export interface AgentFileWriteEvent {
    type: 'agent_file_write';
    path: string;
    content: string;
    originalContent?: string;
    approved?: boolean;
}

export interface AgentDoneEvent {
    type: 'agent_done';
    usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

export interface AgentErrorEvent {
    type: 'agent_error';
    error: string;
}

export type AgentEvent =
    | AgentTextEvent
    | AgentToolStartEvent
    | AgentToolResultEvent
    | AgentFileWriteEvent
    | AgentDoneEvent
    | AgentErrorEvent;
