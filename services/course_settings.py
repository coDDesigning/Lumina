from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import CourseSettings
from schemas.course_settings import CourseSettingsResponse, CourseSettingsUpdate


class CourseSettingsService:
    @staticmethod
    def to_response(settings: CourseSettings) -> CourseSettingsResponse:
        return CourseSettingsResponse(
            study_mode=settings.study_mode,
            difficulty=settings.difficulty,
            question_count=settings.question_count,
            summary_length=settings.summary_length,
            detail_level=settings.detail_level,
            notifications=settings.notifications,
            progress_reminders=settings.progress_reminders,
        )

    @classmethod
    def get_or_create(cls, db: Session, course_id: int) -> CourseSettings:
        settings = db.scalar(
            select(CourseSettings).where(CourseSettings.course_id == course_id)
        )
        if settings is None:
            settings = CourseSettings(course_id=course_id)
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings

    @classmethod
    def update(
        cls,
        db: Session,
        course_id: int,
        update_data: CourseSettingsUpdate,
    ) -> CourseSettings:
        settings = cls.get_or_create(db, course_id)
        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(settings, field, value)
        db.commit()
        db.refresh(settings)
        return settings
