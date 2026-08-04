import * as path from "path";

export class PatchValidationService {
  validateFilePath(workspaceRoot: string | undefined, candidate: string): boolean {
    if (!workspaceRoot) return false;
    const target = path.resolve(workspaceRoot, candidate);
    return target.startsWith(path.resolve(workspaceRoot));
  }
}
