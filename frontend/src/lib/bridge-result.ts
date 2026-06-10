export interface BridgeResult<T> {
  ok: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
    detail?: string;
  };
}

export function unwrap<T>(result: BridgeResult<T>): T {
  if (!result.ok || result.data === undefined) {
    throw new Error(result.error?.message ?? 'Unknown bridge error');
  }
  return result.data;
}

export function unwrapOr<T>(result: BridgeResult<T>, defaultValue: T): T {
  if (!result.ok || result.data === undefined) {
    return defaultValue;
  }
  return result.data;
}
