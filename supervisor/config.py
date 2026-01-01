from pydantic import BaseModel, Field, model_validator


class WorkingHoursConfig(BaseModel, frozen=True):
    """Configuration for working hours scheduling."""

    enabled: bool = False
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=17, ge=0, le=23)
    timezone: str = "US/Pacific"

    @model_validator(mode="after")
    def validate_hours(self) -> "WorkingHoursConfig":
        if self.start_hour >= self.end_hour:
            raise ValueError(f"start_hour ({self.start_hour}) must be < end_hour ({self.end_hour})")
        return self
