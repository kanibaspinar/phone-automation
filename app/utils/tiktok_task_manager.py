"""TikTok task manager.

Mirrors the design of InstagramTaskManager: a priority queue per device,
one semaphore per device to prevent concurrent access, and a pool of
worker threads that execute tasks inside a Flask app context.

Mass actions use run_collection (fetches+filters followers via API) rather
than iterating a pre-built list.
"""
import logging
import queue
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_task_manager_instance = None


def get_tiktok_task_manager():
    """Lazy-singleton accessor for the global TikTok task manager."""
    global _task_manager_instance
    if _task_manager_instance is None:
        try:
            from flask import current_app
            from app.utils.tiktok_automation import TikTokAutomation
            automation = TikTokAutomation()
            _task_manager_instance = TikTokTaskManager(
                automation, current_app._get_current_object()
            )
            logger.info('TikTok task manager initialised')
        except Exception as e:
            logger.error(f'Failed to initialise TikTok task manager: {e}')
            return None
    return _task_manager_instance


# ---------------------------------------------------------------------------
# Task data class
# ---------------------------------------------------------------------------

class TikTokTask:
    """Single unit of TikTok work."""

    def __init__(self, task_type: str, params: dict):
        self.task_id = str(uuid.uuid4())
        self.task_type = task_type
        self.params = params
        self.status = 'pending'   # pending | running | completed | failed
        self.result = None
        self.error = None
        self.created_at = datetime.now()
        self.completed_at: Optional[datetime] = None
        self.device_id: str = params.get('device_id', '')
        self.username: str = params.get('username', '')
        self.target_username: str = params.get('target_username', '')
        # For run_collection, keep a stop event so it can be cancelled
        self._stop_event: Optional[threading.Event] = None

    def stop(self):
        if self._stop_event:
            self._stop_event.set()

    def to_dict(self) -> dict:
        base = {
            'task_id': self.task_id,
            'task_type': self.task_type,
            'status': self.status,
            'device_id': self.device_id,
            'username': self.username,
            'target_username': self.target_username,
            'created_at': self.created_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'duration': str(self.completed_at - self.created_at) if self.completed_at else 'In Progress',
            'result': self.result,
            'error': self.error,
        }

        if self.task_type == 'follow':
            base['action'] = f"Follow {self.target_username} as {self.username}"
        elif self.task_type == 'like_posts':
            base['action'] = f"Like posts from {self.target_username} as {self.username}"
            base['count'] = self.params.get('count', 3)
        elif self.task_type == 'view_profile':
            base['action'] = f"View profile {self.target_username} as {self.username}"
        elif self.task_type == 'comment':
            base['action'] = f"Comment on {self.target_username}'s post as {self.username}"
            base['comment'] = self.params.get('comment', '')
        elif self.task_type == 'like_story':
            base['action'] = f"Like story of {self.target_username} as {self.username}"
        elif self.task_type == 'run_collection':
            targets = self.params.get('targets', '')
            base['action'] = f"Run collection for {self.username} on targets: {targets}"
            base['targets'] = targets

        return base


