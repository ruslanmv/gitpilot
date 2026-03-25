/**
 * Agent loop — the core agentic cycle that ties LLM + tools together.
 *
 * Flow:
 * 1. Build system prompt with workspace context
 * 2. Send user message + conversation history + tools to LLM
 * 3. Stream the LLM response
 * 4. If LLM calls tools → execute them → append results → go to step 2
 * 5. If LLM returns text only → done
 *
 * Yields AgentEvent objects that the chat UI consumes for real-time rendering.
 * Supports cancellation via AbortSignal and has an iteration cap.
 */

import type {
    LLMProvider,
    Message,
    MessageContent,
    AgentEvent,
    StreamChunk,
    ToolCallContent,
    ToolResultContent,
} from './providers/interface';
import { TOOL_DEFINITIONS, ToolExecutor } from './toolEngine';
import { ContextBuilder } from './contextBuilder';

const MAX_ITERATIONS = 25;   // Max tool-use round-trips per user message
const MAX_TOOL_OUTPUT = 50_000; // Truncate tool output to ~12k tokens

export interface AgentLoopOptions {
    provider: LLMProvider;
    toolExecutor: ToolExecutor;
    contextBuilder: ContextBuilder;
    /** Optional callback for tool confirmation (write_file, run_command, etc.) */
    onConfirmTool?: (name: string, args: Record<string, unknown>) => Promise<boolean>;
}

export async function* runAgentLoop(
    userMessage: string,
    conversationHistory: Message[],
    options: AgentLoopOptions,
    signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
    const { provider, toolExecutor, contextBuilder, onConfirmTool } = options;

    // Build system prompt
    let systemPrompt: string;
    try {
        systemPrompt = await contextBuilder.buildSystemPrompt();
    } catch (err: any) {
        yield { type: 'agent_error', error: `Failed to build context: ${err.message}` };
        return;
    }

    // Assemble full message history
    const messages: Message[] = [
        { role: 'system', content: systemPrompt },
        ...conversationHistory,
        { role: 'user', content: userMessage },
    ];

    let iteration = 0;

    while (iteration < MAX_ITERATIONS && !signal.aborted) {
        iteration++;

        // Collect the LLM response (text + tool calls)
        let responseText = '';
        const toolCalls: Map<string, { name: string; arguments: string }> = new Map();

        try {
            for await (const chunk of provider.streamChat(messages, TOOL_DEFINITIONS, signal)) {
                if (signal.aborted) break;

                switch (chunk.type) {
                    case 'text_delta':
                        responseText += chunk.text;
                        yield { type: 'agent_text', text: chunk.text };
                        break;

                    case 'tool_call_start':
                        toolCalls.set(chunk.id, { name: chunk.name, arguments: '' });
                        break;

                    case 'tool_call_delta': {
                        const tc = toolCalls.get(chunk.id);
                        if (tc) tc.arguments += chunk.arguments_delta;
                        break;
                    }

                    case 'tool_call_end':
                        // Tool call fully assembled, will execute below
                        break;

                    case 'error':
                        yield { type: 'agent_error', error: chunk.error };
                        return;

                    case 'done':
                        if (chunk.usage) {
                            yield {
                                type: 'agent_done',
                                usage: {
                                    prompt_tokens: chunk.usage.prompt_tokens,
                                    completion_tokens: chunk.usage.completion_tokens,
                                    total_tokens: chunk.usage.prompt_tokens + chunk.usage.completion_tokens,
                                },
                            };
                        }
                        break;
                }
            }
        } catch (err: any) {
            if (signal.aborted) break;
            yield { type: 'agent_error', error: `LLM request failed: ${err.message}` };
            return;
        }

        if (signal.aborted) break;

        // If no tool calls, we're done — the LLM gave a final text response
        if (toolCalls.size === 0) {
            if (!responseText) {
                yield { type: 'agent_done' };
            }
            return;
        }

        // Build the assistant message with text + tool calls
        const assistantContent: MessageContent[] = [];
        if (responseText) {
            assistantContent.push({ type: 'text', text: responseText });
        }
        for (const [id, tc] of toolCalls) {
            assistantContent.push({
                type: 'tool_call',
                id,
                name: tc.name,
                arguments: tc.arguments,
            } as ToolCallContent);
        }
        messages.push({ role: 'assistant', content: assistantContent });

        // Execute each tool call
        const toolResults: ToolResultContent[] = [];

        for (const [id, tc] of toolCalls) {
            if (signal.aborted) break;

            let parsedArgs: Record<string, unknown> = {};
            try {
                parsedArgs = tc.arguments ? JSON.parse(tc.arguments) : {};
            } catch {
                parsedArgs = {};
            }

            // Emit tool start event
            yield {
                type: 'agent_tool_start',
                id,
                name: tc.name,
                arguments: parsedArgs,
            };

            // Check if tool needs confirmation
            if (onConfirmTool && requiresConfirmation(tc.name)) {
                const confirmed = await onConfirmTool(tc.name, parsedArgs);
                if (!confirmed) {
                    const result = 'Tool execution cancelled by user.';
                    toolResults.push({
                        type: 'tool_result',
                        tool_call_id: id,
                        content: result,
                        is_error: true,
                    });
                    yield { type: 'agent_tool_result', id, name: tc.name, result, is_error: true };
                    continue;
                }
            }

            // Execute the tool
            let result: string;
            let isError = false;

            try {
                result = await toolExecutor.execute(tc.name, parsedArgs);
            } catch (err: any) {
                result = `Error: ${err.message}`;
                isError = true;
            }

            // Truncate large output
            if (result.length > MAX_TOOL_OUTPUT) {
                result = result.slice(0, MAX_TOOL_OUTPUT) + `\n\n... [output truncated, ${result.length} total chars]`;
            }

            toolResults.push({
                type: 'tool_result',
                tool_call_id: id,
                content: result,
                is_error: isError,
            });

            yield { type: 'agent_tool_result', id, name: tc.name, result, is_error: isError };

            // For write/edit operations, also emit a file write event
            if ((tc.name === 'write_file' || tc.name === 'edit_file') && !isError) {
                yield {
                    type: 'agent_file_write',
                    path: String(parsedArgs.path || ''),
                    content: String(parsedArgs.content || parsedArgs.new_text || ''),
                };
            }
        }

        if (signal.aborted) break;

        // Append tool results to messages for the next iteration
        messages.push({
            role: 'tool',
            content: toolResults,
        });

        // Loop continues — LLM will see tool results and decide what to do next
    }

    if (iteration >= MAX_ITERATIONS) {
        yield {
            type: 'agent_error',
            error: `Reached maximum iterations (${MAX_ITERATIONS}). Stopping to prevent runaway execution.`,
        };
    }

    yield { type: 'agent_done' };
}

function requiresConfirmation(toolName: string): boolean {
    // In auto mode, no confirmation needed; this is checked upstream
    return ['run_command', 'git_commit'].includes(toolName);
}
