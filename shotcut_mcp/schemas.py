from typing import Annotated, Optional, Dict, Any
from pydantic import BaseModel, Field


ALLOWED_FILTERS = {"brightness", "volume", "fadeInBrightness", "fadeOutBrightness", "crop"}


class CreateProjectInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    width: int = 1920
    height: int = 1080
    fps: float = 25.0


class AddClipInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    clip_path: Annotated[str, Field(min_length=1)]
    in_s: float = 0.0
    out_s: Optional[float] = None


class SplitClipInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    producer_id: Annotated[str, Field(min_length=1)]
    at_s: float


class RemoveSegmentInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    start_s: float
    end_s: float


class AppendTextOverlayInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    text: Annotated[str, Field(min_length=1, max_length=256)]
    start_s: float
    end_s: float
    font_size: int = 48
    color: str = "#ffffff"
    bg_color: str = "#80000000"
    halign: str = "center"
    valign: str = "bottom"


class ApplyFilterInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    producer_id: Annotated[str, Field(min_length=1)]
    filter_name: str
    params: Dict[str, Any] = Field(default_factory=dict)


class SetTransitionInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    from_producer_id: Annotated[str, Field(min_length=1)]
    to_producer_id: Annotated[str, Field(min_length=1)]
    duration_s: float
    style: str = "luma"


class ProjectInfoInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]


class RenderInput(BaseModel):
    project_name: Annotated[str, Field(min_length=1, max_length=64)]
    output_path: Annotated[str, Field(min_length=1)]
    preset: str = "mp4-1080p"


class ListProjectsInput(BaseModel):
    pass
