"""测试共享配置：所有测试使用临时数据库，不影响开发数据。

在测试会话开始时，将 UserService / ProjectService / PlanService /
AnalyticsService 的默认数据库路径重定向到临时目录。
该补丁在 conftest 模块加载时生效（早于任何测试模块导入），
因此 auth.py / projects.py 等模块级服务实例也会使用临时数据库。
"""

import os
import shutil
import tempfile

# 创建会话级临时目录（在模块加载时创建，全测试会话共享）
_TEST_TMPDIR = tempfile.mkdtemp(prefix="openask_test_")

# 记录原始构造函数
import src.services.user_service as _us
import src.services.project_service as _ps
import src.services.plan_service as _pl
import src.services.analytics_service as _an
import src.services.conversation_service as _cs
import src.services.agent_service as _as
import src.services.canned_response_service as _cr

_orig_user_init = _us.UserService.__init__
_orig_proj_init = _ps.ProjectService.__init__
_orig_plan_init = _pl.PlanService.__init__
_orig_analytics_init = _an.AnalyticsService.__init__
_orig_conv_init = _cs.ConversationService.__init__
_orig_agent_init = _as.AgentService.__init__
_orig_canned_init = _cr.CannedResponseService.__init__

# 定义补丁构造函数：未显式传 db_path 时使用临时目录
def _patched_user_init(self, db_path=None):
    _orig_user_init(self, db_path or os.path.join(_TEST_TMPDIR, "users.db"))

def _patched_proj_init(self, db_path=None):
    _orig_proj_init(self, db_path or os.path.join(_TEST_TMPDIR, "projects.db"))

def _patched_plan_init(self, db_path=None):
    _orig_plan_init(self, db_path or os.path.join(_TEST_TMPDIR, "billing.db"))

def _patched_analytics_init(self, db_path=None):
    _orig_analytics_init(self, db_path or os.path.join(_TEST_TMPDIR, "analytics.db"))

def _patched_conv_init(self, db_path=None):
    _orig_conv_init(self, db_path or os.path.join(_TEST_TMPDIR, "conversations.db"))

def _patched_agent_init(self, db_path=None):
    _orig_agent_init(self, db_path or os.path.join(_TEST_TMPDIR, "agents.db"))

def _patched_canned_init(self, db_path=None):
    _orig_canned_init(self, db_path or os.path.join(_TEST_TMPDIR, "canned.db"))

_us.UserService.__init__ = _patched_user_init
_ps.ProjectService.__init__ = _patched_proj_init
_pl.PlanService.__init__ = _patched_plan_init
_an.AnalyticsService.__init__ = _patched_analytics_init
_cs.ConversationService.__init__ = _patched_conv_init
_as.AgentService.__init__ = _patched_agent_init
_cr.CannedResponseService.__init__ = _patched_canned_init


def pytest_sessionfinish(session, exitstatus):
    """测试结束后清理临时目录。"""
    shutil.rmtree(_TEST_TMPDIR, ignore_errors=True)