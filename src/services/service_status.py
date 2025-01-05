from enum import Enum, auto
from datetime import datetime
from typing import Dict, Any, Optional

class ServiceStatus(Enum):
    """Service status enum"""
    INITIALIZING = auto()
    RUNNING = auto()
    STOPPED = auto()
    ERROR = auto()
    UNKNOWN = auto()

class ServiceState:
    """Service state tracking"""
    def __init__(self, name: str):
        self.name = name
        self.status = ServiceStatus.UNKNOWN
        self.started_at: Optional[datetime] = None
        self.error: Optional[str] = None
        self.metrics: Dict[str, Any] = {}

    def start(self):
        """Mark service as started"""
        self.status = ServiceStatus.RUNNING
        self.started_at = datetime.utcnow()
        self.error = None

    def stop(self):
        """Mark service as stopped"""
        self.status = ServiceStatus.STOPPED
        self.error = None

    def error(self, message: str):
        """Mark service as errored"""
        self.status = ServiceStatus.ERROR
        self.error = message

    def update_metrics(self, metrics: Dict[str, Any]):
        """Update service metrics"""
        self.metrics.update(metrics)

    @property
    def is_running(self) -> bool:
        """Check if service is running"""
        return self.status == ServiceStatus.RUNNING

    @property
    def state(self) -> Dict[str, Any]:
        """Get current state"""
        return {
            'status': self.status.name,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'error': self.error,
            'metrics': self.metrics
        }