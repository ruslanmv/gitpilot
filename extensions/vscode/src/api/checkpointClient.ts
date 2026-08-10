/**
 * Checkpoints — the undo behind Agent mode.
 *
 * GitPilot snapshots the workspace and the conversation before every mutating
 * tool call, so a run that goes wrong is recoverable without reaching for git.
 * Rewinding restores both halves: files alone would leave the model reasoning
 * about edits that no longer exist.
 */
import { GitPilotApiClient } from "./client";

export interface Checkpoint {
  id: string;
  description: string;
  /** ISO-8601, from the backend. */
  timestamp: string;
  /** How many messages the conversation had when this was taken. */
  message_index: number;
  /** The tool this checkpoint was taken in front of, or "manual". */
  tool_name: string;
  target_path?: string | null;
  /**
   * False when the workspace was too large to snapshot. The conversation can
   * still be rewound; the files cannot, and the UI must say so rather than
   * offering a restore it cannot perform.
   */
  has_files: boolean;
}

export interface RewindResult {
  session_id: string;
  messages: Array<{ role: string; content: string; timestamp?: string }>;
  checkpoints: Checkpoint[];
}

export class CheckpointClient {
  constructor(private client: GitPilotApiClient) {}

  /** Newest first, as the backend returns them. */
  async list(sessionId: string): Promise<Checkpoint[]> {
    const data = await this.client.get<{ checkpoints: Checkpoint[] }>(
      `/api/sessions/${sessionId}/checkpoints`
    );
    return data.checkpoints || [];
  }

  async create(sessionId: string, description: string): Promise<Checkpoint> {
    return this.client.post<Checkpoint>(
      `/api/sessions/${sessionId}/checkpoint`,
      { description }
    );
  }

  async rewind(sessionId: string, checkpointId: string): Promise<RewindResult> {
    return this.client.post<RewindResult>(`/api/sessions/${sessionId}/rewind`, {
      checkpoint_id: checkpointId,
    });
  }
}
