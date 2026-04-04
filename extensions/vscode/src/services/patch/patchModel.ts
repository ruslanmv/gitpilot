import type { ProposedEdit } from "../../core/types";

export interface PatchApplyResult {
  success: boolean;
  appliedFiles: string[];
  failedFiles: Array<{ file: string; reason: string }>;
}

export interface StoredOriginalFile {
  file: string;
  existed: boolean;
  content?: Uint8Array;
}

export type ParsedPatch = ProposedEdit;
