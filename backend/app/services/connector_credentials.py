from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models.entities import ConnectorCredential


def read_connector_secret(db: Session, connection_id: int) -> str:
    credential = db.get(ConnectorCredential, connection_id)
    return (credential.secret_payload if credential else "").strip()


def write_connector_secret(db: Session, connection_id: int, secret: str) -> None:
    normalized = secret.strip()
    credential = db.get(ConnectorCredential, connection_id)
    if credential is None:
        credential = ConnectorCredential(connection_id=connection_id, secret_payload=normalized)
        db.add(credential)
    else:
        credential.secret_payload = normalized


def delete_connector_secret(db: Session, connection_id: int) -> None:
    credential = db.get(ConnectorCredential, connection_id)
    if credential is not None:
        db.delete(credential)
