import type { ProposedEdit } from "../../core/types";

export interface PatchApplyResult {
  success: boolean;
  appliedFiles: string[];
  failedFiles: Array<{ file: string; reason: string }>;
}

export type ParsedPatch = ProposedEdit;
