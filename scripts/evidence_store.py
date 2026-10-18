"""Storage, HMAC integrity, and lookup helpers for executor receipts."""
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

ARTIFACTS = Path("/tmp/wave-grafo-evidence")
LOCKS = Path("/tmp/wave-grafo-repo-locks")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def graph_token(graph_path):
    return digest(str(Path(graph_path).resolve()).encode())


def artifact_root(graph_path):
    return ARTIFACTS / graph_token(graph_path)


def id_token(sid):
    return digest(str(sid).encode())


def cycle_token(slice_data):
    return digest(canonical_json({"cycle_no": slice_data.get("cycle_no"),
                                  "cycle_entered_ts": slice_data.get("cycle_entered_ts")}))


def gate_dir(graph_path, sid, slice_data):
    return artifact_root(graph_path) / "receipts" / id_token(sid) / cycle_token(slice_data) / "gate"


def ensure_key(graph_path):
    root = artifact_root(graph_path)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = root / "receipt.key"
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, os.urandom(32))
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        pass
    return read_key(graph_path)


def read_key(graph_path):
    path = artifact_root(graph_path) / "receipt.key"
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            return None
        key = path.read_bytes()
        return key if len(key) == 32 else None
    except OSError:
        return None


def sign_receipt(receipt, graph_path):
    key = ensure_key(graph_path)
    payload = {k: v for k, v in receipt.items() if k != "hmac_sha256"}
    payload["hmac_sha256"] = hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()
    return payload


def _contained(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def verify_receipt(path, graph_path):
    root = artifact_root(graph_path)
    if not _contained(path, root / "receipts"):
        return None, "recibo fora do diretório esperado"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"recibo ilegível: {exc}"
    key = read_key(graph_path)
    supplied = data.pop("hmac_sha256", None)
    expected = hmac.new(key or b"", canonical_json(data), hashlib.sha256).hexdigest()
    if key is None or not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
        return None, "assinatura do recibo inválida"
    log_path = data.get("log_path")
    if not isinstance(log_path, str) or not _contained(log_path, root / "logs"):
        return None, "log fora do diretório esperado"
    try:
        hasher = hashlib.sha256()
        with Path(log_path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError as exc:
        return None, f"log ausente ou ilegível: {exc}"
    if not hmac.compare_digest(hasher.hexdigest(), str(data.get("log_sha256", ""))):
        return None, "sha256 do log não confere"
    data["hmac_sha256"] = supplied
    return data, None


def receipts_for(graph_path, sid, slice_data, phase):
    base = artifact_root(graph_path) / "receipts" / id_token(sid) / cycle_token(slice_data) / phase
    if not base.exists():
        return [], []
    found, errors = [], []
    for path in base.glob("*.json"):
        receipt, error = verify_receipt(path, graph_path)
        if error:
            errors.append(error)
        elif (receipt.get("graph_path") == str(Path(graph_path).resolve()) and
              receipt.get("id") == sid and receipt.get("phase") == phase and
              receipt.get("cycle_no") == slice_data.get("cycle_no") and
              receipt.get("cycle_entered_ts") == slice_data.get("cycle_entered_ts")):
            found.append(receipt)
        else:
            errors.append("campos do recibo não correspondem à sua localização/ciclo")
    return found, errors
