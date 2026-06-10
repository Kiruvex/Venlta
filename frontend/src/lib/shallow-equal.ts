export function shallowEqual<T>(a: T, b: T): boolean {
  if (a === b) return true;
  if (a !== a && b !== b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((val, i) => val === b[i]);
  }
  if (typeof a !== 'object' || a === null || typeof b !== 'object' || b === null) return false;
  const keysA = Object.keys(a as Record<string, any>);
  const keysB = Object.keys(b as Record<string, any>);
  if (keysA.length !== keysB.length) return false;
  for (const key of keysA) {
    if ((a as Record<string, any>)[key] !== (b as Record<string, any>)[key]) return false;
  }
  return true;
}
