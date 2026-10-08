from typing import Annotated, Optional, Dict, Any
from pydantic import BaseModel, Field

from .mlt_xml import FILTER_SPECS

ALLOWED_FILTERS = set(FILTER_SPECS)

ProjectName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]


class CreateProjectInput(BaseModel):
    name: ProjectName
    width: int = Field(default=1920, ge=16, le=7680)
    height: int = Field(default=1080, ge=16, le=7680)
    fps: float = Field(default=25.0, gt=0, le=240)


class AddClipInput(BaseModel):
    project_name: ProjectName
    clip_path: Annotated[str, Field(min_length=1)]
    in_s: float = Field(default=0.0, ge=0)
    out_s: Optional[float] = None


class SplitClipInput(BaseModel):
    project_name: ProjectName
    producer_id: Annotated[str, Field(min_length=1)]
    at_s: float


class TrimClipInput(BaseModel):
    project_name: ProjectName
    producer_id: Annotated[str, Field(min_length=1)]
    in_s: Optional[float] = None
    out_s: Optional[float] = None


class RemoveClipInput(BaseModel):
    project_name: ProjectName
    producer_id: Annotated[str, Field(min_length=1)]


class MoveClipInput(BaseModel):
    project_name: ProjectName
    producer_id: Annotated[str, Field(min_length=1)]
    to_position: int = Field(ge=1)


class RemoveSegmentInput(BaseModel):
    project_name: ProjectName
    start_s: float
    end_s: float


class AppendTextOverlayInput(BaseModel):
    project_name: ProjectName
    text: Annotated[str, Field(min_length=1, max_length=256)]
    start_s: float = Field(ge=0)
    end_s: float
    font_size: int = Field(default=48, ge=8, le=400)
    color: str = "#ffffff"
    bg_color: str = "#80000000"
    halign: str = "center"
    valign: str = "bottom"


class ApplyFilterInput(BaseModel):
    project_name: ProjectName
    producer_id: Annotated[str, Field(min_length=1)]
    filter_name: str
    params: Dict[str, Any] = Field(default_factory=dict)


class SetTransitionInput(BaseModel):
    project_name: ProjectName
    from_producer_id: Annotated[str, Field(min_length=1)]
    to_producer_id: Annotated[str, Field(min_length=1)]
    duration_s: float
    style: str = "luma"


class ProjectInfoInput(BaseModel):
    project_name: ProjectName


class GetTimelineInput(BaseModel):
    project_name: ProjectName


class RenderInput(BaseModel):
    project_name: ProjectName
    output_path: Annotated[str, Field(min_length=1)]
    preset: str = "mp4-1080p"


class ListProjectsInput(BaseModel):
    pass
