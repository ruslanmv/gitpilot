export class ProjectIndexCache<T> {
  private key?: string;
  private value?: T;

  get(cacheKey: string): T | undefined {
    if (this.key === cacheKey) {
      return this.value;
    }
    return undefined;
  }

  set(cacheKey: string, value: T): void {
    this.key = cacheKey;
    this.value = value;
  }

  clear(): void {
    this.key = undefined;
    this.value = undefined;
  }
}
