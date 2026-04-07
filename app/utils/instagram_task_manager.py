import threading
import queue
import logging
import time
import uuid
import os
from datetime import datetime
from typing import Dict, List, Optional
from flask import current_app

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global task manager instance
_task_manager = None

def get_task_manager():
    """Get or create the task manager instance"""
    global _task_manager
    
    if _task_manager is None:
        try:
            from app.utils.instagram_automation import InstagramAutomation
            from app.utils.device_manager import DeviceManager
            from flask import current_app
            
            logger.info("Creating new task manager instance")
            
            # Get device manager instance
            app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            assets_dir = os.path.join(app_root, 'assets')
            
            # Create assets directory if it doesn't exist
            if not os.path.exists(assets_dir):
                os.makedirs(assets_dir)
                logger.info(f"Created assets directory at: {assets_dir}")
            
            logger.info(f"Using assets directory: {assets_dir}")
            device_manager = DeviceManager(assets_dir)
            
            # Initialize Instagram automation
            instagram_automation = InstagramAutomation(device_manager)
            
            # Create task manager with Flask app context
            _task_manager = InstagramTaskManager(instagram_automation, current_app._get_current_object())
            
            logger.info("Task manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize task manager: {str(e)}")
            return None
    else:
        logger.debug("Using existing task manager instance")
    
    return _task_manager

class InstagramTask:
    """Represents an Instagram automation task"""
    def __init__(self, task_type: str, params: dict):
        self.task_id = str(uuid.uuid4())
        self.task_type = task_type
        self.params = params
        self.status = 'pending'  # pending, running, completed, failed
        self.result = None
        self.error = None
        self.created_at = datetime.now()
        self.completed_at = None
        self.device_id = params.get('device_id', '')
        self.username = params.get('username', '')
        self.target_username = params.get('target_username', '')

    def to_dict(self) -> dict:
        """Convert task to dictionary representation"""
        task_details = {
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
            'error': self.error
        }

        # Add task-specific details
        if self.task_type == 'login':
            task_details['action'] = f"Login {self.username} on device {self.device_id}"
        elif self.task_type == 'logout':
            task_details['action'] = f"Logout {self.username} from device {self.device_id}"
        elif self.task_type == 'post_reel':
            task_details['action'] = f"Post reel as {self.username}"
            task_details['caption'] = self.params.get('caption', '')
            task_details['music_query'] = self.params.get('music_query', '')
            task_details['video_path'] = self.params.get('video_path', '')
        elif self.task_type == 'post_photo':
            task_details['action'] = f"Post photo as {self.username}"
            task_details['caption'] = self.params.get('caption', '')
            task_details['music_query'] = self.params.get('music_query', '')
            task_details['photo_path'] = self.params.get('photo_path', '')
        elif self.task_type == 'like_post':
            task_details['action'] = f"Like post from {self.target_username} as {self.username}"
        elif self.task_type == 'like_story':
            task_details['action'] = f"Like story from {self.target_username} as {self.username}"
        elif self.task_type == 'comment_story':
            task_details['action'] = f"Comment on {self.target_username}'s story as {self.username}"
            task_details['comment'] = self.params.get('comment', '')
        elif self.task_type == 'follow_user':
            task_details['action'] = f"Follow {self.target_username} as {self.username}"
        elif self.task_type == 'unfollow_user':
            task_details['action'] = f"Unfollow {self.target_username} as {self.username}"
        elif self.task_type == 'view_story':
            task_details['action'] = f"View {self.target_username}'s story as {self.username}"
        elif self.task_type == 'dm_to_user':
            task_details['action'] = f"Send DM to {self.target_username} as {self.username}"
            task_details['message'] = self.params.get('dm_message', '')
        elif self.task_type == 'comment_post':
            task_details['action'] = f"Comment on {self.target_username}'s post as {self.username}"
            task_details['comment'] = self.params.get('comment', '')

        return task_details

class PrioritizedTask:
    def __init__(self, task: InstagramTask, priority: int):
        self.task = task
        self.priority = priority
    
    def __lt__(self, other):
        return self.priority < other.priority

