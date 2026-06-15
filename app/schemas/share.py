from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
