from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'

class Inclusion(str, Enum):
    included = 'included'        # 截止月内最新观测，进入寿命样本
    superseded = 'superseded'    # 被同资产更新观测替代
    excluded = 'excluded'        # 观测月晚于截止月，不参与
    conflict = 'conflict'        # 业务冲突，整组阻止估计
