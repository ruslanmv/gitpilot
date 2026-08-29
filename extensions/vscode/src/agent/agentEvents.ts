/**
 * The agent event union the chat UI renders — Batch V4-H5.
 *
 * These types used to live in `src/local/providers/interface.ts`, alongside an
 * in-extension LLM client and agent loop that the extension stopped using once the
 * backend grew a real one. That whole tree is gone; the *types* survive because the
 * webview still renders these shapes, and they were the only thing anybody outside
 * `src/local/` imported from it.
 *
 * Kept deliberately narrow: this is the vocabulary of the v2 event stream as the
 * webview consumes it, not a model of the backend's engine. `agentEventBus.ts`
 * extends it with the events Batch V4-E5 added.
 */

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
