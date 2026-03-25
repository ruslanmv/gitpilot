/**
 * LLM Client facade — selects the right provider based on configuration.
 *
 * This is the single entry point for the extension to get an LLM provider.
 * Supports: OpenAI, OllaBridge, Claude/Anthropic, Ollama.
 */

import type { LLMProvider, LLMProviderOptions } from './providers/interface';
import { OpenAIProvider } from './providers/openai';
import { AnthropicProvider } from './providers/anthropic';

export type ProviderName = 'openai' | 'ollabridge' | 'claude' | 'ollama';

export interface LLMClientConfig {
    provider: ProviderName;
    model: string;
    apiKey?: string;
    baseUrl?: string;
    maxTokens?: number;
    temperature?: number;
}

// Default base URLs per provider
const DEFAULT_URLS: Record<ProviderName, string> = {
    openai: 'https://api.openai.com/v1',
    ollabridge: 'https://ruslanmv-ollabridge.hf.space/ollama/v1',
    claude: 'https://api.anthropic.com',
    ollama: 'http://localhost:11434/v1',
};

// Default models per provider
const DEFAULT_MODELS: Record<ProviderName, string> = {
    openai: 'gpt-4o-mini',
    ollabridge: 'qwen2.5:1.5b',
    claude: 'claude-sonnet-4-5-20250514',
    ollama: 'llama3',
};

/**
 * Create an LLM provider instance based on configuration.
 */
export function createProvider(config: LLMClientConfig): LLMProvider {
    const opts: LLMProviderOptions = {
        apiKey: config.apiKey,
        baseUrl: config.baseUrl || DEFAULT_URLS[config.provider],
        model: config.model || DEFAULT_MODELS[config.provider],
        maxTokens: config.maxTokens || 4096,
        temperature: config.temperature,
    };

    switch (config.provider) {
        case 'claude':
            return new AnthropicProvider(opts);

        case 'openai':
            return new OpenAIProvider(opts, 'openai');

        case 'ollabridge':
            // OllaBridge exposes an OpenAI-compatible API
            return new OpenAIProvider(opts, 'ollabridge');

        case 'ollama':
            // Ollama also exposes an OpenAI-compatible API at /v1
            return new OpenAIProvider(opts, 'ollama');

        default:
            throw new Error(`Unsupported provider: ${config.provider}`);
    }
}

/**
 * Validate provider configuration and return any issues.
 */
export function validateConfig(config: LLMClientConfig): string[] {
    const issues: string[] = [];

    if (!config.provider) {
        issues.push('No LLM provider selected');
    }

    if (!config.model) {
        issues.push('No model specified');
    }

    if (config.provider === 'openai' && !config.apiKey) {
        issues.push('OpenAI requires an API key');
    }

    if (config.provider === 'claude' && !config.apiKey) {
        issues.push('Claude requires an API key');
    }

    // OllaBridge and Ollama don't require API keys

    return issues;
}
