from dataclasses import dataclass
from typing import Optional, Any
import json
import functools

@dataclass
class BridgeResult:
    ok: bool
    data: Optional[Any] = None
    error: Optional[dict] = None

    def to_json(self) -> str:
        result = {"ok": self.ok}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return json.dumps(result)

    @staticmethod
    def success(data=None) -> 'BridgeResult':
        return BridgeResult(ok=True, data=data)

    @staticmethod
    def fail(code: str, message: str, detail: str | None = None) -> 'BridgeResult':
        error = {"code": code, "message": message}
        if detail:
            error["detail"] = detail
        return BridgeResult(ok=False, error=error)

def bridge_method(func):
    """装饰器：统一捕获异常并返回 BridgeResult JSON"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            if isinstance(result, BridgeResult):
                return result.to_json()
            return BridgeResult.success(result).to_json()
        except json.JSONDecodeError as e:
            return BridgeResult.fail(
                code="JSON_DECODE_ERROR",
                message=f"JSON decode error: {e}",
                detail=str(e)
            ).to_json()
        except FileNotFoundError as e:
            return BridgeResult.fail(
                code="FILE_NOT_FOUND",
                message=str(e)
            ).to_json()
        except Exception as e:
            return BridgeResult.fail(
                code="INTERNAL_ERROR",
                message=str(e),
                detail=type(e).__name__
            ).to_json()
    return wrapper