class InstagramTaskManager:
    """Manages asynchronous Instagram automation tasks"""
    def __init__(self, instagram_automation, app=None):
        self.instagram_automation = instagram_automation
        self.app = app
        self.task_queue = queue.PriorityQueue()
        self.tasks: Dict[str, InstagramTask] = {}
        self.worker_threads = []
        self.num_workers = 100  # Increased to 100 workers
        self.device_queues = {}
        self.max_tasks_per_device = 1  # Limit concurrent tasks per device
        self.device_semaphores = {}  # Control concurrent access to devices
        
        # Start 100 worker threads
        for _ in range(self.num_workers):
            worker = threading.Thread(target=self._process_tasks, daemon=True)
            worker.start()
            self.worker_threads.append(worker)
        logger.info(f"Instagram Task Manager initialized with {self.num_workers} workers")

    def add_task(self, task_type: str, params: dict) -> str:
        """Add a new task to the queue"""
        task = InstagramTask(task_type, params)
        device_id = params.get('device_id')
        
        # Initialize device semaphore if not exists
        if device_id not in self.device_semaphores:
            self.device_semaphores[device_id] = threading.Semaphore(self.max_tasks_per_device)
        
        # Create device queue if it doesn't exist
        if device_id not in self.device_queues:
            self.device_queues[device_id] = queue.PriorityQueue()
            
        priority = self._get_task_priority(task_type)
        prioritized_task = PrioritizedTask(task, priority)
        
        self.device_queues[device_id].put(prioritized_task)
        self.tasks[task.task_id] = task
        logger.info(f"Added task {task.task_id} of type {task_type} with priority {priority}")
        return task.task_id

    def _get_task_priority(self, task_type: str) -> int:
        """Get priority level for different task types"""
        priorities = {
            'login': 1,  # Highest priority
            'logout': 1,
            'post_reel': 2,  # High priority for posting tasks
            'post_photo': 2,
            'dm_to_user': 3,
            'comment_post': 4,
            'like_post': 5,
            'follow_user': 6,
            'unfollow_user': 6,
            'like_story': 7,
            'view_story': 8,
            'comment_story': 9
        }
        return priorities.get(task_type, 10)  # Default priority for unknown tasks

    def get_task_status(self, task_id: str) -> Optional[dict]:
        """Get the status of a specific task"""
        task = self.tasks.get(task_id)
        return task.to_dict() if task else None

    def get_all_tasks(self) -> List[dict]:
        """Get status of all tasks"""
        # Sort tasks by creation date, newest first
        sorted_tasks = sorted(
            self.tasks.values(),
            key=lambda x: x.created_at,
            reverse=True
        )
        return [task.to_dict() for task in sorted_tasks]

    def get_active_tasks(self) -> List[dict]:
        """Get all tasks that are currently pending or running"""
        active_tasks = [task for task in self.tasks.values() 
                       if task.status in ['pending', 'running']]
        return sorted([task.to_dict() for task in active_tasks],
                     key=lambda x: x['created_at'],
                     reverse=True)

    def get_completed_tasks(self) -> List[dict]:
        """Get all completed tasks"""
        completed_tasks = [task for task in self.tasks.values() 
                         if task.status == 'completed']
        return sorted([task.to_dict() for task in completed_tasks],
                     key=lambda x: x['completed_at'],
                     reverse=True)

    def get_failed_tasks(self) -> List[dict]:
        """Get all failed tasks"""
        failed_tasks = [task for task in self.tasks.values() 
                       if task.status == 'failed']
        return sorted([task.to_dict() for task in failed_tasks],
                     key=lambda x: x['completed_at'],
                     reverse=True)

    def get_tasks_by_username(self, username: str) -> List[dict]:
        """Get all tasks for a specific username"""
        user_tasks = [task for task in self.tasks.values() 
                     if task.username == username]
        return sorted([task.to_dict() for task in user_tasks],
                     key=lambda x: x['created_at'],
                     reverse=True)

    def get_tasks_by_device(self, device_id: str) -> List[dict]:
        """Get all tasks for a specific device"""
        device_tasks = [task for task in self.tasks.values() 
                       if task.device_id == device_id]
        return sorted([task.to_dict() for task in device_tasks],
                     key=lambda x: x['created_at'],
                     reverse=True)

    def _process_tasks(self):
        """Process tasks from the queue with device semaphore"""
        while True:
            try:
                # Get next task from any device queue that has available semaphore
                task = None
                device_id = None
                
                for d_id, device_queue in self.device_queues.items():
                    if not device_queue.empty() and self.device_semaphores[d_id]._value > 0:
                        device_id = d_id
                        prioritized_task = device_queue.get_nowait()
                        task = prioritized_task.task
                        break
                
                if task and device_id:
                    with self.device_semaphores[device_id]:
                        with self.app.app_context():
                            self._execute_task(task)
                    self.device_queues[device_id].task_done()
                else:
                    # No tasks available, sleep briefly
                    time.sleep(0.1)
                    
            except queue.Empty:
                time.sleep(0.1)  # Prevent CPU spinning
            except Exception as e:
                logger.error(f"Error processing task queue: {str(e)}")
                time.sleep(1)

    def _execute_task(self, task: InstagramTask):
        """Execute a single task"""
        logger.info(f"Executing task {task.task_id} of type {task.task_type}")
        task.status = 'running'

        try:
            # Get device ID from task parameters
            device_id = task.params.get('device_id')
            if not device_id:
                raise ValueError("Device ID is required for task execution")

            # Close and reopen Instagram app
            logger.info(f"Closing and reopening Instagram app on device {device_id}")
            try:
                # Start gnirehtet before closing Instagram
                from app.utils.adb import ADBManager
                adb_manager = ADBManager()
                gnirehtet_success, gnirehtet_message = adb_manager.execute_operation(device_id, "start_gnirehtet")
                if not gnirehtet_success:
                    logger.warning(f"Failed to start gnirehtet: {gnirehtet_message}")
                time.sleep(3)  # Wait for gnirehtet to start

                # Close Instagram app
                if not self.instagram_automation.close_instagram(device_id):
                    raise Exception("Failed to close Instagram app")
                time.sleep(3)

                # Reopen Instagram app
                success, message = self.instagram_automation.ensure_instagram_open(device_id)
                if not success:
                    raise Exception(f"Failed to open Instagram app: {message}")
                time.sleep(7)  # Wait for app to fully load
            except Exception as e:
                logger.error(f"Error managing Instagram app state: {str(e)}")
                raise

            # Execute the specific task
            if task.task_type == 'post_reel':
                # First ensure correct account is logged in
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.post_reel(
                    task.params['device_id'],
                    task.params['video_path'],
                    task.params['caption'],
                    task.params.get('music_query')
                )

            elif task.task_type == 'post_photo':
                # First ensure correct account is logged in
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.post_photo(
                    task.params['device_id'],
                    task.params['photo_path'],
                    task.params['caption'],
                    task.params.get('music_query')
                )

            elif task.task_type == 'login':
                success, message = self.instagram_automation.login(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['password'],
                    task.params.get('email'),
                    task.params.get('email_password')
                )
            elif task.task_type == 'logout':
                success, message = self.instagram_automation._clear_instagram_data(task.params['device_id'])
            elif task.task_type == 'like_post':
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.like_post(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username']
                )
            elif task.task_type == 'unfollow_user':
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.unfollow_user(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username']
                )
            elif task.task_type == 'like_story':
                # First ensure correct account is logged in
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.like_story(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username']
                )
            elif task.task_type == 'comment_story':
                # First ensure correct account is logged in
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.comment_story(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username'],
                    task.params.get('comment', '')
                )
            elif task.task_type == 'follow_user':
                # First ensure correct account is logged in
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.follow_user(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username']
                )
            elif task.task_type == 'view_story':
                # First ensure correct account is logged in
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.view_story(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username']
                )
            elif task.task_type == 'dm_to_user':
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                if 'dm_id' in task.params:
                    from app.models.direct_message import DirectMessage
                    from app.extensions import db
                    dm = DirectMessage.query.get(task.params['dm_id'])
                    if dm:
                        dm_text = dm.message
                if dm_text:
                    dm_text=dm_text
                else:
                    dm_text = task.params['dm_message']
                success, message = self.instagram_automation.dm_to_user(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username'],
                    dm_text
                )

                # Update DM record status
                if 'dm_id' in task.params:
                    from app.models.direct_message import DirectMessage
                    from app.extensions import db
                    dm = DirectMessage.query.get(task.params['dm_id'])
                    if dm:
                        dm.status = 'sent' if success else 'failed'
                        dm.error_message = None if success else message
                        db.session.commit()

            elif task.task_type == 'comment_post':
                switch_success, switch_message = self.instagram_automation.switch_account_if_needed(
                    task.params['username'],
                    task.params['device_id'],
                    self.instagram_automation._get_device(task.params['device_id'])
                )
                if not switch_success:
                    raise Exception(f"Failed to switch to correct account: {switch_message}")
                
                success, message = self.instagram_automation.comment_post(
                    task.params['device_id'],
                    task.params['username'],
                    task.params['target_username'],
                    task.params['comment']
                )

                # Update comment record status
                if 'comment_id' in task.params:
                    from app.models.post_comment import PostComment
                    from app.extensions import db
                    comment = PostComment.query.get(task.params['comment_id'])
                    if comment:
                        comment.status = 'sent' if success else 'failed'
                        comment.error_message = None if success else message
                        db.session.commit()

            else:
                raise ValueError(f"Unknown task type: {task.task_type}")

            task.status = 'completed' if success else 'failed'
            task.result = message if success else None
            task.error = None if success else message

        except Exception as e:
            logger.error(f"Error executing task {task.task_id}: {str(e)}")
            task.status = 'failed'
            task.error = str(e)

        task.completed_at = datetime.now()
        logger.info(f"Task {task.task_id} completed with status: {task.status}")

    def _ensure_unicode_text(self, text: str) -> str:
        """Ensure text is properly encoded for Unicode support"""
        try:
            # Handle emoji and special characters
            if not isinstance(text, str):
                text = str(text)
            
            # Normalize Unicode characters
            import unicodedata
            text = unicodedata.normalize('NFKC', text)
            
            # Remove any potential null characters
            text = text.replace('\x00', '')
            
            return text
        except Exception as e:
            logger.error(f"Error processing Unicode text: {str(e)}")
            return text

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Remove completed tasks older than max_age_hours"""
        current_time = datetime.now()
        old_tasks = [
            task_id for task_id, task in self.tasks.items()
            if task.status in ['completed', 'failed'] and
            (current_time - task.completed_at).total_seconds() > max_age_hours * 3600
        ]
        for task_id in old_tasks:
            del self.tasks[task_id]
        logger.info(f"Cleaned up {len(old_tasks)} old tasks") 
