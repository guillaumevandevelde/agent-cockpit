"""Duurzame auto-resume-instellingen per projectmap.

``AutoResumeService`` hield deze twee waarden alleen in geheugen-dicts. Zette je
auto-resume aan via ``POST /api/v1/session-hooks/auto-resume/<cwd>``, dan was
dat na een herstart van de backend stil weer uit — de gebruiker kreeg geen
signaal, de sessie werd simpelweg nooit hervat.

Apparaat-lokaal, dus in de registry-store en niet op het bord: een ``cwd`` is
een pad op deze machine en heeft geen betekenis op een andere.
"""
from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AutoResumeConfig(Base):
    """Instelling per projectmap: staat auto-resume aan, en met welk bericht."""

    __tablename__ = "auto_resume_configs"

    cwd: Mapped[str] = mapped_column(String(1024), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
