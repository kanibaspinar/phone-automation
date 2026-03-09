import threading
import time
from typing import Dict, List, Callable
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        self._tasks: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_tasks, daemon=True)
        self._monitor_thread.start()

    def add_task(self, name: str, func: Callable, interval: int = 60, args: tuple = (), kwargs: dict = None):
        """Add a new task to be executed periodically"""
        with self._lock:
            if name in self._tasks:
                logger.warning(f"Task {name} already exists. Stopping old task.")
                self.stop_task(name)
            
            task_thread = threading.Thread(
                target=self._run_task,
                args=(name, func, interval, args, kwargs or {}),
                daemon=True
            )
            
            self._tasks[name] = {
                'thread': task_thread,
                'last_run': None,
                'interval': interval,
                'running': True,
                'func': func,
                'args': args,
                'kwargs': kwargs or {}
            }
            task_thread.start()
            logger.info(f"Started task: {name}")

    def stop_task(self, name: str):
        """Stop a specific task"""
        with self._lock:
            if name in self._tasks:
                self._tasks[name]['running'] = False
                logger.info(f"Stopped task: {name}")

    def stop_all(self):
        """Stop all tasks and the task manager"""
        with self._lock:
            self._running = False
            for name in list(self._tasks.keys()):
                self.stop_task(name)
        self._monitor_thread.join()
        logger.info("Stopped all tasks")

    def _run_task(self, name: str, func: Callable, interval: int, args: tuple, kwargs: dict):
        """Run a task periodically"""
        while self._running and self._tasks.get(name, {}).get('running', False):
            try:
                func(*args, **kwargs)
                with self._lock:
                    if name in self._tasks:
                        self._tasks[name]['last_run'] = datetime.utcnow()
            except Exception as e:
                logger.error(f"Error in task {name}: {str(e)}")
            
            # Sleep for the specified interval
            time.sleep(interval)

    def _monitor_tasks(self):
        """Monitor tasks and restart them if they fail"""
        while self._running:
            with self._lock:
                for name, task in list(self._tasks.items()):
                    if task['running'] and not task['thread'].is_alive():
                        logger.warning(f"Task {name} died. Restarting...")
                        new_thread = threading.Thread(
                            target=self._run_task,
                            args=(name, task['func'], task['interval'], task['args'], task['kwargs']),
                            daemon=True
                        )
                        task['thread'] = new_thread
                        new_thread.start()
            time.sleep(5)  # Check every 5 seconds

    def get_task_status(self) -> List[Dict]:
        """Get status of all tasks"""
        with self._lock:
            return [{
                'name': name,
                'running': task['running'],
                'last_run': task['last_run'].isoformat() if task['last_run'] else None,
                'interval': task['interval']
            } for name, task in self._tasks.items()] 