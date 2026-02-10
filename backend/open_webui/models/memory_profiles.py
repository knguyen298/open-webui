import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy import select
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, JSONField, get_db, get_db_context


####################
# User Memory Profile DB Schema
####################


class UserMemoryProfile(Base):
    __tablename__ = "user_memory_profile"

    user_id = Column(String, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)

    memory_summary = Column(JSONField, nullable=True)
    pending_manual_memory = Column(JSONField, nullable=True)

    last_processed_chat_updated_at = Column(BigInteger, nullable=True)
    last_run_at = Column(BigInteger, nullable=True)
    next_run_at = Column(BigInteger, nullable=True)

    frequency = Column(String, nullable=False, default="daily")
    time_of_day = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    day_of_week = Column(Integer, nullable=True)

    recent_days = Column(Integer, nullable=True)
    first_n_messages = Column(Integer, nullable=True)
    last_n_messages = Column(Integer, nullable=True)
    user_only = Column(Boolean, nullable=False, default=False)

    status = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class UserMemoryProfileModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str

    memory_summary: Optional[dict] = None
    pending_manual_memory: Optional[list[str]] = None

    last_processed_chat_updated_at: Optional[int] = None
    last_run_at: Optional[int] = None
    next_run_at: Optional[int] = None

    frequency: str = "daily"
    time_of_day: Optional[str] = None
    timezone: Optional[str] = None
    day_of_week: Optional[int] = None

    recent_days: Optional[int] = None
    first_n_messages: Optional[int] = None
    last_n_messages: Optional[int] = None
    user_only: bool = False

    status: Optional[str] = None
    last_error: Optional[str] = None

    created_at: int
    updated_at: int


class UserMemoryProfileCreateForm(BaseModel):
    memory_summary: Optional[dict] = None
    pending_manual_memory: Optional[list[str]] = None

    last_processed_chat_updated_at: Optional[int] = None
    last_run_at: Optional[int] = None
    next_run_at: Optional[int] = None

    frequency: str = "daily"
    time_of_day: Optional[str] = None
    timezone: Optional[str] = None
    day_of_week: Optional[int] = None

    recent_days: Optional[int] = None
    first_n_messages: Optional[int] = None
    last_n_messages: Optional[int] = None
    user_only: bool = False

    status: Optional[str] = None
    last_error: Optional[str] = None


class UserMemoryProfileUpdateForm(BaseModel):
    memory_summary: Optional[dict] = None
    pending_manual_memory: Optional[list[str]] = None

    last_processed_chat_updated_at: Optional[int] = None
    last_run_at: Optional[int] = None
    next_run_at: Optional[int] = None

    frequency: Optional[str] = None
    time_of_day: Optional[str] = None
    timezone: Optional[str] = None
    day_of_week: Optional[int] = None

    recent_days: Optional[int] = None
    first_n_messages: Optional[int] = None
    last_n_messages: Optional[int] = None
    user_only: Optional[bool] = None

    status: Optional[str] = None
    last_error: Optional[str] = None


class UserMemoryProfilesTable:
    def _get_profile_for_update(self, db: Session, user_id: str) -> Optional[UserMemoryProfile]:
        return db.execute(
            select(UserMemoryProfile)
            .where(UserMemoryProfile.user_id == user_id)
            .with_for_update()
        ).scalar_one_or_none()

    def insert_new_profile(
        self,
        user_id: str,
        form_data: UserMemoryProfileCreateForm,
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            now = int(time.time())
            profile = UserMemoryProfile(
                user_id=user_id,
                **form_data.model_dump(),
                created_at=now,
                updated_at=now,
            )

            db.add(profile)
            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def get_profile_by_user_id(
        self, user_id: str, db: Optional[Session] = None
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = db.get(UserMemoryProfile, user_id)
            if profile is None:
                return None

            return UserMemoryProfileModel.model_validate(profile)

    def upsert_profile_by_user_id(
        self,
        user_id: str,
        form_data: UserMemoryProfileCreateForm | UserMemoryProfileUpdateForm,
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            now = int(time.time())
            profile = self._get_profile_for_update(db, user_id)

            if profile is None:
                profile = UserMemoryProfile(
                    user_id=user_id,
                    **form_data.model_dump(exclude_none=True),
                    created_at=now,
                    updated_at=now,
                )
                db.add(profile)
            else:
                update_data = form_data.model_dump(exclude_none=True)
                for key, value in update_data.items():
                    setattr(profile, key, value)
                profile.updated_at = now

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def update_profile_by_user_id(
        self,
        user_id: str,
        form_data: UserMemoryProfileUpdateForm,
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = db.get(UserMemoryProfile, user_id)
            if profile is None:
                return None

            update_data = form_data.model_dump(exclude_none=True)
            for key, value in update_data.items():
                setattr(profile, key, value)

            profile.updated_at = int(time.time())

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def append_pending_manual_memory(
        self,
        user_id: str,
        memory_item: str,
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = self._get_profile_for_update(db, user_id)
            if profile is None:
                return None

            pending_items = profile.pending_manual_memory or []
            pending_items.append(memory_item)
            profile.pending_manual_memory = pending_items
            profile.updated_at = int(time.time())

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def atomic_update_pending_manual_memory(
        self,
        user_id: str,
        pending_manual_memory: list[str],
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = self._get_profile_for_update(db, user_id)
            if profile is None:
                return None

            profile.pending_manual_memory = pending_manual_memory
            profile.updated_at = int(time.time())

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def clear_pending_manual_memory(
        self,
        user_id: str,
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = self._get_profile_for_update(db, user_id)
            if profile is None:
                return None

            profile.pending_manual_memory = []
            profile.updated_at = int(time.time())

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def update_run_state_by_user_id(
        self,
        user_id: str,
        *,
        last_processed_chat_updated_at: Optional[int] = None,
        last_run_at: Optional[int] = None,
        next_run_at: Optional[int] = None,
        status: Optional[str] = None,
        last_error: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = self._get_profile_for_update(db, user_id)
            if profile is None:
                return None

            if last_processed_chat_updated_at is not None:
                profile.last_processed_chat_updated_at = last_processed_chat_updated_at
            if last_run_at is not None:
                profile.last_run_at = last_run_at
            if next_run_at is not None:
                profile.next_run_at = next_run_at
            if status is not None:
                profile.status = status
            if last_error is not None:
                profile.last_error = last_error

            profile.updated_at = int(time.time())

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def atomic_update_memory_summary(
        self,
        user_id: str,
        memory_summary: Optional[dict],
        db: Optional[Session] = None,
    ) -> Optional[UserMemoryProfileModel]:
        with get_db_context(db) as db:
            profile = self._get_profile_for_update(db, user_id)
            if profile is None:
                return None

            profile.memory_summary = memory_summary
            profile.updated_at = int(time.time())

            db.commit()
            db.refresh(profile)
            return UserMemoryProfileModel.model_validate(profile)

    def delete_profile_by_user_id(
        self, user_id: str, db: Optional[Session] = None
    ) -> bool:
        with get_db_context(db) as db:
            profile = db.get(UserMemoryProfile, user_id)
            if profile is None:
                return False

            db.delete(profile)
            db.commit()
            return True


UserMemoryProfiles = UserMemoryProfilesTable()