class _PrioritizedTask:
    def __init__(self, task: TikTokTask, priority: int):
        self.task = task
        self.priority = priority

    def __lt__(self, other: '_PrioritizedTask') -> bool:
        return self.priority < other.priority


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TikTokTaskManager:
    """Asynchronous TikTok automation task queue, per-device serialisation."""

    _PRIORITIES: dict = {
        'follow':         1,
        'like_posts':     2,
        'view_profile':   3,
        'comment':        4,
        'like_story':     5,
        'run_collection': 6,
    }

    def __init__(self, tiktok_automation, app=None, num_workers: int = 50):
        self.automation = tiktok_automation
        self.app = app
        self.tasks: Dict[str, TikTokTask] = {}
        self.device_queues: Dict[str, queue.PriorityQueue] = {}
        self.device_semaphores: Dict[str, threading.Semaphore] = {}
        # cursor_store[device_id][target_username] -> int
        self._cursor_store: Dict[str, Dict[str, int]] = {}
        self.num_workers = num_workers

        for _ in range(self.num_workers):
            t = threading.Thread(target=self._process_loop, daemon=True)
            t.start()

        logger.info(f'TikTokTaskManager started with {self.num_workers} workers')

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_task(self, task_type: str, params: dict) -> str:
        task = TikTokTask(task_type, params)
        device_id = params.get('device_id')

        if device_id not in self.device_semaphores:
            self.device_semaphores[device_id] = threading.Semaphore(1)
        if device_id not in self.device_queues:
            self.device_queues[device_id] = queue.PriorityQueue()

        priority = self._PRIORITIES.get(task_type, 10)
        self.device_queues[device_id].put(_PrioritizedTask(task, priority))
        self.tasks[task.task_id] = task
        logger.info(f'Queued TikTok task {task.task_id} type={task_type} device={device_id}')
        return task.task_id

    def stop_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task and task.status == 'running':
            task.stop()
            return True
        return False

    def get_task_status(self, task_id: str) -> Optional[dict]:
        task = self.tasks.get(task_id)
        return task.to_dict() if task else None

    def get_all_tasks(self) -> List[dict]:
        return [t.to_dict() for t in sorted(
            self.tasks.values(), key=lambda x: x.created_at, reverse=True
        )]

    def get_active_tasks(self) -> List[dict]:
        return [t.to_dict() for t in self.tasks.values()
                if t.status in ('pending', 'running')]

    def get_tasks_by_device(self, device_id: str) -> List[dict]:
        return [t.to_dict() for t in self.tasks.values()
                if t.device_id == device_id]

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _process_loop(self):
        while True:
            try:
                task = device_id = None
                for d_id, dq in self.device_queues.items():
                    if not dq.empty() and self.device_semaphores[d_id]._value > 0:
                        device_id = d_id
                        task = dq.get_nowait().task
                        break

                if task and device_id:
                    with self.device_semaphores[device_id]:
                        with self.app.app_context():
                            self._execute(task)
                    self.device_queues[device_id].task_done()
                else:
                    time.sleep(0.1)

            except queue.Empty:
                time.sleep(0.1)
            except Exception as e:
                logger.error(f'TikTok worker error: {e}')
                time.sleep(1)

    def _execute(self, task: TikTokTask):
        logger.info(f'Executing TikTok task {task.task_id} type={task.task_type}')
        task.status = 'running'

        try:
            device_id = task.params.get('device_id')
            username = task.params.get('username', '')

            if task.task_type == 'follow':
                success, message = self.automation.follow_user(
                    device_id, username, task.params['target_username']
                )

            elif task.task_type == 'like_posts':
                success, message = self.automation.like_posts(
                    device_id, username,
                    task.params['target_username'],
                    task.params.get('count', 3),
                )

            elif task.task_type == 'view_profile':
                success, message = self.automation.view_profile(
                    device_id, username, task.params['target_username']
                )

            elif task.task_type == 'comment':
                success, message = self.automation.comment_on_post(
                    device_id, username,
                    task.params['target_username'],
                    task.params['comment'],
                )

            elif task.task_type == 'like_story':
                success, message = self.automation.like_story(
                    device_id, username, task.params['target_username']
                )

            elif task.task_type == 'run_collection':
                # Build config dict from params (includes all collection_config fields)
                config = {k: v for k, v in task.params.items()
                          if k not in ('device_id', 'username')}
                # Ensure account_id is present for smart_delay
                if 'account_id' not in config:
                    from app.models.tiktok_account import TikTokAccount
                    acc = TikTokAccount.query.filter_by(username=username).first()
                    if acc:
                        config['account_id'] = acc.id

                stop_event = threading.Event()
                task._stop_event = stop_event

                cursor_store = self._cursor_store.setdefault(device_id, {})
                self.automation.run_collection(
                    device_id, username, stop_event, config, cursor_store
                )
                success = True
                message = 'Collection finished'

            else:
                success, message = False, f'Unknown task type: {task.task_type}'

            task.status = 'completed' if success else 'failed'
            task.result = message if success else None
            task.error = None if success else str(message)

        except Exception as e:
            logger.error(f'TikTok task {task.task_id} raised: {e}')
            task.status = 'failed'
            task.error = str(e)

        finally:
            task.completed_at = datetime.now()
