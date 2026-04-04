import type { ParsedPatch } from "./patchModel";
import type { ProposedEdit } from "../../core/types";

export class PatchParser {
  fromEdits(edits: ProposedEdit[]): ParsedPatch[] {
    return edits;
  }
}
