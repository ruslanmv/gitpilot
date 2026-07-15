import * as path from "path";
import * as fs from "fs";

export class PatchValidationService {
  resolveSafePath(workspaceRoot: string | undefined, candidate: string): string | undefined {
    if (!workspaceRoot || !candidate || path.isAbsolute(candidate)) {
      return undefined;
    }

    const root = path.resolve(workspaceRoot);
    const target = path.resolve(root, candidate);
    const relative = path.relative(root, target);

    if (
      relative === "" ||
      relative === ".." ||
      relative.startsWith(`..${path.sep}`) ||
      path.isAbsolute(relative)
    ) {
      return undefined;
    }

    const realRoot = this.safeRealpath(root);
    if (!realRoot) {
      return undefined;
    }

    const realTarget = this.resolveExistingRealpath(target);
    if (!realTarget || !this.isWithinRoot(realRoot, realTarget)) {
      return undefined;
    }

    return target;
  }

  validateFilePath(workspaceRoot: string | undefined, candidate: string): boolean {
    return Boolean(this.resolveSafePath(workspaceRoot, candidate));
  }

  private isWithinRoot(root: string, target: string): boolean {
    const relative = path.relative(root, target);
    return (
      relative !== "" &&
      relative !== ".." &&
      !relative.startsWith(`..${path.sep}`) &&
      !path.isAbsolute(relative)
    );
  }

  private safeRealpath(target: string): string | undefined {
    try {
      return fs.realpathSync.native(target);
    } catch {
      return undefined;
    }
  }

  private resolveExistingRealpath(target: string): string | undefined {
    let current = target;
    const missingSegments: string[] = [];

    while (!fs.existsSync(current)) {
      const parent = path.dirname(current);
      if (parent === current) {
        return undefined;
      }

      missingSegments.unshift(path.basename(current));
      current = parent;
    }

    const realExistingPath = this.safeRealpath(current);
    if (!realExistingPath) {
      return undefined;
    }

    return path.resolve(realExistingPath, ...missingSegments);
  }
}
