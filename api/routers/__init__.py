"""API 子路由包（V1.6 功能介绍知识库化引入）。

既有端点集中定义在 ``api/main.py``；从本版本起，新增的、边界清晰的业务域
以 ``APIRouter`` 形式拆分到本包下，由 ``api/main.py`` 统一 ``include_router``。

现有子路由：
    - :mod:`api.routers.feature_intro` — 功能介绍知识库读取 / 重载。
    - :mod:`api.routers.knowledge_upload` — 用户上传知识库（上传 / 列表 / 删除）。
"""

from __future__ import annotations

from api.routers.feature_intro import router as feature_intro_router
from api.routers.knowledge_upload import router as knowledge_upload_router

__all__ = ["feature_intro_router", "knowledge_upload_router"]
