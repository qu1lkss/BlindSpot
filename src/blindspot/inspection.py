import json
from pathlib import Path
import subprocess
import sys


def inspect_inputs(directory: Path):
    # Даже пробный прогноз может зависнуть внутри runtime.
    limit = 300
    try:
        policy = json.loads((directory / "policy.json").read_bytes())
        value = policy["seconds"]["validation"]
        if type(value) in (int, float) and 0 < value <= limit:
            limit = value
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        result = subprocess.run(
            [sys.executable, "-m", "blindspot.inspection", str(directory)],
            capture_output=True, timeout=limit, check=True,
        )
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        reason = "Истёк срок проверки входов."
    except (subprocess.CalledProcessError, ValueError):
        reason = "Процесс проверки завершился без протокола."
    return {"valid": False, "errors": [{"field": "validation", "message": reason}]}


if __name__ == "__main__":
    from .validation import validate_bundle
    print(json.dumps(validate_bundle(Path(sys.argv[1])), ensure_ascii=False, allow_nan=False))
