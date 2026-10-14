"""Authenticated market-extension configuration and truthful capability discovery."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db
from src.platform.marketdata.extensions import EXTENSIONS, extension_status, set_extension_enabled

router = APIRouter()


class ExtensionUpdate(BaseModel):
    enabled: StrictBool


@router.get("")
def list_extensions(db: Session = Depends(get_db)):
    return [extension_status(db, key) for key in EXTENSIONS]


@router.patch("/{extension_id}")
def update_extension(extension_id: str, payload: ExtensionUpdate, db: Session = Depends(get_db)):
    return set_extension_enabled(db, extension_id, payload.enabled)
